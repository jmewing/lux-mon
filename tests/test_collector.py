"""Test script for the live collector — no InfluxDB required.

Prints a snapshot every 10 seconds directly from the collector's decoder.
Useful for verifying live dongle connectivity and register decoding.
"""

import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.collector import PassiveCollector, CollectorConfig
from collector.outputs import OutputConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def snapshot_printer(decoded: dict) -> None:
    """Print a concise summary each time a snapshot is decoded."""
    print("\n" + "─" * 60)
    print("SNAPSHOT")
    print("─" * 60)

    def show(*keys):
        for key in keys:
            if key in decoded:
                d = decoded[key]
                print(f"  {key:25s}: {d['value']:10.2f} {d['unit']}")

    show("soc", "soh", "battery_voltage", "battery_current",
         "charge_power", "discharge_power")
    show("pv1_voltage", "pv2_voltage", "pv1_power", "pv2_power",
         "pv1_energy_today")
    show("grid_voltage_r", "grid_frequency", "grid_import_power",
         "grid_export_power")
    show("eps_voltage_r", "eps_power", "eps_energy_today")
    show("temp_inverter", "temp_radiator_1", "temp_radiator_2",
         "temp_battery")


if __name__ == "__main__":
    cfg = CollectorConfig(
        dongle_host="192.168.1.100",
        dongle_port=8000,
        write_interval=10,  # short interval for testing
        outputs=OutputConfig(
            mariadb_enabled=False,
            influx_enabled=False,
            mqtt_enabled=False,
        ),
    )

    collector = PassiveCollector(cfg, on_snapshot=snapshot_printer)
    collector.start()

    try:
        while True:
            time.sleep(5)
            stats = collector.stats
            logging.info("Stats: %s", stats)
    except KeyboardInterrupt:
        print("\nStopping...")
        collector.stop()
        collector.wait()
        print("Stopped.")
