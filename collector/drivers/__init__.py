"""Inverter / BMS model drivers for lux-mon.

A driver encapsulates model-specific metadata:
  - input register map
  - active-transport polling batches
  - decode function
  - optional computed-value helper
  - optional per-model sanity (clamp) limits

The registry in `collector/drivers/registry.py` maps the `inverter_model`
setting to a driver instance. Adding a new model means creating a new module
and registering it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from ..registers import RegisterDef


@dataclass
class ModelDriver:
    """Model-specific inverter driver."""

    name: str
    label: str
    input_registers: Dict[int, RegisterDef]
    batches: List[Tuple[int, int]]
    decode: Callable[[Dict[int, int]], dict]
    computed: Optional[Callable[[dict, Dict[int, int]], dict]] = None
    # Per-model overrides merged over the collector's default sanity limits
    # (PassiveCollector._SANITY_LIMITS, sized for the 6000XP). None = defaults.
    sanity_limits: Optional[Dict[str, float]] = None

    def total_registers(self) -> int:
        return max(self.input_registers.keys()) + 1 if self.input_registers else 0
