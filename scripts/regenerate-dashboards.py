#!/usr/bin/env python3
"""Regenerate lux-mon Grafana dashboard JSON files from current DB settings.

This reads settings (pv_max_power, grid_max_power, charge_max_power,
discharge_max_power, eps_max_power, temperature_unit) from MariaDB and
updates axisSoftMax + unit labels in the dashboard JSON files under
grafana/dashboards/. Run after changing settings.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

import pymysql

DB_HOST = os.environ.get("LUX_MARIADB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("LUX_MARIADB_PORT", "3306"))
DB_USER = os.environ.get("LUX_MARIADB_USER", "luxmon")
DB_PASS = os.environ.get("LUX_MARIADB_PASSWORD", "luxmon")
DB_NAME = os.environ.get("LUX_MARIADB_DATABASE", "luxmon")

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "grafana" / "dashboards"

POWER_SETTINGS = [
    "pv_max_power",
    "grid_max_power",
    "charge_max_power",
    "discharge_max_power",
    "eps_max_power",
]

DEFAULTS: dict[str, Any] = {
    "pv_max_power": 6000,
    "grid_max_power": 6000,
    "charge_max_power": 5000,
    "discharge_max_power": 5000,
    "eps_max_power": 6000,
    "temperature_unit": "celsius",
}

# Which Grafana panel title keywords map to which setting for axisSoftMax.
POWER_AXIS_SETTINGS = {
    "pv_max_power": ["PV", "pv power", "pv1 power", "pv2 power", "pv3 power", "solar"],
    "grid_max_power": ["Grid", "grid power", "grid import", "grid export", "grid net"],
    "charge_max_power": ["Charge", "Battery Charge", "charging"],
    "discharge_max_power": ["Discharge", "Battery Discharge", "discharging"],
    "eps_max_power": ["Load", "EPS", "Output Power", "AC Output"],
}

TEMP_TITLE_PATTERNS = re.compile(r"(°C|Temperature|Temp|temp)", re.IGNORECASE)


def load_settings() -> dict:
    settings = dict(DEFAULTS)
    try:
        conn = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            connect_timeout=5,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name, value FROM lux_settings WHERE name IN %s", (tuple(DEFAULTS.keys()),))
                for name, value in cur.fetchall():
                    if name in DEFAULTS:
                        try:
                            settings[name] = type(DEFAULTS[name])(value)
                        except (ValueError, TypeError):
                            settings[name] = value
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not load settings from MariaDB ({exc}); using defaults", file=sys.stderr)
    return settings


def get_int(settings: dict, key: str) -> int:
    try:
        return int(settings.get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        return int(DEFAULTS[key])


def guess_axis_softmax(title: str, settings: dict) -> Optional[int]:
    t = title.lower()
    for setting_key, keywords in POWER_AXIS_SETTINGS.items():
        if any(kw.lower() in t for kw in keywords):
            return get_int(settings, setting_key)
    # Generic power/time-series panels (e.g. "Power Flow") get the max of all power settings
    if "power" in t or "watt" in t:
        return max(get_int(settings, k) for k in POWER_SETTINGS)
    return None


def walk_panels(obj: Any):
    if isinstance(obj, list):
        for item in obj:
            yield from walk_panels(item)
    elif isinstance(obj, dict):
        if obj.get("type") in ("timeseries", "graph"):
            yield obj
        for v in obj.values():
            yield from walk_panels(v)


def update_dashboard(path: Path, settings: dict) -> bool:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    changed = False
    temp_unit = settings.get("temperature_unit", "celsius")

    for panel in walk_panels(data):
        title = panel.get("title", "")
        new_max = guess_axis_softmax(title, settings)
        if new_max is not None:
            panel.setdefault("fieldConfig", {}).setdefault("defaults", {}).setdefault("custom", {})
            panel["fieldConfig"]["defaults"]["custom"]["axisSoftMax"] = new_max
            changed = True

        # Update temperature unit labels in panel titles and field units
        if TEMP_TITLE_PATTERNS.search(title):
            if temp_unit == "fahrenheit":
                new_title = title.replace("°C", "°F").replace("Celsius", "Fahrenheit")
            else:
                new_title = title.replace("°F", "°C").replace("Fahrenheit", "Celsius")
            if new_title != title:
                panel["title"] = new_title
                changed = True
            fc = panel.get("fieldConfig", {})
            unit = fc.get("defaults", {}).get("unit")
            if unit in ("celsius", "fahrenheit"):
                fc["defaults"]["unit"] = temp_unit
                changed = True

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return changed


def main() -> int:
    settings = load_settings()
    print(f"Loaded settings: { {k: settings[k] for k in DEFAULTS} }")

    updated = []
    for path in sorted(DASHBOARD_DIR.glob("*.json")):
        if update_dashboard(path, settings):
            updated.append(path.name)
            print(f"Updated {path.name}")
        else:
            print(f"No changes {path.name}")

    if updated:
        print(f"\nUpdated {len(updated)} dashboard(s): {', '.join(updated)}")
        print("Deploy to alpha with:")
        print("  rsync -av grafana/dashboards/ alpha:/var/lib/grafana/dashboards/lux-mon/")
    else:
        print("\nNo dashboards needed updating.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
