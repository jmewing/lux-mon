"""
LuxPower TCP Protocol Parser

Parses the proprietary LuxPower framing protocol used by EG4/LuxPower
WiFi dongles on TCP port 8000. This is NOT standard Modbus TCP.

Protocol spec: docs/reference/lux-protocol/PROTOCOL.md
"""

import struct
from dataclasses import dataclass, field
from typing import Dict, Optional

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

# ── Holding Registers (Writable, Function Code 0x06/0x10) ───────────
#
# Based on EG4 6000XP Modbus protocol and live probing.  These are the
# user-configurable parameters that the automation engine is allowed to
# modify.  Values listed are raw 16-bit integers; use the scale field to
# convert to/from engineering units.
HOLDING_REGISTERS: Dict[int, dict] = {
    # ── Power command (percent) registers 60-67 ──
    60: {"name": "active_power_percent",     "unit": "%",   "scale": 1.0,   "desc": "Active power command percent", "min": 0, "max": 100},
    61: {"name": "reactive_power_percent",   "unit": "%",   "scale": 1.0,   "desc": "Reactive power command percent", "min": 0, "max": 100},
    62: {"name": "power_factor_command",     "unit": "",    "scale": 1.0,   "desc": "Power factor command", "min": -100, "max": 100},
    63: {"name": "soft_start_slope",         "unit": "%/s", "scale": 1.0,   "desc": "Soft start slope", "min": 0, "max": 100},
    64: {"name": "charge_power_percent",     "unit": "%",   "scale": 1.0,   "desc": "Charge power percent", "min": 0, "max": 100},
    65: {"name": "discharge_power_percent",  "unit": "%",   "scale": 1.0,   "desc": "Discharge power percent", "min": 0, "max": 100},
    # NOTE: register 66 is AC_CHARGE_POWER_CMD (a *percent* command), NOT amps.
    # The real AC charge current (amps) is register 168 (AC_CHARGE_BATTERY_CURRENT).
    66: {"name": "ac_charge_power_percent",  "unit": "%",   "scale": 1.0,   "desc": "AC charge power command percent", "min": 0, "max": 100},
    67: {"name": "ac_charge_soc_limit",      "unit": "%",   "scale": 1.0,   "desc": "AC charge SOC limit", "min": 0, "max": 100},

    # ── AC charge time-of-day slots 68-73 (MM*256+HH: hour in LOW byte, minute in HIGH byte) ──
    # NOTE: encoding is hour in LSB, minute in MSB. 22:00 = 22 (0x0016), NOT 5632.
    68: {"name": "ac_charge_period_1_start", "unit": "time", "scale": 1.0, "desc": "AC charge period 1 start (min<<8)|hour", "min": 0, "max": 2359},
    69: {"name": "ac_charge_period_1_end",   "unit": "time", "scale": 1.0, "desc": "AC charge period 1 end (min<<8)|hour", "min": 0, "max": 2359},
    70: {"name": "ac_charge_period_2_start", "unit": "time", "scale": 1.0, "desc": "AC charge period 2 start (min<<8)|hour", "min": 0, "max": 2359},
    71: {"name": "ac_charge_period_2_end",   "unit": "time", "scale": 1.0, "desc": "AC charge period 2 end (min<<8)|hour", "min": 0, "max": 2359},
    72: {"name": "ac_charge_period_3_start", "unit": "time", "scale": 1.0, "desc": "AC charge period 3 start (min<<8)|hour", "min": 0, "max": 2359},
    73: {"name": "ac_charge_period_3_end",   "unit": "time", "scale": 1.0, "desc": "AC charge period 3 end (min<<8)|hour", "min": 0, "max": 2359},

    # ── Forced charge (charge priority) 74-81 ──
    74: {"name": "forced_charge_power",        "unit": "A",   "scale": 1.0, "desc": "Forced charge current", "min": 0, "max": 140},
    75: {"name": "forced_charge_soc_limit",   "unit": "%",   "scale": 1.0, "desc": "Forced charge SOC limit", "min": 0, "max": 100},
    76: {"name": "forced_charge_period_1_start", "unit": "time", "scale": 1.0, "desc": "Forced charge period 1 start", "min": 0, "max": 2359},
    77: {"name": "forced_charge_period_1_end",   "unit": "time", "scale": 1.0, "desc": "Forced charge period 1 end", "min": 0, "max": 2359},
    78: {"name": "forced_charge_period_2_start", "unit": "time", "scale": 1.0, "desc": "Forced charge period 2 start", "min": 0, "max": 2359},
    79: {"name": "forced_charge_period_2_end",   "unit": "time", "scale": 1.0, "desc": "Forced charge period 2 end", "min": 0, "max": 2359},
    80: {"name": "forced_charge_period_3_start", "unit": "time", "scale": 1.0, "desc": "Forced charge period 3 start", "min": 0, "max": 2359},
    81: {"name": "forced_charge_period_3_end",   "unit": "time", "scale": 1.0, "desc": "Forced charge period 3 end", "min": 0, "max": 2359},

    # ── Forced discharge 82-89 ──
    82: {"name": "forced_discharge_power",     "unit": "A",   "scale": 1.0, "desc": "Forced discharge current", "min": 0, "max": 140},
    83: {"name": "forced_discharge_soc_limit", "unit": "%",   "scale": 1.0, "desc": "Forced discharge SOC limit", "min": 0, "max": 100},
    84: {"name": "forced_discharge_period_1_start", "unit": "time", "scale": 1.0, "desc": "Forced discharge period 1 start", "min": 0, "max": 2359},
    85: {"name": "forced_discharge_period_1_end",   "unit": "time", "scale": 1.0, "desc": "Forced discharge period 1 end", "min": 0, "max": 2359},
    86: {"name": "forced_discharge_period_2_start", "unit": "time", "scale": 1.0, "desc": "Forced discharge period 2 start", "min": 0, "max": 2359},
    87: {"name": "forced_discharge_period_2_end",   "unit": "time", "scale": 1.0, "desc": "Forced discharge period 2 end", "min": 0, "max": 2359},
    88: {"name": "forced_discharge_period_3_start", "unit": "time", "scale": 1.0, "desc": "Forced discharge period 3 start", "min": 0, "max": 2359},
    89: {"name": "forced_discharge_period_3_end",   "unit": "time", "scale": 1.0, "desc": "Forced discharge period 3 end", "min": 0, "max": 2359},

    # ── EPS output 90-91 ──
    90: {"name": "eps_voltage_set",           "unit": "V",   "scale": 0.1,  "desc": "EPS output voltage setpoint", "min": 0, "max": 3000},
    91: {"name": "eps_frequency_set",         "unit": "Hz",  "scale": 0.01, "desc": "EPS output frequency setpoint", "min": 0, "max": 6000},

    # ── Lead-acid battery 99-109 ──
    99:  {"name": "lead_acid_charge_voltage", "unit": "V",   "scale": 0.1,  "desc": "Lead-acid charge voltage reference", "min": 50, "max": 58},
    100: {"name": "lead_acid_discharge_cut_voltage", "unit": "V", "scale": 0.1, "desc": "Lead-acid discharge cutoff voltage", "min": 40, "max": 56},
    101: {"name": "lead_acid_charge_rate",    "unit": "A",   "scale": 1.0,  "desc": "Lead-acid charge rate", "min": 0, "max": 4480},
    102: {"name": "lead_acid_discharge_rate", "unit": "A",   "scale": 1.0,  "desc": "Lead-acid discharge rate", "min": 0, "max": 4480},
    103: {"name": "feed_in_grid_power_percent", "unit": "%", "scale": 1.0,  "desc": "Feed-in grid power percent", "min": 0, "max": 255},
    105: {"name": "discharge_cutoff_soc_eod", "unit": "%",   "scale": 1.0,  "desc": "Discharge cutoff SOC (end of discharge)", "min": 10, "max": 90},

    # ── SOC low limit EPS discharge 125 ──
    125: {"name": "soc_low_limit_eps_discharge", "unit": "%", "scale": 1.0, "desc": "SOC low limit for EPS discharge", "min": 0, "max": 90},

    # ── Lead-acid voltage / battery config 144-151 ──
    144: {"name": "floating_voltage",        "unit": "V",   "scale": 0.1,  "desc": "Floating voltage", "min": 50, "max": 58},
    146: {"name": "ac_input_range",          "unit": "",    "scale": 1.0,  "desc": "AC input range (0=APL, 1=UPS)", "min": 0, "max": 1},
    147: {"name": "battery_capacity",        "unit": "Ah",  "scale": 1.0,  "desc": "Battery capacity", "min": 1, "max": 65535},
    148: {"name": "nominal_battery_voltage", "unit": "V",   "scale": 0.1,  "desc": "Nominal battery voltage", "min": 0, "max": 600},
    149: {"name": "equalization_voltage",    "unit": "V",   "scale": 0.1,  "desc": "Equalization voltage", "min": 50, "max": 59},
    150: {"name": "equalization_period",     "unit": "days","scale": 1.0,  "desc": "Equalization period", "min": 0, "max": 365},
    151: {"name": "equalization_time",       "unit": "h",   "scale": 1.0,  "desc": "Equalization time", "min": 0, "max": 24},

    # ── AC First mode time slots 152-157 ──
    152: {"name": "ac_first_period_1_start",  "unit": "time", "scale": 1.0, "desc": "AC first period 1 start (min<<8)|hour", "min": 0, "max": 2359},
    153: {"name": "ac_first_period_1_end",    "unit": "time", "scale": 1.0, "desc": "AC first period 1 end (min<<8)|hour", "min": 0, "max": 2359},
    154: {"name": "ac_first_period_2_start",  "unit": "time", "scale": 1.0, "desc": "AC first period 2 start (min<<8)|hour", "min": 0, "max": 2359},
    155: {"name": "ac_first_period_2_end",    "unit": "time", "scale": 1.0, "desc": "AC first period 2 end (min<<8)|hour", "min": 0, "max": 2359},
    156: {"name": "ac_first_period_3_start",  "unit": "time", "scale": 1.0, "desc": "AC first period 3 start (min<<8)|hour", "min": 0, "max": 2359},
    157: {"name": "ac_first_period_3_end",    "unit": "time", "scale": 1.0, "desc": "AC first period 3 end (min<<8)|hour", "min": 0, "max": 2359},

    # ── AC charge battery voltage/SOC thresholds 158-161 ──
    158: {"name": "ac_charge_start_battery_voltage", "unit": "V", "scale": 0.1, "desc": "AC charge start battery voltage", "min": 0, "max": 600},
    159: {"name": "ac_charge_end_battery_voltage",   "unit": "V", "scale": 0.1, "desc": "AC charge end battery voltage", "min": 0, "max": 600},
    160: {"name": "ac_charge_start_battery_soc",     "unit": "%", "scale": 1.0, "desc": "AC charge start battery SOC", "min": 0, "max": 100},
    161: {"name": "ac_charge_end_battery_soc",       "unit": "%", "scale": 1.0, "desc": "AC charge end battery SOC", "min": 0, "max": 100},

    # ── Battery warning / protection 162-167 ──
    162: {"name": "battery_warning_voltage",   "unit": "V",   "scale": 0.1,  "desc": "Battery warning voltage", "min": 0, "max": 600},
    163: {"name": "battery_warning_recovery_voltage", "unit": "V", "scale": 0.1, "desc": "Battery warning recovery voltage", "min": 0, "max": 600},
    164: {"name": "battery_warning_soc",       "unit": "%",   "scale": 1.0,  "desc": "Battery warning SOC", "min": 0, "max": 100},
    165: {"name": "battery_warning_recovery_soc", "unit": "%", "scale": 1.0,  "desc": "Battery warning recovery SOC", "min": 0, "max": 100},
    166: {"name": "battery_low_to_utility_voltage", "unit": "V", "scale": 0.1, "desc": "Battery low-to-utility voltage", "min": 0, "max": 600},
    167: {"name": "battery_low_to_utility_soc", "unit": "%", "scale": 1.0, "desc": "Battery low-to-utility SOC", "min": 0, "max": 100},

    # ── AC charge battery current (amps) 168 ──
    # This is the register the EG4 Monitor "AC Charge Battery Current(A)" writes.
    168: {"name": "ac_charge_battery_current", "unit": "A",   "scale": 1.0,  "desc": "AC charge battery current (amps)", "min": 0, "max": 125},

    # ── On-grid EOD voltage 169 ──
    169: {"name": "on_grid_eod_voltage",       "unit": "V",   "scale": 0.1,  "desc": "On-grid end-of-discharge voltage", "min": 40, "max": 58},

    # ── Generator 177 ──
    177: {"name": "max_generator_input_power", "unit": "W",   "scale": 1.0,  "desc": "Max generator input power", "min": 0, "max": 65534},

    # ── Quick charge toggle 233-234 (reverse-engineered from SolarAssistant) ──
    # 0x00E9 (233) = quick-charge on/off switch (0 = off, 1 = on)
    # 0x00EA (234) = quick-charge duration in minutes (0 = no charge / stop)
    #
    # IMPORTANT semantics (confirmed via tcpdump of SolarAssistant):
    #   * The DURATION register (234) is the actual charge controller. Setting
    #     it to 0 means "charge for 0 minutes" = no charge / stop.
    #   * The SWITCH register (233) only toggles the mode; it does NOT start
    #     charging on its own. Enabling the switch with duration=0 does nothing.
    #   * To START: write duration (234) first, then enable switch (233=1).
    #   * To STOP:  write switch (233=0) AND clear duration (234=0).
    233: {"name": "quick_charge_enable", "unit": "",    "scale": 1.0,  "desc": "Quick charge on/off (0=off, 1=on)", "min": 0, "max": 1},
    234: {"name": "quick_charge_duration", "unit": "min", "scale": 1.0,  "desc": "Quick charge duration (minutes, 0=stop)", "min": 0, "max": 240},
}

# Friendly display labels matching the reference portal's terminology.
# These are surfaced in the automation UI so users see "Absorption charge
# voltage" rather than the internal register name "lead_acid_charge_voltage".
HOLDING_LABELS: Dict[str, str] = {
    "lead_acid_charge_voltage": "Absorption charge voltage",
    "floating_voltage": "Float charge voltage",
    "lead_acid_charge_rate": "Max charge current",
    "ac_charge_battery_current": "Grid charge current",
    "lead_acid_discharge_rate": "Max discharge current",
    "lead_acid_discharge_cut_voltage": "Stop discharge voltage",
    "battery_warning_voltage": "Warning start voltage",
    "battery_warning_recovery_voltage": "Warning recovery voltage",
    "battery_low_to_utility_voltage": "Shutdown battery voltage",
    "on_grid_eod_voltage": "On-grid end-of-discharge voltage",
    "ac_charge_start_battery_voltage": "Grid charge start voltage",
    "ac_charge_end_battery_voltage": "Grid charge stop voltage",
    "ac_charge_start_battery_soc": "Grid charge start capacity",
    "ac_charge_end_battery_soc": "Grid charge stop capacity",
    "battery_warning_soc": "Warning start capacity",
    "battery_warning_recovery_soc": "Warning recovery capacity",
    "battery_low_to_utility_soc": "Shutdown battery capacity",
    "discharge_cutoff_soc_eod": "Stop discharge capacity",
    "soc_low_limit_eps_discharge": "SOC low limit (EPS discharge)",
    "equalization_voltage": "Equalization voltage",
    "equalization_period": "Equalization period",
    "equalization_time": "Equalization time",
    "ac_input_range": "AC input range",
    "battery_capacity": "Battery capacity",
    "nominal_battery_voltage": "Nominal battery voltage",
    "max_generator_input_power": "Generator power",
    "quick_charge_enable": "Quick charge",
    "quick_charge_duration": "Quick charge duration",
    "forced_charge_power": "Forced charge current",
    "forced_discharge_power": "Forced discharge current",
    "feed_in_grid_power_percent": "Export power rate",
    "active_power_percent": "Active power command",
    "reactive_power_percent": "Reactive power command",
    "power_factor_command": "Power factor command",
    "charge_power_percent": "Charge power command",
    "discharge_power_percent": "Discharge power command",
    "ac_charge_power_percent": "AC charge power command",
    "ac_charge_soc_limit": "AC charge SOC limit",
    "forced_charge_soc_limit": "Forced charge SOC limit",
    "forced_discharge_soc_limit": "Forced discharge SOC limit",
    "soft_start_slope": "Soft start slope",
    "eps_voltage_set": "EPS output voltage",
    "eps_frequency_set": "EPS output frequency",
}


def holding_label(name: str) -> str:
    """Return the friendly display label for a holding register, falling back
    to the register's own description (or name) when no label is defined."""
    if name in HOLDING_LABELS:
        return HOLDING_LABELS[name]
    reg = HOLDING_BY_NAME.get(name)
    if reg is not None:
        return HOLDING_REGISTERS[reg].get("desc", name)
    return name


# Build reverse lookups by name
HOLDING_BY_NAME: Dict[str, int] = {info["name"]: reg for reg, info in HOLDING_REGISTERS.items()}

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


# ── Request Builder ─────────────────────────────────────────────────

def _serial_to_bytes(s: str) -> bytes:
    """Convert a serial string to a fixed 10-byte field, space-padded."""
    b = s.encode('ascii', errors='replace')
    if len(b) < 10:
        b += b' ' * (10 - len(b))
    return b[:10]


def build_read_request(
    datalog_serial: str,
    inverter_serial: str,
    device_function: int,
    start_register: int,
    count: int,
) -> bytes:
    """
    Build a ReadHold (0x03) or ReadInput (0x04) request packet.

    Uses protocol=1 (no VLB) per the LuxPower spec for client requests.
    Total packet size: 38 bytes.

    Packet layout:
        [0-1]   Header: 0xA1 0x1A
        [2-3]   Protocol: 1 (LE u16)
        [4-5]   Frame length: 32 (LE u16)
        [6]     Unknown: 0x01
        [7]     TCP function: 0xC2 (TranslatedData)
        [8-17]  Datalog serial (10 bytes)
        [18-19] Data length: 18 (LE u16)
        [20]    Address: 0x00 (client)
        [21]    Device function (0x03 or 0x04)
        [22-31] Inverter serial (10 bytes)
        [32-33] Start register (LE u16)
        [34-35] Register count (LE u16)
        [36-37] CRC-16/Modbus over bytes [20:36]
    """
    pkt = bytearray(38)

    # Header
    pkt[0:2] = PREFIX

    # Protocol = 1 (for requests)
    struct.pack_into('<H', pkt, 2, 1)

    # Frame length = 32 (38 - 6)
    struct.pack_into('<H', pkt, 4, 32)

    # Unknown byte
    pkt[6] = 0x01

    # TCP function: TranslatedData
    pkt[7] = TCP_FUNC_TRANSLATED_DATA

    # Datalog serial
    pkt[8:18] = _serial_to_bytes(datalog_serial)

    # Data length = 18 (addr + func + serial + reg + count + crc)
    struct.pack_into('<H', pkt, 18, 18)

    # Address (client = 0)
    pkt[20] = 0x00

    # Device function
    pkt[21] = device_function

    # Inverter serial
    pkt[22:32] = _serial_to_bytes(inverter_serial)

    # Start register
    struct.pack_into('<H', pkt, 32, start_register)

    # Register count
    struct.pack_into('<H', pkt, 34, count)

    # CRC over data frame [20:36]
    crc = crc16_modbus(bytes(pkt[20:36]))
    struct.pack_into('<H', pkt, 36, crc)

    return bytes(pkt)


def build_write_request(
    datalog_serial: str,
    inverter_serial: str,
    register: int,
    value: int,
) -> bytes:
    """
    Build a WriteSingleRegister (0x06) request packet.

    Layout mirrors build_read_request but the Modbus data frame is:
        addr(1) + func(1) + inv_serial(10) + reg(2) + value(2) + crc(2)
    Total data length: 18 bytes -> outer packet: 6 + 2 + 18 = 26 bytes for header
    Full packet size is 38 bytes; frame_len = 32 (38 - 6)
    """
    if not (0 <= value <= 0xFFFF):
        raise ValueError(f"Modbus register value out of range: {value}")

    pkt = bytearray(38)
    pkt[0:2] = PREFIX
    struct.pack_into('<H', pkt, 2, 1)      # protocol
    struct.pack_into('<H', pkt, 4, 32)     # frame length
    pkt[6] = 0x01
    pkt[7] = TCP_FUNC_TRANSLATED_DATA
    pkt[8:18] = _serial_to_bytes(datalog_serial)
    struct.pack_into('<H', pkt, 18, 18)    # data length

    pkt[20] = 0x00                         # address
    pkt[21] = MODBUS_WRITE_SINGLE            # function 0x06
    pkt[22:32] = _serial_to_bytes(inverter_serial)
    struct.pack_into('<H', pkt, 32, register)
    struct.pack_into('<H', pkt, 34, value)

    crc = crc16_modbus(bytes(pkt[20:36]))
    struct.pack_into('<H', pkt, 36, crc)
    return bytes(pkt)


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

    if frame.is_read or frame.device_function == MODBUS_WRITE_SINGLE:
        # Read responses have a value length byte, then values.
        # Write-single responses echo reg+value without a length byte.
        if frame.device_function == MODBUS_WRITE_SINGLE:
            if len(data) >= reg_start + 4 + 2:  # reg + value + crc
                frame.values.append(struct.unpack_from('<H', data, reg_start + 2)[0])
            return
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
