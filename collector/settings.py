"""
Key-value settings stored in MariaDB.

Read by the collector and API at runtime — no config files, no restarts.

Settings are organized into sections matching the SolarAssistant layout:
  - Inverter: model, ratings
  - Battery: type, capacity, metric
  - Grid: provider
  - Dashboard: visual preferences, gauge max values
  - System: localization, timezone, temperature unit
  - MQTT: broker config (future)
  - Collector: write interval, tuning
"""

import logging
from typing import Optional

logger = logging.getLogger("luxmon.settings")

# Default settings with their initial values.
# These are inserted on first run if the row doesn't exist.
DEFAULTS = {
    # ── Inverter ──
    "inverter_model": "eg4_6000xp",     # Inverter model identifier
    "pv_max_power": "8000",             # Max PV input power (W)
    "grid_max_power": "6000",           # Max grid pass-through (W)
    "eps_max_power": "6000",            # Max EPS output (W)
    "charge_max_power": "5000",         # Max charge power (W)
    "discharge_max_power": "5000",      # Max discharge power (W)

    # ── Battery ──
    "battery_type": "inverter",         # Battery data source: inverter, emulated, modbus, etc.
    "battery_capacity": "200",          # Battery capacity (Ah)
    "battery_metric": "soc",            # Battery display metric: soc, voltage, both

    # ── Grid ──
    "grid_provider": "default",         # Grid provider: default, eskom, epex, nordpool

    # ── Dashboard ──
    "dashboard_refresh_sec": "5",       # Dashboard auto-refresh interval
    "chart_default_hours": "6",         # Default chart time range

    # ── System ──
    "timezone": "America/Chicago",      # IANA timezone
    "temperature_unit": "fahrenheit",   # celsius or fahrenheit

    # ── Collector ──
    "write_interval_sec": "5",          # Seconds between MariaDB writes
    "dongle_host": "192.168.1.100",    # WiFi dongle IP
    "dongle_port": "8000",              # WiFi dongle port
    "datalog_serial": "",               # WiFi dongle / datalog serial number
    "inverter_serial": "",              # Inverter serial number

    # ── InfluxDB ──
    "influx_enabled": "false",          # Enable InfluxDB output
    "influx_url": "http://localhost:8086",
    "influx_database": "luxmon",        # InfluxDB v1 database / v2 bucket
    "influx_token": "",                 # InfluxDB v2 token (v1 leaves empty)
    "influx_org": "luxmon",             # InfluxDB v2 org
    "influx_username": "",              # InfluxDB v1 username
    "influx_password": "",              # InfluxDB v1 password

    # ── MQTT / Home Assistant ──
    "mqtt_enabled": "false",            # Enable MQTT output
    "mqtt_host": "localhost",
    "mqtt_port": "1883",
    "mqtt_username": "",
    "mqtt_password": "",
    "mqtt_topic_prefix": "luxmon",
    "mqtt_ha_discovery": "true",        # Publish Home Assistant discovery configs
    "mqtt_device_name": "luxmon",
    "mqtt_device_id": "luxmon_solar",

    # ── Alerts / thresholds ──
    "alerts_enabled": "false",          # Enable alert evaluation
    "alerts_soc_low": "20",             # Battery SOC low threshold (%)
    "alerts_soc_critical": "10",        # Battery SOC critical threshold (%)
    "alerts_battery_temp_high": "50",   # Battery high temp (°C)
    "alerts_inverter_temp_high": "60",  # Inverter high temp (°C)
    "alerts_grid_lost_threshold_sec": "30",  # Seconds before grid-loss alert fires
}


# ── Setting metadata for the UI ──

# Each setting's display info for the settings page form
SETTING_META = {
    "inverter_model": {
        "label": "Model",
        "type": "select",
        "section": "inverter",
        "options": [
            ("eg4_6000xp", "EG4 6000XP"),
            ("eg4_3000ehv", "EG4 3000EHV"),
            ("eg4_6500ex", "EG4 6500EX"),
            ("luxpower_12k", "Luxpower 12K"),
            ("luxpower_sna", "Luxpower SNA"),
            ("voltronic_axpert", "Voltronic / Axpert / MPP"),
            ("growatt", "Growatt"),
            ("solis", "Solis"),
            ("sungrow", "Sungrow"),
            ("goodwe", "GoodWe"),
            ("huawei", "Huawei"),
            ("sunsynk", "Deye / SunSynk / Sol-Ark"),
        ],
        "hint": "Inverter model for protocol selection",
    },
    "pv_max_power": {
        "label": "Max Solar PV Power",
        "type": "number",
        "section": "inverter",
        "min": 10, "max": 1000000,
        "hint": "Watts — gauge ceiling for solar input",
    },
    "grid_max_power": {
        "label": "Max Grid Power",
        "type": "number",
        "section": "inverter",
        "min": 10, "max": 1000000,
        "hint": "Watts — max grid pass-through",
    },
    "eps_max_power": {
        "label": "Max EPS Power",
        "type": "number",
        "section": "inverter",
        "min": 10, "max": 1000000,
        "hint": "Watts — max backup output",
    },
    "charge_max_power": {
        "label": "Max Charge Power",
        "type": "number",
        "section": "inverter",
        "min": 10, "max": 1000000,
        "hint": "Watts — max battery charge rate",
    },
    "discharge_max_power": {
        "label": "Max Discharge Power",
        "type": "number",
        "section": "inverter",
        "min": 10, "max": 1000000,
        "hint": "Watts — max battery discharge rate",
    },
    "battery_type": {
        "label": "Battery",
        "type": "select",
        "section": "battery",
        "options": [
            ("inverter", "Use inverter values"),
            ("emulated", "Emulated BMS"),
            ("daly", "USB Daly UART/RS485"),
            ("jbd", "USB JBD RS485"),
            ("jk", "USB JK RS485"),
            ("modbus", "USB Modbus RS232/485"),
            ("narada", "USB Narada RS485"),
            ("pylontech", "USB PylonTech/Pytes console"),
            ("serial", "USB Serial RS232/485"),
            ("voltronic_lib", "USB Voltronic LIB RS485"),
            ("ve_direct", "USB Victron VE.Direct"),
            ("can", "USB CAN bus"),
        ],
        "hint": "Battery data source / BMS driver",
    },
    "battery_capacity": {
        "label": "Capacity",
        "type": "number",
        "section": "battery",
        "min": 0.1, "max": 100000, "step": 0.1,
        "hint": "Amp-hours — rated battery capacity",
    },
    "battery_metric": {
        "label": "Battery Metric",
        "type": "select",
        "section": "battery",
        "options": [
            ("soc", "State of Charge"),
            ("voltage", "Voltage"),
            ("both", "Both"),
        ],
        "hint": "Primary battery display metric on dashboard",
    },
    "grid_provider": {
        "label": "Provider",
        "type": "select",
        "section": "grid",
        "options": [
            ("default", "Default"),
            ("eskom", "South Africa — Eskom"),
            ("epex", "Europe — EPEX"),
            ("nordpool", "Europe — NordPool"),
        ],
        "hint": "Grid provider for regional settings",
    },
    "dashboard_refresh_sec": {
        "label": "Refresh Interval",
        "type": "number",
        "section": "dashboard",
        "min": 1, "max": 60,
        "hint": "Seconds between dashboard updates",
    },
    "chart_default_hours": {
        "label": "Default Chart Range",
        "type": "number",
        "section": "dashboard",
        "min": 1, "max": 168,
        "hint": "Hours shown on initial chart load",
    },
    "timezone": {
        "label": "Timezone",
        "type": "select",
        "section": "system",
        "options": [
            ("America/Chicago", "America/Chicago"),
            ("America/New_York", "America/New_York"),
            ("America/Denver", "America/Denver"),
            ("America/Los_Angeles", "America/Los_Angeles"),
            ("America/Phoenix", "America/Phoenix"),
            ("America/Anchorage", "America/Anchorage"),
            ("Pacific/Honolulu", "Pacific/Honolulu"),
            ("Europe/London", "Europe/London"),
            ("Europe/Berlin", "Europe/Berlin"),
            ("Europe/Paris", "Europe/Paris"),
            ("Asia/Tokyo", "Asia/Tokyo"),
            ("Asia/Shanghai", "Asia/Shanghai"),
            ("Asia/Kolkata", "Asia/Kolkata"),
            ("Australia/Sydney", "Australia/Sydney"),
            ("Pacific/Auckland", "Pacific/Auckland"),
            ("UTC", "UTC"),
        ],
        "hint": "IANA timezone for timestamps and scheduling",
    },
    "temperature_unit": {
        "label": "Temperature Unit",
        "type": "select",
        "section": "system",
        "options": [
            ("fahrenheit", "°Fahrenheit"),
            ("celsius", "°Celsius"),
        ],
        "hint": "Temperature display unit throughout dashboard",
    },
    "write_interval_sec": {
        "label": "Write Interval",
        "type": "number",
        "section": "collector",
        "min": 1, "max": 300,
        "hint": "Seconds between MariaDB writes",
    },
    "dongle_host": {
        "label": "Dongle Host",
        "type": "text",
        "section": "collector",
        "hint": "IP address of the WiFi dongle",
    },
    "dongle_port": {
        "label": "Dongle Port",
        "type": "number",
        "section": "collector",
        "min": 1, "max": 65535,
        "hint": "TCP port of the WiFi dongle",
    },
    "datalog_serial": {
        "label": "Dongle Serial",
        "type": "text",
        "section": "collector",
        "hint": "WiFi dongle / datalog serial number (required for active polling)",
    },
    "inverter_serial": {
        "label": "Inverter Serial",
        "type": "text",
        "section": "collector",
        "hint": "Inverter serial number (required for active polling)",
    },

    # ── InfluxDB ──
    "influx_enabled": {
        "label": "Enable InfluxDB",
        "type": "checkbox",
        "section": "influxdb",
        "hint": "Write SolarAssistant-compatible measurements to InfluxDB",
    },
    "influx_url": {
        "label": "InfluxDB URL",
        "type": "text",
        "section": "influxdb",
        "hint": "e.g. http://localhost:8086",
    },
    "influx_database": {
        "label": "Database / Bucket",
        "type": "text",
        "section": "influxdb",
        "hint": "InfluxDB v1 database name or v2 bucket",
    },
    "influx_token": {
        "label": "InfluxDB Token",
        "type": "password",
        "section": "influxdb",
        "hint": "InfluxDB v2 token (leave blank for v1)",
    },
    "influx_org": {
        "label": "InfluxDB Org",
        "type": "text",
        "section": "influxdb",
        "hint": "InfluxDB v2 organization",
    },
    "influx_username": {
        "label": "InfluxDB Username",
        "type": "text",
        "section": "influxdb",
        "hint": "InfluxDB v1 username (optional)",
    },
    "influx_password": {
        "label": "InfluxDB Password",
        "type": "password",
        "section": "influxdb",
        "hint": "InfluxDB v1 password (optional)",
    },

    # ── MQTT ──
    "mqtt_enabled": {
        "label": "Enable MQTT",
        "type": "checkbox",
        "section": "mqtt",
        "hint": "Publish state and Home Assistant discovery configs",
    },
    "mqtt_host": {
        "label": "MQTT Host",
        "type": "text",
        "section": "mqtt",
        "hint": "Broker hostname or IP",
    },
    "mqtt_port": {
        "label": "MQTT Port",
        "type": "number",
        "section": "mqtt",
        "min": 1, "max": 65535,
        "hint": "Broker port (usually 1883)",
    },
    "mqtt_username": {
        "label": "MQTT Username",
        "type": "text",
        "section": "mqtt",
        "hint": "Leave blank for anonymous",
    },
    "mqtt_password": {
        "label": "MQTT Password",
        "type": "password",
        "section": "mqtt",
        "hint": "Leave blank for anonymous",
    },
    "mqtt_topic_prefix": {
        "label": "Topic Prefix",
        "type": "text",
        "section": "mqtt",
        "hint": "e.g. luxmon",
    },
    "mqtt_ha_discovery": {
        "label": "Home Assistant Discovery",
        "type": "checkbox",
        "section": "mqtt",
        "hint": "Publish MQTT discovery configs for Home Assistant",
    },
    "mqtt_device_name": {
        "label": "Device Name",
        "type": "text",
        "section": "mqtt",
        "hint": "Friendly name shown in Home Assistant",
    },
    "mqtt_device_id": {
        "label": "Device ID",
        "type": "text",
        "section": "mqtt",
        "hint": "Unique ID for the HA device",
    },

    # ── Alerts ──
    "alerts_enabled": {
        "label": "Enable Alerts",
        "type": "checkbox",
        "section": "alerts",
        "hint": "Evaluate alert rules on every snapshot",
    },
    "alerts_soc_low": {
        "label": "SOC Low Threshold",
        "type": "number",
        "section": "alerts",
        "min": 0, "max": 100,
        "hint": "Battery SOC % that triggers a low alert",
    },
    "alerts_soc_critical": {
        "label": "SOC Critical Threshold",
        "type": "number",
        "section": "alerts",
        "min": 0, "max": 100,
        "hint": "Battery SOC % that triggers a critical alert",
    },
    "alerts_battery_temp_high": {
        "label": "Battery High Temp",
        "type": "number",
        "section": "alerts",
        "min": 0, "max": 100,
        "hint": "Battery temperature (°C) that triggers a high temp alert",
    },
    "alerts_inverter_temp_high": {
        "label": "Inverter High Temp",
        "type": "number",
        "section": "alerts",
        "min": 0, "max": 100,
        "hint": "Inverter heatsink temperature (°C) that triggers a high temp alert",
    },
    "alerts_grid_lost_threshold_sec": {
        "label": "Grid Loss Delay",
        "type": "number",
        "section": "alerts",
        "min": 0, "max": 600,
        "hint": "Seconds grid voltage/frequency must be absent before a grid-loss alert fires",
    },
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
