"""lux-mon REST API — FastAPI app serving inverter telemetry from MariaDB."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymysql
import asyncio
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

TZ = ZoneInfo("America/Chicago")

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
):
    """Return time-series data for the last N minutes.

    Returns a list of {timestamp, registers: {name: value}} objects.
    If *fields* is provided, only those registers are included.
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
                "temp_inverter", "temp_battery", "temp_radiator_1",
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

    Aggregates `lux_hourly_energy` rows into kWh values.
    """
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

        # Pivot to {period: {name: {in_kwh, out_kwh}}}
        data: dict[str, dict[str, dict[str, float]]] = {}
        for period, name, in_kwh, out_kwh in rows:
            data.setdefault(period, {})[name] = {
                "in_kwh": float(in_kwh or 0),
                "out_kwh": float(out_kwh or 0),
            }

        # Ensure all requested periods exist, even if empty
        # (simpler to leave sparse and let the frontend fill)
        return {
            "range": range_,
            "periods": periods,
            "unit": "kWh",
            "data": data,
            "count": len(data),
        }
    finally:
        conn.close()



# ── settings ────────────────────────────────────────────────────────────────

class SettingUpdate(BaseModel):
    value: str


@app.get("/api/settings")
def api_settings():
    """Return all settings (defaults + overrides from DB) with metadata."""
    from collector.settings import get_all, seed_defaults, SETTING_META

    conn = _get_conn()
    try:
        seed_defaults(conn)
        settings = get_all(conn)
        # Attach metadata for each setting
        enriched = {}
        for key, value in settings.items():
            meta = SETTING_META.get(key, {})
            enriched[key] = {
                "value": value,
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
    """Get a single setting value."""
    from collector.settings import get

    conn = _get_conn()
    try:
        value = get(conn, name)
        if value is None:
            raise HTTPException(404, f"Setting '{name}' not found")
        return {"name": name, "value": value}
    finally:
        conn.close()


@app.put("/api/settings/{name}")
def api_setting_put(name: str, body: SettingUpdate):
    """Update a setting value."""
    from collector.settings import set_

    conn = _get_conn()
    try:
        set_(conn, name, body.value)
        return {"name": name, "value": body.value, "updated": True}
    finally:
        conn.close()


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


# ── static dashboard ────────────────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
