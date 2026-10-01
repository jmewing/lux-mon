#!/usr/bin/env bash
# lux-mon dongle freeze monitor with auto-recovery
# Detects when the inverter data goes stale (frozen registers) and alerts.
# With --auto-restart: on stuck state, restarts lux-collector (with cooldown)
# and verifies data resumes. Data over silence.
#
# Freeze rule (2026-09-10): load_power AND battery_power_net must be identical
# for 5 minutes to count as frozen (not just battery, which sits static at night).
# False-positive guard: if grid is importing (positive) AND battery SOC >= 99%,
# the static values are legitimate (battery full, grid carrying steady load) — ignore.
#
# transport=cloud_http (EG4 cloud portal): snapshots are only written when the
# portal has a new upload (every ~20 s - 5 min), so the freshness threshold is
# max(900, 3 x LUX_CLOUD_POLL_SEC + 300) seconds and the identical-values freeze
# rule is skipped (the collector already de-duplicates unchanged portal data, so
# a frozen portal shows up as stale, not as identical values). While the portal
# is rejecting the EG4 login, the collector is never auto-restarted: a restart
# would only replay the rejected password.
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
# Episode dedup: once we've alerted+restarted for a frozen episode, suppress
# repeat ALERTs for the same persistent wedge until data stops being frozen
# for the full EPISODE_SUPPRESS window (chronic dongle fault -> 1 alert).
EPISODE_SUPPRESS=86400  # 24h: re-alert at most daily for a continuously frozen episode
EPISODE_FILE="$STATE_DIR/freeze-episode.state"

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
  # 5 minutes (single distinct value each), else 0. Uses the named
  # lux_registers table (computed values are written there by the collector).
  local load_distinct batt_distinct
  load_distinct=$(docker exec lux-mariadb mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -N -e "
    SELECT COUNT(DISTINCT value) FROM lux_registers
    WHERE name='load_power' AND ts >= NOW(3) - INTERVAL 5 MINUTE;" 2>/dev/null)
  batt_distinct=$(docker exec lux-mariadb mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -N -e "
    SELECT COUNT(DISTINCT value) FROM lux_registers
    WHERE name='battery_power_net' AND ts >= NOW(3) - INTERVAL 5 MINUTE;" 2>/dev/null)
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

# Episode-alert dedup: return 0 to SUPPRESS the alert (episode recently
# alerted+restarted, still frozen), 1 to alert. Resets when data recovers
# (registers_stable==0 -> caller clears the episode state).
episode_suppressed() {
  local ep_now ep_last
  ep_now=$(date +%s)
  ep_last=$(cat "$EPISODE_FILE" 2>/dev/null || echo 0)
  [ "$ep_last" -ge 1 ] && [ $((ep_now - ep_last)) -lt "$EPISODE_SUPPRESS" ]
}
episode_pin() { # record that we just alerted+restarted for this episode
  mkdir -p "$STATE_DIR"
  echo "$(date +%s)" > "$EPISODE_FILE"
}
episode_clear() { rm -f "$EPISODE_FILE"; }

restart_lux() {
  mkdir -p "$STATE_DIR"
  echo "$(date +%s)" > "$STATE_FILE"
  echo "RESTART: restarting lux-collector"
  docker restart lux-collector 2>&1
}

cloud_auth_rejected() {
  # 1 if the collector's most recent EG4 cloud login attempt was rejected
  # (transport=cloud_http), else 0. A restart would only replay the rejected
  # password (account lockout risk); the collector retries on its own with
  # an escalating pause, and a credentials fix needs `docker compose up -d`.
  local last
  last=$(docker logs --since 48h lux-collector 2>&1 \
    | grep -E "EG4 cloud rejected the login|Logged in to the EG4 cloud" | tail -n 1)
  case "$last" in
    *"rejected the login"*) echo "1" ;;
    *) echo "0" ;;
  esac
}

# Active transport (DB-authoritative, like the collector).
TRANSPORT=$(docker exec lux-mariadb mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -N -e \
  "SELECT value FROM lux_settings WHERE name='transport';" 2>/dev/null)
TRANSPORT=${TRANSPORT:-tcp_active}

FRESH_MAX=120              # seconds: dongle transports write every few seconds
SOURCE_DESC="dongle fault"
if [ "$TRANSPORT" = "cloud_http" ]; then
  # Gated writes: one snapshot per new portal upload.
  CLOUD_POLL=$(docker exec lux-collector printenv LUX_CLOUD_POLL_SEC 2>/dev/null)
  CLOUD_POLL=${CLOUD_POLL%%.*}
  case "$CLOUD_POLL" in ''|*[!0-9]*) CLOUD_POLL=60 ;; esac
  FRESH_MAX=$((3 * CLOUD_POLL + 300))
  [ "$FRESH_MAX" -lt 900 ] && FRESH_MAX=900
  SOURCE_DESC="EG4 cloud outage"
fi

# --- 1. freshness check ---
AGE=$(latest_age)
if [ "$AGE" = "no-snapshots" ]; then
  echo "ALERT: no snapshots found in lux_snapshots"
  exit 2
fi
if [ "$AGE" -gt "$FRESH_MAX" ]; then
  echo "ALERT: no fresh snapshots — last write ${AGE}s ago (transport ${TRANSPORT}, limit ${FRESH_MAX}s)"
  if [ "$TRANSPORT" = "cloud_http" ] && [ "$(cloud_auth_rejected)" = "1" ]; then
    if episode_suppressed; then
      echo "OK: known EG4 cloud login rejection, still stale - suppressing duplicate alert"
      exit 0
    fi
    episode_pin
    echo "ALERT: EG4 cloud is rejecting the login - fix LUX_CLOUD_USERNAME/LUX_CLOUD_PASSWORD in .env and run 'docker compose -f docker/docker-compose.yml up -d collector' (not auto-restarting)"
    exit 2
  fi
  if [ "$AUTO_RESTART" = "1" ]; then
    LAST=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    if [ $((NOW - LAST)) -ge "$COOLDOWN" ]; then
      restart_lux
      sleep 60
      AGE2=$(latest_age)
      if [ "$AGE2" != "no-snapshots" ] && [ "$AGE2" -le "$FRESH_MAX" ]; then
        episode_clear
        echo "RECOVERED: lux-collector restarted, data flowing (age ${AGE2}s)"
        exit 0
      fi
      episode_pin
      echo "ALERT: restart did not recover data (age ${AGE2}s)"
      exit 2
    fi
    if episode_suppressed; then
      echo "OK: known ${SOURCE_DESC}, still stale, last restart ${LAST}s ago - suppressing duplicate alert"
      exit 0
    fi
    episode_pin
    echo "ALERT: still stale (last restart ${LAST}s ago, cooldown ${COOLDOWN}s)"
    exit 2
  fi
  exit 2
fi

# --- 2. freeze check (load + battery identical for 5 min) ---
# Skipped for cloud_http: unchanged portal data is de-duplicated by the
# collector (no new snapshot), so a frozen portal is caught by check 1.
if [ "$TRANSPORT" = "cloud_http" ]; then
  episode_clear
  echo "OK: data fresh (last write ${AGE}s ago, transport cloud_http)"
  exit 0
fi
STABLE=$(registers_stable)
if [ "$STABLE" = "1" ]; then
  FP=$(false_positive)
  if [ "$FP" = "1" ]; then
    echo "OK: values static but grid importing + battery full (false positive, ignoring)"
    exit 0
  fi
  echo "ALERT: data frozen — load + battery identical for 5 min"
  if [ "$AUTO_RESTART" = "1" ]; then
    LAST=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    if [ $((NOW - LAST)) -ge "$COOLDOWN" ]; then
      restart_lux
      sleep 60
      AGE2=$(latest_age)
      STABLE2=$(registers_stable)
      if [ "$AGE2" != "no-snapshots" ] && [ "$AGE2" -le "$FRESH_MAX" ] && [ "$STABLE2" != "1" ]; then
        episode_clear
        echo "RECOVERED: lux-collector restarted, data flowing (age ${AGE2}s)"
        exit 0
      fi
      episode_pin
      echo "ALERT: restart did not recover data (age ${AGE2}s, stable=${STABLE2})"
      exit 2
    fi
    if episode_suppressed; then
      echo "OK: known ${SOURCE_DESC}, still frozen, last restart ${LAST}s ago - suppressing duplicate alert"
      exit 0
    fi
    episode_pin
    echo "ALERT: still frozen (last restart ${LAST}s ago, cooldown ${COOLDOWN}s)"
    exit 2
  fi
  exit 2
fi

episode_clear
echo "OK: data fresh (last write ${AGE}s ago), values changing"
exit 0
