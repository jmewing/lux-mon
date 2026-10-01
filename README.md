# lux-mon

> **EG4 12000XP setup** (EG4 cloud transport, dashboards, history backfill, gap-filling, LAN hosting): see [README-EG4-12000XP.md](README-EG4-12000XP.md).

**Local monitoring for LuxPower-based inverters — no cloud required (local-first; optional read-only EG4 cloud transport).**

Works with EG4, LuxPower, and any rebranded inverter using the LuxPower WiFi dongle protocol (TCP port 8000). Also supports RS-485 battery BMS monitoring (EG4 A5/5A, JK BMS, generic Modbus RTU).

## What It Does

- **Passively listens** to your inverter's WiFi dongle — zero bus contention
- **Actively polls** as a fallback for non-broadcasting dongles
- **Reads the EG4 cloud portal** (read-only, optional) when the dongle refuses all local TCP — see [EG4 cloud transport](#eg4-cloud-transport-cloud_http)
- **Stores** time-series data in **MariaDB/MySQL** (InfluxDB optional, both can run together)
- **Exposes** a **REST API** for scripting, morning briefings, Home Assistant, etc.
- **Streams live snapshots** over a **WebSocket** (`/ws`) so the web dashboard updates instantly
- **Writes inverter settings** via a safe holding-register write path with clamping and verification
- **SolarAssistant-style automations** — rule table, battery SOC control, battery protection, and notifications, with global dry-run for safety (38/57 settings mapped)
- **Quick Charge / Generator Charge** — one-shot timed grid charge with automatic restore
- **Solar PV forecast** — weather-based (Open-Meteo) with historical calibration, persisted to MariaDB
- **Alerts** — SOC/temperature/grid-loss thresholds with SMTP + webhook notifications
- **RS-485 BMS monitoring** — EG4 A5/5A, JK BMS, and generic Modbus RTU drivers
- **Home Assistant** — native REST integration, MQTT auto-discovery, and energy-dashboard sensors
- **Runs anywhere** — your Mac, a Raspberry Pi, a Docker container
- **Dashboard-ready** — built-in web UI plus Grafana dashboards

## Status

This project is actively running on private hardware monitoring an EG4 6000XP inverter.

| Component | Status | Details |
|-----------|--------|---------|
| Collector | ✅ Live | 111 input registers decoded, writes to MariaDB + InfluxDB + MQTT |
| REST API | ✅ Live | FastAPI on port 80, systemd-managed |
| Storage | ✅ Live | MariaDB, InfluxDB v2, hourly energy rollups |
| Dashboard | ✅ Live | Web UI with gauges, charts, battery, totals, settings, automations, schedule editor |
| Active polling | ✅ Built | Fallback for non-broadcasting dongles |
| EG4 cloud transport | ⚠️ Built, unverified live | `transport=cloud_http`: read-only HTTPS polling of monitor.eg4electronics.com for dongles that refuse local TCP |
| Runtime settings | ✅ Live | DB-backed, editable from dashboard ⚙️ tab |
| Docker image | ✅ Published | Stable: `jmewing/lux-mon:v1.0.1`; Beta: `jmewing/lux-mon:v1.1.0-beta.1` (amd64 + arm64) on Docker Hub and GHCR |
| RS-485 / BMS | ✅ Live | `lux-mon-rs485` daemon, EG4 A5/5A battery BMS driver deployed |
| Alerts | ✅ Live | SMTP + webhook notifications, rate-limited, UI configurable |
| Solar forecast | ✅ Live | Open-Meteo weather forecast + historical calibration (v1.2.1) |
| Quick charge | ✅ Live | Timed grid charge with restore-on-expiry |
| Home Assistant | ✅ Integrated | Native REST integration + MQTT auto-discovery + energy sensors |
| Schedule editor | ✅ Fixed | Reads/writes actual inverter holding registers (Grid charge + AC first) |
| Automations | ✅ Live | SolarAssistant-style rule table, SOC control, battery protection, notifications (global dry-run; 38/57 settings mapped) |
| Backup/restore | ✅ Built | Nightly systemd timer + one-command restore |
| Grafana | ✅ Built | Pre-loaded dashboards and data source |

### Supported inverter models

lux-mon speaks the **Luxpower SNA** and **18KPV** register families. EG4
inverters are Luxpower rebadges, so the full EG4 lineup is supported via two
families. Each model below maps to one of those two register maps.

> **Verified vs. unverified.** A model is **✅ Verified** only when its
> register map has been confirmed against a live unit (real captures, not just
> the vendor's protocol document). **⚠️ Unverified** models share a register
> map with a verified sibling but have *not* been independently confirmed —
> they may expose extra data fields or decode some registers differently.
> Treat unverified models as best-effort and report any discrepancies.

| Model | Setting value | Register family | Status |
|-------|---------------|-----------------|--------|
| EG4 6000XP | `eg4_6000xp` | SNA | ✅ Verified (reference) |
| Luxpower SNA | `luxpower_sna` | SNA | ✅ Verified |
| EG4 12000XP | `eg4_12000xp` | SNA | ⚠️ Unverified |
| EG4 6500EX-48 | `eg4_6500ex` | SNA | ⚠️ Unverified |
| EG4 3000EHV-48 | `eg4_3000ehv` | SNA | ⚠️ Unverified (legacy/discontinued) |
| LuxPower LXP 6K | `lxp_6k` | SNA | ⚠️ Unverified |
| BigBattery SNA-US 6K | `bigbattery_sna_6k` | SNA | ⚠️ Unverified |
| EG4 18KPV | `eg4_18kpv` | 18KPV | ⚠️ Unverified (document-derived) |
| EG4 12kPV | `eg4_12kpv` | 18KPV | ⚠️ Unverified |
| LuxPower LXP 12K | `lxp_12k` | 18KPV | ⚠️ Unverified |
| LuxPower LXP 18K | `lxp_18k` | 18KPV | ⚠️ Unverified |
| Fortress Envy True 12K | `fortress_envy_12k` | 18KPV | ⚠️ Unverified |

- **SNA family** is the validated reference — the 6000XP map was
  reverse-engineered from live captures. The 12000XP, 6500EX-48, and
  3000EHV-48 (legacy, discontinued) share the exact same register layout.
- **18KPV family** is derived from the official EG4 Modbus protocol document
  and has **not** been validated against a live unit. The 12kPV, LXP 12K/18K,
  and Fortress Envy True 12K share the 18KPV map.
- **FlexBOSS18/21 and GridBOSS** are a *new* platform (not a Luxpower SNA
  rebadge) with an unknown register map. They are intentionally **not**
  aliased to either family — doing so would silently decode garbage. A
  dedicated driver is needed once their protocol is documented.

#### Help verify an unverified model

Running an unverified model? We'd love to confirm it against your hardware.
If your inverter is on the list above but marked ⚠️ Unverified, please:

1. Set `LUX_INVERTER_MODEL` to your model's setting value and run lux-mon.
2. Compare the decoded values against your inverter's own display / the
   vendor's monitoring app.
3. If anything looks off (wrong values, missing fields, extra registers),
   **open a GitHub issue** or **email us at
   [jaspertan219@gmail.com](mailto:jaspertan219@gmail.com)** with a redacted
   log excerpt so we can validate and correct the register map.

Confirmed-good reports let us flip a model from ⚠️ Unverified to ✅ Verified
and document any model-specific differences (e.g. extra data fields the
6000XP doesn't expose).

### In progress / near-term

- **Inverter Edit Mode page** — manual Read/Set for every editable EG4 6000XP holding register, mirroring the EG4 Monitor Maintenance tab.
- **Holding register map corrections** — align `collector/protocol.py` with the `LXP_REGISTERS.txt` reference, add missing registers, fix ranges, and correct AC charge current to register 168.

### Roadmap

- Generator and AC-coupled charge support (generator-charge register path is stubbed)
- A general automation/rule engine (condition → setting write) — see `docs/solarassistant-automation-map.md` for the SolarAssistant reference field map
- More inverter models via pluggable Modbus RTU drivers (FlexBOSS/GridBOSS need a dedicated register map)
- Forecast.Solar provider (listed in settings, not yet wired)

## Architecture

```
EG4/LuxPower Inverter → WiFi Dongle (TCP :8000)
                              │
                              ▼ (Modbus TCP / passive listen)
                    ┌─────────────────┐
                    │  lux-collector   │  Python
                    │  (protocol      │
                    │   parser)       │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
       ┌──────────┐  ┌──────────┐  ┌──────────┐
       │ MariaDB  │  │ InfluxDB │  │  MQTT    │
       │          │  │ (Solar   │  │ (Home    │
       │          │  │ Assistant│  │ Assistant│
       │          │  │ schema)  │  │ discover)│
       └────┬─────┘  └────┬─────┘  └────┬─────┘
            │             │             │
            └─────────────┴─────────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │   REST API       │  FastAPI
                 │  /api/status     │  port 80
                 │  /api/history    │
                 └─────────────────┘

RS-485 BMS (optional) → lux-mon-rs485 daemon → same backends

transport=cloud_http (dongles that refuse local TCP):
Inverter → WiFi Dongle → EG4 cloud ◄── HTTPS, read-only polling ── lux-collector → same backends
```

## Quick Start

### One-command install (Debian / Ubuntu / Raspberry Pi OS)

The installer sets up everything: MariaDB, Python venv, InfluxDB, Mosquitto, Grafana,
and systemd services for the collector and API.

```bash
git clone https://github.com/jmewing/lux-mon.git
cd lux-mon

LUX_INSTALL_DIR=/opt/lux-mon \
LUX_USER=$(whoami) \
LUX_MARIADB_PASSWORD='luxmon' \
LUX_INFLUX_ADMIN_PASSWORD='choose-a-password' \
LUX_DONGLE_HOST=192.168.1.100 \
bash scripts/install.sh
```

After install:
- API: http://YOUR-HOST:80/api/status
- Grafana: http://YOUR-HOST:3000/grafana/d/eg4-flow
- Add an Apache/Nginx reverse proxy on port 80 if desired.

### Docker Compose (full stack)

If you prefer containers, copy `docker/.env.example` to `.env`, fill in your
dongle IP and passwords, then run:

```bash
cp docker/.env.example .env
# edit .env
docker compose -f docker/docker-compose.yml up -d --build
```

This builds the lux-mon image from source, then starts MariaDB, InfluxDB,
Mosquitto, collector, API, and Grafana with pre-loaded dashboards. To use a
pre-built image instead, set `LUX_IMAGE` in `.env` and omit `--build`.

Pre-built multi-arch images (amd64 + arm64) are published to:

- **Stable (v1.0.x):**
  - **Docker Hub:** `jmewing/lux-mon:v1.0.1`
  - **GitHub Container Registry:** `ghcr.io/jmewing/lux-mon:v1.0.1`
- **Development / beta (v1.1.0 inverter-write preview):**
  - **Docker Hub:** `jmewing/lux-mon:v1.1.0-beta.1`
  - **GitHub Container Registry:** `ghcr.io/jmewing/lux-mon:v1.1.0-beta.1`

`latest` always points to the most recent stable release.

Example `.env` for the stable image:

```bash
LUX_IMAGE=jmewing/lux-mon:v1.0.1
```

Example `.env` for the beta image:

```bash
LUX_IMAGE=jmewing/lux-mon:v1.1.0-beta.1
```

Then run:

```bash
docker compose -f docker/docker-compose.yml up -d
```

See `docker/README.md` for details.

### Manual install

```bash
# Clone
git clone https://github.com/jmewing/lux-mon.git
cd lux-mon

# Install Python deps
pip install -r docker/requirements.txt

# Configure via environment (copy example and edit)
cp .env.example .env
# edit .env with your DB credentials, dongle IP, InfluxDB/MQTT options

# Optional: install InfluxDB + Mosquitto + Grafana (Debian/Ubuntu)
LUX_INFLUX_ADMIN_PASSWORD='choose-a-password' bash scripts/setup-grafana-stack.sh

# Run the collector
python -m collector
```

For a config-file approach you can also copy `config.example.py` to `config.py` and pass `--config config.py`.

### EG4 cloud transport (cloud_http)

Some EG4 WiFi dongles (for example on the **E Wi-Fi ENC** firmware) refuse every
local TCP connection, so neither `tcp_active` nor `tcp_passive` can reach them.
For those, lux-mon can read the same data from the EG4 cloud portal
(`monitor.eg4electronics.com`) that the dongle already uploads to:

```
Inverter → WiFi dongle → EG4 cloud  ←(HTTPS, read-only)─  lux-collector → MariaDB / InfluxDB / MQTT
```

**What to expect**

- The portal lags the inverter by roughly **20 s to 5 min**. The collector
  polls the runtime every `LUX_CLOUD_POLL_SEC` (default 60 s, minimum 30 s)
  and writes **one snapshot per new portal upload** (a later portal
  `serverTime`); unchanged, older or offline (`lost`) payloads are skipped,
  never re-stored as fresh. After a (re)start, an upload older than
  3 × `LUX_CLOUD_POLL_SEC` + 300 s is not written; the collector waits for the
  next one. Each new upload also fetches the battery info; the energy
  counters are fetched every `LUX_CLOUD_ENERGY_SEC` (default 300 s) and right
  after the plant's local midnight. The hourly energy rollup counts each
  snapshot for the time since the previous upload (at most 15 min).
- If the portal is unreachable the collector retries after 1, 2 and 4 s, then
  after 1, 2 and 4 poll intervals, and from then on every 5 min until it
  recovers (HTTP 429 skips the fast retries). If the
  portal **rejects the login**, it waits 15 min, 1 h, 6 h and then a day between
  attempts (so a wrong or changed password is not retried over and over),
  and `scripts/lux-mon-freeze-check.sh` does not auto-restart it meanwhile.
  After fixing the credentials, recreate the collector (step 3 below).
- **Read-only:** only the login and three read endpoints
  (`getInverterRuntime`, `getInverterEnergyInfo`, `getBatteryInfo`) are ever
  called — never `remoteRead`/`remoteSet`. Quick charge, automations and
  holding-register reads/writes (`/api/holding*`, `/api/quick-charge/start|stop`)
  are disabled while `transport=cloud_http` (the API answers 409).
- lux-mon never opens a socket to the dongle in this mode.

**Setup**

1. Put your EG4 portal login in `.env` (env-only, never stored in MariaDB):
   `LUX_CLOUD_USERNAME=...` and `LUX_CLOUD_PASSWORD=...` (single-quote the
   password if it contains `$`).
2. `transport`, `inverter_model` and `inverter_serial` are DB-authoritative
   for the collector — editing `.env` alone is ignored once the DB rows
   exist. `transport` has no field on the Configuration page, so set all
   three through the API:

   ```bash
   curl -X PUT http://your-server/api/settings/transport -H 'Content-Type: application/json' -d '{"value": "cloud_http"}'
   curl -X PUT http://your-server/api/settings/inverter_model -H 'Content-Type: application/json' -d '{"value": "eg4_12000xp"}'
   curl -X PUT http://your-server/api/settings/inverter_serial -H 'Content-Type: application/json' -d '{"value": "YOUR_INVERTER_SERIAL"}'
   ```

   Also set the same values in `.env`: `LUX_TRANSPORT=cloud_http`,
   `LUX_INVERTER_MODEL=eg4_12000xp` and `LUX_INVERTER_SERIAL=...`. The api
   container shows an environment value in preference to the DB row (and
   Compose defaults `LUX_INVERTER_MODEL` to `eg4_6000xp`), and the
   Configuration page's **Save** writes every field it shows, including the
   inverter model, back to the DB. If the api container still had the old
   model, a later Save (for example to change the timezone) would switch the
   collector back to the 6000XP driver.
3. Recreate **both** the collector and the api so they pick up `.env`:
   `docker compose -f docker/docker-compose.yml up -d collector api`.
   Then check that the Configuration page shows the new inverter model and
   serial. The first poll logs `First EG4 cloud update: serverTime=...
   deviceTime=..., collector UTC now ...` — check that `serverTime` matches UTC.

**Mapping caveats**

- The cloud has no per-string energy: the **PV energy total** is stored in the
  PV1 slot (`pv1_energy_today` / `pv1_energy_total`); PV2/PV3 energy stay empty.
- **No fault or warning codes** are available from the cloud runtime, so
  `fault_code` / `warning_code` are absent (not zero).
- `load_energy_today` / `load_energy_total` are the portal's server-computed
  "usage", not the inverter's own load counter.
- `eps_power_l1` / `eps_power_l2` are cloud-reported per-leg values and may not
  add up to `eps_power` (the combined backup output).
- **SOH is left blank** when the battery reports no module array (typical for
  the 12000XP), instead of publishing a fake 0 %; `/api/batteries` reports
  `soh_pct: null` for a module that does not report its SOH.
- `battery_current` comes from the battery info fetched with each upload. If
  that fetch fails, it is left out of the snapshot rather than repeated next
  to newer charge/discharge power.
- The inverter temperature is not available from the 12000XP cloud feed (the
  portal sends a constant 0), so `temp_inverter` is absent there.
- Each snapshot also writes one `luxmon_cloud` InfluxDB point (tags
  `data_source=cloud`, `serial`) with the portal `server_time`, `data_age_s`
  (portal lag), status/firmware text and cloud-only values such as
  `smart_load_power`, `eps_load_power`, `grid_load_power` and `bat_power`.
  The load split and `consumption_power` are only written when the portal
  flags them as shown (`smartLoadInverterEnable`, `epsLoadPowerShow`,
  `gridLoadPowerShow`, `hideConsumption`); on the 12000XP they are placeholder
  zeros and are left out.
  "Was this time range cloud-fed?" = "does `luxmon_cloud` have points there?".

For an EG4 12000XP, suggested dashboard gauge settings are `pv_max_power`
24000, `grid_max_power` 24000, `eps_max_power` 12000, `charge_max_power` 12000
and `discharge_max_power` 12000.

### Backfilling history from the EG4 portal

The EG4 portal keeps the inverter's whole history. `python -m collector.backfill`
downloads it through the portal's data export (a `.xls` workbook, one sheet per
day, one row per ~4 min; at most 10 days per request, 3 s apart) and writes it
to InfluxDB exactly as the cloud collector would have: the same
`luxmon_register` names and unit tags (including °F when `temperature_unit` is
fahrenheit) and the same SolarAssistant-style measurements, so Grafana shows one
continuous series. It never writes at or after the first live point, and never
writes MariaDB, MQTT, alerts, the hourly energy rollup or `luxmon_cloud`.
Re-running a range overwrites the same points (same series and timestamp).

The tool ships in the collector image and needs `xlrd`, so rebuild and recreate
the collector once: `docker compose -f docker/docker-compose.yml build collector`
then `docker compose -f docker/docker-compose.yml up -d collector`. It uses the
collector's environment (EG4 login, InfluxDB) and DB settings (serial, model,
`temperature_unit`, `timezone`); nothing is logged about the credentials.

```bash
# 1. Check the mapping on a day that has both export rows and live data
#    (per-field matched count and mean/max difference; writes nothing):
docker exec lux-collector python -m collector.backfill --compare-live 2026-09-26

# 2. Find the first day with data and dry-run everything up to the first live point:
docker exec lux-collector python -m collector.backfill --find-start --dry-run

# 3. Backfill (--end defaults to the day of the first live point; add
#    --tz America/Los_Angeles etc. if the timezone setting is not the plant's zone):
docker exec lux-collector python -m collector.backfill --find-start
#    or a range of plant-local days (inclusive):
docker exec lux-collector python -m collector.backfill --start 2025-06-01 --end 2025-12-31
```

Other options: `--save-xls DIR` keeps the downloads (inside the container; copy
them out with `docker cp`), `--from-xls FILE` re-reads a saved export without the
network (writing from it needs `--tz`), `--request-delay SEC` (default 3),
`--cutoff DATETIME` moves the first-live-point cutoff earlier (a later one also
needs `--allow-live-overlap`, since those rows would sit between live points),
`--tz` / `--model` / `--temp-unit` override the DB settings (a write run refuses
a `--temp-unit` that differs from the collector's setting and any unit tag that
differs from the live data; with MariaDB unreachable it needs `--model` and
`--temp-unit`), `--verbose` prints sample decoded rows.

- The export's `Time` column is the inverter's local clock. It is converted with
  `--tz`, or else the `timezone` setting. That setting only drives schedules and
  defaults to `America/Chicago`, so a portal write run without `--tz` first
  compares the inverter's current UTC offset (portal `deviceTime` vs
  `serverTime`) with the zone and stops on a mismatch, or when the offset cannot
  be read. A `--from-xls` write run cannot check it offline and needs `--tz`. A
  wrong zone shifts every point, and re-running with the right one does not
  remove them. DST fall-back hours are told apart by row order.
- Each 10-day window also writes one `luxmon_backfill` point (tag `run_id`;
  fields `rows`, `snapshots`, `points`, `window`, `source="eg4_export"`,
  `live_start`, `cutoff`). `live_start` and `cutoff` are written before the
  window's data, so an interrupted run leaves the cutoff in place and re-running
  the range is safe. Everything the backfill wrote lies before `live_start`,
  unless `--allow-live-overlap` was used.
- Compared with live data: the export adds `soh` and `cell_temp_max` (the BMS
  values the cloud runtime lacks, written only when non-zero), has no
  `battery_count`, and `state` comes from the portal's status text
  (`Standby`, `PV Charge`, `Battery Grid off`, `PV&Battery Grid off`; any other
  text leaves `state` out and is listed in the summary). Energy follows the live
  semantics: PV total in `pv1_energy_*`, and `load_energy_*` = backup-port (EPS)
  energy + grid-to-user energy, the portal's "Consumption".
- The portal's energy chart endpoints (`analyze/energy/*Column`) return zeros for
  off-grid units and are not used.

#### Automatic gap-filling

With the EG4 portal login in `.env` (`LUX_CLOUD_USERNAME` / `LUX_CLOUD_PASSWORD`,
any transport except `replay`), the collector also repairs holes in the **live**
InfluxDB data on its own. A background thread looks at the `luxmon_register`
`soc` points of the last `LUX_GAPFILL_LOOKBACK_DAYS` days: two consecutive points
more than `LUX_GAPFILL_MIN_GAP_MIN` minutes apart are a gap, and so is the time
from the last point to 30 min ago. It downloads only the plant-local days those
gaps span (the same export, windows and checks as the backfill above) and writes
only the export rows that fall **inside** a gap, at least 60 s from the live
points around it and never in the last 30 min (the export and the dongle's
buffered upload lag behind). Only time after the first live point counts;
anything earlier is the one-off backfill's job. The points are the same as the
backfill writes (same names and unit tags, no `luxmon_cloud`), InfluxDB only —
never MariaDB, MQTT, alerts or the hourly energy rollup, so anything read from
MariaDB still shows the outage.

What EG4 has depends on what was down:

| Outage | What EG4 has | Result |
|--------|--------------|--------|
| lux-mon (the host) offline, dongle online | everything | filled on the first check after lux-mon is back |
| house internet down | nothing until the dongle reconnects; once offline for ~20 min it stores a reading every 5 min and uploads them on reconnect (buffer size undocumented) | filled after the internet is back, usually without the first ~20 min |
| inverter or dongle without power | nothing | stays a gap |

A gap whose days had no export rows is retried at most every 6 h and given up
(one INFO log line) once its end is more than 48 h old. A gap that was still
open when checked is tried again as soon as the live data comes back, since
that is when the dongle uploads its buffer. Each attempt is recorded in
`luxmon_backfill` (tags `mode=gapfill`, `run_id`; fields `gap_start`,
`gap_end`, `attempted_at`, `status` = `filled` / `empty` / `gave_up`,
`rows_written`, `trailing`, `window`), so a restart does not download
everything again. Each run downloads at most 6 export windows, logs in with its
own portal session (never the live transport's) and only when a gap is due;
after a rejected login it leaves the portal alone for 24 h, and it does not log
in while the live transport's login is being rejected. The inverter-clock time
zone check and the unit-tag check of the backfill apply to every run.

| Variable | Default | Description |
|----------|---------|-------------|
| `LUX_GAPFILL_ENABLED` | `true` | Turn the background gap-filler off with `false` |
| `LUX_GAPFILL_INTERVAL_MIN` | `180` | Minutes between checks (minimum 30; the first is 10 min after the collector starts) |
| `LUX_GAPFILL_LOOKBACK_DAYS` | `7` | How many days back to look for gaps (1-30) |
| `LUX_GAPFILL_MIN_GAP_MIN` | `10` | Minutes between two live points that count as a gap (minimum 6, since export rows are ~4-5 min apart) |

These are env-only (not in the Settings page); recreate the collector after a
change. To look or fill by hand (the dry run downloads but writes nothing and
records nothing; a manual run is refused while the background thread is busy):

```bash
docker exec lux-collector python -m collector.backfill --fill-gaps --dry-run
docker exec lux-collector python -m collector.backfill --fill-gaps --lookback-days 14 --min-gap-min 15
```

It prints each gap in plant-local time with what it did (rows written, no rows
yet, waiting until the next retry, given up) and the number of downloads.

## REST API

The API server runs on port 80 and provides:

| Endpoint | Description |
|----------|-------------|
| `GET /api/status` | Latest snapshot with all decoded registers |
| `GET /api/summary` | Compact key metrics for dashboards |
| `GET /api/history?minutes=60&fields=soc,battery_voltage` | Time-series data |
| `GET /api/health` | Health check |
| `GET /api/energy` | Energy totals and hourly rollups |
| `GET /api/forecast?hours=48` | Stored solar PV forecast (predicted + corrected watts) |
| `GET /api/alerts` | Recent alert events |
| `GET /api/alerts/live` | Current alert states (for HA binary sensors) |
| `GET /api/settings` | All runtime settings |
| `GET /api/settings/controllable` | Settings exposed as HA entities |
| `GET /api/settings/{name}` | Single setting value |
| `PUT /api/settings/{name}` | Update a setting (JSON body: `{"value": "..."}`) |
| `GET /api/holding` | Read actual holding-register values directly from the inverter |
| `GET /api/holding/{name}` | Read a single named holding register from the inverter |
| `PUT /api/holding/{name}` | Write a single named holding register to the inverter |
| `GET /api/automation/types` | List automation types (rule_table, battery_soc, battery_protection, notify) |
| `GET /api/automation/conditions` | List condition dimensions for automation rules |
| `GET /api/automation/settings` | List the 50 SolarAssistant-style writable settings with register mapping |
| `GET /api/automation/rules` | List automations + global enable + dry-run |
| `POST /api/automation/rules` | Replace the full automation configuration |
| `DELETE /api/automation/rules/{id}` | Delete one automation |
| `POST /api/automation/enable` | Globally enable/disable the automation engine |
| `POST /api/automation/dry-run` | Globally enable/disable dry-run mode |
| `POST /api/automation/rules/{id}/disable` | Temporarily disable an automation for N minutes |
| `POST /api/automation/test` | Dry-run evaluate automations against the latest snapshot |
| `GET /api/automation/log` | Recent automation actions / dry-runs |
| `GET /api/automation/registers` | List writable holding registers (used by the schedule editor) |
| `GET /api/quick-charge/status` | Current quick-charge state and defaults |
| `POST /api/quick-charge/start` | Start a timed quick charge (JSON: `{"minutes": 60}`) |
| `POST /api/quick-charge/stop` | Stop an active quick charge, restoring the prior value |
| `POST /api/backup` | Create a backup archive |
| `GET /api/backups` | List backup archives |
| `POST /api/prune` | Prune old detail data |
| `GET /api/storage` | Show DB table sizes and disk usage |
| `WS /ws` | Live snapshot WebSocket stream |

The built-in dashboard (`/`) includes a **Power Management** page with three tools:

- **Automations** — SolarAssistant-style condition → action rules with a global dry-run toggle.
- **Timer Schedule** — Grid charge + AC first time slots.
- **Quick Charge** — one-shot timed grid charge.

### Automations

Click **Add Automation** to create a rule. Choose from four types:

- **Rule table** — pick a setting, add up to two conditions, set the action value, and optionally a restore value.
- **Battery state of charge control** — time-of-day + SOC thresholds to choose grid vs battery source.
- **Battery protection** — if SOC drops below a threshold, write a shutdown voltage; restore when SOC recovers.
- **Send notification** — when a condition is met, send an email/webhook via the existing alert notifier.

Conditions include time-of-day, day-of-week, month-of-year, battery SOC/voltage/current, grid voltage/frequency, PV progress, load power, and inverter/battery temperatures.

**Safety:** the automation engine has a **global dry-run toggle** (`automation_global_dry_run`) that is ON by default. While dry-run is enabled, the engine evaluates every rule and logs what it *would* write, but it never sends a Modbus command. Turn dry-run OFF only when you are confident the rules behave as intended.

Individual automations can be enabled/disabled and temporarily disabled for 30 minutes up to 24 hours from the dashboard card. Saved automations are stored as JSON in the `automations_v2` setting.

### Schedule editor

The schedule editor writes directly to the inverter's holding registers via
`PUT /api/holding/{name}`. It covers two groups:

- **Grid charge** — `ac_charge_period_*` (3 slots) plus charge power and SOC limit
- **AC first** — `ac_first_period_*` (3 slots)

Time slots are encoded as `(minute << 8) | hour` (minute in the high byte,
hour in the low byte), matching the LuxPower register format. Writes are sent
sequentially (not in parallel) because the dongle rejects bursts of
simultaneous write connections.

**Design note:** Readable values (SOC, voltage, current, power) are telemetry
and are included automatically. Set values (charge current, SOC limits, time
slots, modes) are only written when explicitly changed via the website. lux-mon
never writes a setting just because it happens to be readable.

### Quick Charge

lux-mon implements the inverter's native quick-charge action using the correct
registers reverse-engineered from SolarAssistant's traffic:

- **`quick_charge_duration`** (register 234 / `0x00EA`) — the actual charge
  controller. Setting it to N minutes starts charging for N minutes; `0` stops.
- **`function_enable_5`** (register 233 / `0x00E9`) — a bitfield; bit 0 is the
  quick-charge start toggle (read-modify-write so other bits are preserved).

Correct semantics (confirmed via tcpdump of SolarAssistant):

- The **duration** register is the charge controller — `0` means "charge 0
  minutes" = no charge / stop.
- **Bit 0 of the enable bitfield** only toggles the mode; it does NOT start
  charging on its own. Enabling with duration `0` does nothing.
- **Start:** write duration first, then set bit 0 of the enable bitfield.
- **Stop:** clear bit 0 of the enable bitfield AND clear the duration.

This differs from the earlier implementation, which drove
`ac_charge_battery_current` (register 168) — that is the *grid charge current*,
not the quick-charge toggle.

The quick-charge control lives on the **main dashboard** (a button with a
▼-toggle for a custom duration, 1–240 minutes, default 60). State is persisted
in the `lux_settings` table so it survives collector restarts, and all actions
are logged. Writes use the shared holding-register write helpers (fresh socket,
echo verification, clamping).

### Solar PV forecast

lux-mon can forecast PV production using a weather-based model (Option A) with optional historical calibration (Option B):

1. Fetch hourly weather (cloud cover + shortwave radiation) from **Open-Meteo** (free, no API key).
2. Compute a clear-sky PV power curve using **pvlib** (sun position + clear-sky irradiance transposed onto the tilted array plane).
3. Scale by a cloud factor, then apply bifacial back-side gain.
4. Optionally correct today's forecast using the last N days of actual-vs-forecast error (bucketed by hour-of-day and cloud cover).
5. Persist the predicted/corrected watts to MariaDB (`lux_solar_forecast` table).

The forecast is exposed at `GET /api/forecast` and overlaid on the dashboard's PV chart (corrected forecast shown as a yellow dashed line). All forecast parameters (location, array kWp/azimuth/tilt, bifacial gain, provider, horizon, refresh interval, calibration) are runtime settings editable from the dashboard ⚙️ tab.

### Alerts

Alert thresholds are evaluated after every snapshot and published to MQTT (for HA binary sensors) and MariaDB (`lux_alerts` table). Supported alerts:

- Battery SOC low / critical
- Battery temperature high
- Inverter temperature high
- Grid lost (configurable threshold in seconds)

Notifications are dispatched via authenticated SMTP relay and/or webhook, rate-limited to one per 5 minutes per alert. All thresholds and notification targets are runtime settings.

### Home Assistant integration

lux-mon has a native Home Assistant integration that connects to the REST API
and exposes live sensors, energy-dashboard sensors, controllable settings
(`number`/`select`/`switch` entities), alerts, and quick-charge buttons.

- **Integration repo:** [jmewing/ha_luxmon](https://github.com/jmewing/ha_luxmon)
- **Add-on repo (HAOS / Supervised):** [jmewing/ha_luxmon_addons](https://github.com/jmewing/ha_luxmon_addons)

Install via HACS, the add-on, or manually — see the integration README for full
instructions.

```bash
# Start the API server
python -m api

# Or install the systemd service (Linux)
sudo cp api/lux-api.service /etc/systemd/system/
sudo systemctl enable --now lux-api.service
```

### Apache Reverse Proxy (Optional)

To serve the dashboard on port 80, configure Apache as a reverse proxy:

```bash
# Enable proxy modules
sudo a2enmod proxy proxy_http

# Add to your default virtual host (/etc/apache2/sites-available/000-default.conf):
#
# <VirtualHost *:80>
#     ProxyPreserveHost On
#     ProxyPass / http://127.0.0.1:80/
#     ProxyPassReverse / http://127.0.0.1:80/
# </VirtualHost>

sudo systemctl reload apache2
```

Now the dashboard is available at `http://<your-server>/` and the API at `http://<your-server>/api/status`, etc.

## Environment Variables

All config can be set via env vars:

| Variable | Default | Description |
|----------|---------|-------------|
| `LUX_TRANSPORT` | `tcp_active` | `tcp_active`, `tcp_passive`, `replay` or `cloud_http` (seeds the DB-authoritative `transport` setting) |
| `LUX_DONGLE_HOST` | `192.168.1.100` | Inverter dongle IP |
| `LUX_DONGLE_PORT` | `8000` | Dongle TCP port |
| `LUX_CLOUD_USERNAME` | — | EG4 portal login for `cloud_http` (env-only, never stored in the DB) |
| `LUX_CLOUD_PASSWORD` | — | EG4 portal password for `cloud_http` (env-only; single-quote it if it contains `$`) |
| `LUX_CLOUD_POLL_SEC` | `60` | Seconds between cloud runtime polls (minimum 30) |
| `LUX_CLOUD_ENERGY_SEC` | `300` | Seconds between cloud energy polls (battery info is fetched with every new upload) |
| `LUX_CLOUD_BASE_URL` | `https://monitor.eg4electronics.com` | EG4 portal base URL (must be `https://`) |
| `LUX_GAPFILL_ENABLED` | `true` | Fill holes in the InfluxDB data from the EG4 export in the background (needs the EG4 login; see [Automatic gap-filling](#automatic-gap-filling)) |
| `LUX_GAPFILL_INTERVAL_MIN` | `180` | Minutes between gap-fill checks (minimum 30) |
| `LUX_GAPFILL_LOOKBACK_DAYS` | `7` | Days back the gap-filler looks (1-30) |
| `LUX_GAPFILL_MIN_GAP_MIN` | `10` | Minutes between live points that count as a gap (minimum 6) |
| `LUX_WRITE_INTERVAL` | `5` | Seconds between DB writes |
| `LUX_STORAGE_TYPE` | `mariadb` | `mariadb` or `influxdb` |
| `LUX_MARIADB_HOST` | `localhost` | MariaDB host |
| `LUX_MARIADB_PORT` | `3306` | MariaDB port |
| `LUX_MARIADB_USER` | `luxmon` | MariaDB user |
| `LUX_MARIADB_PASSWORD` | `luxmon` | MariaDB password |
| `LUX_MARIADB_DATABASE` | `luxmon` | MariaDB database |
| `LUX_INFLUX_URL` | `http://localhost:8086` | InfluxDB URL (optional) |
| `LUX_INFLUX_TOKEN` | `lux-mon-token` | InfluxDB token (optional) |
| `LUX_INFLUX_ORG` | `luxmon` | InfluxDB org (optional) |
| `LUX_INFLUX_BUCKET` | `solar` | InfluxDB bucket (optional) |
| `LUX_REPLAY_FILE` | — | Replay a capture instead of live TCP |
| `LUX_INVERTER_MODEL` | `eg4_6000xp` | Inverter / BMS model driver |
| `LUX_API_HOST` | `0.0.0.0` | API bind address |
| `LUX_API_PORT` | `80` | API port |

### RS-485 / BMS environment variables

The `lux-mon-rs485` daemon (see `collector/rs485_collector.py`) polls an RS-485/serial device and writes to the same backends:

| Variable | Default | Description |
|----------|---------|-------------|
| `LUX_RS485_ENABLED` | `false` | Enable the RS-485 collector |
| `LUX_RS485_PORT` | `/dev/ttyUSB0` | Serial port |
| `LUX_RS485_BAUD` | `115200` | Baud rate |
| `LUX_RS485_DEVICE_TYPE` | — | `jk_bms` \| `modbus_rtu` \| `raw` \| `eg4_a5_bms` \| `eg4_bms` |
| `LUX_RS485_POLL_INTERVAL` | `2.0` | Seconds between reads |
| `LUX_RS485_SLAVE_ID` | `1` | Modbus slave ID |
| `LUX_RS485_MODBUS_START` | `0` | Modbus register start |
| `LUX_RS485_MODBUS_COUNT` | `40` | Modbus register count |
| `LUX_RS485_PREFIX` | `rs485` | Measurement/topic prefix |

## Storage Backends

The collector supports two storage backends, which can be enabled together. Choose by setting `LUX_STORAGE_TYPE` (or the individual `LUX_*_ENABLED` flags).

### MariaDB / MySQL (default)

**Best for:** most users. Zero additional infrastructure if you already run MySQL/MariaDB. The REST API and dashboard read directly from MariaDB.

```bash
# Create the database and user
sudo mysql -e "CREATE DATABASE luxmon; CREATE USER 'luxmon'@'localhost' IDENTIFIED BY 'your-password'; GRANT ALL ON luxmon.* TO 'luxmon'@'localhost';"

# Configure
LUX_STORAGE_TYPE=mariadb
LUX_MARIADB_HOST=localhost
LUX_MARIADB_USER=luxmon
LUX_MARIADB_PASSWORD=your-password
LUX_MARIADB_DATABASE=luxmon
```

Tables are auto-created on first run:
- `lux_snapshots` — one row per write interval with timestamp and raw register JSON
- `lux_registers` — one row per decoded register value, indexed by timestamp and name
- `lux_settings` — runtime settings (key/value)
- `lux_alerts` — alert events
- `lux_solar_forecast` — solar PV forecast time series
- `lux_automation_log` — quick-charge action log

Set `LUX_MARIADB_TABLE_PREFIX` to change the table prefix from `lux_` if needed.

### InfluxDB (optional)

**Best for:** users already running InfluxDB, or who want Grafana's native InfluxDB data source. The REST API does **not** read from InfluxDB — you'd use Grafana or InfluxDB's built-in UI for visualization.

```bash
# Install the Python client
pip install influxdb-client

# Configure
LUX_STORAGE_TYPE=influxdb
LUX_INFLUX_URL=http://localhost:8086
LUX_INFLUX_TOKEN=your-token
LUX_INFLUX_ORG=your-org
LUX_INFLUX_BUCKET=solar
```

The InfluxDB schema is **SolarAssistant-compatible** (one measurement per metric, `inverter_0`/`combined` fields), so existing SolarAssistant Grafana dashboards import directly.

### Adding a New Backend

The collector uses a pluggable writer pattern. To add support for PostgreSQL, SQLite, or another database:

1. Add a new `storage_type` value (e.g. `"postgres"`)
2. Implement `_create_postgres_writer()` and `_write_postgres()` in `collector/collector.py`
3. Add the corresponding `LUX_POSTGRES_*` env vars to `config_from_env()`

See `_create_mariadb_writer()` and `_write_mariadb()` for the pattern to follow.

## Runtime Settings

Settings are stored in the `lux_settings` MariaDB table (auto-created) and read live by the API and dashboard — no config files, no restarts. Change any value with a single API call and the dashboard picks it up on the next refresh. The collector detects config changes on each write cycle and re-applies live-safe settings in place (or exits for a Docker restart when a transport/model change requires it).

| Setting | Default | Description |
|---------|---------|-------------|
| `pv_max_power` | `8000` | Max PV input power (W) — sets gauge ceiling |
| `battery_capacity` | `200` | Battery capacity (Ah) — sets battery gauge ceiling |
| `grid_max_power` | `6000` | Max grid pass-through (W) |
| `eps_max_power` | `6000` | Max EPS output (W) |
| `charge_max_power` | `5000` | Max charge power (W) |
| `discharge_max_power` | `5000` | Max discharge power (W) |
| `dashboard_refresh_sec` | `5` | Dashboard auto-refresh interval |
| `chart_default_hours` | `6` | Default chart time range |
| `write_interval_sec` | `5` | Seconds between MariaDB writes |
| `transport` | `tcp_active` | `tcp_active`, `tcp_passive`, `replay` or `cloud_http` (restart-required; other values are rejected with 422) |
| `timezone` | `America/Chicago` | Local timezone for scheduling |
| `temperature_unit` | `celsius` | Temperature unit |
| `quick_charge_minutes` | `60` | Default quick-charge duration (min, 1–240) |
| `forecast_enabled` | `false` | Enable solar forecast |
| `forecast_latitude` / `forecast_longitude` | site | Forecast location |
| `array_kwp` / `array_azimuth` / `array_tilt` | — | Array geometry for forecast |
| `array_bifacial_gain` | `0.10` | Bifacial back-side gain |
| `forecast_provider` | `open-meteo` | Forecast data source |
| `forecast_hours` | `48` | Forecast horizon |
| `forecast_refresh_min` | `120` | Forecast refresh interval |
| `forecast_bias_enabled` | `true` | Historical calibration |
| `forecast_bias_lookback_days` | `7` | Calibration lookback |
| `forecast_bias_min_samples` | `3` | Min samples per bucket |

```bash
# Read all settings
curl http://your-server/api/settings

# Read one setting
curl http://your-server/api/settings/pv_max_power

# Update a setting
curl -X PUT http://your-server/api/settings/pv_max_power \
  -H 'Content-Type: application/json' \
  -d '{"value": "10000"}'
```

Settings can also be edited directly in MariaDB:

```sql
INSERT INTO lux_settings (name, value) VALUES ('pv_max_power', '10000')
  ON DUPLICATE KEY UPDATE value = '10000';
```

## Backup and Restore

`scripts/backup.sh` creates a single compressed archive containing everything
needed to rebuild your lux-mon system on new hardware:

- MariaDB dump (`luxmon` database)
- InfluxDB v2 bucket backup
- Grafana provisioning files and dashboard JSON
- `.env` configuration
- Runtime settings from `/api/settings`

Run manually:

```bash
# Must run as root to read Grafana provisioning files
sudo bash scripts/backup.sh
```

Configure via `.env`:

```bash
LUX_BACKUP_DIR=/var/backups/lux-mon
LUX_BACKUP_KEEP_DAYS=30
# Optional off-device copy:
LUX_BACKUP_REMOTE=user@nas:/backups/lux-mon
```

The included systemd timer runs the backup automatically every night at 02:00:

```bash
sudo cp scripts/lux-mon-backup.service /etc/systemd/system/
sudo cp scripts/lux-mon-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lux-mon-backup.timer
```

Restore from an archive on a fresh install:

```bash
sudo LUX_BACKUP=/var/backups/lux-mon/luxmon-backup-YYYYMMDD-HHMMSS.tar.gz bash scripts/restore.sh
```

`scripts/prune.sh` deletes detail data older than 90 days while keeping hourly
energy rollups for one year, keeping the MariaDB database small.


### MQTT setting control

lux-mon also accepts setting changes over MQTT. The collector subscribes to:

```
luxmon/luxmon_solar/set/<setting>
```

and writes valid values to MariaDB immediately. Example:

```bash
mosquitto_pub -h 192.168.1.100 -t luxmon/luxmon_solar/set/alerts_soc_low -m 25
```

Acknowledgments and errors are published on:

```
luxmon/luxmon_solar/ack
luxmon/luxmon_solar/error
```

When Home Assistant discovery is enabled, controllable settings appear as
`number` entities under `homeassistant/number/luxmon_*`.

### Backup, prune, and storage

A built-in backup script dumps MariaDB, `.env`, and settings to a timestamped
tarball in `/var/backups/lux-mon`:

```bash
bash scripts/backup.sh
```

Prune old detail data while keeping hourly energy rollups:

```bash
bash scripts/prune.sh
```

Both are also exposed through the REST API:

```bash
# Create a backup
curl -X POST http://your-server/api/backup

# List backups
curl http://your-server/api/backups

# Prune old detail data
curl -X POST http://your-server/api/prune

# Show DB table sizes and disk usage
curl http://your-server/api/storage
```

## Updating

lux-mon is under active development. To pull the latest changes:

```bash
cd lux-mon
git pull origin main
pip install -r docker/requirements.txt  # if dependencies changed
sudo systemctl restart lux-mon.service lux-api.service
```

**One-liner for cron/nightly updates:**

```bash
cd ~/src/lux-mon && git pull origin main && \
  venv/bin/pip install -q -r docker/requirements.txt && \
  sudo systemctl restart lux-mon.service lux-api.service
```

To update automatically every night at 3am:

```bash
# Add to crontab (crontab -e)
0 3 * * * cd ~/src/lux-mon && git pull origin main && venv/bin/pip install -q -r docker/requirements.txt && sudo systemctl restart lux-mon.service lux-api.service
```

> **Note:** Your `.env` file is gitignored and will never be overwritten. Settings stored in the database (`lux_settings` table) are also preserved across updates.

## Versioning

lux-mon uses **semantic versioning** (`MAJOR.MINOR.PATCH`) with automated
release tagging. The version lives in a single place — `api/__init__.py` — and
is bumped automatically by a GitHub Actions workflow on every push to `main`.

| Bump | When | Commit convention |
| ---- | ---- | ----------------- |
| **MAJOR** (`1.x.x`) | New inverter support | `feat(inverter): add support for <model>` |
| **MINOR** (`x.1.x`) | New feature | `feat: <description>` (or `feat(scope): ...`) |
| **PATCH** (`x.x.1`) | Bug fix / maintenance | `fix:`, `chore:`, `docs:`, `refactor:`, `perf:`, `test:`, `ci:` |

A `BREAKING CHANGE` footer or a `!` after the type (e.g. `feat!: ...`) also
forces a **MAJOR** bump.

### How it works

1. On push to `main`, the workflow inspects commit subjects since the last tag.
2. It picks the highest-priority bump (major > minor > patch).
3. It updates `api/__init__.py`, commits the bump, tags `vX.Y.Z`, and creates a
   GitHub release with an auto-generated changelog.

### Manual bump (local)

```bash
# Preview the next version (dry run)
scripts/bump-version.sh

# Force a specific bump and write it back to api/__init__.py
scripts/bump-version.sh --bump minor --write
scripts/bump-version.sh --bump patch --write
scripts/bump-version.sh --bump major --write
```

> **Note:** The base version is the higher of the file version and the last
> git tag, which reconciles the historical drift where tags were created
> without bumping the file.

## Development Replay

Test parsing/storage without a live inverter:

```bash
python -m collector --replay <your-capture-file> --interval 5
```

## Protocol

The LuxPower WiFi dongle broadcasts inverter data over TCP port 8000 using a proprietary framing protocol (not standard Modbus TCP). The protocol has been reverse-engineered — see `docs/reference/lux-protocol/PROTOCOL.md` for the full spec.

Key facts:
- **No polling needed** — the dongle pushes data every ~2 seconds when it has an active TCP client
- **Single TCP client limit** — the dongle accepts only one TCP connection at a time; additional connections are closed immediately. Disconnect any other client (SolarAssistant, the vendor app, another collector instance) before starting this collector.
- **6 packets per cycle**: 3 input register batches + 3 holding register batches
- **40 registers per batch** = 240 registers total per cycle

## Reference Projects

This project builds on the excellent reverse-engineering work of:

- [jefflaplante/lux](https://github.com/jefflaplante/lux) — Protocol specification
- [celsworth/lxp-bridge](https://github.com/celsworth/lxp-bridge) — Original Rust bridge (MQTT/InfluxDB/Postgres)
- [jaredmauch/eg4-bridge](https://github.com/jaredmauch/eg4-bridge) — Maintained EG4 fork
- [larduino/EG4-6000XP-Home-Assistant-Local-Control](https://github.com/larduino/EG4-6000XP-Home-Assistant-Local-Control) — EG4 6000XP register map

Copies of these are mirrored in `docs/reference/` for preservation. The authoritative reverse-engineered Modbus address map for the LXP/EG4 inverter family is `docs/reference/lxp-bridge/doc/LXP_REGISTERS.txt`.

## License

MIT
