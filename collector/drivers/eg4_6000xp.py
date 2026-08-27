"""EG4 6000XP inverter driver.

The EG4 6000XP is a rebadged Luxpower SNA. This driver is an alias of the
canonical `luxpower_sna` driver — it shares the same register map, batches,
and decode path.
"""
from __future__ import annotations

from . import ModelDriver
from .luxpower_sna import create_driver as _sna


def create_driver() -> ModelDriver:
    drv = _sna()
    drv.name = "eg4_6000xp"
    drv.label = "EG4 6000XP"
    return drv
