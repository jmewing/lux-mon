"""Passive TCP transport: listen to a LuxPower/EG4 dongle broadcast stream."""

import logging
import socket
import time
from threading import Thread, Event
from typing import Callable

from . import BaseTransport
from .tcp_base import tcp_connect, safe_close
from ..protocol import LuxFrame, find_frames

logger = logging.getLogger("luxmon.comm.tcp_passive")


class TcpPassiveTransport(BaseTransport):
    """
    Connect to a LuxPower/EG4 WiFi dongle on TCP port 8000 and passively
    listen to the streaming broadcast data. The dongle enforces a single
    TCP connection and emits ReadHold/ReadInput frames continuously.
    """

    def __init__(
        self,
        on_frame: Callable[[LuxFrame], None],
        host: str,
        port: int,
        reconnect_delay: float = 5.0,
        read_timeout: float = 60.0,
    ):
        super().__init__(on_frame)
        self.host = host
        self.port = port
        self.reconnect_delay = reconnect_delay
        self.read_timeout = read_timeout
        self._sock: socket.socket | None = None
        self._thread: Thread | None = None
        self._stop = Event()
        self._frames_received = 0
        self._connect_time = 0.0

    def start(self) -> None:
        logger.info("Starting passive TCP transport for %s:%d", self.host, self.port)
        self._running = True
        self._stop.clear()
        self._thread = Thread(target=self._run, name="lux-tcp-passive", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        logger.info("Stopping passive TCP transport")
        self._running = False
        self._stop.set()
        safe_close(self._sock)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def stats(self) -> dict:
        return {
            "type": "tcp_passive",
            "host": f"{self.host}:{self.port}",
            "connected": self._sock is not None,
            "frames_received": self._frames_received,
            "uptime": time.time() - self._connect_time if self._connect_time else 0,
        }

    def _run(self) -> None:
        buffer = b""

        while not self._stop.is_set():
            if self._sock is None:
                self._sock = tcp_connect(self.host, self.port, self.read_timeout)
                if self._sock is None:
                    self._stop.wait(self.reconnect_delay)
                    continue
                self._connect_time = time.time()
                buffer = b""

            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    logger.warning(
                        "Dongle closed connection (single-connection limit or inverter off)"
                    )
                    safe_close(self._sock)
                    self._sock = None
                    self._stop.wait(self.reconnect_delay)
                    continue

                buffer += chunk
                buffer = self._consume_frames(buffer)

                if len(buffer) > 8192:
                    next_a1 = buffer.find(bytes([0xA1, 0x1A]), 1)
                    buffer = buffer[next_a1:] if next_a1 > 0 else b""

            except socket.timeout:
                logger.warning("Socket timeout, reconnecting...")
                safe_close(self._sock)
                self._sock = None
            except OSError as exc:
                logger.error("Socket error: %s", exc)
                safe_close(self._sock)
                self._sock = None
                self._stop.wait(self.reconnect_delay)

    def _consume_frames(self, buffer: bytes) -> bytes:
        frames = find_frames(buffer)
        if frames:
            self._frames_received += len(frames)
            for frame in frames:
                self._emit(frame)
            last_frame = frames[-1]
            last_pos = buffer.find(last_frame.raw) + len(last_frame.raw)
            return buffer[last_pos:]
        return buffer
