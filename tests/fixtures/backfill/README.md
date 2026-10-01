# EG4 data-export fixtures (`collector.backfill`)

Rows from a real EG4 portal data export (`GET /WManage/web/analyze/data/export/{serial}/{start}?endDateText={end}`,
a legacy BIFF `.xls` workbook, one worksheet per day) used by `tests/test_backfill.py`.
No request in the test suite touches the network.

| File | Content | Source |
|------|---------|--------|
| `export_rows_12000xp.json` | 7 rows of an EG4 12000XP (off-grid, split-phase, 2 MPPT, 1 battery bank), 2026-09-24/25, exactly as `collector.backfill.parse_book` returns them (mixed float / string cells, all 114 columns in `headers`). **Modified:** `Serial number` replaced by the placeholder `TEST000001`. | The repository owner's own portal export |

Rows included (plant-local `Time`):

| Sheet | Time | Why |
|-------|------|-----|
| 2026-09-24 | 07:15:18 | `Standby` after the battery ran flat: SOC `0%`, backup output off, `warningCode` `0x4000000`, `pLoad` `0` |
| 2026-09-24 | 08:43:51 | `PV Charge` (PV charging, backup output still off) |
| 2026-09-25 | 00:03:14 | Night, battery only (`Battery Grid off`), `BatCurrent` `0` |
| 2026-09-25 | 00:07:16 | Night, battery discharging (`BatCurrent` `-0.5`) |
| 2026-09-25 | 11:59:32 | Midday PV: `vpv1` 262.2, SOC `30%`, `tradiator1` 49 °C |
| 2026-09-25 | 13:24:05 | Midday PV charging (`PV&Battery Grid off`, `BatCurrent` 22.6) |
| 2026-09-25 | 23:56:51 | End of day: daily and lifetime energy counters |

Every row has the 12000XP's `pf` `[0]`, hex status words (`0x00`, `0xC003`), zero BMS cell voltages / cycle
count (not reported) and the split-phase garbage in `vacs`/`vact`/`vepss`/`vepst`.

Do not add real account data, e-mail addresses or your own inverter/dongle serial numbers to these files.
