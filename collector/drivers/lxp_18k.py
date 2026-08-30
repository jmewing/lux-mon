"""LuxPower LXP 18K inverter driver.

The LuxPower LXP 18K is the OEM's own branding of the 18 kW hybrid platform
(3-MPPT, split-phase 120/240V, AFCI, generator input). It shares the exact
same register map as the EG4 18KPV. This driver is an alias of the
`eg4_18kpv` driver (document-derived, not yet capture-validated).
"""
from __future__ import annotations

from . import ModelDriver
from .eg4_18kpv import create_driver as _18kpv


def create_driver() -> ModelDriver:
    drv = _18kpv()
    drv.name = "lxp_18k"
    drv.label = "LuxPower LXP 18K"
    return drv
