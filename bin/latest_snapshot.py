#!/usr/bin/env python3
"""Print the most recent inverter snapshot from MariaDB."""

import json
import os
import sys
from pathlib import Path

import pymysql


DB_CONFIG = {
    "host": os.environ.get("LUX_MARIADB_HOST", "localhost"),
    "port": int(os.environ.get("LUX_MARIADB_PORT", "3306")),
    "user": os.environ.get("LUX_MARIADB_USER", "luxmon"),
    "password": os.environ.get("LUX_MARIADB_PASSWORD", "luxmon"),
    "database": os.environ.get("LUX_MARIADB_DATABASE", "luxmon"),
    "autocommit": True,
}

TABLE_PREFIX = os.environ.get("LUX_MARIADB_TABLE_PREFIX", "lux_")


def main():
    try:
        conn = pymysql.connect(**DB_CONFIG)
    except Exception as exc:
        print(f"Failed to connect to MariaDB: {exc}", file=sys.stderr)
        sys.exit(1)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT s.id, s.ts, r.name, r.value, r.unit
            FROM {TABLE_PREFIX}snapshots s
            JOIN {TABLE_PREFIX}registers r ON r.snapshot_id = s.id
            WHERE s.id = (SELECT id FROM {TABLE_PREFIX}snapshots ORDER BY ts DESC LIMIT 1)
            ORDER BY r.name
            """
        )
        rows = cur.fetchall()

    if not rows:
        print("No snapshots found.")
        return

    snapshot_id, ts = rows[0][0], rows[0][1]
    print(f"Snapshot {snapshot_id} at {ts}")
    print("-" * 50)
    for _, _, name, value, unit in rows:
        unit = unit or ""
        print(f"  {name:30s}: {value:12.2f} {unit}")


if __name__ == "__main__":
    main()
