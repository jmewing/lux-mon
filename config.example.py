"""Example runtime config for python -m collector --config config.py"""

# Dongle settings
config.dongle_host = "192.168.1.100"
config.dongle_port = 8000

# Storage backend: "mariadb" (recommended on Alpha) or "influxdb"
config.storage_type = "mariadb"

# Write interval in seconds
config.write_interval = 30

# MariaDB settings (when storage_type == "mariadb")
config.mariadb_host = "localhost"
config.mariadb_port = 3306
config.mariadb_user = "luxmon"
config.mariadb_password = "luxmon"
config.mariadb_database = "luxmon"

# InfluxDB settings (when storage_type == "influxdb")
# config.influx_url = "http://localhost:8086"
# config.influx_token = "lux-mon-token"
# config.influx_org = "luxmon"
# config.influx_bucket = "solar"

# Replay a capture file instead of connecting to the dongle (dev mode)
# config.replay_file = "tests/capture_raw.bin"
