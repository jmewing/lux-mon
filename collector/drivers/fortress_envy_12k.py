"""Fortress Power Envy True 12K inverter driver.

The Fortress Power Envy True 12K (formerly "Envy 12kW") is a rebadged
LuxPower LXP 12K — 3-MPPT, split-phase 120/240V hybrid. It shares the exact
same register map as the EG4 18KPV / 12kPV. This driver is an alias of the
`eg4_18kpv` driver (document-derived, not yet capture-validated).
"""
from __future__ import annotations

from . import ModelDriver
from .eg4_18kpv import create_driver as _18kpv


def create_driver() -> ModelDriver:
    drv = _18kpv()
    drv.name = "fortress_envy_12k"
    drv.label = "Fortress Envy True 12K"
    return drv
