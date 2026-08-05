"""Generic Modbus RTU master driver for RS-485 devices."""

from __future__ import annotations

import logging
from typing import Any, Dict

from . import Rs485Device, Rs485DeviceConfig

logger = logging.getLogger("luxmon.rs485.modbus_rtu")

try:
    from pymodbus.client import ModbusSerialClient
    from pymodbus.exceptions import ModbusException
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "pymodbus is required for Modbus RTU support. "
        "Install it with: pip install pymodbus>=3.6.0"
    ) from exc


class ModbusRtuDevice(Rs485Device):
    """Generic Modbus RTU master polling driver.

    Configuration options (via ``cfg.options`` or env-derived ``Rs485DeviceConfig``):
      - ``slave_id``      – Modbus slave address (default 1)
      - ``modbus_start``  – First register address (default 0)
      - ``modbus_count``  – Number of registers to read (default 40)
      - ``modbus_function`` – "input" (0x04, default) or "holding" (0x03)
      - ``scale_map``     – Optional dict mapping register offset to (name, unit, scale)
                            If omitted, registers are exposed as ``register_0`` etc.
    """

    name = "modbus_rtu"
    label = "Generic Modbus RTU device"

    def __init__(self, config: Rs485DeviceConfig):
        super().__init__(config)
        self._client: Optional[Any] = None
        self._scale_map: Dict[int, tuple] = {}
        self._parse_scale_map()

    def _parse_scale_map(self) -> None:
        scale_map = self.cfg.options.get("scale_map")
        if not scale_map:
            return
        if isinstance(scale_map, dict):
            for k, v in scale_map.items():
                try:
                    self._scale_map[int(k)] = v
                except (ValueError, TypeError):
                    logger.warning("Invalid scale_map key %r", k)

    def _client_instance(self) -> Any:
        if self._client is None:
            self._client = ModbusSerialClient(
                port=self.cfg.port,
                baudrate=self.cfg.baudrate,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=self.cfg.timeout,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                logger.exception("Error closing Modbus client")
        self._client = None

    def read(self) -> Dict[str, Dict[str, Any]]:
        client = self._client_instance()
        if not client.connect():
            logger.warning("Failed to connect to Modbus RTU device on %s", self.cfg.port)
            return {}

        func = self.cfg.modbus_function.lower()
        slave = self.cfg.slave_id
        start = self.cfg.modbus_start
        count = self.cfg.modbus_count

        try:
            if func == "holding":
                rr = client.read_holding_registers(start, count, slave=slave)
            else:
                rr = client.read_input_registers(start, count, slave=slave)

            if rr is None:
                logger.warning("No response from Modbus slave %d", slave)
                return {}
            if rr.isError():
                logger.warning("Modbus error from slave %d: %s", slave, rr)
                return {}

            registers = rr.registers
        except ModbusException as exc:
            logger.warning("Modbus exception: %s", exc)
            return {}
        except Exception:
            logger.exception("Unexpected Modbus read error")
            return {}

        result: Dict[str, Dict[str, Any]] = {}
        for i, raw in enumerate(registers):
            reg_addr = start + i
            scale_info = self._scale_map.get(i)
            if scale_info:
                if isinstance(scale_info, dict):
                    name = scale_info.get("name", f"register_{reg_addr}")
                    unit = scale_info.get("unit", "")
                    scale = float(scale_info.get("scale", 1.0))
                    signed = bool(scale_info.get("signed", False))
                else:
                    name, unit, scale = scale_info[:3]
                    signed = len(scale_info) > 3 and scale_info[3]
                value = raw if not signed else (raw - 0x10000 if raw >= 0x8000 else raw)
                result[name] = {"value": float(value) * scale, "unit": unit}
            else:
                result[f"register_{reg_addr}"] = {"value": float(raw), "unit": ""}

        return result
