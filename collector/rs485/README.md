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
