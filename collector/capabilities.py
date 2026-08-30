"""Model capability layer for lux-mon.

The LuxPower/EG4 family shares ONE register map — there is no per-model
register table. An 18KPV and a 6000XP both *have* register 22 (PV start
voltage), register 60 (active power %), etc. The difference is which
registers are *meaningful/functional* on a given model.

This module defines:

  * CAPABILITIES — the set of feature flags a model may or may not support.
  * MODEL_CAPABILITIES — maps each `inverter_model` setting value to the
    capabilities its hardware actually has.
  * filter_holding_registers() — filters the global holding-register map
    down to the registers that apply to a given model.

The model is identified at runtime by reading hold registers 7-8 (4 ASCII
chars = firmware/model code) and register 16 (Device Type / DTC). The
`inverter_model` setting is the user-facing alias for that detection.

Capability flags are coarse feature groups, not per-register toggles. A
register is tagged with the capability it requires; if the model lacks that
capability, the register is hidden from the UI/API and never written.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Set

# ── Capability flags ────────────────────────────────────────────────
#
# Each flag names a hardware feature that some LuxPower/EG4 models have and
# others do not. Registers are tagged with the flag(s) they require.

# PV string count. The SNA family (6000XP, 6500EX, 3000EHV, 12000XP) has 2
# MPPT inputs (PV1/PV2). The 12kPV has 3 (PV1-3). The 18KPV has up to 4-6.
PV1 = "pv1"
PV2 = "pv2"
PV3 = "pv3"
PV4 = "pv4"
PV5 = "pv5"
PV6 = "pv6"

# Split-phase (120/240V) vs single-phase (120V) vs three-phase (208/400V).
SINGLE_PHASE = "single_phase"
SPLIT_PHASE = "split_phase"
THREE_PHASE = "three_phase"

# Feature groups.
AFCI = "afci"                    # arc-fault circuit interrupter (18KPV)
GENERATOR = "generator"          # generator input / generator charge
SMART_LOAD = "smart_load"        # smart-load output port
AC_COUPLE = "ac_couple"          # AC-coupled PV input
WATTNODE = "wattnode"            # WattNode external CT meter
GRID_PEAK_SHAVING = "grid_peak_shaving"
VOLT_WATT = "volt_watt"          # Volt-Watt curve
QV_CURVE = "qv_curve"            # Q(V) reactive power curve
QP_CURVE = "qp_curve"            # cosphi(P) curve
SEVEN_DAY_SCHEDULE = "seven_day_schedule"  # 7-day scheduling (regs 500-723)
BATTERY_BMS = "battery_bms"      # per-battery BMS registers (5000+)

# ── Model → capabilities ───────────────────────────────────────────

# SNA family: 2-MPPT, single-phase, no AFCI/generator/smart-load/AC-couple.
# This is the capture-validated reference family (6000XP and rebadges).
_SNA_CAPS: FrozenSet[str] = frozenset({
    PV1, PV2,
    SINGLE_PHASE,
    BATTERY_BMS,
})

# 18KPV family: 3+ MPPT, split-phase, AFCI, generator, smart load, AC couple.
_18KPV_CAPS: FrozenSet[str] = frozenset({
    PV1, PV2, PV3, PV4, PV5, PV6,
    SPLIT_PHASE,
    AFCI,
    GENERATOR,
    SMART_LOAD,
    AC_COUPLE,
    WATTNODE,
    GRID_PEAK_SHAVING,
    VOLT_WATT,
    QV_CURVE,
    QP_CURVE,
    SEVEN_DAY_SCHEDULE,
    BATTERY_BMS,
})

# 12kPV: 3 MPPT, split-phase, but no AFCI (it's a smaller 18KPV sibling).
_12KPV_CAPS: FrozenSet[str] = frozenset({
    PV1, PV2, PV3,
    SPLIT_PHASE,
    GENERATOR,
    SMART_LOAD,
    AC_COUPLE,
    GRID_PEAK_SHAVING,
    VOLT_WATT,
    QV_CURVE,
    QP_CURVE,
    SEVEN_DAY_SCHEDULE,
    BATTERY_BMS,
})

MODEL_CAPABILITIES: Dict[str, FrozenSet[str]] = {
    # ── SNA family (2-MPPT single-phase) ──
    "eg4_6000xp": _SNA_CAPS,
    "luxpower_sna": _SNA_CAPS,
    "eg4_12000xp": _SNA_CAPS,
    "eg4_6500ex": _SNA_CAPS,
    "eg4_3000ehv": _SNA_CAPS,
    "lxp_6k": _SNA_CAPS,
    "bigbattery_sna_6k": _SNA_CAPS,

    # ── 18KPV family (3+ MPPT split-phase, AFCI) ──
    "eg4_18kpv": _18KPV_CAPS,
    "lxp_18k": _18KPV_CAPS,
    "fortress_envy_12k": _18KPV_CAPS,

    # ── 12kPV (3 MPPT split-phase, no AFCI) ──
    "eg4_12kpv": _12KPV_CAPS,
    "lxp_12k": _12KPV_CAPS,
}

# Default capabilities when the model is unknown (assume the most capable
# family so nothing is hidden that might actually apply).
DEFAULT_CAPABILITIES: FrozenSet[str] = _18KPV_CAPS


def capabilities_for(model: str) -> FrozenSet[str]:
    """Return the capability set for a model, defaulting to the full set."""
    return MODEL_CAPABILITIES.get(model, DEFAULT_CAPABILITIES)


def register_applies(
    capabilities: FrozenSet[str],
    required: Set[str] | FrozenSet[str] | None,
) -> bool:
    """Return True if a register's required capabilities are all present.

    A register with no `required` capabilities applies to every model.
    """
    if not required:
        return True
    return required.issubset(capabilities)


def filter_holding_registers(
    holding_registers: Dict[int, dict],
    model: str,
) -> Dict[int, dict]:
    """Filter a holding-register map down to the registers a model supports.

    Registers without a `capabilities` key are always included (they are
    shared across the whole family). Registers with a `capabilities` key are
    included only if the model has every required capability.
    """
    caps = capabilities_for(model)
    return {
        reg: info
        for reg, info in holding_registers.items()
        if register_applies(caps, info.get("capabilities"))
    }
