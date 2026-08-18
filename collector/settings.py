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
import os
from typing import Optional

logger = logging.getLogger("luxmon.settings")


# ── Environment variable mapping ────────────────────────────────────────────
#
# The collector reads configuration from environment variables (LUX_*) first,
# and only falls back to the MariaDB `lux_settings` table when the env var is
# unset/empty. This mapping lets the API compute the *effective* value (what is
# actually running) using the same precedence, so the settings page reflects
# reality instead of a stale DB row.
#
# Each entry: setting_key -> (env_var, cast)
#   cast: None (string), "int", "float", or "bool"
SETTING_ENV = {
    # ── Inverter ──
    "inverter_model": ("LUX_INVERTER_MODEL", None),

    # ── Collector / dongle ──
    "dongle_host": ("LUX_DONGLE_HOST", None),
    "dongle_port": ("LUX_DONGLE_PORT", "int"),
    "datalog_serial": ("LUX_DATALOG_SERIAL", None),
    "inverter_serial": ("LUX_INVERTER_SERIAL", None),
    "write_interval_sec": ("LUX_WRITE_INTERVAL", "int"),
    "transport": ("LUX_TRANSPORT", None),

    # ── InfluxDB ──
    "influx_enabled": ("LUX_INFLUX_ENABLED", "bool"),
    "influx_url": ("LUX_INFLUX_URL", None),
    "influx_database": ("LUX_INFLUX_DATABASE", None),
    "influx_token": ("LUX_INFLUX_TOKEN", None),
    "influx_org": ("LUX_INFLUX_ORG", None),
    "influx_username": ("LUX_INFLUX_USERNAME", None),
    "influx_password": ("LUX_INFLUX_PASSWORD", None),

    # ── MQTT ──
    "mqtt_enabled": ("LUX_MQTT_ENABLED", "bool"),
    "mqtt_host": ("LUX_MQTT_HOST", None),
    "mqtt_port": ("LUX_MQTT_PORT", "int"),
    "mqtt_username": ("LUX_MQTT_USERNAME", None),
    "mqtt_password": ("LUX_MQTT_PASSWORD", None),
    "mqtt_topic_prefix": ("LUX_MQTT_TOPIC_PREFIX", None),
    "mqtt_ha_discovery": ("LUX_MQTT_HA_DISCOVERY", "bool"),
    "mqtt_device_name": ("LUX_MQTT_DEVICE_NAME", None),
    "mqtt_device_id": ("LUX_MQTT_DEVICE_ID", None),

    # ── System ──
    "temperature_unit": ("LUX_TEMPERATURE_UNIT", None),

    # ── Alerts ──
    "alerts_enabled": ("LUX_ALERTS_ENABLED", "bool"),
    "alerts_soc_low": ("LUX_ALERTS_SOC_LOW", "float"),
    "alerts_soc_critical": ("LUX_ALERTS_SOC_CRITICAL", "float"),
    "alerts_battery_temp_high": ("LUX_ALERTS_BATTERY_TEMP_HIGH", "float"),
    "alerts_inverter_temp_high": ("LUX_ALERTS_INVERTER_TEMP_HIGH", "float"),
    "alerts_grid_lost_threshold_sec": ("LUX_ALERTS_GRID_LOST_THRESHOLD_SEC", "float"),
    "alerts_email_enabled": ("LUX_ALERTS_EMAIL_ENABLED", "bool"),
    "alerts_email_smtp_host": ("LUX_ALERTS_EMAIL_SMTP_HOST", None),
    "alerts_email_smtp_port": ("LUX_ALERTS_EMAIL_SMTP_PORT", "int"),
    "alerts_email_username": ("LUX_ALERTS_EMAIL_USERNAME", None),
    "alerts_email_password": ("LUX_ALERTS_EMAIL_PASSWORD", None),
    "alerts_email_from": ("LUX_ALERTS_EMAIL_FROM", None),
    "alerts_email_to": ("LUX_ALERTS_EMAIL_TO", None),
    "alerts_email_tls": ("LUX_ALERTS_EMAIL_TLS", "bool"),
    "alerts_webhook_enabled": ("LUX_ALERTS_WEBHOOK_ENABLED", "bool"),
    "alerts_webhook_url": ("LUX_ALERTS_WEBHOOK_URL", None),
}


def _cast_env(value: str, cast: Optional[str]):
    """Cast an env string to the requested type, mirroring collector._env_or."""
    if cast == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if cast == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if cast == "bool":
        return str(value).lower() in ("1", "true", "yes")
    return value


def effective_value(name: str, db_value: Optional[str]):
    """Return the effective value for a setting using env > DB > default precedence.

    Mirrors the collector's startup precedence so the settings page shows what
    is actually running, not a stale DB row.
    """
    if name in SETTING_ENV:
        env_var, cast = SETTING_ENV[name]
        raw = os.environ.get(env_var)
        if raw is not None and raw != "":
            return _cast_env(raw, cast)
    if db_value is not None and db_value != "":
        return db_value
    return DEFAULTS.get(name, "")

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
    "transport": "tcp_active",           # tcp_active, tcp_passive, replay
    "dongle_host": "192.168.12.224",    # WiFi dongle IP
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

    # ── Alert notifications ──
    "alerts_email_enabled": "false",        # Enable SMTP email alerts
    "alerts_email_smtp_host": "",           # SMTP relay host (e.g. smtp.gmail.com)
    "alerts_email_smtp_port": "587",        # SMTP port (587 for STARTTLS, 465 for SSL)
    "alerts_email_username": "",            # SMTP username
    "alerts_email_password": "",            # SMTP password or app token
    "alerts_email_from": "",                # From address
    "alerts_email_to": "",                  # To address(es), comma-separated
    "alerts_email_tls": "true",             # Use TLS/STARTTLS (required)
    "alerts_webhook_enabled": "false",      # Enable webhook alerts
    "alerts_webhook_url": "",               # Webhook URL for POST JSON alerts

    # ── Automation / Rules Engine ──
    "automation_enabled": "false",          # Enable time/sensor automation rules
    "automation_rules": "[]",               # JSON list of automation rules

    # ── Quick Charge ──
    "quick_charge_amps": "85",              # Quick charge target current (A)
    "quick_charge_minutes": "60",           # Quick charge duration (min)
    "quick_charge_state": "{}",             # JSON quick-charge runtime state
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
    "transport": {
        "label": "Transport",
        "type": "select",
        "section": "collector",
        "options": [
            ("tcp_active", "Active polling (Modbus TCP)"),
            ("tcp_passive", "Passive broadcast stream"),
            ("replay", "Replay capture file"),
        ],
        "hint": "How the collector reads inverter data",
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
    "alerts_email_enabled": {
        "label": "Enable Email Alerts",
        "type": "checkbox",
        "section": "alerts",
        "hint": "Send alert state changes via authenticated SMTP relay",
    },
    "alerts_email_smtp_host": {
        "label": "SMTP Host",
        "type": "text",
        "section": "alerts",
        "hint": "e.g. smtp.gmail.com — authenticated relay only, never direct delivery",
    },
    "alerts_email_smtp_port": {
        "label": "SMTP Port",
        "type": "number",
        "section": "alerts",
        "min": 1, "max": 65535,
        "hint": "587 for STARTTLS, 465 for SSL/TLS",
    },
    "alerts_email_username": {
        "label": "SMTP Username",
        "type": "text",
        "section": "alerts",
        "hint": "Usually the full email address",
    },
    "alerts_email_password": {
        "label": "SMTP Password / App Token",
        "type": "password",
        "section": "alerts",
        "hint": "Use an app-specific password for Gmail / Outlook",
    },
    "alerts_email_from": {
        "label": "From Address",
        "type": "text",
        "section": "alerts",
        "hint": "Sender email address",
    },
    "alerts_email_to": {
        "label": "To Address(es)",
        "type": "text",
        "section": "alerts",
        "hint": "Comma-separated recipient addresses",
    },
    "alerts_email_tls": {
        "label": "Use TLS",
        "type": "checkbox",
        "section": "alerts",
        "hint": "Always enable TLS/STARTTLS for SMTP relay",
    },
    "alerts_webhook_enabled": {
        "label": "Enable Webhook Alerts",
        "type": "checkbox",
        "section": "alerts",
        "hint": "POST JSON alert events to a custom URL",
    },
    "alerts_webhook_url": {
        "label": "Webhook URL",
        "type": "text",
        "section": "alerts",
        "hint": "HTTPS endpoint that receives POST JSON payloads",
    },

    # ── Automation ──
    "automation_enabled": {
        "label": "Enable Automation Rules",
        "type": "checkbox",
        "section": "automation",
        "hint": "Evaluate time/sensor rules and write inverter settings automatically",
    },
    "automation_rules": {
        "label": "Automation Rules (JSON)",
        "type": "textarea",
        "section": "automation",
        "hint": "JSON array of rules; use the Automations page for a friendly editor",
    },

    # ── Quick Charge ──
    "quick_charge_amps": {
        "label": "Quick Charge Current",
        "type": "number",
        "section": "quick_charge",
        "min": 0, "max": 140,
        "hint": "Amps — target AC charge current for a quick charge",
    },
    "quick_charge_minutes": {
        "label": "Quick Charge Duration",
        "type": "number",
        "section": "quick_charge",
        "min": 1, "max": 1440,
        "hint": "Minutes — how long the quick charge runs before restoring",
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


def seed_from_env(conn) -> int:
    """Seed DB settings from environment variables (bootstrap).

    For each setting that has an env-var mapping, insert the env value into the
    DB if the row is missing or empty. This ensures the DB is populated with the
    correct container-internal values (hostnames, secrets) on first run, so the
    DB-authoritative collector reads the right configuration.

    Returns the number of rows inserted/updated.
    """
    count = 0
    try:
        with conn.cursor() as cur:
            for name, (env_var, _cast) in SETTING_ENV.items():
                raw = os.environ.get(env_var)
                if raw is None or raw == "":
                    continue
                # Only seed if the DB row is missing or empty.
                cur.execute(
                    "INSERT INTO lux_settings (name, value) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE value = IF(value = '' OR value IS NULL, VALUES(value), value)",
                    (name, raw),
                )
                count += cur.rowcount
    except Exception:
        logger.exception("Failed to seed settings from environment")
    return count
