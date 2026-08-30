"""EG4 18KPV-12LV register map.

The 18KPV is a larger Luxpower-family hybrid inverter (12 kW, 3 MPPT,
split-phase 120/240 V, generator input, AFCI arc-fault detection). It shares
the SNA register family but extends it with additional input registers
(AFCI, generator, split-phase EPS) and a much larger holding-register set.

Source: `docs/reference/eg4-bridge/doc/EG4-18KPV-12LV-Modbus-Protocol.txt`
(official EG4 Modbus RTU protocol document).

NOTE: This map is derived from the official protocol document and has NOT yet
been validated against a live 18KPV capture. The shared SNA registers
(0-152) are capture-validated on the 6000XP; the 18KPV-specific additions
(153+) are document-derived and marked as such.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from ..registers import INPUT_REGISTERS as SNA_INPUT_REGISTERS
from ..registers import RegisterDef, decode_registers as _decode_registers


# The 18KPV input register map is a superset of the SNA map. The shared
# registers (0-152) are identical; the 18KPV adds AFCI arc-fault registers
# (140-152) and a few extras. We start from the SNA map and overlay the
# 18KPV-specific additions.
INPUT_REGISTERS: Dict[int, RegisterDef] = dict(SNA_INPUT_REGISTERS)

# 18KPV-specific input registers (document-derived, not yet capture-validated).
_18KPV_EXTRA: Dict[int, RegisterDef] = {
    # AFCI (arc-fault circuit interrupter) — 18KPV only
    140: RegisterDef("afci_current_ch1", "mA", 1.0, "AFCI current CH1"),
    141: RegisterDef("afci_current_ch2", "mA", 1.0, "AFCI current CH2"),
    142: RegisterDef("afci_current_ch3", "mA", 1.0, "AFCI current CH3"),
    143: RegisterDef("afci_current_ch4", "mA", 1.0, "AFCI current CH4"),
    144: RegisterDef("afci_flag", "", 1.0, "AFCI arc alarm / self-test flags"),
    145: RegisterDef("afci_arc_ch1", "", 1.0, "AFCI real-time arc CH1"),
    146: RegisterDef("afci_arc_ch2", "", 1.0, "AFCI real-time arc CH2"),
    147: RegisterDef("afci_arc_ch3", "", 1.0, "AFCI real-time arc CH3"),
    148: RegisterDef("afci_arc_ch4", "", 1.0, "AFCI real-time arc CH4"),
    149: RegisterDef("afci_max_arc_ch1", "", 1.0, "AFCI max arc CH1"),
    150: RegisterDef("afci_max_arc_ch2", "", 1.0, "AFCI max arc CH2"),
    151: RegisterDef("afci_max_arc_ch3", "", 1.0, "AFCI max arc CH3"),
    152: RegisterDef("afci_max_arc_ch4", "", 1.0, "AFCI max arc CH4"),

    # ── Split-phase / hybrid additions (document-derived) ──
    # EPS L1/L2 total energy (32-bit pairs)
    135: RegisterDef("eps_energy_l1_total", "kWh", 0.1, "EPS L1 total energy (low word)", pair_high=136),
    137: RegisterDef("eps_energy_l2_total", "kWh", 0.1, "EPS L2 total energy (low word)", pair_high=138),
    # AC couple power
    153: RegisterDef("ac_couple_power", "W", 1.0, "AC couple power"),
    206: RegisterDef("ac_couple_power_s", "W", 1.0, "AC couple power S-phase"),
    207: RegisterDef("ac_couple_power_t", "W", 1.0, "AC couple power T-phase"),
    # Split-phase L1/L2 per-leg
    193: RegisterDef("grid_voltage_l1n", "V", 0.1, "Grid L1-N voltage"),
    194: RegisterDef("grid_voltage_l2n", "V", 0.1, "Grid L2-N voltage"),
    195: RegisterDef("gen_voltage_l1n", "V", 0.1, "Generator L1-N voltage"),
    196: RegisterDef("gen_voltage_l2n", "V", 0.1, "Generator L2-N voltage"),
    197: RegisterDef("inv_power_l1n", "W", 1.0, "Inverting power L1-N"),
    198: RegisterDef("inv_power_l2n", "W", 1.0, "Inverting power L2-N"),
    199: RegisterDef("rec_power_l1n", "W", 1.0, "Rectifying power L1-N"),
    200: RegisterDef("rec_power_l2n", "W", 1.0, "Rectifying power L2-N"),
    201: RegisterDef("grid_export_l1n", "W", 1.0, "Grid export L1-N"),
    202: RegisterDef("grid_export_l2n", "W", 1.0, "Grid export L2-N"),
    203: RegisterDef("grid_import_l1n", "W", 1.0, "Grid import L1-N"),
    204: RegisterDef("grid_import_l2n", "W", 1.0, "Grid import L2-N"),
    # Additional PV strings (PV4-6)
    217: RegisterDef("pv4_voltage", "V", 0.1, "PV4 voltage"),
    218: RegisterDef("pv5_voltage", "V", 0.1, "PV5 voltage"),
    219: RegisterDef("pv6_voltage", "V", 0.1, "PV6 voltage"),
    220: RegisterDef("pv4_power", "W", 1.0, "PV4 power"),
    221: RegisterDef("pv5_power", "W", 1.0, "PV5 power"),
    222: RegisterDef("pv6_power", "W", 1.0, "PV6 power"),
    223: RegisterDef("pv4_energy_today", "kWh", 0.1, "PV4 energy today"),
    224: RegisterDef("pv4_energy_total", "kWh", 0.1, "PV4 total energy (low word)", pair_high=225),
    226: RegisterDef("pv5_energy_today", "kWh", 0.1, "PV5 energy today"),
    227: RegisterDef("pv5_energy_total", "kWh", 0.1, "PV5 total energy (low word)", pair_high=228),
    229: RegisterDef("pv6_energy_today", "kWh", 0.1, "PV6 energy today"),
    230: RegisterDef("pv6_energy_total", "kWh", 0.1, "PV6 total energy (low word)", pair_high=231),
    # Smart load power
    232: RegisterDef("smart_load_power", "W", 1.0, "Smart load power"),
}

INPUT_REGISTERS.update(_18KPV_EXTRA)

# Active TCP batches. The 18KPV has 233+ input registers; poll in 40-register
# groups (the protocol requires group-aligned reads). Covers 0-232.
BATCHES = [(0, 40), (40, 40), (80, 40), (120, 40), (160, 40), (200, 40)]
# Per-battery BMS registers (5000+). 8 batteries x 30 registers = 5000-5239.
BATTERY_BATCHES = [(5000, 40), (5040, 40), (5080, 40), (5120, 40), (5160, 40), (5200, 40)]
BATCHES = BATCHES + BATTERY_BATCHES


def _decode(raw: Dict[int, int]) -> dict:
    return _decode_registers(raw, INPUT_REGISTERS)


def create_driver() -> "ModelDriver":
    from . import ModelDriver

    return ModelDriver(
        name="eg4_18kpv",
        label="EG4 18KPV",
        input_registers=INPUT_REGISTERS,
        batches=BATCHES,
        decode=_decode,
        computed=None,
    )
