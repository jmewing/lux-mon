"""
Multi-backend output writers for lux-mon.

Supports:
  - MariaDB (default, existing schema)
  - InfluxDB line-protocol (SolarAssistant-compatible measurement schema)
  - MQTT (Home Assistant auto-discovery + raw state topic)

Multiple backends can be enabled at the same time via env vars.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode

from collector.alerts import Alerts
from collector.mqtt_commands import MqttCommands, CONTROLLABLE_SETTINGS

logger = logging.getLogger("luxmon.outputs")


# Mapping from lux-mon decoded register names to SolarAssistant-style
# InfluxDB measurement/field names.
#
# SolarAssistant uses one measurement per metric (e.g. "PV power") and writes
# a single field ("inverter_0" for single-inverter installs, "combined" for
# combined/site-wide values). lux-mon only has one inverter, so we emit both
# to stay compatible with the SolarAssistant Grafana dashboard.
_REGISTER_TO_SA = {
    # Power values
    "pv1_power": ("PV power 1", "inverter_0"),
    "pv2_power": ("PV power 2", "inverter_0"),
    "pv3_power": ("Auxiliary PV power", "inverter_0"),
    "pv_power_total": ("PV power", "combined"),
    "grid_export_power": ("Grid power", "inverter_0_out"),
    "grid_import_power": ("Grid power", "inverter_0_in"),
    "grid_power_net": ("Grid power", "combined"),
    "inv_power": ("Inverter power", "inverter_0"),
    "eps_power": ("EPS power", "inverter_0"),
    "load_power": ("Load power", "combined"),
    "charge_power": ("Battery power", "inverter_0_in"),
    "discharge_power": ("Battery power", "inverter_0_out"),
    "battery_power_net": ("Battery power", "combined"),

    # Voltage / current / frequency
    "pv1_voltage": ("PV voltage 1", "inverter_0"),
    "pv2_voltage": ("PV voltage 2", "inverter_0"),
    "pv1_current": ("PV current 1", "inverter_0"),
    "pv2_current": ("PV current 2", "inverter_0"),
    "battery_voltage": ("Battery voltage", "inverter_0"),
    "battery_current": ("Battery current", "inverter_0"),
    "grid_voltage_r": ("Grid voltage", "inverter_0"),
    "grid_frequency": ("Grid frequency", "inverter_0"),
    "ac_output_voltage": ("AC output voltage", "inverter_0"),
    "eps_frequency": ("Generator frequency", "inverter_0"),

    # State of charge / health
    "soc": ("Battery state of charge", "inverter_0"),
    "soh": ("Battery state of charge", "inverter_0_soh"),

    # Temperatures
    "temp_inverter": ("Inverter temperature", "inverter_0"),
    "temp_battery": ("Battery temperature", "combined"),
    "temp_radiator_1": ("Radiator temperature 1", "inverter_0"),
    "temp_radiator_2": ("Radiator temperature 2", "inverter_0"),
    "runtime": ("Inverter runtime", "inverter_0"),
    "state": ("Inverter state", "inverter_0"),
    "fault_code": ("Inverter fault code", "inverter_0"),
    "warning_code": ("Inverter warning code", "inverter_0"),
    "outside_temperature": ("Outside temperature", "combined"),
    "cloud_cover": ("Cloud cover", "combined"),
    "pv_power_predicted": ("PV power predicted", "combined"),

    # Energy (kWh) - totals and today
    "pv1_energy_today": ("PV energy today 1", "inverter_0"),
    "pv2_energy_today": ("PV energy today 2", "inverter_0"),
    "pv1_energy_total": ("PV energy total 1", "inverter_0"),
    "pv2_energy_total": ("PV energy total 2", "inverter_0"),
    "charge_energy_today": ("Battery energy in today", "inverter_0"),
    "discharge_energy_today": ("Battery energy out today", "inverter_0"),
    "grid_export_today": ("Grid energy out today", "inverter_0"),
    "grid_import_today": ("Grid energy in today", "inverter_0"),
}


@dataclass
class OutputConfig:
    """Configuration for output backends."""
    # MariaDB
    mariadb_enabled: bool = True
    mariadb_host: str = "localhost"
    mariadb_port: int = 3306
    mariadb_user: str = "luxmon"
    mariadb_password: str = "luxmon"
    mariadb_database: str = "luxmon"
    mariadb_table_prefix: str = "lux_"

    # InfluxDB v1 (SolarAssistant uses InfluxDB 1.x with no auth token)
    influx_enabled: bool = False
    influx_url: str = "http://localhost:8086"
    influx_token: str = ""
    influx_org: str = "luxmon"
    influx_bucket: str = "solar"
    influx_username: str = ""
    influx_password: str = ""
    influx_database: str = "luxmon"  # InfluxDB v1 database name
    influx_retention: str = "autogen"
    influx_precision: str = "s"

    # MQTT / Home Assistant
    mqtt_enabled: bool = False
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_topic_prefix: str = "luxmon"
    mqtt_ha_discovery: bool = True
    mqtt_ha_prefix: str = "homeassistant"
    mqtt_device_name: str = "luxmon"
    mqtt_device_id: str = "luxmon_solar"

    # Display
    temperature_unit: str = "celsius"  # celsius or fahrenheit

    # Alerts / thresholds
    alerts_enabled: bool = False
    alerts_soc_low: float = 20.0
    alerts_soc_critical: float = 10.0
    alerts_battery_temp_high: float = 50.0
    alerts_inverter_temp_high: float = 60.0
    alerts_grid_lost_threshold_sec: float = 30.0

    # Alert notifications
    alerts_email_enabled: bool = False
    alerts_email_smtp_host: str = ""
    alerts_email_smtp_port: int = 587
    alerts_email_username: str = ""
    alerts_email_password: str = ""
    alerts_email_from: str = ""
    alerts_email_to: str = ""
    alerts_email_tls: bool = True
    alerts_webhook_enabled: bool = False
    alerts_webhook_url: str = ""


def _sa_value(decoded: dict, key: str) -> Optional[float]:
    """Safely pull a numeric value from decoded registers."""
    info = decoded.get(key)
    if not info:
        return None
    val = info.get("value")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _escape_influx(s: str) -> str:
    """Escape tag keys/values and field keys for InfluxDB line protocol."""
    return s.replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def _to_line(measurement: str, tags: Dict[str, str], fields: Dict[str, Any], ts_ns: int) -> str:
    """Build a single InfluxDB line-protocol line."""
    tag_str = "".join(
        f",{_escape_influx(k)}={_escape_influx(v)}"
        for k, v in tags.items()
        if v != "" and v is not None
    )
    field_parts = []
    for k, v in fields.items():
        if isinstance(v, bool):
            v_str = "t" if v else "f"
        elif isinstance(v, str):
            v_str = '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
        elif v is None or v != v:  # NaN check
            continue
        else:
            v_str = str(float(v))
        field_parts.append(f"{_escape_influx(k)}={v_str}")
    if not field_parts:
        return ""
    return f"{_escape_influx(measurement)}{tag_str} {','.join(field_parts)} {ts_ns}"


def _build_computed(decoded: dict) -> Dict[str, float]:
    """Derive combined/net values from raw registers."""
    computed = {}

    # Total PV power
    pv_total = (
        (_sa_value(decoded, "pv1_power") or 0.0)
        + (_sa_value(decoded, "pv2_power") or 0.0)
        + (_sa_value(decoded, "pv3_power") or 0.0)
    )
    if pv_total > 0 or any(_sa_value(decoded, k) is not None for k in ("pv1_power", "pv2_power", "pv3_power")):
        computed["pv_power_total"] = pv_total

    # Net grid power (positive = import, negative = export)
    grid_export = _sa_value(decoded, "grid_export_power") or 0.0
    grid_import = _sa_value(decoded, "grid_import_power") or 0.0
    if grid_export > 0 or grid_import > 0:
        computed["grid_power_net"] = grid_import - grid_export

    # Net battery power (positive = charge, negative = discharge)
    charge = _sa_value(decoded, "charge_power") or 0.0
    discharge = _sa_value(decoded, "discharge_power") or 0.0
    if charge > 0 or discharge > 0:
        computed["battery_power_net"] = charge - discharge

    # Load power (derived). The LuxPower/EG4 protocol has no dedicated load
    # register; load is the power balance across all sources/sinks:
    #   load = pv + discharge + grid_import - charge - grid_export
    # In EPS/off-grid mode eps_power already equals the load, but in grid
    # passthrough eps_power is 0 and the load rides on grid_import minus the
    # battery charge current (plus inverter self-consumption).
    load = (pv_total + discharge + grid_import) - (charge + grid_export)
    computed["load_power"] = max(0.0, load)

    # AC output voltage: use EPS phase R (S/T registers in current map appear unreliable)
    eps_r = _sa_value(decoded, "eps_voltage_r")
    if eps_r is not None:
        computed["ac_output_voltage"] = eps_r

    # PV currents derived from voltage+power when missing
    for i in (1, 2):
        v_key = f"pv{i}_voltage"
        p_key = f"pv{i}_power"
        c_key = f"pv{i}_current"
        if c_key not in computed and _sa_value(decoded, c_key) is None:
            v = _sa_value(decoded, v_key)
            p = _sa_value(decoded, p_key)
            if v is not None and p is not None and v > 0:
                computed[c_key] = p / v

    # Energy totals for Home Assistant energy dashboard
    pv_energy_total = (
        (_sa_value(decoded, "pv1_energy_total") or 0.0)
        + (_sa_value(decoded, "pv2_energy_total") or 0.0)
        + (_sa_value(decoded, "pv3_energy_total") or 0.0)
    )
    if pv_energy_total > 0 or any(_sa_value(decoded, k) is not None for k in ("pv1_energy_total", "pv2_energy_total", "pv3_energy_total")):
        computed["pv_energy_total"] = pv_energy_total

    charge_total = _sa_value(decoded, "charge_energy_total")
    if charge_total is not None:
        computed["battery_in_energy_total"] = charge_total

    discharge_total = _sa_value(decoded, "discharge_energy_total")
    if discharge_total is not None:
        computed["battery_out_energy_total"] = discharge_total

    grid_import_total = _sa_value(decoded, "grid_import_total")
    if grid_import_total is not None:
        computed["grid_import_energy_total"] = grid_import_total

    grid_export_total = _sa_value(decoded, "grid_export_total")
    if grid_export_total is not None:
        computed["grid_export_energy_total"] = grid_export_total

    return computed


class Outputs:
    """Container for all enabled output backends."""

    def __init__(self, cfg: OutputConfig, tz_name: str = "UTC"):
        self.cfg = cfg
        self.tz_name = tz_name
        self._mariadb_conn: Any = None
        self._influx_client: Any = None
        self._mqtt_client: Any = None
        self._mqtt_commands: Any = None
        self._mqtt_ha_announced: set[str] = set()
        self._alerts: Optional[Alerts] = None
        self._init_backends()
        if self.cfg.alerts_enabled:
            self._alerts = Alerts(self.cfg, mqtt_client=self._mqtt_client, mariadb_conn=self._mariadb_conn)

    def _init_backends(self) -> None:
        if self.cfg.mariadb_enabled:
            self._init_mariadb()
        if self.cfg.influx_enabled:
            self._init_influx()
        if self.cfg.mqtt_enabled:
            self._init_mqtt()

    # ── MariaDB ─────────────────────────────────────────────────────
    def _init_mariadb(self) -> None:
        try:
            import pymysql
            self._mariadb_conn = pymysql.connect(
                host=self.cfg.mariadb_host,
                port=self.cfg.mariadb_port,
                user=self.cfg.mariadb_user,
                password=self.cfg.mariadb_password,
                database=self.cfg.mariadb_database,
                autocommit=True,
            )
            self._init_mariadb_schema()
            logger.info("MariaDB writer connected")
        except Exception:
            logger.exception("Failed to connect to MariaDB")
            self._mariadb_conn = None

    def _init_mariadb_schema(self) -> None:
        import pymysql
        prefix = self.cfg.mariadb_table_prefix
        with self._mariadb_conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {prefix}snapshots (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    ts DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                    raw_registers JSON,
                    KEY idx_ts (ts)
                ) ENGINE=InnoDB
            """)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {prefix}registers (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    snapshot_id BIGINT NOT NULL,
                    name VARCHAR(64) NOT NULL,
                    value DOUBLE NOT NULL,
                    unit VARCHAR(16),
                    ts DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                    KEY idx_ts_name (ts, name),
                    KEY idx_snapshot (snapshot_id),
                    CONSTRAINT fk_{prefix}snapshot
                        FOREIGN KEY (snapshot_id) REFERENCES {prefix}snapshots(id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB
            """)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {prefix}hourly_energy (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    hour DATETIME NOT NULL,
                    name VARCHAR(64) NOT NULL,
                    value_in DOUBLE DEFAULT NULL,
                    value_out DOUBLE DEFAULT NULL,
                    unit VARCHAR(16),
                    UNIQUE KEY idx_hour_name (hour, name),
                    KEY idx_hour (hour)
                ) ENGINE=InnoDB
            """)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {prefix}settings (
                    name VARCHAR(64) PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB
            """)

    # ── InfluxDB ────────────────────────────────────────────────────
    def _init_influx(self) -> None:
        # Prefer v2 client if token provided, otherwise use v1 line protocol via requests
        if self.cfg.influx_token:
            try:
                from influxdb_client import InfluxDBClient
                self._influx_client = InfluxDBClient(
                    url=self.cfg.influx_url,
                    token=self.cfg.influx_token,
                    org=self.cfg.influx_org,
                )
                logger.info("InfluxDB v2 client ready")
                return
            except Exception:
                logger.exception("Failed to create InfluxDB v2 client")
                self._influx_client = None
        # v1: we will use HTTP POST in write_influxdb
        logger.info("InfluxDB v1 line-protocol writer ready (database=%s)", self.cfg.influx_database)

    # ── MQTT ────────────────────────────────────────────────────────
    def _init_mqtt(self) -> None:
        try:
            import paho.mqtt.publish as publish
            import paho.mqtt.client as mqtt

            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2 if hasattr(mqtt, "CallbackAPIVersion") else mqtt)
            if self.cfg.mqtt_username:
                client.username_pw_set(self.cfg.mqtt_username, self.cfg.mqtt_password)
            client.connect(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=60)
            client.loop_start()
            self._mqtt_client = client
            logger.info("MQTT writer connected to %s:%d", self.cfg.mqtt_host, self.cfg.mqtt_port)
            self._mqtt_commands = MqttCommands(
                self._mqtt_client,
                prefix=self.cfg.mqtt_topic_prefix,
                device_id=self.cfg.mqtt_device_id,
                db_host=self.cfg.mariadb_host,
                db_port=self.cfg.mariadb_port,
                db_user=self.cfg.mariadb_user,
                db_password=self.cfg.mariadb_password,
                db_name=self.cfg.mariadb_database,
                table_prefix=self.cfg.mariadb_table_prefix,
                cfg=self.cfg,
            )
        except Exception:
            logger.exception("Failed to connect to MQTT broker")
            self._mqtt_client = None
            self._mqtt_commands = None

    # ── Public write entrypoint ─────────────────────────────────────
    def write(self, decoded: dict, raw_registers: dict[int, int]) -> None:
        """Write a decoded snapshot to all enabled backends."""
        if self._mariadb_conn:
            self._write_mariadb(decoded, raw_registers)
        # Use temperature-unit-converted copy for display backends
        out_decoded = self._convert_temperatures(decoded)
        if self.cfg.influx_enabled:
            self._write_influxdb(out_decoded)
        if self._mqtt_client:
            self._write_mqtt(out_decoded)

    def _convert_temperatures(self, decoded: dict) -> dict:
        """Return a shallow copy with temperature values converted if needed."""
        if self.cfg.temperature_unit != "fahrenheit":
            return decoded
        out = dict(decoded)
        temp_keys = (
            "temp_inverter", "temp_battery", "temp_radiator_1", "temp_radiator_2",
            "outside_temperature",
        )
        for key in temp_keys:
            info = out.get(key)
            if not isinstance(info, dict) or "value" not in info:
                continue
            try:
                c = float(info["value"])
                out[key] = {**info, "value": round(c * 9.0 / 5.0 + 32.0, 1), "unit": "°F"}
            except (TypeError, ValueError):
                continue
        return out

    # ── MariaDB writing ─────────────────────────────────────────────
    def _write_mariadb(self, decoded: dict, raw_registers: dict[int, int]) -> None:
        import pymysql
        prefix = self.cfg.mariadb_table_prefix
        raw_json = json.dumps(raw_registers)

        with self._mariadb_conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {prefix}snapshots (ts, raw_registers) VALUES (NOW(3), %s)",
                (raw_json,),
            )
            snapshot_id = cur.lastrowid
            rows = [
                (snapshot_id, key, float(info["value"]), info.get("unit", ""))
                for key, info in decoded.items()
                if isinstance(info, dict) and "value" in info
            ]
            # Merge computed/derived power values (e.g. load_power) so the API's
            # /api/history endpoint can serve them alongside raw registers.
            # Only power values (watts) are written; energy totals (kWh) and
            # derived currents/voltages are intentionally excluded to avoid
            # unit confusion in the registers table.
            computed = _build_computed(decoded)
            for key in ("load_power", "pv_power_total", "grid_power_net", "battery_power_net"):
                value = computed.get(key)
                if value is None:
                    continue
                rows.append((snapshot_id, key, float(value), "W"))
            if rows:
                cur.executemany(
                    f"INSERT INTO {prefix}registers (snapshot_id, name, value, unit) VALUES (%s, %s, %s, %s)",
                    rows,
                )
            self._update_hourly_energy(cur, decoded, prefix)

        logger.info("Wrote MariaDB snapshot %d with %d registers", snapshot_id, len(rows))

    def _update_hourly_energy(self, cur, decoded: dict, prefix: str) -> None:
        """Accumulate per-hour energy-in/out counters for SolarAssistant-style rollups."""
        hour = time.strftime("%Y-%m-%d %H:00:00")
        energy_pairs = {
            "battery": ("charge_power", "discharge_power"),
            "grid": ("grid_import_power", "grid_export_power"),
            "pv": ("pv_power_total", None),
            "load": ("inv_power", None),
        }
        # Compute average power over the interval by integrating current snapshot values.
        interval_sec = float(self.cfg._write_interval) if hasattr(self.cfg, "_write_interval") else 5.0
        interval_hours = interval_sec / 3600.0

        # Battery in/out energy for this interval (kWh)
        charge = _sa_value(decoded, "charge_power") or 0.0
        discharge = _sa_value(decoded, "discharge_power") or 0.0
        self._upsert_hourly(cur, prefix, hour, "Battery power", charge * interval_hours, discharge * interval_hours)

        # Grid in/out
        grid_in = _sa_value(decoded, "grid_import_power") or 0.0
        grid_out = _sa_value(decoded, "grid_export_power") or 0.0
        self._upsert_hourly(cur, prefix, hour, "Grid power", grid_in * interval_hours, grid_out * interval_hours)

        # Load — derived power balance (see _build_computed).
        # load = pv + discharge + grid_import - charge - grid_export
        pv_load = (
            (_sa_value(decoded, "pv1_power") or 0.0)
            + (_sa_value(decoded, "pv2_power") or 0.0)
            + (_sa_value(decoded, "pv3_power") or 0.0)
        )
        load = max(0.0, (pv_load + discharge + grid_in) - (charge + grid_out))
        self._upsert_hourly(cur, prefix, hour, "Load power", load * interval_hours, None)

        # PV
        pv = (
            (_sa_value(decoded, "pv1_power") or 0.0)
            + (_sa_value(decoded, "pv2_power") or 0.0)
            + (_sa_value(decoded, "pv3_power") or 0.0)
        )
        self._upsert_hourly(cur, prefix, hour, "PV power", pv * interval_hours, None)

    def _upsert_hourly(self, cur, prefix: str, hour: str, name: str, value_in: Optional[float], value_out: Optional[float]) -> None:
        cur.execute(
            f"""
            INSERT INTO {prefix}hourly_energy (hour, name, value_in, value_out)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                value_in = COALESCE(value_in, 0) + COALESCE(%s, 0),
                value_out = COALESCE(value_out, 0) + COALESCE(%s, 0)
            """,
            (hour, name, value_in, value_out, value_in, value_out),
        )

    # ── InfluxDB writing ────────────────────────────────────────────
    def _write_influxdb(self, decoded: dict) -> None:
        ts_ns = int(time.time_ns())
        lines: List[str] = []

        computed = _build_computed(decoded)
        all_values: dict[str, float] = {}
        for key, info in decoded.items():
            if isinstance(info, dict) and "value" in info:
                val = _sa_value(decoded, key)
                if val is not None:
                    all_values[key] = val
        all_values.update(computed)

        # Emit SolarAssistant-compatible measurement points
        measurements: Dict[str, Dict[str, Any]] = {}
        for lux_name, val in all_values.items():
            if lux_name not in _REGISTER_TO_SA:
                continue
            measurement, field = _REGISTER_TO_SA[lux_name]
            measurements.setdefault(measurement, {})[field] = val

        for measurement, fields in measurements.items():
            line = _to_line(measurement, {}, fields, ts_ns)
            if line:
                lines.append(line)

        # Also write a catch-all luxmon register point for every decoded register
        for key, info in decoded.items():
            val = _sa_value(decoded, key)
            if val is not None:
                line = _to_line("luxmon_register", {"name": key, "unit": info.get("unit", "")}, {"value": val}, ts_ns)
                if line:
                    lines.append(line)

        if not lines:
            return

        payload = "\n".join(lines)
        if self._influx_client:
            try:
                self._influx_client.write_api().write(
                    bucket=self.cfg.influx_bucket,
                    org=self.cfg.influx_org,
                    record=payload,
                    write_precision="ns",
                )
                logger.info("Wrote %d InfluxDB v2 lines", len(lines))
            except Exception:
                logger.exception("InfluxDB v2 write failed; falling back to line protocol")
                self._post_influx_v1(payload)
        else:
            self._post_influx_v1(payload)

    def _post_influx_v1(self, payload: str) -> None:
        try:
            import requests
            params = {"db": self.cfg.influx_database, "precision": self.cfg.influx_precision}
            if self.cfg.influx_username:
                params.update({"u": self.cfg.influx_username, "p": self.cfg.influx_password})
            url = self.cfg.influx_url.rstrip("/") + "/write"
            resp = requests.post(url, params=params, data=payload, timeout=10)
            resp.raise_for_status()
            logger.info("Wrote %d InfluxDB v1 lines to %s", len(payload.splitlines()), url)
        except Exception:
            logger.exception("InfluxDB v1 write failed")

    # ── MQTT writing ──────────────────────────────────────────────────
    def _write_mqtt(self, decoded: dict) -> None:
        if not self._mqtt_client:
            return

        base = self.cfg.mqtt_topic_prefix
        device = self.cfg.mqtt_device_id
        computed = _build_computed(decoded)
        state_payload: dict[str, Any] = {}

        for key, info in decoded.items():
            if isinstance(info, dict) and "value" in info:
                val = _sa_value(decoded, key)
                if val is not None:
                    state_payload[key] = val
        state_payload.update(computed)

        state_topic = f"{base}/{device}/state"
        self._mqtt_client.publish(state_topic, json.dumps(state_payload), qos=0, retain=False)

        # Publish current values of MQTT-controllable settings so HA number entities sync
        self._publish_settings_state()

        # Home Assistant auto-discovery for key sensors
        if self.cfg.mqtt_ha_discovery:
            self._ha_announce(decoded, computed, state_topic)
            self._publish_ha_number_discovery()

        logger.info("Published MQTT state to %s (%d fields)", state_topic, len(state_payload))

    def _ha_announce(self, decoded: dict, computed: dict, state_topic: str) -> None:
        """Publish Home Assistant MQTT discovery configs for core sensors."""
        temp_unit = "°F" if self.cfg.temperature_unit == "fahrenheit" else "°C"
        sensors = [
            ("pv_power_total", "PV Power", "power", "W", "solar-power"),
            ("grid_power_net", "Grid Net Power", "power", "W", "transmission-tower"),
            ("battery_power_net", "Battery Net Power", "power", "W", "battery"),
            ("load_power", "Load Power", "power", "W", "lightning-bolt"),
            ("soc", "Battery SOC", "battery", "%", "battery"),
            ("battery_voltage", "Battery Voltage", "voltage", "V", "flash"),
            ("pv1_voltage", "PV Voltage 1", "voltage", "V", "solar-power"),
            ("pv2_voltage", "PV Voltage 2", "voltage", "V", "solar-power"),
            ("grid_voltage_r", "Grid Voltage", "voltage", "V", "transmission-tower"),
            ("grid_frequency", "Grid Frequency", "frequency", "Hz", "sine-wave"),
            ("ac_output_voltage", "AC Output Voltage", "voltage", "V", "flash"),
            ("temp_inverter", "Inverter Temperature", "temperature", temp_unit, "thermometer"),
            ("temp_battery", "Battery Temperature", "temperature", temp_unit, "thermometer"),
            ("temp_radiator_1", "Radiator Temperature 1", "temperature", temp_unit, "thermometer"),
            ("temp_radiator_2", "Radiator Temperature 2", "temperature", temp_unit, "thermometer"),
            ("eps_power", "EPS Output Power", "power", "W", "flash"),
            ("eps_frequency", "EPS Output Frequency", "frequency", "Hz", "sine-wave"),
            ("runtime", "Runtime", "duration", "s", "timer"),
            ("state", "Operating State", "", "", "information"),
            ("fault_code", "Fault Code", "", "", "alert"),
            ("warning_code", "Warning Code", "", "", "alert"),
        ]

        device_info = {
            "identifiers": [self.cfg.mqtt_device_id],
            "name": self.cfg.mqtt_device_name,
            "model": "lux-mon",
            "manufacturer": "lux-mon",
            "sw_version": "0.5.0",
        }

        for key, name, dev_class, unit, icon in sensors:
            if key not in decoded and key not in computed:
                continue
            uniq = f"{self.cfg.mqtt_device_id}_{key}"
            if uniq in self._mqtt_ha_announced:
                continue
            config_topic = f"{self.cfg.mqtt_ha_prefix}/sensor/{uniq}/config"
            config = {
                "name": name,
                "unique_id": uniq,
                "state_topic": state_topic,
                "value_template": f"{{{{ value_json.{key} }}}}",
                "unit_of_measurement": unit,
                "device_class": dev_class,
                "icon": f"mdi:{icon}",
                "device": device_info,
            }
            self._mqtt_client.publish(config_topic, json.dumps(config), qos=1, retain=True)
            self._mqtt_ha_announced.add(uniq)

        # Energy sensors (state_class: total_increasing) for Home Assistant energy dashboard
        energy_sensors = [
            ("pv_energy_total", "PV Energy Total", "energy", "kWh", "solar-power", "total_increasing"),
            ("grid_import_energy_total", "Grid Import Energy Total", "energy", "kWh", "transmission-tower", "total_increasing"),
            ("grid_export_energy_total", "Grid Export Energy Total", "energy", "kWh", "transmission-tower", "total_increasing"),
            ("battery_in_energy_total", "Battery Charge Energy Total", "energy", "kWh", "battery-charging", "total_increasing"),
            ("battery_out_energy_total", "Battery Discharge Energy Total", "energy", "kWh", "battery", "total_increasing"),
        ]
        for key, name, dev_class, unit, icon, state_class in energy_sensors:
            if key not in decoded and key not in computed:
                continue
            uniq = f"{self.cfg.mqtt_device_id}_{key}"
            if uniq in self._mqtt_ha_announced:
                continue
            config_topic = f"{self.cfg.mqtt_ha_prefix}/sensor/{uniq}/config"
            config = {
                "name": name,
                "unique_id": uniq,
                "state_topic": state_topic,
                "value_template": f"{{{{ value_json.{key} }}}}",
                "unit_of_measurement": unit,
                "device_class": dev_class,
                "state_class": state_class,
                "icon": f"mdi:{icon}",
                "device": device_info,
            }
            self._mqtt_client.publish(config_topic, json.dumps(config), qos=1, retain=True)
            self._mqtt_ha_announced.add(uniq)

    def _publish_settings_state(self) -> None:
        """Publish current values of MQTT-controllable settings."""
        if not self._mariadb_conn:
            return
        try:
            from collector.settings import get_all
            settings = get_all(self._mariadb_conn)
            for name in CONTROLLABLE_SETTINGS:
                value = settings.get(name)
                if value is None:
                    continue
                topic = f"{self.cfg.mqtt_topic_prefix}/{self.cfg.mqtt_device_id}/settings/{name}"
                self._mqtt_client.publish(topic, str(value), qos=0, retain=False)
        except Exception:
            logger.exception("Failed to publish settings state to MQTT")

    def _publish_ha_number_discovery(self) -> None:
        """Publish Home Assistant `number` discovery configs for controllable settings."""
        from collector.settings import SETTING_META
        device_info = {
            "identifiers": [self.cfg.mqtt_device_id],
            "name": self.cfg.mqtt_device_name,
            "model": "lux-mon",
            "manufacturer": "lux-mon",
            "sw_version": "0.5.0",
        }
        for name in CONTROLLABLE_SETTINGS:
            meta = SETTING_META.get(name)
            if not meta:
                continue
            uniq = f"{self.cfg.mqtt_device_id}_set_{name}"
            if uniq in self._mqtt_ha_announced:
                continue
            config_topic = f"{self.cfg.mqtt_ha_prefix}/number/{uniq}/config"
            state_topic = f"{self.cfg.mqtt_topic_prefix}/{self.cfg.mqtt_device_id}/settings/{name}"
            command_topic = f"{self.cfg.mqtt_topic_prefix}/{self.cfg.mqtt_device_id}/set/{name}"
            unit = meta.get("unit") or meta.get("hint", "").split()[-1] if meta.get("hint") else ""
            config: dict[str, Any] = {
                "name": meta.get("label", name),
                "unique_id": uniq,
                "state_topic": state_topic,
                "command_topic": command_topic,
                "min": meta.get("min", 0),
                "max": meta.get("max", 65535),
                "step": meta.get("step", 1),
                "device": device_info,
            }
            if unit:
                config["unit_of_measurement"] = unit
            self._mqtt_client.publish(config_topic, json.dumps(config), qos=1, retain=True)
            self._mqtt_ha_announced.add(uniq)

    # ── Lifecycle ─────────────────────────────────────────────────────
    def evaluate_alerts(self, decoded: dict) -> dict:
        """Evaluate alert thresholds if alerts are enabled."""
        if self._alerts:
            return self._alerts.evaluate(decoded)
        return {}

    def close(self) -> None:
        if self._alerts:
            try:
                self._alerts.close()
            except Exception:
                pass
        if self._mariadb_conn:
            try:
                self._mariadb_conn.close()
            except Exception:
                pass
        if self._mqtt_commands:
            try:
                self._mqtt_commands.client.loop_stop()
                self._mqtt_commands.client.disconnect()
            except Exception:
                pass
            self._mqtt_commands = None
        if self._mqtt_client:
            try:
                self._mqtt_client.loop_stop()
                self._mqtt_client.disconnect()
            except Exception:
                pass
