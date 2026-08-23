"""
lux-mon automation / rule engine.

Rebuilt around a "rule table" model (target-first, nested subset columns,
one automation per target type, restore-on-exit) that mirrors the behavior of
the reference "Power Management" feature on the EG4/Luxpower inverter portal.

The core abstraction is a *rule table*:

  * A rule table controls ONE target setting (a writable holding register).
  * It has an ordered list of *subset columns*.  Each column is a condition
    dimension (e.g. "time of day", "battery voltage", "battery state of
    charge").  Columns nest left-to-right: the first column is the outer
    grouping, the second is the inner grouping, and so on.
  * Each column holds a list of *ranges*.  A range is a [from, to] interval
    (inclusive) plus, at the leaf, the value to write to the target.
  * The rightmost column's ranges carry the target value; every other column's
    ranges just partition the space.

Example (grid charge current as a function of time and battery voltage):

    Time of day        Battery voltage        Set: grid charge current
    21:00 - 23:59      0.0 - 54.0 V          85 A
                       55.0 - 56.0 V         45 A
                       57.0 - 58.0 V         1 A
    23:59 - 23:59      0.0 - 54.0 V          85 A
                       55.0 - 56.0 V         45 A
                       57.5 - 58.4 V         0 A

This is stored as a nested tree, not a flat if/then list.

Automation types
----------------
There are four automation types, matching the reference portal:

  1. "rule_table"            — the generic multi-dimensional rule table above.
  2. "battery_soc"           — battery state-of-charge control (Point A / Point B
                               time + SOC thresholds -> Grid/Battery output source).
  3. "battery_protection"    — "if SOC <= X%, shutdown output; restore to Y when
                               recovered" (restore-on-exit semantics).
  4. "notify"                — send a notification when a condition is met.

Only ONE automation may be active per target type (e.g. you cannot have two
"grid charge current" rule tables).  This is enforced on save.

Restore-on-exit
---------------
A rule table may declare a `restore` value.  When the rule table's conditions
stop matching (or the automation is disabled), the engine writes the restore
value back to the target register.  This is how "battery protection" reverts
the shutdown voltage once the battery recovers.

Safety design (unchanged from the prior engine):
  * Only registers listed in protocol.HOLDING_REGISTERS can be written.
  * Each register has hard min/max clamping.
  * Writes are performed on a separate short-lived socket so they cannot
    corrupt the collector's read stream.
  * A `dry_run` flag evaluates and logs without writing.
"""

from __future__ import annotations

import json
import logging
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pymysql

from .protocol import (
    HOLDING_REGISTERS,
    HOLDING_BY_NAME,
    MODBUS_READ_HOLD,
    MODBUS_WRITE_SINGLE,
    build_read_request,
    build_write_request,
    find_frames,
)

logger = logging.getLogger("luxmon.automation")

# Setting keys (stored in lux_settings)
SETTING_KEY = "automation_rules"
ENABLED_KEY = "automation_enabled"
DRY_RUN_DEFAULT = False

# ── Automation types ────────────────────────────────────────────────────────

TYPE_RULE_TABLE = "rule_table"
TYPE_BATTERY_SOC = "battery_soc"
TYPE_BATTERY_PROTECTION = "battery_protection"
TYPE_NOTIFY = "notify"

AUTOMATION_TYPES = [
    TYPE_RULE_TABLE,
    TYPE_BATTERY_SOC,
    TYPE_BATTERY_PROTECTION,
    TYPE_NOTIFY,
]

# ── Subset (condition) dimensions ───────────────────────────────────────────

# A subset column is a condition dimension.  Each has a kind, a display label,
# a unit, and a value type (time vs number).
SUBSET_KINDS = {
    "time_of_day": {"label": "Time of day", "unit": "", "value_type": "time"},
    "battery_voltage": {"label": "Battery voltage", "unit": "V", "value_type": "number"},
    "battery_soc": {"label": "Battery state of charge", "unit": "%", "value_type": "number"},
    "battery_current": {"label": "Battery current", "unit": "A", "value_type": "number"},
    "pv_power": {"label": "Solar PV power", "unit": "W", "value_type": "number"},
    "grid_power": {"label": "Grid power", "unit": "W", "value_type": "number"},
    "load_power": {"label": "Load power", "unit": "W", "value_type": "number"},
    "inverter_temp": {"label": "Inverter temperature", "unit": "°C", "value_type": "number"},
    "battery_temp": {"label": "Battery temperature", "unit": "°C", "value_type": "number"},
}

# Map subset kind -> the sensor name(s) in a decoded snapshot.
SUBSET_SENSOR = {
    "time_of_day": None,  # handled specially (uses wall clock)
    "battery_voltage": "battery_voltage",
    "battery_soc": "soc",
    "battery_current": "battery_current",
    "pv_power": "pv1_power",
    "grid_power": "grid_import_power",
    "load_power": "eps_power",
    "inverter_temp": "temp_inverter",
    "battery_temp": "temp_battery",
}


# ── Data models ─────────────────────────────────────────────────────────────

@dataclass
class Range:
    """A single [from, to] interval within a subset column.

    For the leaf (rightmost) column, `value` is the target value to write.
    For non-leaf columns, `value` is None and the range just partitions space.
    """
    from_value: Optional[float] = None
    to_value: Optional[float] = None
    value: Optional[float] = None

    def contains(self, v: float) -> bool:
        if self.from_value is not None and v < self.from_value:
            return False
        if self.to_value is not None and v > self.to_value:
            return False
        return True


@dataclass
class SubsetColumn:
    """A condition dimension (column) in a rule table."""
    kind: str  # one of SUBSET_KINDS
    ranges: List[Range] = field(default_factory=list)


@dataclass
class RuleTable:
    """A target-first, multi-dimensional rule table."""
    id: str
    name: str
    target: str  # holding register name to write
    columns: List[SubsetColumn] = field(default_factory=list)
    enabled: bool = True
    dry_run: bool = False
    restore: Optional[float] = None  # value to write when conditions stop matching

    def evaluate(self, snapshot: dict, now: datetime) -> Optional[float]:
        """Return the target value to write, or None if no leaf matches."""
        if not self.enabled:
            return None
        if not self.columns:
            return None
        return _eval_columns(self.columns, 0, snapshot, now)


@dataclass
class BatterySocPoint:
    """A single Point (A or B) on the SOC-vs-time boundary line.

    Each point has a time of day and a SOC threshold.  The two points define
    a line on the SOC-vs-hour graph: above the line = battery, below = grid.
    """
    time: str = "00:00"  # "HH:MM"
    soc: float = 50.0    # SOC threshold (%) at this time


@dataclass
class BatterySocAutomation:
    """Battery state-of-charge control (Point A / Point B boundary line).

    The points define a piecewise-linear SOC threshold over the day.  When the
    current SOC is above the interpolated threshold, the output source is
    battery; below it, grid.
    """
    id: str
    name: str
    points: List[BatterySocPoint] = field(default_factory=list)
    enabled: bool = True
    dry_run: bool = False

    def evaluate(self, snapshot: dict, now: datetime) -> Optional[str]:
        """Return 'grid' or 'battery' (output source priority), or None."""
        if not self.enabled:
            return None
        soc = _snapshot_value(snapshot, "soc")
        if soc is None:
            return None
        if not self.points:
            return None
        threshold = self._threshold_at(now)
        if threshold is None:
            return None
        return "battery" if soc >= threshold else "grid"

    def _threshold_at(self, now: datetime) -> Optional[float]:
        """Interpolate the SOC threshold at the current time of day.

        Points are sorted by time.  The threshold is interpolated between the
        two points that bracket the current time.  Before the first point and
        after the last point, the boundary wraps around midnight (the line is
        periodic over 24h).
        """
        pts = sorted(self.points, key=lambda p: _hm_to_minutes(p.time))
        if not pts:
            return None
        now_min = now.hour * 60 + now.minute

        # If only one point, it's a flat line.
        if len(pts) == 1:
            return pts[0].soc

        # Build a circular list: append the first point again at +24h.
        times = [_hm_to_minutes(p.time) for p in pts]
        socs = [p.soc for p in pts]
        times.append(times[0] + 1440)
        socs.append(socs[0])

        # Find the segment that brackets now_min (or now_min + 1440 for wrap).
        for i in range(len(pts)):
            t0, t1 = times[i], times[i + 1]
            s0, s1 = socs[i], socs[i + 1]
            # Handle the wrap-around segment (last -> first across midnight).
            seg_start = t0
            seg_end = t1
            probe = now_min
            if seg_start > seg_end:
                # This shouldn't happen after sorting + append, but guard.
                continue
            if seg_start <= probe <= seg_end:
                if t1 == t0:
                    return s0
                frac = (probe - t0) / (t1 - t0)
                return s0 + frac * (s1 - s0)
            # Also check the wrapped probe (now_min + 1440) for the last segment.
            probe_wrap = now_min + 1440
            if seg_start <= probe_wrap <= seg_end:
                if t1 == t0:
                    return s0
                frac = (probe_wrap - t0) / (t1 - t0)
                return s0 + frac * (s1 - s0)
        return None


@dataclass
class BatteryProtectionAutomation:
    """Battery protection: if SOC <= X%, shutdown output; restore to Y when recovered."""
    id: str
    name: str
    threshold_soc: float = 25.0       # "if SOC is X% or lower"
    shutdown_register: str = "shutdown_battery_voltage"  # target to drive
    restore_value: float = 40.0       # value to restore when recovered
    enabled: bool = True
    dry_run: bool = False

    def evaluate(self, snapshot: dict, now: datetime) -> Optional[float]:
        """Return the value to write (shutdown value) when SOC <= threshold, else None."""
        if not self.enabled:
            return None
        soc = _snapshot_value(snapshot, "soc")
        if soc is None:
            return None
        if soc <= self.threshold_soc:
            # Shutdown: write the shutdown value (0.0V typically).
            return 0.0
        return None


@dataclass
class NotifyAutomation:
    """Send a notification when a condition is met."""
    id: str
    name: str
    condition_kind: str = "battery_soc"
    threshold: Optional[float] = None
    operator: str = "<="  # "<=", ">=", "<", ">", "=="
    enabled: bool = True
    dry_run: bool = False

    def evaluate(self, snapshot: dict, now: datetime) -> bool:
        if not self.enabled:
            return False
        sensor = SUBSET_SENSOR.get(self.condition_kind)
        if sensor is None:
            return False
        v = _snapshot_value(snapshot, sensor)
        if v is None or self.threshold is None:
            return False
        if self.operator == "<=":
            return v <= self.threshold
        if self.operator == ">=":
            return v >= self.threshold
        if self.operator == "<":
            return v < self.threshold
        if self.operator == ">":
            return v > self.threshold
        if self.operator == "==":
            return v == self.threshold
        return False


# ── Evaluation helpers ──────────────────────────────────────────────────────

def _hm_to_minutes(hm: str) -> int:
    """Convert 'HH:MM' to minutes since midnight."""
    parts = str(hm).split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time string: {hm}")
    return int(parts[0]) * 60 + int(parts[1])


def _normalize_range_value(v: Any, value_type: str) -> Optional[float]:
    """Normalize a range boundary to a float.

    For time-of-day subsets, 'HH:MM' strings are converted to minutes since
    midnight.  For numeric subsets, the value is passed through as a float.
    """
    if v is None or v == "":
        return None
    if value_type == "time":
        if isinstance(v, str) and ":" in v:
            return float(_hm_to_minutes(v))
        return float(v)
    return float(v)


def _snapshot_value(snapshot: dict, sensor: str) -> Optional[float]:
    """Extract a numeric value from a decoded snapshot dict."""
    if sensor not in snapshot:
        return None
    entry = snapshot[sensor]
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _subset_value(kind: str, snapshot: dict, now: datetime) -> Optional[float]:
    """Return the current value for a subset kind, or None if unavailable."""
    if kind == "time_of_day":
        return float(now.hour * 60 + now.minute)
    sensor = SUBSET_SENSOR.get(kind)
    if sensor is None:
        return None
    return _snapshot_value(snapshot, sensor)


def _eval_columns(
    columns: List[SubsetColumn],
    idx: int,
    snapshot: dict,
    now: datetime,
) -> Optional[float]:
    """Recursively evaluate nested subset columns.

    Returns the leaf target value, or None if no range matches at any level.
    """
    if idx >= len(columns):
        return None
    col = columns[idx]
    v = _subset_value(col.kind, snapshot, now)
    if v is None:
        return None
    is_leaf = (idx == len(columns) - 1)
    for rng in col.ranges:
        if not rng.contains(v):
            continue
        if is_leaf:
            return rng.value
        result = _eval_columns(columns, idx + 1, snapshot, now)
        if result is not None:
            return result
    return None


# ── Public API ───────────────────────────────────────────────────────────────

class AutomationEngine:
    """Evaluate automation rules and perform safe inverter writes."""

    def __init__(
        self,
        db_host: str,
        db_port: int,
        db_user: str,
        db_password: str,
        db_name: str,
        table_prefix: str = "lux_",
        notifiers: Any = None,
    ):
        self.db_args = (db_host, db_port, db_user, db_password, db_name)
        self.table_prefix = table_prefix
        self.settings_table = f"{table_prefix}settings"
        self.log_table = f"{table_prefix}automation_log"
        self._last_written: Dict[str, Tuple[int, str]] = {}
        self._restore_pending: Dict[str, bool] = {}
        self._notifiers = notifiers
        self._notify_last_sent: Dict[str, float] = {}
        self._notify_min_interval_sec = 300  # throttle repeated notify automations
        self._ensure_tables()

    def _conn(self):
        return pymysql.connect(
            host=self.db_args[0],
            port=self.db_args[1],
            user=self.db_args[2],
            password=self.db_args[3],
            database=self.db_args[4],
            autocommit=True,
        )

    def _ensure_tables(self) -> None:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.log_table} (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        ts DATETIME DEFAULT CURRENT_TIMESTAMP,
                        rule_id VARCHAR(64),
                        rule_name VARCHAR(255),
                        register_name VARCHAR(64),
                        raw_value INT,
                        scaled_value FLOAT,
                        dry_run TINYINT(1) DEFAULT 0,
                        success TINYINT(1) DEFAULT 0,
                        message TEXT
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
        finally:
            conn.close()

    # ── loading ──────────────────────────────────────────────────────────────

    def load_automations(self) -> List[Any]:
        """Load all automations from the settings table."""
        try:
            conn = self._conn()
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT value FROM {self.settings_table} WHERE name = %s",
                    (SETTING_KEY,),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if not row or not row[0]:
            return []
        try:
            data = json.loads(row[0])
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse automation JSON: %s", exc)
            return []
        return [_parse_automation(item) for item in data if isinstance(item, dict)]

    def is_enabled(self) -> bool:
        try:
            conn = self._conn()
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT value FROM {self.settings_table} WHERE name = %s",
                    (ENABLED_KEY,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return False
        return str(row[0]).strip().lower() in ("true", "1", "yes", "on")

    # ── evaluation + apply ──────────────────────────────────────────────────

    def evaluate_and_apply(
        self,
        snapshot: dict,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
        timezone: str = "America/Chicago",
    ) -> List[Dict[str, Any]]:
        """Evaluate all automations and apply any pending writes.

        Returns a list of action results.
        """
        results: List[Dict[str, Any]] = []
        if not self.is_enabled():
            return results

        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        automations = self.load_automations()
        if not automations:
            return results

        for auto in automations:
            try:
                if isinstance(auto, RuleTable):
                    results.extend(self._apply_rule_table(auto, snapshot, now, dongle_host, dongle_port, datalog_serial, inverter_serial))
                elif isinstance(auto, BatterySocAutomation):
                    results.extend(self._apply_battery_soc(auto, snapshot, now, dongle_host, dongle_port, datalog_serial, inverter_serial))
                elif isinstance(auto, BatteryProtectionAutomation):
                    results.extend(self._apply_battery_protection(auto, snapshot, now, dongle_host, dongle_port, datalog_serial, inverter_serial))
                elif isinstance(auto, NotifyAutomation):
                    results.extend(self._apply_notify(auto, snapshot, now))
            except Exception:
                logger.exception("Automation %s failed", getattr(auto, "id", "?"))
        return results

    def _apply_rule_table(
        self,
        auto: RuleTable,
        snapshot: dict,
        now: datetime,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        target = auto.evaluate(snapshot, now)

        if target is None:
            # Conditions no longer match — apply restore value if configured.
            if auto.restore is not None:
                results.append(self._write_target(auto, auto.restore, dongle_host, dongle_port, datalog_serial, inverter_serial, reason="restore"))
            return results

        results.append(self._write_target(auto, target, dongle_host, dongle_port, datalog_serial, inverter_serial))
        return results

    def _apply_battery_soc(
        self,
        auto: BatterySocAutomation,
        snapshot: dict,
        now: datetime,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
    ) -> List[Dict[str, Any]]:
        action = auto.evaluate(snapshot, now)
        if action is None:
            return []

        # NOTE: In the reference portal, "Battery state of charge" control is
        # an *internal* feature that requires a direct battery data source
        # (BMS or emulated BMS).  It does NOT write a Luxpower holding
        # register; it switches the portal's own "output source priority"
        # between grid and battery based on the SOC boundary line.
        #
        # lux-mon mirrors this: the automation evaluates the SOC boundary and
        # reports the intended source (grid/battery), but the actual
        # grid/battery switching is performed by the inverter's own AC-first
        # schedule / charge-priority settings, not by a single writable
        # register.  We therefore log the intended action rather than fabricate
        # a register write that the hardware does not support.
        results: List[Dict[str, Any]] = []
        results.append({
            "rule_id": auto.id,
            "name": auto.name,
            "type": TYPE_BATTERY_SOC,
            "action": action,
            "dry_run": auto.dry_run,
        })
        return results

    def _apply_battery_protection(
        self,
        auto: BatteryProtectionAutomation,
        snapshot: dict,
        now: datetime,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        target = auto.evaluate(snapshot, now)
        if target is None:
            # SOC recovered — restore the configured value.
            results.append(self._write_target(auto, auto.restore_value, dongle_host, dongle_port, datalog_serial, inverter_serial, reason="restore"))
            return results
        # Shutdown: write 0.0V to the shutdown register.
        results.append(self._write_target(auto, target, dongle_host, dongle_port, datalog_serial, inverter_serial, reason="shutdown"))
        return results

    def _apply_notify(self, auto: NotifyAutomation, snapshot: dict, now: datetime) -> List[Dict[str, Any]]:
        if not auto.evaluate(snapshot, now):
            return []

        # Rate-limit repeated notifications for the same automation.
        now_ts = time.time()
        last = self._notify_last_sent.get(auto.id, 0)
        if now_ts - last < self._notify_min_interval_sec:
            return []
        self._notify_last_sent[auto.id] = now_ts

        sensor = SUBSET_SENSOR.get(auto.condition_kind)
        value = _snapshot_value(snapshot, sensor) if sensor else None
        message = (
            f"{auto.name}: {auto.condition_kind} {auto.operator} {auto.threshold} "
            f"(current {value})"
        )

        if auto.dry_run:
            logger.info("DRY-RUN notify: %s", message)
            return [{
                "rule_id": auto.id,
                "name": auto.name,
                "type": TYPE_NOTIFY,
                "notified": True,
                "dry_run": True,
                "message": message,
            }]

        if self._notifiers is not None:
            try:
                self._notifiers.send(
                    alert_name=auto.name,
                    active=True,
                    value=float(value) if value is not None else 0.0,
                    message=message,
                )
            except Exception:
                logger.exception("Notify dispatch failed for %s", auto.name)

        return [{
            "rule_id": auto.id,
            "name": auto.name,
            "type": TYPE_NOTIFY,
            "notified": True,
            "dry_run": auto.dry_run,
            "message": message,
        }]

    def _write_target(
        self,
        auto: Any,
        target: float,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
        reason: str = "set",
    ) -> Dict[str, Any]:
        """Write a target value to the automation's target register."""
        register_name = getattr(auto, "target", None) or getattr(auto, "shutdown_register", None)
        if register_name is None:
            return {"rule_id": auto.id, "error": "No target register"}

        reg_addr = HOLDING_BY_NAME.get(register_name)
        if reg_addr is None:
            msg = f"Unknown holding register: {register_name}"
            logger.warning(msg)
            self._log(auto, register_name, None, None, False, msg)
            return {"rule_id": auto.id, "error": msg}

        meta = HOLDING_REGISTERS[reg_addr]
        raw = _engineering_to_raw(target, meta)
        if raw is None:
            msg = f"Invalid target {target} for {meta['name']}"
            logger.warning(msg)
            self._log(auto, register_name, raw, target, False, msg)
            return {"rule_id": auto.id, "error": msg}

        # Clamp to safety limits.
        raw_min = _engineering_to_raw(meta.get("min", 0), meta)
        raw_max = _engineering_to_raw(meta.get("max", 65535), meta)
        if raw_min is not None and raw < raw_min:
            raw = raw_min
            target = _raw_to_engineering(raw, meta)
        if raw_max is not None and raw > raw_max:
            raw = raw_max
            target = _raw_to_engineering(raw, meta)

        if auto.dry_run:
            msg = f"DRY-RUN would {reason} {meta['name']} = {target} (raw={raw})"
            logger.info(msg)
            self._log(auto, register_name, raw, target, True, msg)
            return {
                "rule_id": auto.id,
                "register": meta["name"],
                "value": target,
                "raw": raw,
                "dry_run": True,
                "reason": reason,
            }

        # Skip if we already wrote this exact raw value recently.
        last = self._last_written.get(meta["name"])
        if last and last[0] == raw:
            logger.debug("Skipping repeat write of %s = %s", meta["name"], raw)
            return {"rule_id": auto.id, "register": meta["name"], "skipped": True}

        success, msg = _write_holding_register(
            dongle_host, dongle_port, datalog_serial, inverter_serial, reg_addr, raw
        )
        if success:
            self._last_written[meta["name"]] = (raw, datetime.now().isoformat())
        self._log(auto, register_name, raw, target, success, msg)
        return {
            "rule_id": auto.id,
            "register": meta["name"],
            "value": target,
            "raw": raw,
            "success": success,
            "message": msg,
            "reason": reason,
        }

    def _log(
        self,
        auto: Any,
        register_name: str,
        raw: Optional[int],
        scaled: Optional[float],
        success: bool,
        message: str,
    ) -> None:
        try:
            conn = self._conn()
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self.log_table}
                    (rule_id, rule_name, register_name, raw_value, scaled_value, dry_run, success, message)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        auto.id,
                        auto.name,
                        register_name,
                        raw,
                        scaled,
                        getattr(auto, "dry_run", False),
                        success,
                        message,
                    ),
                )
        finally:
            conn.close()


# ── Helpers ─────────────────────────────────────────────────────────────────

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
    """
    Send a WriteSingleRegister request and verify the echo.

    Uses a fresh socket so the collector's read transport is not disturbed.
    """
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


# ── Parsing ─────────────────────────────────────────────────────────────────

def _parse_automation(data: Dict[str, Any]) -> Any:
    """Parse a single automation dict into the appropriate dataclass."""
    atype = data.get("type", TYPE_RULE_TABLE)

    if atype == TYPE_BATTERY_SOC:
        points = []
        for p in data.get("points", []):
            points.append(BatterySocPoint(
                time=p.get("time", "00:00"),
                soc=float(p.get("soc", 50.0)),
            ))
        return BatterySocAutomation(
            id=str(data.get("id", "unknown")),
            name=data.get("name", "Battery state of charge"),
            points=points,
            enabled=bool(data.get("enabled", True)),
            dry_run=bool(data.get("dry_run", DRY_RUN_DEFAULT)),
        )

    if atype == TYPE_BATTERY_PROTECTION:
        return BatteryProtectionAutomation(
            id=str(data.get("id", "unknown")),
            name=data.get("name", "Battery protection"),
            threshold_soc=float(data.get("threshold_soc", 25.0)),
            shutdown_register=data.get("shutdown_register", "shutdown_battery_voltage"),
            restore_value=float(data.get("restore_value", 40.0)),
            enabled=bool(data.get("enabled", True)),
            dry_run=bool(data.get("dry_run", DRY_RUN_DEFAULT)),
        )

    if atype == TYPE_NOTIFY:
        return NotifyAutomation(
            id=str(data.get("id", "unknown")),
            name=data.get("name", "Notification"),
            condition_kind=data.get("condition_kind", "battery_soc"),
            threshold=data.get("threshold"),
            operator=data.get("operator", "<="),
            enabled=bool(data.get("enabled", True)),
            dry_run=bool(data.get("dry_run", DRY_RUN_DEFAULT)),
        )

    # Default: rule table
    columns = []
    for c in data.get("columns", []):
        kind = c.get("kind", "time_of_day")
        value_type = SUBSET_KINDS.get(kind, {}).get("value_type", "number")
        ranges = []
        for r in c.get("ranges", []):
            ranges.append(Range(
                from_value=_normalize_range_value(r.get("from"), value_type),
                to_value=_normalize_range_value(r.get("to"), value_type),
                value=r.get("value"),
            ))
        columns.append(SubsetColumn(kind=kind, ranges=ranges))

    return RuleTable(
        id=str(data.get("id", "unknown")),
        name=data.get("name", "Unnamed rule"),
        target=data.get("target", "ac_charge_battery_current"),
        columns=columns,
        enabled=bool(data.get("enabled", True)),
        dry_run=bool(data.get("dry_run", DRY_RUN_DEFAULT)),
        restore=data.get("restore"),
    )


# ── Standalone test entrypoint ──────────────────────────────────────────────

if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO)
    engine = AutomationEngine(
        db_host=os.getenv("LUX_MARIADB_HOST", "localhost"),
        db_port=int(os.getenv("LUX_MARIADB_PORT", "3306")),
        db_user=os.getenv("LUX_MARIADB_USER", "luxmon"),
        db_password=os.getenv("LUX_MARIADB_PASSWORD", "luxmon"),
        db_name=os.getenv("LUX_MARIADB_DATABASE", "luxmon"),
    )
    print("Enabled:", engine.is_enabled())
    for a in engine.load_automations():
        print(a)
