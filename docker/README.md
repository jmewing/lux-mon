# Docker / Compose Notes

The included `docker-compose.yml` provides an optional **InfluxDB + Grafana** stack for users who prefer that backend. The collector itself defaults to **MariaDB/MySQL**, which is what we use on `alpha` (the Raspberry Pi already runs MariaDB for WordPress).

## Run InfluxDB + Grafana locally

```bash
cd docker
docker compose up -d
```

- InfluxDB: http://localhost:8086  (luxmon / lux-mon-password)
- Grafana:  http://localhost:3000   (luxmon / lux-mon-password)

## Run the collector against InfluxDB

```bash
export LUX_STORAGE_TYPE=influxdb
python3 -m collector
```

## Run the collector against MariaDB

```bash
# MariaDB must already exist; the collector auto-creates tables.
export LUX_STORAGE_TYPE=mariadb
export LUX_MARIADB_HOST=localhost
export LUX_MARIADB_USER=luxmon
export LUX_MARIADB_PASSWORD=luxmon
export LUX_MARIADB_DATABASE=luxmon
python3 -m collector
```

## Verify data in MariaDB

```bash
mysql -u luxmon -pluxmon luxmon -e 'SELECT * FROM lux_snapshots ORDER BY ts DESC LIMIT 1;'
mysql -u luxmon -pluxmon luxmon -e 'SELECT name, value, unit FROM lux_registers WHERE ts > NOW() - INTERVAL 1 MINUTE ORDER BY ts DESC;'
```

## Verify data in InfluxDB

```bash
curl -G 'http://localhost:8086/query?db=solar' \
  --data-urlencode 'q=SELECT * FROM inverter ORDER BY time DESC LIMIT 1'
```

## Stop

```bash
cd docker
docker compose down
```
