"""EG4 3000EHV-48 inverter driver.

The EG4 3000EHV-48 is a legacy small off-grid inverter in the Luxpower SNA
family (2-MPPT, single-phase). Discontinued but still in the field. This
driver is an alias of the canonical `luxpower_sna` driver.
"""
from __future__ import annotations

from . import ModelDriver
from .luxpower_sna import create_driver as _sna


def create_driver() -> ModelDriver:
    drv = _sna()
    drv.name = "eg4_3000ehv"
    drv.label = "EG4 3000EHV-48"
    return drv
