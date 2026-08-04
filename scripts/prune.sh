#!/usr/bin/env bash
# Prune old lux-mon detail data while keeping rollups.
set -euo pipefail

SRC_DIR="${LUX_SRC_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"

if [ -f "$SRC_DIR/.env" ]; then
  set -a
  . "$SRC_DIR/.env"
  set +a
fi

DB_NAME="${LUX_MARIADB_DATABASE:-luxmon}"
DB_USER="${LUX_MARIADB_USER:-luxmon}"
DB_PASS="${LUX_MARIADB_PASSWORD:-}"
DB_HOST="${LUX_MARIADB_HOST:-localhost}"
DB_PORT="${LUX_MARIADB_PORT:-3306}"
KEEP_DAYS="${LUX_PRUNE_KEEP_DAYS:-90}"
KEEP_HOURLY_DAYS="${LUX_PRUNE_KEEP_HOURLY_DAYS:-365}"

mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" ${DB_PASS:+-p"$DB_PASS"} "$DB_NAME" <<SQL
DELETE FROM lux_registers WHERE ts < DATE_SUB(NOW(), INTERVAL ${KEEP_DAYS} DAY);
DELETE FROM lux_snapshots WHERE ts < DATE_SUB(NOW(), INTERVAL ${KEEP_DAYS} DAY);
DELETE FROM lux_alerts WHERE ts < DATE_SUB(NOW(), INTERVAL ${KEEP_DAYS} DAY);
DELETE FROM lux_hourly_energy WHERE hour < DATE_SUB(NOW(), INTERVAL ${KEEP_HOURLY_DAYS} DAY);
SQL

echo "Pruned data older than ${KEEP_DAYS} days; hourly energy older than ${KEEP_HOURLY_DAYS} days."
