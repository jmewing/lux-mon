# EG4 Modbus Python Tools

Capture and decode tools for the LuxPower TCP protocol used by EG4 18kPV WiFi dongles.

## Scripts

### capture_raw.py
Raw TCP hex dump from the dongle. Useful for debugging protocol issues.

```bash
python3 capture_raw.py --host 192.168.1.100 --duration 30
```

### decode_capture.py
Full packet decoder. Connects to the dongle, parses LuxPower TCP v5 frames,
decodes Modbus register data, and displays human-readable output.

```bash
python3 decode_capture.py --host 192.168.1.100 --duration 30
```

## Protocol

See `../PROTOCOL.md` for the full wire protocol specification.

## Hardware

- **Inverter:** EG4 18kPV
- **WiFi Dongle:** LuxPower protocol on TCP port 8000
