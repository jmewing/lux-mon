# LuxPower Wire Protocol — TCP/8000

*Reverse-engineered from [lxp-bridge](https://github.com/celsworth/lxp-bridge) (Rust) and **verified against live wire captures** from an EG4 18kPV on 2026-03-24.*

---

## Overview

The EG4 18kPV WiFi dongle exposes **TCP port 8000** on the local network. This is NOT standard Modbus TCP. It's a **LuxPower proprietary framing protocol** that wraps Modbus register data inside a TCP transport layer.

SolarAssistant, lxp-bridge, and the EG4 cloud all use this same protocol.

---

## TCP Frame Structure

Every packet follows this structure:

```
Offset  Size    Field               Description
──────  ─────   ──────────────────  ─────────────────────────────────
0-1     2       Prefix              Always [0xA1, 0x1A] (161, 26)
2-3     2       Protocol            LE u16. Usually 5 for our firmware. 1 or 2 in older versions.
4-5     2       Frame Length        LE u16. Length of everything AFTER these 6 bytes
6       1       Unknown             Always 1
7       1       TCP Function        See TCP Function table below
8-17    10      Datalog Serial      10-byte ASCII serial of the WiFi dongle
18+     var     Payload             Function-specific data
```

**Total frame size** = 6 + Frame Length

### TCP Functions

| Value | Hex  | Name           | Description                          |
|-------|------|----------------|--------------------------------------|
| 193   | 0xC1 | Heartbeat      | Keep-alive from dongle               |
| 194   | 0xC2 | TranslatedData | Modbus read/write operations         |
| 195   | 0xC3 | ReadParam      | Parameter reads (less common)        |
| 196   | 0xC4 | WriteParam     | Parameter writes (less common)       |

**TranslatedData (0xC2)** is the primary function for all Modbus register operations.

---

## TranslatedData Payload

### Wire Layout (verified from live capture)

The data frame lives at `input[20..len-2]`, with CRC at `input[len-2..len]`.

```
Offset  Size    Field               Description
──────  ─────   ──────────────────  ─────────────────────────────────
18-19   2       Data Length Prefix  LE u16 (part of frame, before data)
20      1       Address             0=client→inverter, 1=inverter→client
21      1       Device Function     Modbus function code (see table)
22-31   10      Inverter Serial     10-byte ASCII serial of the inverter
32-33   2       Register            LE u16. Starting register number
34      1*      Value Length Byte   Byte count of following values (*conditional)
35+     var     Values              Register data (LE u16 per register)
last 2  2       Checksum            CRC-16/MODBUS of data frame bytes
```

**CRC scope:** `CRC16_MODBUS(input[20..len-2])` — the data frame excluding the checksum itself.

### Device Functions

| Value | Name        | Use                                  |
|-------|-------------|--------------------------------------|
| 3     | ReadHold    | Read holding registers (config, R/W) |
| 4     | ReadInput   | Read input registers (live data, RO) |
| 6     | WriteSingle | Write one holding register           |
| 16    | WriteMulti  | Write multiple holding registers     |

Error responses: function code + 128 (131, 132, 134, 144).

---

## Value Length Byte

Whether the value length byte is present depends on protocol version, source, and function:

| Protocol | DeviceFunction      | Source   | Has VLB? |
|----------|---------------------|----------|----------|
| 1        | Any                 | Any      | **No**   |
| ≠1       | ReadHold / ReadInput| Inverter | **Yes**  |
| ≠1       | WriteSingle         | Any      | **No**   |
| ≠1       | WriteMulti          | Client   | **Yes**  |

**Our dongle uses protocol=5**, so all ReadHold/ReadInput responses from the inverter **have the VLB**.

---

## Live Capture Results (2026-03-24)

### Connection Details
- **Dongle IP:** 192.168.1.100:8000
- **Datalog Serial:** AAAAAAAAAA
- **Inverter Serial:** 1234567890
- **Protocol Version:** 5
- **Frame Length:** 111 bytes (117 total per packet)

### What the Dongle Sends Unsolicited

Without sending any requests, the dongle pushes **TranslatedData packets every ~2 seconds** in a repeating cycle:

| Packet | Function    | Registers  | Description              |
|--------|-------------|------------|--------------------------|
| 1      | ReadHold(3) | 80-119     | Holding registers batch 3 |
| 2      | ReadHold(3) | 40-79      | Holding registers batch 2 |
| 3      | ReadInput(4)| 40-79      | Input registers batch 2   |
| 4      | ReadInput(4)| 0-39       | Input registers batch 1   |
| 5      | ReadInput(4)| 80-119     | Input registers batch 3   |
| 6      | ReadHold(3) | 0-39       | Holding registers batch 1 |

Each batch = 40 registers × 2 bytes = 80 bytes of data + overhead.

**This is key:** SolarAssistant is likely just listening to these unsolicited broadcasts. We can do the same for reads without creating any bus contention.

### Decoded Live Data

```
🔋 BATTERY
  SOC: 91%  |  SOH: 100%  |  Voltage: 53.3V
  Cell V: 3.332-3.336V (ΔV = 0.004V, excellent balance)
  Cell T: 21.0-23.0°C
  Cycle Count: 400

☀️ SOLAR (nighttime capture)
  PV1/PV2/PV3: 0W (expected)
  Daily: PV1=4.1 PV2=1.3 PV3=1.4 = 6.8 kWh total

🔌 GRID
  AC: 240.3V @ 60.01Hz, PF=1.000
  To User: 1,979W  |  To Grid: 0W

⚡ DAILY ENERGY
  Charge: 22.1 kWh  |  Discharge: 21.3 kWh
  To Grid: 0.1 kWh  |  To User: 66.2 kWh

📊 ALL-TIME (613 days runtime)
  PV Total: 9,673 kWh  |  To User: 45,855 kWh
  Charged: 6,285 kWh  |  Discharged: 5,211 kWh
```

---

## Building Request Packets

### ReadHold / ReadInput Request

```
TCP Frame:
  [0xA1, 0x1A]                    prefix
  protocol.to_le_bytes()          protocol (use 1 for requests)
  frame_length.to_le_bytes()      = 18 + data_length
  [0x01]                          unknown (always 1)
  [0xC2]                          TranslatedData
  datalog_serial[10]              "AAAAAAAAAA"

Data Frame (at offset 18):
  data_length.to_le_bytes()       = 18 (for read requests)
  [0x00]                          address (client)
  [device_function]               3=ReadHold, 4=ReadInput
  inverter_serial[10]             "1234567890"
  register.to_le_bytes()          starting register
  count.to_le_bytes()             number of registers
  crc16_modbus[2]                 CRC of data frame
```

### WriteSingle Request

Same structure but:
- Device function = 6
- Instead of register count, include the new value (u16 LE)
- Protocol = 1 (no VLB)

### WriteMulti Request

- Device function = 16
- Protocol = 2
- After register: register_count (u16 LE) + value_length_byte + values
- CRC covers the data frame

---

## Safety-Critical Registers

**These registers control charging/discharging behavior. Writes here affect physical hardware.**

| Register | Name | Safe Range | Notes |
|----------|------|------------|-------|
| 41 | BatteryChargeRate | 0-1000 (‰) | Per-mille of rated |
| 42 | ChgCutOffV | 540-580 | /10 = 54.0-58.0V |
| 43 | DischgCutOffV | 440-480 | /10 = 44.0-48.0V |
| 44 | ChgCutOffSOC | 0-100 | % |
| 45 | DischgCutOffSOC | 0-100 | % |
| 60 | ACChgSOCLimit | 0-100 | % |
| 61 | ACDischgSOCLimit | 0-100 | % |
| 96 | WorkMode | 0-2 | 0=SelfUse, 1=FeedIn, 2=Backup |

**NEVER write registers outside the known safe range without explicit user confirmation.**

---

## Coexistence with SolarAssistant

Since the dongle pushes data unsolicited, our strategy is:

1. **Reads:** Just listen to the unsolicited stream. Zero bus contention.
2. **Writes:** Send WriteSingle/WriteMulti requests interleaved between the dongle's broadcast cycle. The inverter will respond, and the next broadcast will reflect the new values.
3. **No polling needed:** We don't need to send ReadHold/ReadInput requests at all — the dongle does it for us.

This means **we can coexist with SolarAssistant perfectly** — both just listen to the same broadcast stream, and only our writes add any traffic.
