"""
EG4 6000XP Register Map

Based on the official EG4 Modbus protocol document and validated against
live captures from a 6000XP inverter .

Register addresses are 0-based as they appear in the LuxPower TCP frames.
All values are unsigned 16-bit integers unless noted as 32-bit pairs.
"""

from dataclasses import dataclass
from typing import Optional, Callable

@dataclass
class RegisterDef:
    """Definition of a single Modbus register."""
    name: str
    unit: str
    scale: float = 1.0
    desc: str = ""
    signed: bool = False
    # For packed registers (multiple values in one register)
    bitmask: Optional[int] = None
    bitshift: int = 0
    # For 32-bit values spanning two registers
    pair_high: Optional[int] = None  # register number of the high word

    def decode(self, raw: int, high_raw: int = 0) -> float:
        """Decode a raw register value to its scaled float."""
        if self.pair_high is not None:
            # 32-bit value: low word + (high word << 16)
            val = raw + (high_raw << 16)
        elif self.bitmask is not None:
            val = (raw & self.bitmask) >> self.bitshift
        else:
            val = raw

        if self.signed and val >= 0x8000:
            val = val - 0x10000  # two's complement for 16-bit

        return val * self.scale


# ── Input Registers (Read-only, Function Code 0x04) ────────────────

INPUT_REGISTERS: dict[int, RegisterDef] = {
    # Register 0-39: Live data batch 1
    0:  RegisterDef("state",              "",     1.0,    "Operating state"),
    1:  RegisterDef("pv1_voltage",        "V",    0.1,    "PV1 voltage"),
    2:  RegisterDef("pv2_voltage",        "V",    0.1,    "PV2 voltage"),
    3:  RegisterDef("pv3_voltage",        "V",    0.1,    "PV3 voltage"),
    4:  RegisterDef("battery_voltage",    "V",    0.1,    "Battery voltage"),
    5:  RegisterDef("soc",                "%",    1.0,    "Battery SOC", bitmask=0xFF, bitshift=0),
    # SOH is also in register 5, high byte
    6:  RegisterDef("internal_fault",     "",     1.0,    "Internal fault code"),
    7:  RegisterDef("pv1_power",          "W",    1.0,    "PV1 power"),
    8:  RegisterDef("pv2_power",          "W",    1.0,    "PV2 power"),
    9:  RegisterDef("pv3_power",          "W",    1.0,    "PV3 power"),
    10: RegisterDef("charge_power",       "W",    1.0,    "Battery charge power"),
    11: RegisterDef("discharge_power",    "W",    1.0,    "Battery discharge power"),
    12: RegisterDef("grid_voltage_r",     "V",    0.1,    "Grid voltage phase R"),
    13: RegisterDef("grid_voltage_s",     "V",    0.1,    "Grid voltage phase S"),
    14: RegisterDef("grid_voltage_t",     "V",    0.1,    "Grid voltage phase T"),
    15: RegisterDef("grid_frequency",     "Hz",   0.01,   "Grid frequency"),
    16: RegisterDef("inv_power",          "W",    1.0,    "Inverter output power (grid port)"),
    17: RegisterDef("rec_power",          "W",    1.0,    "AC charging rectified power"),
    18: RegisterDef("inv_current_rms",    "A",    0.01,   "Inverter current RMS"),
    19: RegisterDef("power_factor",       "",     0.001,  "Power factor"),
    20: RegisterDef("eps_voltage_r",      "V",    0.1,    "EPS output voltage R"),
    21: RegisterDef("eps_voltage_s",      "V",    0.1,    "EPS output voltage S"),
    22: RegisterDef("eps_voltage_t",      "V",    0.1,    "EPS output voltage T"),
    23: RegisterDef("eps_frequency",      "Hz",   0.01,   "EPS output frequency"),
    24: RegisterDef("eps_power",          "W",    1.0,    "EPS output power"),
    25: RegisterDef("eps_apparent_power", "VA",   1.0,    "EPS apparent power"),
    26: RegisterDef("grid_export_power",  "W",    1.0,    "Power exported to grid"),
    27: RegisterDef("grid_import_power",  "W",    1.0,    "Power imported from grid"),
    28: RegisterDef("pv1_energy_today",   "kWh",  0.1,    "PV1 energy today"),
    29: RegisterDef("pv2_energy_today",   "kWh",  0.1,    "PV2 energy today"),
    30: RegisterDef("pv3_energy_today",   "kWh",  0.1,    "PV3 energy today"),
    31: RegisterDef("inv_energy_today",   "kWh",  0.1,    "Inverter output energy today"),
    32: RegisterDef("rec_energy_today",   "kWh",  0.1,    "AC charge rectified energy today"),
    33: RegisterDef("charge_energy_today","kWh",  0.1,    "Charged energy today"),
    34: RegisterDef("discharge_energy_today","kWh",0.1,   "Discharged energy today"),
    35: RegisterDef("eps_energy_today",   "kWh",  0.1,    "EPS output energy today"),
    36: RegisterDef("grid_export_today",  "kWh",  0.1,    "Grid export energy today"),
    37: RegisterDef("grid_import_today",  "kWh",  0.1,    "Grid import energy today"),
    38: RegisterDef("bus1_voltage",       "V",    0.1,    "Bus 1 voltage"),
    39: RegisterDef("bus2_voltage",       "V",    0.1,    "Bus 2 voltage"),

    # Register 40-79: All-time energy + temperatures
    40: RegisterDef("pv1_energy_total",   "kWh",  0.1,    "PV1 total energy (low word)", pair_high=41),
    42: RegisterDef("pv2_energy_total",   "kWh",  0.1,    "PV2 total energy (low word)", pair_high=43),
    44: RegisterDef("pv3_energy_total",   "kWh",  0.1,    "PV3 total energy (low word)", pair_high=45),
    46: RegisterDef("inv_energy_total",   "kWh",  0.1,    "Inverter total energy (low word)", pair_high=47),
    48: RegisterDef("rec_energy_total",   "kWh",  0.1,    "AC charge total energy (low word)", pair_high=49),
    50: RegisterDef("charge_energy_total","kWh",  0.1,    "Charge total energy (low word)", pair_high=51),
    52: RegisterDef("discharge_energy_total","kWh",0.1,   "Discharge total energy (low word)", pair_high=53),
    54: RegisterDef("eps_energy_total",   "kWh",  0.1,    "EPS total energy (low word)", pair_high=55),
    56: RegisterDef("grid_export_total",  "kWh",  0.1,    "Grid export total (low word)", pair_high=57),
    58: RegisterDef("grid_import_total",  "kWh",  0.1,    "Grid import total (low word)", pair_high=59),
    60: RegisterDef("fault_code",         "",     1.0,    "Fault code (low word)", pair_high=61),
    62: RegisterDef("warning_code",       "",     1.0,    "Warning code (low word)", pair_high=63),
    64: RegisterDef("temp_inverter",      "°C",   1.0,    "Internal temperature"),
    65: RegisterDef("temp_radiator_1",    "°C",   1.0,    "Radiator temperature 1"),
    66: RegisterDef("temp_radiator_2",    "°C",   1.0,    "Radiator temperature 2"),
    67: RegisterDef("temp_battery",       "°C",   1.0,    "Battery temperature"),
    69: RegisterDef("runtime",            "s",    1.0,    "Runtime (low word)", pair_high=70),
    71: RegisterDef("auto_test",          "",     1.0,    "Auto test status/step"),
    72: RegisterDef("auto_test_limit",    "",     1.0,    "Auto test limit"),
    73: RegisterDef("auto_test_default",  "ms",   1.0,    "Auto test default time"),
    74: RegisterDef("auto_test_trip",     "",     1.0,    "Auto test trip value"),
    75: RegisterDef("auto_test_trip_time","ms",   1.0,    "Auto test trip time"),
    77: RegisterDef("ac_input_type",      "",     1.0,    "0=Grid, 1=Generator"),

    # Register 80-119: BMS data
    81: RegisterDef("bms_max_charge_current",   "A", 0.01, "BMS max charge current"),
    82: RegisterDef("bms_max_discharge_current","A", 0.01, "BMS max discharge current"),
    83: RegisterDef("bms_charge_voltage_ref",   "V", 0.1,  "BMS charge voltage reference"),
    84: RegisterDef("bms_discharge_cut_voltage", "V", 0.1,  "BMS discharge cutoff voltage"),
    85: RegisterDef("bms_status_0",       "",     1.0,    "BMS status 0"),
    86: RegisterDef("bms_status_1",       "",     1.0,    "BMS status 1"),
    87: RegisterDef("bms_status_2",       "",     1.0,    "BMS status 2"),
    88: RegisterDef("bms_status_3",       "",     1.0,    "BMS status 3"),
    89: RegisterDef("bms_status_4",       "",     1.0,    "BMS status 4"),
    90: RegisterDef("bms_status_5",       "",     1.0,    "BMS status 5"),
    91: RegisterDef("bms_status_6",       "",     1.0,    "BMS status 6"),
    92: RegisterDef("bms_status_7",       "",     1.0,    "BMS status 7"),
    93: RegisterDef("bms_status_8",       "",     1.0,    "BMS status 8"),
    94: RegisterDef("bms_status_9",       "",     1.0,    "BMS status 9"),
    95: RegisterDef("bms_status_inv",     "",     1.0,    "Inverter battery status summary"),
    96: RegisterDef("battery_count",       "",     1.0,    "Number of parallel batteries"),
    97: RegisterDef("battery_capacity",   "Ah",   1.0,    "Battery capacity"),
    98: RegisterDef("battery_current",    "A",    0.1,    "Battery current (signed)", signed=True),
    99: RegisterDef("bms_fault_code",     "",     1.0,    "BMS fault code"),
    100:RegisterDef("bms_warning_code",   "",     1.0,    "BMS warning code"),
    101:RegisterDef("cell_voltage_max",   "V",    0.001,  "Maximum cell voltage"),
    102:RegisterDef("cell_voltage_min",   "V",    0.001,  "Minimum cell voltage"),
    103:RegisterDef("cell_temp_max",      "°C",   0.1,    "Maximum cell temperature", signed=True),
    104:RegisterDef("cell_temp_min",      "°C",   0.1,    "Minimum cell temperature", signed=True),
    105:RegisterDef("bms_fw_update_state","",     1.0,    "BMS firmware update state"),
    106:RegisterDef("cycle_count",        "",     1.0,    "Charge/discharge cycle count"),
    107:RegisterDef("battery_voltage_inv","V",    0.1,    "Inverter battery voltage sample"),
    108:RegisterDef("temp_t1",            "°C",   0.1,    "Temperature sensor T1"),
    109:RegisterDef("temp_t2",            "°C",   0.1,    "Temperature sensor T2"),
    110:RegisterDef("temp_t3",            "°C",   0.1,    "Temperature sensor T3"),
    111:RegisterDef("temp_t4",            "°C",   0.1,    "Temperature sensor T4"),
    112:RegisterDef("temp_t5",            "°C",   0.1,    "Temperature sensor T5"),
    113:RegisterDef("parallel_info",      "",     1.0,    "Master/slave, phase, parallel count"),

    # Register 120+: Extended data
    120:RegisterDef("bus_p_voltage",      "V",    0.1,    "Half bus voltage"),
    121:RegisterDef("gen_voltage",        "V",    0.1,    "Generator voltage"),
    122:RegisterDef("gen_frequency",      "Hz",   0.01,   "Generator frequency"),
    123:RegisterDef("gen_power",          "W",    1.0,    "Generator power"),
    124:RegisterDef("gen_energy_today",   "kWh",  0.1,    "Generator energy today"),
    125:RegisterDef("gen_energy_total",   "kWh",  0.1,    "Generator total energy (low word)", pair_high=126),
    127:RegisterDef("eps_voltage_l1n",    "V",    0.1,    "EPS L1-N voltage"),
    128:RegisterDef("eps_voltage_l2n",    "V",    0.1,    "EPS L2-N voltage"),
    129:RegisterDef("eps_power_l1",       "W",    1.0,    "EPS L1 power"),
    130:RegisterDef("eps_power_l2",       "W",    1.0,    "EPS L2 power"),
    131:RegisterDef("eps_apparent_l1",    "VA",   1.0,    "EPS L1 apparent power"),
    132:RegisterDef("eps_apparent_l2",    "VA",   1.0,    "EPS L2 apparent power"),
    133:RegisterDef("eps_energy_l1_today","kWh",  0.1,    "EPS L1 energy today"),
    134:RegisterDef("eps_energy_l2_today","kWh",  0.1,    "EPS L2 energy today"),

    # ── Load power / reactive / serial (114, 115-118, 139) ──
    # NOTE: register 114 is on-grid load power on the 12k; register 170 is the
    # general on-grid load power. Both are documented; 170 is the canonical one.
    114:RegisterDef("load_power_12k",     "W",    1.0,    "On-grid load power (12k)"),
    115:RegisterDef("serial_ascii_0_3",   "",     1.0,    "Serial number ASCII chars 0-3"),
    116:RegisterDef("serial_ascii_4_5",   "",     1.0,    "Serial number ASCII chars 4-5"),
    117:RegisterDef("serial_ascii_6_7",   "",     1.0,    "Serial number ASCII chars 6-7"),
    118:RegisterDef("serial_ascii_8_9",   "",     1.0,    "Serial number ASCII chars 8-9"),
    139:RegisterDef("reactive_power",     "Var",  1.0,    "Reactive power"),

    # ── Load power / energy (170-173) ──
    170:RegisterDef("load_power",         "W",    1.0,    "On-grid load power"),
    171:RegisterDef("load_energy_today",  "kWh",  0.1,    "Load energy today"),
    172:RegisterDef("load_energy_total",  "kWh",  0.1,    "Load total energy (low word)", pair_high=173),

    # ── NTC temps / remaining charge time (210, 214-216) ──
    210:RegisterDef("remaining_charge_time", "min", 1.0,  "Remaining charge time (one-click)"),
    214:RegisterDef("ntc_temp_indc",      "°C",   1.0,    "NTC temperature INDC"),
    215:RegisterDef("ntc_temp_dcdcl",     "°C",   1.0,    "NTC temperature DCDCL"),
    216:RegisterDef("ntc_temp_dcdch",     "°C",   1.0,    "NTC temperature DCDCH"),
}


# ── Per-battery BMS registers (5000+) ───────────────────────────────
# Each battery occupies 30 registers starting at 5000 + (n * 30).
# Offsets and scales derived from the LuxPower HA integration.
# These are read-only input registers (function code 0x04).

BATTERY_START = 5000
BATTERY_BLOCK_SIZE = 30
BATTERY_COUNT = 8  # support up to 8 parallel batteries

BATTERY_REGISTERS: dict[int, RegisterDef] = {}
for batt in range(1, BATTERY_COUNT + 1):
    base = BATTERY_START + (batt - 1) * BATTERY_BLOCK_SIZE
    defs = {
        3:  RegisterDef(f"battery_{batt}_capacity",             "Ah",   1.0,    f"Battery {batt} capacity"),
        5:  RegisterDef(f"battery_{batt}_max_charge_current",    "A",    0.1,    f"Battery {batt} max charge current"),
        6:  RegisterDef(f"battery_{batt}_max_discharge_current", "A",    0.1,    f"Battery {batt} max discharge current"),
        8:  RegisterDef(f"battery_{batt}_voltage",               "V",    0.01,   f"Battery {batt} voltage"),
        9:  RegisterDef(f"battery_{batt}_current",               "A",    0.1,    f"Battery {batt} current", signed=True),
        10: RegisterDef(f"battery_{batt}_soc",                 "%",    1.0,    f"Battery {batt} SOC", bitmask=0xFF, bitshift=0),
        11: RegisterDef(f"battery_{batt}_cycle_count",         "cycles", 1.0,  f"Battery {batt} cycle count"),
        12: RegisterDef(f"battery_{batt}_max_cell_temp",         "°C",   0.1,    f"Battery {batt} max cell temperature"),
        13: RegisterDef(f"battery_{batt}_min_cell_temp",         "°C",   0.1,    f"Battery {batt} min cell temperature"),
        14: RegisterDef(f"battery_{batt}_max_cell_voltage",    "V",    0.001,  f"Battery {batt} max cell voltage"),
        15: RegisterDef(f"battery_{batt}_min_cell_voltage",      "V",    0.001,  f"Battery {batt} min cell voltage"),
        16: RegisterDef(f"battery_{batt}_max_temp_cell",         "",     1.0,    f"Battery {batt} cell with max temperature", bitmask=0xFF00, bitshift=8),
        17: RegisterDef(f"battery_{batt}_max_voltage_cell",     "",     1.0,    f"Battery {batt} cell with max voltage", bitmask=0xFF00, bitshift=8),
        18: RegisterDef(f"battery_{batt}_firmware",              "",     1.0,    f"Battery {batt} firmware raw"),
    }
    # NOTE: SOH (high byte of register 10), min-temp-cell (low byte of 16),
    # and min-voltage-cell (low byte of 17) are packed into the SAME registers
    # as SOC / max-temp-cell / max-voltage-cell. RegisterDef supports only one
    # field per register, so those secondary fields are decoded directly by the
    # /api/batteries endpoint from raw registers rather than via this map.
    for offset, regdef in defs.items():
        BATTERY_REGISTERS[base + offset] = regdef

    # Serial number spans registers 19-32 (14 registers = 28 bytes).
    for i in range(14):
        BATTERY_REGISTERS[base + 19 + i] = RegisterDef(
            f"battery_{batt}_serial_{i}", "", 1.0, f"Battery {batt} serial char pair {i}"
        )

# Merge battery registers into the global input register map.
INPUT_REGISTERS.update(BATTERY_REGISTERS)


def decode_battery_serial(reg_values: dict[int, int], battery_index: int = 1) -> str:
    """Decode the 14-register ASCII serial number for a given battery."""
    base = BATTERY_START + (battery_index - 1) * BATTERY_BLOCK_SIZE
    chars = []
    for i in range(14):
        raw = reg_values.get(base + 19 + i, 0)
        # Each register holds two bytes, big-endian: high byte first.
        chars.append(chr((raw >> 8) & 0xFF))
        chars.append(chr(raw & 0xFF))
    serial = "".join(chars).rstrip("\x00").strip()
    return serial

# SOH is packed in register 5 high byte
SOH_REGISTER = RegisterDef("soh", "%", 1.0, "Battery SOH", bitmask=0xFF00, bitshift=8)


def decode_registers(reg_values: dict[int, int], register_map: dict[int, RegisterDef] = None) -> dict:
    """
    Decode a dict of {register_number: raw_value} into named/scaled values.

    Handles 32-bit pairs, packed registers, and signed values.
    Returns a dict of {name: {"value": float, "unit": str, "raw": int}}.

    An optional register_map overrides the global INPUT_REGISTERS, which is
    used by model drivers to supply their own register definitions.
    """
    if register_map is None:
        register_map = INPUT_REGISTERS
    result = {}

    # First pass: decode simple registers
    for reg_num, raw_val in reg_values.items():
        if reg_num in register_map:
            reg_def = register_map[reg_num]
            if reg_def.pair_high is not None:
                continue  # Skip low words, handled in second pass
            val = reg_def.decode(raw_val)
            result[reg_def.name] = {
                "value": val,
                "unit": reg_def.unit,
                "raw": raw_val,
                "desc": reg_def.desc,
            }

    # Second pass: 32-bit pairs
    for reg_num, raw_val in reg_values.items():
        if reg_num in register_map:
            reg_def = register_map[reg_num]
            if reg_def.pair_high is not None:
                high_reg = reg_def.pair_high
                if high_reg in reg_values:
                    val = reg_def.decode(raw_val, reg_values[high_reg])
                    result[reg_def.name] = {
                        "value": val,
                        "unit": reg_def.unit,
                        "raw": raw_val,
                        "raw_high": reg_values[high_reg],
                        "desc": reg_def.desc,
                    }

    # SOH from register 5 high byte
    if 5 in reg_values:
        soh_val = SOH_REGISTER.decode(reg_values[5])
        result["soh"] = {
            "value": soh_val,
            "unit": "%",
            "raw": reg_values[5],
            "desc": "Battery state of health",
        }

    return result
