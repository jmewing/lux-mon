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
}

INPUT_REGISTERS.update(_18KPV_EXTRA)

# Active TCP batches. The 18KPV has 153+ input registers; poll in 40-register
# groups (the protocol requires group-aligned reads).
BATCHES = [(0, 40), (40, 40), (80, 40), (120, 40)]


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
