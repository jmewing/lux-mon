"""EG4 12000XP inverter driver.

The EG4 12000XP is a 12 kW off-grid inverter in the Luxpower SNA family
(split-phase 120/240 V, 2 MPPT, with generator and smart-load ports). This
driver is an alias of the canonical `luxpower_sna` driver — same register map,
batches, and decode path — with sanity limits sized for the larger unit.
"""
from __future__ import annotations

from typing import Dict

from . import ModelDriver
from .luxpower_sna import create_driver as _sna

# Clamp limits merged over the collector defaults (which are 6000XP-sized).
# battery_voltage, temperatures, soc and soh keep the defaults. The BMS
# current limits are deliberately not clamped (banks report e.g. 400 A).
SANITY_LIMITS: Dict[str, float] = {
    "pv1_power": 14000,          # 14 kW recommended per MPPT
    "pv2_power": 14000,
    "pv1_voltage": 500,          # max PV input voltage
    "pv2_voltage": 500,
    "grid_import_power": 24000,  # 100 A x 240 V
    "grid_export_power": 15000,
    "charge_power": 12500,       # 12 kW battery charge
    "rec_power": 12500,
    "discharge_power": 15500,    # 15.36 kW surge
    "eps_power": 15500,
    "inv_power": 15500,
    "eps_power_l1": 7700,        # 7.5 kW per leg
    "eps_power_l2": 7700,
    "battery_current": 260,      # 250 A max
}


def create_driver() -> ModelDriver:
    drv = _sna()
    drv.name = "eg4_12000xp"
    drv.label = "EG4 12000XP"
    # A fresh dict per call so no caller can mutate another driver's limits.
    drv.sanity_limits = dict(SANITY_LIMITS)
    return drv
