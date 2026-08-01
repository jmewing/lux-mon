# lux — EG4 Inverter CLI

Go CLI for communicating with EG4 inverters via the LuxPower TCP protocol.

## Table of Contents

- [Quick Start](#quick-start)
- [MQTT Publishing](#mqtt-publishing)
- [Environment Variables](#environment-variables)
- [Protocol Overview](#protocol-overview)
- [Understanding Registers](#understanding-registers)
- [Input Registers Reference](#input-registers-reference)
- [Holding Registers Reference](#holding-registers-reference)
- [Register 21 — Master Function Flags](#register-21--master-function-flags)
- [Changing Inverter Settings](#changing-inverter-settings)
- [Safety Considerations](#safety-considerations)
- [Coexistence with SolarAssistant](#coexistence-with-solarassistant)

---

## Quick Start

```bash
# Build
go build -o bin/lux ./cmd/lux

# Monitor live data (shows decoded register values)
./bin/lux monitor --duration 30

# Dump all registers as human-readable table
./bin/lux dump --format=table

# Dump as JSON (includes anomaly detection)
./bin/lux dump --format=json

# Show all registers including unlabeled ones
./bin/lux dump --format=table --all
```

### Command Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | (required) | WiFi dongle IP address |
| `--port` | 8000 | WiFi dongle TCP port |
| `--format` | json | Output format: `json` or `table` |
| `--wait` | 15 | Seconds to collect data before output |
| `--all` | false | Show unlabeled registers too |

---

## MQTT Publishing

The `publish` command streams inverter data to an MQTT broker using **Solar Assistant-compatible topic names**. This enables Home Assistant integration and remote monitoring.

```bash
# Basic usage (requires host and broker)
lux publish --host 192.168.1.100 --broker tcp://192.168.1.50:1883

# Custom broker and topic prefix
lux publish --broker tcp://192.168.1.100:1883 --prefix solar_assistant

# With authentication
lux publish --username myuser --password mypass

# Quiet mode (suppress per-packet logging)
lux publish --quiet

# JSON log output (for systemd/docker log aggregation)
lux publish --log-json
```

### Command Flags

| Flag | Default | Env Var | Description |
|------|---------|---------|-------------|
| `--broker` | (required) | `LUX_BROKER` | MQTT broker URL |
| `--prefix` | solar_assistant | `LUX_PREFIX` | Topic prefix |
| `--username` | | `LUX_MQTT_USER` | MQTT username |
| `--password` | | `LUX_MQTT_PASS` | MQTT password |
| `--retain` | true | `LUX_RETAIN` | Use MQTT retain flag |
| `--quiet` | false | `LUX_QUIET` | Suppress per-packet logging |
| `--log-json` | false | `LUX_LOG_JSON` | Output logs as JSON lines |

### Topic Structure

Topics are published under `{prefix}/inverter_1/{metric}/state`:

```
solar_assistant/inverter_1/pv_voltage_1/state     → "350.0"
solar_assistant/inverter_1/battery_voltage/state  → "53.3"
solar_assistant/inverter_1/grid_power/state       → "1500"
solar_assistant/inverter_1/pv_power/state         → "3200"
solar_assistant/total/battery_state_of_charge/state → "85"
```

### Health Check Topics

Two topics are maintained for health monitoring:

| Topic | Description |
|-------|-------------|
| `{prefix}/inverter_1/online/state` | `"true"` when connected, `"false"` on disconnect (MQTT LWT) |
| `{prefix}/inverter_1/last_seen/state` | RFC3339 timestamp of last successful publish |

### Automatic Reconnection

The publish command automatically reconnects to the inverter with exponential backoff:

- Initial retry: 1 second
- Backoff doubles each failure: 1s → 2s → 4s → 8s → ...
- Maximum backoff: 60 seconds
- MQTT connection stays alive across inverter reconnects
- `online/state` topic is set to `"false"` during reconnect attempts

### Running as a Service

**systemd** (`/etc/systemd/system/lux-publish.service`):

```ini
[Unit]
Description=Lux MQTT Publisher
After=network.target

[Service]
Type=simple
Environment="LUX_HOST=192.168.1.100"
Environment="LUX_BROKER=tcp://localhost:1883"
ExecStart=/usr/local/bin/lux publish --quiet
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Docker**:

```dockerfile
FROM golang:1.24-alpine AS builder
WORKDIR /app
COPY . .
RUN go build -o lux .

FROM alpine:latest
COPY --from=builder /app/lux /usr/local/bin/
ENV LUX_HOST=192.168.1.100
ENV LUX_BROKER=tcp://mqtt:1883
CMD ["lux", "publish", "--quiet"]

HEALTHCHECK --interval=30s --timeout=5s \
  CMD mosquitto_sub -h mqtt -t "solar_assistant/inverter_1/online/state" -C 1 | grep -q true
```

---

## Environment Variables

All flags can be set via environment variables with the `LUX_` prefix. Environment variables are useful for containerized deployments and systemd services.

| Env Var | Flag | Default | Description |
|---------|------|---------|-------------|
| `LUX_HOST` | `--host` | (required) | Inverter/dongle IP address |
| `LUX_PORT` | `--port` | 8000 | Dongle TCP port |
| `LUX_BROKER` | `--broker` | (required) | MQTT broker URL |
| `LUX_PREFIX` | `--prefix` | solar_assistant | MQTT topic prefix |
| `LUX_MQTT_USER` | `--username` | | MQTT username |
| `LUX_MQTT_PASS` | `--password` | | MQTT password |
| `LUX_RETAIN` | `--retain` | true | Use MQTT retain flag |
| `LUX_QUIET` | `--quiet` | false | Suppress per-packet logging |
| `LUX_LOG_JSON` | `--log-json` | false | Output logs as JSON lines |

**Example:**

```bash
export LUX_HOST=192.168.1.50
export LUX_BROKER=tcp://192.168.1.100:1883
export LUX_QUIET=true
lux publish
```

---

## Protocol Overview

The EG4 18kPV WiFi dongle exposes **TCP port 8000** on the local network. This is NOT standard Modbus TCP — it's a **LuxPower proprietary framing protocol** that wraps Modbus register data.

### Key Points

1. **Unsolicited Broadcasts**: The dongle pushes register data every ~2 seconds without any requests
2. **Two Register Types**: Holding (config, R/W) and Input (live data, R/O)
3. **Protocol Version**: Current firmware uses protocol v5
4. **CRC Validation**: All packets include CRC-16/Modbus checksum

### Frame Structure

```
Offset  Field               Description
──────  ──────────────────  ─────────────────────────────────
0-1     Prefix              Always [0xA1, 0x1A]
2-3     Protocol            LE u16, typically 5
4-5     Frame Length        LE u16, bytes after this field
6       Unknown             Always 0x01
7       TCP Function        0xC1=Heartbeat, 0xC2=TranslatedData
8-17    Datalog Serial      10-byte ASCII (WiFi dongle serial)
18+     Payload             Function-specific data
```

### Broadcast Cycle

The dongle broadcasts these packets in rotation (~2s intervals):

| Function | Registers | Content |
|----------|-----------|---------|
| ReadHold(3) | 0-39 | Holding batch 1 |
| ReadHold(3) | 40-79 | Holding batch 2 |
| ReadHold(3) | 80-119 | Holding batch 3 |
| ReadInput(4) | 0-39 | Input batch 1 (PV, battery, grid) |
| ReadInput(4) | 40-79 | Input batch 2 (energy totals) |
| ReadInput(4) | 80-119 | Input batch 3 (BMS data) |

**This means you can read all data just by listening — no polling required.**

---

## Understanding Registers

### Holding Registers vs Input Registers

| Aspect | Holding Registers | Input Registers |
|--------|-------------------|-----------------|
| **Modbus Function** | 03 (read), 06/16 (write) | 04 (read only) |
| **Purpose** | Configuration & settings | Live measurements |
| **Persistence** | Saved to EEPROM | Transient (real-time) |
| **Writeable** | Yes | No |
| **Examples** | Schedules, SOC limits, mode flags | Voltage, power, temperature |

**Analogy:**
- **Holding** = Thermostat settings (you set the target temperature)
- **Input** = Thermometer readings (you read the current temperature)

### Value Encoding

| Type | Encoding | Example |
|------|----------|---------|
| Voltage | Raw ÷ 10 | 533 → 53.3V |
| Frequency | Raw ÷ 100 | 6001 → 60.01Hz |
| Power Factor | Raw ÷ 1000 | 1000 → 1.000 |
| Percentage | Raw as-is | 85 → 85% |
| Power | Raw as-is | 3500 → 3500W |
| Time | High byte:Low byte | 0x0E1E → 14:30 |
| SOC/SOH | Low byte=SOC, High byte=SOH | 0x6455 → SOC=85%, SOH=100% |
| 32-bit values | Two registers (lo, hi) | Combine: `(hi << 16) | lo` then ÷10 for kWh |

### Bitmask Registers

Some registers pack multiple flags into bits. The most important is **Register 21** (Master Function Flags).

---

## Input Registers Reference

Input registers are **read-only** and contain live measurements.

### Status & Solar (0-9)

| Reg | Name | Scale | Unit | Description | Expected Range |
|-----|------|-------|------|-------------|----------------|
| 0 | Status | - | - | Operating mode (see status codes) | - |
| 1 | PV1 Voltage | ÷10 | V | Solar string 1 voltage | 0-550V |
| 2 | PV2 Voltage | ÷10 | V | Solar string 2 voltage | 0-550V |
| 3 | PV3 Voltage | ÷10 | V | Solar string 3 voltage | 0-550V |
| 4 | Battery Voltage | ÷10 | V | Battery bank voltage | 40-60V |
| 5 | SOC/SOH | special | % | State of charge (lo) / health (hi) | 0-100% |
| 6 | Internal Fault | bitmask | - | Fault code flags | 0 = no fault |
| 7 | PV1 Power | - | W | Solar string 1 power | 0-8000W |
| 8 | PV2 Power | - | W | Solar string 2 power | 0-8000W |
| 9 | PV3 Power | - | W | Solar string 3 power | 0-8000W |

### Battery & Grid (10-27)

| Reg | Name | Scale | Unit | Description | Expected Range |
|-----|------|-------|------|-------------|----------------|
| 10 | Battery Charge Power | - | W | Power into battery | 0-15000W |
| 11 | Battery Discharge Power | - | W | Power from battery | 0-15000W |
| 12 | Grid Voltage | ÷10 | V | AC grid voltage | 108-132V |
| 15 | Grid Frequency | ÷100 | Hz | AC grid frequency | 59.3-60.5Hz |
| 16 | Inverter Power | - | W | Inverter output | 0-20000W |
| 17 | Rectifier Power | - | W | AC→DC conversion | 0-20000W |
| 19 | Power Factor | ÷1000 | - | Power factor | 0.8-1.0 |
| 20 | EPS Voltage | ÷10 | V | Backup output voltage | 0-140V |
| 23 | EPS Frequency | ÷100 | Hz | Backup output frequency | 59-61Hz |
| 26 | Power To Grid | - | W | Export to grid | 0-18000W |
| 27 | Power From Grid | - | W | Import from grid | 0-18000W |

### Daily Energy (28-37)

| Reg | Name | Scale | Unit | Description |
|-----|------|-------|------|-------------|
| 28 | PV1 Energy Today | ÷10 | kWh | String 1 generation |
| 29 | PV2 Energy Today | ÷10 | kWh | String 2 generation |
| 30 | PV3 Energy Today | ÷10 | kWh | String 3 generation |
| 33 | Battery Charge Today | ÷10 | kWh | Energy charged |
| 34 | Battery Discharge Today | ÷10 | kWh | Energy discharged |
| 36 | Grid Export Today | ÷10 | kWh | Exported to grid |
| 37 | Grid Import Today | ÷10 | kWh | Imported from grid |

### Lifetime Energy (40-59)

These are 32-bit values split across two registers. Combine `(hi << 16) | lo` then divide by 10 for kWh.

| Regs | Name | Description |
|------|------|-------------|
| 40-41 | PV1 Energy Total | Lifetime string 1 generation |
| 42-43 | PV2 Energy Total | Lifetime string 2 generation |
| 44-45 | PV3 Energy Total | Lifetime string 3 generation |
| 50-51 | Battery Charge Total | Lifetime energy charged |
| 52-53 | Battery Discharge Total | Lifetime energy discharged |
| 56-57 | Grid Export Total | Lifetime grid export |
| 58-59 | Grid Import Total | Lifetime grid import |

### Temperature (64-68)

| Reg | Name | Scale | Unit | Expected Range |
|-----|------|-------|------|----------------|
| 64 | Internal Temp | - | °C | -10 to 70°C |
| 65 | Radiator 1 Temp | - | °C | -10 to 85°C |
| 66 | Radiator 2 Temp | - | °C | -10 to 85°C |
| 68 | Battery Temp | ÷10 | °C | -10 to 50°C |

### BMS Data (89-114)

| Reg | Name | Scale | Unit | Description | Expected Range |
|-----|------|-------|------|-------------|----------------|
| 89 | Max Charge Current | ÷10 | A | BMS charge limit | 0-300A |
| 90 | Max Discharge Current | ÷10 | A | BMS discharge limit | 0-300A |
| 91 | Charge Voltage Ref | ÷10 | V | Target charge voltage | 50-60V |
| 92 | Discharge Cutoff V | ÷10 | V | Low voltage cutoff | 40-52V |
| 104 | Battery Count | - | - | Packs detected | 1-16 |
| 105 | Battery Capacity | - | Ah | Total capacity | 50-1000Ah |
| 106 | Battery Current | ÷100 | A | Current flow | -300 to 300A |
| 109 | Max Cell Voltage | ÷1000 | V | Highest cell | 2.5-3.65V |
| 110 | Min Cell Voltage | ÷1000 | V | Lowest cell | 2.5-3.65V |
| 111 | Max Cell Temp | ÷10 | °C | Hottest cell | -10 to 55°C |
| 112 | Min Cell Temp | ÷10 | °C | Coldest cell | -10 to 55°C |
| 114 | Cycle Count | - | cycles | Charge cycles | 0-10000 |

### Status Codes (Register 0)

| Value | Status |
|-------|--------|
| 0x00 | Standby |
| 0x04 | PV On-grid |
| 0x08 | PV Charge |
| 0x0C | PV Charge On-grid |
| 0x10 | Battery On-grid |
| 0x11 | Bypass |
| 0x14 | PV & Battery On-grid |
| 0x20 | AC Charge |
| 0x28 | PV & AC Charge |
| 0x40 | Battery Off-grid |
| 0x80 | PV Off-grid |
| 0xC0 | PV & Battery Off-grid |

---

## Holding Registers Reference

Holding registers are **read/write** and contain configuration settings.

### System Info (0-7) — Read Only

| Reg | Name | Description |
|-----|------|-------------|
| 0-1 | Model | Inverter model identifier |
| 2-6 | Serial | Inverter serial number (ASCII packed) |
| 7 | Firmware Code | Firmware version |

### Time & Config (12-24)

| Reg | Name | Description | R/W |
|-----|------|-------------|-----|
| 12 | Time Month/Year | Clock: month(hi)/year-2000(lo) | RW |
| 13 | Time Hour/Day | Clock: hour(hi)/day(lo) | RW |
| 14 | Time Sec/Min | Clock: sec(hi)/min(lo) | RW |
| 15 | Comm Address | Modbus address (1-247) | RW |
| 16 | Language | Display language (0=EN, 1=CN) | RW |
| 20 | PV Input Mode | 0=Independent, 1=Parallel | RW |
| 21 | **Master Flags** | **Primary mode control (see below)** | RW |
| 22 | PV Start Voltage | Min PV voltage to start (÷10 V) | RW |
| 23 | Grid Connect Time | Delay before grid connect (s) | RW |
| 24 | Grid Reconnect Time | Delay before reconnect (s) | RW |

### Grid Protection (25-53) — DO NOT MODIFY

These registers control grid voltage/frequency trip points per IEEE 1547/UL1741. **Modifying these can cause safety issues or grid code violations.**

| Regs | Description |
|------|-------------|
| 25-36 | Grid voltage high/low limits and trip times |
| 38-53 | Grid frequency high/low limits and trip times |

**The CLI blocks writes to registers 25-53.**

### Power Control (60-65)

| Reg | Name | Scale | Unit | Description | Range |
|-----|------|-------|------|-------------|-------|
| 60 | Active Power % | - | % | Max output limit | 0-110% |
| 62 | Power Factor Cmd | ÷1000 | - | Target PF | 0.8-1.0 |
| 64 | Charge Power % | - | % | Max charge rate | 0-100% |
| 65 | Discharge Power % | - | % | Max discharge rate | 0-100% |

### AC Charge Schedule (66-73)

| Reg | Name | Encoding | Description |
|-----|------|----------|-------------|
| 66 | AC Charge Power | % | Grid charge rate |
| 67 | AC Charge SOC Limit | % | Stop charging at this SOC |
| 68 | Period 1 Start | HH:MM | Window 1 start time |
| 69 | Period 1 End | HH:MM | Window 1 end time |
| 70-71 | Period 2 | HH:MM | Window 2 start/end |
| 72-73 | Period 3 | HH:MM | Window 3 start/end |

### Charge Priority Schedule (74-81)

Same structure as AC Charge. When enabled (reg 21 bit 11), solar charges battery before export.

| Reg | Name | Description |
|-----|------|-------------|
| 74 | Power | Charge rate % |
| 75 | SOC Limit | Target SOC % |
| 76-81 | Periods 1-3 | Start/end times |

### Forced Discharge Schedule (82-89)

Same structure. When enabled (reg 21 bit 10), forces battery discharge during windows.

| Reg | Name | Description |
|-----|------|-------------|
| 82 | Power | Discharge rate % |
| 83 | SOC Limit | Minimum SOC % |
| 84-89 | Periods 1-3 | Start/end times |

### Battery Limits

| Reg | Name | Scale | Unit | Description | Range |
|-----|------|-------|------|-------------|-------|
| 99 | Lead Acid Charge V | ÷10 | V | Charge voltage | 48-60V |
| 100 | Lead Acid Discharge V | ÷10 | V | Cutoff voltage | 40-52V |
| 103 | Grid Feed-in Limit | - | % | Max export | 0-100% |
| 105 | Discharge Cutoff SOC | - | % | On-grid min SOC | 0-100% |
| 125 | EPS Cutoff SOC | - | % | Off-grid min SOC | 0-100% |
| 144 | Floating Voltage | ÷10 | V | Float charge V | 48-58V |
| 147 | Battery Capacity | - | Ah | Configured capacity | 50-1000 |
| 148 | Nominal Voltage | ÷10 | V | Nominal V | 40-60V |
| 160 | AC Charge Start SOC | - | % | Begin AC charge below | 0-100% |
| 161 | AC Charge End SOC | - | % | Stop AC charge at | 0-100% |
| 168 | AC Charge Current | - | A | Max AC charge current | 0-200A |

---

## Register 21 — Master Function Flags

This is the most important register for controlling inverter behavior. It's a 16-bit bitmask.

### Bit Definitions

| Bit | Name | Description |
|-----|------|-------------|
| 0 | EPS | Emergency Power Supply enable |
| 1 | OverloadDerate | Overload derate enable |
| 2 | DRMS | Demand Response Management |
| 3 | LVRT | Low Voltage Ride-Through |
| 4 | AntiIsland | Anti-islanding protection |
| 5 | NeutralDetect | Neutral detection |
| 6 | GridOnPowerSS | Grid-on power soft start |
| **7** | **ACCharge** | **AC (grid) charging enable** |
| 8 | SeamlessEPS | Seamless EPS switching |
| 9 | Standby | Standby mode |
| **10** | **ForcedDischarge** | **Forced discharge enable** |
| **11** | **ChargePriority** | **Charge priority enable** |
| 12 | ISO | Isolation detection |
| 13 | GFCI | Ground fault circuit interrupter |
| 14 | DCI | DC injection detection |
| **15** | **FeedInGrid** | **Feed-in to grid enable** |

### Reading Flags

```go
flags := client.GetHolding(21)
if flags & (1 << 7) != 0 {
    fmt.Println("AC Charging is enabled")
}
if flags & (1 << 15) != 0 {
    fmt.Println("Grid feed-in is enabled")
}
```

### Common Flag Combinations

| Value | Active Flags | Description |
|-------|--------------|-------------|
| 0x8FC3 | EPS, DRMS, AntiIsland, NeutralDetect, ACCharge, ForcedDischarge, ChargePriority, FeedInGrid | Typical home setup |
| 0x0001 | EPS | EPS only |
| 0x8000 | FeedInGrid | Export only |

---

## Changing Inverter Settings

### Read-Modify-Write Pattern

For bitmask registers like reg 21, always read first, modify the bit, then write:

```go
// Enable AC charging (bit 7)
current := client.GetHolding(21)
newValue := current | (1 << 7)  // Set bit 7
client.WriteSingle(21, newValue)

// Disable AC charging
current := client.GetHolding(21)
newValue := current &^ (1 << 7)  // Clear bit 7
client.WriteSingle(21, newValue)
```

### Setting Time Schedules

Time values are packed as `(hour << 8) | minute`:

```go
// Set AC charge window to 02:00 - 06:00
startTime := (2 << 8) | 0   // 02:00 = 0x0200 = 512
endTime := (6 << 8) | 0     // 06:00 = 0x0600 = 1536
client.WriteSingle(68, startTime)  // Period 1 start
client.WriteSingle(69, endTime)    // Period 1 end
```

### Setting Percentages

Percentage registers take raw values 0-100:

```go
// Set discharge cutoff to 10%
client.WriteSingle(105, 10)

// Set charge rate to 50%
client.WriteSingle(64, 50)
```

### Example: Enable Off-Peak Charging

```go
// 1. Set AC charge window (02:00 - 06:00)
client.WriteSingle(68, 0x0200)  // Start 02:00
client.WriteSingle(69, 0x0600)  // End 06:00

// 2. Set charge rate and SOC limit
client.WriteSingle(66, 100)    // 100% charge rate
client.WriteSingle(67, 100)    // Charge to 100% SOC

// 3. Enable AC charging in master flags
current := client.GetHolding(21)
client.WriteSingle(21, current | (1 << 7))
```

---

## Safety Considerations

### Protected Registers (25-53)

Registers 25-53 contain **grid protection settings** required for electrical code compliance (IEEE 1547, UL1741). The CLI refuses to write to these registers.

**Never modify these values** — incorrect settings can:
- Cause the inverter to trip unexpectedly
- Create safety hazards
- Violate grid interconnection requirements
- Void your warranty

### Rate Limiting

The CLI enforces a rate limit of **10 writes per minute** to prevent:
- Accidental rapid-fire writes
- EEPROM wear (limited write cycles)
- Bus contention issues

### Write Logging

All write attempts are logged with timestamp, register, old value, new value, and success status for audit purposes.

### Recommended Practices

1. **Read before write** — Always verify current value before modifying
2. **Test in monitor mode** — Watch the inverter's response
3. **One change at a time** — Don't batch unrelated changes
4. **Know your limits** — Understand what each register controls
5. **Keep defaults documented** — Record factory settings before changing

---

## Coexistence with SolarAssistant

Since the dongle broadcasts data unsolicited:

1. **Reads**: Just listen to the broadcast stream — zero additional bus traffic
2. **Writes**: Send requests between broadcast packets
3. **No conflicts**: Both tools receive the same broadcast data

This means `lux` and SolarAssistant can run simultaneously without interference. Only write operations add any traffic, and those are rate-limited.

---

## Architecture

```
cmd/lux/          CLI entry point
pkg/lux/
  protocol.go     Packet encode/decode, CRC, frame parsing
  registers.go    Register definitions with names, units, ranges
  client.go       TCP client with automatic register tracking
  safety.go       Write guards and rate limiting
```

---

## Hardware Reference

- **Inverter**: EG4 18kPV (18kW hybrid inverter)
- **WiFi Dongle**: LuxPower protocol, TCP port 8000
- **Battery**: 48V nominal LiFePO4 system
- **Grid**: US split-phase 120/240V, 60Hz

---

## References

- [lxp-bridge](https://github.com/celsworth/lxp-bridge) — Rust implementation (primary reference)
- [EG4 Electronics](https://eg4electronics.com/) — Manufacturer
- IEEE 1547 — Grid interconnection standard
- Modbus Application Protocol Specification
