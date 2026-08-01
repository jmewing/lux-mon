"""
Key-value settings stored in MariaDB.

Read by the collector and API at runtime — no config files, no restarts.
"""

import logging
from typing import Optional

logger = logging.getLogger("luxmon.settings")

# Default settings with their initial values.
# These are inserted on first run if the row doesn't exist.
DEFAULTS = {
    # Inverter ratings (used for gauge max values in dashboard)
    "pv_max_power": "8000",         # Max PV input power (W)
    "battery_capacity": "200",      # Battery capacity (Ah)
    "grid_max_power": "6000",       # Max grid pass-through (W)
    "eps_max_power": "6000",        # Max EPS output (W)
    "charge_max_power": "5000",     # Max charge power (W)
    "discharge_max_power": "5000",  # Max discharge power (W)

    # Dashboard preferences
    "dashboard_refresh_sec": "5",   # Dashboard auto-refresh interval
    "chart_default_hours": "6",     # Default chart time range

    # Collector tuning
    "write_interval_sec": "5",      # Seconds between MariaDB writes
}


def get_all(conn) -> dict[str, str]:
    """Return all settings as a dict, filling in defaults for missing keys."""
    settings = dict(DEFAULTS)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name, value FROM lux_settings")
            for name, value in cur.fetchall():
                settings[name] = value
    except Exception:
        logger.exception("Failed to read settings")
    return settings


def get(conn, name: str) -> Optional[str]:
    """Get a single setting value, or None if not found."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM lux_settings WHERE name = %s", (name,))
            row = cur.fetchone()
            if row:
                return row[0]
    except Exception:
        logger.exception("Failed to read setting %s", name)
    return DEFAULTS.get(name)


def set_(conn, name: str, value: str) -> None:
    """Insert or update a setting."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO lux_settings (name, value) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE value = VALUES(value)",
                (name, value),
            )
    except Exception:
        logger.exception("Failed to write setting %s", name)


def seed_defaults(conn) -> int:
    """Insert default settings for any keys that don't exist yet.

    Returns the number of new rows inserted.
    """
    count = 0
    try:
        with conn.cursor() as cur:
            for name, value in DEFAULTS.items():
                cur.execute(
                    "INSERT IGNORE INTO lux_settings (name, value) VALUES (%s, %s)",
                    (name, value),
                )
                count += cur.rowcount
    except Exception:
        logger.exception("Failed to seed default settings")
    return count
