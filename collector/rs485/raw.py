"""Raw/hex listener driver for RS-485 discovery."""

from __future__ import annotations

import logging
from typing import Any, Dict

from . import Rs485Device, Rs485DeviceConfig

logger = logging.getLogger("luxmon.rs485.raw")

try:
    import serial
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "pyserial is required for RS-485 support. "
        "Install it with: pip install pyserial>=3.5"
    ) from exc


class RawSerialDevice(Rs485Device):
    """Read raw bytes from the serial port and expose them as a hex string.

    Useful for discovering what an unknown RS-485 device is sending, or for
    verifying that bytes are arriving at all.
    """

    name = "raw"
    label = "Raw serial hex listener"

    def __init__(self, config: Rs485DeviceConfig):
        super().__init__(config)
        self._port: Any = None

    def _open(self) -> serial.Serial:
        if self._port is not None and self._port.is_open:
            return self._port
        self._port = serial.Serial(
            port=self.cfg.port,
            baudrate=self.cfg.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.cfg.timeout,
        )
        return self._port

    def close(self) -> None:
        if self._port is not None and self._port.is_open:
            try:
                self._port.close()
            except Exception:
                logger.exception("Error closing serial port")
        self._port = None

    def read(self) -> Dict[str, Dict[str, Any]]:
        port = self._open()
        port.reset_input_buffer()
        # Just listen; do not transmit in raw mode.
        data = port.read(2048)
        return {
            "raw_hex": {"value": data.hex(), "unit": "hex"},
            "raw_length": {"value": float(len(data)), "unit": "bytes"},
        }
