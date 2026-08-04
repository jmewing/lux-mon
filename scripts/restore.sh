#!/usr/bin/env bash
# Restore lux-mon from a backup archive created by scripts/backup.sh.
# Usage: LUX_BACKUP=/var/backups/lux-mon/luxmon-backup-YYYYMMDD-HHMMSS.tar.gz ./scripts/restore.sh
#
# WARNING: This will overwrite the existing lux-mon MariaDB and InfluxDB data.
# Stop the lux-mon collector/API before running this.
set -euo pipefail

SRC_DIR="${LUX_SRC_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"

if [ -f "$SRC_DIR/.env" ]; then
  set -a
  . "$SRC_DIR/.env"
  set +a
fi

BACKUP_FILE="${LUX_BACKUP:-}"
if [ -z "$BACKUP_FILE" ] || [ ! -f "$BACKUP_FILE" ]; then
  echo "Usage: LUX_BACKUP=/path/to/luxmon-backup-YYYYMMDD-HHMMSS.tar.gz ./scripts/restore.sh"
  exit 1
fi

DB_NAME="${LUX_MARIADB_DATABASE:-luxmon}"
DB_USER="${LUX_MARIADB_USER:-luxmon}"
DB_PASS="${LUX_MARIADB_PASSWORD:-}"
DB_HOST="${LUX_MARIADB_HOST:-localhost}"
DB_PORT="${LUX_MARIADB_PORT:-3306}"

INFLUX_URL="${LUX_INFLUX_URL:-http://localhost:8086}"
INFLUX_TOKEN="${LUX_INFLUX_TOKEN:-}"
INFLUX_ORG="${LUX_INFLUX_ORG:-luxmon}"
INFLUX_ADMIN_USER="${LUX_INFLUX_USERNAME:-}"
INFLUX_ADMIN_PASS="${LUX_INFLUX_ADMIN_PASSWORD:-}"

GRAFANA_PROVISIONING="${LUX_GRAFANA_PROVISIONING:-/etc/grafana/provisioning}"
GRAFANA_DASHBOARDS="${LUX_GRAFANA_DASHBOARDS:-/var/lib/grafana/dashboards}"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "[restore] Extracting ${BACKUP_FILE}"
tar -xzf "$BACKUP_FILE" -C "$TMP"

cat "$TMP/backup.meta" || true

# 1. Restore MariaDB
if [ -f "$TMP/luxmon.sql" ]; then
  echo "[restore] Restoring MariaDB database ${DB_NAME}"
  mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" ${DB_PASS:+-p"$DB_PASS"} -e "CREATE DATABASE IF NOT EXISTS ${DB_NAME};" || true
  mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" ${DB_PASS:+-p"$DB_PASS"} "$DB_NAME" < "$TMP/luxmon.sql"
  echo "[restore] MariaDB restore OK"
fi

# 2. Restore InfluxDB v2 bucket data
if [ -d "$TMP/influxdb-backup" ] && [ -n "$INFLUX_TOKEN" ]; then
  echo "[restore] Restoring InfluxDB bucket ${INFLUX_BUCKET}"

  influx_restore() {
    local backup_path="$1"
    if command -v influx >/dev/null 2>&1; then
      influx restore \
        --host "$INFLUX_URL" \
        --token "$INFLUX_TOKEN" \
        --bucket "$INFLUX_BUCKET" \
        --new-bucket "$INFLUX_BUCKET" \
        "$backup_path"
    else
      docker run --rm --network host \
        -v "$backup_path:/backup:ro" \
        -e INFLUX_TOKEN="$INFLUX_TOKEN" \
        influxdb:2.7 influx restore \
          --host "$INFLUX_URL" \
          --token "$INFLUX_TOKEN" \
          --bucket "$INFLUX_BUCKET" \
          --new-bucket "$INFLUX_BUCKET" \
          /backup
    fi
  }

  # InfluxDB restore cannot overwrite an existing bucket. Delete it first if present.
  if influx bucket list --host "$INFLUX_URL" --org "$INFLUX_ORG" --token "$INFLUX_TOKEN" 2>/dev/null | grep -qw "$INFLUX_BUCKET"; then
    echo "[restore] Deleting existing InfluxDB bucket ${INFLUX_BUCKET} before restore"
    influx bucket delete --host "$INFLUX_URL" --org "$INFLUX_ORG" --token "$INFLUX_TOKEN" --name "$INFLUX_BUCKET" || true
  fi

  influx_restore "$TMP/influxdb-backup"
  echo "[restore] InfluxDB restore OK"
else
  echo "[restore] Skipping InfluxDB restore (no backup dir or LUX_INFLUX_TOKEN not set)"
fi

# 3. Restore Grafana provisioning and dashboards
if [ -d "$TMP/grafana-provisioning" ]; then
  echo "[restore] Restoring Grafana provisioning"
  sudo rm -rf "$GRAFANA_PROVISIONING"
  sudo mkdir -p "$GRAFANA_PROVISIONING"
  sudo cp -a "$TMP/grafana-provisioning/"* "$GRAFANA_PROVISIONING/"
  echo "[restore] Grafana provisioning restore OK"
fi
if [ -d "$TMP/grafana-dashboards" ]; then
  echo "[restore] Restoring Grafana dashboards"
  sudo rm -rf "$GRAFANA_DASHBOARDS"
  sudo mkdir -p "$GRAFANA_DASHBOARDS"
  sudo cp -a "$TMP/grafana-dashboards/"* "$GRAFANA_DASHBOARDS/"
  echo "[restore] Grafana dashboards restore OK"
fi

# 4. Restore .env if no local .env exists
if [ -f "$TMP/env" ] && [ ! -f "$SRC_DIR/.env" ]; then
  cp "$TMP/env" "$SRC_DIR/.env"
  echo "[restore] .env restored to ${SRC_DIR}/.env"
fi

# 5. Runtime settings can be re-applied via the API
if [ -f "$TMP/settings.json" ] && [ -f "$SRC_DIR/.env" ]; then
  echo "[restore] Runtime settings backed up; re-apply manually via /api/settings if needed."
fi

echo "[restore] Done. Restart lux-mon services if they were stopped."
