# SolarAssistant Automation — Reference Field Map

> Source: live inspection of the SolarAssistant device at `192.168.1.100/power`
> (Phoenix LiveView / Elixir app), captured 2026-08-27 via Playwright.
> This is the authoritative writable-register surface SolarAssistant exposes for
> the Luxpower / EG4 inverter, mapped for re-implementation in lux-mon.

## Overview

The `/power` page "Add automation" button opens a modal (`#add-automation-modal`)
with a **"Select automation"** chooser. Clicking **"show all"** reveals **4
top-level automation types**:

| # | Automation type | `phx-value-id` | Behavior |
|---|---|---|---|
| 1 | Rule table | `set-setting` | Form-driven (setting → conditions → values) |
| 2 | Send notification | `notify` | Form-driven (conditions only) |
| 3 | Battery state of charge control | `battery-soc` | One-click template (auto-creates) |
| 4 | Battery protection | `battery-protection` | One-click template (auto-creates) |

The first two are **form-driven** (built step by step). The last two are
**one-click templates** that immediately create a populated automation you then
edit.

---

## 1. Rule table (`set-setting`)

The most complex type. Three levels.

### Level 1 — "Which setting do you want to control?"

A single dropdown (`#setting`) with **50 inverter settings**, all under the
`Inverter` optgroup, prefixed `inverters_0_`:

| Value | Label |
|---|---|
| `ac_couple` | AC couple |
| `absorption_charge_voltage` | Absorption charge voltage |
| `combine_pv/ac_supply` | Combine PV/AC supply |
| `discharge_according_to` | Discharge according to |
| `export_power_rate` | Export power rate |
| `export_to_grid` | Export to grid |
| `float_charge_voltage` | Float charge voltage |
| `force_off_grid` | Force off grid |
| `generator_charge_according_to` | Generator charge according to |
| `generator_charge_current` | Generator charge current |
| `generator_charge_start_capacity` | Generator charge start capacity |
| `generator_charge_start_voltage` | Generator charge start voltage |
| `generator_charge_stop_capacity` | Generator charge stop capacity |
| `generator_charge_stop_voltage` | Generator charge stop voltage |
| `generator_power` | Generator power |
| `grid_charge` | Grid charge |
| `grid_charge_according_to` | Grid charge according to |
| `grid_charge_current` | Grid charge current |
| `grid_charge_slot_1_end` | Grid charge slot 1 end |
| `grid_charge_slot_1_start` | Grid charge slot 1 start |
| `grid_charge_slot_2_end` | Grid charge slot 2 end |
| `grid_charge_slot_2_start` | Grid charge slot 2 start |
| `grid_charge_slot_3_end` | Grid charge slot 3 end |
| `grid_charge_slot_3_start` | Grid charge slot 3 start |
| `grid_charge_start_capacity` | Grid charge start capacity |
| `grid_charge_start_voltage` | Grid charge start voltage |
| `grid_charge_stop_capacity` | Grid charge stop capacity |
| `grid_charge_stop_voltage` | Grid charge stop voltage |
| `grid_first_slot_1_end` | Grid first slot 1 end |
| `grid_first_slot_1_start` | Grid first slot 1 start |
| `grid_first_slot_2_end` | Grid first slot 2 end |
| `grid_first_slot_2_start` | Grid first slot 2 start |
| `grid_first_slot_3_end` | Grid first slot 3 end |
| `grid_first_slot_3_start` | Grid first slot 3 start |
| `max_charge_current` | Max charge current |
| `max_discharge_current` | Max discharge current |
| `on-grid_mode` | On-grid mode |
| `shutdown_battery_capacity` | Shutdown battery capacity |
| `shutdown_battery_voltage` | Shutdown battery voltage |
| `smart_load` | Smart load |
| `smart_load_start_pv_power` | Smart load start PV power |
| `smart_load_start_capacity` | Smart load start capacity |
| `smart_load_start_voltage` | Smart load start voltage |
| `smart_load_stop_capacity` | Smart load stop capacity |
| `smart_load_stop_voltage` | Smart load stop voltage |
| `stop_discharge_capacity` | Stop discharge capacity |
| `stop_discharge_voltage` | Stop discharge voltage |
| `warning_start_capacity` | Warning start capacity |
| `warning_start_voltage` | Warning start voltage |

### Level 2 — "Based on which condition(s)?"

After picking a setting, a condition dropdown (`#condition_0`) appears with **9
condition types**:

| Value | Label |
|---|---|
| `system__info_time_of_day` | Time of day |
| `system__info_day_of_week` | Day of week |
| `system__info_month_of_year` | Month of year |
| `totals__status_battery_state_of_charge` | Battery state of charge |
| `totals__status_battery_voltage` | Battery voltage |
| `totals__status_grid_voltage` | Grid voltage |
| `inverters_0_status_grid_frequency` | Grid frequency |
| `weather__status_pv_energy_progress` | PV energy progress |
| `weather__status_pv_energy_remaining_today` | PV energy remaining today |

A **second optional condition** (`#condition_1`) then appears, with the same
list **minus** the already-selected condition (can't pick the same one twice).
Defaults to `--- Optional ---`.

### Level 3 — Condition value(s) + action value

Clicking **Next** saves the automation (it does NOT advance to a separate step)
and reveals the rule table. The third level is the **value inputs**:

- **Condition value**: for "Battery state of charge" it's a **range** — two
  number inputs `from` (min 0, max 100, step 1) and `to` (min 0, max 100,
  step 1), rendered as `0 % to 25 %`.
- **Action value**: the "Set [setting]" column holds the value to write to the
  chosen setting (e.g. "Grid charge" → on/off, or a numeric value).
- **Enable/Disable** toggle per automation.

The rule table renders as a grid: **"When [condition] → Set [setting]"** with
value cells inline.

---

## 2. Send notification (`notify`)

Simpler — no setting to control, just conditions.

- **Level 1**: "Based on which condition(s)?" — same **9 condition types**.
- **Level 2**: optional second condition (`--- Optional ---`).
- **Level 3**: condition value(s) (range for SOC, etc.). No action column — it
  sends a notification when the condition is met.

---

## 3. Battery state of charge control (`battery-soc`)

**One-click template** — clicking it immediately creates a "Battery state of
charge" automation (no form). Renders a rich visual editor:

- **Point A** and **Point B**, each with:
  - **Time of day** (e.g. 08:00, 15:00)
  - **Battery capacity** thresholds: "X% or lower → Grid" and "Y% or higher →
    Battery"
- A **State of charge vs Hour of day** chart (SOC 20/40/60/80% on Y, hours
  00–24 on X) with a divider line: "Battery above this line / Grid below this
  line".
- **Enable** toggle.
- Warning banner: *"Your battery source is set to 'Use inverter values' while
  the inverter doesn't have a BMS connection..."*

Essentially a **time-of-day + SOC-based grid/battery priority scheduler** with a
visual curve editor.

---

## 4. Battery protection (`battery-protection`)

**One-click template** — creates a "Battery protection" automation. Edit form:

- **Trigger**: "If battery state of charge is **[dropdown: 5/10/15/20/25/30/35/40/45/50]%** or lower"
- **Action 1**: "Shutdown inverter output via **Shutdown battery voltage** setting" (link to `/inverter/settings`)
- **Action 2**: "When state of charge is recovered, restore setting to **[number: 40.0, min 40.0, max 59.0, step 0.1]** V"
- **Enable** toggle

---

## Key structural notes

1. **Two distinct patterns**: form-driven (Rule table, Send notification) vs.
   template-driven (Battery SOC control, Battery protection).
2. **Condition list is shared** across Rule table and Send notification — 9
   conditions: 3 time-based (`system__info_*`), 4 measurement-based
   (`totals__status_*` + `inverters_0_status_grid_frequency`), 2 weather/PV-based
   (`weather__status_*`).
3. **The "Next" button actually saves** — it doesn't advance to a separate step.
   The third-level value inputs appear inline in the saved rule table, not in a
   pre-save form.
4. **The setting list (50 items) is the full inverter register surface**
   SolarAssistant can control — the authoritative list of what's writable on the
   Luxpower/EG4 inverter.
5. **Existing automations on the device** (reference): 2× "Grid charge" (SOC
   0–25% → set Grid charge), 3× "Battery state of charge", 1× "Battery
   protection" (SOC ≤25% → shutdown via voltage, restore 40.0V).

---

## Mapping to lux-mon (implementation notes)

- The 50-item setting list maps to lux-mon's **controllable holding registers**
  (see `collector/protocol.py` and `/api/holding/controllable`). Most are already
  exposed; the generator-charge and smart-load families are the likely gaps.
- The 9 condition types map to lux-mon's **live metrics** (battery SOC/voltage,
  grid voltage/frequency, PV energy) plus time-of-day/day-of-week/month-of-year
  (pure scheduler conditions, no register read needed).
- lux-mon already has a **schedule editor** (Grid charge + AC first) and a
  **quick-charge** feature. The SolarAssistant "Rule table" is a more general
  automation engine (arbitrary condition → arbitrary setting write) that lux-mon
  does not yet have.
- The "Battery SOC control" and "Battery protection" templates are
  higher-level conveniences built on top of the rule engine.

### Rule table = two-level nested grid

The rule-table automation is a two-level nested grid, matching Solar Assistant's
actual UI (a "When [group] And when [range] Set [setting]" grid with `+`/`-`
buttons on each level):

```
set <setting>  ->  when <group condition>  ->  and when <range condition>
                   ->  set value to <range action_value>
```

- **Top level** = `setting` — the holding register to write (free choice from
  the 50-item list).
- **Group level** = `group_kind` + a list of `groups`.  Each group is a
  free-choice condition (e.g. a time-of-day block) with a `+`/`-` button.
- **Range level** = `range_kind` + a list of `ranges` inside each group.  Each
  range is a free-choice condition (e.g. a battery-voltage range) plus its own
  `action_value` (the value written to `setting` when both the group and range
  match).
- **Only `setting` is ever written to the inverter.** The group and range
  conditions are pure validation gates — they never write anything themselves.

Both `group_kind` and `range_kind` are free choices from the full condition
list — nothing is hardcoded to time/voltage.  The engine finds the first group
whose condition matches, then the first range within it whose condition
matches, and writes that range's `action_value` to `setting`.

Example (the exact Solar Assistant "Grid charge current" rule):

```
setting = grid_charge_current
group_kind = time_of_day, range_kind = battery_voltage

00:00–06:00   0–44 V  → 125 A
             44–48 V  → 100 A
             48–52 V  →  85 A
             52–56 V  →  45 A
             56–58 V  →   5 A
06:01–20:59   0–59 V  →   0 A
21:00–23:59   0–56 V  → 100 A
             56–56 V  →  15 A
```
