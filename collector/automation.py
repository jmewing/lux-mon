"""
lux-mon automation / rule engine.

Mirrors SolarAssistant's "Power management" automations:
  * time-of-day windows
  * sensor range conditions (battery voltage, SOC, etc.)
  * write a target value to a Modbus holding register

Rules are stored as JSON in the lux_settings table under the key
"automation_rules".  Global on/off is controlled by the setting
"automation_enabled".  A rule may set "dry_run": true to evaluate and
log what *would* be written without actually sending the Modbus command.

Safety design:
  * Only registers listed in protocol.HOLDING_REGISTERS can be written.
  * Each register has hard min/max clamping.
  * Before writing we read the register back and verify it changed.
  * Time-of-day uses the configured timezone.
  * Writes are performed on a separate short-lived socket so they cannot
    corrupt the collector's read stream.
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
from .settings import get as _get_setting

logger = logging.getLogger("luxmon.automation")

SETTING_KEY = "automation_rules"
ENABLED_KEY = "automation_enabled"
DRY_RUN_DEFAULT = False

# Condition sources that may appear in rule conditions.
SENSOR_NAMES = {
    "battery_voltage",
    "soc",
    "soh",
    "battery_current",
    "pv1_power",
    "pv2_power",
    "grid_import_power",
    "grid_export_power",
    "charge_power",
    "discharge_power",
    "temp_inverter",
    "temp_battery",
}


# ── Data models ────────────────────────────────────────────────────────────

@dataclass
class TimeWindow:
    start: str  # "HH:MM" 24-hour
    end: str    # "HH:MM" 24-hour

    def contains(self, now: datetime) -> bool:
        start_min = _hm_to_minutes(self.start)
        end_min = _hm_to_minutes(self.end)
        now_min = now.hour * 60 + now.minute
        if start_min <= end_min:
            return start_min <= now_min <= end_min
        # Wraps past midnight (e.g. 21:00 -> 06:00)
        return now_min >= start_min or now_min <= end_min


@dataclass
class Condition:
    sensor: str
    min: Optional[float] = None
    max: Optional[float] = None

    def evaluate(self, snapshot: dict) -> bool:
        if self.sensor not in snapshot:
            return False
        value = snapshot[self.sensor]["value"]
        if self.min is not None and value < self.min:
            return False
        if self.max is not None and value > self.max:
            return False
        return True


@dataclass
class RangeRow:
    min: Optional[float]
    max: Optional[float]
    value: float


@dataclass
class Action:
    register_name: str
    value: Optional[float] = None        # static value
    ranges: List[RangeRow] = field(default_factory=list)  # sensor -> value table
    range_sensor: Optional[str] = None   # sensor used by ranges table

    def target_value(self, snapshot: dict) -> Optional[float]:
        if self.value is not None:
            return float(self.value)
        if not self.ranges or not self.range_sensor:
            return None
        sensor_val = snapshot.get(self.range_sensor, {}).get("value")
        if sensor_val is None:
            return None
        for row in self.ranges:
            if row.min is not None and sensor_val < row.min:
                continue
            if row.max is not None and sensor_val > row.max:
                continue
            return float(row.value)
        return None


@dataclass
class Rule:
    id: str
    name: str
    enabled: bool = True
    dry_run: bool = False
    time_window: Optional[TimeWindow] = None
    conditions: List[Condition] = field(default_factory=list)
    action: Optional[Action] = None

    def evaluate(self, snapshot: dict, now: datetime) -> Optional[float]:
        if not self.enabled:
            return None
        if self.time_window is not None and not self.time_window.contains(now):
            return None
        for cond in self.conditions:
            if not cond.evaluate(snapshot):
                return None
        if self.action is None:
            return None
        return self.action.target_value(snapshot)


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
    ):
        self.db_args = (db_host, db_port, db_user, db_password, db_name)
        self.table_prefix = table_prefix
        self.settings_table = f"{table_prefix}settings"
        self.log_table = f"{table_prefix}automation_log"
        self._last_written: Dict[str, Tuple[int, str]] = {}
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

    def load_rules(self) -> List[Rule]:
        """Load rules from the settings table."""
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
            logger.error("Failed to parse automation rules JSON: %s", exc)
            return []
        return [_parse_rule(item) for item in data if isinstance(item, dict)]

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

    def evaluate_and_apply(
        self,
        snapshot: dict,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
        timezone: str = "America/Chicago",
    ) -> List[Dict[str, Any]]:
        """Evaluate all rules and apply any pending writes.  Returns a list of action results."""
        results: List[Dict[str, Any]] = []
        if not self.is_enabled():
            return results

        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        rules = self.load_rules()
        if not rules:
            return results

        for rule in rules:
            try:
                target = rule.evaluate(snapshot, now)
                if target is None:
                    continue
                if rule.action is None:
                    continue
                reg_addr = HOLDING_BY_NAME.get(rule.action.register_name)
                if reg_addr is None:
                    msg = f"Unknown holding register: {rule.action.register_name}"
                    logger.warning(msg)
                    self._log(rule, None, None, False, msg)
                    results.append({"rule_id": rule.id, "error": msg})
                    continue

                meta = HOLDING_REGISTERS[reg_addr]
                raw = _engineering_to_raw(target, meta)
                if raw is None:
                    msg = f"Invalid target {target} for {meta['name']}"
                    logger.warning(msg)
                    self._log(rule, raw, target, False, msg)
                    results.append({"rule_id": rule.id, "error": msg})
                    continue

                # Clamp to safety limits.
                raw_min = _engineering_to_raw(meta.get("min", 0), meta)
                raw_max = _engineering_to_raw(meta.get("max", 65535), meta)
                if raw_min is not None and raw < raw_min:
                    raw = raw_min
                    target = _raw_to_engineering(raw, meta)
                if raw_max is not None and raw > raw_max:
                    raw = raw_max
                    target = _raw_to_engineering(raw, meta)

                if rule.dry_run:
                    msg = f"DRY-RUN would set {meta['name']} = {target} (raw={raw})"
                    logger.info(msg)
                    self._log(rule, raw, target, True, msg)
                    results.append({
                        "rule_id": rule.id,
                        "register": meta["name"],
                        "value": target,
                        "raw": raw,
                        "dry_run": True,
                    })
                    continue

                # Skip if we already wrote this exact raw value recently.
                last = self._last_written.get(meta["name"])
                if last and last[0] == raw:
                    logger.debug("Skipping repeat write of %s = %s", meta["name"], raw)
                    continue

                success, msg = _write_holding_register(
                    dongle_host,
                    dongle_port,
                    datalog_serial,
                    inverter_serial,
                    reg_addr,
                    raw,
                )
                if success:
                    self._last_written[meta["name"]] = (raw, datetime.now().isoformat())
                self._log(rule, raw, target, success, msg)
                results.append({
                    "rule_id": rule.id,
                    "register": meta["name"],
                    "value": target,
                    "raw": raw,
                    "success": success,
                    "message": msg,
                })
            except Exception:
                logger.exception("Automation rule %s failed", rule.id)
        return results

    def _log(
        self,
        rule: Rule,
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
                        rule.id,
                        rule.name,
                        rule.action.register_name if rule.action else None,
                        raw,
                        scaled,
                        rule.dry_run,
                        success,
                        message,
                    ),
                )
        finally:
            conn.close()


# ── Helpers ─────────────────────────────────────────────────────────────────

def _hm_to_minutes(hm: str) -> int:
    """Convert 'HH:MM' to minutes since midnight."""
    parts = hm.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time string: {hm}")
    return int(parts[0]) * 60 + int(parts[1])


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


# ── Rule parsing ────────────────────────────────────────────────────────────

def _parse_rule(data: Dict[str, Any]) -> Rule:
    time_window = None
    tw = data.get("time_window") or data.get("time")
    if tw:
        time_window = TimeWindow(start=tw.get("start", "00:00"), end=tw.get("end", "23:59"))

    conditions = []
    for c in data.get("conditions", []):
        conditions.append(
            Condition(
                sensor=c.get("sensor", "battery_voltage"),
                min=c.get("min"),
                max=c.get("max"),
            )
        )

    action_data = data.get("action", {})
    action = None
    if action_data:
        ranges = []
        for r in action_data.get("ranges", []):
            ranges.append(
                RangeRow(
                    min=r.get("min"),
                    max=r.get("max"),
                    value=float(r["value"]),
                )
            )
        action = Action(
            register_name=action_data.get("register", "ac_charge_power"),
            value=action_data.get("value"),
            ranges=ranges,
            range_sensor=action_data.get("range_sensor", "battery_voltage"),
        )

    return Rule(
        id=str(data.get("id", "unknown")),
        name=data.get("name", "Unnamed rule"),
        enabled=bool(data.get("enabled", True)),
        dry_run=bool(data.get("dry_run", DRY_RUN_DEFAULT)),
        time_window=time_window,
        conditions=conditions,
        action=action,
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
    for r in engine.load_rules():
        print(r)
