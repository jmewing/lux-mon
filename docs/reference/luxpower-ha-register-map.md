# LuxPower / EG4 Complete Modbus Register Map

Consolidated reference extracted from the Home Assistant integration
[`ant0nkr/luxpower-ha-integration`](https://github.com/ant0nkr/luxpower-ha-integration)
(v1.0.0), which is itself sourced from the official LuxPower protocol document
`06-01-01-PTC-Luxpower MODBUS Protocol_2024.04.26.pdf` and the
`EG4-18KPV-12LV-Modbus-Protocol.pdf`.

> **Key fact:** LuxPower and EG4 (and other rebrands) **share the same Modbus
> protocol and register map.** The model is identified at runtime by reading
> hold registers 7–8 (4 ASCII chars = firmware/model code) and register 16
> (Device Type / DTC). There is no hardcoded per-model register table — the map
> is shared, with model-specific variations handled by:
> - `PV_INPUT_MODEL` (hold reg 20) — PV string count differs per model
> - Split-phase (US) vs three-phase register layouts
> - 12 V / 24 V / 48 V battery voltage classes

This document is **public protocol data only** — no serials, IPs, or tokens.

---

## Model identification

| Register | Type | Meaning |
|---|---|---|
| 7–8 | Hold | Firmware/model code (4 ASCII chars, 2 chars per register, high byte first) |
| 9 | Hold | Slave Ver (redundant CPU) + Com Ver (communication CPU) |
| 10 | Hold | Cntl Ver (control CPU) + FWVer (external FW) |
| 16 | Hold | Language (0=EN, 1=DE) + Device Type (DTC, 0–31) |
| 19 | Hold | DTC: Device type (3 = XOLTA, high-speed comm) |
| 224 | Hold | LCD version, screen type, ODM, machine model code |

---

## Input Registers (read-only, Function Code 0x04)

### Core status & PV (0–11)

| Reg | Name | Unit | Scale |
|---|---|---|---|
| 0 | Inverter state | — | 1 |
| 1 | PV1 voltage | V | 0.1 |
| 2 | PV2 voltage | V | 0.1 |
| 3 | PV3 voltage | V | 0.1 |
| 4 | Battery voltage | V | 0.1 |
| 5 | SOC (low byte) / SOH (high byte) | % | 1 |
| 6 | Internal fault code | — | 1 |
| 7 | PV1 power | W | 1 |
| 8 | PV2 power | W | 1 |
| 9 | PV3 power (or total) | W | 1 |
| 10 | Battery charge power | W | 1 |
| 11 | Battery discharge power | W | 1 |

### Grid (12–19, 26–27)

| Reg | Name | Unit | Scale |
|---|---|---|---|
| 12 | Grid voltage R | V | 0.1 |
| 13 | Grid voltage S | V | 0.1 |
| 14 | Grid voltage T | V | 0.1 |
| 15 | Grid frequency | Hz | 0.01 |
| 16 | On-grid inverter power (Pinv) | W | 1 |
| 17 | AC charging rectified power (Prec) | W | 1 |
| 18 | Inverter RMS current | A | 0.01 |
| 19 | Power factor | — | 0.001 |
| 26 | Grid export power (PtoGrid) | W | 1 |
| 27 | Grid import power (PtoUser) | W | 1 |

### EPS / off-grid (20–25)

| Reg | Name | Unit | Scale |
|---|---|---|---|
| 20 | EPS voltage R | V | 0.1 |
| 21 | EPS voltage S | V | 0.1 |
| 22 | EPS voltage T | V | 0.1 |
| 23 | EPS frequency | Hz | 0.01 |
| 24 | EPS power | W | 1 |
| 25 | EPS apparent power | VA | 1 |

### Daily energy (28–37, 124, 171)

| Reg | Name | Unit | Scale |
|---|---|---|---|
| 28 | PV1 energy today | kWh | 0.1 |
| 29 | PV2 energy today | kWh | 0.1 |
| 30 | PV3 energy today | kWh | 0.1 |
| 31 | Inverter output energy today | kWh | 0.1 |
| 32 | AC charge rectified energy today | kWh | 0.1 |
| 33 | Battery charge energy today | kWh | 0.1 |
| 34 | Battery discharge energy today | kWh | 0.1 |
| 35 | EPS output energy today | kWh | 0.1 |
| 36 | Grid export energy today | kWh | 0.1 |
| 37 | Grid import energy today | kWh | 0.1 |
| 124 | Generator energy today | kWh | 0.1 |
| 171 | Load energy today | kWh | 0.1 |

### Bus voltages (38–39, 120)

| Reg | Name | Unit | Scale |
|---|---|---|---|
| 38 | Bus 1 voltage | V | 0.1 |
| 39 | Bus 2 voltage | V | 0.1 |
| 120 | Half bus voltage | V | 0.1 |

### Cumulative energy (32-bit pairs, 40–59, 125–126, 172–173)

Each is a low-word/high-word pair. Low word listed; high word = +1.

| Reg (low) | Name | Unit | Scale |
|---|---|---|---|
| 40 | PV1 total energy | kWh | 0.1 |
| 42 | PV2 total energy | kWh | 0.1 |
| 44 | PV3 total energy | kWh | 0.1 |
| 46 | Inverter total energy | kWh | 0.1 |
| 48 | AC charge total energy | kWh | 0.1 |
| 50 | Charge total energy | kWh | 0.1 |
| 52 | Discharge total energy | kWh | 0.1 |
| 54 | EPS total energy | kWh | 0.1 |
| 56 | Grid export total | kWh | 0.1 |
| 58 | Grid import total | kWh | 0.1 |
| 125 | Generator total energy | kWh | 0.1 |
| 172 | Load total energy | kWh | 0.1 |

### Fault / warning codes (32-bit, 60–63)

| Reg | Name |
|---|---|
| 60–61 | Fault code (low/high) |
| 62–63 | Warning code (low/high) |

### Temperature & runtime (64–70)

| Reg | Name | Unit | Scale |
|---|---|---|---|
| 64 | Internal temperature | °C | 1 |
| 65 | Radiator temperature 1 | °C | 1 |
| 66 | Radiator temperature 2 | °C | 1 |
| 67 | Battery temperature | °C | 1 |
| 69–70 | Total runtime (low/high) | s | 1 |

### Auto test (71–75)

| Reg | Name | Unit |
|---|---|---|
| 71 | Auto test status/step | bitfield |
| 72 | Auto test V/F limit | 0.1 V / 0.01 Hz |
| 73 | Auto test default time | ms |
| 74 | Auto test trip value | 0.1 V / 0.01 Hz |
| 75 | Auto test trip time | ms |

### BMS (80–107)

| Reg | Name | Unit | Scale |
|---|---|---|---|
| 80 | Battery type & brand | — | 1 |
| 81 | BMS max charge current | A | 0.01 |
| 82 | BMS max discharge current | A | 0.01 |
| 83 | BMS charge voltage ref | V | 0.1 |
| 84 | BMS discharge cut voltage | V | 0.1 |
| 85–94 | BMS status 0–9 | — | 1 |
| 95 | Inverter battery status summary | — | 1 |
| 96 | Parallel battery count | — | 1 |
| 97 | Battery capacity | Ah | 1 |
| 98 | Battery current (signed) | A | 0.01 |
| 99 | BMS fault code | — | 1 |
| 100 | BMS warning code | — | 1 |
| 101 | Max cell voltage | V | 0.001 |
| 102 | Min cell voltage | V | 0.001 |
| 103 | Max cell temperature (signed) | °C | 0.1 |
| 104 | Min cell temperature (signed) | °C | 0.1 |
| 105 | BMS firmware update state | — | 1 |
| 106 | Cycle count | — | 1 |
| 107 | Inverter battery voltage sample | V | 0.1 |

### Parallel / system (113, 174)

| Reg | Name |
|---|---|
| 113 | Master/slave, phase, parallel count |
| 174 | DIP switch status / safety switch |

### Serial number (115–118)

| Reg | Name |
|---|---|
| 115 | Serial ASCII chars 0–3 |
| 116 | Serial ASCII chars 4–5 |
| 117 | Serial ASCII chars 6–7 |
| 118 | Serial ASCII chars 8–9 |

### Generator (121–123)

| Reg | Name | Unit | Scale |
|---|---|---|---|
| 121 | Generator voltage | V | 0.1 |
| 122 | Generator frequency | Hz | 0.01 |
| 123 | Generator power | W | 1 |

### Split-phase / US-specific (127–138, 193–204)

| Reg | Name | Unit | Scale |
|---|---|---|---|
| 127 | EPS L1-N voltage | V | 0.1 |
| 128 | EPS L2-N voltage | V | 0.1 |
| 129 | EPS L1 power | W | 1 |
| 130 | EPS L2 power | W | 1 |
| 131 | EPS L1 apparent power | VA | 1 |
| 132 | EPS L2 apparent power | VA | 1 |
| 133 | EPS L1 energy today | kWh | 0.1 |
| 134 | EPS L2 energy today | kWh | 0.1 |
| 135–136 | EPS L1 total energy (low/high) | kWh | 0.1 |
| 137–138 | EPS L2 total energy (low/high) | kWh | 0.1 |
| 193 | Grid L1-N voltage | V | 0.1 |
| 194 | Grid L2-N voltage | V | 0.1 |
| 195 | Generator L1-N voltage | V | 0.1 |
| 196 | Generator L2-N voltage | V | 0.1 |
| 197 | Inverting power L1-N | W | 1 |
| 198 | Inverting power L2-N | W | 1 |
| 199 | Rectifying power L1-N | W | 1 |
| 200 | Rectifying power L2-N | W | 1 |
| 201 | Grid export L1-N | W | 1 |
| 202 | Grid export L2-N | W | 1 |
| 203 | Grid import L1-N | W | 1 |
| 204 | Grid import L2-N | W | 1 |

### Three-phase specific (180–192, 205–209)

| Reg | Name | Unit |
|---|---|---|
| 180 | Inverter power S-phase | W |
| 181 | Inverter power T-phase | W |
| 182 | Rectified power S-phase | W |
| 183 | Rectified power T-phase | W |
| 184 | Grid export S-phase | W |
| 185 | Grid export T-phase | W |
| 186 | Grid import S-phase | W |
| 187 | Grid import T-phase | W |
| 188 | Generator power S-phase | W |
| 189 | Generator power T-phase | W |
| 190 | RMS current S-phase | 0.01 A |
| 191 | RMS current T-phase | 0.01 A |
| 192 | Power factor S-phase | 0.001 |
| 205 | Power factor T-phase | 0.001 |
| 208 | On-grid load power S-phase | W |
| 209 | On-grid load power T-phase | W |

### Miscellaneous (114, 139, 170, 153, 206–207, 210, 214–216, 232)

| Reg | Name | Unit | Scale |
|---|---|---|---|
| 114 | On-grid load power (12k) | W | 1 |
| 139 | Reactive power | Var | 1 |
| 170 | Load power (on-grid) | W | 1 |
| 153 | AC couple power | W | 1 |
| 206 | AC couple power S-phase | W | 1 |
| 207 | AC couple power T-phase | W | 1 |
| 210 | Remaining charge time (one-click) | min | 1 |
| 214 | NTC temp INDC | °C | 1 |
| 215 | NTC temp DCDCL | °C | 1 |
| 216 | NTC temp DCDCH | °C | 1 |
| 232 | Smart load power | W | 1 |

### Additional PV strings (217–231)

| Reg | Name | Unit | Scale |
|---|---|---|---|
| 217 | PV4 voltage | V | 0.1 |
| 218 | PV5 voltage | V | 0.1 |
| 219 | PV6 voltage | V | 0.1 |
| 220 | PV4 power | W | 1 |
| 221 | PV5 power | W | 1 |
| 222 | PV6 power | W | 1 |
| 223 | PV4 energy today | kWh | 0.1 |
| 224–225 | PV4 total energy (low/high) | kWh | 0.1 |
| 226 | PV5 energy today | kWh | 0.1 |
| 227–228 | PV5 total energy (low/high) | kWh | 0.1 |
| 229 | PV6 energy today | kWh | 0.1 |
| 230–231 | PV6 total energy (low/high) | kWh | 0.1 |

---

## Hold Registers (writable, Function Code 0x06 / 0x10)

### Firmware & device info (7–20)

| Reg | Name | Notes |
|---|---|---|
| 7–8 | Firmware/model code | Read-only |
| 9 | Slave/Com version | Read-only |
| 10 | Cntl/FW version | Read-only |
| 11 | Reset settings | Bit 7 = restart inverter |
| 12 | Time year/month | |
| 13 | Time day/hour | |
| 14 | Time minute/second | |
| 15 | Modbus comm address | 0–150 |
| 16 | Language + device type | |
| 19 | Device type (high-speed) | 3 = XOLTA |
| 20 | PV input model | 0=no PV, 1=PV1, 2=PV2, 3=PV1&2 parallel, 4=PV1&2 separate |

### Function enable 1 (21) — bitfield

| Bit | Meaning |
|---|---|
| 0 | EPS enable (off-grid) |
| 1 | Overfrequency load derate |
| 2 | DRMS enable |
| 3 | LVRT enable |
| 4 | Anti-islanding |
| 5 | Neutral detection |
| 6 | On-grid power soft start |
| 7 | AC charge enable |
| 8 | Seamless off-grid switch |
| 9 | Standby (0) / power on (1) |
| 10 | Forced discharge enable |
| 11 | Forced charge enable |
| 12 | ISO enable |
| 13 | GFCI enable |
| 14 | DCI enable |
| 15 | Feed-in grid enable |

### Grid & power settings (22–63)

| Reg | Name | Unit | Range |
|---|---|---|---|
| 22 | PV start voltage | 0.1 V | 900–5000 |
| 23 | On-grid wait time | s | 30–600 |
| 24 | Reconnect wait time | s | 0–900 |
| 25 | Grid volt connect low | 0.1 V | |
| 26 | Grid volt connect high | 0.1 V | |
| 27 | Grid freq connect low | 0.01 Hz | |
| 28 | Grid freq connect high | 0.01 Hz | |
| 29–40 | Grid volt limit levels 1–3 (low/high + time) | 0.1 V | |
| 41 | Grid volt moving avg high | 0.1 V | |
| 42–53 | Grid freq limit levels 1–3 (low/high + time) | 0.01 Hz | |
| 54 | Max Q% for Q(V) | % | |
| 55–58 | Q(V) curve V2L/V1L/V1H/V2H | 0.1 V | |
| 59 | Reactive power command type | enum | 0–7 |
| 60 | Active power % command | % | 0–100 |
| 61 | Reactive power % command | % | 0–60 |
| 62 | PF command | 0.001 | |
| 63 | Power soft start slope | %/min | 1–4000 |

### Charge/discharge control (64–89)

| Reg | Name | Unit | Range |
|---|---|---|---|
| 64 | Charge power % (legacy) | % | 0–100 |
| 65 | Discharge power % (legacy) | % | 0–100 |
| 66 | AC charge power % | % | 0–100 |
| 67 | AC charge SOC limit | % | 0–100 |
| 68–73 | AC charge time slots (start/end ×3) | HH:MM | |
| 74 | Charge priority power % | % | 0–100 |
| 75 | Charge priority SOC limit + start hour | % | |
| 76–81 | Charge priority time slots ×3 | HH:MM | |
| 82 | Forced discharge power % | % | 0–100 |
| 83 | Forced discharge SOC limit + start hour | % | |
| 84–89 | Forced discharge time slots ×3 | HH:MM | |

### EPS & Q(V)/P(V) (90–97)

| Reg | Name | Unit | Range |
|---|---|---|---|
| 90 | EPS output voltage | V | 230/240 |
| 91 | EPS output frequency | Hz | 50/60 |
| 92 | cosphi(P) lock-in voltage | 0.1 V | 2300–3000 |
| 93 | cosphi(P) lock-out voltage | 0.1 V | 1500–3000 |
| 94 | Q(V) lock-in power | % | 0–100 |
| 95 | Q(V) lock-out power | % | 0–100 |
| 96 | Q(V) delay | main period | 0–2000 |
| 97 | Overfrequency derate delay | main period | 0–1000 |

### Lead-acid battery (99–109)

| Reg | Name | Unit | Range |
|---|---|---|---|
| 99 | Lead-acid charge voltage | 0.1 V | 500–590 |
| 100 | Lead-acid discharge cutoff | 0.1 V | 400–520 |
| 101 | Charge current | A | 0–140 |
| 102 | Discharge current | A | 0–140 |
| 103 | Feed-in grid power | % | 0–100 |
| 105 | EOD SOC | % | 10–90 |
| 106 | Lead-acid discharge temp low | 0.1 °C | signed |
| 107 | Lead-acid discharge temp high | 0.1 °C | |
| 108 | Lead-acid charge temp low | 0.1 °C | |
| 109 | Lead-acid charge temp high | 0.1 °C | |

### Function enable 3 (110) — bitfield

| Bit | Meaning |
|---|---|
| 0 | PV grid off enable |
| 1 | Fast zero export |
| 2 | Micro grid enable |
| 3 | Battery shared |
| 4 | Charge last enable |
| 5–6 | CT sample ratio |
| 7 | Buzzer enable |
| 8–9 | PV CT sample type |
| 10 | Take load together |
| 11 | On-grid working mode |
| 12–13 | PV CT sample ratio |
| 14 | Green mode enable |
| 15 | Eco mode enable |

### System config (112–120)

| Reg | Name | Notes |
|---|---|---|
| 112 | System type | 0=single, 1=parallel P, 2=parallel S, 3=3-phase M, 4=2×208 M |
| 113 | Composed phase | write-only |
| 114 | Clear function | write 1 to clear parallel alarm |
| 115 | Overfrequency derate start | 0.01 Hz, 5000–5200 |
| 116 | Start discharge threshold | W, default 50 |
| 117 | Start charge threshold | W, default −50 (signed) |
| 118 | Battery voltage derate start | 0.1 V |
| 119 | External CT power offset | W (signed) |
| 120 | System enable 2 | bitfield (AC charge type, discharge ctrl type, etc.) |

### Derate / scheduling (124–143)

| Reg | Name | Unit | Range |
|---|---|---|---|
| 124 | Overfrequency derate end | 0.01 Hz | 5000–5200 |
| 125 | SOC low limit EPS discharge | % | 0–EOD |
| 126–131 | Optimal charge/discharge time marks (00:00–23:30) | enum | 0–3 |
| 132 | Battery cell voltage limits | 0.1 V | |
| 133 | Battery cell count (series/parallel) | — | |
| 134 | Underfrequency derate start | 0.01 Hz | 4500–5000 |
| 135 | Underfrequency derate end | 0.01 Hz | 4500–5000 |
| 136 | Underfrequency derate ratio | %Pm/Hz | 1–100 |
| 137 | Specific load compensation | W | |
| 138 | Charge power % (precise) | 0.1 % | 0–1000 |
| 139 | Discharge power % (precise) | 0.1 % | 0–1000 |
| 140 | AC charge % (precise) | 0.1 % | 0–1000 |
| 141 | Charge priority % (precise) | 0.1 % | 0–1000 |
| 142 | Forced discharge % (precise) | 0.1 % | 0–1000 |
| 143 | Active power % (precise) | 0.1 % | 0–1000 |

### Battery config (144–151)

| Reg | Name | Unit | Range |
|---|---|---|---|
| 144 | Float charge voltage | 0.1 V | 500–560 |
| 145 | Output priority | enum | 0=bat, 1=PV, 2=AC |
| 146 | Line mode | enum | 0=APL, 1=UPS, 2=GEN |
| 147 | Battery capacity | Ah | 0–10000 |
| 148 | Nominal battery voltage | 0.1 V | 400–590 |
| 149 | Equalization voltage | 0.1 V | 500–590 |
| 150 | Equalization interval | day | 0–365 |
| 151 | Equalization time | hour | 0–24 |

### AC first / charge thresholds (152–169)

| Reg | Name | Unit | Range |
|---|---|---|---|
| 152–157 | AC first time slots ×3 | HH:MM | |
| 158 | AC charge start voltage | 0.1 V | 385–520 |
| 159 | AC charge end voltage | 0.1 V | 480–590 |
| 160 | AC charge start SOC | % | 0–90 |
| 161 | AC charge end SOC | % | 0–90 |
| 162 | Battery undervoltage alarm | 0.1 V | 400–500 |
| 163 | Battery undervoltage recovery | 0.1 V | 420–520 |
| 164 | Battery undervoltage SOC alarm | % | 0–90 |
| 165 | Battery undervoltage SOC recovery | % | 20–100 |
| 166 | Battery low-to-utility voltage | 0.1 V | 444–514 |
| 167 | Battery low-to-utility SOC | % | 0–100 |
| 168 | AC charge battery current | A | 0–140 |
| 169 | On-grid EOD voltage | 0.1 V | 400–560 |

### SOC curve (171–175)

| Reg | Name | Unit | Range |
|---|---|---|---|
| 171 | SOC curve battery volt 1 | 0.1 V | 400–600 |
| 172 | SOC curve battery volt 2 | 0.1 V | 400–600 |
| 173 | SOC curve SOC 1 | % | 0–100 |
| 174 | SOC curve SOC 2 | % | 0–100 |
| 175 | Battery inner resistance | µΩ | 0–100 |

### Generator / function enable 4 (176–179)

| Reg | Name | Unit |
|---|---|---|
| 176 | Max grid import power | 0.1 kW |
| 177 | Generator rated power | W |
| 179 | Function enable 4 | bitfield (see below) |

**Function enable 4 (179) bits:** ACCT direction, PVCT direction, AFCI alarm clear,
battery wakeup/PV-sell-first, VoltWatt enable, trip time unit, active power command,
grid peak shaving, generator peak shaving, battery charge control (SOC/volt),
battery discharge control (SOC/volt), AC coupling, PV arc enable, smart load,
RSD disable, on-grid always-on.

### AFCI / VoltWatt / QV (180–193)

| Reg | Name | Unit | Range |
|---|---|---|---|
| 180 | AFCI arc threshold | A | 0–65535 |
| 181 | VoltWatt V1 | 0.1 V | default 1.06 Vn |
| 182 | VoltWatt V2 | 0.1 V | default 1.1 Vn |
| 183 | VoltWatt delay | ms | 500–60000 |
| 184 | VoltWatt P2 | % | |
| 185 | Vref QV | 0.1 V | |
| 186 | Vref filter time | s | |
| 187 | Q3 QV | % | |
| 188 | Q4 QV | % | |
| 189–192 | P1–P4 QP curve | % | |
| 193 | Underfrequency ramp rate | %Pm/Hz | 1–100 |

### Generator charge (194–198)

| Reg | Name | Unit | Range |
|---|---|---|---|
| 194 | Generator charge start voltage | 0.1 V | 384–520 |
| 195 | Generator charge end voltage | 0.1 V | 480–590 |
| 196 | Generator charge start SOC | % | 0–90 |
| 197 | Generator charge end SOC | % | 20–100 |
| 198 | Max generator charge current | A | 0–4000 |

### Advanced (199–261)

| Reg | Name | Unit | Range |
|---|---|---|---|
| 199 | Overtemperature derate point | 0.1 °C | 600–900 |
| 201 | Charge priority end voltage | 0.1 V | 480–590 |
| 202 | Forced discharge end voltage | 0.1 V | 400–560 |
| 203 | Grid regulation | bitfield | |
| 204 | Lead-acid capacity | Ah | 50–5000 |
| 205 | Grid type | enum | split/3-phase |
| 206 | Grid peak shaving power | W | 0–65535 |
| 207 | Grid peak shaving SOC | % | 0–100 |
| 208 | Grid peak shaving voltage | 0.1 V | 480–590 |
| 209–212 | Peak shaving time slots ×2 | HH:MM | |
| 213 | Smart load on voltage | 0.1 V | 480–590 |
| 214 | Smart load off voltage | 0.1 V | 400–520 |
| 215 | Smart load on SOC | % | 0–100 |
| 216 | Smart load off SOC | % | 0–100 |
| 217 | Start PV power | 0.1 kW | 0–120 |
| 218 | Grid peak shaving SOC 1 | % | 0–100 |
| 219 | Grid peak shaving volt 1 | 0.1 V | 480–590 |
| 220 | AC couple start SOC | % | 0–100 |
| 221 | AC couple end SOC | % | 0–255 |
| 222 | AC couple start volt | 0.1 V | 400–595 |
| 223 | AC couple end volt | 0.1 V | 420–800 |
| 224 | LCD config | — | |
| 225 | LCD password | — | 0–65535 |
| 227 | Battery stop charge SOC | % | 10–101 |
| 228 | Battery stop charge voltage | 0.1 V | 400–595 |
| 230 | Meter config | — | |
| 231 | Reset record | bit 0 = reset G100 lockout | |
| 232 | Grid peak shaving power 1 | W | 0–65535 |
| 233 | Function enable 5 | bitfield (see below) | |
| 234 | Quick charge time | min | 0–1440 |
| 235 | No-full-charge day config | — | |
| 236 | Float charge threshold | 0.01 C | 1–255 |
| 237 | Generator cool-down time | 0.1 min | 1–255 |
| 241 | Service mode enable | — | 0–65535 |
| 242 | NPE threshold | 0.1 V | 0–65535 |
| 244 | Bootloader version + update flag | — | |
| 245 | Flash size | — | |
| 248–250 | WattNode CT amps phase 1–3 | A | 0–65535 |
| 251 | WattNode CT directions + frequency | bitfield | |
| 252 | NEC 120% bus bar limit | W | 0–65535 |
| 253 | SOC delta/hysteresis | % | 0–100 |
| 254 | Voltage delta/hysteresis | 0.1 V | 0–100 |
| 256–259 | Generator time slots ×2 | HH:MM | |
| 260 | Bus voltage high limit | 0.1 V | 0–8000 |
| 261 | Discharge recovery | % | 0–100 |

**Function enable 5 (233) bits:** quick charge start, battery backup, maintenance,
7-day work mode, dry contactor multiplex, external CT position, overfrequency f-stop.

---

## 7-Day Scheduling (Hold Registers 500–723)

Advanced per-day scheduling for AC charge, forced charge, forced discharge, and
peak shaving. **Prerequisite:** bit 3 of hold register 233 must be enabled.

Each day uses 8 registers (2 time periods × 4 registers each). Each period:
`Power+SOC`, `Voltage`, `StartHour+StartMin`, `EndHour+EndMin`.

| Module | Register range |
|---|---|
| AC charge | 500–555 |
| Forced charge | 556–611 |
| Forced discharge | 612–667 |
| Peak shaving | 668–723 |

Day offsets (8 registers/day): Monday=0, Tuesday=8, Wednesday=16, Thursday=24,
Friday=32, Saturday=40, Sunday=48.

---

## Battery BMS Registers (5000+)

Per-battery BMS data. Each battery occupies 30 registers starting at 5000.
Offsets within each 30-register block:

| Offset | Name |
|---|---|
| 3 | Capacity |
| 5 | Max charge current |
| 6 | Max discharge current |
| 8 | Voltage |
| 9 | Current |
| 10 | SOH + SOC |
| 11 | Cycle count |
| 12 | Max cell temperature |
| 13 | Min cell temperature |
| 14 | Max cell voltage |
| 15 | Min cell voltage |
| 16 | Cell temperatures |
| 17 | Cell voltages |
| 18 | Firmware |
| 19–32 | Serial number (14 chars) |

---

## Fault Codes

| Code | Meaning |
|---|---|
| 0 | Internal communication failure 1 |
| 1 | Model fault |
| 8 | Parallel CAN communication failure |
| 9 | The host is missing |
| 10 | Inconsistent rated power |
| 11 | Inconsistent AC or safety settings |
| 12 | UPS short circuit |
| 13 | UPS reverse current |
| 14 | BUS short circuit |
| 15 | Abnormal phase in three-phase system |
| 16 | Relay failure |
| 17 | Internal communication failure 2 |
| 18 | Internal communication failure 3 |
| 19 | BUS overvoltage |
| 20 | EPS connection fault |
| 21 | PV overvoltage |
| 22 | Overcurrent protection |
| 23 | Neutral fault |
| 24 | PV short circuit |
| 25 | Heatsink temperature out of range |
| 26 | Internal failure |
| 27 | Consistency failure |
| 28 | Inconsistent generator connection |
| 29 | Parallel sync signal loss |
| 31 | Internal communication failure 4 |

## Warning Codes

| Code | Meaning |
|---|---|
| 0 | Battery communication failed |
| 1 | AFCI communication failure |
| 2 | Battery low temperature |
| 3 | Meter communication failed |
| 4 | Battery cannot be charged/discharged |
| 5 | Automated test failed |
| 6 | RSD active |
| 7 | LCD communication failure |
| 8 | Software version mismatch |
| 9 | Fan is stuck |
| 10 | Grid overload |
| 11 | Parallel secondaries exceed limit |
| 12 | Battery reverse MOS abnormal |
| 13 | Radiator temperature out of range |
| 14 | Multiple primary units in parallel |
| 15 | Battery reverse |
| 16 | No grid connection |
| 17 | Grid voltage out of range |
| 18 | Grid frequency out of range |
| 20 | Insulation resistance low |
| 21 | Leakage current too high |
| 22 | DCI exceeded standard |
| 23 | PV short circuit |
| 25 | Battery overvoltage |
| 26 | Battery undervoltage |
| 27 | Battery open circuit |
| 28 | EPS overload |
| 29 | EPS voltage high |
| 30 | Meter reversed |
| 31 | DCV exceeded standard |

---

## Known divergences / unresolved (from the HA integration docs)

These are flagged by the HA integration as needing hardware validation. Do **not**
change these on inference alone — they are safety thresholds.

| Reg | Issue |
|---|---|
| 161 | AC charge end SOC: description allows 0, doc says 20 |
| 165 | Battery low SOC recovery: floor lowered to 0 (hardware reports 0 when unconfigured) |
| 106 | Lead-acid discharge temp low: signed (65336 → −20.0 °C) |
| 201 | Charge first end voltage: max raised to 59.5 (hardware reads 595) |
| 75 | Charge first SOC limit: max raised to 101 (hardware reads 101) |
| 252 | NEC 120% bus bar limit: unit/range disagree (W vs A) |
| 260 | Bus overvoltage alarm point: probable 10× scale error |
| 261 | Discharge recovery: one of two entities may not exist |

---

## Rebrand / OEM landscape

LuxPower is the OEM. The following brands sell LuxPower-rebranded inverters that
share this exact register map:

| Brand | Model(s) | LuxPower equivalent |
|---|---|---|
| **EG4** (Signature Solar) | 6000XP, 6500EX, 3000EHV, 12000XP, 12kPV, 18KPV | SNA-US 6000, SNA family, 12K, 18K |
| **LuxPower** (original) | SNA-US 6000, LXP 6K/12K/18K | — |
| **Fortress Power** | Envy True 12K (formerly "Envy 12kW") | LXP 12K |
| **BigBattery** | SNA-US 6K (sold as-is) | SNA-US 6000 |

**Not LuxPower rebrands** (different OEM, different register map — do NOT alias):
- Growatt (SPF series)
- Sol-Ark (Deye-based)
- Deye / SunSynk
- Solis, Sungrow, GoodWe, Huawei, Voltronic/Axpert

> Note: EG4's newer **FlexBOSS / GridBOSS** platform is *not* a LuxPower SNA
> rebadge — it has an unknown register map and must not be aliased to either
> the SNA or 18KPV family.
