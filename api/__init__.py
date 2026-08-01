"""lux-mon REST API — FastAPI app serving inverter telemetry from MariaDB."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pymysql
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

TZ = ZoneInfo("America/Chicago")

app = FastAPI(title="lux-mon API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ── database ────────────────────────────────────────────────────────────────

DB_CONFIG = {
    "host": os.getenv("LUX_DB_HOST", "localhost"),
    "user": os.getenv("LUX_DB_USER", "luxmon"),
    "password": os.getenv("LUX_DB_PASSWORD", "luxmon"),
    "database": os.getenv("LUX_DB_NAME", "luxmon"),
    "charset": "utf8mb4",
}


def _get_conn() -> pymysql.Connection:
    return pymysql.connect(**DB_CONFIG)


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

        return {
            "snapshot_id": snap_id,
            "timestamp": ts_aware.isoformat(),
            "registers": registers,
        }
    finally:
        conn.close()


# ── static dashboard ────────────────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
