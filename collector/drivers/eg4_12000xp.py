"""EG4 12000XP inverter driver.

The EG4 12000XP is a 12 kW off-grid inverter in the Luxpower SNA family
(2-MPPT, single-phase). This driver is an alias of the canonical
`luxpower_sna` driver — same register map, batches, and decode path.
"""
from __future__ import annotations

from . import ModelDriver
from .luxpower_sna import create_driver as _sna


def create_driver() -> ModelDriver:
    drv = _sna()
    drv.name = "eg4_12000xp"
    drv.label = "EG4 12000XP"
    return drv
