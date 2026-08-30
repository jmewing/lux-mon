"""LuxPower LXP 6K inverter driver.

The LuxPower LXP 6K is the OEM's own branding of the SNA-US 6000 platform
(2-MPPT, single-phase off-grid). It shares the exact same register map as the
EG4 6000XP. This driver is an alias of the canonical `luxpower_sna` driver.
"""
from __future__ import annotations

from . import ModelDriver
from .luxpower_sna import create_driver as _sna


def create_driver() -> ModelDriver:
    drv = _sna()
    drv.name = "lxp_6k"
    drv.label = "LuxPower LXP 6K"
    return drv
