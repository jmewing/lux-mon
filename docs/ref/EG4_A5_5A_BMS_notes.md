# EG4 A5/5A Battery BMS Serial Protocol Notes

These notes are empirical. The protocol was reverse-engineered from a live EG4
200 Ah battery BMS connected to the "PC ready" / display port on
`/dev/ttyUSB0` at 115200 8N1.

No public protocol specification has been found for the A5/5A framing. The
closest match is the LuxpowerTek / Voltronic family style: frames start with
`0xA5 0x5A`, followed by a length byte, command, sub-command, payload, and a
checksum byte at the end.

## Frame format

```text
0xA5 0x5A | length | command | sub-command | payload... | checksum
```

- `length` is the number of bytes *after* the length byte, i.e.
  `command + sub-command + payload + checksum`.
- Checksum algorithm: sum of all bytes from `length` through the end of the
  payload, low byte only (`sum & 0xFF`). Verified against captured frames.
- All 16-bit integers in the payload are big-endian.

## Observed command/sub-commands

| Command | Sub | Payload len | Description |
|---------|-----|-------------|-------------|
| 0x80    | 0x01| 0           | Heartbeat / keep-alive broadcast |
| 0x82    | 0x10| 35          | Status frame (pack voltage, current, temperatures, average cell voltage, flags) |
| 0x82    | 0x11| 57          | Cell-voltage frame (16 cells + trailing zeros) |
| 0x82    | 0x13| 19          | All zeros in captures (possibly unused or configuration frame) |
| 0x82    | 0x20| 3           | Small response; meaning unknown |
| 0x82    | 0x21| 3           | Small response; meaning unknown |

The BMS broadcasts continuously; the host does not need to send requests to
receive 0x80/0x01, 0x82/0x10, or 0x82/0x11 frames. Sending a poll request with
command 0x82 and the desired sub-command appears to trigger an immediate
response.

## Status frame (0x82 0x10) layout

Payload is 35 bytes. Verified by capturing 17 consecutive frames and
correlating against the EG4 inverter's live values.

| Offset | Bytes | Field | Scale | Notes |
|--------|-------|-------|-------|-------|
| 0      | 1     | —     | —     | Always 0x00 in captures; may be a status/version byte |
| 1–2    | 2     | voltage | 0.01 V | Pack voltage |
| 3–4    | 2     | current | 0.01 A, signed | Positive = charge, negative = discharge |
| 5–6    | 2     | temperature_pcb | 1 °C | Likely MOSFET/PCB temperature |
| 7–8    | 2     | field_7_8 | — | Unknown |
| 9–10   | 2     | field_9_10 | — | Unknown |
| 11–12  | 2     | field_11_12 | — | Possibly battery/sensor temperature candidate |
| 13–14  | 2     | field_13_14 | — | Possibly max cell temperature candidate |
| 15–16  | 2     | field_15_16 | — | Unknown; always 0 in captures |
| 17–18  | 2     | avg_cell_voltage | 1 mV | Matches average computed from 0x82 0x11 cell frame |
| 19–20  | 2     | field_19_20 | — | Unknown; always 0 in captures |
| 21–22  | 2     | status_word | — | Tentative; always 0x0001 in normal operation |
| 23–24  | 2     | protection_word | — | Tentative; always 0x0001 in normal operation |
| 25–26  | 2     | field_25_26 | — | Candidate for cycle_count: in one frame matched inverter cycle_count (53), but across 17 frames it varied 51–53. Left as raw field until stable correlation is confirmed. |
| 27–28  | 2     | error_word | — | Tentative; always 0x0001 in normal operation |
| 29–30  | 2     | field_29_30 | — | Unknown; always 0x0001 in captures |
| 31–32  | 2     | field_31_32 | — | Unknown; always 0x0001 in captures |
| 33–34  | 2     | field_33_34 | — | Unknown; always 0x0000 in captures |

### Open questions

- **SOC location is not yet known.** Because the observed battery stayed at
  0% SOC during decoding, every candidate SOC byte also reads 0. A capture
  while the battery charges is required to locate SOC.
- The meaning of `field_25_26` (candidate cycle_count) is uncertain because
  it varied between frames.
- Sub-commands 0x20 and 0x21 return 3-byte payloads that may contain
  capacity, SOH, or firmware information; not yet decoded.

## Cell-voltage frame (0x82 0x11) layout

Payload is 57 bytes.

| Offset | Bytes | Field | Scale |
|--------|-------|-------|-------|
| 0      | 1     | —     | —     |
| 1–2    | 2     | cell_1_voltage  | 1 mV |
| 3–4    | 2     | cell_2_voltage  | 1 mV |
| ...    | ...   | ...   | ... |
| 31–32  | 2     | cell_16_voltage | 1 mV |
| 33–56  | 24    | —     | —     | Trailing zeros in captures |

## Implementation

See `collector/rs485/eg4_a5_bms.py` for the lux-mon driver. Fields are written
with the configured `LUX_RS485_PREFIX` (e.g. `eg4_bms_`) to MariaDB,
InfluxDB, and MQTT so they do not collide with inverter registers.
