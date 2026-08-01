# lux-mon

**Local monitoring for LuxPower-based inverters — no cloud required.**

Works with EG4, LuxPower, and any rebranded inverter using the LuxPower WiFi dongle protocol (TCP port 8000).

## What It Does

- **Passively listens** to your inverter's WiFi dongle — zero bus contention
- **Stores** time-series data in **MariaDB/MySQL** (default) or InfluxDB
- **Visualizes** with Grafana dashboards
- **Exposes** a REST API for scripting, morning briefings, Home Assistant, etc.
- **Runs anywhere** — your Mac, a Raspberry Pi, a Docker container

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
                    ┌────────┴────────┐
                    │                 │
                    ▼                 ▼
            ┌──────────┐     ┌──────────────┐
            │ Grafana  │     │  REST API     │
            │ Dashboard│     │  (FastAPI)    │
            └──────────┘     └──────────────┘
```

## Quick Start

```bash
# Clone
git clone https://github.com/jmewing/lux-mon.git
cd lux-mon

# Install
pip install -r docker/requirements.txt

# Configure (copy example and edit)
cp config.example.py config.py

# Run the collector
python -m collector --config config.py
```

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
| `LUX_INFLUX_URL` | `http://localhost:8086` | InfluxDB URL |
| `LUX_INFLUX_TOKEN` | `lux-mon-token` | InfluxDB token |
| `LUX_INFLUX_ORG` | `luxmon` | InfluxDB org |
| `LUX_INFLUX_BUCKET` | `solar` | InfluxDB bucket |
| `LUX_REPLAY_FILE` | — | Replay a capture instead of live TCP |

## Development Replay

Test parsing/storage without a live inverter:

```bash
python -m collector --replay tests/capture_raw.bin --interval 5
```

## Protocol

The LuxPower WiFi dongle broadcasts inverter data over TCP port 8000 using a proprietary framing protocol (not standard Modbus TCP). The protocol has been reverse-engineered — see `docs/reference/lux-protocol/PROTOCOL.md` for the full spec.

Key facts:
- **No polling needed** — the dongle pushes data every ~2 seconds when the inverter is active
- **Single TCP client limit** — the dongle closes extra connections when another client is already connected (or when the inverter is off and has no telemetry to send)
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
