#!/bin/bash
# Install InfluxDB 1.x, Mosquitto, and Grafana on Debian/Ubuntu (e.g. Raspberry Pi)
set -e

echo "==> lux-mon Grafana/MQTT/InfluxDB stack installer"

# ── InfluxDB 1.x ─────────────────────────────────────────────────────────
if ! command -v influxd >/dev/null 2>&1; then
  echo "==> Installing InfluxDB 1.x..."
  # InfluxData archive key + repo (Debian 12 / Ubuntu 24.04 compatible)
  curl -fsSL https://repos.influxdata.com/influxdata-archive_compat.key | sudo gpg --dearmor -o /usr/share/keyrings/influxdata-archive_compat.gpg
  echo "deb [signed-by=/usr/share/keyrings/influxdata-archive_compat.gpg] https://repos.influxdata.com/debian stable main" | sudo tee /etc/apt/sources.list.d/influxdata.list
  sudo apt-get update
  sudo apt-get install -y influxdb
  sudo systemctl enable influxdb
  sudo systemctl start influxdb
else
  echo "==> InfluxDB already installed"
fi

# Create luxmon database and user if not present
echo "==> Configuring InfluxDB database..."
if ! influx -execute "SHOW DATABASES" | grep -q "^luxmon$"; then
  influx -execute "CREATE DATABASE luxmon"
fi
influx -execute "CREATE USER luxmon WITH PASSWORD 'luxmon' WITH ALL PRIVILEGES" || true
influx -execute "GRANT ALL ON luxmon TO luxmon" || true

# Bind to localhost only for security
if ! grep -q "^bind-address = \"127.0.0.1:8086\"" /etc/influxdb/influxdb.conf 2>/dev/null; then
  sudo sed -i 's/^# bind-address = \"127.0.0.1:8086\"/bind-address = \"127.0.0.1:8086\"/' /etc/influxdb/influxdb.conf || true
  sudo sed -i 's/^bind-address = \":8086\"/bind-address = \"127.0.0.1:8086\"/' /etc/influxdb/influxdb.conf || true
  sudo systemctl restart influxdb
fi

# ── Mosquitto MQTT ───────────────────────────────────────────────────────
if ! command -v mosquitto >/dev/null 2>&1; then
  echo "==> Installing Mosquitto..."
  sudo apt-get install -y mosquitto mosquitto-clients
  sudo systemctl enable mosquitto
else
  echo "==> Mosquitto already installed"
fi

# Allow anonymous local access for Home Assistant / lux-mon on same host
if ! grep -q "^allow_anonymous true" /etc/mosquitto/conf.d/local.conf 2>/dev/null; then
  echo "==> Configuring Mosquitto for anonymous localhost access..."
  sudo tee /etc/mosquitto/conf.d/local.conf > /dev/null <<'EOF'
allow_anonymous true
listener 1883 127.0.0.1
EOF
  sudo systemctl restart mosquitto
fi

# ── Grafana ──────────────────────────────────────────────────────────────
if ! command -v grafana-server >/dev/null 2>&1; then
  echo "==> Installing Grafana..."
  sudo apt-get install -y apt-transport-https software-properties-common wget
  wget -q -O /usr/share/keyrings/grafana.key https://apt.grafana.com/gpg.key
  echo "deb [signed-by=/usr/share/keyrings/grafana.key] https://apt.grafana.com stable main" | sudo tee /etc/apt/sources.list.d/grafana.list
  sudo apt-get update
  sudo apt-get install -y grafana
  sudo systemctl enable grafana-server
else
  echo "==> Grafana already installed"
fi

# Provision lux-mon datasource + dashboards
echo "==> Provisioning Grafana for lux-mon..."
sudo mkdir -p /etc/grafana/provisioning/datasources
sudo mkdir -p /etc/grafana/provisioning/dashboards
sudo mkdir -p /var/lib/grafana/dashboards/lux-mon

sudo cp "$(dirname "$0")/../grafana/provisioning/datasources/influxdb.yaml" /etc/grafana/provisioning/datasources/lux-mon.yaml
sudo cp "$(dirname "$0")/../grafana/provisioning/dashboards/dashboards.yaml" /etc/grafana/provisioning/dashboards/lux-mon.yaml
sudo cp "$(dirname "$0")/../grafana/dashboards/lux-mon-charts.json" /var/lib/grafana/dashboards/lux-mon/

# Bind Grafana to localhost only; it will be exposed via the same Apache reverse proxy as lux-mon
sudo sed -i 's/^;\?http_addr =.*/http_addr = 127.0.0.1/' /etc/grafana/grafana.conf 2>/dev/null || true
sudo sed -i 's/^;\?http_addr =.*/http_addr = 127.0.0.1/' /etc/grafana/grafana.ini 2>/dev/null || true

sudo systemctl restart grafana-server

echo "==> Done. Grafana dashboards at http://127.0.0.1:3000/grafana/d/lux-mon-charts/lux-mon-charts"
echo "==> InfluxDB at http://127.0.0.1:8086 (db: luxmon)"
echo "==> MQTT at 127.0.0.1:1883"
