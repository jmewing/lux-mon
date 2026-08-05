# Reference Documents

This directory contains protocol documentation, manuals, and notes for the
hardware and protocols used by lux-mon.

| File | Description |
|------|-------------|
| `EG4_LifePower4_Communication_Protocol.pdf` | EG4 LifePower4 battery 0x7E serial protocol (BMS module addressing, voltage/current/temp/SOH registers). |
| `EG4_LifePower4_Communication_Protocol.txt` | OCR/text extraction of the above PDF for easier searching. |
| `EG4-LL-48V-100AH-Manual.pdf` | EG4-LL 48 V 100 Ah rack battery user manual. |
| `EG4-LL-48V-24V-Manual.pdf` | EG4-LL 48 V / 24 V rack battery user manual. |
| `JK_BMS_RS485_esphome.md` | ESPHome JK-PBx BMS RS-485 wiring/addressing guide from txubelaxu/esphome-jk-bms. |
| `EG4_A5_5A_BMS_notes.md` | Empirical notes on the EG4 A5/5A proprietary serial protocol used by the 200 Ah battery BMS on `/dev/ttyUSB0`.

See `collector/rs485/README.md` for the driver implementations.
