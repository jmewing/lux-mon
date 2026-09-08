#!/usr/bin/env bash
# lux-mon dongle freeze monitor
# Detects when the inverter data goes stale (frozen registers) and alerts.
# Checks: latest snapshot timestamp freshness + register value stability.
# Usage: lux-mon-freeze-check.sh  (run from cron/heartbeat)
# Exit 0 = healthy, exit 2 = frozen/stale (alert condition)

DB_USER="luxmon"
DB_PASS="_X6XHeXOqrQ638Zo6oTA--YcWXpk7wJV"
DB_NAME="luxmon"

# 1. Latest snapshot freshness (should be < 60s old)
LATEST_TS=$(docker exec lux-mariadb mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -N -e \
  "SELECT ts FROM lux_snapshots ORDER BY id DESC LIMIT 1;" 2>/dev/null)
if [ -z "$LATEST_TS" ]; then
  echo "ALERT: no snapshots found in lux_snapshots"
  exit 2
fi
LATEST_EPOCH=$(date -d "$LATEST_TS" +%s 2>/dev/null)
NOW_EPOCH=$(date +%s)
AGE=$((NOW_EPOCH - LATEST_EPOCH))
if [ "$AGE" -gt 120 ]; then
  echo "ALERT: no fresh snapshots — last write ${AGE}s ago ($LATEST_TS)"
  exit 2
fi

# 2. Register stability: compare the last N snapshots' key registers
#    (pv1=7, discharge=11, soc=5). If identical across a LONG span
#    (>= 5 minutes), data is frozen. Short identical spans are normal
#    at night (PV=0, discharge=0 while battery idles) — SOC still moves.
#    The 9/7 wedge was identical for 9.5 HOURS including SOC.
STABLE=$(docker exec lux-mariadb mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -N -e "
SELECT COUNT(DISTINCT CONCAT(JSON_EXTRACT(raw_registers,'$.7'),'|',JSON_EXTRACT(raw_registers,'$.11'),'|',(JSON_EXTRACT(raw_registers,'$.5') & 0xFF)))
FROM (SELECT raw_registers FROM lux_snapshots ORDER BY id DESC LIMIT 30) t;" 2>/dev/null)

if [ "$STABLE" = "1" ]; then
  # Check the time span of those 30 snapshots (should be ~3 min at 6s cadence)
  SPAN=$(docker exec lux-mariadb mysql -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -N -e "
  SELECT TIMESTAMPDIFF(SECOND, MIN(ts), MAX(ts)) FROM (SELECT ts FROM lux_snapshots ORDER BY id DESC LIMIT 30) t;" 2>/dev/null)
  if [ "$SPAN" -ge 300 ]; then
    echo "ALERT: data frozen — last 30 snapshots identical over ${SPAN}s (pv1/discharge/soc unchanged)"
    exit 2
  fi
fi

echo "OK: data fresh (last write ${AGE}s ago), values changing"
exit 0
