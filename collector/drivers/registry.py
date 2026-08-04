"""Registry mapping inverter_model setting values to ModelDriver factories."""
from __future__ import annotations

import logging
from typing import Callable, Dict

from . import ModelDriver
from .eg4_6000xp import create_driver as _eg4_6000xp

logger = logging.getLogger(__name__)

# Map of setting value -> driver factory.
# New drivers should be imported above and added here.
DRIVERS: Dict[str, Callable[[], ModelDriver]] = {
    "eg4_6000xp": _eg4_6000xp,
    # Models that share the same LuxPower/EG4 register family reuse the
    # 6000XP driver until a dedicated driver is written.
    "eg4_3000ehv": _eg4_6000xp,
    "eg4_6500ex": _eg4_6000xp,
    "luxpower_12k": _eg4_6000xp,
    "luxpower_sna": _eg4_6000xp,
    "voltronic_axpert": _eg4_6000xp,
    "growatt": _eg4_6000xp,
    "solis": _eg4_6000xp,
    "sungrow": _eg4_6000xp,
    "goodwe": _eg4_6000xp,
    "huawei": _eg4_6000xp,
    "sunsynk": _eg4_6000xp,
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
