# Solar Assistant Field Mapping (reference for lux-mon parity)

Captured 2026-08-21 from a live Solar Assistant install (Raspberry Pi,
`192.168.12.12`, SA v2.0.0) and cross-referenced against lux-mon's register
map and the EG4/LuxPower Modbus protocol doc.

## Solar Assistant architecture

Solar Assistant runs the **same stack as lux-mon**:

| Port | Service | Notes |
|------|---------|-------|
| 80   | `beam.smp` (Elixir/Phoenix) | Main SA web app + API |
| 8899 | `beam.smp` | Second SA listener (raw TCP, not HTTP) |
| 1883 | `mosquitto` | MQTT broker (anonymous access allowed) |
| 3000 | `grafana-server` | Grafana (localhost only) |
| 8086 | `influxd` | InfluxDB v1.8 (localhost only) |
| 8088 | `influxd` | InfluxDB RPC (localhost only) |
| 5353 | `avahi-daemon` | mDNS, publishes `_solar-assistant._tcp` |

The Elixir app self-extracts to `/dev/shm/grafana-sync/<hash>/` and its
persistent Grafana dashboards live in `/etc/grafana/solar-assistant/dashboards/`.

## InfluxDB schema (SA measurement → field)

SA uses **one measurement per metric**, with a single field:
- `inverter_0` for single-inverter values
- `combined` for site-wide/aggregate values

Full measurement list (35 total):

```
AC output voltage        Battery current          Battery power
Battery power hourly     Battery power in hourly  Battery power out hourly
Battery state of charge  Battery temperature      Battery voltage
CPU temperature          Cloud cover              Free storage
Grid frequency           Grid power               Grid power hourly
Grid power in hourly     Grid power out hourly    Grid voltage
Inverter temperature     Load power               Load power essential
Load power hourly        Load power non-essential Outside temperature
PV current 1             PV current 2             PV power
PV power 1               PV power 2               PV power hourly
PV power predicted       PV power predicted hourly PV voltage 1
PV voltage 2
```

## Field → register mapping (SA vs lux-mon)

| SA measurement | SA field | lux-mon register | lux-mon name | Match |
|---|---|---|---|---|
| Battery voltage | inverter_0 | 4 | battery_voltage | ✅ |
| Battery state of charge | combined | 5 | soc | ✅ |
| Battery power | combined | 10/11 | charge/discharge_power | ✅ (sign) |
| Battery current | inverter_0 | 98 | battery_current | ✅ (signed) |
| Battery temperature | combined | 67 | temp_battery | ✅ |
| PV power 1 | inverter_0 | 7 | pv1_power | ✅ |
| PV power 2 | inverter_0 | 8 | pv2_power | ✅ |
| PV voltage 1 | inverter_0 | 1 | pv1_voltage | ✅ |
| PV voltage 2 | inverter_0 | 2 | pv2_voltage | ✅ |
| PV current 1 | inverter_0 | — | (derived) | ⚠️ see below |
| PV current 2 | inverter_0 | — | (derived) | ⚠️ see below |
| Grid voltage | inverter_0 | 12 | grid_voltage_r | ✅ |
| Grid frequency | inverter_0 | 15 | grid_frequency | ✅ |
| Grid power | combined | 26/27 | grid_export/import_power | ✅ |
| Load power | combined | 24 | eps_power | ✅ |
| AC output voltage | inverter_0 | 20 | eps_voltage_r | ✅ |
| Inverter temperature | inverter_0 | **66** | temp_radiator_2 | ⚠️ see below |
| Outside temperature | combined | — | (not from inverter) | — |
| Cloud cover | combined | — | (not from inverter) | — |
| PV power predicted | combined | — | (not from inverter) | — |
| CPU temperature | combined | — | (Pi CPU, not inverter) | — |

## Key findings

### 1. "Inverter temperature" is a labeling difference, NOT a bug

SA's "Inverter temperature" reads **register 66 (Radiator 2 / heat-sink 2)**,
not register 64 (internal temperature).

- SA "Inverter temperature" = 102.2°F = 39°C = lux-mon `temp_radiator_2` (reg 66)
- lux-mon `temp_inverter` = 91.4°F = 33°C = reg 64 (internal temp)

Per the EG4 protocol doc, reg 64 = "Internal Temp", reg 65/66 = "Radiator 1/2
Temp" (heat sinks). **lux-mon is correct**; SA is mislabeling the heat-sink
temperature as "Inverter temperature". lux-mon exposes both `temp_inverter`
(reg 64) and `temp_radiator_2` (reg 66) separately, which is strictly more
correct.

### 2. Battery current is signed (negative = discharge)

Register 98 (`battery_current`) is signed. When discharging, the raw value is
two's-complement negative (e.g. raw 65432 → -10.4 A).

**Bug fixed 2026-08-21:** `PassiveCollector._clamp_values` was clamping *any*
negative value to 0.0, which zeroed out `battery_current` during discharge.
Added `_SIGNED_FIELDS = {"battery_current"}` so signed fields are not
zero-clamped.

### 3. Fields SA derives that lux-mon does not (yet) have

These are not raw inverter registers — SA computes or sources them elsewhere:

- **PV current 1/2** — SA reports these; lux-mon has `pv1_voltage`/`pv2_voltage`
  but no `pv1_current`/`pv2_current` register. Current can be derived as
  `power / voltage` per MPPT string.
- **Outside temperature** — sourced from a weather API, not the inverter.
- **Cloud cover** — sourced from a weather API.
- **PV power predicted** — sourced from a weather/solar forecast API.
- **CPU temperature** — the Pi's own CPU temp, not the inverter.
- **Free storage** — the Pi's disk free space.
- **Hourly rollups** (`* hourly`, `* in hourly`, `* out hourly`) — InfluxDB
  continuous queries / downsampling, not raw registers.

## lux-mon parity gaps (to close)

1. Derive `pv1_current` / `pv2_current` from `pv1_power / pv1_voltage` (and
   same for string 2) to match SA's "PV current 1/2".
2. Add weather-sourced `outside_temperature`, `cloud_cover`, and
   `pv_power_predicted` (optional; requires a weather API).
3. Add hourly downsampling measurements to match SA's `* hourly` series.
4. (Optional) Add `CPU temperature` and `Free storage` for host health parity.
