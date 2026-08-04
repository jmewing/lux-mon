# lux-mon Grafana + InfluxDB + MQTT Stack

This directory contains everything needed to run a local Grafana dashboard backed by InfluxDB and fed by the lux-mon collector.

## Quick Start on alpha (Raspberry Pi / Ubuntu)

```bash
cd /home/jmewing/src/lux-mon
LUX_INFLUX_ADMIN_PASSWORD='your-secure-password' bash scripts/setup-grafana-stack.sh
```

The script installs and configures:

- **InfluxDB 2.x** on `127.0.0.1:8086`
  - org: `luxmon`
  - bucket: `luxmon`
  - username: `luxmon`
  - admin password: set via `LUX_INFLUX_ADMIN_PASSWORD` (required for the initial bootstrap only; lux-mon uses the API token afterwards)
- **Mosquitto MQTT** on `127.0.0.1:1883` (anonymous localhost access)
- **Grafana** on `127.0.0.1:3000`
  - datasource: `lux-mon` (InfluxDB 2.x / InfluxQL via DBRP mapping `luxmon/autogen`)
  - dashboard: `lux-mon Charts` (imported and adapted from SolarAssistant)

The API token is saved to `/tmp/influx-luxmon.token` on alpha.

## lux-mon Collector Configuration

Make sure the collector's `.env` enables the new backends:

```bash
LUX_INFLUX_ENABLED=true
LUX_INFLUX_URL=http://localhost:8086
LUX_INFLUX_TOKEN=<token from /tmp/influx-luxmon.token>
LUX_INFLUX_ORG=luxmon
LUX_INFLUX_BUCKET=luxmon

LUX_MQTT_ENABLED=true
LUX_MQTT_HOST=localhost
LUX_MQTT_PORT=1883
LUX_MQTT_TOPIC_PREFIX=luxmon
LUX_MQTT_HA_DISCOVERY=true
LUX_MQTT_HA_PREFIX=homeassistant
LUX_MQTT_DEVICE_NAME=luxmon
LUX_MQTT_DEVICE_ID=luxmon_solar
```

After editing `.env`, restart the collector:

```bash
sudo systemctl restart lux-mon.service
```

You can also store these settings in MariaDB via the web UI (`/api/settings`). Environment variables take precedence.

## Accessing Grafana

- Direct (via SSH tunnel): `http://127.0.0.1:3000/grafana/d/lux-mon-charts/lux-mon-charts`
- Through Apache reverse proxy (add to your vhost):

  ```apache
  ProxyPass        /grafana http://127.0.0.1:3000
  ProxyPassReverse /grafana http://127.0.0.1:3000
  ```

  Then visit: `http://192.168.12.8/grafana/d/lux-mon-charts/lux-mon-charts`

- Default Grafana credentials: `admin` / `admin` (change on first login)

## Updating dashboards from settings

After changing power/temperature settings in the web UI, regenerate the Grafana dashboard JSON files so panel axis maxima and temperature units match:

```bash
python3 scripts/regenerate-dashboards.py
```

Then copy the updated JSON files to your Grafana dashboards folder (on alpha):

```bash
rsync -av grafana/dashboards/ alpha:/var/lib/grafana/dashboards/lux-mon/
```

The script reads `pv_max_power`, `grid_max_power`, `charge_max_power`, `discharge_max_power`, `eps_max_power`, and `temperature_unit` from MariaDB and updates `axisSoftMax` plus temperature labels in all `grafana/dashboards/*.json` files.

## Schema

The collector writes SolarAssistant-compatible measurement names so that existing SolarAssistant Grafana dashboards import directly:

| Measurement | Field | Meaning |
|-------------|-------|---------|
| `PV power` | `combined` | total PV power (pv1 + pv2 + pv3) |
| `PV power 1` | `inverter_0` | string 1 power |
| `PV power 2` | `inverter_0` | string 2 power |
| `Grid power` | `combined` | net grid import - export |
| `Load power` | `combined` | inverter output power |
| `Battery power` | `combined` | net charge - discharge |
| `Battery voltage` | `combined` | battery voltage |
| `Battery SOC` | `combined` | state of charge % |
| `Battery temperature` | `combined` | battery temp |
| `Inverter temperature` | `inverter_0` | inverter heatsink temp |
| `AC voltage` | `inverter_0` | output voltage |
| `AC frequency` | `inverter_0` | output frequency |

Tags are intentionally minimal to match the SolarAssistant schema. The `device_id` (`luxmon_solar`) and `device_name` (`luxmon`) are included for MQTT/Home Assistant.

## Home Assistant MQTT Discovery

When `LUX_MQTT_HA_DISCOVERY=true`, the collector publishes discovery configs under:

```
homeassistant/sensor/luxmon_<key>/config
```

and state under:

```
luxmon/luxmon_solar/state
```

Add your MQTT broker to Home Assistant (default: `192.168.12.8:1883` if exposed) and sensors will appear automatically.

## Files

- `provisioning/datasources/influxdb.yaml` — Grafana datasource config (InfluxDB 1.x fallback)
- `provisioning/dashboards/dashboards.yaml` — dashboard provider
- `dashboards/lux-mon-charts.json` — imported SolarAssistant "Charts" dashboard
- `../scripts/setup-grafana-stack.sh` — one-command install/bootstrap script
- `../scripts/regenerate-dashboards.py` — update dashboard JSON from lux-mon settings
