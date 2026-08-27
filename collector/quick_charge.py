"""
lux-mon Quick Charge.

Implements the inverter's native quick-charge action using the correct
registers reverse-engineered from SolarAssistant's traffic:

  * `quick_charge_duration` (register 234 / 0x00EA) — the actual charge
    controller.  Setting it to N minutes starts charging for N minutes;
    setting it to 0 stops charging.
  * `quick_charge_enable` (register 233 / 0x00E9) — the on/off switch.

Correct semantics (confirmed via tcpdump of SolarAssistant):

  * The DURATION register is the charge controller.  `0` = "charge 0 minutes"
    = no charge / stop.
  * The SWITCH register only toggles the mode; it does NOT start charging on
    its own.  Enabling the switch with duration=0 does nothing.
  * To START: write duration (234) first, then enable switch (233=1).
  * To STOP:  write switch (233=0) AND clear duration (234=0).

This differs from the old implementation, which drove `ac_charge_battery_current`
(register 168) — that is the *grid charge current*, not the quick-charge toggle.

State is persisted in the `lux_settings` table so it survives collector
restarts and is visible to the REST API.  Writes reuse the automation engine's
safe write helpers (fresh socket, echo verification, clamping).

Safety design:
  * Only registers in protocol.HOLDING_REGISTERS are written.
  * Duration is clamped to 1..240 minutes (EG4 documented max).
  * A positive duration is REQUIRED before enabling — never enable with 0.
  * Stop clears BOTH the switch and the duration (no zero-duration hack).
  * All actions are logged to the automation log table.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import pymysql

from .protocol import HOLDING_REGISTERS, HOLDING_BY_NAME
from .automation import _write_holding_register, _engineering_to_raw

logger = logging.getLogger("luxmon.quick_charge")

# Setting keys (stored in lux_settings)
QC_STATE_KEY = "quick_charge_state"          # JSON: {"active": bool, "minutes": int, "deadline_ts": float, "started_at": str}
QC_MINUTES_KEY = "quick_charge_minutes"      # default duration (min)

# The registers we drive for quick charge.
QC_ENABLE_NAME = "quick_charge_enable"
QC_ENABLE_REG = HOLDING_BY_NAME[QC_ENABLE_NAME]
QC_DURATION_NAME = "quick_charge_duration"
QC_DURATION_REG = HOLDING_BY_NAME[QC_DURATION_NAME]

# Duration bounds (EG4 documented max is 240 minutes).
QC_MIN_MINUTES = 1
QC_MAX_MINUTES = 240


@dataclass
class QuickChargeState:
    active: bool = False
    minutes: int = 0
    deadline_ts: float = 0.0
    started_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "minutes": self.minutes,
            "deadline_ts": self.deadline_ts,
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "QuickChargeState":
        return cls(
            active=bool(d.get("active", False)),
            minutes=int(d.get("minutes", 0)),
            deadline_ts=float(d.get("deadline_ts", 0.0)),
            started_at=str(d.get("started_at", "")),
        )


class QuickChargeManager:
    """Manage the inverter's native quick-charge action."""

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
        minutes = self._get_setting(QC_MINUTES_KEY) or "60"
        return {
            **state.to_dict(),
            "default_minutes": int(minutes),
            "min_minutes": QC_MIN_MINUTES,
            "max_minutes": QC_MAX_MINUTES,
            "enable_register": QC_ENABLE_NAME,
            "enable_address": QC_ENABLE_REG,
            "duration_register": QC_DURATION_NAME,
            "duration_address": QC_DURATION_REG,
        }

    def start(
        self,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
        minutes: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Start a quick charge for `minutes` (default 60, range 1..240).

        Writes the duration register first, then enables the switch.  A
        positive duration is required — never enable with duration 0.
        """
        minutes = int(minutes) if minutes is not None else int(self._get_setting(QC_MINUTES_KEY) or "60")
        minutes = max(QC_MIN_MINUTES, min(QC_MAX_MINUTES, minutes))

        dur_meta = HOLDING_REGISTERS[QC_DURATION_REG]
        dur_raw = _engineering_to_raw(minutes, dur_meta)
        if dur_raw is None:
            return {"ok": False, "error": f"Invalid duration {minutes}"}

        if dry_run:
            self._log(QC_DURATION_NAME, dur_raw, minutes, True, f"DRY-RUN would set duration={minutes}min then enable quick charge")
            return {
                "ok": True,
                "dry_run": True,
                "minutes": minutes,
                "duration_raw": dur_raw,
            }

        # 1) Write duration first.
        ok, msg = _write_holding_register(
            dongle_host, dongle_port, datalog_serial, inverter_serial, QC_DURATION_REG, dur_raw
        )
        if not ok:
            self._log(QC_DURATION_NAME, dur_raw, minutes, False, msg)
            return {"ok": False, "error": f"Failed to set duration: {msg}"}
        self._log(QC_DURATION_NAME, dur_raw, minutes, True, f"Quick charge duration set to {minutes}min")

        # 2) Enable the switch.
        en_meta = HOLDING_REGISTERS[QC_ENABLE_REG]
        en_raw = _engineering_to_raw(1, en_meta)
        ok, msg = _write_holding_register(
            dongle_host, dongle_port, datalog_serial, inverter_serial, QC_ENABLE_REG, en_raw
        )
        if not ok:
            self._log(QC_ENABLE_NAME, en_raw, 1, False, msg)
            return {"ok": False, "error": f"Failed to enable quick charge: {msg}"}
        self._log(QC_ENABLE_NAME, en_raw, 1, True, "Quick charge enabled")

        deadline = time.time() + minutes * 60
        state = QuickChargeState(
            active=True,
            minutes=minutes,
            deadline_ts=deadline,
            started_at=datetime.now(ZoneInfo("UTC")).isoformat(),
        )
        self._save_state(state)
        return {
            "ok": True,
            "minutes": minutes,
            "duration_raw": dur_raw,
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
        """Stop an active quick charge: disable the switch AND clear duration.

        This is the correct cancel — it clears BOTH registers, so the inverter
        does not keep charging (unlike SolarAssistant's broken "stop" which
        only clears the switch).
        """
        state = self._load_state()

        if dry_run:
            self._log(QC_ENABLE_NAME, 0, 0, True, "DRY-RUN would disable quick charge and clear duration")
            return {"ok": True, "dry_run": True, "stopped": state.active}

        # 1) Disable the switch.
        en_meta = HOLDING_REGISTERS[QC_ENABLE_REG]
        en_raw = _engineering_to_raw(0, en_meta)
        ok, msg = _write_holding_register(
            dongle_host, dongle_port, datalog_serial, inverter_serial, QC_ENABLE_REG, en_raw
        )
        if not ok:
            self._log(QC_ENABLE_NAME, en_raw, 0, False, msg)
            return {"ok": False, "error": f"Failed to disable quick charge: {msg}"}
        self._log(QC_ENABLE_NAME, en_raw, 0, True, "Quick charge disabled")

        # 2) Clear the duration (this is what actually stops charging).
        dur_meta = HOLDING_REGISTERS[QC_DURATION_REG]
        dur_raw = _engineering_to_raw(0, dur_meta)
        ok, msg = _write_holding_register(
            dongle_host, dongle_port, datalog_serial, inverter_serial, QC_DURATION_REG, dur_raw
        )
        if not ok:
            self._log(QC_DURATION_NAME, dur_raw, 0, False, msg)
            return {"ok": False, "error": f"Failed to clear duration: {msg}"}
        self._log(QC_DURATION_NAME, dur_raw, 0, True, "Quick charge duration cleared")

        state.active = False
        self._save_state(state)
        return {"ok": True, "stopped": True}

    def tick(
        self,
        dongle_host: str,
        dongle_port: int,
        datalog_serial: str,
        inverter_serial: str,
    ) -> Optional[Dict[str, Any]]:
        """Called each writer loop.  If a quick charge is past its deadline,
        stop it (disable + clear duration) and clear the state.  Returns a
        result dict if a stop happened, else None."""
        state = self._load_state()
        if not state.active:
            return None
        if time.time() < state.deadline_ts:
            return None

        # Deadline passed — stop.
        result = self.stop(
            dongle_host, dongle_port, datalog_serial, inverter_serial, dry_run=False
        )
        if result.get("ok"):
            self._log(QC_DURATION_NAME, 0, 0, True, f"Quick charge expired after {state.minutes}min, stopped")
        return result
