"""
lux-mon collector.

Connects to an inverter or dongle via a pluggable transport, decodes incoming
Modbus register frames, and writes snapshots to MariaDB or InfluxDB on a
configurable interval.

Supported transports:
    tcp_passive   - listen to a LuxPower/EG4 WiFi dongle broadcast stream
    tcp_active    - actively poll via the dongle's Modbus TCP gateway mode
    replay        - replay a captured binary file for offline testing

Future transports:
    rtu_serial    - Modbus RTU over RS485 (USB adapter)
    solarman      - Solarman WiFi dongle local protocol
"""

import os
import time
import logging
import signal
import sys
import json
from dataclasses import dataclass, field
from typing import Optional, Callable
from threading import Thread, Event
from pathlib import Path

from .protocol import LuxFrame
from .registers import decode_registers


def _env_or(key: str, default=None, cast=None):
    val = os.environ.get(key)
    if val is None:
        return default
    return cast(val) if cast else val


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

    # Storage backend selection: "mariadb" or "influxdb"
    storage_type: str = "mariadb"

    # InfluxDB settings (used when storage_type == "influxdb")
    influx_url: str = "http://localhost:8086"
    influx_token: str = "lux-mon-token"
    influx_org: str = "luxmon"
    influx_bucket: str = "solar"

    # MariaDB settings (used when storage_type == "mariadb")
    mariadb_host: str = "localhost"
    mariadb_port: int = 3306
    mariadb_user: str = "luxmon"
    mariadb_password: str = "luxmon"
    mariadb_database: str = "luxmon"
    mariadb_table_prefix: str = "lux_"

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
        storage_type=_env_or("LUX_STORAGE_TYPE", "mariadb"),
        influx_url=_env_or("LUX_INFLUX_URL", "http://localhost:8086"),
        influx_token=_env_or("LUX_INFLUX_TOKEN", "lux-mon-token"),
        influx_org=_env_or("LUX_INFLUX_ORG", "luxmon"),
        influx_bucket=_env_or("LUX_INFLUX_BUCKET", "solar"),
        mariadb_host=_env_or("LUX_MARIADB_HOST", "localhost"),
        mariadb_port=_env_or("LUX_MARIADB_PORT", 3306, int),
        mariadb_user=_env_or("LUX_MARIADB_USER", "luxmon"),
        mariadb_password=_env_or("LUX_MARIADB_PASSWORD", "luxmon"),
        mariadb_database=_env_or("LUX_MARIADB_DATABASE", "luxmon"),
        mariadb_table_prefix=_env_or("LUX_MARIADB_TABLE_PREFIX", "lux_"),
        transport_options=_load_transport_options(),
    )


def _load_transport_options() -> dict:
    """Load generic transport options from environment if present."""
    # Currently no dedicated env vars; reserved for future use.
    return {}


def _create_transport(cfg: CollectorConfig, on_frame: Callable[[LuxFrame], None]):
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
        )

    raise ValueError(f"Unknown transport: {cfg.transport}")


class PassiveCollector:
    """Collector that receives frames from a transport and writes snapshots."""

    def __init__(self, config: CollectorConfig,
                 on_snapshot: Optional[Callable[[dict], None]] = None):
        self.cfg = config
        self._on_snapshot = on_snapshot
        self._stop = Event()
        self._transport = None
        self._writer: Optional[Thread] = None
        self._latest_input_raw: dict[int, int] = {}
        self._latest_hold_raw: dict[int, int] = {}
        self._latest_decoded: dict = {}
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

        self._transport = _create_transport(self.cfg, self._handle_frame)
        self._transport.start()

        self._writer = Thread(target=self._writer_loop, name="lux-writer", daemon=True)
        self._writer.start()

    def stop(self) -> None:
        """Signal the collector to stop and release resources."""
        self._stop.set()
        if self._transport:
            self._transport.stop()

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
        writer = self._create_writer()

        while not self._stop.wait(self.cfg.write_interval):
            if not self._latest_input_raw:
                logger.warning("No input data received yet, skipping write")
                continue

            if 4 not in self._latest_input_raw:
                logger.warning("Input register batch incomplete, skipping write")
                continue

            try:
                self._latest_decoded = decode_registers(self._latest_input_raw)
                self._clamp_values(self._latest_decoded)
                self._write_snapshot(writer, self._latest_decoded)
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
    _SANITY_LIMITS: dict[str, float] = {
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

    def _create_writer(self):
        """Create and return a storage writer based on storage_type."""
        if self.cfg.storage_type == "influxdb":
            return self._create_influx_writer()
        elif self.cfg.storage_type == "mariadb":
            return self._create_mariadb_writer()
        else:
            logger.error("Unknown storage_type: %s", self.cfg.storage_type)
            return None

    def _create_influx_writer(self):
        """Create and return an InfluxDB write client."""
        try:
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.write_api import SYNCHRONOUS

            client = InfluxDBClient(
                url=self.cfg.influx_url,
                token=self.cfg.influx_token,
                org=self.cfg.influx_org,
            )
            return client.write_api(write_options=SYNCHRONOUS)
        except ImportError:
            logger.error("influxdb_client not installed; cannot write to InfluxDB")
            return None

    def _create_mariadb_writer(self):
        """Create and return a MariaDB connection."""
        try:
            import pymysql

            conn = pymysql.connect(
                host=self.cfg.mariadb_host,
                port=self.cfg.mariadb_port,
                user=self.cfg.mariadb_user,
                password=self.cfg.mariadb_password,
                database=self.cfg.mariadb_database,
                autocommit=True,
            )
            self._init_mariadb_schema(conn)
            return conn
        except Exception:
            logger.exception("Failed to connect to MariaDB")
            return None

    def _init_mariadb_schema(self, conn) -> None:
        """Create MariaDB tables if they do not exist."""
        prefix = self.cfg.mariadb_table_prefix
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {prefix}snapshots (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    ts DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                    raw_registers JSON,
                    KEY idx_ts (ts)
                ) ENGINE=InnoDB
            """)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {prefix}registers (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    snapshot_id BIGINT NOT NULL,
                    name VARCHAR(64) NOT NULL,
                    value DOUBLE NOT NULL,
                    unit VARCHAR(16),
                    ts DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                    KEY idx_ts_name (ts, name),
                    KEY idx_snapshot (snapshot_id),
                    CONSTRAINT fk_{prefix}snapshot
                        FOREIGN KEY (snapshot_id) REFERENCES {prefix}snapshots(id)
                        ON DELETE CASCADE
                ) ENGINE=InnoDB
            """)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {prefix}settings (
                    name VARCHAR(64) PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB
            """)

    def _write_snapshot(self, writer, decoded: dict) -> None:
        """Write a decoded register snapshot to the configured store."""
        if writer is None:
            return
        if self.cfg.storage_type == "influxdb":
            self._write_influxdb(writer, decoded)
        elif self.cfg.storage_type == "mariadb":
            self._write_mariadb(writer, decoded)

    def _write_influxdb(self, write_api, decoded: dict) -> None:
        """Write a decoded register snapshot to InfluxDB."""
        from influxdb_client import Point

        points = []

        point = Point("inverter")
        for key, val_info in decoded.items():
            if isinstance(val_info, dict) and "value" in val_info:
                try:
                    point = point.field(key, float(val_info["value"]))
                except (TypeError, ValueError):
                    pass
        points.append(point)

        for key, val_info in decoded.items():
            if isinstance(val_info, dict) and "value" in val_info:
                p = Point("register").tag("name", key).tag("unit", val_info.get("unit", ""))
                p = p.field("value", float(val_info["value"]))
                points.append(p)

        write_api.write(bucket=self.cfg.influx_bucket, record=points)
        logger.info("Wrote %d points to InfluxDB (%d fields)", len(points), len(decoded))

    def _write_mariadb(self, conn, decoded: dict) -> None:
        """Write a decoded register snapshot to MariaDB."""
        import pymysql

        prefix = self.cfg.mariadb_table_prefix
        raw_json = json.dumps(self._latest_input_raw)

        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {prefix}snapshots (ts, raw_registers) VALUES (NOW(3), %s)",
                (raw_json,),
            )
            snapshot_id = cur.lastrowid
            rows = [
                (snapshot_id, key, float(info["value"]), info.get("unit", ""))
                for key, info in decoded.items()
                if isinstance(info, dict) and "value" in info
            ]
            cur.executemany(
                f"INSERT INTO {prefix}registers (snapshot_id, name, value, unit) VALUES (%s, %s, %s, %s)",
                rows,
            )
        logger.info("Wrote MariaDB snapshot %d with %d registers", snapshot_id, len(rows))

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
    overrides: Optional[dict] = None,
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

    # Legacy LUX_POLL_MODE=true maps to tcp_active for backwards compatibility
    poll_mode = os.environ.get("LUX_POLL_MODE", "").lower() in ("1", "true", "yes")
    if poll_mode and cfg.transport == "tcp_passive":
        cfg.transport = "tcp_active"
        logger.warning(
            "LUX_POLL_MODE=true is deprecated; set LUX_TRANSPORT=tcp_active instead"
        )

    collector = PassiveCollector(cfg)
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
