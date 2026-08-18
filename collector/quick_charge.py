"""
lux-mon Quick Charge / Generator Charge.

Implements the two one-shot inverter actions that SolarAssistant lacks but
the EG4 Monitor portal exposes:

  * **Quick Charge** — write `ac_charge_battery_current` (register 168) to a
    target current for a fixed number of minutes, then restore the prior value.
  * **Generator Charge** — write the generator charge current / enable path.

State is persisted in the `lux_settings` table so it survives collector
restarts and is visible to the REST API.  Writes reuse the automation engine's
safe write helpers (fresh socket, echo verification, clamping).

Safety design:
  * Only registers in protocol.HOLDING_REGISTERS are written.
  * Values are clamped to the register's documented min/max.
  * A restore value is captured before the first write and re-applied on expiry.
  * All actions are logged to the automation log table.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import pymysql

from .protocol import HOLDING_REGISTERS, HOLDING_BY_NAME
from .automation import _write_holding_register, _read_holding_register, _engineering_to_raw

logger = logging.getLogger("luxmon.quick_charge")

# Setting keys (stored in lux_settings)
QC_STATE_KEY = "quick_charge_state"          # JSON: {"active": bool, "amps": int, "deadline_ts": float, "restore_raw": int, "started_at": str}
QC_AMPS_KEY = "quick_charge_amps"            # default target current (A)
QC_MINUTES_KEY = "quick_charge_minutes"      # default duration (min)

# The register we drive for quick charge (AC charge battery current, amps).
QC_REGISTER_NAME = "ac_charge_battery_current"
QC_REGISTER = HOLDING_BY_NAME[QC_REGISTER_NAME]

# Generator charge register (max generator input power, W).
GEN_REGISTER_NAME = "max_generator_input_power"
GEN_REGISTER = HOLDING_BY_NAME[GEN_REGISTER_NAME]


@dataclass
class QuickChargeState:
    active: bool = False
    amps: int = 0
    deadline_ts: float = 0.0
    restore_raw: Optional[int] = None
    started_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "amps": self.amps,
            "deadline_ts": self.deadline_ts,
            "restore_raw": self.restore_raw,
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "QuickChargeState":
        return cls(
            active=bool(d.get("active", False)),
            amps=int(d.get("amps", 0)),
            deadline_ts=float(d.get("deadline_ts", 0.0)),
            restore_raw=d.get("restore_raw"),
            started_at=str(d.get("started_at", "")),
        )


class QuickChargeManager:
    """Manage one-shot quick-charge / generator-charge actions."""

    def __init__(
        self,
        db_host: str,
        db_port: int,
        db_user: str,
        db_password: str,
        db_name: str,
        table_prefix: str = "lux_",
    ):
        self._db_args = (db_host, db_port, db_user, db_password, db_name)
        self.settings_table = f"{table_prefix}settings"
        self.log_table = f"{table_prefix}automation_log"

    def _conn(self):
        return pymysql.connect(
            host=self._db_args[0],
            port=self._db_args[1],
            user=self._db_args[2],
            password=self._db_args[3],
            database=self._db_args[4],
            autocommit=True,
        )

    # ── settings helpers ──────────────────────────────────────────────

    def _get_setting(self, name: str) -> Optional[str]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT value FROM {self.settings_table} WHERE name = %s", (name,))
                row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _set_setting(self, name: str, value: str) -> None:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self.settings_table} (name, value) VALUES (%s, %s) "
                    f"ON DUPLICATE KEY UPDATE value = VALUES(value)",
                    (name, value),
                )
        finally:
            conn.close()

    def _load_state(self) -> QuickChargeState:
        raw = self._get_setting(QC_STATE_KEY)
        if not raw:
            return QuickChargeState()
        try:
            return QuickChargeState.from_dict(json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            return QuickChargeState()

    def _save_state(self, state: QuickChargeState) -> None:
        self._set_setting(QC_STATE_KEY, json.dumps(state.to_dict()))

    def _log(self, register_name: str, raw: Optional[int], scaled: Optional[float], success: bool, message: str) -> None:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self.log_table}
                    (rule_id, rule_name, register_name, raw_value, scaled_value, dry_run, success, message)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    ("quick_charge", "Quick Charge", register_name, raw, scaled, 0, success, message),
                )
        finally:
            conn.close()

    # ── public API ────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """Return the current quick-charge state plus defaults."""
        state = self._load_state()
        amps = self._get_setting(QC_AMPS_KEY) or "85"
        minutes = self._get_setting(QC_MINUTES_KEY) or "60"
        return {
            **state.to_dict(),
            "default_amps": int(amps),
            "default_minutes": int(minutes),
            "register": QC_REGISTER_NAME,
            "register_address": QC_REGISTER,
        }

    def start(
        self,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
        amps: Optional[int] = None,
        minutes: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Start a quick charge: write AC charge current to `amps` for `minutes`.

        Captures the current register value first so it can be restored on expiry.
        """
        amps = int(amps) if amps is not None else int(self._get_setting(QC_AMPS_KEY) or "85")
        minutes = int(minutes) if minutes is not None else int(self._get_setting(QC_MINUTES_KEY) or "60")

        meta = HOLDING_REGISTERS[QC_REGISTER]
        raw = _engineering_to_raw(amps, meta)
        if raw is None:
            return {"ok": False, "error": f"Invalid amps value {amps}"}
        # Clamp
        raw_min = _engineering_to_raw(meta.get("min", 0), meta)
        raw_max = _engineering_to_raw(meta.get("max", 65535), meta)
        if raw_min is not None and raw < raw_min:
            raw = raw_min
        if raw_max is not None and raw > raw_max:
            raw = raw_max

        # Capture current value for restore (best-effort).
        restore_raw: Optional[int] = None
        if datalog_serial and inverter_serial:
            ok, cur_raw, _ = _read_holding_register(
                dongle_host, dongle_port, datalog_serial, inverter_serial, QC_REGISTER
            )
            if ok and cur_raw is not None:
                restore_raw = cur_raw

        if dry_run:
            self._log(QC_REGISTER_NAME, raw, amps, True, f"DRY-RUN would set {QC_REGISTER_NAME} = {amps}A for {minutes}min (restore={restore_raw})")
            return {
                "ok": True,
                "dry_run": True,
                "amps": amps,
                "minutes": minutes,
                "raw": raw,
                "restore_raw": restore_raw,
            }

        success, msg = _write_holding_register(
            dongle_host, dongle_port, datalog_serial, inverter_serial, QC_REGISTER, raw
        )
        if not success:
            self._log(QC_REGISTER_NAME, raw, amps, False, msg)
            return {"ok": False, "error": msg}

        deadline = time.time() + minutes * 60
        state = QuickChargeState(
            active=True,
            amps=amps,
            deadline_ts=deadline,
            restore_raw=restore_raw,
            started_at=datetime.now(ZoneInfo("UTC")).isoformat(),
        )
        self._save_state(state)
        self._log(QC_REGISTER_NAME, raw, amps, True, f"Quick charge started: {amps}A for {minutes}min (restore={restore_raw})")
        return {
            "ok": True,
            "amps": amps,
            "minutes": minutes,
            "raw": raw,
            "restore_raw": restore_raw,
            "deadline_ts": deadline,
        }

    def stop(
        self,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Stop an active quick charge immediately, restoring the prior value."""
        state = self._load_state()
        if not state.active:
            return {"ok": True, "stopped": False, "message": "No active quick charge"}

        restore_raw = state.restore_raw if state.restore_raw is not None else 0
        meta = HOLDING_REGISTERS[QC_REGISTER]
        restore_amps = restore_raw * meta.get("scale", 1.0)

        if dry_run:
            self._log(QC_REGISTER_NAME, restore_raw, restore_amps, True, f"DRY-RUN would restore {QC_REGISTER_NAME} = {restore_amps}A")
            return {"ok": True, "dry_run": True, "restore_raw": restore_raw}

        success, msg = _write_holding_register(
            dongle_host, dongle_port, datalog_serial, inverter_serial, QC_REGISTER, restore_raw
        )
        if not success:
            self._log(QC_REGISTER_NAME, restore_raw, restore_amps, False, msg)
            return {"ok": False, "error": msg}

        state.active = False
        self._save_state(state)
        self._log(QC_REGISTER_NAME, restore_raw, restore_amps, True, f"Quick charge stopped, restored {QC_REGISTER_NAME} = {restore_amps}A")
        return {"ok": True, "stopped": True, "restore_raw": restore_raw}

    def tick(
        self,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
    ) -> Optional[Dict[str, Any]]:
        """Called each writer loop.  If a quick charge is past its deadline,
        restore the prior value and clear the state.  Returns a result dict
        if a restore happened, else None."""
        state = self._load_state()
        if not state.active:
            return None
        if time.time() < state.deadline_ts:
            return None

        # Deadline passed — restore.
        restore_raw = state.restore_raw if state.restore_raw is not None else 0
        meta = HOLDING_REGISTERS[QC_REGISTER]
        restore_amps = restore_raw * meta.get("scale", 1.0)

        success, msg = _write_holding_register(
            dongle_host, dongle_port, datalog_serial, inverter_serial, QC_REGISTER, restore_raw
        )
        state.active = False
        self._save_state(state)
        self._log(QC_REGISTER_NAME, restore_raw, restore_amps, success, f"Quick charge expired, restored {QC_REGISTER_NAME} = {restore_amps}A: {msg}")
        return {"ok": success, "restored": True, "restore_raw": restore_raw, "message": msg}
