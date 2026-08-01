# lux-mon

**Local monitoring for LuxPower-based inverters — no cloud required.**

Works with EG4, LuxPower, and any rebranded inverter using the LuxPower WiFi dongle protocol (TCP port 8000).

## What It Does

- **Passively listens** to your inverter's WiFi dongle — zero bus contention
- **Stores** time-series data in **MariaDB/MySQL** (InfluxDB optional)
- **Exposes** a **REST API** for scripting, morning briefings, Home Assistant, etc.
- **Runs anywhere** — your Mac, a Raspberry Pi, a Docker container
- **Dashboard-ready** — works with Grafana or any SQL-based visualization tool

## Architecture

```
EG4/LuxPower Inverter → WiFi Dongle (TCP :8000)
                              │
                              ▼ (passive listen)
                    ┌─────────────────┐
                    │  lux-collector   │  Python
                    │  (protocol      │
                    │   parser)       │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  MariaDB/MySQL  │  (or InfluxDB)
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │   REST API       │  FastAPI
                    │  /api/status     │  port 8080
                    │  /api/history    │
                    └─────────────────┘
```

## Quick Start

```bash
# Clone
git clone https://github.com/jmewing/lux-mon.git
cd lux-mon

# Install
pip install -r docker/requirements.txt

# Configure via environment (copy example and edit)
cp .env.example .env
# edit .env with your DB credentials and dongle IP

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

The API also serves a built-in web dashboard at `/` with real-time gauges, power flow, historic charts, battery details, and energy totals.

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
| `LUX_WRITE_INTERVAL` | `30` | Seconds between DB writes |
| `LUX_STORAGE_TYPE` | `mariadb` | `mariadb` or `influxdb` |
| `LUX_MARIADB_HOST` | `localhost` | MariaDB host |
| `LUX_MARIADB_PORT` | `3306` | MariaDB port |
| `LUX_MARIADB_USER` | `luxmon` | MariaDB user |
| `LUX_MARIADB_PASSWORD` | `luxmon` | MariaDB password |
| `LUX_MARIADB_DATABASE` | `luxmon` | MariaDB database |
| `LUX_MARIADB_TABLE_PREFIX` | `lux_` | Table name prefix |
| `LUX_INFLUX_URL` | `http://localhost:8086` | InfluxDB URL (optional) |
| `LUX_INFLUX_TOKEN` | `lux-mon-token` | InfluxDB token (optional) |
| `LUX_INFLUX_ORG` | `luxmon` | InfluxDB org (optional) |
| `LUX_INFLUX_BUCKET` | `solar` | InfluxDB bucket (optional) |
| `LUX_REPLAY_FILE` | — | Replay a capture instead of live TCP |
| `LUX_API_HOST` | `0.0.0.0` | API bind address |
| `LUX_API_PORT` | `8080` | API port |

## Storage Backends

The collector supports two storage backends. Choose one with `LUX_STORAGE_TYPE`.

### MariaDB / MySQL (default)

**Best for:** most users, especially on Raspberry Pi or existing LAMP stacks.

```bash
# Create the database and user
sudo mysql -e "CREATE DATABASE luxmon; CREATE USER 'luxmon'@'localhost' IDENTIFIED BY 'your-password'; GRANT ALL ON luxmon.* TO 'luxmon'@'localhost'; FLUSH PRIVILEGES;"
```

Set in `.env`:
```env
LUX_STORAGE_TYPE=mariadb
LUX_MARIADB_HOST=localhost
LUX_MARIADB_PORT=3306
LUX_MARIADB_USER=luxmon
LUX_MARIADB_PASSWORD=your-password
LUX_MARIADB_DATABASE=luxmon
```

Tables are auto-created on first run (`lux_snapshots`, `lux_registers`). The `LUX_MARIADB_TABLE_PREFIX` lets you change the prefix if needed.

**Dependencies:** `pymysql` (included in `docker/requirements.txt`)

### InfluxDB

**Best for:** users already running InfluxDB, or who want native time-series query performance.

```bash
# Install InfluxDB OSS v2.x, then create a bucket and token
influx setup --org luxmon --bucket solar --username admin --password your-password
influx auth create --org luxmon --write-bucket solar --read-bucket solar
```

Set in `.env`:
```env
LUX_STORAGE_TYPE=influxdb
LUX_INFLUX_URL=http://localhost:8086
LUX_INFLUX_TOKEN=your-generated-token
LUX_INFLUX_ORG=luxmon
LUX_INFLUX_BUCKET=solar
```

**Dependencies:** `influxdb-client` (included in `docker/requirements.txt`)

### Adding a New Backend

The collector uses a pluggable storage interface. To add support for PostgreSQL, SQLite, or another database:

1. Add a new `_create_<backend>_writer()` method in `collector/collector.py`
2. Add a `_write_<backend>()` method for the actual writes
3. Add the new `storage_type` to the `if/elif` chain in `_create_writer()`
4. Add the corresponding env vars to `CollectorConfig` and `config_from_env()`

The schema is simple — two tables (snapshots + registers) — so porting to any SQL database is straightforward.

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

Copies of these are mirrored in `docs/reference/` for preservation.

## License

MIT

## Status

This project is actively running on a Raspberry Pi 4 ("alpha") monitoring an EG4 6000XP inverter.

| Component | Status | Details |
|-----------|--------|---------|
| Collector | ✅ Live | 111 registers decoded, ~2 snapshots/min to MariaDB |
| REST API | ✅ Live | FastAPI on port 8080, systemd-managed |
| Storage | ✅ Live | MariaDB, ~11K snapshots/day, ~110MB/day |
| Dashboard | ✅ Live | Web UI with gauges, charts, battery, totals |
| Active polling | 🔜 Planned | Fallback for non-broadcasting dongles |
