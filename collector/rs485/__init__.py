"""RS-485 / serial device drivers for lux-mon.

This package provides pluggable drivers for devices connected to the host via
an RS-485 or TTL serial adapter (for example a CP210x USB-to-UART bridge).

Each driver inherits from :class:`Rs485Device` and implements ``read()`` to
return a snapshot of decoded values.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Rs485DeviceConfig:
    """Common configuration for an RS-485 device."""

    port: str = "/dev/ttyUSB0"
    baudrate: int = 115200
    timeout: float = 1.0
    poll_interval: float = 2.0
    device_type: str = "jk_bms"
    # Generic Modbus settings
    slave_id: int = 1
    modbus_start: int = 0
    modbus_count: int = 40
    modbus_function: str = "input"  # or "holding"
    # Driver-specific options
    options: Dict[str, Any] = field(default_factory=dict)


class Rs485Device(ABC):
    """Base class for an RS-485 connected device driver.

    A driver owns a serial port and knows how to request/parse data from one
    specific device family. The :meth:`read` method should return a dictionary
    of decoded values using the same shape as the main inverter driver:

        {"field_name": {"value": float, "unit": str}}
    """

    name: str = "generic"
    label: str = "Generic RS-485 device"

    def __init__(self, config: Rs485DeviceConfig):
        self.cfg = config

    @abstractmethod
    def read(self) -> Dict[str, Dict[str, Any]]:
        """Read the device and return decoded values.

        Returns a mapping of field name -> {"value": ..., "unit": ...}.
        Returns an empty dict if the read failed or no data was available.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release any resources."""
        pass
