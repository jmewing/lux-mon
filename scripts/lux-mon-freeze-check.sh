#!/usr/bin/env bash
# lux-mon dongle freeze monitor with auto-recovery
# Detects when the inverter data goes stale (frozen registers) and alerts.
# With --auto-restart: on stuck state, restarts lux-collector (with cooldown)
# and verifies data resumes. Data over silence.
#
# Freeze rule (2026-09-10): load_power AND battery_power_net must be identical
# for 30 minutes to count as frozen (not just battery, which sits static at night).
# False-positive guard: if grid is importing (positive) AND battery SOC >= 99%,
# the static values are legitimate (battery full, grid carrying steady load) — ignore.
#
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
  # 1 if load_power AND battery_power_net are each identical over the last
  # 30 minutes (single distinct value each), else 0. Uses the named
  # lux_registers table (computed values are written there by the collector).
  local load_distinct batt_distinct
  load_distinct=$(docker exec lux-mariadb mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -N -e "
    SELECT COUNT(DISTINCT value) FROM lux_registers
    WHERE name='load_power' AND ts >= NOW(3) - INTERVAL 30 MINUTE;" 2>/dev/null)
  batt_distinct=$(docker exec lux-mariadb mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -N -e "
    SELECT COUNT(DISTINCT value) FROM lux_registers
    WHERE name='battery_power_net' AND ts >= NOW(3) - INTERVAL 30 MINUTE;" 2>/dev/null)
  if [ "$load_distinct" = "1" ] && [ "$batt_distinct" = "1" ]; then
    echo "1"
  else
    echo "0"
  fi
}

false_positive() {
  # 1 if grid is importing (grid_power_net > 0) AND battery is full (soc >= 99),
  # meaning static values are legitimate and should NOT alert. Else 0.
  local grid soc
  grid=$(docker exec lux-mariadb mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -N -e "
    SELECT value FROM lux_registers WHERE name='grid_power_net' ORDER BY snapshot_id DESC LIMIT 1;" 2>/dev/null)
  soc=$(docker exec lux-mariadb mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -N -e "
    SELECT value FROM lux_registers WHERE name='soc' ORDER BY snapshot_id DESC LIMIT 1;" 2>/dev/null)
  if [ -n "$grid" ] && [ -n "$soc" ]; then
    if awk "BEGIN{exit !($grid > 0 && $soc >= 99)}"; then
      echo "1"
    else
      echo "0"
    fi
  else
    echo "0"
  fi
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

# --- 2. freeze check (load + battery identical for 30 min) ---
STABLE=$(registers_stable)
if [ "$STABLE" = "1" ]; then
  FP=$(false_positive)
  if [ "$FP" = "1" ]; then
    echo "OK: values static but grid importing + battery full (false positive, ignoring)"
    exit 0
  fi
  echo "ALERT: data frozen — load + battery identical for 30 min"
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

echo "OK: data fresh (last write ${AGE}s ago), values changing"
exit 0
