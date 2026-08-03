#!/bin/bash
# Install/configure InfluxDB 2.x, Mosquitto, and Grafana on Debian/Ubuntu (e.g. Raspberry Pi)
set -e

ALPHA_USER=$(whoami)
echo "==> lux-mon Grafana/MQTT/InfluxDB stack installer (InfluxDB 2.x aware)"

# ── InfluxDB 2.x ───────────────────────────────────────────────────────
if ! command -v influxd >/dev/null 2>&1; then
  echo "==> Installing InfluxDB 2.x..."
  curl -fsSL https://repos.influxdata.com/influxdata-archive_compat.key | sudo gpg --dearmor -o /usr/share/keyrings/influxdata-archive_compat.gpg
  echo "deb [signed-by=/usr/share/keyrings/influxdata-archive_compat.gpg] https://repos.influxdata.com/debian stable main" | sudo tee /etc/apt/sources.list.d/influxdata.list
  sudo apt-get update
  sudo apt-get install -y influxdb2
else
  echo "==> InfluxDB already installed"
fi

sudo systemctl enable influxdb || true
sudo systemctl start influxdb || true

# Wait for HTTP API
for i in {1..30}; do
  if curl -fsS http://127.0.0.1:8086/api/v2/setup >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Bootstrap if not already set up
SETUP_ALLOWED=$(curl -fsS http://127.0.0.1:8086/api/v2/setup 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('allowed', False))")
if [[ "$SETUP_ALLOWED" == "True" ]]; then
  if [[ -z "${LUX_INFLUX_ADMIN_PASSWORD:-}" ]]; then
    echo "ERROR: InfluxDB needs bootstrapping. Set LUX_INFLUX_ADMIN_PASSWORD before running this script."
    echo "Example: LUX_INFLUX_ADMIN_PASSWORD='your-secure-password' bash scripts/setup-grafana-stack.sh"
    exit 1
  fi
  echo "==> Bootstrapping InfluxDB 2.x..."
  curl -fsS -X POST http://127.0.0.1:8086/api/v2/setup \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"luxmon\",\"password\":\"$LUX_INFLUX_ADMIN_PASSWORD\",\"org\":\"luxmon\",\"bucket\":\"luxmon\",\"retentionRules\":[{\"type\":\"expire\",\"everySeconds\":0}]}" \
    >/tmp/influx-setup.json
  echo "Bootstrap response saved to /tmp/influx-setup.json"
fi

# Create an all-access token for lux-mon if one doesn't exist
INFLUX_TOKEN=$(curl -fsS -u "luxmon:$LUX_INFLUX_ADMIN_PASSWORD" http://127.0.0.1:8086/api/v2/authorizations 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
for a in data.get('authorizations', []):
    if a.get('description') == 'lux-mon':
        print(a.get('token'))
        break
" || true)

if [[ -z "$INFLUX_TOKEN" ]]; then
  echo "==> Creating lux-mon API token..."
  ORG_ID=$(curl -fsS -u "luxmon:$LUX_INFLUX_ADMIN_PASSWORD" http://127.0.0.1:8086/api/v2/orgs 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('orgs',[{}])[0].get('id',''))" || true)
  USER_ID=$(curl -fsS -u "luxmon:$LUX_INFLUX_ADMIN_PASSWORD" http://127.0.0.1:8086/api/v2/me 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('id',''))" || true)
  if [[ -n "$ORG_ID" && -n "$USER_ID" ]]; then
    TOKEN_RESP=$(curl -fsS -u "luxmon:$LUX_INFLUX_ADMIN_PASSWORD" -X POST http://127.0.0.1:8086/api/v2/authorizations \
      -H "Content-Type: application/json" \
      -d "{\"description\":\"lux-mon\",\"orgID\":\"$ORG_ID\",\"userID\":\"$USER_ID\",\"permissions\":[{\"action\":\"read\",\"resource\":{\"type\":\"buckets\"}},{\"action\":\"write\",\"resource\":{\"type\":\"buckets\"}}]}")
    INFLUX_TOKEN=$(echo "$TOKEN_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('token',''))" || true)
    echo "$TOKEN_RESP" >/tmp/influx-token.json
  fi
fi

if [[ -n "$INFLUX_TOKEN" ]]; then
  echo "==> lux-mon InfluxDB token: $INFLUX_TOKEN"
  echo "$INFLUX_TOKEN" > /tmp/influx-luxmon.token
fi

# ── Mosquitto MQTT ───────────────────────────────────────────────────────
if ! command -v mosquitto >/dev/null 2>&1; then
  echo "==> Installing Mosquitto..."
  sudo apt-get install -y mosquitto mosquitto-clients
  sudo systemctl enable mosquitto
else
  echo "==> Mosquitto already installed"
fi

if ! grep -q "^allow_anonymous true" /etc/mosquitto/conf.d/local.conf 2>/dev/null; then
  echo "==> Configuring Mosquitto for anonymous localhost access..."
  sudo tee /etc/mosquitto/conf.d/local.conf >/dev/null <<'EOF'
allow_anonymous true
listener 1883 127.0.0.1
EOF
  sudo systemctl restart mosquitto
fi

# ── Grafana ──────────────────────────────────────────────────────────────
if ! command -v grafana-server >/dev/null 2>&1; then
  echo "==> Installing Grafana..."
  sudo apt-get install -y apt-transport-https software-properties-common wget
  sudo mkdir -p /etc/apt/keyrings/
  wget -q -O - https://apt.grafana.com/gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
  echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" | sudo tee /etc/apt/sources.list.d/grafana.list
  sudo apt-get update
  sudo apt-get install -y grafana
  sudo systemctl enable grafana-server
else
  echo "==> Grafana already installed"
fi

# Provision datasource + dashboards
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
echo "==> Provisioning Grafana for lux-mon from $REPO_DIR..."
sudo mkdir -p /etc/grafana/provisioning/datasources
sudo mkdir -p /etc/grafana/provisioning/dashboards
sudo mkdir -p /var/lib/grafana/dashboards/lux-mon

# Build a 2.x-compatible datasource YAML if we have a token
if [[ -f /tmp/influx-luxmon.token ]]; then
  TOKEN=$(cat /tmp/influx-luxmon.token)
  # Create InfluxDB v1 DBRP mapping so the imported SolarAssistant InfluxQL dashboard works
  BUCKET_ID=$(curl -fsS -H "Authorization: Token $TOKEN" "http://127.0.0.1:8086/api/v2/buckets?org=luxmon" 2>/dev/null | python3 -c "import json,sys; print([b['id'] for b in json.load(sys.stdin)['buckets'] if b['name']=='luxmon'][0])" || true)
  if [[ -n "$BUCKET_ID" ]]; then
    curl -fsS -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
      -X POST http://127.0.0.1:8086/api/v2/dbrps \
      -d "{\"org\":\"luxmon\",\"bucketID\":\"$BUCKET_ID\",\"database\":\"luxmon\",\"retention_policy\":\"autogen\",\"default\":true}" >/dev/null 2>&1 || true
  fi
  sudo tee /etc/grafana/provisioning/datasources/lux-mon.yaml >/dev/null <<EOF
apiVersion: 1

datasources:
  - name: lux-mon
    type: influxdb
    access: proxy
    url: http://127.0.0.1:8086
    database: luxmon
    isDefault: true
    editable: true
    jsonData:
      version: InfluxQL
      organization: luxmon
      tlsSkipVerify: true
    secureJsonData:
      token: "$TOKEN"
EOF
else
  # Fallback: assume 1.x InfluxDB compatibility mode (not used by default)
  sudo cp "$REPO_DIR/grafana/provisioning/datasources/influxdb.yaml" /etc/grafana/provisioning/datasources/lux-mon.yaml
fi

sudo cp "$REPO_DIR/grafana/provisioning/dashboards/dashboards.yaml" /etc/grafana/provisioning/dashboards/lux-mon.yaml
sudo cp "$REPO_DIR/grafana/dashboards/lux-mon-charts.json" /var/lib/grafana/dashboards/lux-mon/

# Bind Grafana to localhost only
sudo sed -i 's/^;\?http_addr =.*/http_addr = 127.0.0.1/' /etc/grafana/grafana.ini 2>/dev/null || true

sudo systemctl restart grafana-server

echo "==> Done."
echo "==> InfluxDB 2.x: http://127.0.0.1:8086 (org: luxmon, bucket: luxmon)"
echo "==> MQTT: 127.0.0.1:1883"
echo "==> Grafana: http://127.0.0.1:3000/grafana/d/lux-mon-charts/lux-mon-charts"
if [[ -f /tmp/influx-luxmon.token ]]; then
  echo "==> API token saved to /tmp/influx-luxmon.token and printed above"
fi
