# lux-mon — Project Status & To-Do

> Recreated 2026-08-27 after the original status/direction notes were lost in a
> system crash. This file tracks features that are mentioned or implied but not
> yet fully implemented, plus known gaps and future direction.

## Legend

- ✅ Done / live
- 🚧 Partially implemented (stub, backend-only, or missing UI)
- ⬜ Not started

---

## Features mentioned in the README but not yet fully implemented

### 1. Generator Charge — 🚧 (backend stubbed, not wired)
- `collector/quick_charge.py` defines `GEN_REGISTER_NAME = "max_generator_input_power"` and a `GEN_REGISTER`, but the generator-charge path is **not** implemented end-to-end.
- The README "Roadmap" lists "Generator and AC-coupled charge support" as future work.
- **To do:** implement the generator-charge start/stop flow (write generator charge current + enable), add API endpoints (`/api/quick-charge/generator/start` etc.), and surface it in the dashboard.

### 2. Forecast.Solar provider — 🚧 (listed, not wired)
- `collector/settings.py` lists `forecast_provider` options `open-meteo` and `forecast.solar`, but `collector/forecast.py` only implements the Open-Meteo path.
- **To do:** implement the Forecast.Solar provider (or remove the option from settings until it's real).

### 3. Battery protection automations — 🚧 (engine supports it, no UI)
- The automation engine supports the `battery_protection` type (SOC threshold + restore-on-exit), but there is no dashboard UI to configure it.
- **To do:** add a battery-protection wizard/panel to the Automations page.

### 4. Inverter Edit Mode page — ⬜ (explicitly "in progress")
- Manual Read/Set for every editable EG4 6000XP holding register, mirroring the EG4 Monitor Maintenance tab.
- **To do:** build the page; the backend already exposes `GET /api/automation/registers` and `PUT /api/settings/{name}`.

### 5. Automation page overhaul — ⬜ (explicitly "in progress")
- Rebuild around a premade option list + wizard (like SolarAssistant's Power Management), with restore-value support and time/day conditions.
- The rule-table engine is done; the UI is still the older flat form.

### 6. Holding register map corrections — ⬜ (explicitly "in progress")
- Align `collector/protocol.py` with `LXP_REGISTERS.txt`, add missing registers, fix ranges, correct AC charge current to register 168.

### 7. More inverter models — 🚧 (aliases only)
- `collector/drivers/registry.py` maps many model names (`eg4_3000ehv`, `eg4_6500ex`, `luxpower_12k`, `growatt`, `solis`, etc.) to the `eg4_6000xp` driver as a fallback.
- **To do:** write dedicated drivers for models that differ from the 6000XP register family.

### 8. RS-485 transport for the main collector — 🚧 (separate daemon only)
- The main collector's `transport` options are `tcp_passive`, `tcp_active`, `replay`. RS-485 is a **separate** daemon (`lux-mon-rs485`), not a pluggable transport of the main collector.
- The collector docstring lists `rtu_serial` and `solarman` as "future transports".
- **To do:** decide whether to unify RS-485 as a main-collector transport or keep the separate daemon.

---

## Known gaps / technical debt

### 9. `LUX_WRITE_INTERVAL` default mismatch
- `collector/__main__.py` and `collector/collector.py` default `write_interval` to **30** seconds, but the README and `settings.py` document **5** seconds.
- **To do:** reconcile the default (likely 5s) across code, docs, and `.env.example`.

### 10. `LUX_STORAGE_TYPE` vs. per-backend flags
- The legacy `LUX_STORAGE_TYPE=mariadb|influxdb` maps to enabling one backend, but the newer `LUX_MARIADB_ENABLED` / `LUX_INFLUX_ENABLED` / `LUX_MQTT_ENABLED` flags allow running multiple backends together.
- The README's "Storage Backends" section still frames it as an either/or choice.
- **To do:** document the multi-backend model clearly and deprecate `LUX_STORAGE_TYPE` in favor of the flags.

### 11. `config.example.py` vs. env-var config
- The README mentions a `config.example.py` / `--config config.py` path, but the collector is now primarily env-var + DB-authoritative. Verify the config-file path still works end-to-end.

### 12. Hourly energy rollup location
- The README mentions "hourly energy rollups", but there is no dedicated `collector/energy.py`. Confirm where hourly rollups are computed (likely `outputs.py` or the API) and document it.

### 13. `bin/lux-mon-rs485` build step
- `bin/lux-mon-rs485` is a Python script (ASCII text executable), not a compiled binary. Confirm it's a thin wrapper around `collector/rs485_collector.py` and document how it's installed/run.

### 14. `captures/` directory — ✅ removed from repo (2026-08-27)
- Contained ~4.6 MB of RS-485 BMS reference captures. Removed via `git rm -r --cached captures/` and added `captures/` to `.gitignore` (files stay on disk, just untracked). No code/docs referenced them.

### 15. Lost `PROJECT.md` / `PLAN.md`
- `.gitignore` references `PROJECT.md` and `PLAN.md` (both gitignored, so never committed). These were the project status/direction notes lost in the crash. This `TODO.md` is their replacement — consider whether to keep them gitignored or track a status doc going forward.

---

## Future direction (from README roadmap)

- ⬜ Generator and AC-coupled charge support (see #1)
- ⬜ Battery protection automations UI (see #3)
- ⬜ More inverter models via pluggable Modbus RTU drivers (see #7)
- ⬜ Forecast.Solar provider (see #2)

---

## Recently completed (for context)

- ✅ Solar PV forecast (Option A weather + Option B historical calibration) — v1.2.1
- ✅ Quick Charge (timed grid charge with restore)
- ✅ Alerts (SOC/temp/grid-loss) with SMTP + webhook
- ✅ RS-485 BMS daemon (EG4 A5/5A, JK BMS, Modbus RTU)
- ✅ Native Home Assistant integration (`jmewing/ha_luxmon`) + HAOS add-on (`jmewing/ha_luxmon_addons`)
- ✅ API port default changed 8080 → 80 repo-wide
- ✅ Automation rule-table engine (target-first, nested subsets, restore-on-exit)
