# lux-mon captures

Reference data captured from live EG4 200Ah battery BMS on `/dev/ttyUSB0` using the A5/5A protocol.

| File | Description |
|------|-------------|
| `bms-capture-20260805-0801.json` | 30 parsed frames (60 s), Aug 5 07:58 CDT. Full decoded record from `collector/rs485/eg4_a5_bms.py`. |
| `bms-capture-20260805-0801.ndjson` | Same data, newline-delimited JSON for streaming analysis. |
| `a5-status-20260805-020156.csv` | Raw/status frame hex dump from earlier overnight capture. |
| `all-frames-20260805-021312.csv` | All raw A5/5A frames from earlier capture. |
| `all-frames-now.csv` | Snapshot of all frames at a single point. |
| `api-snapshots-*.csv` | Inverter API snapshots taken alongside the BMS captures for cross-reference. |

Notes from 2026-08-05 capture:
- Pack voltage: ~53.45 V
- Current: ~-0.4 A (small discharge)
- PCB temp: 29 °C
- Cell voltages: 3.337–3.343 V, delta ~5 mV
- `field_25_26` varied 51–53 across 30 frames — not yet confirmed as cycle count.
- SOC is still not decoded from the A5/5A status frame.
