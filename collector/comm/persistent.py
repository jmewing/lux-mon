"""Persistent dongle connection (LuxPower-standard connection lifecycle).

The EG4/LuxPower WiFi dongle is designed for a SINGLE long-lived TCP
connection per client, kept alive with periodic heartbeats (TcpFunction 0xC1).
Opening a fresh connection per read/write (the old behaviour) makes the
dongle drop transactions and close connections mid-poll, which surfaced as
intermittent "No valid response" failures on local port 8000 — while the EG4
cloud (which uses a persistent channel) stayed rock solid.

This module provides one persistent socket per process with:

  * automatic (re)connect with TCP keepalive
  * periodic heartbeat packets to keep the dongle's session alive
  * a thread-safe `transact()` that serializes all reads/writes over the
    single socket (the dongle rejects concurrent/burst connections)
  * automatic reconnect + retry when the dongle drops the connection

Both the API (reads + writes) and the collector (polling) use this class.
"""

import logging
import socket
import struct
import threading
import time
from typing import Callable, Optional

from ..protocol import (
    PREFIX,
    TCP_FUNC_HEARTBEAT,
    TCP_FUNC_TRANSLATED_DATA,
    _serial_to_bytes,
    crc16_modbus,
    find_frames,
)

logger = logging.getLogger("luxmon.comm.persistent")


def build_heartbeat_request(datalog_serial: str) -> bytes:
    """Build a heartbeat packet (TcpFunction 0xC1, protocol 2, 1 data byte).

    Matches the lxp-bridge reference: frame = A1 1A | proto(2) | flen-6 |
    0x01 | 0xC1 | datalog(10) | 0x00.
    """
    data = bytes([0])  # heartbeat has a single zero data byte
    data_length = len(data)
    frame_length = 18 + data_length
    pkt = bytearray(frame_length)
    pkt[0:2] = PREFIX
    struct.pack_into("<H", pkt, 2, 2)          # protocol = 2 (heartbeat)
    struct.pack_into("<H", pkt, 4, frame_length - 6)
    pkt[6] = 0x01
    pkt[7] = TCP_FUNC_HEARTBEAT
    pkt[8:18] = _serial_to_bytes(datalog_serial)
    pkt[18:] = data
    return bytes(pkt)


class PersistentDongleConnection:
    """One long-lived TCP connection to the dongle, with heartbeats.

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
        heartbeat_interval: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.host = host
        self.port = port
        self.datalog_serial = datalog_serial
        self.inverter_serial = inverter_serial
        self.timeout = timeout
        self.heartbeat_interval = heartbeat_interval
        self.max_retries = max_retries

        self._lock = threading.RLock()
        self._sock: Optional[socket.socket] = None
        self._last_heartbeat = 0.0
        self._stop = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._connect_count = 0
        self._tx_count = 0

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Open the connection and start the heartbeat thread."""
        self._stop.clear()
        self._connect()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name="lux-dongle-heartbeat", daemon=True
        )
        self._heartbeat_thread.start()
        logger.info(
            "Persistent dongle connection started (%s:%d)", self.host, self.port
        )

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=5.0)

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
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        sock.connect((self.host, self.port))
        self._sock = sock
        self._connect_count += 1
        self._last_heartbeat = time.time()
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

    def _send_heartbeat(self) -> None:
        """Send a heartbeat packet to keep the dongle session alive."""
        try:
            req = build_heartbeat_request(self.datalog_serial)
            with self._lock:
                if self._sock is None:
                    return
                self._sock.sendall(req)
                self._last_heartbeat = time.time()
        except OSError as exc:
            logger.warning("Heartbeat send failed: %s", exc)
            with self._lock:
                if self._sock is not None:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self.heartbeat_interval)
            if self._stop.is_set():
                break
            self._send_heartbeat()

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
