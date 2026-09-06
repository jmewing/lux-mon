"""Active TCP transport: send ReadInput/ReadHold requests via Modbus TCP gateway.

Uses the process-wide persistent dongle connection (LuxPower-standard
lifecycle: one long-lived socket with heartbeats) instead of opening a fresh
connection per poll, which the dongle drops intermittently.
"""

import logging
import time
from threading import Event, Thread
from typing import Callable, List, Optional, Tuple

from . import BaseTransport
from .persistent import get_shared_connection
from ..protocol import (
    MODBUS_READ_INPUT,
    build_read_request,
)

logger = logging.getLogger("luxmon.comm.tcp_active")


class TcpActiveTransport(BaseTransport):
    """
    Active Modbus TCP polling transport.

    Sends explicit ReadInput requests through a LuxPower/EG4 WiFi dongle or
    any Modbus TCP gateway. Each request covers one batch of registers.
    Responses are parsed and delivered as frames.

    All requests are serialized over the shared persistent connection, so the
    dongle never sees connection churn or concurrent sockets.
    """

    def __init__(
        self,
        on_frame: Callable[[object], None],
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
        self._conn = None
        self._thread: Optional[Thread] = None
        self._stop = Event()
        self._frames_received = 0
        self._poll_requests = 0

    def start(self) -> None:
        logger.info(
            "Starting active TCP transport for %s:%d (batches: %s)",
            self.host, self.port, self.batches,
        )
        self._running = True
        self._stop.clear()
        self._conn = get_shared_connection(
            self.host, self.port, self.datalog_serial, self.inverter_serial,
            timeout=self.read_timeout,
        )
        self._thread = Thread(target=self._run, name="lux-tcp-active", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        logger.info("Stopping active TCP transport")
        self._running = False
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def stats(self) -> dict:
        base = self._conn.stats() if self._conn else {}
        return {
            "type": "tcp_active",
            "host": f"{self.host}:{self.port}",
            "connected": base.get("connected", False),
            "frames_received": self._frames_received,
            "poll_requests": self._poll_requests,
            "connects": base.get("connects", 0),
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
        batch_idx = 0
        next_send_time = time.time()

        while not self._stop.is_set():
            # Throttle: one batch every poll_interval seconds
            wait_time = next_send_time - time.time()
            if wait_time > 0:
                self._stop.wait(wait_time)
            if self._stop.is_set():
                break
            next_send_time = time.time() + self.poll_interval

            request = requests[batch_idx]
            start, count = self.batches[batch_idx]

            def match(frame) -> bool:
                if frame.is_error:
                    return True
                return frame.is_read_input and frame.register == start

            frame = self._conn.transact(request, match, timeout=self.read_timeout)
            if frame is not None:
                self._poll_requests += 1
                if frame.is_error:
                    logger.warning(
                        "Poll batch %d-%d: Modbus error code %s",
                        start, start + count - 1, frame.error_code,
                    )
                else:
                    self._frames_received += 1
                    self._emit(frame)

            batch_idx = (batch_idx + 1) % len(self.batches)
