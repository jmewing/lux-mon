#!/usr/bin/env bash
# lux-mon dongle freeze monitor with auto-recovery
# Detects when the inverter data goes stale (frozen registers) and alerts.
# With --auto-restart: on stuck state, restarts lux-collector (with cooldown)
# and verifies data resumes. Data over silence.
# Usage: lux-mon-freeze-check.sh [--auto-restart]
# Exit 0 = healthy/recovered, exit 2 = frozen/stale (alert condition)

DB_USER="luxmon"
DB_PASS="_X6XHeXOqrQ638Zo6oTA--YcWXpk7wJV"
DB_NAME="luxmon"

AUTO_RESTART=0
[ "${1:-}" = "--auto-restart" ] && AUTO_RESTART=1

STATE_DIR="/var/tmp/lux-mon"
COOLDOWN=1800   # 30 min between auto-restarts
STATE_FILE="$STATE_DIR/freeze-restart.state"

# --- helpers ---
latest_age() {
  LATEST_TS=$(docker exec lux-mariadb mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -N -e \
    "SELECT ts FROM lux_snapshots ORDER BY id DESC LIMIT 1;" 2>/dev/null)
  if [ -z "$LATEST_TS" ]; then
    echo "no-snapshots"
    return
  fi
  LATEST_EPOCH=$(date -d "$LATEST_TS" +%s 2>/dev/null)
  NOW_EPOCH=$(date +%s)
  echo $((NOW_EPOCH - LATEST_EPOCH))
}

registers_stable() {
  # 1 if last 30 snapshots have identical pv1/discharge/soc, else 0
  docker exec lux-mariadb mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -N -e "
SELECT COUNT(DISTINCT CONCAT(JSON_EXTRACT(raw_registers,'$.7'),'|',JSON_EXTRACT(raw_registers,'$.11'),'|',(JSON_EXTRACT(raw_registers,'$.5') & 0xFF)))
FROM (SELECT raw_registers FROM lux_snapshots ORDER BY id DESC LIMIT 30) t;" 2>/dev/null
}

stable_span() {
  # seconds spanned by the last 30 snapshots
  docker exec lux-mariadb mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -N -e "
  SELECT TIMESTAMPDIFF(SECOND, MIN(ts), MAX(ts)) FROM (SELECT ts FROM lux_snapshots ORDER BY id DESC LIMIT 30) t;" 2>/dev/null
}

restart_lux() {
  mkdir -p "$STATE_DIR"
  echo "$(date +%s)" > "$STATE_FILE"
  echo "RESTART: restarting lux-collector"
  docker restart lux-collector 2>&1
}

# --- 1. freshness check ---
AGE=$(latest_age)
if [ "$AGE" = "no-snapshots" ]; then
  echo "ALERT: no snapshots found in lux_snapshots"
  exit 2
fi
if [ "$AGE" -gt 120 ]; then
  echo "ALERT: no fresh snapshots — last write ${AGE}s ago"
  if [ "$AUTO_RESTART" = "1" ]; then
    LAST=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    if [ $((NOW - LAST)) -ge "$COOLDOWN" ]; then
      restart_lux
      sleep 60
      AGE2=$(latest_age)
      if [ "$AGE2" != "no-snapshots" ] && [ "$AGE2" -le 120 ]; then
        echo "RECOVERED: lux-collector restarted, data flowing (age ${AGE2}s)"
        exit 0
      fi
      echo "ALERT: restart did not recover data (age ${AGE2}s)"
      exit 2
    fi
    echo "ALERT: still stale (last restart ${LAST}s ago, cooldown ${COOLDOWN}s)"
  fi
  exit 2
fi

# --- 2. register stability check ---
STABLE=$(registers_stable)
if [ "$STABLE" = "1" ]; then
  SPAN=$(stable_span)
  if [ "$SPAN" -ge 300 ]; then
    echo "ALERT: data frozen — last 30 snapshots identical over ${SPAN}s (pv1/discharge/soc unchanged)"
    if [ "$AUTO_RESTART" = "1" ]; then
      LAST=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
      NOW=$(date +%s)
      if [ $((NOW - LAST)) -ge "$COOLDOWN" ]; then
        restart_lux
        sleep 60
        AGE2=$(latest_age)
        STABLE2=$(registers_stable)
        if [ "$AGE2" != "no-snapshots" ] && [ "$AGE2" -le 120 ] && [ "$STABLE2" != "1" ]; then
          echo "RECOVERED: lux-collector restarted, data flowing (age ${AGE2}s)"
          exit 0
        fi
        echo "ALERT: restart did not recover data (age ${AGE2}s, stable=${STABLE2})"
        exit 2
      fi
      echo "ALERT: still frozen (last restart ${LAST}s ago, cooldown ${COOLDOWN}s)"
    fi
    exit 2
  fi
fi

echo "OK: data fresh (last write ${AGE}s ago), values changing"
exit 0
