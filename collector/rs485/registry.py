"""Registry for RS-485 device drivers."""

from __future__ import annotations

import logging
from typing import Callable, Dict

from . import Rs485Device, Rs485DeviceConfig

logger = logging.getLogger(__name__)

# Lazy imports to avoid hard dependencies when a driver isn't used.
_DRIVER_FACTORIES: Dict[str, Callable[[Rs485DeviceConfig], Rs485Device]] = {}


def _try_import(driver_name: str, module: str, cls: str):
    """Import a driver class, logging a clear warning if deps are missing."""
    try:
        mod = __import__(module, fromlist=[cls])
        return getattr(mod, cls)
    except ImportError as exc:
        logger.warning(
            "Skipping RS-485 driver %r: %s",
            driver_name,
            exc,
        )
        return None


def _register_factories():
    """Populate the registry. Called lazily to avoid circular imports."""
    if _DRIVER_FACTORIES:
        return

    candidates = [
        ("eg4_a5_bms", "collector.rs485.eg4_a5_bms", "Eg4A5BmsDevice"),
        ("eg4_bms", "collector.rs485.eg4_bms", "Eg4BmsDevice"),
        ("jk_bms", "collector.rs485.jk_bms", "JkBmsDevice"),
        ("modbus_rtu", "collector.rs485.modbus_rtu", "ModbusRtuDevice"),
        ("raw", "collector.rs485.raw", "RawSerialDevice"),
    ]

    for name, module, cls in candidates:
        factory = _try_import(name, module, cls)
        if factory is not None:
            _DRIVER_FACTORIES[name] = factory


def list_devices() -> list[str]:
    """Return the names of available RS-485 device drivers."""
    _register_factories()
    return list(_DRIVER_FACTORIES.keys())


def get_device(config: Rs485DeviceConfig) -> Rs485Device:
    """Instantiate the configured RS-485 device driver."""
    _register_factories()
    device_type = (config.device_type or "jk_bms").lower()
    factory = _DRIVER_FACTORIES.get(device_type)
    if factory is None:
        raise ValueError(
            f"Unsupported RS-485 device_type: {device_type!r}. "
            f"Available: {', '.join(list_devices())}"
        )
    logger.debug("Using RS-485 driver %r on %s", device_type, config.port)
    return factory(config)
