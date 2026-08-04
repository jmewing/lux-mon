"""
lux-mon collector.

Connects to an inverter or dongle via a pluggable transport, decodes incoming
Modbus register frames, and writes snapshots to one or more output backends on
a configurable interval.

Supported transports:
    tcp_passive   - listen to a LuxPower/EG4 WiFi dongle broadcast stream
    tcp_active    - actively poll via the dongle's Modbus TCP gateway mode
    replay        - replay a captured binary file for offline testing

Supported output backends (can be enabled together):
    mariadb       - existing relational snapshots + hourly rollups
    influxdb      - SolarAssistant-compatible InfluxDB 1.x/2.x line protocol
    mqtt          - Home Assistant auto-discovery + raw state topic

Future transports:
    rtu_serial    - Modbus RTU over RS485 (USB adapter)
    solarman      - Solarman WiFi dongle local protocol
"""

import os
import time
import logging
import signal
import sys
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable
from threading import Thread, Event
from pathlib import Path

from .protocol import LuxFrame
from .drivers import ModelDriver
from .drivers.registry import get_driver, DEFAULT_MODEL
from .outputs import Outputs, OutputConfig


def _env_or(key: str, default=None, cast=None):
    val = os.environ.get(key)
    if val is None:
        return default
    return cast(val) if cast else val


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").lower()
    return val in ("1", "true", "yes") if val else default


logger = logging.getLogger("luxmon.collector")


@dataclass
class CollectorConfig:
    """Configuration for the collector."""
    dongle_host: str = "192.168.1.100"
    dongle_port: int = 8000
    read_timeout: float = 60.0
    reconnect_delay: float = 5.0
    write_interval: int = 30  # seconds between storage writes
    replay_file: Optional[str] = None  # if set, replay a capture file instead of live TCP

    # Transport selection. Options: tcp_passive, tcp_active, replay
    transport: str = "tcp_active"

    # Active polling settings (used by tcp_active)
    poll_interval: float = 2.0  # seconds between poll requests
    poll_register_start: int = 0  # first register to poll
    poll_register_count: int = 40  # registers per poll request (legacy single-batch mode)

    # Dongle/inverter serials (required for active polling)
    datalog_serial: str = ""
    inverter_serial: str = ""

    # Output backends (multiple can be enabled)
    outputs: OutputConfig = field(default_factory=OutputConfig)

    # Extra transport options passed through to transport constructors
    transport_options: dict = field(default_factory=dict)


def config_from_env() -> CollectorConfig:
    """Build a CollectorConfig from environment variables."""
    return CollectorConfig(
        dongle_host=_env_or("LUX_DONGLE_HOST", "192.168.1.100"),
        dongle_port=_env_or("LUX_DONGLE_PORT", 8000, int),
        read_timeout=_env_or("LUX_READ_TIMEOUT", 60.0, float),
        reconnect_delay=_env_or("LUX_RECONNECT_DELAY", 5.0, float),
        write_interval=_env_or("LUX_WRITE_INTERVAL", 30, int),
        replay_file=_env_or("LUX_REPLAY_FILE"),
        transport=_env_or("LUX_TRANSPORT", "tcp_active"),
        poll_interval=_env_or("LUX_POLL_INTERVAL", 2.0, float),
        poll_register_start=_env_or("LUX_POLL_REG_START", 0, int),
        poll_register_count=_env_or("LUX_POLL_REG_COUNT", 40, int),
        datalog_serial=_env_or("LUX_DATALOG_SERIAL", ""),
        inverter_serial=_env_or("LUX_INVERTER_SERIAL", ""),
        outputs=OutputConfig(
            # Legacy LUX_STORAGE_TYPE=influxdb maps to enabling InfluxDB
            mariadb_enabled=not (_env_or("LUX_STORAGE_TYPE") == "influxdb"),
            mariadb_host=_env_or("LUX_MARIADB_HOST", "localhost"),
            mariadb_port=_env_or("LUX_MARIADB_PORT", 3306, int),
            mariadb_user=_env_or("LUX_MARIADB_USER", "luxmon"),
            mariadb_password=_env_or("LUX_MARIADB_PASSWORD", "luxmon"),
            mariadb_database=_env_or("LUX_MARIADB_DATABASE", "luxmon"),
            mariadb_table_prefix=_env_or("LUX_MARIADB_TABLE_PREFIX", "lux_"),

            influx_enabled=_env_bool("LUX_INFLUX_ENABLED", _env_or("LUX_STORAGE_TYPE") == "influxdb"),
            influx_url=_env_or("LUX_INFLUX_URL", "http://localhost:8086"),
            influx_token=_env_or("LUX_INFLUX_TOKEN", ""),
            influx_org=_env_or("LUX_INFLUX_ORG", "luxmon"),
            influx_bucket=_env_or("LUX_INFLUX_BUCKET", "solar"),
            influx_username=_env_or("LUX_INFLUX_USERNAME", ""),
            influx_password=_env_or("LUX_INFLUX_PASSWORD", ""),
            influx_database=_env_or("LUX_INFLUX_DATABASE", "luxmon"),

            mqtt_enabled=_env_bool("LUX_MQTT_ENABLED"),
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
        ),
        transport_options=_load_transport_options(),
    )


def _load_transport_options() -> dict:
    """Load generic transport options from environment if present."""
    # Currently no dedicated env vars; reserved for future use.
    return {}


def _load_db_serials(cfg: CollectorConfig) -> None:
    """Fill datalog/inverter serials from MariaDB settings if env didn't set them.

    Environment variables take precedence so deployments can keep using .env
    while the web UI can store the values in the database.
    """
    try:
        import pymysql
        from .settings import get

        conn = pymysql.connect(
            host=cfg.outputs.mariadb_host,
            port=cfg.outputs.mariadb_port,
            user=cfg.outputs.mariadb_user,
            password=cfg.outputs.mariadb_password,
            database=cfg.outputs.mariadb_database,
            autocommit=True,
        )
        try:
            if not cfg.datalog_serial:
                cfg.datalog_serial = get(conn, "datalog_serial") or ""
            if not cfg.inverter_serial:
                cfg.inverter_serial = get(conn, "inverter_serial") or ""
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to load serials from MariaDB settings")


def _load_db_setting(name: str) -> Optional[str]:
    """Load a single setting value from MariaDB, or None if unavailable."""
    try:
        from .settings import get
        conn = _get_db_conn()
        if conn is None:
            return None
        try:
            return get(conn, name)
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to load setting %s from MariaDB", name)
    return None


def _load_db_output_settings(cfg: CollectorConfig) -> None:
    """Fill output backend settings from MariaDB if env didn't set them.

    Only fills keys that are empty/default in the environment so that .env
    continues to override the database.
    """
    out = cfg.outputs
    try:
        import pymysql
        from .settings import get

        conn = pymysql.connect(
            host=out.mariadb_host,
            port=out.mariadb_port,
            user=out.mariadb_user,
            password=out.mariadb_password,
            database=out.mariadb_database,
            autocommit=True,
        )
        try:
            def _override(key: str, current, cast=None):
                if current is True or (isinstance(current, str) and current):
                    return current
                db_val = get(conn, key)
                if db_val is None:
                    return current
                if cast == bool:
                    return str(db_val).lower() in ("1", "true", "yes")
                if cast == int:
                    try:
                        return int(db_val)
                    except ValueError:
                        return current
                return str(db_val) if db_val is not None else current

            out.mariadb_enabled = _override("mariadb_enabled", out.mariadb_enabled, bool)
            out.influx_enabled = _override("influx_enabled", out.influx_enabled, bool)
            out.influx_url = _override("influx_url", out.influx_url)
            out.influx_database = _override("influx_database", out.influx_database)
            out.influx_token = _override("influx_token", out.influx_token)
            out.influx_org = _override("influx_org", out.influx_org)
            out.influx_username = _override("influx_username", out.influx_username)
            out.influx_password = _override("influx_password", out.influx_password)

            out.mqtt_enabled = _override("mqtt_enabled", out.mqtt_enabled, bool)
            out.mqtt_host = _override("mqtt_host", out.mqtt_host)
            out.mqtt_port = _override("mqtt_port", out.mqtt_port, int)
            out.mqtt_username = _override("mqtt_username", out.mqtt_username)
            out.mqtt_password = _override("mqtt_password", out.mqtt_password)
            out.mqtt_topic_prefix = _override("mqtt_topic_prefix", out.mqtt_topic_prefix)
            out.mqtt_ha_discovery = _override("mqtt_ha_discovery", out.mqtt_ha_discovery, bool)
            out.mqtt_device_name = _override("mqtt_device_name", out.mqtt_device_name)
            out.mqtt_device_id = _override("mqtt_device_id", out.mqtt_device_id)
            out.temperature_unit = _override("temperature_unit", out.temperature_unit)

            out.alerts_enabled = _override("alerts_enabled", out.alerts_enabled, bool)
            out.alerts_soc_low = _override("alerts_soc_low", out.alerts_soc_low, float)
            out.alerts_soc_critical = _override("alerts_soc_critical", out.alerts_soc_critical, float)
            out.alerts_battery_temp_high = _override("alerts_battery_temp_high", out.alerts_battery_temp_high, float)
            out.alerts_inverter_temp_high = _override("alerts_inverter_temp_high", out.alerts_inverter_temp_high, float)
            out.alerts_grid_lost_threshold_sec = _override("alerts_grid_lost_threshold_sec", out.alerts_grid_lost_threshold_sec, float)
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to load output settings from MariaDB")


def _create_transport(cfg: CollectorConfig, on_frame: Callable[[LuxFrame], None], driver: ModelDriver):
    """Instantiate the configured transport."""
    transport = cfg.transport.lower()

    if transport == "replay":
        from .comm.replay import ReplayTransport
        if not cfg.replay_file:
            raise ValueError("LUX_REPLAY_FILE is required when transport=replay")
        return ReplayTransport(on_frame, cfg.replay_file)

    if transport == "tcp_passive":
        from .comm.tcp_passive import TcpPassiveTransport
        return TcpPassiveTransport(
            on_frame,
            host=cfg.dongle_host,
            port=cfg.dongle_port,
            reconnect_delay=cfg.reconnect_delay,
            read_timeout=cfg.read_timeout,
        )

    if transport == "tcp_active":
        from .comm.tcp_active import TcpActiveTransport
        if not cfg.datalog_serial or not cfg.inverter_serial:
            raise ValueError(
                "LUX_DATALOG_SERIAL and LUX_INVERTER_SERIAL are required "
                "when transport=tcp_active"
            )
        return TcpActiveTransport(
            on_frame,
            host=cfg.dongle_host,
            port=cfg.dongle_port,
            datalog_serial=cfg.datalog_serial,
            inverter_serial=cfg.inverter_serial,
            reconnect_delay=cfg.reconnect_delay,
            read_timeout=cfg.read_timeout,
            poll_interval=cfg.poll_interval,
            batches=driver.batches,
        )

    raise ValueError(f"Unknown transport: {cfg.transport}")


class PassiveCollector:
    """Collector that receives frames from a transport and writes snapshots."""

    def __init__(self, config: CollectorConfig,
                 on_snapshot: Optional[Callable[[Dict], None]] = None,
                 driver: Optional[ModelDriver] = None):
        self.cfg = config
        self.driver = driver or get_driver(DEFAULT_MODEL)
        self._on_snapshot = on_snapshot
        self._stop = Event()
        self._transport = None
        self._writer: Optional[Thread] = None
        self._outputs: Optional[Outputs] = None
        self._latest_input_raw: Dict = {}
        self._latest_hold_raw: Dict = {}
        self._latest_decoded: Dict = {}
        self._last_write = 0.0

        # Register handler for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        self.stop()

    def start(self) -> None:
        """Start the transport and writer threads."""
        if self.cfg.replay_file:
            logger.info("Starting lux-mon collector (transport=replay, file=%s)",
                        self.cfg.replay_file)
        else:
            logger.info("Starting lux-mon collector (transport=%s, %s:%d)",
                        self.cfg.transport, self.cfg.dongle_host, self.cfg.dongle_port)

        # Wire outputs before transport starts so callbacks are ready.
        self.cfg.outputs._write_interval = float(self.cfg.write_interval)
        self._outputs = Outputs(self.cfg.outputs, tz_name="UTC")

        self._transport = _create_transport(self.cfg, self._handle_frame, self.driver)
        self._transport.start()

        self._writer = Thread(target=self._writer_loop, name="lux-writer", daemon=True)
        self._writer.start()

    def stop(self) -> None:
        """Signal the collector to stop and release resources."""
        self._stop.set()
        if self._transport:
            self._transport.stop()
        if self._outputs:
            self._outputs.close()

    def wait(self) -> None:
        """Block until the collector stops."""
        if self._writer and self._writer.is_alive():
            self._writer.join()

    def _handle_frame(self, frame: LuxFrame) -> None:
        """Process a parsed frame delivered by the transport."""
        if not frame.is_translated_data:
            return

        if frame.is_read_input:
            for i, raw_val in enumerate(frame.values):
                reg_num = frame.register + i
                self._latest_input_raw[reg_num] = raw_val

            if frame.values:
                logger.debug("Input frame: %d regs starting @ %d",
                             len(frame.values), frame.register)

        elif frame.is_read_hold:
            for i, raw_val in enumerate(frame.values):
                reg_num = frame.register + i
                self._latest_hold_raw[reg_num] = raw_val

            if frame.values:
                logger.debug("Hold frame: %d regs starting @ %d",
                             len(frame.values), frame.register)

    def _writer_loop(self) -> None:
        """Periodic writer thread: decode registers and write to storage."""
        while not self._stop.wait(self.cfg.write_interval):
            if not self._latest_input_raw:
                logger.warning("No input data received yet, skipping write")
                continue

            if 4 not in self._latest_input_raw:
                logger.warning("Input register batch incomplete, skipping write")
                continue

            try:
                self._latest_decoded = self.driver.decode(self._latest_input_raw)
                self._clamp_values(self._latest_decoded)
                if self._outputs:
                    self._outputs.write(self._latest_decoded, self._latest_input_raw)
                    self._outputs.evaluate_alerts(self._latest_decoded)
                self._last_write = time.time()

                if self._on_snapshot:
                    try:
                        self._on_snapshot(self._latest_decoded)
                    except Exception:
                        logger.exception("Snapshot callback failed")

            except Exception:
                logger.exception("Failed to write snapshot")

        logger.info("Writer loop exiting")

    # ── Sanity clamping ──────────────────────────────────────────────
    _SANITY_LIMITS: Dict = {
        "pv1_power": 8000,
        "pv2_power": 8000,
        "grid_import_power": 6000,
        "grid_export_power": 6000,
        "charge_power": 5000,
        "discharge_power": 5000,
        "eps_power": 6000,
        "battery_voltage": 65,
        "battery_current": 150,
        "temp_inverter": 100,
        "temp_battery": 80,
        "temp_radiator_1": 100,
        "temp_radiator_2": 100,
        "soc": 100,
        "soh": 100,
    }

    def _clamp_values(self, decoded: dict) -> None:
        """Clamp decoded values to physical sanity limits in-place."""
        for key, limit in self._SANITY_LIMITS.items():
            if key in decoded:
                val = decoded[key]["value"]
                if val < 0:
                    decoded[key]["value"] = 0.0
                elif val > limit:
                    logger.warning(
                        "Clamping %s: %.0f → %.0f (limit %.0f)",
                        key, val, limit, limit,
                    )
                    decoded[key]["value"] = limit

    @property
    def stats(self) -> dict:
        """Return current collector statistics."""
        transport_stats = self._transport.stats() if self._transport else {}
        return {
            **transport_stats,
            "last_write": self._last_write,
            "input_registers_known": len(self._latest_input_raw),
            "hold_registers_known": len(self._latest_hold_raw),
        }


def run_collector(
    config_path: Optional[str] = None,
    log_level: str = "INFO",
    overrides: Optional[Dict] = None,
) -> None:
    """CLI entrypoint: load config and run the collector."""
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = config_from_env()
    if config_path:
        _load_config(cfg, Path(config_path))

    if overrides:
        for key, value in overrides.items():
            if value is not None and hasattr(cfg, key):
                setattr(cfg, key, value)

    # Fill serials and output settings from DB if not provided by environment.
    _load_db_serials(cfg)
    _load_db_output_settings(cfg)

    # Legacy LUX_POLL_MODE=true maps to tcp_active for backwards compatibility
    poll_mode = os.environ.get("LUX_POLL_MODE", "").lower() in ("1", "true", "yes")
    if poll_mode and cfg.transport == "tcp_passive":
        cfg.transport = "tcp_active"
        logger.warning(
            "LUX_POLL_MODE=true is deprecated; set LUX_TRANSPORT=tcp_active instead"
        )

    inverter_model = _env_or("LUX_INVERTER_MODEL") or _load_db_setting("inverter_model") or DEFAULT_MODEL
    try:
        driver = get_driver(inverter_model)
        logger.info("Using inverter driver: %s", driver.label)
    except ValueError as exc:
        logger.error("%s; falling back to %s", exc, DEFAULT_MODEL)
        driver = get_driver(DEFAULT_MODEL)

    collector = PassiveCollector(cfg, driver=driver)
    collector.start()
    collector.wait()


def _load_config(cfg: CollectorConfig, path: Path) -> None:
    """Load collector config from a Python file."""
    if not path.exists():
        return

    ns = {"config": cfg}
    exec(compile(path.read_text(), str(path), "exec"), ns)


if __name__ == "__main__":
    run_collector(sys.argv[1] if len(sys.argv) > 1 else None)
