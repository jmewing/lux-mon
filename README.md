# lux-mon

**Local monitoring for LuxPower-based inverters — no cloud required.**

Works with EG4, LuxPower, and any rebranded inverter using the LuxPower WiFi dongle protocol (TCP port 8000). Also supports RS-485 battery BMS monitoring (EG4 A5/5A, JK BMS, generic Modbus RTU).

## What It Does

- **Passively listens** to your inverter's WiFi dongle — zero bus contention
- **Actively polls** as a fallback for non-broadcasting dongles
- **Stores** time-series data in **MariaDB/MySQL** (InfluxDB optional, both can run together)
- **Exposes** a **REST API** for scripting, morning briefings, Home Assistant, etc.
- **Streams live snapshots** over a **WebSocket** (`/ws`) so the web dashboard updates instantly
- **Writes inverter settings** via a safe automation rule engine with dry-run, clamping, and action logging
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
| Dashboard | ✅ Live | Web UI with gauges, charts, battery, totals, settings, automations |
| Active polling | ✅ Built | Fallback for non-broadcasting dongles |
| Runtime settings | ✅ Live | DB-backed, editable from dashboard ⚙️ tab |
| Docker image | ✅ Published | Stable: `jmewing/lux-mon:v1.0.1`; Beta: `jmewing/lux-mon:v1.1.0-beta.1` (amd64 + arm64) on Docker Hub and GHCR |
| RS-485 / BMS | ✅ Live | `lux-mon-rs485` daemon, EG4 A5/5A battery BMS driver deployed |
| Alerts | ✅ Live | SMTP + webhook notifications, rate-limited, UI configurable |
| Solar forecast | ✅ Live | Open-Meteo weather forecast + historical calibration (v1.2.1) |
| Quick charge | ✅ Live | Timed grid charge with restore-on-expiry |
| Home Assistant | ✅ Integrated | Native REST integration + MQTT auto-discovery + energy sensors |
| Backup/restore | ✅ Built | Nightly systemd timer + one-command restore |
| Grafana | ✅ Built | Pre-loaded dashboards and data source |
| Automation rules | ✅ Built | Rule-table model (time + sensor conditions) that writes holding registers |

### In progress / near-term

- **Inverter Edit Mode page** — manual Read/Set for every editable EG4 6000XP holding register, mirroring the EG4 Monitor Maintenance tab.
- **Automation page overhaul** — rebuild around a premade option list + wizard (like SolarAssistant's Power Management), with restore-value support and time/day conditions.
- **Holding register map corrections** — align `collector/protocol.py` with the `LXP_REGISTERS.txt` reference, add missing registers, fix ranges, and correct AC charge current to register 168.

### Roadmap

- Generator and AC-coupled charge support (generator-charge register path is stubbed)
- Battery protection automations (SOC threshold + restore) — rule-table model supports this, needs UI
- More inverter models via pluggable Modbus RTU drivers
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
- Grafana: http://YOUR-HOST:3000/grafana/d/lux-mon-charts/lux-mon-charts
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
| `GET /api/automation/registers` | List writable holding registers for automation actions |
| `GET /api/automation/types` | List automation types (rule_table, battery_soc, battery_protection, notify) |
| `GET /api/automation/rules` | List automation rules and global enable flag |
| `POST /api/automation/rules` | Replace the full rule set (JSON array) |
| `DELETE /api/automation/rules/{rule_id}` | Delete a single rule |
| `POST /api/automation/enable` | Globally enable/disable the automation engine |
| `POST /api/automation/test` | Dry-run evaluate rules against the latest snapshot |
| `GET /api/automation/log` | Recent automation actions / dry-runs |
| `GET /api/quick-charge/status` | Current quick-charge state and defaults |
| `POST /api/quick-charge/start` | Start a timed quick charge (JSON: `{"amps": 85, "minutes": 30}`) |
| `POST /api/quick-charge/stop` | Stop an active quick charge, restoring the prior value |
| `POST /api/backup` | Create a backup archive |
| `GET /api/backups` | List backup archives |
| `POST /api/prune` | Prune old detail data |
| `GET /api/storage` | Show DB table sizes and disk usage |
| `WS /ws` | Live snapshot WebSocket stream |

The built-in dashboard (`/`) includes an **Automations** page where you can enable the engine, add rules with time windows and sensor conditions, and test them in dry-run mode before allowing live inverter writes.

### Automation rules

Rules are stored as JSON in the `automation_rules` setting and evaluated after every snapshot. The engine uses a **rule-table model** (target-first, nested subset columns, restore-on-exit) that mirrors the EG4/Luxpower "Power Management" portal. Four automation types are supported:

- `rule_table` — a multi-dimensional rule table (e.g. grid charge current as a function of time-of-day and battery voltage)
- `battery_soc` — battery state-of-charge control (time + SOC thresholds → grid/battery output source)
- `battery_protection` — "if SOC ≤ X%, shutdown output; restore to Y when recovered" (restore-on-exit)
- `notify` — send an email/webhook notification when a condition is met

Only one automation may be active per target type. A rule can set a holding register to a static value or choose a value from a sensor-range table. Each rule supports:

- `time_window`: `{"start": "21:00", "end": "06:00"}` (wraps past midnight)
- `conditions`: list of `{"sensor": "battery_voltage", "min": 0, "max": 54}` checks
- `action`: `{"register": "ac_charge_battery_current", "value": 85}` or a `ranges` table
- `dry_run`: when `true`, the rule logs what it *would* write but never sends a Modbus command
- `restore`: a value written back when conditions stop matching (or the automation is disabled)

Only registers listed in `collector/protocol.py:HOLDING_REGISTERS` can be written, and every value is clamped to the register's documented min/max. Example rule matching SolarAssistant's night grid-charge behavior:

```json
{
  "id": "night_grid_charge",
  "name": "Grid charge current (night)",
  "enabled": true,
  "dry_run": true,
  "time_window": {"start": "21:00", "end": "06:00"},
  "conditions": [],
  "action": {
    "register": "ac_charge_battery_current",
    "range_sensor": "battery_voltage",
    "ranges": [
      {"min": 0,   "max": 54.0, "value": 85},
      {"min": 55.0, "max": 56.0, "value": 45},
      {"min": 57.0, "max": 58.0, "value": 5}
    ]
  }
}
```

Enable the engine globally with the `automation_enabled` setting (or the dashboard toggle). Set `dry_run: false` on a rule only when you are ready for live inverter writes.

**Design note:** Readable values (SOC, voltage, current, power) are telemetry and are included automatically. Set values (charge current, SOC limits, time slots, modes) are only written when explicitly changed via the website or when an automation rule says "this is what we want it to be." lux-mon never writes a setting just because it happens to be readable.

### Quick Charge / Generator Charge

lux-mon implements the two one-shot inverter actions the EG4 Monitor portal exposes:

- **Quick Charge** — write `ac_charge_battery_current` (register 168) to a target current for a fixed number of minutes, then restore the prior value.
- **Generator Charge** — write the generator charge current / enable path (register path stubbed, see roadmap).

State is persisted in the `lux_settings` table so it survives collector restarts. Writes reuse the automation engine's safe write helpers (fresh socket, echo verification, clamping). Defaults are configurable via the `quick_charge_amps` and `quick_charge_minutes` settings.

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
| `LUX_DONGLE_HOST` | `192.168.1.100` | Inverter dongle IP |
| `LUX_DONGLE_PORT` | `8000` | Dongle TCP port |
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
- `lux_automation_log` — automation actions / dry-runs

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
| `timezone` | `America/Chicago` | Local timezone for scheduling |
| `temperature_unit` | `celsius` | Temperature unit |
| `quick_charge_amps` | — | Default quick-charge target current (A) |
| `quick_charge_minutes` | — | Default quick-charge duration (min) |
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
