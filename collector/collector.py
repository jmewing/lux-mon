"""
Passive LuxPower TCP collector.

Connects to a LuxPower/EG4 WiFi dongle on port 8000 and passively listens to
the streaming broadcast data. No active polling is required — the dongle
continuously emits ReadHold and ReadInput frames containing inverter telemetry.

Parsed register values are written to the configured storage backend (MariaDB
or InfluxDB) on a configurable interval.
"""

import os
import socket
import time
import logging
import signal
import sys
import json
from dataclasses import dataclass
from typing import Optional, Callable
from threading import Thread, Event
from pathlib import Path

from .protocol import find_frames, LuxFrame, build_read_request, MODBUS_READ_INPUT
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

    # Active polling mode: send ReadInput requests instead of passive listen.
    # Required for dongles that don't auto-broadcast (some firmware versions).
    poll_mode: bool = False
    poll_interval: float = 2.0  # seconds between poll requests
    poll_register_start: int = 0  # first register to poll
    poll_register_count: int = 40  # registers per poll request

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


def config_from_env() -> CollectorConfig:
    """Build a CollectorConfig from environment variables."""
    return CollectorConfig(
        dongle_host=_env_or("LUX_DONGLE_HOST", "192.168.1.100"),
        dongle_port=_env_or("LUX_DONGLE_PORT", 8000, int),
        read_timeout=_env_or("LUX_READ_TIMEOUT", 60.0, float),
        reconnect_delay=_env_or("LUX_RECONNECT_DELAY", 5.0, float),
        write_interval=_env_or("LUX_WRITE_INTERVAL", 30, int),
        replay_file=_env_or("LUX_REPLAY_FILE"),
        poll_mode=_env_or("LUX_POLL_MODE", False, lambda v: v.lower() in ("1", "true", "yes")),
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
    )


class PassiveCollector:
    """Passive TCP collector for LuxPower/EG4 dongles."""

    def __init__(self, config: CollectorConfig,
                 on_snapshot: Optional[Callable[[dict], None]] = None):
        self.cfg = config
        self._stop = Event()
        self._sock: Optional[socket.socket] = None
        self._writer: Optional[Thread] = None
        self._reader: Optional[Thread] = None
        self._latest_input_raw: dict[int, int] = {}
        self._latest_hold_raw: dict[int, int] = {}
        self._latest_decoded: dict = {}
        self._last_write = 0.0
        self._on_snapshot = on_snapshot
        self._connect_time = 0.0
        self._frames_received = 0
        self._poll_requests = 0

        # Register handler for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        self.stop()

    def start(self) -> None:
        """Start the collector threads."""
        if self.cfg.replay_file:
            logger.info("Starting LuxPower replay collector from %s", self.cfg.replay_file)
            self._reader = Thread(target=self._replay_loop, name="lux-replay", daemon=True)
        else:
            logger.info("Starting LuxPower passive collector for %s:%d",
                        self.cfg.dongle_host, self.cfg.dongle_port)
            self._reader = Thread(target=self._reader_loop, name="lux-reader", daemon=True)
        self._writer = Thread(target=self._writer_loop, name="lux-writer", daemon=True)
        self._reader.start()
        self._writer.start()

    def stop(self) -> None:
        """Signal the collector to stop and close connections."""
        self._stop.set()
        self._close_socket()

    def wait(self) -> None:
        """Block until the collector stops."""
        if self._reader and self._reader.is_alive():
            self._reader.join()
        if self._writer and self._writer.is_alive():
            self._writer.join()

    def _connect(self) -> Optional[socket.socket]:
        """Open a TCP connection to the dongle."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.cfg.read_timeout)
            sock.connect((self.cfg.dongle_host, self.cfg.dongle_port))
            self._connect_time = time.time()
            logger.info("Connected to dongle %s:%d", self.cfg.dongle_host, self.cfg.dongle_port)
            return sock
        except OSError as exc:
            logger.error("Failed to connect to dongle: %s", exc)
            return None

    def _close_socket(self) -> None:
        """Close the active socket if any."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _reader_loop(self) -> None:
        """Main reader thread: connect, read bytes, parse frames."""
        if self.cfg.poll_mode:
            self._poll_loop()
        else:
            self._passive_loop()

    def _passive_loop(self) -> None:
        """Passive listen mode: dongle auto-broadcasts data."""
        buffer = b""

        while not self._stop.is_set():
            if self._sock is None:
                self._sock = self._connect()
                if self._sock is None:
                    self._stop.wait(self.cfg.reconnect_delay)
                    continue

            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    logger.warning(
                        "Dongle closed connection immediately (single-connection limit or inverter off)"
                    )
                    self._close_socket()
                    self._stop.wait(self.cfg.reconnect_delay)
                    continue

                buffer += chunk

                frames = find_frames(buffer)
                if frames:
                    self._frames_received += len(frames)
                    for frame in frames:
                        self._handle_frame(frame)

                    last_frame = frames[-1]
                    last_pos = buffer.find(last_frame.raw) + len(last_frame.raw)
                    buffer = buffer[last_pos:]

                if len(buffer) > 8192:
                    next_a1 = buffer.find(bytes([0xA1, 0x1A]), 1)
                    if next_a1 > 0:
                        buffer = buffer[next_a1:]
                    else:
                        buffer = b""

            except socket.timeout:
                logger.warning("Socket timeout, reconnecting...")
                self._close_socket()
            except OSError as exc:
                logger.error("Socket error: %s", exc)
                self._close_socket()
                self._stop.wait(self.cfg.reconnect_delay)

    def _poll_loop(self) -> None:
        """Active polling mode: send ReadInput requests and parse responses."""
        if not self.cfg.datalog_serial or not self.cfg.inverter_serial:
            logger.error(
                "Poll mode requires LUX_DATALOG_SERIAL and LUX_INVERTER_SERIAL. "
                "Falling back to passive mode."
            )
            self._passive_loop()
            return

        logger.info(
            "Active polling mode: reading %d registers starting at %d every %.1fs",
            self.cfg.poll_register_count,
            self.cfg.poll_register_start,
            self.cfg.poll_interval,
        )

        # Build the request packet once (it doesn't change)
        request = build_read_request(
            datalog_serial=self.cfg.datalog_serial,
            inverter_serial=self.cfg.inverter_serial,
            device_function=MODBUS_READ_INPUT,
            start_register=self.cfg.poll_register_start,
            count=self.cfg.poll_register_count,
        )

        while not self._stop.is_set():
            if self._sock is None:
                self._sock = self._connect()
                if self._sock is None:
                    self._stop.wait(self.cfg.reconnect_delay)
                    continue
                buffer = b""

            try:
                # Send the ReadInput request
                self._sock.sendall(request)
                self._poll_requests += 1

                # Read the response
                chunk = self._sock.recv(4096)
                if not chunk:
                    logger.warning("Dongle closed connection during poll")
                    self._close_socket()
                    self._stop.wait(self.cfg.reconnect_delay)
                    continue

                buffer += chunk

                # Parse frames from the response
                frames = find_frames(buffer)
                if frames:
                    self._frames_received += len(frames)
                    for frame in frames:
                        self._handle_frame(frame)

                    last_frame = frames[-1]
                    last_pos = buffer.find(last_frame.raw) + len(last_frame.raw)
                    buffer = buffer[last_pos:]

                # Wait before next poll
                self._stop.wait(self.cfg.poll_interval)

            except socket.timeout:
                logger.warning("Poll timeout, reconnecting...")
                self._close_socket()
            except OSError as exc:
                logger.error("Socket error during poll: %s", exc)
                self._close_socket()
                self._stop.wait(self.cfg.reconnect_delay)

    def _replay_loop(self) -> None:
        """Replay a captured binary file for offline testing/development."""
        path = Path(self.cfg.replay_file)
        if not path.exists():
            logger.error("Replay file not found: %s", path)
            return

        data = path.read_bytes()
        logger.info("Replaying %d bytes from %s", len(data), path)

        # Feed the whole capture once, simulating live arrival in chunks
        chunk_size = 512
        buffer = b""
        for offset in range(0, len(data), chunk_size):
            if self._stop.is_set():
                break
            buffer += data[offset:offset + chunk_size]

            frames = find_frames(buffer)
            if frames:
                self._frames_received += len(frames)
                for frame in frames:
                    self._handle_frame(frame)
                last_frame = frames[-1]
                last_pos = buffer.find(last_frame.raw) + len(last_frame.raw)
                buffer = buffer[last_pos:]

            time.sleep(0.5)

        logger.info("Replay finished (%d frames parsed)", self._frames_received)

        # Keep writer alive for a while so it can write decoded snapshots
        while not self._stop.is_set():
            time.sleep(1)

        logger.info("Replay loop exiting")

    def _handle_frame(self, frame: LuxFrame) -> None:
        """Process a parsed frame."""
        if not frame.is_translated_data:
            return

        if frame.is_read_input:
            # Merge input register values into the latest snapshot.
            for i, raw_val in enumerate(frame.values):
                reg_num = frame.register + i
                self._latest_input_raw[reg_num] = raw_val

            if frame.values:
                logger.debug("Input frame: %d regs starting @ %d",
                             len(frame.values), frame.register)

        elif frame.is_read_hold:
            # Holding registers are configuration/settings; keep them separate
            # for future use but do not decode them as live telemetry.
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

            # Require at least the core battery voltage register before writing
            if 4 not in self._latest_input_raw:
                logger.warning("Input register batch incomplete, skipping write")
                continue

            try:
                self._latest_decoded = decode_registers(self._latest_input_raw)
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

        # Main solar point with key metrics
        point = Point("inverter")
        for key, val_info in decoded.items():
            if isinstance(val_info, dict) and "value" in val_info:
                try:
                    point = point.field(key, float(val_info["value"]))
                except (TypeError, ValueError):
                    pass
        points.append(point)

        # Also write individual points with units for easier Grafana queries
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
        return {
            "connected": self._sock is not None,
            "frames_received": self._frames_received,
            "poll_requests": self._poll_requests,
            "input_registers_known": len(self._latest_input_raw),
            "hold_registers_known": len(self._latest_hold_raw),
            "last_write": self._last_write,
            "uptime": time.time() - self._connect_time if self._connect_time else 0,
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

    collector = PassiveCollector(cfg)
    collector.start()
    collector.wait()


def _load_config(cfg: CollectorConfig, path: Path) -> None:
    """Load collector config from a Python file."""
    if not path.exists():
        return

    # Simple exec-based config loader; production would use YAML/TOML
    ns = {"config": cfg}
    exec(compile(path.read_text(), str(path), "exec"), ns)


if __name__ == "__main__":
    run_collector(sys.argv[1] if len(sys.argv) > 1 else None)
