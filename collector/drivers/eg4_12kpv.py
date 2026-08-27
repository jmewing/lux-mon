"""EG4 12kPV inverter driver.

The EG4 12kPV is the 12 kW sibling of the 18KPV — same 3-MPPT, split-phase
120/240V, AFCI, generator-input register family. This driver is an alias of
the `eg4_18kpv` driver (document-derived, not yet capture-validated).
"""
from __future__ import annotations

from . import ModelDriver
from .eg4_18kpv import create_driver as _18kpv


def create_driver() -> ModelDriver:
    drv = _18kpv()
    drv.name = "eg4_12kpv"
    drv.label = "EG4 12kPV"
    return drv
