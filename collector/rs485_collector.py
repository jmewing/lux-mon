"""RS-485 collector daemon for lux-mon.

Runs alongside (or independently of) the main TCP collector. It polls an
RS-485/serial device using a pluggable driver and writes the decoded snapshot
to the same backends as the main collector (MariaDB, InfluxDB, MQTT).

Environment variables:
  LUX_RS485_ENABLED        - set to "true" to enable this collector
  LUX_RS485_PORT           - serial port, default /dev/ttyUSB0
  LUX_RS485_BAUD           - baud rate, default 115200
  LUX_RS485_DEVICE_TYPE    - jk_bms | modbus_rtu | raw
  LUX_RS485_POLL_INTERVAL  - seconds between reads, default 2.0
  LUX_RS485_SLAVE_ID       - Modbus slave ID, default 1
  LUX_RS485_MODBUS_START   - Modbus register start, default 0
  LUX_RS485_MODBUS_COUNT   - Modbus register count, default 40
  LUX_RS485_PREFIX         - measurement/topic prefix, default "rs485"

Plus all standard LUX_MARIADB_*, LUX_INFLUX_*, LUX_MQTT_* variables.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path
from threading import Event
from typing import Any, Dict, Optional

from .collector import _env_bool, _env_or
from .outputs import OutputConfig, Outputs
from .rs485 import Rs485DeviceConfig
from .rs485.registry import get_device

logger = logging.getLogger("luxmon.rs485_collector")


RS485_ENV_PREFIX = "LUX_RS485_"


def _rs485_env_or(key: str, default=None, cast=None):
    val = os.environ.get(f"{RS485_ENV_PREFIX}{key}")
    if val is None:
        return default
    return cast(val) if cast else val


def _rs485_env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(f"{RS485_ENV_PREFIX}{key}", "").lower()
    return val in ("1", "true", "yes") if val else default


def rs485_config_from_env() -> Rs485DeviceConfig:
    """Build an Rs485DeviceConfig from environment variables."""
    return Rs485DeviceConfig(
        port=_rs485_env_or("PORT", "/dev/ttyUSB0"),
        baudrate=_rs485_env_or("BAUD", 115200, int),
        timeout=_rs485_env_or("TIMEOUT", 1.0, float),
        poll_interval=_rs485_env_or("POLL_INTERVAL", 2.0, float),
        device_type=_rs485_env_or("DEVICE_TYPE", "jk_bms"),
        slave_id=_rs485_env_or("SLAVE_ID", 1, int),
        modbus_start=_rs485_env_or("MODBUS_START", 0, int),
        modbus_count=_rs485_env_or("MODBUS_COUNT", 40, int),
        modbus_function="input" if _rs485_env_or("MODBUS_FUNCTION", "input") == "input" else "holding",
        options={},
    )


def _output_config_from_env() -> OutputConfig:
    """Build an OutputConfig from the standard lux-mon env vars."""
    return OutputConfig(
        mariadb_enabled=_env_bool("LUX_MARIADB_ENABLED", True),
        mariadb_host=_env_or("LUX_MARIADB_HOST", "localhost"),
        mariadb_port=_env_or("LUX_MARIADB_PORT", 3306, int),
        mariadb_user=_env_or("LUX_MARIADB_USER", "luxmon"),
        mariadb_password=_env_or("LUX_MARIADB_PASSWORD", "luxmon"),
        mariadb_database=_env_or("LUX_MARIADB_DATABASE", "luxmon"),
        mariadb_table_prefix=_env_or("LUX_MARIADB_TABLE_PREFIX", "lux_"),

        influx_enabled=_env_bool("LUX_INFLUX_ENABLED", False),
        influx_url=_env_or("LUX_INFLUX_URL", "http://localhost:8086"),
        influx_token=_env_or("LUX_INFLUX_TOKEN", ""),
        influx_org=_env_or("LUX_INFLUX_ORG", "luxmon"),
        influx_bucket=_env_or("LUX_INFLUX_BUCKET", "luxmon"),
        influx_username=_env_or("LUX_INFLUX_USERNAME", ""),
        influx_password=_env_or("LUX_INFLUX_PASSWORD", ""),
        influx_database=_env_or("LUX_INFLUX_DATABASE", "luxmon"),

        mqtt_enabled=_env_bool("LUX_MQTT_ENABLED", False),
        mqtt_host=_env_or("LUX_MQTT_HOST", "localhost"),
        mqtt_port=_env_or("LUX_MQTT_PORT", 1883, int),
        mqtt_username=_env_or("LUX_MQTT_USERNAME", ""),
        mqtt_password=_env_or("LUX_MQTT_PASSWORD", ""),
        mqtt_topic_prefix=_env_or("LUX_MQTT_TOPIC_PREFIX", "luxmon"),
        mqtt_ha_discovery=_env_bool("LUX_MQTT_HA_DISCOVERY", True),
        mqtt_ha_prefix=_env_or("LUX_MQTT_HA_PREFIX", "homeassistant"),
        mqtt_device_name=_env_or("LUX_MQTT_DEVICE_NAME", "luxmon"),
        mqtt_device_id=_env_or("LUX_MQTT_DEVICE_ID", "luxmon_solar"),
        temperature_unit=_env_or("LUX_TEMPERATURE_UNIT", "celsius"),

        alerts_enabled=_env_bool("LUX_ALERTS_ENABLED"),
        alerts_soc_low=_env_or("LUX_ALERTS_SOC_LOW", 20.0, float),
        alerts_soc_critical=_env_or("LUX_ALERTS_SOC_CRITICAL", 10.0, float),
        alerts_battery_temp_high=_env_or("LUX_ALERTS_BATTERY_TEMP_HIGH", 50.0, float),
        alerts_inverter_temp_high=_env_or("LUX_ALERTS_INVERTER_TEMP_HIGH", 60.0, float),
        alerts_grid_lost_threshold_sec=_env_or("LUX_ALERTS_GRID_LOST_THRESHOLD_SEC", 30.0, float),

        alerts_email_enabled=_env_bool("LUX_ALERTS_EMAIL_ENABLED"),
        alerts_email_smtp_host=_env_or("LUX_ALERTS_EMAIL_SMTP_HOST", ""),
        alerts_email_smtp_port=_env_or("LUX_ALERTS_EMAIL_SMTP_PORT", 587, int),
        alerts_email_username=_env_or("LUX_ALERTS_EMAIL_USERNAME", ""),
        alerts_email_password=_env_or("LUX_ALERTS_EMAIL_PASSWORD", ""),
        alerts_email_from=_env_or("LUX_ALERTS_EMAIL_FROM", ""),
        alerts_email_to=_env_or("LUX_ALERTS_EMAIL_TO", ""),
        alerts_email_tls=_env_bool("LUX_ALERTS_EMAIL_TLS", True),
        alerts_webhook_enabled=_env_bool("LUX_ALERTS_WEBHOOK_ENABLED"),
        alerts_webhook_url=_env_or("LUX_ALERTS_WEBHOOK_URL", ""),
    )


def _prefix_keys(data: Dict[str, Dict[str, Any]], prefix: str) -> Dict[str, Dict[str, Any]]:
    """Prefix field names so they don't collide with inverter data."""
    if not prefix:
        return data
    prefixed: Dict[str, Dict[str, Any]] = {}
    for key, value in data.items():
        if key.startswith(f"{prefix}_"):
            prefixed[key] = value
        else:
            prefixed[f"{prefix}_{key}"] = value
    return prefixed


def _numeric_values(data: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Return only entries whose value can be cast to float.

    String fields (e.g. raw hex dumps) are dropped because MariaDB/InfluxDB
    expect numeric values. Strings can still be inspected in discovery mode.
    """
    numeric: Dict[str, Dict[str, Any]] = {}
    for key, info in data.items():
        if not isinstance(info, dict) or "value" not in info:
            continue
        val = info["value"]
        if isinstance(val, bool):
            numeric[key] = info
            continue
        try:
            float(val)
            numeric[key] = info
        except (TypeError, ValueError):
            logger.debug("Dropping non-numeric field %r (value=%r)", key, val)
    return numeric


class Rs485Collector:
    """Polls an RS-485 device and writes snapshots to lux-mon backends."""

    def __init__(
        self,
        device_cfg: Rs485DeviceConfig,
        output_cfg: OutputConfig,
        prefix: str = "rs485",
        write_interval: float = 30.0,
    ):
        self.device_cfg = device_cfg
        self.output_cfg = output_cfg
        self.prefix = prefix
        self.write_interval = write_interval
        self._stop = Event()
        self._device: Optional[Any] = None
        self._outputs: Optional[Outputs] = None

        # Register handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        self._stop.set()

    def run(self) -> None:
        """Main loop: connect, poll, write."""
        logger.info(
            "Starting RS-485 collector (type=%s, port=%s, %d baud)",
            self.device_cfg.device_type,
            self.device_cfg.port,
            self.device_cfg.baudrate,
        )

        self._device = get_device(self.device_cfg)
        self._outputs = Outputs(self.output_cfg, tz_name="UTC")

        next_write = 0.0
        latest_data: Dict[str, Dict[str, Any]] = {}

        while not self._stop.is_set():
            start = time.time()
            try:
                data = self._device.read()
                if data:
                    latest_data = data
                    logger.debug("Read %d fields from RS-485 device", len(data))
                else:
                    logger.debug("No data from RS-485 device this poll")
            except Exception:
                logger.exception("Failed to read from RS-485 device")
                time.sleep(self.device_cfg.poll_interval)
                continue

            if latest_data and time.time() >= next_write:
                try:
                    prefixed = _prefix_keys(latest_data, self.prefix)
                    numeric = _numeric_values(prefixed)
                    if numeric:
                        self._outputs.write(numeric, raw_registers={})
                except Exception:
                    logger.exception("Failed to write RS-485 snapshot")
                next_write = time.time() + self.write_interval

            elapsed = time.time() - start
            sleep_time = max(0.0, self.device_cfg.poll_interval - elapsed)
            if sleep_time > 0 and not self._stop.is_set():
                time.sleep(sleep_time)

        self.stop()
        logger.info("RS-485 collector stopped")

    def stop(self) -> None:
        self._stop.set()
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                logger.exception("Error closing RS-485 device")
        if self._outputs is not None:
            try:
                self._outputs.close()
            except Exception:
                logger.exception("Error closing outputs")


def run_rs485_collector(log_level: str = "INFO") -> None:
    """CLI entry point for the RS-485 collector."""
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not _rs485_env_bool("ENABLED", False):
        logger.error("LUX_RS485_ENABLED is not set to true. Exiting.")
        sys.exit(1)

    device_cfg = rs485_config_from_env()
    output_cfg = _output_config_from_env()
    prefix = _rs485_env_or("PREFIX", "rs485")
    write_interval = _rs485_env_or("WRITE_INTERVAL", 30.0, float)

    collector = Rs485Collector(device_cfg, output_cfg, prefix=prefix, write_interval=write_interval)
    collector.run()
