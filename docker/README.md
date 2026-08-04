# Docker / Compose (v1.0)

The included `docker-compose.yml` runs the entire lux-mon stack in one command:

- MariaDB (settings + snapshots)
- InfluxDB v2 (SolarAssistant-compatible time-series backend)
- Mosquitto MQTT broker (Home Assistant auto-discovery)
- lux-mon collector (reads the EG4/LuxPower dongle)
- lux-mon API + web dashboard
- Grafana with pre-provisioned datasources and dashboards

## Quick start

1. Copy the example environment file to the repository root:

   ```bash
   cp docker/.env.example .env
   ```

2. Edit `.env` and set at least:
   - `LUX_DONGLE_HOST` — IP of your EG4/LuxPower WiFi/LAN dongle
   - `LUX_MARIADB_ROOT_PASSWORD` / `LUX_MARIADB_PASSWORD`
   - `LUX_INFLUX_TOKEN` (a long random string used as the admin token)
   - `LUX_INFLUX_ADMIN_PASSWORD`
   - `LUX_GRAFANA_ADMIN_PASSWORD`

3. From the repository root, start the stack:

   ```bash
   docker compose -f docker/docker-compose.yml up -d
   ```

4. Open the dashboards after ~30 seconds (MariaDB/InfluxDB need a moment to initialise):
   - Web UI/API: http://<host>:8080/
   - Grafana:    http://<host>:3000/  (anonymous read is enabled; admin login uses the password from `.env`)
   - MQTT state topic: `luxmon/luxmon_solar/state`
   - Home Assistant discovery: `homeassistant/sensor/luxmon_solar_*/config`

## Services

| Service          | Image                     | Port  | Notes                                          |
|------------------|---------------------------|-------|------------------------------------------------|
| lux-mariadb      | `mariadb:10.11`           | 3306  | Auto-creates `luxmon` DB and user              |
| lux-influxdb     | `influxdb:2.7`            | 8086  | Auto-creates org/bucket/admin token              |
| lux-mosquitto    | `eclipse-mosquitto:2`     | 1883  | Anonymous MQTT enabled                         |
| lux-collector    | built from repo           | —     | Reads dongle every `LUX_WRITE_INTERVAL` seconds |
| lux-api          | built from repo           | 8080  | Serves REST API and static dashboard             |
| lux-grafana      | `grafana/grafana:latest`  | 3000  | Pre-loaded lux-mon datasources + dashboards    |

## Stopping

```bash
docker compose -f docker/docker-compose.yml down
```

To remove persistent data volumes as well:

```bash
docker compose -f docker/docker-compose.yml down -v
```

## Updating

```bash
git pull
docker compose -f docker/docker-compose.yml up -d --build
```
