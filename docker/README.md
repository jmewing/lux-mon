# lux-mon Docker Compose Stack

This directory contains a one-command Docker Compose deployment of the full
lux-mon stack:

- MariaDB — primary time-series + settings store
- InfluxDB v2 — SolarAssistant-compatible metrics bucket
- Mosquitto — MQTT broker for Home Assistant discovery and live data
- lux-collector — reads from the inverter dongle and writes to all backends
- lux-api — FastAPI REST server and web dashboard
- Grafana — pre-provisioned dashboards

## Quick Start

From the repository root (not this directory):

```bash
cp docker/.env.example .env
# edit .env with your dongle IP, passwords, and token

docker compose -f docker/docker-compose.yml up -d --build
```

- Web dashboard / API: http://localhost:8080/
- Grafana: http://localhost:3000/
- MQTT broker: localhost:1883

## Configuration

All configuration is via `.env` in the repository root. See `docker/.env.example`
for every available variable.

Key variables:

- `LUX_DONGLE_HOST` — IP of your WiFi dongle
- `LUX_MARIADB_PASSWORD` — database password
- `LUX_INFLUX_TOKEN` — long random admin token (≥32 chars)
- `LUX_INFLUX_ADMIN_PASSWORD` — InfluxDB admin password
- `LUX_GRAFANA_ADMIN_PASSWORD` — Grafana admin password

## Build vs. Pull

The compose file includes `build` sections for collector/API. By default,
`docker compose up --build` builds the image locally and tags it `lux-mon:local`.

To use a published image instead, set `LUX_IMAGE` in `.env`:

```bash
LUX_IMAGE=ghcr.io/jmewing/lux-mon:v1.0
```

Then run without `--build`:

```bash
docker compose -f docker/docker-compose.yml up -d
```

## Notes

- The compose context is the repository root, so run commands from there.
- `db-init` creates the MariaDB tables on first start before collector/API start.
- `influxdb-init` creates the v1 DBRP mapping required by the InfluxQL datasource.
- `grafana-init` copies dashboards and renders the InfluxDB datasource template.
