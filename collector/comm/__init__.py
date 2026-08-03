"""
Pluggable communication transports for lux-mon.

Each transport is responsible for connecting to an inverter or dongle and
delivering parsed LuxFrame objects to the collector. The collector itself is
transport-agnostic: it only cares about receiving decoded snapshots.
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional
from ..protocol import LuxFrame


class BaseTransport(ABC):
    """Abstract base class for inverter communication transports."""

    def __init__(self, on_frame: Callable[[LuxFrame], None]):
        self._on_frame = on_frame
        self._running = False

    @abstractmethod
    def start(self) -> None:
        """Start the transport and begin delivering frames."""
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        """Stop the transport and release any resources."""
        raise NotImplementedError

    @abstractmethod
    def stats(self) -> dict:
        """Return transport-specific statistics."""
        raise NotImplementedError

    def _emit(self, frame: LuxFrame) -> None:
        """Deliver a parsed frame to the collector."""
        try:
            self._on_frame(frame)
        except Exception:
            # Collector handles logging; transport must not die on callback errors
            pass
