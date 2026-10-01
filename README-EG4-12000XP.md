# lux-mon for the EG4 12000XP

This guide covers the EG4 12000XP setup added on top of lux-mon: reading the
inverter through EG4's cloud, the four Grafana dashboards, importing history,
automatic gap-filling, and hosting the dashboards on the local network.

The general lux-mon documentation is in [README.md](README.md).

---

## Contents

1. [How data flows](#how-data-flows)
2. [First-time setup](#first-time-setup)
3. [Everyday commands](#everyday-commands)
4. [The dashboards](#the-dashboards)
5. [Reading the inverter through the EG4 cloud](#reading-the-inverter-through-the-eg4-cloud)
6. [Importing history (backfill)](#importing-history-backfill)
7. [Automatic gap-filling](#automatic-gap-filling)
8. [Hosting on the local network](#hosting-on-the-local-network)
9. [Settings reference](#settings-reference)
10. [Troubleshooting](#troubleshooting)

---

## How data flows

```
EG4 12000XP ── WiFi dongle ──► EG4 cloud (monitor.eg4electronics.com)
                                     │
                                     ├─ live:     collector (cloud_http transport), ~every 1–2 min
                                     └─ history:  .xls data export ── backfill / gap-fill
                                                        │
                                                        ▼
                              InfluxDB (bucket "luxmon") ──► Grafana (4 EG4 dashboards)
                              MariaDB (settings, snapshots)
                              MQTT (Home Assistant discovery)
```

**Why the cloud instead of the dongle.** The usual way to read an EG4 inverter
locally is the WiFi dongle's TCP port 8000. This dongle (serial `DJ…`) runs
EG4's encrypted **"E Wi-Fi ENC"** firmware and refuses all local connections,
so lux-mon reads the same data EG4's portal shows. EG4 says that firmware can't
be rolled back, so **don't press "Update Firmware"** on the dongle. Wired RS485
is the fully local alternative; it needs an adapter, and the dongle has to be
unplugged.

Everything runs in Docker on this Mac. These are the containers:

| Container | Purpose | Port |
|---|---|---|
| `lux-collector` | Reads EG4's cloud and writes data; runs gap-filling | none |
| `lux-grafana` | Dashboards | 3000 |
| `lux-influxdb` | Time-series storage | 8086 |
| `lux-mariadb` | Settings and snapshots | internal only |
| `lux-mosquitto` | MQTT broker | 1883 |
| `lux-api` | lux-mon web UI / API | 80 |

---

## First-time setup

All commands run from the repository root (the `lux-mon` folder).

1. **Credentials.** In `.env`, set your EG4 portal login. `.env` is git-ignored;
   never commit it. Use single quotes if the password contains `$`.

   ```
   LUX_CLOUD_USERNAME=you@example.com
   LUX_CLOUD_PASSWORD='your-password'
   LUX_TRANSPORT=cloud_http
   LUX_INVERTER_MODEL=eg4_12000xp
   LUX_INVERTER_SERIAL=<your inverter serial>
   LUX_TEMPERATURE_UNIT=fahrenheit
   LUX_GRAFANA_ROOT_URL=http://macstudio.local:3000/
   ```

   `docker/.env` is a symlink to this file, so Compose reads the same values.

2. **Settings in the database.** lux-mon's MariaDB settings take priority over
   `.env`. Make sure these values are set; you can change them in the web UI at
   `http://macstudio.local/`:

   | Setting | Value |
   |---|---|
   | `transport` | `cloud_http` |
   | `inverter_model` | `eg4_12000xp` |
   | `timezone` | `America/Los_Angeles` |
   | `temperature_unit` | `fahrenheit` |

3. **Build and start the stack:**

   ```bash
   docker compose -f docker/docker-compose.yml build collector
   ```

   ```bash
   docker compose -f docker/docker-compose.yml up -d
   ```

4. **Import the history** (once). See [Importing history](#importing-history-backfill).

5. **Open Grafana** at <http://macstudio.local:3000>. It opens on *Live power flow*.

---

## Everyday commands

Each command runs from the repository root.

Follow the collector log:

```bash
docker logs -f lux-collector
```

See container status:

```bash
docker compose -f docker/docker-compose.yml ps
```

Restart the collector, for example after editing `.env`:

```bash
docker compose -f docker/docker-compose.yml up -d --no-deps collector
```

Rebuild and restart after changing Python code:

```bash
docker compose -f docker/docker-compose.yml build collector && docker compose -f docker/docker-compose.yml up -d --no-deps collector api
```

Reload the dashboards after editing the JSON files in `grafana/dashboards/`:

```bash
docker compose -f docker/docker-compose.yml up grafana-init
```

Grafana picks up the changed files within about 30 seconds.

Run the tests:

```bash
python -m pytest -q
```

---

## The dashboards

Four dashboards live in `grafana/dashboards/`, linked to each other by a nav bar
at the top. All of them use Pacific time and refresh every minute.

### Live power flow (`eg4-flow`, home page)
- A diagram of Solar → Inverter → Loads (L1-N / L2-N) and Battery, with the
  generator / AC input. Arrows follow the direction of the battery flow.
- Headline numbers: battery SOC, solar, load, battery charging or discharging,
  and generator.
- **Data freshness**: *Live* means the last reading is under 5 minutes old,
  *Delayed* under 15 minutes, *Stale* beyond that.
- A 24-hour chart of Solar, Battery and Load.

### Energy (`eg4-energy`)
- **Today**: solar, consumption, battery charged and discharged,
  self-sufficiency. Days are split at local midnight.
- **Lifetime** totals.
- **Daily** bars: solar vs consumption, and battery in vs out.
- **Monthly** bars for the last 12 months.
- **Accumulated solar**:
  - lifetime total at each month end (bars that grow every month);
  - total for the selected time range;
  - a running total from the start of the selected range. Pick a range that
    starts before install day (8 May 2026) to see the whole lifetime curve.

### Battery (`eg4-battery`)
- SOC gauge and history, voltage, power, current, BMS charge and discharge
  limits, module count, and daily charged vs discharged.
- A panel lists what EG4's cloud doesn't provide for the 12000XP: per-cell
  voltages, cycle count and SOH.

### Solar strings & inverter (`eg4-pv`)
- PV1 vs PV2 power, voltage and current, and solar by hour of day.
- Inverter mode and status, EPS output voltage and frequency, L1/L2 balance,
  radiator temperatures and DC bus voltages.

**Colours.** The colours are fixed and were checked for colour-blind safety:
Solar `#c98500`, Battery `#199e70`, Load / consumption `#3987e5`. Generator
`#d95926` only ever appears in its own panel. This is an off-grid site, so grid
values are always 0.

**Notes.**
- Grafana has two datasources: `lux-mon-flux` (Flux, used by these dashboards)
  and `lux-mon` (InfluxQL).
- The cloud has no per-string energy counter, so `pv1_energy_*` holds the
  **total** solar yield. Every dashboard labels it "Solar".

---

## Reading the inverter through the EG4 cloud

The collector's `cloud_http` transport lives in `collector/comm/cloud_http.py`.

- It logs in to `monitor.eg4electronics.com` and polls runtime data every 60 s.
  It polls energy and battery data every 300 s.
- It converts the responses into the same register values the dongle would
  produce, so decoding and storage work exactly as with a local connection.
- It writes only when EG4 has published new data, roughly every 1–2 minutes.
- **Read-only by design.** An allowlist permits only the login, runtime, energy,
  battery and export endpoints. Nothing can change inverter settings, and
  quick-charge and automations are switched off under this transport.
- **Logins.** It logs in again automatically when EG4 silently ends a session.
  After a rejected password it pauses for longer each time, so a wrong password
  can't lock your account.
- **Privacy.** Credentials stay in `.env` only. They are never stored in the
  database or written to logs.

Things the cloud doesn't provide for the 12000XP:
- fault and warning codes;
- per-leg EPS voltages;
- generator power (that field is a counter on this model);
- inverter internal temperature (always 0);
- per-cell battery data.

---

## Importing history (backfill)

`python -m collector.backfill` downloads the portal's **data export** and writes
it to InfluxDB:
- The export is a `.xls` workbook with one sheet per day and one row about every
  4 minutes, fetched 10 days per request with 3 seconds between requests.
- Imported data uses the same names, units and temperature conversion as live
  data, so Grafana shows one continuous line.
- It **never writes at or after the first live reading**, and re-running a range
  overwrites the same points rather than duplicating them.
- It writes to InfluxDB only: never MariaDB, MQTT, alerts or rollups.

Run these inside the collector container.

Import everything from the first day EG4 has data:

```bash
docker exec lux-collector python -m collector.backfill --find-start
```

Import a specific date range:

```bash
docker exec lux-collector python -m collector.backfill --start 2026-05-08 --end 2026-09-26
```

See what would be imported, without writing anything:

```bash
docker exec lux-collector python -m collector.backfill --start 2026-09-01 --end 2026-09-10 --dry-run
```

Check that the mapping is right by comparing one day's export with live data
(writes nothing):

```bash
docker exec lux-collector python -m collector.backfill --compare-live 2026-09-26
```

**Checks before writing:**
- The time zone is checked against the inverter's clock. A wrong `timezone`
  setting would shift every point, so the run stops instead.
- If the unit tags (°C/°F) don't match the live data, the run stops.

History for this system starts on **2026-05-08**, install day.

---

## Automatic gap-filling

If the collector misses data, a background thread in the collector fills the
hole from EG4's export later. This covers the Mac being off, Docker being
stopped, or the internet being down. The code is in `collector/gapfill.py`.

- **Detecting gaps.** Every 3 hours, starting 10 minutes after the collector
  starts, it looks at the last 7 days. Any stretch longer than 10 minutes
  without a reading counts as a gap. It ignores the most recent 30 minutes,
  because EG4's export lags behind.
- **Filling gaps.** It downloads only the affected days and writes **only the
  rows strictly inside each gap**. Live data is never touched and nothing is
  duplicated.
- **Retrying.** A gap EG4 doesn't have yet is retried every 6 hours, and given
  up after 48 hours. It makes at most 6 downloads per run.
- **Records.** Each attempt is recorded in InfluxDB (measurement
  `luxmon_backfill`, tag `mode=gapfill`), so a restart doesn't re-download
  everything.

What can be recovered depends on what went offline:

| What went offline | Recoverable? |
|---|---|
| This Mac or Docker, while the house internet stays up | Yes, at full ~4-minute detail. The dongle keeps uploading to EG4. |
| The house internet | Usually. After about 20 minutes offline the dongle stores a reading every 5 minutes and uploads them when it reconnects. The first ~20 minutes may be missing. |
| The dongle or inverter has no power | No. Nothing was recorded anywhere. |

Run a fill by hand:

```bash
docker exec lux-collector python -m collector.backfill --fill-gaps
```

Show the gaps without filling them:

```bash
docker exec lux-collector python -m collector.backfill --fill-gaps --dry-run
```

Look further back, or treat shorter holes as gaps:

```bash
docker exec lux-collector python -m collector.backfill --fill-gaps --lookback-days 14 --min-gap-min 6
```

To turn automatic gap-filling off, set `LUX_GAPFILL_ENABLED=false` in `.env`
and restart the collector.

---

## Hosting on the local network

The dashboards are served at **<http://macstudio.local:3000>**. If a device can't
resolve `.local` names (some Android phones and TVs), use the Mac's IP instead:
`http://<mac-ip>:3000` (find the IP in System Settings → Network).

How it's set up:
- **Hostname.** The Mac's local hostname is `macstudio`. It was set once with
  `sudo scutil --set LocalHostName macstudio`, or in System Settings → General
  → Sharing → Local hostname.
- **Grafana's address.** Grafana knows its public address through
  `LUX_GRAFANA_ROOT_URL` in `.env`, which feeds `GF_SERVER_ROOT_URL`.
- **Home page.** *Live power flow* is the home page, set by
  `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH` in `docker/docker-compose.yml`.
- **Access.** Visitors get read-only access with no login. Editing needs the
  Grafana admin login (`LUX_GRAFANA_ADMIN_PASSWORD`).

To keep it available:
- The Mac never sleeps (`pmset` sleep 0) and restarts after a power failure.
- Docker Desktop is a login item, and every container restarts automatically.
- After a reboot, the dashboards come back once a user logs in, because Docker
  Desktop runs per-user.
- If Docker starts all containers at once, the collector waits up to 2 minutes
  for MariaDB, so it never starts with the wrong settings.
- Optional: a DHCP reservation for the Mac's IP in UniFi keeps the IP address
  stable.

**Security note.** These are also reachable by anyone on the local network,
with no extra password:
- the lux-mon web UI (port 80);
- MQTT (port 1883, anonymous);
- the InfluxDB login page (port 8086).

Anyone on the network can change lux-mon's settings through the web UI or MQTT.
If nothing else on your network needs MQTT or InfluxDB, consider binding them to
`127.0.0.1` in `docker/docker-compose.yml`.

---

## Settings reference

These go in `.env`. Restart the collector after changing them.

| Variable | Default | Meaning |
|---|---|---|
| `LUX_TRANSPORT` | `tcp_active` | Use `cloud_http` for this setup. |
| `LUX_INVERTER_MODEL` | `eg4_6000xp` | Set to `eg4_12000xp`. |
| `LUX_INVERTER_SERIAL` | none | The inverter serial, which the cloud transport uses. |
| `LUX_CLOUD_USERNAME` / `LUX_CLOUD_PASSWORD` | none | EG4 portal login. |
| `LUX_CLOUD_POLL_SEC` | `60` | Runtime poll interval (minimum 30). |
| `LUX_CLOUD_ENERGY_SEC` | `300` | Energy and battery poll interval. |
| `LUX_CLOUD_BASE_URL` | `https://monitor.eg4electronics.com` | Portal address. |
| `LUX_TEMPERATURE_UNIT` | `fahrenheit` | Fallback unit; keep it equal to the `temperature_unit` setting. |
| `LUX_GAPFILL_ENABLED` | `true` | Automatic gap-filling on or off. |
| `LUX_GAPFILL_INTERVAL_MIN` | `180` | Minutes between gap checks (minimum 30). |
| `LUX_GAPFILL_LOOKBACK_DAYS` | `7` | How far back to look (1–30). |
| `LUX_GAPFILL_MIN_GAP_MIN` | `10` | Shortest hole counted as a gap (minimum 6). |
| `LUX_GRAFANA_ROOT_URL` | `http://localhost:3000/` | Grafana's address for other devices. |

Remember that the MariaDB settings (`transport`, `inverter_model`, `timezone`,
`temperature_unit`) take priority over `.env`.

---

## Troubleshooting

**The dashboard says "Stale", or the charts stopped.**
- Check the log with `docker logs --tail 50 lux-collector`.
- `EG4 cloud rejected the login` means you should fix the credentials in `.env`,
  then restart the collector.
- Network errors usually mean the internet or EG4's portal is down. The
  collector recovers on its own, and gap-filling fills the hole later.

**There's a gap in a chart.**
- Run `docker exec lux-collector python -m collector.backfill --fill-gaps`.
- If the report says "no rows", EG4 doesn't have the data (yet).

**Two lines for one temperature.**
- This means some readings were stored in °C and some in °F.
- Make sure the `temperature_unit` setting and `LUX_TEMPERATURE_UNIT` agree, and
  restart the collector.
- A warning appears in the log whenever the unit changes.

**I want local data instead of the cloud.**
- If the dongle ever accepts connections on port 8000 again, switch `transport`
  to `tcp_active` and set `LUX_DONGLE_HOST` to the dongle's IP. The dashboards
  don't need to change.
- A Waveshare RS485-to-Ethernet gateway on the inverter's RS485 jack would work
  too (pin 7 = B, pin 8 = A). The dongle has to be unplugged for this, which
  stops EG4 cloud reporting, and lux-mon needs a Modbus TCP transport added.

**After editing a dashboard in the Grafana UI.** UI edits are overwritten the
next time `grafana-init` runs. Make permanent changes in the JSON files in
`grafana/dashboards/`.
