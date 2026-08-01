"""
LuxPower TCP Protocol Parser

Parses the proprietary LuxPower framing protocol used by EG4/LuxPower
WiFi dongles on TCP port 8000. This is NOT standard Modbus TCP.

Protocol spec: docs/reference/lux-protocol/PROTOCOL.md
"""

import struct
from dataclasses import dataclass, field
from typing import Optional

# ── Protocol Constants ──────────────────────────────────────────────

PREFIX = bytes([0xA1, 0x1A])
HEADER_SIZE = 18  # prefix(2) + protocol(2) + frame_len(2) + unknown(1) + tcp_func(1) + serial(10)

TCP_FUNC_HEARTBEAT = 0xC1
TCP_FUNC_TRANSLATED_DATA = 0xC2
TCP_FUNC_READ_PARAM = 0xC3
TCP_FUNC_WRITE_PARAM = 0xC4

MODBUS_READ_HOLD = 3
MODBUS_READ_INPUT = 4
MODBUS_WRITE_SINGLE = 6
MODBUS_WRITE_MULTI = 16

# ── Data Classes ────────────────────────────────────────────────────

@dataclass
class LuxFrame:
    """A parsed LuxPower TCP frame."""
    protocol: int
    tcp_function: int
    datalog_serial: str
    raw: bytes = field(repr=False)

    # Data frame fields (for TranslatedData)
    address: int = 0
    device_function: int = 0
    inverter_serial: str = ""
    register: int = 0
    values: list[int] = field(default_factory=list)
    is_error: bool = False
    error_code: int = 0

    @property
    def is_heartbeat(self) -> bool:
        return self.tcp_function == TCP_FUNC_HEARTBEAT

    @property
    def is_translated_data(self) -> bool:
        return self.tcp_function == TCP_FUNC_TRANSLATED_DATA

    @property
    def is_read(self) -> bool:
        return self.device_function in (MODBUS_READ_HOLD, MODBUS_READ_INPUT)

    @property
    def is_read_input(self) -> bool:
        return self.device_function == MODBUS_READ_INPUT

    @property
    def is_read_hold(self) -> bool:
        return self.device_function == MODBUS_READ_HOLD


# ── CRC16/MODBUS ───────────────────────────────────────────────────

CRC16_TABLE = [
    0x0000, 0xC0C1, 0xC181, 0x0140, 0xC301, 0x03C0, 0x0280, 0xC241,
    0xC601, 0x06C0, 0x0780, 0xC741, 0x0500, 0xC5C1, 0xC481, 0x0440,
    0xCC01, 0x0CC0, 0x0D80, 0xCD41, 0x0F00, 0xCFC1, 0xCE81, 0x0E40,
    0x0A00, 0xCAC1, 0xCB81, 0x0B40, 0xC901, 0x09C0, 0x0880, 0xC841,
    0xD801, 0x18C0, 0x1980, 0xD941, 0x1B00, 0xDBC1, 0xDA81, 0x1A40,
    0x1E00, 0xDEC1, 0xDF81, 0x1F40, 0xDD01, 0x1DC0, 0x1C80, 0xDC41,
    0x1400, 0xD4C1, 0xD581, 0x1540, 0xD701, 0x17C0, 0x1680, 0xD641,
    0xD201, 0x12C0, 0x1380, 0xD341, 0x1100, 0xD1C1, 0xD081, 0x1040,
    0xF001, 0x30C0, 0x3180, 0xF141, 0x3300, 0xF3C1, 0xF281, 0x3240,
    0x3600, 0xF6C1, 0xF781, 0x3740, 0xF501, 0x35C0, 0x3480, 0xF441,
    0x3C00, 0xFCC1, 0xFD81, 0x3D40, 0xFF01, 0x3FC0, 0x3E80, 0xFE41,
    0xFA01, 0x3AC0, 0x3B80, 0xFB41, 0x3900, 0xF9C1, 0xF881, 0x3840,
    0x2800, 0xE8C1, 0xE981, 0x2940, 0xEB01, 0x2BC0, 0x2A80, 0xEA41,
    0xEE01, 0x2EC0, 0x2F80, 0xEF41, 0x2D00, 0xEDC1, 0xEC81, 0x2C40,
    0xE401, 0x24C0, 0x2580, 0xE541, 0x2700, 0xE7C1, 0xE681, 0x2640,
    0x2200, 0xE2C1, 0xE381, 0x2340, 0xE101, 0x21C0, 0x2080, 0xE041,
    0xA001, 0x60C0, 0x6180, 0xA141, 0x6300, 0xA3C1, 0xA281, 0x6240,
    0x6600, 0xA6C1, 0xA781, 0x6740, 0xA501, 0x65C0, 0x6480, 0xA441,
    0x6C00, 0xACC1, 0xAD81, 0x6D40, 0xAF01, 0x6FC0, 0x6E80, 0xAE41,
    0xAA01, 0x6AC0, 0x6B80, 0xAB41, 0x6900, 0xA9C1, 0xA881, 0x6840,
    0x7800, 0xB8C1, 0xB981, 0x7940, 0xBB01, 0x7BC0, 0x7A80, 0xBA41,
    0xBE01, 0x7EC0, 0x7F80, 0xBF41, 0x7D00, 0xBDC1, 0xBC81, 0x7C40,
    0xB401, 0x74C0, 0x7580, 0xB541, 0x7700, 0xB7C1, 0xB681, 0x7640,
    0x7200, 0xB2C1, 0xB381, 0x7340, 0xB101, 0x71C0, 0x7080, 0xB041,
    0x5000, 0x90C1, 0x9181, 0x5140, 0x9301, 0x53C0, 0x5280, 0x9241,
    0x9601, 0x56C0, 0x5780, 0x9741, 0x5500, 0x95C1, 0x9481, 0x5440,
    0x9C01, 0x5CC0, 0x5D80, 0x9D41, 0x5F00, 0x9FC1, 0x9E81, 0x5E40,
    0x5A00, 0x9AC1, 0x9B81, 0x5B40, 0x9901, 0x59C0, 0x5880, 0x9841,
    0x8801, 0x48C0, 0x4980, 0x8941, 0x4B00, 0x8BC1, 0x8A81, 0x4A40,
    0x4E00, 0x8EC1, 0x8F81, 0x4F40, 0x8D01, 0x4DC0, 0x4C80, 0x8C41,
    0x4400, 0x84C1, 0x8581, 0x4540, 0x8701, 0x47C0, 0x4680, 0x8641,
    0x8201, 0x42C0, 0x4380, 0x8341, 0x4100, 0x81C1, 0x8081, 0x4040,
]


def crc16_modbus(data: bytes) -> int:
    """Calculate CRC-16/MODBUS of data bytes."""
    crc = 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ CRC16_TABLE[(crc ^ b) & 0xFF]
    return crc


# ── Frame Parser ────────────────────────────────────────────────────

def parse_frame(data: bytes) -> Optional[LuxFrame]:
    """
    Parse a single LuxPower TCP frame.

    Returns a LuxFrame on success, None if the frame is invalid.
    """
    if len(data) < HEADER_SIZE:
        return None

    if data[0:2] != PREFIX:
        return None

    protocol = struct.unpack_from('<H', data, 2)[0]
    frame_length = struct.unpack_from('<H', data, 4)[0]
    unknown = data[6]
    tcp_func = data[7]
    datalog_serial = data[8:18].decode('ascii', errors='replace').rstrip('\x00')

    frame = LuxFrame(
        protocol=protocol,
        tcp_function=tcp_func,
        datalog_serial=datalog_serial,
        raw=data,
    )

    # Parse data frame for TranslatedData (0xC2)
    if tcp_func == TCP_FUNC_TRANSLATED_DATA and len(data) >= 20:
        _parse_data_frame(data, frame)

    return frame


def _parse_data_frame(data: bytes, frame: LuxFrame) -> None:
    """Parse the inner Modbus data frame within a TranslatedData packet."""
    # Data frame starts at offset 18
    # Bytes 18-19: data length prefix (LE u16)
    data_len = struct.unpack_from('<H', data, 18)[0]

    # The actual Modbus frame starts at offset 20
    modbus_start = 20
    if len(data) < modbus_start + 2:
        return

    frame.address = data[modbus_start]
    frame.device_function = data[modbus_start + 1]

    # Check for error response (function code + 128)
    if frame.device_function >= 128:
        frame.is_error = True
        if len(data) > modbus_start + 2:
            frame.error_code = data[modbus_start + 2]
        return

    # Inverter serial at bytes 22-31 (offset 2-11 in modbus frame)
    inv_start = modbus_start + 2
    if len(data) >= inv_start + 10:
        frame.inverter_serial = data[inv_start:inv_start + 10].decode('ascii', errors='replace').rstrip('\x00')

    # Register and values
    reg_start = inv_start + 10  # offset 12 in modbus frame
    if len(data) >= reg_start + 2:
        frame.register = struct.unpack_from('<H', data, reg_start)[0]

    if frame.is_read:
        # Read responses have a value length byte, then values
        val_start = reg_start + 2
        if len(data) > val_start:
            val_len = data[val_start]
            vals_begin = val_start + 1
            # Each register value is 2 bytes (u16 LE)
            num_regs = val_len // 2
            for i in range(num_regs):
                offset = vals_begin + i * 2
                if offset + 2 <= len(data) - 2:  # -2 for CRC
                    val = struct.unpack_from('<H', data, offset)[0]
                    frame.values.append(val)


def find_frames(data: bytes) -> list[LuxFrame]:
    """
    Scan a byte buffer for LuxPower frames and parse them.

    Returns a list of parsed LuxFrame objects.
    """
    frames = []
    i = 0
    while i < len(data) - 1:
        if data[i] == PREFIX[0] and data[i + 1] == PREFIX[1]:
            if i + 6 <= len(data):
                frame_len = struct.unpack_from('<H', data, i + 4)[0]
                total = 6 + frame_len
                if i + total <= len(data):
                    frame = parse_frame(data[i:i + total])
                    if frame:
                        frames.append(frame)
                    i += total
                    continue
        i += 1
    return frames


# ── Register Maps ───────────────────────────────────────────────────

# Input registers (read-only, live data) — from lxp-bridge doc/inputs.md
# These are the register *offsets* within each batch
INPUT_REGISTERS = {
    # Batch 1: registers 0-39
    0:  {"name": "v_pv_1",         "unit": "V",   "scale": 0.1,  "desc": "PV1 voltage"},
    1:  {"name": "v_pv_2",         "unit": "V",   "scale": 0.1,  "desc": "PV2 voltage"},
    2:  {"name": "v_pv_3",         "unit": "V",   "scale": 0.1,  "desc": "PV3 voltage"},
    3:  {"name": "v_bat",          "unit": "V",   "scale": 0.1,  "desc": "Battery voltage"},
    4:  {"name": "soc",            "unit": "%",   "scale": 1,    "desc": "Battery state of charge"},
    5:  {"name": "soh",            "unit": "%",   "scale": 1,    "desc": "Battery state of health"},
    6:  {"name": "p_pv_1",         "unit": "W",   "scale": 1,    "desc": "PV1 power"},
    7:  {"name": "p_pv_2",         "unit": "W",   "scale": 1,    "desc": "PV2 power"},
    8:  {"name": "p_pv_3",         "unit": "W",   "scale": 1,    "desc": "PV3 power"},
    9:  {"name": "p_pv",           "unit": "W",   "scale": 1,    "desc": "Total PV power"},
    10: {"name": "p_charge",       "unit": "W",   "scale": 1,    "desc": "Battery charge power"},
    11: {"name": "p_discharge",    "unit": "W",   "scale": 1,    "desc": "Battery discharge power"},
    12: {"name": "v_ac_r",         "unit": "V",   "scale": 0.1,  "desc": "Grid voltage R"},
    13: {"name": "v_ac_s",         "unit": "V",   "scale": 0.1,  "desc": "Grid voltage S"},
    14: {"name": "v_ac_t",         "unit": "V",   "scale": 0.1,  "desc": "Grid voltage T"},
    15: {"name": "f_ac",           "unit": "Hz",  "scale": 0.01, "desc": "Grid frequency"},
    16: {"name": "p_inv",          "unit": "W",   "scale": 1,    "desc": "Inverter power"},
    17: {"name": "p_rec",          "unit": "W",   "scale": 1,    "desc": "Rectifier power"},
    18: {"name": "pf",             "unit": "",    "scale": 0.001,"desc": "Power factor"},
    19: {"name": "v_eps_r",        "unit": "V",   "scale": 0.1,  "desc": "EPS voltage R"},
    20: {"name": "v_eps_s",        "unit": "V",   "scale": 0.1,  "desc": "EPS voltage S"},
    21: {"name": "v_eps_t",        "unit": "V",   "scale": 0.1,  "desc": "EPS voltage T"},
    22: {"name": "f_eps",          "unit": "Hz",  "scale": 0.01, "desc": "EPS frequency"},
    23: {"name": "p_to_grid",      "unit": "W",   "scale": 1,    "desc": "Power to grid"},
    24: {"name": "p_to_user",      "unit": "W",   "scale": 1,    "desc": "Power to user/load"},
    25: {"name": "e_pv_day",       "unit": "kWh", "scale": 0.1,  "desc": "PV energy today"},
    26: {"name": "e_pv_day_1",     "unit": "kWh", "scale": 0.1,  "desc": "PV1 energy today"},
    27: {"name": "e_pv_day_2",     "unit": "kWh", "scale": 0.1,  "desc": "PV2 energy today"},
    28: {"name": "e_pv_day_3",     "unit": "kWh", "scale": 0.1,  "desc": "PV3 energy today"},
    29: {"name": "e_inv_day",      "unit": "kWh", "scale": 0.1,  "desc": "Inverter energy today"},
    30: {"name": "e_rec_day",      "unit": "kWh", "scale": 0.1,  "desc": "Rectifier energy today"},
    31: {"name": "e_chg_day",      "unit": "kWh", "scale": 0.1,  "desc": "Charge energy today"},
    32: {"name": "e_dischg_day",   "unit": "kWh", "scale": 0.1,  "desc": "Discharge energy today"},
    33: {"name": "e_eps_day",      "unit": "kWh", "scale": 0.1,  "desc": "EPS energy today"},
    34: {"name": "e_to_grid_day",  "unit": "kWh", "scale": 0.1,  "desc": "Grid export today"},
    35: {"name": "e_to_user_day",  "unit": "kWh", "scale": 0.1,  "desc": "Grid import today"},
    36: {"name": "v_bus_1",        "unit": "V",   "scale": 0.1,  "desc": "Bus voltage 1"},
    37: {"name": "v_bus_2",        "unit": "V",   "scale": 0.1,  "desc": "Bus voltage 2"},
    38: {"name": "status",         "unit": "",    "scale": 1,    "desc": "Status bitfield"},
    39: {"name": "v_pv",           "unit": "V",   "scale": 0.1,  "desc": "Total PV voltage"},
}

# Batch 2: registers 40-79
INPUT_REGISTERS_2 = {
    40: {"name": "e_pv_all",       "unit": "kWh", "scale": 0.1,  "desc": "PV energy all-time"},
    41: {"name": "e_pv_all_1",     "unit": "kWh", "scale": 0.1,  "desc": "PV1 energy all-time"},
    42: {"name": "e_pv_all_2",     "unit": "kWh", "scale": 0.1,  "desc": "PV2 energy all-time"},
    43: {"name": "e_pv_all_3",     "unit": "kWh", "scale": 0.1,  "desc": "PV3 energy all-time"},
    44: {"name": "e_inv_all",      "unit": "kWh", "scale": 0.1,  "desc": "Inverter energy all-time"},
    45: {"name": "e_rec_all",      "unit": "kWh", "scale": 0.1,  "desc": "Rectifier energy all-time"},
    46: {"name": "e_chg_all",      "unit": "kWh", "scale": 0.1,  "desc": "Charge energy all-time"},
    47: {"name": "e_dischg_all",   "unit": "kWh", "scale": 0.1,  "desc": "Discharge energy all-time"},
    48: {"name": "e_eps_all",      "unit": "kWh", "scale": 0.1,  "desc": "EPS energy all-time"},
    49: {"name": "e_to_grid_all",  "unit": "kWh", "scale": 0.1,  "desc": "Grid export all-time"},
    50: {"name": "e_to_user_all",  "unit": "kWh", "scale": 0.1,  "desc": "Grid import all-time"},
    51: {"name": "t_inner",        "unit": "°C",  "scale": 1,    "desc": "Inverter temperature"},
    52: {"name": "t_rad_1",        "unit": "°C",  "scale": 1,    "desc": "Radiator temp 1"},
    53: {"name": "t_rad_2",        "unit": "°C",  "scale": 1,    "desc": "Radiator temp 2"},
    54: {"name": "t_bat",          "unit": "°C",  "scale": 1,    "desc": "Battery temperature"},
    55: {"name": "runtime_lo",     "unit": "s",   "scale": 1,    "desc": "Runtime (low word)"},
    56: {"name": "runtime_hi",     "unit": "s",   "scale": 1,    "desc": "Runtime (high word)"},
}

# Batch 3: registers 80-119
INPUT_REGISTERS_3 = {
    80: {"name": "max_chg_curr",   "unit": "A",   "scale": 0.1,  "desc": "Max charge current"},
    81: {"name": "max_dischg_curr","unit": "A",   "scale": 0.1,  "desc": "Max discharge current"},
    82: {"name": "charge_volt_ref","unit": "V",   "scale": 0.1,  "desc": "Charge voltage ref"},
    83: {"name": "dischg_cut_volt","unit": "V",   "scale": 0.1,  "desc": "Discharge cutoff voltage"},
    84: {"name": "bat_status_0",   "unit": "",    "scale": 1,    "desc": "Battery status 0"},
    85: {"name": "bat_status_1",   "unit": "",    "scale": 1,    "desc": "Battery status 1"},
    86: {"name": "bat_status_2",   "unit": "",    "scale": 1,    "desc": "Battery status 2"},
    87: {"name": "bat_status_3",   "unit": "",    "scale": 1,    "desc": "Battery status 3"},
    88: {"name": "bat_status_4",   "unit": "",    "scale": 1,    "desc": "Battery status 4"},
    89: {"name": "bat_status_5",   "unit": "",    "scale": 1,    "desc": "Battery status 5"},
    90: {"name": "bat_status_6",   "unit": "",    "scale": 1,    "desc": "Battery status 6"},
    91: {"name": "bat_status_7",   "unit": "",    "scale": 1,    "desc": "Battery status 7"},
    92: {"name": "bat_status_8",   "unit": "",    "scale": 1,    "desc": "Battery status 8"},
    93: {"name": "bat_status_9",   "unit": "",    "scale": 1,    "desc": "Battery status 9"},
    94: {"name": "bat_status_inv", "unit": "",    "scale": 1,    "desc": "Battery status inverter"},
    95: {"name": "bat_count",      "unit": "",    "scale": 1,    "desc": "Battery count"},
    96: {"name": "bat_capacity",   "unit": "Ah",  "scale": 1,    "desc": "Battery capacity"},
}

# Combined map
ALL_INPUT_REGISTERS = {}
ALL_INPUT_REGISTERS.update(INPUT_REGISTERS)
ALL_INPUT_REGISTERS.update(INPUT_REGISTERS_2)
ALL_INPUT_REGISTERS.update(INPUT_REGISTERS_3)


def decode_register_values(frame: LuxFrame) -> dict:
    """
    Decode register values from a parsed frame into named fields with scaling applied.
    """
    result = {}
    reg_map = ALL_INPUT_REGISTERS if frame.is_read_input else {}

    for i, raw_val in enumerate(frame.values):
        reg_num = frame.register + i
        if reg_num in reg_map:
            info = reg_map[reg_num]
            scaled = raw_val * info["scale"]
            result[info["name"]] = {
                "value": scaled,
                "raw": raw_val,
                "unit": info["unit"],
                "desc": info["desc"],
            }

    return result
