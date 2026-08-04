#!/usr/bin/env bash
# Backup lux-mon: MariaDB + InfluxDB v2 + Grafana provisioning/dashboards + .env + settings.
# The resulting archive can be restored with scripts/restore.sh on a fresh install.
set -euo pipefail

SRC_DIR="${LUX_SRC_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"

# Load .env if present so DB credentials and paths are available.
if [ -f "$SRC_DIR/.env" ]; then
  set -a
  . "$SRC_DIR/.env"
  set +a
fi

BACKUP_DIR="${LUX_BACKUP_DIR:-/var/backups/lux-mon}"
KEEP_DAYS="${LUX_BACKUP_KEEP_DAYS:-30}"
REMOTE_DEST="${LUX_BACKUP_REMOTE:-}"

DB_NAME="${LUX_MARIADB_DATABASE:-luxmon}"
DB_USER="${LUX_MARIADB_USER:-luxmon}"
DB_PASS="${LUX_MARIADB_PASSWORD:-}"
DB_HOST="${LUX_MARIADB_HOST:-localhost}"
DB_PORT="${LUX_MARIADB_PORT:-3306}"

INFLUX_URL="${LUX_INFLUX_URL:-http://localhost:8086}"
INFLUX_TOKEN="${LUX_INFLUX_TOKEN:-}"
INFLUX_ORG="${LUX_INFLUX_ORG:-luxmon}"
INFLUX_BUCKET="${LUX_INFLUX_BUCKET:-luxmon}"

GRAFANA_PROVISIONING="${LUX_GRAFANA_PROVISIONING:-/etc/grafana/provisioning}"
GRAFANA_DASHBOARDS="${LUX_GRAFANA_DASHBOARDS:-/var/lib/grafana/dashboards}"

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
NAME="luxmon-backup-${TIMESTAMP}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$BACKUP_DIR"

echo "[backup] Starting lux-mon backup: ${NAME}"

# 1. MariaDB dump
mysqldump -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" ${DB_PASS:+-p"$DB_PASS"} "$DB_NAME" \
  > "$TMP/luxmon.sql"
echo "[backup] MariaDB dump OK"

# 2. InfluxDB v2 backup (works whether running natively or in Docker)
if [ -n "$INFLUX_TOKEN" ]; then
  INFLUX_BACKUP_DIR="$TMP/influxdb-backup"
  mkdir -p "$INFLUX_BACKUP_DIR"

  # Prefer a local influx CLI if installed.
  if command -v influx >/dev/null 2>&1; then
    influx backup \
      --host "$INFLUX_URL" \
      --org "$INFLUX_ORG" \
      --token "$INFLUX_TOKEN" \
      "$INFLUX_BACKUP_DIR"
  else
    # Fall back to running the official influxdb container against the live API.
    docker run --rm --network host \
      -v "$INFLUX_BACKUP_DIR:/out" \
      -e INFLUX_TOKEN="$INFLUX_TOKEN" \
      influxdb:2.7 influx backup \
        --host "$INFLUX_URL" \
        --org "$INFLUX_ORG" \
        --token "$INFLUX_TOKEN" \
        /out
  fi
  echo "[backup] InfluxDB backup OK"
else
  echo "[backup] Skipping InfluxDB backup: LUX_INFLUX_TOKEN not set"
fi

# 3. Grafana provisioning and dashboard JSON files
if [ -d "$GRAFANA_PROVISIONING" ]; then
  if [ "$EUID" -eq 0 ]; then
    cp -a "$GRAFANA_PROVISIONING" "$TMP/grafana-provisioning"
  else
    sudo cp -a "$GRAFANA_PROVISIONING" "$TMP/grafana-provisioning" || true
  fi
  echo "[backup] Grafana provisioning OK"
fi
if [ -d "$GRAFANA_DASHBOARDS" ]; then
  if [ "$EUID" -eq 0 ]; then
    cp -a "$GRAFANA_DASHBOARDS" "$TMP/grafana-dashboards"
  else
    sudo cp -a "$GRAFANA_DASHBOARDS" "$TMP/grafana-dashboards" || true
  fi
  echo "[backup] Grafana dashboards OK"
fi

# 4. lux-mon .env configuration
if [ -f "$SRC_DIR/.env" ]; then
  cp "$SRC_DIR/.env" "$TMP/env"
  echo "[backup] .env OK"
fi

# 5. Runtime settings from the API
curl -sf "${LUX_API_URL:-http://127.0.0.1:8080}/api/settings" > "$TMP/settings.json" || true

# 6. Metadata
{
  echo "NAME=${NAME}"
  echo "TIMESTAMP=${TIMESTAMP}"
  echo "HOSTNAME=$(hostname)"
  echo "SRC_DIR=${SRC_DIR}"
  echo "DB_HOST=${DB_HOST}"
  echo "DB_NAME=${DB_NAME}"
  echo "INFLUX_URL=${INFLUX_URL}"
  echo "INFLUX_ORG=${INFLUX_ORG}"
  echo "INFLUX_BUCKET=${INFLUX_BUCKET}"
} > "$TMP/backup.meta"

# 7. Create archive
ARCHIVE="$BACKUP_DIR/${NAME}.tar.gz"
tar -czf "$ARCHIVE" -C "$TMP" .
echo "[backup] Archive created: ${ARCHIVE}"

# 8. Rotate local backups
find "$BACKUP_DIR" -maxdepth 1 -name 'luxmon-backup-*.tar.gz' -mtime +"$KEEP_DAYS" -delete || true

# 9. Optional remote copy
if [ -n "$REMOTE_DEST" ]; then
  echo "[backup] Copying to remote: ${REMOTE_DEST}"
  if [[ "$REMOTE_DEST" == rsync://* ]]; then
    rsync -a --mkpath "$ARCHIVE" "${REMOTE_DEST#rsync://}"
  else
    # Treat as scp-style destination, e.g. user@host:/path or /mnt/usb
    if [[ "$REMOTE_DEST" == *@*:* ]]; then
      scp "$ARCHIVE" "$REMOTE_DEST/"
    else
      mkdir -p "$REMOTE_DEST"
      cp -a "$ARCHIVE" "$REMOTE_DEST/"
    fi
  fi
  echo "[backup] Remote copy OK"
fi

echo "[backup] Done: ${ARCHIVE}"
