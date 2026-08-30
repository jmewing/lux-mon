"""BigBattery SNA-US 6K inverter driver.

The BigBattery SNA-US 6K is a rebadged LuxPower SNA-US 6000 (2-MPPT,
single-phase off-grid), sold as-is under the BigBattery brand. It shares the
exact same register map as the EG4 6000XP. This driver is an alias of the
canonical `luxpower_sna` driver.
"""
from __future__ import annotations

from . import ModelDriver
from .luxpower_sna import create_driver as _sna


def create_driver() -> ModelDriver:
    drv = _sna()
    drv.name = "bigbattery_sna_6k"
    drv.label = "BigBattery SNA-US 6K"
    return drv
