"""SolarAssistant-style automation engine for lux-mon.

This module provides:

1. Low-level holding-register read/write primitives (`_write_holding_register`,
   `_read_holding_register`, `_engineering_to_raw`, `_raw_to_engineering`).
2. A general automation engine (`AutomationEngine`) that evaluates condition →
   action rules after every snapshot and writes named holding registers.

Design choices (v2):

* Storage: automations are stored as JSON in the MariaDB `lux_settings` table
  under the key `automations_v2`.  Global enable and global dry-run are
  separate settings (`automation_enabled`, `automation_global_dry_run`).
* Execution: the collector evaluates automations after each successful snapshot.
* Safety: a **global dry-run toggle** controls whether writes are actually
  sent.  When dry-run is true the engine logs every intended action but never
  writes to the inverter.  Individual automations can still be enabled/disabled.
* Restore-on-exit: each automation may specify a `restore_value`.  When the
  automation's conditions stop matching and a previous write was recorded, the
  restore value is written (unless dry-run is enabled).
* Disable-for timer: each automation has a `disabled_until` Unix timestamp.
  The dashboard sets this via a dropdown (30m, 1h, 2h, 4h, 8h, 12h, 24h).
* Quick charge: automations remain active during quick charge; they are a
  separate concern.
* Priority: Quick Charge > Automation > Timer Schedule.

Supported automation types:

* `rule_table`         — two-level nested grid: set <setting> -> when <group
                         condition> -> and when <range condition> -> set value
                         to <range action_value>
* `battery_soc`        — time-of-day + SOC grid/battery source scheduler
* `battery_protection` — SOC threshold → shutdown + restore voltage
* `notify`             — condition met → email/webhook notification

The rule-table type is a two-level nested grid (matching Solar Assistant): the
top level is the setting to write, the outer level is a list of group blocks
(each a free-choice condition), and the inner level is a list of ranges (each a
free-choice condition plus its own action value).  Only the top-level setting
is ever written to the inverter; the conditions are pure validation gates.
"""

from __future__ import annotations

import json
import logging
import socket
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .protocol import (
    HOLDING_BY_NAME,
    HOLDING_REGISTERS,
    MODBUS_READ_HOLD,
    MODBUS_WRITE_SINGLE,
    build_read_request,
    build_write_request,
    find_frames,
)

logger = logging.getLogger("luxmon.automation")

# ── Settings keys ───────────────────────────────────────────────────────────
SETTING_AUTOMATIONS = "automations_v2"
SETTING_ENABLED = "automation_enabled"
SETTING_DRY_RUN = "automation_global_dry_run"

# ── Register primitives (shared with quick_charge.py and api/__init__.py) ────


def _engineering_to_raw(value: float, meta: dict) -> Optional[int]:
    """Convert an engineering-unit value to a raw Modbus register value."""
    try:
        scale = meta.get("scale", 1.0) or 1.0
        raw = int(round(value / scale))
        return raw
    except Exception:
        return None


def _raw_to_engineering(raw: int, meta: dict) -> float:
    scale = meta.get("scale", 1.0) or 1.0
    return raw * scale


def _write_holding_register(
    host: str,
    port: int,
    datalog_serial: str,
    inverter_serial: str,
    register: int,
    value: int,
    timeout: float = 10.0,
) -> Tuple[bool, str]:
    """Send a WriteSingleRegister request and verify the echo."""
    req = build_write_request(datalog_serial, inverter_serial, register, value)
    sock: Optional[socket.socket] = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)
        sock.sendall(req)

        deadline = time.time() + timeout
        buffer = b""
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buffer += chunk
            frames = find_frames(buffer)
            for frame in frames:
                if frame.is_error:
                    return False, f"Modbus error response: code {frame.error_code}"
                if frame.device_function == MODBUS_WRITE_SINGLE and frame.register == register:
                    if frame.values and frame.values[0] == value:
                        return True, f"Wrote register {register} = {value}"
                    return True, f"Write accepted (echo value {frame.values})"
        return False, "No valid write response received"
    except Exception as exc:
        return False, f"Write failed: {exc}"
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def _read_holding_register(
    host: str,
    port: int,
    datalog_serial: str,
    inverter_serial: str,
    register: int,
    timeout: float = 10.0,
) -> Tuple[bool, Optional[int], str]:
    """Read a single holding register back for verification."""
    req = build_read_request(
        datalog_serial, inverter_serial, MODBUS_READ_HOLD, register, 1
    )
    sock: Optional[socket.socket] = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)
        sock.sendall(req)

        deadline = time.time() + timeout
        buffer = b""
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buffer += chunk
            frames = find_frames(buffer)
            for frame in frames:
                if frame.is_error:
                    return False, None, f"Modbus error response: code {frame.error_code}"
                if frame.is_read_hold and frame.register == register and frame.values:
                    return True, frame.values[0], f"Read back {register} = {frame.values[0]}"
        return False, None, "No valid read response received"
    except Exception as exc:
        return False, None, f"Read failed: {exc}"
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


# ── SolarAssistant setting name → lux-mon holding-register name ─────────────
#
# SolarAssistant exposes 50 abstract inverter settings.  We map each to a
# lux-mon holding-register name in HOLDING_BY_NAME where known.  Unmapped keys
# can still be configured in the UI but will log a warning and skip the write.

SETTING_NAME_TO_REGISTER: Dict[str, Optional[str]] = {
    # Charging voltages
    "absorption_charge_voltage": "lead_acid_charge_voltage",
    "float_charge_voltage": "floating_voltage",
    "equalization_voltage": "equalization_voltage",

    # Grid charge
    "grid_charge": None,
    "grid_charge_according_to": None,
    "grid_charge_current": "ac_charge_battery_current",
    "grid_charge_slot_1_start": "ac_charge_period_1_start",
    "grid_charge_slot_1_end": "ac_charge_period_1_end",
    "grid_charge_slot_2_start": "ac_charge_period_2_start",
    "grid_charge_slot_2_end": "ac_charge_period_2_end",
    "grid_charge_slot_3_start": "ac_charge_period_3_start",
    "grid_charge_slot_3_end": "ac_charge_period_3_end",
    "grid_charge_start_capacity": "ac_charge_start_battery_soc",
    "grid_charge_start_voltage": "ac_charge_start_battery_voltage",
    "grid_charge_stop_capacity": "ac_charge_end_battery_soc",
    "grid_charge_stop_voltage": "ac_charge_end_battery_voltage",

    # AC first / grid first
    "grid_first_slot_1_start": "ac_first_period_1_start",
    "grid_first_slot_1_end": "ac_first_period_1_end",
    "grid_first_slot_2_start": "ac_first_period_2_start",
    "grid_first_slot_2_end": "ac_first_period_2_end",
    "grid_first_slot_3_start": "ac_first_period_3_start",
    "grid_first_slot_3_end": "ac_first_period_3_end",

    # Max charge/discharge currents
    "max_charge_current": "ac_charge_battery_current",  # best available proxy
    "max_discharge_current": "lead_acid_discharge_rate",

    # Force off-grid / on-grid mode
    "force_off_grid": None,
    "on-grid_mode": None,

    # AC couple / combine PV/AC supply
    "ac_couple": None,
    "combine_pv/ac_supply": None,

    # Export / feed-in
    "export_to_grid": None,
    "export_power_rate": "feed_in_grid_power_percent",

    # Discharge limits
    "discharge_according_to": None,
    "stop_discharge_voltage": "lead_acid_discharge_cut_voltage",
    "stop_discharge_capacity": "discharge_cutoff_soc_eod",

    # Shutdown / battery protection
    "shutdown_battery_voltage": "battery_low_to_utility_voltage",
    "shutdown_battery_capacity": "battery_low_to_utility_soc",

    # Warning thresholds
    "warning_start_voltage": "battery_warning_voltage",
    "warning_start_capacity": "battery_warning_soc",

    # Generator charge
    "generator_charge_according_to": None,
    "generator_charge_current": "forced_charge_power",
    "generator_charge_start_voltage": None,
    "generator_charge_start_capacity": None,
    "generator_charge_stop_voltage": None,
    "generator_charge_stop_capacity": None,
    "generator_power": "max_generator_input_power",

    # Smart load
    "smart_load": None,
    "smart_load_start_pv_power": None,
    "smart_load_start_capacity": None,
    "smart_load_start_voltage": None,
    "smart_load_stop_capacity": None,
    "smart_load_stop_voltage": None,

    # Soft start / power factor
    "soft_start_slope": "soft_start_slope",
    "active_power_percent": "active_power_percent",
    "reactive_power_percent": "reactive_power_percent",
    "power_factor_command": "power_factor_command",

    # Charge / discharge power percent
    "charge_power_percent": "charge_power_percent",
    "discharge_power_percent": "discharge_power_percent",
    "ac_charge_power_percent": "ac_charge_power_percent",
}

REGISTER_TO_SETTING_NAME = {
    reg_name: sa_name
    for sa_name, reg_name in SETTING_NAME_TO_REGISTER.items()
    if reg_name
}


# ── Condition dimensions ─────────────────────────────────────────────────────

CONDITION_KINDS: Dict[str, Dict[str, Any]] = {
    "time_of_day": {"label": "Time of day", "unit": "", "value_type": "time"},
    "day_of_week": {"label": "Day of week", "unit": "", "value_type": "select",
                    "options": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]},
    "month_of_year": {"label": "Month of year", "unit": "", "value_type": "select",
                      "options": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]},
    "battery_voltage": {"label": "Battery voltage", "unit": "V", "value_type": "number"},
    "battery_soc": {"label": "Battery state of charge", "unit": "%", "value_type": "number"},
    "battery_current": {"label": "Battery current", "unit": "A", "value_type": "number"},
    "grid_voltage": {"label": "Grid voltage", "unit": "V", "value_type": "number"},
    "grid_frequency": {"label": "Grid frequency", "unit": "Hz", "value_type": "number"},
    "pv_energy_progress": {"label": "PV energy progress", "unit": "%", "value_type": "number"},
    "pv_energy_remaining_today": {"label": "PV energy remaining today", "unit": "%", "value_type": "number"},
    "load_power": {"label": "Load power", "unit": "W", "value_type": "number"},
    "inverter_temp": {"label": "Inverter temperature", "unit": "°C", "value_type": "number"},
    "battery_temp": {"label": "Battery temperature", "unit": "°C", "value_type": "number"},
}

# Condition kind -> snapshot sensor key (None for scheduler-only kinds).
CONDITION_SENSOR = {
    "time_of_day": None,
    "day_of_week": None,
    "month_of_year": None,
    "battery_voltage": "battery_voltage",
    "battery_soc": "soc",
    "battery_current": "battery_current",
    "grid_voltage": "grid_voltage",
    "grid_frequency": "grid_frequency",
    "pv_energy_progress": None,
    "pv_energy_remaining_today": None,
    "load_power": "eps_power",
    "inverter_temp": "temp_inverter",
    "battery_temp": "temp_battery",
}

# Extra lux-mon-only conditions surfaced under "show all".
EXTRA_CONDITION_KINDS = {
    "pv_power": {"label": "Solar PV power", "unit": "W", "value_type": "number"},
    "grid_power": {"label": "Grid power", "unit": "W", "value_type": "number"},
}
EXTRA_CONDITION_SENSOR = {
    "pv_power": "pv1_power",
    "grid_power": "grid_import_power",
}

ALL_CONDITION_KINDS = {**CONDITION_KINDS, **EXTRA_CONDITION_KINDS}
ALL_CONDITION_SENSOR = {**CONDITION_SENSOR, **EXTRA_CONDITION_SENSOR}


# ── Automation types ─────────────────────────────────────────────────────────

TYPE_RULE_TABLE = "rule_table"
TYPE_BATTERY_SOC = "battery_soc"
TYPE_BATTERY_PROTECTION = "battery_protection"
TYPE_NOTIFY = "notify"

AUTOMATION_TYPES = [
    {"id": TYPE_RULE_TABLE, "label": "Rule table", "phx_value_id": "set-setting"},
    {"id": TYPE_BATTERY_SOC, "label": "Battery state of charge control", "phx_value_id": "battery-soc"},
    {"id": TYPE_BATTERY_PROTECTION, "label": "Battery protection", "phx_value_id": "battery-protection"},
    {"id": TYPE_NOTIFY, "label": "Send notification", "phx_value_id": "notify"},
]


# ── Data models ─────────────────────────────────────────────────────────────

@dataclass
class Condition:
    kind: str
    min: Optional[float] = None
    max: Optional[float] = None
    value: Optional[Any] = None  # exact/select value (e.g. day_of_week)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "min": self.min, "max": self.max, "value": self.value}

    @classmethod
    def from_dict(cls, data: dict) -> "Condition":
        return cls(
            kind=data.get("kind", "time_of_day"),
            min=_to_float(data.get("min")),
            max=_to_float(data.get("max")),
            value=data.get("value"),
        )


@dataclass
class Range:
    """An inner range: a condition gate plus the value to write when it matches."""
    condition: Condition
    action_value: Optional[float] = None

    def to_dict(self) -> dict:
        return {"condition": self.condition.to_dict(), "action_value": self.action_value}

    @classmethod
    def from_dict(cls, data: dict) -> "Range":
        return cls(
            condition=Condition.from_dict(data.get("condition", {})),
            action_value=_to_float(data.get("action_value")),
        )


@dataclass
class Group:
    """An outer block: a condition gate plus the inner ranges it contains."""
    condition: Condition
    ranges: List[Range] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"condition": self.condition.to_dict(), "ranges": [r.to_dict() for r in self.ranges]}

    @classmethod
    def from_dict(cls, data: dict) -> "Group":
        return cls(
            condition=Condition.from_dict(data.get("condition", {})),
            ranges=[Range.from_dict(r) for r in data.get("ranges", [])],
        )


@dataclass
class Automation:
    """A single automation rule.

    The rule-table type is a two-level nested grid, matching Solar Assistant:

        set <setting>  ->  when <group condition>  ->  and when <range condition>
                           ->  set value to <range action_value>

    * `setting` is the top-level action (the holding register to write).
    * `group_kind` is the outer grouping condition kind (free choice).
    * `range_kind` is the inner range condition kind (free choice).
    * `groups` is the list of outer blocks; each block has a `condition` (the
      outer gate) and a list of `ranges`.
    * Each `range` has its own `condition` (the inner gate) and `action_value`
      (the value written to `setting` when both the group and range match).
    * Only `setting` is ever written to the inverter; the conditions are pure
      validation gates.

    Both the group kind and range kind are free choices from the full condition
    list — nothing is hardcoded to time/voltage.
    """
    id: str
    name: str
    type: str
    enabled: bool = True
    disabled_until: float = 0.0
    setting: Optional[str] = None
    group_kind: Optional[str] = None
    range_kind: Optional[str] = None
    groups: List[Group] = field(default_factory=list)
    conditions: List[Condition] = field(default_factory=list)  # notify only
    action_value: Optional[float] = None  # battery_protection only
    restore_value: Optional[float] = None
    points: List[dict] = field(default_factory=list)
    threshold: Optional[float] = None
    notify_message: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "enabled": self.enabled,
            "disabled_until": self.disabled_until,
            "setting": self.setting,
            "group_kind": self.group_kind,
            "range_kind": self.range_kind,
            "groups": [g.to_dict() for g in self.groups],
            "conditions": [c.to_dict() for c in self.conditions],
            "action_value": self.action_value,
            "restore_value": self.restore_value,
            "points": self.points,
            "threshold": self.threshold,
            "notify_message": self.notify_message,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Automation":
        return cls(
            id=str(data.get("id", "")),
            name=data.get("name", ""),
            type=data.get("type", TYPE_RULE_TABLE),
            enabled=bool(data.get("enabled", True)),
            disabled_until=float(data.get("disabled_until", 0.0) or 0.0),
            setting=data.get("setting"),
            group_kind=data.get("group_kind"),
            range_kind=data.get("range_kind"),
            groups=[Group.from_dict(g) for g in data.get("groups", [])],
            conditions=[Condition.from_dict(c) for c in data.get("conditions", [])],
            action_value=_to_float(data.get("action_value")),
            restore_value=_to_float(data.get("restore_value")),
            points=list(data.get("points", [])),
            threshold=_to_float(data.get("threshold")),
            notify_message=data.get("notify_message", ""),
        )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _now_in_tz(tz_name: str) -> datetime:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz)


def _parse_hhmm(value: str) -> Optional[int]:
    try:
        h, m = str(value).strip().split(":", 1)
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _time_matches(value: Any, kind: str, tz_name: str = "America/Chicago") -> bool:
    """Check scheduler-only condition kinds."""
    now = _now_in_tz(tz_name)
    if kind == "time_of_day":
        txt = str(value) if value else ""
        if "-" in txt:
            start, end = txt.split("-", 1)
        else:
            return False
        start_mins = _parse_hhmm(start)
        end_mins = _parse_hhmm(end)
        if start_mins is None or end_mins is None:
            return False
        current = now.hour * 60 + now.minute
        if start_mins <= end_mins:
            return start_mins <= current <= end_mins
        return current >= start_mins or current <= end_mins
    if kind == "day_of_week":
        return now.strftime("%a") == str(value)
    if kind == "month_of_year":
        return now.strftime("%b") == str(value)
    return False


def _sensor_value(snapshot: Dict, sensor: str) -> Optional[float]:
    if sensor not in snapshot:
        return None
    val = snapshot[sensor]
    if isinstance(val, dict):
        return float(val.get("value", 0))
    try:
        return float(val)
    except Exception:
        return None


def _condition_matches(condition: Condition, snapshot: Dict, tz_name: str = "America/Chicago") -> bool:
    kind = condition.kind
    if kind in ("time_of_day", "day_of_week", "month_of_year"):
        return _time_matches(condition.value, kind, tz_name)

    sensor = ALL_CONDITION_SENSOR.get(kind)
    if sensor is None:
        logger.warning("Condition kind %s has no live sensor mapping yet", kind)
        return False

    value = _sensor_value(snapshot, sensor)
    if value is None:
        return False

    if condition.min is not None and value < condition.min:
        return False
    if condition.max is not None and value > condition.max:
        return False
    return True


def _all_conditions_match(conditions: List[Condition], snapshot: Dict, tz_name: str = "America/Chicago") -> bool:
    if not conditions:
        return True
    return all(_condition_matches(c, snapshot, tz_name) for c in conditions)


def _find_matching_range(
    auto: "Automation", snapshot: Dict, tz_name: str = "America/Chicago"
) -> Optional[Tuple[Group, Range]]:
    """Find the (group, range) whose conditions both match the snapshot.

    The outer group condition is evaluated first; if it matches, the inner
    ranges are evaluated in order and the first matching range wins.  Returns
    None when no group/range matches.
    """
    for group in auto.groups:
        if not _condition_matches(group.condition, snapshot, tz_name):
            continue
        for rng in group.ranges:
            if _condition_matches(rng.condition, snapshot, tz_name):
                return group, rng
    return None


def _clamp_to_register(value: float, meta: dict) -> float:
    min_v = meta.get("min")
    max_v = meta.get("max")
    if min_v is not None:
        value = max(value, float(min_v))
    if max_v is not None:
        value = min(value, float(max_v))
    return value


# ── Automation engine ───────────────────────────────────────────────────────

class AutomationEngine:
    """Evaluate and execute SolarAssistant-style automations."""

    def __init__(
        self,
        db_host: str,
        db_port: int,
        db_user: str,
        db_password: str,
        db_name: str,
        table_prefix: str = "lux_",
        notifiers: Optional[Any] = None,
    ):
        self.db_host = db_host
        self.db_port = db_port
        self.db_user = db_user
        self.db_password = db_password
        self.db_name = db_name
        self.table_prefix = table_prefix
        self.settings_table = f"{table_prefix}settings"
        self.log_table = f"{table_prefix}automation_log"
        self._notifiers = notifiers
        self._last_eval: Dict[str, bool] = {}
        self._last_notify: Dict[str, float] = {}
        self._last_written_value: Dict[str, Any] = {}

    def _db(self):
        import pymysql
        return pymysql.connect(
            host=self.db_host,
            port=self.db_port,
            user=self.db_user,
            password=self.db_password,
            database=self.db_name,
            autocommit=True,
        )

    def _get_setting(self, name: str, default: str = "") -> str:
        try:
            with self._db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT value FROM {self.settings_table} WHERE name = %s",
                        (name,),
                    )
                    row = cur.fetchone()
                    return row[0] if row and row[0] is not None else default
        except Exception:
            logger.exception("Failed to read setting %s", name)
            return default

    def _set_setting(self, name: str, value: str) -> None:
        try:
            with self._db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"INSERT INTO {self.settings_table} (name, value) VALUES (%s, %s) "
                        f"ON DUPLICATE KEY UPDATE value = VALUES(value)",
                        (name, value),
                    )
        except Exception:
            logger.exception("Failed to write setting %s", name)

    def load(self) -> List[Automation]:
        raw = self._get_setting(SETTING_AUTOMATIONS, "[]")
        try:
            data = json.loads(raw) if raw else []
            return [Automation.from_dict(a) for a in data]
        except Exception:
            logger.exception("Failed to parse automations JSON")
            return []

    def save(self, automations: List[Automation]) -> None:
        data = [a.to_dict() for a in automations]
        self._set_setting(SETTING_AUTOMATIONS, json.dumps(data))

    def load_dict(self) -> Dict[str, Any]:
        return {
            "enabled": str(self._get_setting(SETTING_ENABLED, "false")).lower() in ("true", "1", "yes"),
            "global_dry_run": str(self._get_setting(SETTING_DRY_RUN, "true")).lower() in ("true", "1", "yes"),
            "automations": [a.to_dict() for a in self.load()],
        }

    def save_dict(self, data: Dict[str, Any]) -> None:
        self._set_setting(SETTING_ENABLED, "true" if data.get("enabled") else "false")
        self._set_setting(SETTING_DRY_RUN, "true" if data.get("global_dry_run", True) else "false")
        if "automations" in data:
            self.save([Automation.from_dict(a) for a in data["automations"]])

    def _log(
        self,
        automation_id: str,
        automation_name: str,
        register_name: str,
        raw_value: Optional[int],
        scaled_value: Optional[float],
        dry_run: bool,
        success: bool,
        message: str,
    ) -> None:
        try:
            with self._db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO {self.log_table}
                        (rule_id, rule_name, register_name, raw_value, scaled_value,
                         dry_run, success, message, ts)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        """,
                        (automation_id, automation_name, register_name, raw_value,
                         scaled_value, int(dry_run), int(success), message),
                    )
        except Exception:
            logger.exception("Failed to write automation log")

    def evaluate_and_apply(
        self,
        snapshot: Dict,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
        timezone: str = "America/Chicago",
    ) -> None:
        enabled = str(self._get_setting(SETTING_ENABLED, "false")).lower() in ("true", "1", "yes")
        dry_run = str(self._get_setting(SETTING_DRY_RUN, "true")).lower() in ("true", "1", "yes")
        automations = self.load()
        now = time.time()

        for auto in automations:
            if not enabled or not auto.enabled:
                continue
            if auto.disabled_until and now < auto.disabled_until:
                continue

            try:
                self._evaluate_one(
                    auto, snapshot,
                    dongle_host, dongle_port, datalog_serial, inverter_serial,
                    dry_run=dry_run, timezone=timezone,
                )
            except Exception:
                logger.exception("Automation %s evaluation failed", auto.id)

    def _evaluate_one(
        self,
        auto: Automation,
        snapshot: Dict,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
        dry_run: bool,
        timezone: str,
    ) -> None:
        matched = _all_conditions_match(auto.conditions, snapshot, timezone)
        was_active = self._last_eval.get(auto.id, False)

        if auto.type == TYPE_NOTIFY:
            if matched and not was_active:
                self._send_notification(auto)
            self._last_eval[auto.id] = matched
            return

        if auto.type == TYPE_BATTERY_PROTECTION:
            self._evaluate_battery_protection(
                auto, snapshot,
                dongle_host, dongle_port, datalog_serial, inverter_serial,
                dry_run, timezone,
            )
            return

        if auto.type == TYPE_BATTERY_SOC:
            self._evaluate_battery_soc(
                auto, snapshot,
                dongle_host, dongle_port, datalog_serial, inverter_serial,
                dry_run, timezone,
            )
            return

        # TYPE_RULE_TABLE — two-level nested grid:
        #   set <setting> -> when <group condition> -> and when <range condition>
        #   -> set value to <range action_value>
        # Only `setting` is written; the group/range conditions are pure gates.
        match = _find_matching_range(auto, snapshot, timezone)
        if match is not None and auto.setting is not None:
            _, rng = match
            if rng.action_value is not None:
                value = float(rng.action_value)
                last = self._last_written_value.get(auto.id)
                if last != value:
                    self._do_write(
                        auto.id, auto.name, auto.setting, value,
                        dongle_host, dongle_port, datalog_serial, inverter_serial,
                        dry_run,
                    )
                    self._last_written_value[auto.id] = value
                self._last_eval[auto.id] = True
                return
        if was_active and auto.restore_value is not None and auto.setting is not None:
            value = float(auto.restore_value)
            last = self._last_written_value.get(auto.id)
            if last != value:
                self._do_write(
                    auto.id, auto.name, auto.setting, value,
                    dongle_host, dongle_port, datalog_serial, inverter_serial,
                    dry_run,
                )
                self._last_written_value[auto.id] = value
        self._last_eval[auto.id] = False

    def _evaluate_battery_protection(
        self,
        auto: Automation,
        snapshot: Dict,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
        dry_run: bool,
        timezone: str,
    ) -> None:
        soc = _sensor_value(snapshot, "soc")
        if soc is None or auto.threshold is None:
            return

        was_active = self._last_eval.get(auto.id, False)
        matched = soc <= auto.threshold

        if matched:
            if auto.action_value is not None:
                value = float(auto.action_value)
                last = self._last_written_value.get(auto.id)
                if last != value:
                    self._do_write(
                        auto.id, auto.name, "shutdown_battery_voltage", value,
                        dongle_host, dongle_port, datalog_serial, inverter_serial, dry_run,
                    )
                    self._last_written_value[auto.id] = value
            self._last_eval[auto.id] = True
        else:
            if was_active and auto.restore_value is not None:
                value = float(auto.restore_value)
                last = self._last_written_value.get(auto.id)
                if last != value:
                    self._do_write(
                        auto.id, auto.name, "shutdown_battery_voltage", value,
                        dongle_host, dongle_port, datalog_serial, inverter_serial, dry_run,
                    )
                    self._last_written_value[auto.id] = value
            self._last_eval[auto.id] = False

    def _evaluate_battery_soc(
        self,
        auto: Automation,
        snapshot: Dict,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
        dry_run: bool,
        timezone: str,
    ) -> None:
        """Battery SOC control: choose grid or battery source based on time + SOC."""
        soc = _sensor_value(snapshot, "soc")
        if soc is None:
            return

        now = _now_in_tz(timezone)
        current_mins = now.hour * 60 + now.minute

        target_source = "battery"
        if auto.points:
            # Find the active interval.  Points are sorted by time; each point
            # defines the source from its time until the next point.
            points = sorted(auto.points, key=lambda p: _parse_hhmm(p.get("time", "00:00")) or 0)
            for i, point in enumerate(points):
                t = _parse_hhmm(point.get("time", "00:00"))
                if t is None:
                    continue
                next_t = _parse_hhmm(points[(i + 1) % len(points)].get("time", "00:00"))
                if next_t is None:
                    next_t = t
                if t <= next_t:
                    if t <= current_mins < next_t:
                        target_source = point.get("source", "battery")
                        break
                else:
                    # interval wraps midnight
                    if current_mins >= t or current_mins < next_t:
                        target_source = point.get("source", "battery")
                        break

        # TODO: wire to the real source-select registers once their semantics are
        # confirmed.  For now we log the decision so the user can verify behavior.
        msg = f"Battery SOC control: active interval source={target_source}, SOC={soc:.1f}%"
        self._log(auto.id, auto.name, "battery_source", None, None, dry_run, True, msg)
        logger.info("[Automation %s] %s", auto.id, msg)

    def _do_write(
        self,
        automation_id: str,
        automation_name: str,
        setting_name: str,
        value: float,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
        dry_run: bool,
    ) -> None:
        reg_name = SETTING_NAME_TO_REGISTER.get(setting_name)
        if reg_name is None:
            self._log(automation_id, automation_name, setting_name, None, value, dry_run, False,
                      f"Setting '{setting_name}' is not mapped to a holding register yet")
            return
        reg_addr = HOLDING_BY_NAME.get(reg_name)
        if reg_addr is None:
            self._log(automation_id, automation_name, setting_name, None, value, dry_run, False,
                      f"Register '{reg_name}' not found in HOLDING_BY_NAME")
            return

        meta = HOLDING_REGISTERS[reg_addr]
        clamped = _clamp_to_register(value, meta)
        raw = _engineering_to_raw(clamped, meta)
        if raw is None:
            self._log(automation_id, automation_name, setting_name, None, value, dry_run, False,
                      f"Failed to convert {clamped} to raw value for {reg_name}")
            return

        if dry_run:
            self._log(automation_id, automation_name, setting_name, raw, clamped, True, True,
                      f"DRY-RUN would write {setting_name}={clamped} ({reg_name}@{reg_addr}={raw})")
            logger.info("[DRY-RUN] Automation %s: %s=%s", automation_id, setting_name, clamped)
            return

        ok, msg = _write_holding_register(
            dongle_host, dongle_port, datalog_serial, inverter_serial, reg_addr, raw
        )
        self._log(automation_id, automation_name, setting_name, raw, clamped, False, ok, msg)
        if not ok:
            logger.warning("Automation %s write failed: %s", automation_id, msg)

    def _send_notification(self, auto: Automation) -> None:
        if self._notifiers is None:
            return
        last = self._last_notify.get(auto.id, 0)
        now = time.time()
        if now - last < 300:
            return
        self._last_notify[auto.id] = now
        try:
            msg = auto.notify_message or f"Automation '{auto.name}' condition matched"
            self._notifiers.send(auto.name, True, 0.0, msg)
            self._log(auto.id, auto.name, "notify", None, None, False, True, msg)
        except Exception:
            logger.exception("Automation notification failed")
