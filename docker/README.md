# Quick Start

## Run InfluxDB + Grafana locally

```bash
cd docker
docker compose up -d
```

- InfluxDB: http://localhost:8086  (luxmon / lux-mon-password)
- Grafana:  http://localhost:3000   (luxmon / lux-mon-password)

## Run the collector

```bash
# Native Python
python3 -m collector.collector

# Or with a config file
python3 -m collector.collector config/collector.py
```

## Verify data

```bash
curl -G 'http://localhost:8086/query?db=solar' \
  --data-urlencode 'q=SELECT * FROM inverter ORDER BY time DESC LIMIT 1'
```

## Stop

```bash
cd docker
docker compose down
```
