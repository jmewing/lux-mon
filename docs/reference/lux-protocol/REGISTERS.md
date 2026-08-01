# EG4 18kPV Register Map

*Source: lxp-bridge `LXP_REGISTERS.txt` + packet.rs Register enum + community research*

---

## Holding Registers (Read/Write via DeviceFunction 3/6/16)

These are the configuration registers. Read with ReadHold (3), write with WriteSingle (6) or WriteMulti (16).

### System Config

| Reg | Name | Description | R/W |
|-----|------|-------------|-----|
| 0-1 | MODEL | Model info (lithiumType, powerRating, batteryType, etc.) | R |
| 2-6 | SERIAL_NUM | Inverter serial number | R |
| 7 | FW_CODE | Firmware code | R |
| 12-14 | TIME | Current time (see encoding below) | RW |
| 15 | COM_ADDR | Communication address | RW |
| 16 | LANGUAGE | Display language | RW |
| 19 | DEVICE_TYPE | Device type identifier | R |
| 20 | PV_INPUT_MODE | PV input configuration | RW |

### Register 21 — Master Function Bitmask ⭐

**This is the most important register for mode control.**

| Bit | Name | Description |
|-----|------|-------------|
| 0 | eps_en | EPS (Emergency Power Supply) enable |
| 1 | ovf_load_derate_en | Overload derate enable |
| 2 | drms_en | DRMS enable |
| 3 | lvrt_en | Low voltage ride-through (?) |
| 4 | anti_island_en | Anti-islanding enable |
| 5 | neutral_detect_en | Neutral detection enable |
| 6 | grid_on_power_ss_en | Grid-on power soft start |
| 7 | **ac_charge_en** | **AC (grid) charging enable** |
| 8 | sw_seamless_en | Seamless EPS switching |
| 9 | set_to_standby | Standby mode |
| 10 | **forced_discharge_en** | **Forced discharge enable** |
| 11 | **charge_priority_en** | **Charge priority enable** |
| 12 | iso_en | Isolation detection |
| 13 | gfci_en | Ground fault circuit interrupter |
| 14 | dci_en | DC injection detection |
| 15 | **feed_in_grid_en** | **Feed-in to grid enable** |

**Usage:** Read-modify-write. Fetch current value, flip the bit(s) you want, write back.

### Register 110 — Secondary Function Bitmask

| Bit | Name | Description |
|-----|------|-------------|
| 0 | (unknown) | Setting fails with error |
| 1 | fast_zero_export | Fast zero export mode |
| 2 | micro_grid_en | Micro grid enable |

### Grid Protection (25-53)

| Reg | Name | Description | R/W |
|-----|------|-------------|-----|
| 22 | START_PV_VOLT | PV voltage to start inverter | RW |
| 23 | CONNECT_TIME | Grid connect delay time | RW |
| 24 | RECONNECT_TIME | Grid reconnect delay time | RW |
| 25-40 | GRID_VOLT_LIMIT* | Grid voltage protection limits (3 levels) | RW |
| 41 | GRID_VOLT_MOV_AVG_HIGH | Grid voltage moving average high | RW |
| 42-53 | GRID_FREQ_LIMIT* | Grid frequency protection limits (3 levels) | RW |

### Power Control (60-65)

| Reg | Name | Description | R/W |
|-----|------|-------------|-----|
| 60 | ACTIVE_POWER_PERCENT | Active power percent command | RW |
| 61 | REACTIVE_POWER_PERCENT | Reactive power percent | RW |
| 62 | PF_CMD | Power factor command | RW |
| 63 | POWER_SOFT_START_SLOPE | Soft start ramp rate | RW |
| 64 | **CHARGE_POWER_PERCENT** | **System charge rate (%)** | RW |
| 65 | **DISCHG_POWER_PERCENT** | **System discharge rate (%)** | RW |

### AC Charge Schedule (66-73) ⭐

| Reg | Name | Description | R/W |
|-----|------|-------------|-----|
| 66 | AC_CHARGE_POWER_CMD | Grid charge power rate (%) | RW |
| 67 | AC_CHARGE_SOC_LIMIT | AC charge SOC limit (%) | RW |
| 68 | AC_CHARGE_START_0 | Period 0 start: hour(MSB) minute(LSB) | RW |
| 69 | AC_CHARGE_END_0 | Period 0 end: hour(MSB) minute(LSB) | RW |
| 70 | AC_CHARGE_START_1 | Period 1 start | RW |
| 71 | AC_CHARGE_END_1 | Period 1 end | RW |
| 72 | AC_CHARGE_START_2 | Period 2 start | RW |
| 73 | AC_CHARGE_END_2 | Period 2 end | RW |

### Charge Priority Schedule (74-81) ⭐

| Reg | Name | Description | R/W |
|-----|------|-------------|-----|
| 74 | CHG_PRIORITY_POWER | Charge priority rate (%) | RW |
| 75 | CHG_PRIORITY_SOC_LIMIT | Charge priority SOC limit (%) | RW |
| 76-77 | CHG_PRIORITY_TIME_0 | Period 0 start/end | RW |
| 78-79 | CHG_PRIORITY_TIME_1 | Period 1 start/end | RW |
| 80-81 | CHG_PRIORITY_TIME_2 | Period 2 start/end | RW |

### Forced Discharge Schedule (82-89) ⭐

| Reg | Name | Description | R/W |
|-----|------|-------------|-----|
| 82 | FORCED_DISCHG_POWER | Forced discharge power rate (%) | RW |
| 83 | FORCED_DISCHG_SOC_LIMIT | Forced discharge SOC floor (%) | RW |
| 84-85 | FORCED_DISCHG_TIME_0 | Period 0 start/end | RW |
| 86-87 | FORCED_DISCHG_TIME_1 | Period 1 start/end | RW |
| 88-89 | FORCED_DISCHG_TIME_2 | Period 2 start/end | RW |

### Battery & SOC Limits (99-169) ⭐

| Reg | Name | Description | R/W |
|-----|------|-------------|-----|
| 99 | LEAD_ACID_CHARGE_VOLT_REF | Charge voltage reference | RW |
| 100 | LEAD_ACID_DISCHG_CUTOFF_VOLT | Discharge cutoff voltage | RW |
| 103 | FEED_IN_GRID_POWER_PERCENT | Max grid export (%) | RW |
| 105 | **DISCHG_CUTOFF_SOC** | **Discharge cutoff SOC (%)** | RW |
| 116 | P_TO_USER_START_DISCHG | Power threshold to start discharge | RW |
| 125 | **EPS_DISCHG_CUTOFF_SOC** | **EPS discharge cutoff SOC (%)** | RW |
| 144 | FLOATING_VOLTAGE | Battery floating voltage | RW |
| 147 | BATTERY_CAPACITY | Battery capacity (Ah) | RW |
| 148 | NOMINAL_BATTERY_VOLTAGE | Nominal battery voltage | RW |
| 160 | **AC_CHARGE_START_SOC** | **SOC to begin AC charging (%)** | RW |
| 161 | **AC_CHARGE_END_SOC** | **SOC to stop AC charging (%)** | RW |
| 162 | BATTERY_WARNING_VOLTAGE | Battery warning voltage | RW |
| 164 | BATTERY_WARNING_SOC | Battery warning SOC (%) | RW |
| 166 | BAT_LOW_TO_UTILITY_VOLT | Switch to grid voltage | RW |
| 167 | BAT_LOW_TO_UTILITY_SOC | Switch to grid SOC (%) | RW |
| 168 | AC_CHARGE_BATTERY_CURRENT | AC charge current limit (A) | RW |
| 169 | ON_GRID_EOD_VOLTAGE | On-grid end-of-discharge voltage | RW |

### Time Register Encoding

Registers 12-14 encode the inverter clock:
```
Reg 12: month (high byte) | year-2000 (low byte)   → e.g., 0x031A = March 2026
Reg 13: hour (high byte)  | day (low byte)          → e.g., 0x1418 = 20:24
Reg 14: second (high byte)| minute (low byte)       → e.g., 0x1E1E = 30:30
```

### Time Schedule Encoding

All schedule registers (68-89, 152-157) pack hour and minute into one u16:
```
value = (hour << 8) | minute
hour   = value >> 8
minute = value & 0xFF
```

---

## Input Registers (Read-only via DeviceFunction 4)

Live data from the inverter. Read in blocks of 40 registers:
- Registers 0-39 (ReadInput1): PV, battery, grid, daily energy
- Registers 40-79 (ReadInput2): Lifetime energy, temperatures
- Registers 80-119 (ReadInput3): BMS data, cell voltages
- Registers 0-126 (ReadInputAll): All in one shot (254 bytes)

### Key Input Registers

| Reg | Name | Scale | Unit | Description |
|-----|------|-------|------|-------------|
| 0 | status | - | bitmask | Inverter status (see StatusString) |
| 1 | v_pv_1 | ÷10 | V | PV string 1 voltage |
| 2 | v_pv_2 | ÷10 | V | PV string 2 voltage |
| 3 | v_pv_3 | ÷10 | V | PV string 3 voltage |
| 4 | v_bat | ÷10 | V | Battery voltage |
| 5(lo) | soc | - | % | State of charge |
| 5(hi) | soh | - | % | State of health |
| 7 | p_pv_1 | - | W | PV string 1 power |
| 8 | p_pv_2 | - | W | PV string 2 power |
| 9 | p_pv_3 | - | W | PV string 3 power |
| 10 | p_charge | - | W | Battery charge power |
| 11 | p_discharge | - | W | Battery discharge power |
| 12 | v_ac_r | ÷10 | V | Grid voltage (phase R) |
| 15 | f_ac | ÷100 | Hz | Grid frequency |
| 16 | p_inv | - | W | Inverter power |
| 17 | p_rec | - | W | Rectifier power |
| 23 | p_to_grid | - | W | Power exporting to grid |
| 24 | p_to_user | - | W | Power importing from grid |
| 25 | e_pv_day_1 | ÷10 | kWh | PV gen today, string 1 |
| 26 | e_pv_day_2 | ÷10 | kWh | PV gen today, string 2 |
| 27 | e_pv_day_3 | ÷10 | kWh | PV gen today, string 3 |
| 30 | e_chg_day | ÷10 | kWh | Battery charge today |
| 31 | e_dischg_day | ÷10 | kWh | Battery discharge today |
| 33 | e_to_grid_day | ÷10 | kWh | Grid export today |
| 34 | e_to_user_day | ÷10 | kWh | Grid import today |

### Inverter Status Codes

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

## Registers We Care About Most

For managing battery schedules, these are the critical ones:

### Read (monitoring)
- Input regs 0-39: SOC, PV power, battery power, grid power, daily energy
- Hold reg 21: Current mode flags (what's enabled)

### Write (control)
- **Reg 21**: Enable/disable AC charge, forced discharge, charge priority, grid feed-in
- **Regs 64-65**: Charge/discharge power rate
- **Regs 66-73**: AC charge schedule (3 time periods + SOC limit)
- **Regs 74-81**: Charge priority schedule
- **Regs 82-89**: Forced discharge schedule
- **Regs 105, 125**: Discharge cutoff SOC limits
- **Regs 160-161**: AC charge start/end SOC
