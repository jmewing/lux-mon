"""JK BMS RS-485 driver.

Implements the proprietary JK (JiKong) BMS serial protocol used by many
JK-BMS models over UART-TTL or RS-485 adapters. The status request frame
starts with the magic bytes ``0x4E 0x57`` ("NW") and the response is a
variable-length frame containing cell voltages, temperatures, current, SOC,
and protection/status flags.

Reference implementations:
  - https://github.com/syssi/esphome-jk-bms
  - https://github.com/PurpleAlien/jk-bms_grafana
  - https://github.com/NEEY-electronic/JK/tree/JK-BMS
"""

from __future__ import annotations

import logging
import struct
import time
from typing import Any, Dict, Optional

from . import Rs485Device, Rs485DeviceConfig

logger = logging.getLogger("luxmon.rs485.jk_bms")

# Try to import pyserial; fail gracefully with a clear message.
try:
    import serial
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "pyserial is required for RS-485 support. "
        "Install it with: pip install pyserial>=3.5"
    ) from exc


# JK BMS protocol constants
MAGIC = bytes([0x4E, 0x57])  # "NW"
FRAME_HEADER_LEN = 18  # prefix(2) + length(2) + zeros(4) + function(2) + data_len(4) + terminator(4)
FUNCTION_READ_ALL = 0x06

# Standard status request used by newer JK BMS firmware.
# Format: magic + length + zeros + function_read_all + subcmd + zeros + terminator + checksum
STATUS_REQUEST = bytearray.fromhex(
    "4E 57 00 13 00 00 00 00 06 03 00 00 00 00 00 00 68 00 00 01 29"
)

# Fallback status request used by older firmware (register 0x79).
STATUS_REQUEST_FALLBACK = bytearray.fromhex(
    "4E 57 00 13 00 00 00 00 03 03 00 79 00 00 00 00 68"
)


def _jk_checksum(data: bytes) -> int:
    """JK BMS uses a simple sum-of-bytes "checksum" appended as 4 big-endian bytes."""
    return sum(data) & 0xFFFFFFFF


def _append_checksum(cmd: bytearray) -> bytes:
    """Append the 4-byte big-endian checksum to a command."""
    crc = _jk_checksum(cmd)
    cmd.extend(struct.pack(">I", crc))
    return bytes(cmd)


def _get_u16(data: bytes, offset: int) -> int:
    return (data[offset] << 8) | data[offset + 1]


def _get_u32(data: bytes, offset: int) -> int:
    return (_get_u16(data, offset) << 16) | _get_u16(data, offset + 2)


def _get_s16(data: bytes, offset: int) -> int:
    val = _get_u16(data, offset)
    if val >= 0x8000:
        val -= 0x10000
    return val


def _temperature(raw: int) -> float:
    """Decode JK temperature value (0-99 positive, 100+ wraps negative)."""
    if raw > 100:
        return float(raw - 100 - 100)
    return float(raw)


def _current(raw: int, sign_byte: int) -> float:
    """Decode signed current.

    JK current is unsigned magnitude; direction is inferred from a separate
    sign/direction byte or bit. We use the common convention: bit 0 of
    sign_byte == 1 means discharge (negative current), otherwise charge.
    """
    current = float(raw) * 0.01
    if sign_byte & 0x01:
        return -current
    return current


class JkBmsDevice(Rs485Device):
    """JK BMS serial device driver."""

    name = "jk_bms"
    label = "JK BMS (JiKong) via RS-485/UART"

    def __init__(self, config: Rs485DeviceConfig):
        super().__init__(config)
        self._port: Optional[serial.Serial] = None
        self._last_request_kind = "standard"

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
            write_timeout=self.cfg.timeout,
        )
        logger.debug("Opened %s at %d baud", self.cfg.port, self.cfg.baudrate)
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

        # Try the standard request first; some older firmware needs the fallback.
        requests = [
            ("standard", _append_checksum(bytearray(STATUS_REQUEST))),
            ("fallback", _append_checksum(bytearray(STATUS_REQUEST_FALLBACK))),
        ]

        for kind, request in requests:
            self._last_request_kind = kind
            logger.debug("Sending JK BMS %s status request", kind)
            port.write(request)
            time.sleep(0.1)
            raw = port.read(4096)
            if not raw:
                continue
            parsed = self._parse_frame(raw)
            if parsed:
                return parsed

        return {}

    def _parse_frame(self, data: bytes) -> Optional[Dict[str, Dict[str, Any]]]:
        """Parse a JK BMS status response frame."""
        if len(data) < 20:
            logger.debug("Response too short (%d bytes)", len(data))
            return None

        if not data.startswith(MAGIC):
            logger.debug("Response does not start with JK magic bytes")
            return None

        # Frame layout:
        #   [0:2]   magic "NW"
        #   [2:4]   payload length (big-endian, includes data section)
        #   [4:8]   zeros
        #   [8]     function (0x06 for read_all)
        #   [9]     sub-function / device address
        #   [10:12] data length (big-endian)
        #   ...     data payload
        #   last 4  checksum (sum of all preceding bytes, big-endian)
        frame_len = _get_u16(data, 2)
        function = data[8]
        data_len = _get_u16(data, 10)

        if function != FUNCTION_READ_ALL and function != 0x03:
            logger.debug("Unknown JK function byte 0x%02X", function)
            return None

        # Validate checksum over the whole frame minus the trailing 4 checksum bytes.
        expected_len = 12 + data_len + 4
        if len(data) < expected_len:
            logger.debug("Frame incomplete: expected %d bytes, got %d", expected_len, len(data))
            return None

        payload = data[: expected_len - 4]
        stored_crc = _get_u32(data, expected_len - 4)
        calc_crc = _jk_checksum(payload)
        if stored_crc != calc_crc:
            logger.debug("JK checksum mismatch: stored=%08x calc=%08x", stored_crc, calc_crc)
            # Some firmware doesn't include the length field in the checksum.
            alt_crc = _jk_checksum(data[2 : expected_len - 4])
            if stored_crc != alt_crc:
                return None
            logger.debug("Checksum matched using alternate start offset")

        # Data section starts at offset 12.
        body = data[12 : expected_len - 4]
        return self._parse_status(body)

    def _parse_status(self, data: bytes) -> Optional[Dict[str, Dict[str, Any]]]:
        """Parse the status payload (body) into decoded values."""
        if len(data) < 2:
            return None

        result: Dict[str, Dict[str, Any]] = {}

        # 0x79: Individual cell voltages
        if data[0] != 0x79:
            logger.debug("Status payload does not start with cell voltage marker 0x79")
            return None

        cell_count = data[1] // 3
        if cell_count == 0 or cell_count > 64:
            logger.debug("Implausible cell count %d", cell_count)
            return None

        if len(data) < cell_count * 3 + 3:
            logger.debug("Status payload too short for %d cells", cell_count)
            return None

        cell_voltages = []
        min_cell = 100.0
        max_cell = 0.0
        sum_cells = 0.0
        min_idx = 0
        max_idx = 0
        for i in range(cell_count):
            offset = 3 + i * 3
            idx = data[offset]
            voltage = _get_u16(data, offset + 1) * 0.001
            cell_voltages.append(voltage)
            sum_cells += voltage
            if voltage < min_cell:
                min_cell = voltage
                min_idx = i + 1
            if voltage > max_cell:
                max_cell = voltage
                max_idx = i + 1
            result[f"cell_{i + 1}_voltage"] = {"value": voltage, "unit": "V"}

        if cell_count:
            avg_cell = sum_cells / cell_count
            result["cell_min_voltage"] = {"value": min_cell, "unit": "V"}
            result["cell_max_voltage"] = {"value": max_cell, "unit": "V"}
            result["cell_avg_voltage"] = {"value": avg_cell, "unit": "V"}
            result["cell_delta_voltage"] = {"value": max_cell - min_cell, "unit": "V"}
            result["cell_min_index"] = {"value": float(min_idx), "unit": ""}
            result["cell_max_index"] = {"value": float(max_idx), "unit": ""}
            result["cell_count"] = {"value": float(cell_count), "unit": ""}

        offset = cell_count * 3 + 3

        # Helper to pull the next 3-byte tagged value.
        def tag_value(idx: int) -> int:
            nonlocal offset
            if len(data) < offset + 3:
                return 0
            tag = data[offset]
            if tag != idx:
                logger.debug("Expected tag 0x%02X at offset %d, got 0x%02X", idx, offset, tag)
            val = _get_u16(data, offset + 1)
            offset += 3
            return val

        # Temperatures and main pack data
        if len(data) >= offset + 3:
            result["mosfet_temperature"] = {"value": _temperature(tag_value(0x80)), "unit": "°C"}
        if len(data) >= offset + 3:
            result["battery_box_temperature"] = {"value": _temperature(tag_value(0x81)), "unit": "°C"}
        if len(data) >= offset + 3:
            result["battery_temperature_1"] = {"value": _temperature(tag_value(0x82)), "unit": "°C"}

        # Total voltage (tag 0x83)
        if len(data) >= offset + 3 and data[offset] == 0x83:
            total_voltage = tag_value(0x83) * 0.01
            result["total_voltage"] = {"value": total_voltage, "unit": "V"}
        else:
            total_voltage = 0.0

        # Current (tag 0x84). Direction/sign byte is a bit further down; we use a
        # heuristic: the byte immediately after the current value is the sign byte.
        current = 0.0
        if len(data) >= offset + 3 and data[offset] == 0x84:
            raw_current = tag_value(0x84)
            sign_byte = data[offset] if len(data) > offset else 0
            current = _current(raw_current, sign_byte)
            result["current"] = {"value": current, "unit": "A"}
            if total_voltage:
                power = total_voltage * current
                result["power"] = {"value": power, "unit": "W"}
                result["charge_power"] = {"value": max(0.0, power), "unit": "W"}
                result["discharge_power"] = {"value": abs(min(0.0, power)), "unit": "W"}

        # SOC (tag 0x85)
        if len(data) >= offset + 1 and data[offset] == 0x85:
            result["soc"] = {"value": float(data[offset + 1]), "unit": "%"}
            offset += 2

        # Temperature sensor count (tag 0x86)
        if len(data) >= offset + 2 and data[offset] == 0x86:
            result["temperature_sensor_count"] = {"value": float(data[offset + 1]), "unit": ""}
            offset += 2

        # Cycle count (tag 0x87)
        if len(data) >= offset + 3 and data[offset] == 0x87:
            result["cycle_count"] = {"value": float(_get_u16(data, offset + 1)), "unit": ""}
            offset += 3

        # Total cycle capacity (tag 0x89)
        if len(data) >= offset + 5 and data[offset] == 0x89:
            result["total_cycle_capacity"] = {"value": float(_get_u32(data, offset + 1)), "unit": "Ah"}
            offset += 5

        # Cell count / total strings (tag 0x8A)
        if len(data) >= offset + 3 and data[offset] == 0x8A:
            result["total_strings"] = {"value": float(_get_u16(data, offset + 1)), "unit": ""}
            offset += 3

        # Warning bitmask (tag 0x8B)
        if len(data) >= offset + 3 and data[offset] == 0x8B:
            result["warning_bitmask"] = {"value": float(_get_u16(data, offset + 1)), "unit": ""}
            offset += 3

        # Status / operation mode bitmask (tag 0x8C)
        if len(data) >= offset + 3 and data[offset] == 0x8C:
            mode = _get_u16(data, offset + 1)
            result["operation_mode_bitmask"] = {"value": float(mode), "unit": ""}
            result["charging_enabled"] = {"value": float(bool(mode & 0x01)), "unit": ""}
            result["discharging_enabled"] = {"value": float(bool(mode & 0x02)), "unit": ""}
            result["balancing_enabled"] = {"value": float(bool(mode & 0x04)), "unit": ""}
            offset += 3

        # Remaining capacity (tag 0xAA) and full capacity (tag 0xAA full)
        # We attempt a lightweight scan for 0xAA.
        for scan in range(offset, min(len(data) - 5, offset + 120)):
            if data[scan] == 0xAA:
                result["full_charge_capacity"] = {"value": float(_get_u32(data, scan + 1)), "unit": "Ah"}
                break

        logger.info(
            "JK BMS parsed: %d cells, %.2f V, %.2f A, %.1f%% SOC",
            cell_count,
            total_voltage,
            current,
            result.get("soc", {}).get("value", 0.0),
        )
        return result
