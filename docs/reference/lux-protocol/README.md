# EG4 Modbus — Direct Inverter Control for Conduit

A Go library and Conduit skill for direct Modbus communication with the EG4 18kPV inverter via the WiFi dongle's TCP port 8000.

## Goal

Read and write inverter registers — battery charge/discharge schedules, modes, power limits — without depending on SolarAssistant, lxp-bridge, or the EG4 cloud.

## Architecture

```
CLI / Library
  └── eg4-modbus Go library
        └── TCP:8000 → WiFi Dongle → RS485/Modbus RTU → EG4 18kPV Inverter
```

The library handles the LuxPower wire protocol (TCP framing around Modbus RTU). The Conduit skill exposes high-level actions like `set_charge_schedule`, `get_battery_status`, `set_mode`.

## Status

**Phase: Research & Design** — protocol fully reverse-engineered from lxp-bridge source.

## Key Documents

- `PROTOCOL.md` — Full wire protocol specification
- `REGISTERS.md` — Register map with read/write capabilities
- `DESIGN.md` — Implementation plan and phasing
