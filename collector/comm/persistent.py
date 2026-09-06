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

IMPORTANT — matches lxp-bridge (LuxPower's own reference) exactly:

  * lxp-bridge default is `heartbeats: false`; it relies on TCP keepalive
    (60s) to hold the connection, NOT proactive heartbeat packets.
  * When heartbeats ARE enabled, lxp-bridge is REACTIVE: it only echoes back
    a heartbeat the dongle sends; it never initiates one.

We deliberately do NOT send proactive heartbeats. Sending unsolicited
protocol=2 heartbeat packets interleaved with protocol=1 read/write traffic
causes the dongle to close the connection ("Broken pipe"), which is exactly
the flakiness we are eliminating.

Both the API (reads + writes) and the collector (polling) use this class.
"""

import logging
import socket
import threading
import time
from typing import Callable, Optional

from ..protocol import find_frames

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

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Open the connection."""
        self._connect()
        logger.info(
            "Persistent dongle connection started (%s:%d)", self.host, self.port
        )

    def stop(self) -> None:
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
                time.sleep(0.4 * (attempt + 1))
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
