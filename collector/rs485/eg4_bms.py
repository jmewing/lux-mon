"""EG4 LL battery BMS driver via Modbus RTU.

Implements the EG4-LL Modbus protocol used by EG4 rack/wall-mount LiFePO4
batteries. Based on the EG4-LL MODBUS communication protocol and the
esphome-eg4-bms reference implementation.

Register map:
- 0x0000 total voltage (0.01 V)
- 0x0001 current (0.01 A, signed)
- 0x0002-0x0011 cell voltages 1-16 (mV -> 0.001 V)
- 0x0012 PCB/MOSFET temperature (°C, signed)
- 0x0013 average temperature (°C, signed)
- 0x0014 max temperature (°C, signed)
- 0x0015 remaining capacity (Ah)
- 0x0016 max charge current (A)
- 0x0017 state of health (%)
- 0x0018 state of charge (%)
- 0x0019 status word
- 0x001A warning word
- 0x001B protection word
- 0x001C error word
- 0x001D-0x001E cycle count (32-bit)
- 0x0021-0x0023 temperature sensors 1-6 (packed int8)
- 0x0025 full capacity (0.1 Ah)
- 0x0069 model string (22 bytes)
- 0x0075 firmware version (6 bytes)
- 0x0078 serial number (16 bytes)

Status word bits (low nibble):
  0x00 Standby, 0x01 Charging, 0x02 Discharging, 0x04 Protect, 0x08 Charging Limited
"""

from __future__ import annotations

import struct
from typing import Any, Dict, Optional, Tuple

from . import Rs485Device, Rs485DeviceConfig


# Modbus function code
FUNC_READ_HOLDING = 0x03

# Default BMS address for EG4 batteries
DEFAULT_SLAVE_ADDRESS = 0x10

# Register blocks we poll cyclically
BLOCK_MAIN = (0x0000, 0x12)      # voltage, current, 16 cells
BLOCK_STATUS = (0x0012, 0x15)    # temps, capacity, SOH, SOC, status, warnings, errors
BLOCK_MODEL = (0x0069, 0x0B)     # 22 bytes model
BLOCK_FW = (0x0075, 0x03)        # 6 bytes firmware
BLOCK_SERIAL = (0x0078, 0x08)    # 16 bytes serial

POLL_SEQUENCE = [BLOCK_MAIN, BLOCK_STATUS, BLOCK_MODEL, BLOCK_FW, BLOCK_SERIAL]


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _build_read_request(slave: int, start: int, count: int) -> bytes:
    pdu = struct.pack(">BBHH", slave, FUNC_READ_HOLDING, start, count)
    return pdu + struct.pack("<H", _crc16(pdu))


class Eg4BmsDevice(Rs485Device):
    """EG4 LL BMS Modbus RTU driver."""

    name = "eg4_bms"
    description = "EG4 LL battery BMS via Modbus RTU"

    def __init__(self, cfg: Rs485DeviceConfig):
        super().__init__(cfg)
        self.slave = int(cfg.extra.get("slave_address", DEFAULT_SLAVE_ADDRESS))
        self.poll_index = 0
        self._last_text: Dict[str, str] = {}

    def _read_u16(self, payload: bytes, offset: int) -> int:
        return struct.unpack_from(">H", payload, offset)[0]

    def _read_s16(self, payload: bytes, offset: int) -> int:
        return struct.unpack_from(">h", payload, offset)[0]

    def _read_u32(self, payload: bytes, offset: int) -> int:
        return struct.unpack_from(">I", payload, offset)[0]

    def poll_request(self) -> bytes:
        start, count = POLL_SEQUENCE[self.poll_index]
        self.poll_index = (self.poll_index + 1) % len(POLL_SEQUENCE)
        return _build_read_request(self.slave, start, count)

    def parse_response(self, data: bytes) -> Optional[Dict[str, Dict[str, Any]]]:
        if len(data) < 5:
            return None

        slave = data[0]
        if slave != self.slave:
            return None

        function = data[1]
        if function & 0x80:
            # Modbus error
            return {"modbus_error": {"value": function, "unit": "code"}}
        if function != FUNC_READ_HOLDING:
            return None

        byte_count = data[2]
        if len(data) < 3 + byte_count + 2:
            return None

        payload = data[3:3 + byte_count]
        calc_crc = _crc16(data[:-2])
        recv_crc = struct.unpack("<H", data[-2:])[0]
        if calc_crc != recv_crc:
            return None

        result: Dict[str, Dict[str, Any]] = {}

        if byte_count == 0x24:  # 36 bytes: voltage, current, 16 cells
            total_voltage = self._read_u16(payload, 0) * 0.01
            current_raw = self._read_s16(payload, 2)
            current = current_raw * 0.01
            power = round(total_voltage * current, 2)

            result["voltage"] = {"value": round(total_voltage, 2), "unit": "V"}
            result["current"] = {"value": round(current, 2), "unit": "A"}
            result["power"] = {"value": power, "unit": "W"}

            min_cell = 10.0
            max_cell = 0.0
            sum_cell = 0.0
            min_cell_num = 0
            max_cell_num = 0
            valid_cells = 0

            for i in range(16):
                cell_mv = self._read_u16(payload, 4 + i * 2)
                if 0 < cell_mv < 5000:
                    cell_v = cell_mv * 0.001
                    result[f"cell_{i + 1}_voltage"] = {"value": round(cell_v, 3), "unit": "V"}
                    if cell_v < min_cell:
                        min_cell = cell_v
                        min_cell_num = i + 1
                    if cell_v > max_cell:
                        max_cell = cell_v
                        max_cell_num = i + 1
                    sum_cell += cell_v
                    valid_cells += 1
                else:
                    result[f"cell_{i + 1}_voltage"] = {"value": None, "unit": "V"}

            if valid_cells:
                result["cell_min_voltage"] = {"value": round(min_cell, 3), "unit": "V"}
                result["cell_max_voltage"] = {"value": round(max_cell, 3), "unit": "V"}
                result["cell_delta_voltage"] = {"value": round(max_cell - min_cell, 3), "unit": "V"}
                result["cell_avg_voltage"] = {"value": round(sum_cell / valid_cells, 3), "unit": "V"}
                result["cell_min_number"] = {"value": min_cell_num, "unit": ""}
                result["cell_max_number"] = {"value": max_cell_num, "unit": ""}

        elif byte_count == 0x2A:  # 42 bytes: temps, capacity, SOC/SOH, status
            result["pcb_temperature"] = {"value": self._read_s16(payload, 0), "unit": "°C"}
            result["avg_temperature"] = {"value": self._read_s16(payload, 2), "unit": "°C"}
            result["max_temperature"] = {"value": self._read_s16(payload, 4), "unit": "°C"}
            result["remaining_capacity"] = {"value": self._read_u16(payload, 6), "unit": "Ah"}
            result["max_charge_current"] = {"value": self._read_u16(payload, 8), "unit": "A"}
            result["soh"] = {"value": self._read_u16(payload, 10), "unit": "%"}
            result["soc"] = {"value": self._read_u16(payload, 12), "unit": "%"}

            status = self._read_u16(payload, 14)
            result["status_word"] = {"value": status, "unit": ""}
            result["status_text"] = {"value": self._decode_status(status), "unit": ""}
            result["heating"] = {"value": 1 if (status & 0x8000) else 0, "unit": ""}

            warnings = self._read_u16(payload, 16)
            result["warnings_word"] = {"value": warnings, "unit": ""}
            result["warnings_text"] = {"value": self._decode_warnings(warnings), "unit": ""}

            protection = self._read_u16(payload, 18)
            result["protection_word"] = {"value": protection, "unit": ""}
            result["protection_text"] = {"value": self._decode_protection(protection), "unit": ""}
            result["charging_allowed"] = {
                "value": 1 if not (protection & 0x0503) else 0,
                "unit": "",
            }
            result["discharging_allowed"] = {
                "value": 1 if not (protection & 0x2E0C) else 0,
                "unit": "",
            }

            error = self._read_u16(payload, 20)
            result["error_word"] = {"value": error, "unit": ""}
            result["error_text"] = {"value": self._decode_error(error), "unit": ""}

            result["cycle_count"] = {"value": self._read_u32(payload, 22), "unit": ""}

            # Temperature sensors 1-6 packed as signed bytes
            for i in range(6):
                result[f"temperature_{i + 1}"] = {"value": int.from_bytes(payload[30 + i:i + 31], "big", signed=True), "unit": "°C"}

            full_capacity = self._read_u16(payload, 38)
            result["full_capacity"] = {"value": round(full_capacity * 0.1, 1), "unit": "Ah"}

        elif byte_count == 0x16:  # 22 bytes: model
            result["model"] = {"value": self._extract_ascii(payload), "unit": ""}
        elif byte_count == 0x06:  # 6 bytes: firmware version
            result["firmware"] = {"value": self._extract_ascii(payload), "unit": ""}
        elif byte_count == 0x10:  # 16 bytes: serial number
            result["serial"] = {"value": self._extract_ascii(payload), "unit": ""}

        return result

    @staticmethod
    def _extract_ascii(data: bytes) -> str:
        s = ""
        for b in data:
            if b == 0:
                break
            if 0x20 <= b <= 0x7E:
                s += chr(b)
        return s.rstrip()

    @staticmethod
    def _decode_status(status: int) -> str:
        heating = status & 0x8000
        state = status & 0x000F
        states = {0: "Standby", 1: "Charging", 2: "Discharging", 4: "Protect", 8: "Charging Limited"}
        text = "Heating+" if heating else ""
        text += states.get(state, f"Unknown({state})")
        return text

    @staticmethod
    def _decode_warnings(warnings: int) -> str:
        if warnings == 0:
            return "None"
        flags = [
            (0x0001, "Pack OV"), (0x0002, "Cell OV"), (0x0004, "Pack UV"),
            (0x0008, "Cell UV"), (0x0010, "Charge OC"), (0x0020, "Discharge OC"),
            (0x0040, "Abnormal Temp"), (0x0080, "MOS Overheat"),
            (0x0100, "Charge OT"), (0x0200, "Discharge OT"),
            (0x0400, "Charge UT"), (0x0800, "Discharge UT"),
            (0x1000, "Low Capacity"), (0x2000, "Float Stopped"),
        ]
        return "; ".join(name for bit, name in flags if warnings & bit) or f"Unknown({warnings})"

    @staticmethod
    def _decode_protection(protection: int) -> str:
        if protection == 0:
            return "None"
        flags = [
            (0x0001, "Pack OV"), (0x0002, "Cell OV"), (0x0004, "Pack UV"),
            (0x0008, "Cell UV"), (0x0010, "Charge OC"), (0x0020, "Discharge OC"),
            (0x0040, "Abnormal Temp"), (0x0080, "MOS Overheat"),
            (0x0100, "Charge OT"), (0x0200, "Discharge OT"),
            (0x0400, "Charge UT"), (0x0800, "Discharge UT"),
            (0x1000, "Low Capacity"), (0x2000, "Discharge SC"),
        ]
        return "; ".join(name for bit, name in flags if protection & bit) or f"Unknown({protection})"

    @staticmethod
    def _decode_error(error: int) -> str:
        if error == 0:
            return "None"
        flags = [
            (0x0001, "Voltage Error"), (0x0002, "Temperature Error"),
            (0x0004, "Current Flow Error"), (0x0010, "Cell Unbalance"),
        ]
        return "; ".join(name for bit, name in flags if error & bit) or f"Unknown({error})"
