# EG4 cloud fixtures (`transport=cloud_http`)

Real EG4 portal (monitor.eg4electronics.com) JSON payloads used by
`tests/test_cloud_http.py`. Each file holds the response body exactly as it was
captured (one line), except where noted below. No request in the test suite
touches the network.

| File | Endpoint | Device / notes | Source |
|------|----------|----------------|--------|
| `runtime_12000xp.json` | `POST /WManage/api/inverter/getInverterRuntime` | EG4 12000XP (portal model SNA12K-US, fwCode ceaa-0508, 1 battery, off-grid on PV + battery, status 0xC0), captured 2026-01-09 by the issue reporter from browser devtools. **Modified:** `serialNum` set to the placeholder `TEST000001` (the reporter had blanked it). | https://github.com/joyfulhouse/eg4_web_monitor/issues/76#issuecomment-3730924449 |
| `runtime_18kpv.json` | `POST /WManage/api/inverter/getInverterRuntime` | EG4 18kPV (fAAB-2122, 3x EG4 280 Ah, grid-tied, AC-charging), pylxpweb maintainer's sanitized capture, 2025-09-10. | https://github.com/joyfulhouse/pylxpweb/blob/ef91a3a0c24b9b9e36dbb2acaa9188a5d44169ed/tests/samples/runtime_1234567890.json |
| `runtime_offline.json` | `POST /WManage/api/inverter/getInverterRuntime` | 18kPV offline (`lost: true`, statusText offline): partial payload with frozen last values, 2026-06. | https://github.com/joyfulhouse/pylxpweb/blob/ef91a3a0c24b9b9e36dbb2acaa9188a5d44169ed/tests/samples/runtime_offline.json |
| `energy_18kpv.json` | `POST /WManage/api/inverter/getInverterEnergyInfo` | Same 18kPV as `runtime_18kpv.json`. No public 12000XP energy capture exists; the schema is shared. | https://github.com/joyfulhouse/pylxpweb/blob/ef91a3a0c24b9b9e36dbb2acaa9188a5d44169ed/tests/samples/energy_1234567890.json |
| `battery_18kpv.json` | `POST /WManage/api/battery/getBatteryInfo` | 18kPV with 3 EG4-protocol battery modules (full `batteryArray`). | https://github.com/joyfulhouse/pylxpweb/blob/ef91a3a0c24b9b9e36dbb2acaa9188a5d44169ed/tests/samples/battery_1234567890.json |
| `battery_no_array.json` | `POST /WManage/api/battery/getBatteryInfo` | Battery payload with no module array (`totalNumber` 0, capacity fields 0), the shape reported for 12000XP units. Serial blanked to `XXX` by the reporter. | https://github.com/joyfulhouse/eg4_web_monitor/issues/76#issuecomment-3730900865 |
| `battery_offline.json` | `POST /WManage/api/battery/getBatteryInfo` | Offline device (`lost: true`), mostly empty strings / zeros, 2026-06. | https://github.com/joyfulhouse/pylxpweb/blob/ef91a3a0c24b9b9e36dbb2acaa9188a5d44169ed/tests/samples/battery_offline.json |
| `login.json` | `POST /WManage/api/login` | Sanitized account with FlexBOSS21 + 18KPV + GridBOSS (placeholder serials, `user@example.com`). No public 12000XP login capture exists. | https://github.com/joyfulhouse/pylxpweb/blob/ef91a3a0c24b9b9e36dbb2acaa9188a5d44169ed/tests/samples/login.json |
| `session_expired.html` | any data endpoint | **Synthetic**: a minimal HTML login page standing in for the HTTP 200 HTML response the portal returns when a session has expired. Not a capture. | n/a |

Do not add real account data, e-mail addresses or your own inverter/dongle
serial numbers to these files.
