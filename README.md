# lux-mon

**Local monitoring for LuxPower-based inverters — no cloud required.**

Works with EG4, LuxPower, and any rebranded inverter using the LuxPower WiFi dongle protocol (TCP port 8000).

## What It Does

- **Passively listens** to your inverter's WiFi dongle — zero bus contention, coexists with SolarAssistant
- **Stores** time-series data in InfluxDB (or SQLite for lightweight setups)
- **Visualizes** with Grafana dashboards
- **Exposes** a REST API for scripting, morning briefings, Home Assistant, etc.
- **Runs anywhere** — your Mac, a Raspberry Pi, a Docker container, alongside Home Assistant

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
                    │  InfluxDB       │  (or SQLite)
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
pip install -r requirements.txt

# Configure (edit your inverter's IP)
cp config.example.yaml config.yaml

# Run the collector
python -m collector
```

## Protocol

The LuxPower WiFi dongle broadcasts inverter data over TCP port 8000 using a proprietary framing protocol (not standard Modbus TCP). The protocol has been reverse-engineered — see `docs/reference/lux-protocol/PROTOCOL.md` for the full spec.

Key facts:
- **No polling needed** — the dongle pushes data every ~2 seconds
- **6 packets per cycle**: 3 input register batches + 3 holding register batches
- **40 registers per batch** = 240 registers total per cycle
- **Coexists peacefully** with SolarAssistant — both just listen

## Reference Projects

This project builds on the excellent reverse-engineering work of:

- [jefflaplante/lux](https://github.com/jefflaplante/lux) — Protocol specification
- [celsworth/lxp-bridge](https://github.com/celsworth/lxp-bridge) — Original Rust bridge (MQTT/InfluxDB/Postgres)
- [jaredmauch/eg4-bridge](https://github.com/jaredmauch/eg4-bridge) — Maintained EG4 fork
- [larduino/EG4-6000XP-Home-Assistant-Local-Control](https://github.com/larduino/EG4-6000XP-Home-Assistant-Local-Control) — EG4 6000XP register map

Copies of these are mirrored in `docs/reference/` for preservation.

## License

MIT
