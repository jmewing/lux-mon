#!/usr/bin/env bash
# Backup lux-mon MariaDB + settings + .env
set -euo pipefail

BACKUP_DIR="${LUX_BACKUP_DIR:-/var/backups/lux-mon}"
KEEP_DAYS="${LUX_BACKUP_KEEP_DAYS:-30}"
DB_NAME="${LUX_MARIADB_DATABASE:-luxmon}"
DB_USER="${LUX_MARIADB_USER:-luxmon}"
DB_PASS="${LUX_MARIADB_PASSWORD:-}"
DB_HOST="${LUX_MARIADB_HOST:-localhost}"
DB_PORT="${LUX_MARIADB_PORT:-3306}"
SRC_DIR="${LUX_SRC_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
NAME="luxmon-backup-${TIMESTAMP}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$BACKUP_DIR"

# MariaDB dump
mysqldump -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" ${DB_PASS:+-p"$DB_PASS"} "$DB_NAME" \
  > "$TMP/luxmon.sql"

# .env if present
if [ -f "$SRC_DIR/.env" ]; then
  cp "$SRC_DIR/.env" "$TMP/env"
fi

# settings JSON snapshot (optional, helps cross-check)
curl -sf http://127.0.0.1:8080/api/settings > "$TMP/settings.json" || true

# tar it up
tar -czf "$BACKUP_DIR/${NAME}.tar.gz" -C "$TMP" .

# prune old backups
find "$BACKUP_DIR" -maxdepth 1 -name 'luxmon-backup-*.tar.gz' -mtime +"$KEEP_DAYS" -delete || true

echo "$BACKUP_DIR/${NAME}.tar.gz"
