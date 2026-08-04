"""EG4 6000XP inverter driver.

This driver uses the existing `collector.registers` decode path for
backwards compatibility while exposing model-specific metadata (register
map, polling batches) through the ModelDriver interface.
"""
from __future__ import annotations

from typing import Dict

from ..registers import INPUT_REGISTERS, decode_registers as _decode_registers
from . import ModelDriver

# Active TCP batches covering the input register map used by the EG4 6000XP
BATCHES = [(0, 40), (40, 40), (80, 40)]


def _decode(raw: Dict[int, int]) -> dict:
    """Decode raw registers into the standard lux-mon dict structure.

    The returned dict maps register names to {"value": float, "unit": str, "raw": int}.
    """
    return _decode_registers(raw, INPUT_REGISTERS)


def create_driver() -> ModelDriver:
    return ModelDriver(
        name="eg4_6000xp",
        label="EG4 6000XP",
        input_registers=INPUT_REGISTERS,
        batches=BATCHES,
        decode=_decode,
        computed=None,
    )
