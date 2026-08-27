"""Registry mapping inverter_model setting values to ModelDriver factories."""
from __future__ import annotations

import logging
from typing import Callable, Dict

from . import ModelDriver
from .eg4_6000xp import create_driver as _eg4_6000xp
from .eg4_18kpv import create_driver as _eg4_18kpv
from .eg4_12kpv import create_driver as _eg4_12kpv
from .eg4_12000xp import create_driver as _eg4_12000xp
from .eg4_6500ex import create_driver as _eg4_6500ex
from .eg4_3000ehv import create_driver as _eg4_3000ehv
from .luxpower_sna import create_driver as _luxpower_sna

logger = logging.getLogger(__name__)

# Map of setting value -> driver factory.
#
# Only models with a *validated* register map are registered here. The
# Luxpower SNA family (EG4 6000XP and rebadges) is the only family with a
# reverse-engineered, capture-validated map today. Other brands (Growatt,
# Solis, Sungrow, GoodWe, Huawei, SunSynk/Deye, Voltronic/Axpert) use
# different register layouts and must NOT be aliased to the SNA map — doing
# so would silently decode garbage. They are intentionally absent until a
# dedicated driver with a validated map is written.
DRIVERS: Dict[str, Callable[[], ModelDriver]] = {
    # ── SNA family (capture-validated on the 6000XP) ──
    # 2-MPPT, single-phase off-grid inverters. All share the exact same
    # register family; the 6000XP is the validated reference.
    "eg4_6000xp": _eg4_6000xp,
    "luxpower_sna": _luxpower_sna,
    "eg4_12000xp": _eg4_12000xp,
    "eg4_6500ex": _eg4_6500ex,
    "eg4_3000ehv": _eg4_3000ehv,

    # ── 18KPV family (document-derived, NOT yet capture-validated) ──
    # 3-MPPT, split-phase 120/240V, AFCI, generator input. The 18KPV map is
    # derived from the official EG4 Modbus protocol doc; the 12kPV shares it.
    "eg4_18kpv": _eg4_18kpv,
    "eg4_12kpv": _eg4_12kpv,

    # ── FlexBOSS / GridBOSS ──
    # NEW platform (not a Luxpower SNA rebadge). Register map unknown; do NOT
    # alias to either family above. Intentionally absent until a dedicated
    # driver with a validated map is written.
}

DEFAULT_MODEL = "eg4_6000xp"


def get_driver(model: str) -> ModelDriver:
    """Return the driver for a given inverter_model setting value."""
    factory = DRIVERS.get(model)
    if factory is None:
        raise ValueError(f"Unsupported inverter_model: {model!r}")
    drv = factory()
    if drv.name != model:
        logger.warning(
            "No dedicated driver for %r yet; using %r register family as fallback.",
            model,
            drv.name,
        )
    return drv
