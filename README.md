# lux-mon

**Local monitoring for LuxPower-based inverters — no cloud required.**

Works with EG4, LuxPower, and any rebranded inverter using the LuxPower WiFi dongle protocol (TCP port 8000).

## What It Does

- **Passively listens** to your inverter's WiFi dongle — zero bus contention
- **Actively polls** as a fallback for non-broadcasting dongles
- **Stores** time-series data in **MariaDB/MySQL** (InfluxDB optional)
- **Exposes** a **REST API** for scripting, morning briefings, Home Assistant, etc.
- **Streams live snapshots** over a **WebSocket** (`/ws`) so the web dashboard updates instantly
- **Writes inverter settings** via a safe automation rule engine with dry-run, clamping, and action logging
- **Home Assistant energy dashboard** ready — publishes MQTT sensors with `state_class: total_increasing` for solar, grid, and battery flows
- **Runs anywhere** — your Mac, a Raspberry Pi, a Docker container
- **Dashboard-ready** — works with Grafana or any SQL-based visualization tool

## Status

This project is actively running on a **Dell R420 (`automation`, 192.168.12.10)** monitoring an EG4 6000XP inverter (serial `5203740777`) via the WiFi dongle at `192.168.12.224:8000`.

| Component | Status | Details |
|-----------|--------|---------|
| Collector | ✅ Live | 111 input registers decoded, ~30s writes to MariaDB + InfluxDB + MQTT |
| REST API | ✅ Live | FastAPI on port 8080, systemd-managed |
| Storage | ✅ Live | MariaDB, InfluxDB v2, hourly energy rollups |
| Dashboard | ✅ Live | Web UI with gauges, charts, battery, totals, settings, automations |
| Active polling | ✅ Built | Fallback for non-broadcasting dongles |
| Runtime settings | ✅ Live | DB-backed, editable from dashboard ⚙️ tab |
| Docker image | ✅ Published | `jmewing/lux-mon:v1.0.1` (amd64 + arm64) on Docker Hub and GHCR |
| RS-485 / BMS | ✅ Live | `lux-mon-rs485` daemon, EG4 A5/5A battery BMS driver deployed |
| Alerts | ✅ Live | SMTP + webhook notifications, rate-limited, UI configurable |
| Home Assistant | ✅ Integrated | MQTT auto-discovery, energy dashboard sensors |
| Backup/restore | ✅ Built | Nightly systemd timer + one-command restore |
| Grafana | ✅ Built | Pre-loaded `lux-mon-charts` dashboard and data source |
| Automation rules | ✅ Built | Time + sensor conditions that write holding registers |

### In progress / near-term

- **Inverter Edit Mode page** — manual Read/Set for every editable EG4 6000XP holding register, mirroring the EG4 Monitor Maintenance tab.
- **Automation page overhaul** — rebuild around a premade option list + wizard (like SolarAssistant's Power Management), with restore-value support and time/day conditions.
- **Holding register map corrections** — align `collector/protocol.py` with the `LXP_REGISTERS.txt` reference, add missing registers, fix ranges, and correct AC charge current to register 168.

### Roadmap

- Quick Charge button that actually works (EG4 Monitor-style timed grid charge)
- Generator charge support once the generator input is reconnected
- Battery protection automations (SOC threshold + restore)
- More inverter models via pluggable Modbus RTU drivers

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
                 │  /api/status     │  port 8080
                 │  /api/history    │
                 └─────────────────┘
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
LUX_DONGLE_HOST=192.168.12.224 \
bash scripts/install.sh
```

After install:
- API: http://your-host:8080/api/status
- Grafana: http://your-host:3000/grafana/d/lux-mon-charts/lux-mon-charts
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

- **Docker Hub:** `jmewing/lux-mon:v1.0.1`
- **GitHub Container Registry:** `ghcr.io/jmewing/lux-mon:v1.0.1`

Example `.env`:

```bash
LUX_IMAGE=jmewing/lux-mon:v1.0.1
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

The API server runs on port 8080 and provides:

| Endpoint | Description |
|----------|-------------|
| `GET /api/status` | Latest snapshot with all decoded registers |
| `GET /api/summary` | Compact key metrics for dashboards |
| `GET /api/history?minutes=60&fields=soc,battery_voltage` | Time-series data |
| `GET /api/health` | Health check |
| `GET /api/settings` | All runtime settings |
| `GET /api/settings/{name}` | Single setting value |
| `PUT /api/settings/{name}` | Update a setting (JSON body: `{"value": "..."}`) |
| `GET /api/automation/registers` | List writable holding registers for automation actions |
| `GET /api/automation/rules` | List automation rules and global enable flag |
| `POST /api/automation/rules` | Replace the full rule set (JSON array) |
| `POST /api/automation/enable` | Globally enable/disable the automation engine |
| `POST /api/automation/test` | Dry-run evaluate rules against the latest snapshot |
| `GET /api/automation/log` | Recent automation actions / dry-runs |
| `POST /api/backup` | Create a backup archive |
| `GET /api/backups` | List backup archives |
| `POST /api/prune` | Prune old detail data |
| `GET /api/storage` | Show DB table sizes and disk usage |

The built-in dashboard (`/`) includes an **Automations** page where you can enable the engine, add rules with time windows and sensor conditions, and test them in dry-run mode before allowing live inverter writes.

### Automation rules

Rules are stored as JSON in the `automation_rules` setting and evaluated after every snapshot. A rule can set a holding register to a static value or choose a value from a sensor-range table (e.g., different charge amps for different battery-voltage bands). Each rule supports:

- `time_window`: `{"start": "21:00", "end": "06:00"}` (wraps past midnight)
- `conditions`: list of `{"sensor": "battery_voltage", "min": 0, "max": 54}` checks
- `action`: `{"register": "ac_charge_battery_current", "value": 85}` or a `ranges` table
- `dry_run`: when `true`, the rule logs what it *would* write but never sends a Modbus command

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

The API also serves a built-in web dashboard at `/` with real-time gauges, power flow, historic charts, battery details, energy totals, and runtime settings.

A pre-built Grafana dashboard is included in `grafana/dashboards/lux-mon-charts.json` and provisioned automatically by `scripts/setup-grafana-stack.sh`. It uses a SolarAssistant-compatible InfluxDB schema so existing SolarAssistant dashboards import directly.

For Home Assistant users, see [`docs/home-assistant-energy.md`](docs/home-assistant-energy.md).

```bash
# Start the API server
python -m api

# Or install the systemd service (Linux)
sudo cp api/lux-api.service /etc/systemd/system/
sudo systemctl enable --now lux-api.service
```

### Apache Reverse Proxy (Optional)

To serve the dashboard on port 80 instead of 8080, configure Apache as a reverse proxy:

```bash
# Enable proxy modules
sudo a2enmod proxy proxy_http

# Add to your default virtual host (/etc/apache2/sites-available/000-default.conf):
#
# <VirtualHost *:80>
#     ProxyPreserveHost On
#     ProxyPass / http://127.0.0.1:8080/
#     ProxyPassReverse / http://127.0.0.1:8080/
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
| `LUX_API_PORT` | `8080` | API port |

## Storage Backends

The collector supports two storage backends. Choose one by setting `LUX_STORAGE_TYPE`.

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

### Adding a New Backend

The collector uses a pluggable writer pattern. To add support for PostgreSQL, SQLite, or another database:

1. Add a new `storage_type` value (e.g. `"postgres"`)
2. Implement `_create_postgres_writer()` and `_write_postgres()` in `collector/collector.py`
3. Add the corresponding `LUX_POSTGRES_*` env vars to `config_from_env()`

See `_create_mariadb_writer()` and `_write_mariadb()` for the pattern to follow.

## Runtime Settings

Settings are stored in the `lux_settings` MariaDB table (auto-created) and read live by the API and dashboard — no config files, no restarts. Change any value with a single API call and the dashboard picks it up on the next refresh.

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
mosquitto_pub -h 192.168.12.8 -t luxmon/luxmon_solar/set/alerts_soc_low -m 25
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
