"""Persistent dongle connection (LuxPower-standard connection lifecycle).

The EG4/LuxPower WiFi dongle is designed for a SINGLE long-lived TCP
connection per client. Opening a fresh connection per read/write (the old
behaviour) makes the dongle drop transactions and close connections
mid-poll, which surfaced as intermittent "No valid response" failures on
local port 8000 — while the EG4 cloud (which uses a persistent channel)
stayed rock solid.

This module provides one persistent socket per process with:

  * automatic (re)connect with TCP keepalive (60s, matching lxp-bridge)
  * a thread-safe `transact()` that serializes all reads/writes over the
    single socket (the dongle rejects concurrent/burst connections)
  * automatic reconnect + retry when the dongle drops the connection

IMPORTANT — matches lxp-bridge (LuxPower's own reference):

  * lxp-bridge sends a Heartbeat (0xC1, protocol=2) after 120s of silence
    to keep the dongle session alive (inverter.rs: "If no messages received
    for 120 seconds, send a heartbeat"). We do the same via a background
    heartbeat thread.
  * lxp-bridge waits RECONNECT_DELAY_SECS=5 between reconnection attempts;
    we use the same delay to avoid wedging the dongle's session state with
    rapid-fire reconnects.
  * We also detect stale responses: if the dongle returns byte-identical
    register data across consecutive reads, we force a full reconnect
    instead of trusting the cached response.

Both the API (reads + writes) and the collector (polling) use this class.
"""

import logging
import socket
import threading
import time
from typing import Callable, Optional

from ..protocol import find_frames, build_heartbeat

logger = logging.getLogger("luxmon.comm.persistent")


class PersistentDongleConnection:
    """One long-lived TCP connection to the dongle (no proactive heartbeats).

    All transactions are serialized through an internal lock so reads and
    writes never collide on the socket (the dongle drops bursts). If the
    dongle closes the connection, the next transaction reconnects and retries.
    """

    def __init__(
        self,
        host: str,
        port: int,
        datalog_serial: str,
        inverter_serial: str,
        timeout: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        self.host = host
        self.port = port
        self.datalog_serial = datalog_serial
        self.inverter_serial = inverter_serial
        self.timeout = timeout
        self.max_retries = max_retries

        self._lock = threading.RLock()
        self._sock: Optional[socket.socket] = None
        self._connect_count = 0
        self._tx_count = 0

        # Heartbeat thread: keeps the dongle session alive (lxp-bridge sends
        # a heartbeat after 120s of silence). Started in start(), stopped in
        # stop().
        self._hb_thread: Optional[threading.Thread] = None
        self._hb_stop = threading.Event()
        self._hb_interval = 60.0  # check every 60s; send if 120s+ silence

        # Staleness detection: remember the last response signature so we can
        # detect the dongle serving frozen/cached data (the 9/7 wedge).
        self._last_resp_sig: Optional[bytes] = None
        self._stale_count = 0
        self._stale_threshold = 5  # N identical responses => force reconnect

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Open the connection and start the heartbeat thread."""
        self._connect()
        self._hb_stop.clear()
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop, name="lux-dongle-heartbeat", daemon=True
        )
        self._hb_thread.start()
        logger.info(
            "Persistent dongle connection started (%s:%d)", self.host, self.port
        )

    def stop(self) -> None:
        self._hb_stop.set()
        if self._hb_thread is not None:
            self._hb_thread.join(timeout=2.0)
            self._hb_thread = None
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def stats(self) -> dict:
        return {
            "type": "persistent",
            "host": f"{self.host}:{self.port}",
            "connected": self._sock is not None,
            "connects": self._connect_count,
            "transactions": self._tx_count,
        }

    # ── connection management ────────────────────────────────────────────────

    def _connect(self) -> None:
        """Open the socket (caller must hold the lock)."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        # TCP keepalive (60s), matching lxp-bridge's set_keepalive(60).
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        except OSError:
            pass
        sock.connect((self.host, self.port))
        self._sock = sock
        self._connect_count += 1
        logger.info("Persistent dongle connected (%s:%d)", self.host, self.port)

    def _ensure_connected(self) -> None:
        """Reconnect if the socket is missing or dead (caller holds lock)."""
        if self._sock is not None:
            # Cheap liveness probe: a zero-byte peek fails on a closed socket.
            try:
                self._sock.settimeout(0.2)
                self._sock.recv(1, socket.MSG_PEEK)
                self._sock.settimeout(self.timeout)
                return
            except socket.timeout:
                # No data pending but connection is alive — fine.
                self._sock.settimeout(self.timeout)
                return
            except OSError:
                self._sock = None
        self._connect()

    # ── heartbeat ───────────────────────────────────────────────────────────

    def _heartbeat_loop(self) -> None:
        """Send a heartbeat after 120s of silence (matches lxp-bridge).

        The dongle expects periodic traffic to keep the session alive. If we
        go silent too long, the session goes stale and the dongle starts
        serving cached data (the 9/7 wedge). lxp-bridge sends a Heartbeat
        (0xC1, protocol=2) after 120s of no received messages; we mirror that.
        """
        last_rx = time.time()
        while not self._hb_stop.wait(self._hb_interval):
            with self._lock:
                if self._sock is None:
                    continue
                # Track last successful receive time via a lightweight probe.
                try:
                    self._sock.settimeout(0.2)
                    self._sock.recv(1, socket.MSG_PEEK)
                    self._sock.settimeout(self.timeout)
                except socket.timeout:
                    pass  # no pending data — connection alive
                except OSError:
                    self._sock = None
                    continue
                if time.time() - last_rx >= 120:
                    try:
                        hb = build_heartbeat(self.datalog_serial)
                        self._sock.sendall(hb)
                        self._tx_count += 1
                        logger.debug("Sent heartbeat to dongle")
                    except OSError as exc:
                        logger.warning("Heartbeat send failed: %s", exc)
                        self._sock = None
                    last_rx = time.time()

    # ── staleness detection ──────────────────────────────────────────────────

    def _note_response(self, raw: bytes) -> None:
        """Track response signatures to detect frozen/cached dongle data."""
        if raw == self._last_resp_sig:
            self._stale_count += 1
            if self._stale_count >= self._stale_threshold:
                logger.warning(
                    "Dongle returned identical data %d times — forcing reconnect "
                    "(possible stale/cached response)",
                    self._stale_count,
                )
                self._stale_count = 0
                if self._sock is not None:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None
        else:
            self._last_resp_sig = raw
            self._stale_count = 0

    # ── transactions ────────────────────────────────────────────────────────

    def transact(
        self,
        request: bytes,
        match: Callable[[object], bool],
        timeout: Optional[float] = None,
    ) -> Optional[object]:
        """Send a request over the persistent socket and return the matching frame.

        `match(frame)` returns True for the response frame we care about.
        Retries (reconnect + resend) up to `max_retries` times when the dongle
        drops the connection or the response never arrives.
        """
        timeout = timeout or self.timeout
        with self._lock:
            for attempt in range(self.max_retries + 1):
                self._ensure_connected()
                if self._sock is None:
                    time.sleep(0.5)
                    continue
                try:
                    self._sock.settimeout(timeout)
                    self._sock.sendall(request)
                    self._tx_count += 1
                    deadline = time.time() + timeout
                    buffer = b""
                    while time.time() < deadline:
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            break
                        self._sock.settimeout(remaining)
                        try:
                            chunk = self._sock.recv(4096)
                        except socket.timeout:
                            break
                        if not chunk:
                            # Dongle closed the connection — reconnect and retry.
                            logger.warning(
                                "Dongle closed connection during transaction (attempt %d)",
                                attempt + 1,
                            )
                            self._sock = None
                            break
                        buffer += chunk
                        for frame in find_frames(buffer):
                            if match(frame):
                                # Staleness guard: identical responses across
                                # consecutive reads => dongle serving cached
                                # data; force a reconnect.
                                self._note_response(frame.raw)
                                return frame
                    # If we got here without a match and the socket is still
                    # alive, the dongle simply didn't answer this request.
                    if self._sock is not None:
                        logger.debug(
                            "No matching response (attempt %d); retrying", attempt + 1
                        )
                except OSError as exc:
                    logger.warning(
                        "Transaction socket error (attempt %d): %s", attempt + 1, exc
                    )
                    if self._sock is not None:
                        try:
                            self._sock.close()
                        except OSError:
                            pass
                        self._sock = None
                time.sleep(5.0)  # lxp-bridge RECONNECT_DELAY_SECS
            return None


# ── module-level shared instance (one per process) ──────────────────────────

_shared: Optional[PersistentDongleConnection] = None
_shared_lock = threading.Lock()


def get_shared_connection(
    host: str,
    port: int,
    datalog_serial: str,
    inverter_serial: str,
    timeout: float = 10.0,
) -> PersistentDongleConnection:
    """Return the process-wide persistent connection, creating it if needed.

    The API and collector each run in their own process, so each gets its own
    persistent socket — but within a process, every read/write shares one
    connection (the LuxPower-standard lifecycle).
    """
    global _shared
    with _shared_lock:
        if _shared is None or (
            _shared.host, _shared.port,
            _shared.datalog_serial, _shared.inverter_serial,
        ) != (host, port, datalog_serial, inverter_serial):
            if _shared is not None:
                _shared.stop()
            _shared = PersistentDongleConnection(
                host, port, datalog_serial, inverter_serial, timeout=timeout
            )
            _shared.start()
        return _shared
