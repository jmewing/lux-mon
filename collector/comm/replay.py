"""Replay transport: feed a captured binary file to the collector offline."""

import logging
import time
from pathlib import Path
from threading import Thread, Event
from typing import Callable

from . import BaseTransport
from ..protocol import LuxFrame, find_frames

logger = logging.getLogger("luxmon.comm.replay")


class ReplayTransport(BaseTransport):
    """Replay a captured binary file for offline testing/development."""

    def __init__(
        self,
        on_frame: Callable[[LuxFrame], None],
        replay_file: str,
        chunk_size: int = 512,
        chunk_delay: float = 0.5,
    ):
        super().__init__(on_frame)
        self.replay_file = replay_file
        self.chunk_size = chunk_size
        self.chunk_delay = chunk_delay
        self._thread: Thread | None = None
        self._stop = Event()
        self._frames_received = 0

    def start(self) -> None:
        path = Path(self.replay_file)
        if not path.exists():
            raise FileNotFoundError(f"Replay file not found: {path}")
        logger.info("Starting replay transport from %s", path)
        self._running = True
        self._stop.clear()
        self._thread = Thread(target=self._run, name="lux-replay", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        logger.info("Stopping replay transport")
        self._running = False
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def stats(self) -> dict:
        return {
            "type": "replay",
            "file": self.replay_file,
            "frames_received": self._frames_received,
        }

    def _run(self) -> None:
        data = Path(self.replay_file).read_bytes()
        logger.info("Replaying %d bytes from %s", len(data), self.replay_file)

        buffer = b""
        for offset in range(0, len(data), self.chunk_size):
            if self._stop.is_set():
                break
            buffer += data[offset:offset + self.chunk_size]

            frames = find_frames(buffer)
            if frames:
                self._frames_received += len(frames)
                for frame in frames:
                    self._emit(frame)
                last_frame = frames[-1]
                last_pos = buffer.find(last_frame.raw) + len(last_frame.raw)
                buffer = buffer[last_pos:]

            time.sleep(self.chunk_delay)

        logger.info("Replay finished (%d frames parsed)", self._frames_received)

        while not self._stop.is_set():
            time.sleep(1)
