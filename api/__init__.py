"""lux-mon REST API — FastAPI app serving inverter telemetry from MariaDB."""

from __future__ import annotations

import glob
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

import asyncio
import math

import pymysql
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Any
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.staticfiles import StaticFiles as StarletteStaticFiles
from starlette.types import Scope, Receive, Send

class CacheStaticFiles(StarletteStaticFiles):
    """StaticFiles variant that adds long-term cache headers to immutable assets."""

    IMMUTABLE = {".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".woff2", ".woff", ".ttf", ".ico"}

    async def get_response(self, path: str, scope: Scope) -> Any:
        response = await super().get_response(path, scope)
        ext = os.path.splitext(path)[1].lower()
        if ext in self.IMMUTABLE:
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        return response

TZ = ZoneInfo("America/Chicago")

logger = logging.getLogger("luxmon.api")

app = FastAPI(title="lux-mon API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)

# ── database ────────────────────────────────────────────────────────────────

# Support both legacy LUX_DB_* names and the LUX_MARIADB_* names used by the
# collector / Docker Compose stack.
DB_CONFIG = {
    "host": os.getenv("LUX_DB_HOST") or os.getenv("LUX_MARIADB_HOST", "localhost"),
    "user": os.getenv("LUX_DB_USER") or os.getenv("LUX_MARIADB_USER", "luxmon"),
    "password": os.getenv("LUX_DB_PASSWORD") or os.getenv("LUX_MARIADB_PASSWORD", "luxmon"),
    "database": os.getenv("LUX_DB_NAME") or os.getenv("LUX_MARIADB_DATABASE", "luxmon"),
    "port": int(os.getenv("LUX_DB_PORT") or os.getenv("LUX_MARIADB_PORT", "3306")),
    "charset": "utf8mb4",
}


def _get_conn() -> pymysql.Connection:
    conn = pymysql.connect(**DB_CONFIG)
    conn.autocommit(True)
    return conn


# ── helpers ─────────────────────────────────────────────────────────────────

def _row_to_dict(row: tuple, columns: list[str]) -> dict[str, Any]:
    return dict(zip(columns, row))


# Working-mode status codes (Register 0) → human-readable label.
# Source: EG4-18KPV-12LV Modbus protocol, Table 9 "Working modes definition".
_STATE_LABELS = {
    0x00: "Standby",
    0x01: "Fault",
    0x02: "Programming",
    0x04: "PV on-grid",
    0x08: "PV charging battery",
    0x0C: "PV charging battery + on-grid",
    0x10: "Battery on-grid",
    0x14: "PV + battery on-grid",
    0x20: "AC charge (grid → battery)",
    0x28: "PV + AC charge",
    0x40: "Battery off-grid",
    0x80: "PV off-grid",
    0x88: "PV charge + off-grid",
    0xC0: "PV + battery off-grid",
}


def _state_label(value) -> str:
    """Return a human-readable label for a raw working-mode state value."""
    try:
        code = int(value)
    except (TypeError, ValueError):
        return str(value)
    label = _STATE_LABELS.get(code)
    if label is None:
        return f"Unknown ({code})"
    return f"{label} ({code})"


# ── endpoints ────────────────────────────────────────────────────────────────

@app.get("/api/status")
def api_status():
    """Return the latest inverter snapshot with all decoded registers."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ts FROM lux_snapshots ORDER BY id DESC LIMIT 1"
            )
            snap = cur.fetchone()
            if not snap:
                raise HTTPException(404, "No snapshots found")

            snap_id, snap_ts = snap
            # Attach timezone to naive DB timestamp
            ts_aware = snap_ts.replace(tzinfo=TZ) if snap_ts.tzinfo is None else snap_ts

            cur.execute(
                "SELECT name, value, unit FROM lux_registers "
                "WHERE snapshot_id = %s ORDER BY name",
                (snap_id,),
            )
            registers = {
                row[0]: {"value": float(row[1]), "unit": row[2]}
                for row in cur.fetchall()
            }

        # Human-readable working-mode label alongside the raw state code.
        if "state" in registers:
            registers["state_label"] = {
                "value": _state_label(registers["state"]["value"]),
                "unit": "",
            }

        return {
            "snapshot_id": snap_id,
            "timestamp": ts_aware.isoformat(),
            "registers": registers,
        }
    finally:
        conn.close()


@app.get("/api/history")
def api_history(
    minutes: int = Query(60, ge=1, le=1440, description="Time window in minutes"),
    fields: str = Query(
        "",
        description="Comma-separated register names (empty = all)",
    ),
    max_points: int = Query(
        500,
        ge=10,
        le=5000,
        description="Maximum number of snapshot points to return; larger windows are downsampled",
    ),
):
    """Return time-series data for the last N minutes.

    Returns a list of {timestamp, registers: {name: value}} objects.
    If *fields* is provided, only those registers are included.
    Larger windows are automatically downsampled to keep responses fast.
    """
    since = datetime.now(TZ) - timedelta(minutes=minutes)
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ts FROM lux_snapshots "
                "WHERE ts >= %s ORDER BY ts ASC",
                (since,),
            )
            snapshots = cur.fetchall()
            if not snapshots:
                return {"snapshots": [], "count": 0}

            # Downsample large windows to avoid multi-second responses on a Pi.
            if len(snapshots) > max_points:
                step = math.ceil(len(snapshots) / max_points)
                snapshots = snapshots[::step]

            snap_ids = [s[0] for s in snapshots]
            placeholders = ",".join(["%s"] * len(snap_ids))

            field_filter = ""
            params: list[Any] = list(snap_ids)
            if fields:
                field_names = [f.strip() for f in fields.split(",") if f.strip()]
                field_filter = " AND name IN (" + ",".join(["%s"] * len(field_names)) + ")"
                params.extend(field_names)

            cur.execute(
                f"SELECT snapshot_id, name, value FROM lux_registers "
                f"WHERE snapshot_id IN ({placeholders}){field_filter} "
                f"ORDER BY snapshot_id, name",
                params,
            )
            rows = cur.fetchall()

        # Build time-series
        snap_map: dict[int, dict[str, Any]] = {
            s[0]: {
                "timestamp": (
                    s[1].replace(tzinfo=TZ) if s[1].tzinfo is None else s[1]
                ).isoformat(),
                "registers": {},
            }
            for s in snapshots
        }
        for snap_id, name, value in rows:
            snap_map[snap_id]["registers"][name] = float(value)

        return {
            "snapshots": list(snap_map.values()),
            "count": len(snapshots),
        }
    finally:
        conn.close()


@app.get("/api/health")
def api_health():
    """Health check."""
    return {"status": "ok"}


@app.get("/api/alerts")
def api_alerts(limit: int = Query(50, ge=1, le=500, description="Number of recent alert events")):
    """Return recent alert events from MariaDB."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ts, alert_name, state, value, message FROM lux_alerts "
                "ORDER BY ts DESC LIMIT %s",
                (limit,),
            )
            columns = ["id", "timestamp", "alert_name", "state", "value", "message"]
            rows = cur.fetchall()
        return {"alerts": [_row_to_dict(row, columns) for row in rows], "count": len(rows)}
    finally:
        conn.close()


@app.get("/api/summary")
def api_summary():
    """Compact summary for dashboards — key metrics only."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ts FROM lux_snapshots ORDER BY id DESC LIMIT 1"
            )
            snap = cur.fetchone()
            if not snap:
                raise HTTPException(404, "No snapshots found")

            snap_id, snap_ts = snap
            ts_aware = snap_ts.replace(tzinfo=TZ) if snap_ts.tzinfo is None else snap_ts

            # Key metrics for dashboard
            keys = [
                "soc", "soh", "battery_voltage", "battery_current",
                "battery_capacity",
                "charge_power", "discharge_power",
                "charge_energy_today", "discharge_energy_today",
                "pv1_power", "pv2_power", "pv1_energy_today", "pv2_energy_today",
                "grid_import_power", "grid_export_power",
                "grid_import_today", "grid_export_today",
                "grid_voltage_r", "grid_frequency",
                "eps_power", "eps_energy_today",
                "temp_inverter", "temp_battery", "temp_radiator_1", "temp_radiator_2",
                "cell_voltage_min", "cell_voltage_max",
                "cell_temp_min", "cell_temp_max",
                "runtime", "cycle_count",
                "state", "fault_code", "warning_code",
                "bms_status_0", "bms_status_1", "bms_status_2",
                "bms_status_3", "bms_status_4", "bms_status_5",
                "bms_status_6", "bms_status_7", "bms_status_8", "bms_status_9",
                "bms_charge_voltage_ref", "bms_discharge_cut_voltage",
                "bms_max_charge_current", "bms_max_discharge_current",
                "bms_fault_code", "bms_warning_code",
                "eps_voltage_r", "eps_voltage_s", "eps_voltage_t",
            ]
            placeholders = ",".join(["%s"] * len(keys))
            cur.execute(
                f"SELECT name, value, unit FROM lux_registers "
                f"WHERE snapshot_id = %s AND name IN ({placeholders})",
                [snap_id] + keys,
            )
            registers = {
                row[0]: {"value": float(row[1]), "unit": row[2]}
                for row in cur.fetchall()
            }
            # Compute derived AC output voltage from EPS phase R
            eps_r = registers.get("eps_voltage_r", {}).get("value")
            if eps_r is not None:
                registers["ac_output_voltage"] = {
                    "value": round(eps_r, 1),
                    "unit": "V"
                }
            # Convert temperatures for display if configured in Fahrenheit
            cur.execute("SELECT value FROM lux_settings WHERE name = 'temperature_unit'")
            row = cur.fetchone()
            temp_unit = row[0] if row else "celsius"
            if temp_unit == "fahrenheit":
                for tkey in ("temp_inverter", "temp_battery", "temp_radiator_1", "temp_radiator_2"):
                    if tkey in registers:
                        c = registers[tkey]["value"]
                        registers[tkey] = {
                            "value": round(c * 9.0 / 5.0 + 32.0, 1),
                            "unit": "°F",
                        }

        # Human-readable working-mode label alongside the raw state code.
        if "state" in registers:
            registers["state_label"] = {
                "value": _state_label(registers["state"]["value"]),
                "unit": "",
            }

        return {
            "snapshot_id": snap_id,
            "timestamp": ts_aware.isoformat(),
            "registers": registers,
        }
    finally:
        conn.close()


# ── energy totals ───────────────────────────────────────────────────────────

@app.get("/api/energy")
def api_energy(
    range_: str = Query("daily", alias="range", regex="^(daily|weekly|monthly)$"),
    periods: int = Query(30, ge=1, le=365, description="Number of periods to return"),
):
    """Return daily/weekly/monthly energy totals from MariaDB hourly rollups.

    Aggregates `lux_hourly_energy` rows into kWh values. All requested
    periods are returned, filling missing periods and categories with 0 kWh.
    """
    from datetime import date, timedelta
    from collections import defaultdict

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            if range_ == "daily":
                cur.execute(
                    """
                    SELECT DATE(hour) AS period, name,
                           ROUND(SUM(value_in) / 1000, 3) AS in_kwh,
                           ROUND(SUM(value_out) / 1000, 3) AS out_kwh
                    FROM lux_hourly_energy
                    WHERE hour >= DATE(NOW()) - INTERVAL %s DAY
                    GROUP BY period, name
                    ORDER BY period ASC
                    """,
                    (periods,),
                )
            elif range_ == "weekly":
                cur.execute(
                    """
                    SELECT CONCAT(YEAR(hour), '-W', LPAD(WEEK(hour, 1), 2, '0')) AS period,
                           name,
                           ROUND(SUM(value_in) / 1000, 3) AS in_kwh,
                           ROUND(SUM(value_out) / 1000, 3) AS out_kwh
                    FROM lux_hourly_energy
                    WHERE hour >= DATE(NOW()) - INTERVAL %s WEEK
                    GROUP BY period, name
                    ORDER BY period ASC
                    """,
                    (periods,),
                )
            else:  # monthly
                cur.execute(
                    """
                    SELECT DATE_FORMAT(hour, '%%Y-%%m') AS period,
                           name,
                           ROUND(SUM(value_in) / 1000, 3) AS in_kwh,
                           ROUND(SUM(value_out) / 1000, 3) AS out_kwh
                    FROM lux_hourly_energy
                    WHERE hour >= DATE(NOW()) - INTERVAL %s MONTH
                    GROUP BY period, name
                    ORDER BY period ASC
                    """,
                    (periods,),
                )
            rows = cur.fetchall()

        # Pivot to {period_str: {name: {in_kwh, out_kwh}}}
        data: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
        for period, name, in_kwh, out_kwh in rows:
            period_str = str(period)
            data[period_str][name] = {
                "in_kwh": float(in_kwh or 0),
                "out_kwh": float(out_kwh or 0),
            }

        # Generate all requested periods and fill missing categories with zeroes.
        known_categories = {"PV power", "Battery power", "Grid power", "Load power"}
        today = date.today()
        full_data: dict[str, dict[str, dict[str, float]]] = {}
        for i in range(periods - 1, -1, -1):
            if range_ == "daily":
                period = (today - timedelta(days=i)).isoformat()
            elif range_ == "weekly":
                d = today - timedelta(weeks=i)
                period = f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"
            else:
                d = today - timedelta(days=i * 30)
                period = d.strftime("%Y-%m")
            existing = data.get(period, {})
            full_data[period] = {
                cat: existing.get(cat, {"in_kwh": 0.0, "out_kwh": 0.0})
                for cat in known_categories
            }

        return {
            "range": range_,
            "periods": periods,
            "unit": "kWh",
            "data": full_data,
            "count": len(full_data),
        }
    finally:
        conn.close()



# ── settings ────────────────────────────────────────────────────────────────

class SettingUpdate(BaseModel):
    value: Any


@app.get("/api/settings")
def api_settings():
    """Return all settings (effective value: env > DB > default) with metadata."""
    from collector.settings import get_all, seed_defaults, SETTING_META, effective_value, SETTING_ENV

    conn = _get_conn()
    try:
        seed_defaults(conn)
        settings = get_all(conn)
        # Attach metadata for each setting, resolving the effective value so the
        # page reflects what is actually running (env overrides DB).
        enriched = {}
        for key, value in settings.items():
            meta = SETTING_META.get(key, {})
            effective = effective_value(key, value)
            enriched[key] = {
                "value": effective,
                "db_value": value,
                "source": "env" if (key in SETTING_ENV and os.environ.get(SETTING_ENV[key][0]) not in (None, "")) else ("db" if value not in (None, "") else "default"),
                "label": meta.get("label", key),
                "type": meta.get("type", "text"),
                "section": meta.get("section", "general"),
                "options": meta.get("options"),
                "hint": meta.get("hint", ""),
                "min": meta.get("min"),
                "max": meta.get("max"),
                "step": meta.get("step"),
            }
        return {"settings": enriched}
    finally:
        conn.close()


@app.get("/api/settings/{name}")
def api_setting_get(name: str):
    """Get a single setting's effective value (env > DB > default)."""
    from collector.settings import get, effective_value, DEFAULTS

    conn = _get_conn()
    try:
        value = get(conn, name)
        if value is None and name not in DEFAULTS:
            raise HTTPException(404, f"Setting '{name}' not found")
        effective = effective_value(name, value)
        return {"name": name, "value": effective}
    finally:
        conn.close()


@app.put("/api/settings/{name}")
def api_setting_put(name: str, body: SettingUpdate):
    """Update a setting value (DB + .env mirror)."""
    from collector.settings import set_, SETTING_ENV

    conn = _get_conn()
    try:
        str_value = str(body.value) if body.value is not None else ""
        set_(conn, name, str_value)
        _sync_env_file(name, str_value)
        return {"name": name, "value": str_value, "updated": True}
    finally:
        conn.close()


def _sync_env_file(name: str, value: str) -> None:
    """Mirror a setting change into the .env file (bootstrap mirror).

    The .env file is only a bootstrap/fallback for fresh installs; the DB is
    authoritative at runtime. Keeping it in sync means a future
    `docker compose down`/`up` reproduces the same configuration.
    """
    from collector.settings import SETTING_ENV

    if name not in SETTING_ENV:
        return
    env_var, _cast = SETTING_ENV[name]
    env_path = Path(os.environ.get("LUX_ENV_FILE", "/srv/lux-stack/.env"))
    if not env_path.exists():
        logger.warning("Env file %s not found; skipping .env sync", env_path)
        return

    try:
        lines = env_path.read_text().splitlines()
        new_lines = []
        found = False
        for line in lines:
            if line.startswith(env_var + "="):
                new_lines.append(f"{env_var}={value}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{env_var}={value}")
        env_path.write_text("\n".join(new_lines) + "\n")
        logger.info("Synced %s=%s to %s", env_var, value, env_path)
    except Exception:
        logger.exception("Failed to sync %s to .env", env_var)


# ── backup / prune / storage ────────────────────────────────────────────────

BACKUP_DIR = Path(os.environ.get("LUX_BACKUP_DIR", "/var/backups/lux-mon"))
SRC_DIR = Path(__file__).parent.parent


@app.post("/api/backup")
def api_backup():
    """Trigger a MariaDB + .env backup and return the archive path."""
    script = SRC_DIR / "scripts" / "backup.sh"
    if not script.exists():
        raise HTTPException(status_code=500, detail="backup.sh not found")
    try:
        result = subprocess.run(
            ["/bin/bash", str(script)],
            capture_output=True,
            text=True,
            check=True,
        )
        path = result.stdout.strip().splitlines()[-1]
        return {"backup_path": path, "ok": True}
    except subprocess.CalledProcessError as exc:
        logger.exception("Backup failed")
        raise HTTPException(status_code=500, detail=exc.stderr or "backup failed")


@app.get("/api/backups")
def api_backups():
    """List available backup archives."""
    files = sorted(glob.glob(str(BACKUP_DIR / "luxmon-backup-*.tar.gz")), reverse=True)
    return {
        "backups": [
            {
                "path": f,
                "name": Path(f).name,
                "size_bytes": Path(f).stat().st_size,
                "mtime": Path(f).stat().st_mtime,
            }
            for f in files
            if Path(f).is_file()
        ]
    }


@app.post("/api/prune")
def api_prune():
    """Trigger retention pruning of old detail data."""
    script = SRC_DIR / "scripts" / "prune.sh"
    if not script.exists():
        raise HTTPException(status_code=500, detail="prune.sh not found")
    try:
        result = subprocess.run(
            ["/bin/bash", str(script)],
            capture_output=True,
            text=True,
            check=True,
        )
        return {"ok": True, "output": result.stdout.strip()}
    except subprocess.CalledProcessError as exc:
        logger.exception("Prune failed")
        raise HTTPException(status_code=500, detail=exc.stderr or "prune failed")


@app.get("/api/storage")
def api_storage():
    """Show storage usage for lux-mon data."""
    db_sizes = {}
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name,
                       ROUND(data_length + index_length, 0) AS bytes
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND table_name LIKE 'lux_%'
            """)
            for row in cur.fetchall():
                db_sizes[row[0]] = int(row[1])
    finally:
        conn.close()

    disk = shutil.disk_usage(BACKUP_DIR) if BACKUP_DIR.exists() else shutil.disk_usage("/")
    backup_total = sum(
        Path(f).stat().st_size for f in glob.glob(str(BACKUP_DIR / "luxmon-backup-*.tar.gz"))
        if Path(f).is_file()
    )

    return {
        "database_bytes": db_sizes,
        "database_total_bytes": sum(db_sizes.values()),
        "backup_dir": str(BACKUP_DIR),
        "backup_total_bytes": backup_total,
        "disk_total_bytes": disk.total,
        "disk_used_bytes": disk.used,
        "disk_free_bytes": disk.free,
    }


# ── websocket live feed ─────────────────────────────────────────────────────

_active_ws_clients: set = set()


def _fetch_latest_snapshot() -> Optional[dict]:
    """Read the most recent snapshot from MariaDB."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, ts FROM lux_snapshots ORDER BY id DESC LIMIT 1")
            snap = cur.fetchone()
            if not snap:
                return None
            snap_id, snap_ts = snap
            ts_aware = snap_ts.replace(tzinfo=TZ) if snap_ts.tzinfo is None else snap_ts
            cur.execute(
                "SELECT name, value, unit FROM lux_registers "
                "WHERE snapshot_id = %s ORDER BY name",
                (snap_id,),
            )
            registers = {
                row[0]: {"value": float(row[1]), "unit": row[2]}
                for row in cur.fetchall()
            }
            return {
                "snapshot_id": snap_id,
                "timestamp": ts_aware.isoformat(),
                "registers": registers,
            }
    except Exception:
        return None
    finally:
        conn.close()


async def _ws_broadcaster_task() -> None:
    """Background task: push the latest snapshot to every connected WS client."""
    last_id: Optional[int] = None
    while True:
        await asyncio.sleep(1.0)
        if not _active_ws_clients:
            last_id = None
            continue
        try:
            snap = await asyncio.to_thread(_fetch_latest_snapshot)
        except Exception:
            snap = None
        if not snap:
            continue
        # Only send when a new snapshot arrives.
        if snap["snapshot_id"] == last_id:
            continue
        last_id = snap["snapshot_id"]
        payload = {"type": "snapshot", "data": snap}
        disconnected = set()
        for ws in _active_ws_clients:
            try:
                await ws.send_json(payload)
            except Exception:
                disconnected.add(ws)
        for ws in disconnected:
            _active_ws_clients.discard(ws)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Stream live snapshots as JSON messages."""
    await websocket.accept()
    _active_ws_clients.add(websocket)
    # Send current snapshot immediately so the client has data right away.
    snap = _fetch_latest_snapshot()
    if snap:
        await websocket.send_json({"type": "snapshot", "data": snap})
    try:
        while True:
            # Keep connection alive; clients can send ping/keepalive text.
            message = await websocket.receive_text()
            if message in ("ping", "keepalive"):
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        _active_ws_clients.discard(websocket)


@app.on_event("startup")
async def _start_ws_broadcaster() -> None:
    asyncio.create_task(_ws_broadcaster_task())


def _load_db_setting(name: str) -> Optional[str]:
    """Load a single setting value from MariaDB, or None if unavailable."""
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM lux_settings WHERE name = %s", (name,))
            row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        conn.close()


# ── automation / rules engine ───────────────────────────────────────────────

from collector.protocol import HOLDING_REGISTERS, HOLDING_BY_NAME
from collector.automation import AutomationEngine, SETTING_KEY as AUTOMATION_SETTING, ENABLED_KEY as AUTOMATION_ENABLED


class AutomationRuleBody(BaseModel):
    id: Optional[str] = None
    name: str
    enabled: bool = True
    dry_run: bool = False
    time_window: Optional[dict] = None
    conditions: List[dict] = []
    action: dict


class AutomationTestBody(BaseModel):
    snapshot: Optional[dict] = None
    timezone: Optional[str] = None


def _load_automation_engine() -> AutomationEngine:
    return AutomationEngine(
        db_host=DB_CONFIG["host"],
        db_port=DB_CONFIG["port"],
        db_user=DB_CONFIG["user"],
        db_password=DB_CONFIG["password"],
        db_name=DB_CONFIG["database"],
    )


@app.get("/api/automation/registers")
def api_automation_registers():
    """Return the list of writable holding registers for automation actions."""
    return {
        "registers": [
            {
                "name": info["name"],
                "address": reg,
                "unit": info["unit"],
                "scale": info["scale"],
                "min": info.get("min"),
                "max": info.get("max"),
                "desc": info["desc"],
            }
            for reg, info in sorted(HOLDING_REGISTERS.items())
        ]
    }


@app.get("/api/automation/rules")
def api_automation_rules():
    """Return the current automation rules and global enable flag."""
    engine = _load_automation_engine()
    from collector.settings import get

    conn = _get_conn()
    try:
        enabled_raw = get(conn, AUTOMATION_ENABLED)
        rules_raw = get(conn, AUTOMATION_SETTING)
    finally:
        conn.close()

    return {
        "enabled": str(enabled_raw).lower() in ("true", "1", "yes", "on") if enabled_raw else False,
        "rules": json.loads(rules_raw or "[]"),
    }


@app.post("/api/automation/rules")
def api_automation_rules_save(body: List[dict]):
    """Replace the entire automation rule set."""
    from collector.settings import set_

    conn = _get_conn()
    try:
        set_(conn, AUTOMATION_SETTING, json.dumps(body))
        return {"saved": True, "count": len(body)}
    finally:
        conn.close()


@app.delete("/api/automation/rules/{rule_id}")
def api_automation_rule_delete(rule_id: str):
    """Delete a single automation rule by id."""
    from collector.settings import get, set_

    conn = _get_conn()
    try:
        rules_raw = get(conn, AUTOMATION_SETTING) or "[]"
        rules = json.loads(rules_raw)
        new_rules = [r for r in rules if str(r.get("id")) != rule_id]
        set_(conn, AUTOMATION_SETTING, json.dumps(new_rules))
        return {"deleted": True, "removed": len(rules) - len(new_rules)}
    finally:
        conn.close()


@app.post("/api/automation/enable")
def api_automation_enable(enabled: bool = True):
    """Enable or disable the automation engine globally."""
    from collector.settings import set_

    conn = _get_conn()
    try:
        set_(conn, AUTOMATION_ENABLED, "true" if enabled else "false")
        return {"enabled": enabled}
    finally:
        conn.close()


@app.post("/api/automation/test")
def api_automation_test(body: AutomationTestBody):
    """Dry-run evaluate rules against a snapshot (or the latest DB snapshot)."""
    engine = _load_automation_engine()

    snapshot = body.snapshot
    if snapshot is None:
        snap = _fetch_latest_snapshot()
        if snap is None:
            raise HTTPException(404, "No snapshot available")
        snapshot = snap["registers"]

    tz = body.timezone or _load_db_setting("timezone") or "America/Chicago"

    # Force every rule into dry_run mode for this test.
    rules = engine.load_rules()
    results = []
    from zoneinfo import ZoneInfo
    from datetime import datetime

    now = datetime.now(ZoneInfo(tz))
    for rule in rules:
        rule.dry_run = True
        target = rule.evaluate(snapshot, now)
        if target is None:
            results.append({"rule_id": rule.id, "name": rule.name, "matched": False})
            continue
        meta = HOLDING_REGISTERS[HOLDING_BY_NAME[rule.action.register_name]]
        raw = int(round(target / meta["scale"]))
        results.append({
            "rule_id": rule.id,
            "name": rule.name,
            "matched": True,
            "register": meta["name"],
            "value": target,
            "raw": raw,
        })
    return {"enabled": engine.is_enabled(), "timezone": tz, "results": results}


@app.get("/api/automation/log")
def api_automation_log(limit: int = Query(50, ge=1, le=500)):
    """Return recent automation actions / dry-runs."""
    engine = _load_automation_engine()
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, ts, rule_id, rule_name, register_name,
                       raw_value, scaled_value, dry_run, success, message
                FROM {engine.log_table}
                ORDER BY ts DESC LIMIT %s
                """,
                (limit,),
            )
            columns = [
                "id", "timestamp", "rule_id", "rule_name", "register_name",
                "raw_value", "scaled_value", "dry_run", "success", "message",
            ]
            rows = cur.fetchall()
        return {"log": [_row_to_dict(row, columns) for row in rows], "count": len(rows)}
    finally:
        conn.close()


# ── quick charge / generator charge ────────────────────────────────────────

from collector.quick_charge import QuickChargeManager


def _load_quick_charge() -> QuickChargeManager:
    return QuickChargeManager(
        db_host=DB_CONFIG["host"],
        db_port=DB_CONFIG["port"],
        db_user=DB_CONFIG["user"],
        db_password=DB_CONFIG["password"],
        db_name=DB_CONFIG["database"],
    )


def _resolve_dongle() -> dict:
    """Resolve dongle connection params from env or DB settings."""
    host = os.getenv("LUX_DONGLE_HOST") or _load_db_setting("dongle_host") or "192.168.1.100"
    port = int(os.getenv("LUX_DONGLE_PORT") or _load_db_setting("dongle_port") or "8000")
    datalog = os.getenv("LUX_DATALOG_SERIAL") or _load_db_setting("datalog_serial") or ""
    inverter = os.getenv("LUX_INVERTER_SERIAL") or _load_db_setting("inverter_serial") or ""
    return {
        "dongle_host": host,
        "dongle_port": port,
        "datalog_serial": datalog,
        "inverter_serial": inverter,
    }


class QuickChargeBody(BaseModel):
    amps: Optional[int] = None
    minutes: Optional[int] = None
    dry_run: bool = False


@app.get("/api/quick-charge/status")
def api_quick_charge_status():
    """Return the current quick-charge state and defaults."""
    qc = _load_quick_charge()
    return qc.status()


@app.post("/api/quick-charge/start")
def api_quick_charge_start(body: QuickChargeBody):
    """Start a quick charge (write AC charge current for N minutes)."""
    dongle = _resolve_dongle()
    if not dongle["datalog_serial"] or not dongle["inverter_serial"]:
        raise HTTPException(400, "datalog_serial / inverter_serial not configured")

    qc = _load_quick_charge()
    result = qc.start(
        dongle_host=dongle["dongle_host"],
        dongle_port=dongle["dongle_port"],
        datalog_serial=dongle["datalog_serial"],
        inverter_serial=dongle["inverter_serial"],
        amps=body.amps,
        minutes=body.minutes,
        dry_run=body.dry_run,
    )
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "quick charge failed"))
    return result


@app.post("/api/quick-charge/stop")
def api_quick_charge_stop(dry_run: bool = False):
    """Stop an active quick charge, restoring the prior value."""
    dongle = _resolve_dongle()
    if not dongle["datalog_serial"] or not dongle["inverter_serial"]:
        raise HTTPException(400, "datalog_serial / inverter_serial not configured")

    qc = _load_quick_charge()
    result = qc.stop(
        dongle_host=dongle["dongle_host"],
        dongle_port=dongle["dongle_port"],
        datalog_serial=dongle["datalog_serial"],
        inverter_serial=dongle["inverter_serial"],
        dry_run=dry_run,
    )
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "quick charge stop failed"))
    return result


# ── static dashboard ────────────────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/", CacheStaticFiles(directory=str(STATIC_DIR), html=True), name="static")
