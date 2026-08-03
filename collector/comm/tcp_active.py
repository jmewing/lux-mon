"""Active TCP transport: send ReadInput/ReadHold requests via Modbus TCP gateway."""

import logging
import socket
import time
from threading import Thread, Event
from typing import Callable, Optional, Tuple, List, Union

from . import BaseTransport
from .tcp_base import tcp_connect, safe_close
from ..protocol import (
    LuxFrame,
    MODBUS_READ_INPUT,
    build_read_request,
    find_frames,
)

logger = logging.getLogger("luxmon.comm.tcp_active")


class TcpActiveTransport(BaseTransport):
    """
    Active Modbus TCP polling transport.

    Sends explicit ReadInput requests through a LuxPower/EG4 WiFi dongle or
    any Modbus TCP gateway. Each request covers one batch of registers.
    Responses are parsed and delivered as frames.

    This mode is future-proof: it works even when dongles disable the plain
    port-8000 broadcast stream.

    Note: some dongles respond with mismatched function codes or broadcast
    cached frames instead of direct responses. We accept any valid frames
    and let the collector merge registers by absolute register number.
    """

    def __init__(
        self,
        on_frame: Callable[[LuxFrame], None],
        host: str,
        port: int,
        datalog_serial: str,
        inverter_serial: str,
        reconnect_delay: float = 5.0,
        read_timeout: float = 10.0,
        poll_interval: float = 2.0,
        batches: Optional[List[Tuple[int, int]]] = None,
    ):
        super().__init__(on_frame)
        self.host = host
        self.port = port
        self.datalog_serial = datalog_serial
        self.inverter_serial = inverter_serial
        self.reconnect_delay = reconnect_delay
        self.read_timeout = read_timeout
        self.poll_interval = poll_interval
        # Default batches cover the full 111-register input map used by EG4 6000XP
        self.batches = batches or [(0, 40), (40, 40), (80, 40)]
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[Thread] = None
        self._stop = Event()
        self._frames_received = 0
        self._poll_requests = 0
        self._connect_time = 0.0

    def start(self) -> None:
        logger.info(
            "Starting active TCP transport for %s:%d (batches: %s)",
            self.host, self.port, self.batches,
        )
        self._running = True
        self._stop.clear()
        self._thread = Thread(target=self._run, name="lux-tcp-active", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        logger.info("Stopping active TCP transport")
        self._running = False
        self._stop.set()
        safe_close(self._sock)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def stats(self) -> dict:
        return {
            "type": "tcp_active",
            "host": f"{self.host}:{self.port}",
            "connected": self._sock is not None,
            "frames_received": self._frames_received,
            "poll_requests": self._poll_requests,
            "uptime": time.time() - self._connect_time if self._connect_time else 0,
        }

    def _build_requests(self) -> List[bytes]:
        return [
            build_read_request(
                datalog_serial=self.datalog_serial,
                inverter_serial=self.inverter_serial,
                device_function=MODBUS_READ_INPUT,
                start_register=start,
                count=count,
            )
            for start, count in self.batches
        ]

    def _run(self) -> None:
        requests = self._build_requests()
        buffer = b""
        batch_idx = 0
        next_send_time = time.time()

        while not self._stop.is_set():
            if self._sock is None:
                self._sock = tcp_connect(self.host, self.port, self.read_timeout)
                if self._sock is None:
                    self._stop.wait(self.reconnect_delay)
                    continue
                self._connect_time = time.time()
                buffer = b""

            # Throttle: one batch every poll_interval seconds
            wait_time = next_send_time - time.time()
            if wait_time > 0:
                self._stop.wait(wait_time)
            if self._stop.is_set():
                break
            next_send_time = time.time() + self.poll_interval

            request = requests[batch_idx]

            try:
                self._sock.sendall(request)
                self._poll_requests += 1
                logger.debug("Sent poll request for batch %d", batch_idx)

                # Read any frames that arrive within the per-request window.
                # The dongle may respond directly, broadcast cached frames, or
                # stay silent. We accept whatever valid frames we get.
                deadline = time.time() + max(1.0, min(self.read_timeout, self.poll_interval * 0.8))

                while time.time() < deadline and not self._stop.is_set():
                    try:
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            break
                        self._sock.settimeout(remaining)
                        chunk = self._sock.recv(4096)
                        if not chunk:
                            logger.warning("Dongle closed connection during poll")
                            safe_close(self._sock)
                            self._sock = None
                            break

                        buffer += chunk
                        frames, buffer = self._consume_frames(buffer)
                        if frames:
                            self._frames_received += len(frames)
                            for frame in frames:
                                self._emit(frame)
                            # Stop after first group of frames; next batch soon
                            break
                    except socket.timeout:
                        break
                    except OSError as exc:
                        logger.error("Socket error during poll: %s", exc)
                        safe_close(self._sock)
                        self._sock = None
                        break

                batch_idx = (batch_idx + 1) % len(self.batches)

            except OSError as exc:
                logger.error("Socket error during poll: %s", exc)
                safe_close(self._sock)
                self._sock = None

    def _consume_frames(self, buffer: bytes) -> Tuple[List[LuxFrame], bytes]:
        """Parse all complete frames from buffer and return leftover bytes."""
        frames = find_frames(buffer)
        if not frames:
            return [], buffer

        last_frame = frames[-1]
        last_pos = buffer.find(last_frame.raw) + len(last_frame.raw)
        leftover = buffer[last_pos:]
        return frames, leftover
