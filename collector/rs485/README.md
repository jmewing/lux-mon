# lux-mon RS-485 / serial device support

This package adds pluggable RS-485 and TTL-UART drivers to lux-mon so the
same collector can ingest data from external devices (battery monitors,
meters, weather stations, etc.) and write it to the same MariaDB, InfluxDB,
and MQTT backends.

It is designed to be **additive**: the existing TCP collector is untouched,
and RS-485 collection runs as a separate daemon.

## Available drivers

| Driver         | Device / protocol                                   |
|----------------|------------------------------------------------------|
| `eg4_a5_bms`   | EG4 battery BMS via proprietary A5/5A serial protocol |
| `eg4_bms`      | EG4 LL LiFePO4 battery BMS via Modbus RTU           |
| `jk_bms`       | JiKong (JK) BMS over UART/RS-485                    |
| `modbus_rtu`   | Generic Modbus RTU master                           |
| `raw`          | Listen-only hex dump for discovery / unknown devices |

New drivers can be added by creating a class that inherits from
`Rs485Device` and registering it in `registry.py`.

## Quick start (native install)

1. Install lux-mon. The installer now adds `pyserial`/`pymodbus` and puts
   `jmewing` in the `dialout` group.

2. Discover what is connected to your RS-485 adapter:

   ```bash
   lux-mon-discover-rs485 /dev/ttyUSB0
   ```

3. Enable the RS-485 collector in `/srv/lux-mon/.env`:

   ```env
   LUX_RS485_ENABLED=true
   LUX_RS485_PORT=/dev/ttyUSB0
   LUX_RS485_BAUD=115200
   LUX_RS485_DEVICE_TYPE=jk_bms
   LUX_RS485_POLL_INTERVAL=2.0
   LUX_RS485_WRITE_INTERVAL=30.0
   LUX_RS485_PREFIX=rs485
   ```

4. Start the collector:

   ```bash
   sudo systemctl enable --now lux-mon-rs485
   ```

   Or run manually for testing:

   ```bash
   cd /srv/lux-mon
   source .env
   venv/bin/lux-mon-rs485
   ```

## Data shape

Every driver returns a dictionary of:

```python
{
    "field_name": {"value": float, "unit": str},
    ...
}
```

Before writing, the collector prefixes field names with `LUX_RS485_PREFIX`
(default `rs485_`) so they do not collide with inverter registers. For
example a JK BMS `total_voltage` becomes `rs485_total_voltage` in MariaDB
and `luxmon_register{name="rs485_total_voltage"}` in InfluxDB.

## Adding a new driver

1. Create `collector/rs485/my_device.py` with a class:

   ```python
   from . import Rs485Device, Rs485DeviceConfig

   class MyDevice(Rs485Device):
       name = "my_device"
       label = "My custom device"

       def read(self):
           return {"temperature": {"value": 25.0, "unit": "°C"}}
   ```

2. Register it in `collector/rs485/registry.py`:

   ```python
   ("my_device", "collector.rs485.my_device", "MyDevice"),
   ```

3. Optional: add a scale map or protocol-specific options to
   `Rs485DeviceConfig.options`.

## Notes

- `modbus_rtu` is a generic master; supply a `scale_map` in code to translate
  registers into named sensors.
- `raw` never transmits, it only listens. Use it to confirm bytes are arriving
  before choosing a driver.
- Missing dependencies (e.g. `pymodbus`) are logged and skipped instead of
  crashing the registry.

## EG4 A5/5A BMS field map

The `eg4_a5_bms` driver decodes broadcast frames from EG4 rack/wall-mount
batteries on their "PC ready" / display port.

Known fields from the 0x82 0x10 status frame (35-byte payload). The
offsets below were cross-checked against 17 consecutive status frames and
inverter-reported values:

| Bytes | Name | Scale | Notes |
|-------|------|-------|-------|
| 1-2   | `voltage` | 0.01 V | Pack voltage |
| 3-4   | `current` | 0.01 A, signed | Negative = discharge |
| 5-6   | `temperature_pcb` | 1 °C | Likely MOSFET/PCB temperature |
| 17-18 | `avg_cell_voltage` | 1 mV | Average of the 16 cells |
| 21-22 | `status_word` | - | BMS status bits (tentative) |
| 23-24 | `protection_word` | - | Protection bits (tentative) |
| 27-28 | `error_word` | - | Error/fault bits (tentative) |

The field at bytes 25-26 (`field_25_26`) is a **candidate for `cycle_count`** —
in one captured frame it equaled the inverter-reported cycle count (53), but
in a 17-frame capture it varied between 51 and 53, so it is left as a raw
integer until a stable correlation is confirmed.

The remaining fields are exported as `field_X_Y` integers so they can be
correlated against inverter-reported values once the battery is active. In
particular, **SOC has not yet been located** because the observed battery is
at 0% SOC, so the candidate byte(s) cannot be distinguished from other zero
fields. A future update will map SOC as soon as the battery charges or
discharges.

The 0x82 0x11 frame carries the 16 individual cell voltages in mV.
