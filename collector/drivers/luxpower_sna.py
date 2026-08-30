"""Luxpower SNA-series inverter driver.

The Luxpower SNA is the reference platform that EG4 rebadges as the 6000XP
(and several other models). This driver is the canonical home for the SNA
register family; `eg4_6000xp` is an alias of it.

Register map: `collector/registers.py` (INPUT_REGISTERS) — reverse-engineered
and validated against live captures from a Luxpower SNA / EG4 6000XP.
"""
from __future__ import annotations

from typing import Dict

from ..registers import INPUT_REGISTERS, decode_registers as _decode_registers
from . import ModelDriver

# Active TCP batches covering the input register map used by the SNA family.
BATCHES = [(0, 40), (40, 40), (80, 40)]
# Per-battery BMS registers (5000+). 8 batteries x 30 registers = 5000-5239.
BATTERY_BATCHES = [(5000, 40), (5040, 40), (5080, 40), (5120, 40), (5160, 40), (5200, 40)]
BATCHES = BATCHES + BATTERY_BATCHES


def _decode(raw: Dict[int, int]) -> dict:
    """Decode raw registers into the standard lux-mon dict structure."""
    return _decode_registers(raw, INPUT_REGISTERS)


def create_driver() -> ModelDriver:
    return ModelDriver(
        name="luxpower_sna",
        label="Luxpower SNA",
        input_registers=INPUT_REGISTERS,
        batches=BATCHES,
        decode=_decode,
        computed=None,
    )
