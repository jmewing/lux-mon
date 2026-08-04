#!/usr/bin/env bash
# Prune old lux-mon detail data while keeping rollups.
set -euo pipefail

DB_NAME="${LUX_MARIADB_DATABASE:-luxmon}"
DB_USER="${LUX_MARIADB_USER:-luxmon}"
DB_PASS="${LUX_MARIADB_PASSWORD:-}"
DB_HOST="${LUX_MARIADB_HOST:-localhost}"
DB_PORT="${LUX_MARIADB_PORT:-3306}"
KEEP_DAYS="${LUX_PRUNE_KEEP_DAYS:-90}"
KEEP_HOURLY_DAYS="${LUX_PRUNE_KEEP_HOURLY_DAYS:-365}"

mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" ${DB_PASS:+-p"$DB_PASS"} "$DB_NAME" <<SQL
-- Delete old raw register rows (keep detail for last N days)
DELETE FROM lux_registers WHERE ts < DATE_SUB(NOW(), INTERVAL ${KEEP_DAYS} DAY);

-- Delete old snapshot rows (keep detail for last N days)
DELETE FROM lux_snapshots WHERE ts < DATE_SUB(NOW(), INTERVAL ${KEEP_DAYS} DAY);

-- Delete old alert events (keep alert history for last N days)
DELETE FROM lux_alerts WHERE ts < DATE_SUB(NOW(), INTERVAL ${KEEP_DAYS} DAY);

-- Delete old hourly energy rows but keep longer history
DELETE FROM lux_hourly_energy WHERE hour < DATE_SUB(NOW(), INTERVAL ${KEEP_HOURLY_DAYS} DAY);

-- Compact tables
OPTIMIZE TABLE lux_registers, lux_snapshots, lux_alerts, lux_hourly_energy;
SQL

echo "Pruned data older than ${KEEP_DAYS} days; hourly energy older than ${KEEP_HOURLY_DAYS} days."
