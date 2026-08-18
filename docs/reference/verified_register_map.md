# lux-mon Verified Register Map

Cross-reference of the EG4 Monitor "Old Settings" page (`remoteSetOffGrid`) field
names against the LXP register map and live-captured dongle values.

**Source:** EG4 portal JS (`remoteCtrlCommon.js`, `remoteSet.js`) + LXP_REGISTERS.txt
+ live differential capture of dongle `192.168.12.224:8000`.

**Inverter:** Luxpower SNA, model 740, serial `5203740777`, dongle `BJ44501402`.

## Write mechanism (authoritative)

The EG4 "Set" button for each field calls:

```
POST /WManage/web/maintain/remoteSet/write
{ inverterSn, holdParam: <NAME>, valueText: <value>, clientType: 'WEB', remoteSetType: 'NORMAL' }
```

- `holdParam` = holding register (16-bit value)
- `bitParam` = bitmask register (fetch-modify-write a single bit)
- `functionParam` = function enable/disable (maps to a bit in register 21 or 110)
- `timeParam` = time-of-day (HH:MM encoded as HH*256+MM)

## Holding registers (holdParam → LXP register)

| EG4 holdParam | LXP reg | Name | Unit | Scale | EG4 min/max |
|---|---|---|---|---|---|
| HOLD_PV_INPUT_MODE | 20 | PV input mode | enum | 1 | 0/3/4 |
| HOLD_LINE_MODE_INPUT | 146 | AC input range | enum | 1 | 0/1 |
| HOLD_LEAD_ACID_CHARGE_VOLT_REF | 99 | Lead-acid charge voltage | V | 0.1 | 50-58 |
| HOLD_LEAD_ACID_CHARGE_RATE | 101 | Lead-acid charge rate | A | 1 | 0-4480 |
| HOLD_LEAD_ACID_DISCHARGE_RATE | 102 | Lead-acid discharge rate | A | 1 | 0-4480 |
| HOLD_LEAD_ACID_DISCHARGE_CUT_OFF_VOLT | 100 | Lead-acid discharge cutoff | V | 0.1 | 40-56 |
| HOLD_FEED_IN_GRID_POWER_PERCENT | 103 | Feed-in grid power % | % | 1 | 0-255 |
| HOLD_FLOATING_VOLTAGE | 144 | Floating voltage | V | 0.1 | 50-58 |
| HOLD_EQUALIZATION_VOLTAGE | 149 | Equalization voltage | V | 0.1 | 50-59 |
| HOLD_EQUALIZATION_PERIOD | 150 | Equalization period | days | 1 | 0-365 |
| HOLD_EQUALIZATION_TIME | 151 | Equalization time | hours | 1 | 0-24 |
| HOLD_AC_CHARGE_BATTERY_CURRENT | 168 | AC charge battery current | A | 1 | 0-125 |
| HOLD_AC_CHARGE_START_BATTERY_VOLTAGE | 158 | AC charge start voltage | V | 0.1 | 38.4-57 |
| HOLD_AC_CHARGE_END_BATTERY_VOLTAGE | 159 | AC charge end voltage | V | 0.1 | 48-59 |
| HOLD_AC_CHARGE_START_BATTERY_SOC | 160 | AC charge start SOC | % | 1 | 1-90 |
| HOLD_AC_CHARGE_END_BATTERY_SOC | 161 | AC charge end SOC | % | 1 | 20-100 |
| HOLD_BATTERY_WARNING_VOLTAGE | 162 | Battery warning voltage | V | 0.1 | 40-56 |
| HOLD_BATTERY_WARNING_SOC | 164 | Battery warning SOC | % | 1 | 0-90 |
| HOLD_SOC_LOW_LIMIT_EPS_DISCHG | 125 | SOC low limit EPS discharge | % | 1 | 0-90 |
| HOLD_ON_GRID_EOD_VOLTAGE | 169 | On-grid EOD voltage | V | 0.1 | 40-58 |
| HOLD_DISCHG_CUT_OFF_SOC_EOD | 105 | Discharge cutoff SOC EOD | % | 1 | 10-90 |
| HOLD_MAX_GENERATOR_INPUT_POWER | 177 | Max generator input power | W | 1 | 0-65534 |
| HOLD_SET_MASTER_OR_SLAVE | 112 | Parallel mode | enum | 1 | 0/1/2 |
| HOLD_SET_COMPOSED_PHASE | 113 | Composed phase | enum | 1 | 0/1/2 |
| _12K_HOLD_LEAD_CAPACITY | 147 | Battery capacity | Ah | 1 | 1-65535 |

### AC First time slots (timeParam → LXP register)

| EG4 timeParam | LXP reg | Meaning |
|---|---|---|
| HOLD_AC_FIRST_START_TIME | 152 | AC first slot 1 start |
| HOLD_AC_FIRST_END_TIME | 153 | AC first slot 1 end |
| HOLD_AC_FIRST_START_TIME_1 | 154 | AC first slot 2 start |
| HOLD_AC_FIRST_END_TIME_1 | 155 | AC first slot 2 end |
| HOLD_AC_FIRST_START_TIME_2 | 156 | AC first slot 3 start |
| HOLD_AC_FIRST_END_TIME_2 | 157 | AC first slot 3 end |

### AC Charge time slots (timeParam → LXP register)

| EG4 timeParam | LXP reg | Meaning |
|---|---|---|
| HOLD_AC_CHARGE_START_TIME | 68 | AC charge slot 1 start |
| HOLD_AC_CHARGE_END_TIME | 69 | AC charge slot 1 end |
| HOLD_AC_CHARGE_START_TIME_1 | 70 | AC charge slot 2 start |
| HOLD_AC_CHARGE_END_TIME_1 | 71 | AC charge slot 2 end |
| HOLD_AC_CHARGE_START_TIME_2 | 72 | AC charge slot 3 start |
| HOLD_AC_CHARGE_END_TIME_2 | 73 | AC charge slot 3 end |

## Bitmask registers (bitParam)

| EG4 bitParam | LXP reg | Bit | Meaning |
|---|---|---|---|
| BIT_AC_CHARGE_TYPE | 21 | — | AC charge based on (Disable/Time/Voltage/SOC/Volt+Time/SOC+Time) |
| BIT_GENERATOR_CHARGE_TYPE | 21 | — | Generator charge type (Voltage/SOC) |
| BIT_DISCHG_CONTROL_TYPE | 21 | — | Discharge control (Voltage/SOC) |
| BIT_FAN_1_MAX_SPEED | — | — | Fan 1 max speed % (10-100) |
| BIT_FAN_2_MAX_SPEED | — | — | Fan 2 max speed % (10-100) |

## Function enable/disable (functionParam → register 21/110 bits)

| EG4 functionParam | LXP reg | Bit | Meaning |
|---|---|---|---|
| FUNC_SET_TO_STANDBY | 21 | MSB bit1 | Normal/Standby |
| FUNC_BATTERY_ECO_EN | 21 | — | Battery ECO |
| FUNC_BUZZER_EN | 21 | — | Buzzer |
| FUNC_PV_GRID_OFF_EN | 110 | — | PV grid off |
| FUNC_PV_ARC | 21 | — | PV arc |
| FUNC_PV_ARC_FAULT_CLEAR | — | — | PV arc fault clear (command) |
| FUNC_RSD_DISABLE | 21 | — | RSD |
| FUNC_N_PE_CONNECT_INNER_EN | 21 | — | N-PE bond |
| FUNC_TAKE_LOAD_TOGETHER | 21 | — | PV&AC take load jointly |
| FUNC_GRID_CT_CONNECTION_EN | 21 | — | Grid CT connection |
| FUNC_FEED_IN_GRID_EN | 21 | MSB bit7 | Export to grid |
| FUNC_BAT_SHARED | 21 | — | Battery shared |
| FUNC_RUN_WITHOUT_GRID_12K | 110 | — | Run without grid |
| FUNC_GEN_PEAK_SHAVING | 21 | — | Generator boost |
| FUNC_AC_COUPLING_FUNCTION | 21 | — | AC couple |
| FUNC_SMART_LOAD_ENABLE | 21 | — | Smart load |
| FUNC_ON_GRID_ALWAYS_ON | 21 | — | Grid always on |
| FUNC_FAN_SPEED_SLOPE_CTRL_1 | — | — | Fan 1 speed slope |
| FUNC_FAN_SPEED_SLOPE_CTRL_2 | — | — | Fan 2 speed slope |

## Generator charge (OFF_GRID_HOLD_* — off-grid only)

| EG4 holdParam | Meaning | EG4 min/max |
|---|---|---|
| OFF_GRID_HOLD_MAX_GEN_CHG_BAT_CURR | Generator charge current | 0-110 A |
| OFF_GRID_HOLD_GEN_CHG_START_VOLT | Gen charge start voltage | 38.4-57 V |
| OFF_GRID_HOLD_GEN_CHG_END_VOLT | Gen charge end voltage | 48-59 V |
| OFF_GRID_HOLD_GEN_CHG_START_SOC | Gen charge start SOC | 1-90 % |
| OFF_GRID_HOLD_GEN_CHG_END_SOC | Gen charge end SOC | 20-100 % |

## AC Couple / Smart Load (_12K_HOLD_*)

| EG4 holdParam | Meaning | EG4 min/max |
|---|---|---|
| _12K_HOLD_AC_COUPLE_START_VOLT | AC couple start volt | 40-52 V |
| _12K_HOLD_AC_COUPLE_START_SOC | AC couple start SOC | 0-80 % |
| _12K_HOLD_AC_COUPLE_END_VOLT | AC couple end volt | 40-56 V |
| _12K_HOLD_AC_COUPLE_END_SOC | AC couple end SOC | 0-100 % |
| _12K_HOLD_START_PV_POWER | Smart load start PV power | 0-25.5 kW |
| _12K_HOLD_SMART_LOAD_START_VOLT | Smart load start volt | 40-59 V |
| _12K_HOLD_SMART_LOAD_START_SOC | Smart load start SOC | 0-100 % |
| _12K_HOLD_SMART_LOAD_END_VOLT | Smart load end volt | 40-59 V |
| _12K_HOLD_SMART_LOAD_END_SOC | Smart load end SOC | 0-100 % |

## Quick Charge (cloud task — NOT a register write)

Quick Charge is a **cloud-orchestrated task**, not a direct register write:

- **Start**: `POST /web/config/quickCharge/start` `{inverterSn, minute: 1-240}`
- **Stop**: `POST /web/config/quickCharge/stop` `{inverterSn}`
- **Status**: `POST /web/config/quickCharge/getStatusInfo` → `{hasUnclosedQuickChargeTask, remainTimeBeforeQuickChargeStop, unclosedQuickChargeTaskStatus}`

The cloud sends Modbus writes to the dongle as the task progresses (WAIT_CHARGE → CHARGING → DONE).
**Register 234** is the quick-charge remaining-minutes countdown (observed 55→54→53→52).

## Generator Quick Start (direct command)

- **Start**: `POST /api/inverter/ctrlGenExercise` `{inverterSn, enable: true}`
- **Stop**: `POST /api/inverter/ctrlGenExercise` `{inverterSn, enable: false}`

## Live-captured baseline values (2026-08-17 20:34 CDT)

| Register | Value | Meaning |
|---|---|---|
| 20 | 4 | PV input mode = two MPPT different string |
| 21 | 897 (0x381) | FUNC bitmask (AC charge enabled) |
| 66 | 100 | AC charge power % |
| 67 | 0 | AC charge SOC limit |
| 110 | 32896 (0x8080) | FUNC bitmask 2 |
| 146 | 1 | AC input range = UPS |
| 147 | 350 | Battery capacity 350Ah |
| 148 | 480 | Nominal battery voltage 48.0V |
| 168 | 100 | AC charge battery current 100A |
| 177 | 5000 | Max generator input 5000W |
| 234 | 55 | Quick charge remaining minutes |
