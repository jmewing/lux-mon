#!/bin/sh
set -e

# Wait a moment for InfluxDB to finish its own setup
sleep 5

# Create a v1 DBRP mapping so the provisioned InfluxQL datasource works
influx v1 dbrp create \
  --db luxmon \
  --rp autogen \
  --bucket luxmon \
  --default \
  --org "${INFLUX_ORG:-luxmon}" \
  --token "${INFLUX_TOKEN}" \
  --host "${INFLUX_HOST:-http://influxdb:8086}" \
  || true

echo "InfluxDB v1 DBRP mapping created (or already present)."
