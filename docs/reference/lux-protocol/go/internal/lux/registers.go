package lux

import (
	"fmt"
	"strings"
)

// Register 21 Master Function bitmask constants.
// Use read-modify-write pattern: fetch current value, flip bits, write back.
const (
	MasterEPSEnable        = 1 << 0  // EPS (Emergency Power Supply)
	MasterOverloadDerate   = 1 << 1  // Overload derate
	MasterDRMS             = 1 << 2  // DRMS
	MasterLVRT             = 1 << 3  // Low voltage ride-through
	MasterAntiIsland       = 1 << 4  // Anti-islanding
	MasterNeutralDetect    = 1 << 5  // Neutral detection
	MasterGridOnPowerSS    = 1 << 6  // Grid-on power soft start
	MasterACChargeEnable   = 1 << 7  // AC (grid) charging enable
	MasterSeamlessEPS      = 1 << 8  // Seamless EPS switching
	MasterStandby          = 1 << 9  // Standby mode
	MasterForcedDischarge  = 1 << 10 // Forced discharge enable
	MasterChargePriority   = 1 << 11 // Charge priority enable
	MasterISO              = 1 << 12 // Isolation detection
	MasterGFCI             = 1 << 13 // Ground fault circuit interrupter
	MasterDCI              = 1 << 14 // DC injection detection
	MasterFeedInGrid       = 1 << 15 // Feed-in to grid enable
)

// RegisterDef defines a register with its human-readable name, scaling divisor, and unit.
type RegisterDef struct {
	Name        string            // Human-readable name
	Description string            // Description of what this register is for
	Divisor     float64           // Divisor for scaling (0 = no scaling, raw integer)
	Unit        string            // Unit string (V, W, Hz, %, etc.)
	Special     string            // Special decoding: "soc_soh", "time", "clock_my", "clock_hd", "clock_sm", "bitmask", "lo", "hi", "enum", "ident", "status"
	Enum        map[uint16]string // Enum value mappings (for Special="enum")
	Bits        map[int]string    // Bit definitions (for Special="bitmask")
	Min         float64           // Minimum expected value (scaled), 0 if no min
	Max         float64           // Maximum expected value (scaled), 0 if no max
	HasRange    bool              // True if Min/Max are meaningful
}

// Validate checks if the raw value is within expected range.
// Returns empty string if OK, warning message if anomalous.
func (r RegisterDef) Validate(raw uint16) string {
	if !r.HasRange {
		return ""
	}
	scaled := r.ScaledValue(raw)
	if r.Min != 0 && scaled < r.Min {
		return fmt.Sprintf("below min (%.1f < %.1f)", scaled, r.Min)
	}
	if r.Max != 0 && scaled > r.Max {
		return fmt.Sprintf("above max (%.1f > %.1f)", scaled, r.Max)
	}
	return ""
}

// FormatValue returns the scaled value with unit as a string.
func (r RegisterDef) FormatValue(raw uint16) string {
	if r.Special == "soc_soh" {
		soc := raw & 0xFF
		soh := (raw >> 8) & 0xFF
		return fmt.Sprintf("SOC=%d%% SOH=%d%%", soc, soh)
	}
	if r.Special == "time" {
		hour := raw & 0xFF
		min := raw >> 8
		return fmt.Sprintf("%02d:%02d", hour, min)
	}
	if r.Special == "clock_my" {
		month := raw >> 8
		year := raw & 0xFF
		return fmt.Sprintf("%04d-%02d", 2000+int(year), month)
	}
	if r.Special == "clock_hd" {
		hour := raw >> 8
		day := raw & 0xFF
		return fmt.Sprintf("day %02d @ %02d:xx", day, hour)
	}
	if r.Special == "clock_sm" {
		sec := raw >> 8
		min := raw & 0xFF
		return fmt.Sprintf("xx:%02d:%02d", min, sec)
	}
	if r.Special == "bitmask" {
		return r.formatBitmask(raw)
	}
	if r.Special == "enum" {
		if name, ok := r.Enum[raw]; ok {
			return name
		}
		return fmt.Sprintf("Unknown(%d)", raw)
	}
	if r.Special == "ident" {
		return fmt.Sprintf("%d", raw)
	}
	if r.Special == "status" {
		return r.formatStatus(raw)
	}
	if r.Divisor > 0 {
		scaled := float64(raw) / r.Divisor
		if r.Unit != "" {
			return fmt.Sprintf("%.2f %s", scaled, r.Unit)
		}
		return fmt.Sprintf("%.2f", scaled)
	}
	if r.Unit != "" {
		return fmt.Sprintf("%d %s", raw, r.Unit)
	}
	return fmt.Sprintf("%d", raw)
}

// formatBitmask returns a human-readable string of set flags.
func (r RegisterDef) formatBitmask(raw uint16) string {
	if r.Bits == nil {
		return fmt.Sprintf("0x%04X", raw)
	}
	var flags []string
	for bit := 0; bit < 16; bit++ {
		if raw&(1<<bit) != 0 {
			if name, ok := r.Bits[bit]; ok {
				flags = append(flags, name)
			} else {
				flags = append(flags, fmt.Sprintf("bit%d", bit))
			}
		}
	}
	if len(flags) == 0 {
		return "(none)"
	}
	return fmt.Sprintf("%s", strings.Join(flags, ", "))
}

// formatStatus returns a human-readable inverter status string.
func (r RegisterDef) formatStatus(raw uint16) string {
	if name, ok := StatusCodes[raw]; ok {
		return name
	}
	return fmt.Sprintf("Unknown(0x%02X)", raw)
}

// ScaledValue returns the numeric scaled value.
func (r RegisterDef) ScaledValue(raw uint16) float64 {
	if r.Divisor > 0 {
		return float64(raw) / r.Divisor
	}
	return float64(raw)
}

// FormatValueOnly returns the scaled value as a string without the unit.
func (r RegisterDef) FormatValueOnly(raw uint16) string {
	if r.Special == "soc_soh" {
		soc := raw & 0xFF
		soh := (raw >> 8) & 0xFF
		return fmt.Sprintf("SOC=%d%% SOH=%d%%", soc, soh)
	}
	if r.Special == "time" {
		hour := raw & 0xFF
		min := raw >> 8
		return fmt.Sprintf("%02d:%02d", hour, min)
	}
	if r.Special == "clock_my" {
		month := raw >> 8
		year := raw & 0xFF
		return fmt.Sprintf("%04d-%02d", 2000+int(year), month)
	}
	if r.Special == "clock_hd" {
		hour := raw >> 8
		day := raw & 0xFF
		return fmt.Sprintf("day %02d @ %02d:xx", day, hour)
	}
	if r.Special == "clock_sm" {
		sec := raw >> 8
		min := raw & 0xFF
		return fmt.Sprintf("xx:%02d:%02d", min, sec)
	}
	if r.Special == "bitmask" {
		return r.formatBitmask(raw)
	}
	if r.Special == "enum" {
		if name, ok := r.Enum[raw]; ok {
			return name
		}
		return fmt.Sprintf("Unknown(%d)", raw)
	}
	if r.Special == "ident" {
		return fmt.Sprintf("%d", raw)
	}
	if r.Special == "status" {
		return r.formatStatus(raw)
	}
	if r.Divisor > 0 {
		return fmt.Sprintf("%.2f", float64(raw)/r.Divisor)
	}
	return fmt.Sprintf("%d", raw)
}

// StatusCodes maps inverter status register values to descriptions.
var StatusCodes = map[uint16]string{
	0x00: "Standby",
	0x04: "PV On-grid",
	0x08: "PV Charge",
	0x0C: "PV Charge On-grid",
	0x10: "Battery On-grid",
	0x11: "Bypass",
	0x14: "PV & Battery On-grid",
	0x20: "AC Charge",
	0x28: "PV & AC Charge",
	0x40: "Battery Off-grid",
	0x80: "PV Off-grid",
	0xC0: "PV & Battery Off-grid",
}

// MasterFunctionBits defines the bit names for register 21.
var MasterFunctionBits = map[int]string{
	0:  "EPS",
	1:  "OverloadDerate",
	2:  "DRMS",
	3:  "LVRT",
	4:  "AntiIsland",
	5:  "NeutralDetect",
	6:  "GridOnPowerSS",
	7:  "ACCharge",
	8:  "SeamlessEPS",
	9:  "Standby",
	10: "ForcedDischarge",
	11: "ChargePriority",
	12: "ISO",
	13: "GFCI",
	14: "DCI",
	15: "FeedInGrid",
}

// SecondaryFunctionBits defines the bit names for register 110.
var SecondaryFunctionBits = map[int]string{
	1: "FastZeroExport",
	2: "MicroGrid",
}

// PVInputModes maps PV input mode values to descriptions.
var PVInputModes = map[uint16]string{
	0: "Independent",
	1: "Parallel",
}

// LanguageCodes maps language register values to names.
var LanguageCodes = map[uint16]string{
	0: "English",
	1: "Chinese",
}

// InputRegisters maps input register numbers to their definitions.
// Based on lxp-bridge, Python decoder, and REGISTERS.md.
// InputRegisters maps input register numbers to their definitions.
var InputRegisters = map[uint16]RegisterDef{
	// Status and PV voltages
	0: {"Status", "Current inverter operating mode", 0, "", "status", nil, nil, 0, 0, false},
	1: {"PV1 Voltage", "Solar string 1 voltage", 10, "V", "", nil, nil, 0, 550, true},
	2: {"PV2 Voltage", "Solar string 2 voltage", 10, "V", "", nil, nil, 0, 550, true},
	3: {"PV3 Voltage", "Solar string 3 voltage", 10, "V", "", nil, nil, 0, 550, true},

	// Battery (48V nominal = 40-58V typical range)
	4: {"Battery Voltage", "Battery bank voltage", 10, "V", "", nil, nil, 40, 60, true},
	5: {"SOC/SOH", "State of charge (lo) and health (hi)", 0, "", "soc_soh", nil, nil, 0, 0, false},
	6: {"Internal Fault", "Internal fault code bitmask", 0, "", "bitmask", nil, nil, 0, 0, false},

	// PV power (18kW max PV input)
	7: {"PV1 Power", "Solar string 1 power output", 0, "W", "", nil, nil, 0, 8000, true},
	8: {"PV2 Power", "Solar string 2 power output", 0, "W", "", nil, nil, 0, 8000, true},
	9: {"PV3 Power", "Solar string 3 power output", 0, "W", "", nil, nil, 0, 8000, true},

	// Battery power (max ~15kW charge/discharge)
	10: {"Battery Charge Power", "Power flowing into battery", 0, "W", "", nil, nil, 0, 15000, true},
	11: {"Battery Discharge Power", "Power flowing out of battery", 0, "W", "", nil, nil, 0, 15000, true},

	// Grid AC - ranges depend on system config (120V/240V, 50Hz/60Hz)
	12: {"Grid Voltage", "AC grid voltage (phase R or single-phase)", 10, "V", "", nil, nil, 0, 0, false},
	13: {"Grid Voltage S", "AC grid voltage phase S (3-phase only)", 10, "V", "", nil, nil, 0, 0, false},
	14: {"Grid Voltage T", "AC grid voltage phase T (3-phase only)", 10, "V", "", nil, nil, 0, 0, false},
	15: {"Grid Frequency", "AC grid frequency", 100, "Hz", "", nil, nil, 0, 0, false},

	// Inverter (18kW rated)
	16: {"Inverter Power", "Total inverter output power", 0, "W", "", nil, nil, 0, 20000, true},
	17: {"Rectifier Power", "Power being rectified from AC", 0, "W", "", nil, nil, 0, 20000, true},
	18: {"Inverter RMS Current", "Inverter output current", 0, "A", "", nil, nil, 0, 0, false},
	19: {"Power Factor", "Power factor (1.0 = unity)", 1000, "", "", nil, nil, 0, 0, false},

	// EPS (backup) - ranges depend on system config
	20: {"EPS Voltage R", "Emergency power voltage phase R", 10, "V", "", nil, nil, 0, 0, false},
	21: {"EPS Voltage S", "Emergency power voltage phase S (3-phase)", 10, "V", "", nil, nil, 0, 0, false},
	22: {"EPS Voltage T", "Emergency power voltage phase T (3-phase)", 10, "V", "", nil, nil, 0, 0, false},
	23: {"EPS Frequency", "Emergency power frequency", 100, "Hz", "", nil, nil, 0, 0, false},
	24: {"EPS Active Power", "Emergency power active load", 0, "W", "", nil, nil, 0, 18000, true},
	25: {"EPS Apparent Power", "Emergency power apparent load", 0, "VA", "", nil, nil, 0, 18000, true},

	// Grid power
	26: {"Power To Grid", "Power being exported to grid", 0, "W", "", nil, nil, 0, 18000, true},
	27: {"Power From Grid", "Power being imported from grid", 0, "W", "", nil, nil, 0, 18000, true},

	// Daily energy (reasonable daily max ~100kWh)
	28: {"PV1 Energy Today", "Solar string 1 generation today", 10, "kWh", "", nil, nil, 0, 50, true},
	29: {"PV2 Energy Today", "Solar string 2 generation today", 10, "kWh", "", nil, nil, 0, 50, true},
	30: {"PV3 Energy Today", "Solar string 3 generation today", 10, "kWh", "", nil, nil, 0, 50, true},
	31: {"Inverter Energy Today", "Total inverter output today", 10, "kWh", "", nil, nil, 0, 150, true},
	32: {"Rectifier Energy Today", "Energy rectified from AC today", 10, "kWh", "", nil, nil, 0, 100, true},
	33: {"Battery Charge Today", "Energy charged to battery today", 10, "kWh", "", nil, nil, 0, 100, true},
	34: {"Battery Discharge Today", "Energy discharged from battery today", 10, "kWh", "", nil, nil, 0, 100, true},
	35: {"EPS Energy Today", "Emergency power consumption today", 10, "kWh", "", nil, nil, 0, 100, true},
	36: {"Grid Export Today", "Energy exported to grid today", 10, "kWh", "", nil, nil, 0, 150, true},
	37: {"Grid Import Today", "Energy imported from grid today", 10, "kWh", "", nil, nil, 0, 150, true},

	// DC bus (typically 350-450V)
	38: {"DC Bus 1 Voltage", "Internal DC bus 1 voltage", 10, "V", "", nil, nil, 300, 500, true},
	39: {"DC Bus 2 Voltage", "Internal DC bus 2 voltage", 10, "V", "", nil, nil, 300, 500, true},

	// All-time energy (u32 pairs) - no range check on raw halves
	40: {"PV1 Energy Total (lo)", "Lifetime PV1 energy, low word", 0, "", "lo", nil, nil, 0, 0, false},
	41: {"PV1 Energy Total (hi)", "Lifetime PV1 energy, high word", 0, "", "hi", nil, nil, 0, 0, false},
	42: {"PV2 Energy Total (lo)", "Lifetime PV2 energy, low word", 0, "", "lo", nil, nil, 0, 0, false},
	43: {"PV2 Energy Total (hi)", "Lifetime PV2 energy, high word", 0, "", "hi", nil, nil, 0, 0, false},
	44: {"PV3 Energy Total (lo)", "Lifetime PV3 energy, low word", 0, "", "lo", nil, nil, 0, 0, false},
	45: {"PV3 Energy Total (hi)", "Lifetime PV3 energy, high word", 0, "", "hi", nil, nil, 0, 0, false},
	46: {"Inverter Energy Total (lo)", "Lifetime inverter output, low word", 0, "", "lo", nil, nil, 0, 0, false},
	47: {"Inverter Energy Total (hi)", "Lifetime inverter output, high word", 0, "", "hi", nil, nil, 0, 0, false},
	48: {"Rectifier Energy Total (lo)", "Lifetime rectified energy, low word", 0, "", "lo", nil, nil, 0, 0, false},
	49: {"Rectifier Energy Total (hi)", "Lifetime rectified energy, high word", 0, "", "hi", nil, nil, 0, 0, false},
	50: {"Battery Charge Total (lo)", "Lifetime battery charge, low word", 0, "", "lo", nil, nil, 0, 0, false},
	51: {"Battery Charge Total (hi)", "Lifetime battery charge, high word", 0, "", "hi", nil, nil, 0, 0, false},
	52: {"Battery Discharge Total (lo)", "Lifetime battery discharge, low word", 0, "", "lo", nil, nil, 0, 0, false},
	53: {"Battery Discharge Total (hi)", "Lifetime battery discharge, high word", 0, "", "hi", nil, nil, 0, 0, false},
	54: {"EPS Energy Total (lo)", "Lifetime EPS consumption, low word", 0, "", "lo", nil, nil, 0, 0, false},
	55: {"EPS Energy Total (hi)", "Lifetime EPS consumption, high word", 0, "", "hi", nil, nil, 0, 0, false},
	56: {"Grid Export Total (lo)", "Lifetime grid export, low word", 0, "", "lo", nil, nil, 0, 0, false},
	57: {"Grid Export Total (hi)", "Lifetime grid export, high word", 0, "", "hi", nil, nil, 0, 0, false},
	58: {"Grid Import Total (lo)", "Lifetime grid import, low word", 0, "", "lo", nil, nil, 0, 0, false},
	59: {"Grid Import Total (hi)", "Lifetime grid import, high word", 0, "", "hi", nil, nil, 0, 0, false},

	// Fault/warning - 0 is normal
	60: {"Fault Code (lo)", "Active fault code, low word", 0, "", "lo", nil, nil, 0, 0, false},
	61: {"Fault Code (hi)", "Active fault code, high word", 0, "", "hi", nil, nil, 0, 0, false},
	62: {"Warning Code (lo)", "Active warning code, low word", 0, "", "lo", nil, nil, 0, 0, false},
	63: {"Warning Code (hi)", "Active warning code, high word", 0, "", "hi", nil, nil, 0, 0, false},

	// Temperature (-10 to 70°C normal operating range)
	64: {"Internal Temp", "Inverter internal temperature", 0, "°C", "", nil, nil, -10, 70, true},
	65: {"Radiator 1 Temp", "Heat sink 1 temperature", 0, "°C", "", nil, nil, -10, 85, true},
	66: {"Radiator 2 Temp", "Heat sink 2 temperature", 0, "°C", "", nil, nil, -10, 85, true},
	67: {"Battery Temp Raw", "Battery temperature (raw, encoding varies)", 0, "°C", "", nil, nil, 0, 0, false},
	68: {"Battery Temp Scaled", "Battery temperature (÷10 scaling)", 10, "°C", "", nil, nil, -10, 50, true},

	// Runtime
	69: {"Runtime (lo)", "Total runtime seconds, low word", 0, "s", "lo", nil, nil, 0, 0, false},
	70: {"Runtime (hi)", "Total runtime seconds, high word", 0, "s", "hi", nil, nil, 0, 0, false},

	// BMS data - ranges depend on battery system config
	89:  {"Max Charge Current", "BMS max allowed charge current", 10, "A", "", nil, nil, 0, 0, false},
	90:  {"Max Discharge Current", "BMS max allowed discharge current", 10, "A", "", nil, nil, 0, 0, false},
	91:  {"Charge Voltage Ref", "BMS charge voltage reference", 10, "V", "", nil, nil, 0, 0, false},
	92:  {"Discharge Cutoff Voltage", "BMS discharge cutoff voltage", 10, "V", "", nil, nil, 0, 0, false},
	93:  {"Battery Status 0", "Battery pack 0 status flags", 0, "", "bitmask", nil, nil, 0, 0, false},
	94:  {"Battery Status 1", "Battery pack 1 status flags", 0, "", "bitmask", nil, nil, 0, 0, false},
	95:  {"Battery Status 2", "Battery pack 2 status flags", 0, "", "bitmask", nil, nil, 0, 0, false},
	96:  {"Battery Status 3", "Battery pack 3 status flags", 0, "", "bitmask", nil, nil, 0, 0, false},
	97:  {"Battery Status 4", "Battery pack 4 status flags", 0, "", "bitmask", nil, nil, 0, 0, false},
	98:  {"Battery Status 5", "Battery pack 5 status flags", 0, "", "bitmask", nil, nil, 0, 0, false},
	99:  {"Battery Status 6", "Battery pack 6 status flags", 0, "", "bitmask", nil, nil, 0, 0, false},
	100: {"Battery Status 7", "Battery pack 7 status flags", 0, "", "bitmask", nil, nil, 0, 0, false},
	101: {"Battery Status 8", "Battery pack 8 status flags", 0, "", "bitmask", nil, nil, 0, 0, false},
	102: {"Battery Status 9", "Battery pack 9 status flags", 0, "", "bitmask", nil, nil, 0, 0, false},
	103: {"Battery Status Inv", "Inverter-side battery status", 0, "", "bitmask", nil, nil, 0, 0, false},
	104: {"Battery Count", "Number of battery packs detected", 0, "", "", nil, nil, 0, 0, false},
	105: {"Battery Capacity", "Total battery capacity", 0, "Ah", "", nil, nil, 0, 0, false},
	106: {"Battery Current", "Battery current (+ charge, - discharge)", 100, "A", "", nil, nil, 0, 0, false},
	107: {"BMS Event 1", "BMS event flags register 1", 0, "", "bitmask", nil, nil, 0, 0, false},
	108: {"BMS Event 2", "BMS event flags register 2", 0, "", "bitmask", nil, nil, 0, 0, false},
	109: {"Max Cell Voltage", "Highest cell voltage in pack", 1000, "V", "", nil, nil, 0, 0, false},
	110: {"Min Cell Voltage", "Lowest cell voltage in pack", 1000, "V", "", nil, nil, 0, 0, false},
	111: {"Max Cell Temp", "Highest cell temperature", 10, "°C", "", nil, nil, 0, 0, false},
	112: {"Min Cell Temp", "Lowest cell temperature", 10, "°C", "", nil, nil, 0, 0, false},
	114: {"Cycle Count", "Battery charge/discharge cycles", 0, "cycles", "", nil, nil, 0, 10000, true},
	115: {"BMS Data 115", "Model-specific BMS data", 0, "", "ident", nil, nil, 0, 0, false},
}

// HoldingRegisters maps holding register numbers to their definitions.
// Based on REGISTERS.md and lxp-bridge sources.
// HoldingRegisters maps holding register numbers to their definitions.
var HoldingRegisters = map[uint16]RegisterDef{
	// System config - identifiers have no range
	0:  {"Model (lo)", "Inverter model identifier, low word", 0, "", "ident", nil, nil, 0, 0, false},
	1:  {"Model (hi)", "Inverter model identifier, high word", 0, "", "ident", nil, nil, 0, 0, false},
	2:  {"Serial 1", "Serial number chars 1-2", 0, "", "ident", nil, nil, 0, 0, false},
	3:  {"Serial 2", "Serial number chars 3-4", 0, "", "ident", nil, nil, 0, 0, false},
	4:  {"Serial 3", "Serial number chars 5-6", 0, "", "ident", nil, nil, 0, 0, false},
	5:  {"Serial 4", "Serial number chars 7-8", 0, "", "ident", nil, nil, 0, 0, false},
	6:  {"Serial 5", "Serial number chars 9-10", 0, "", "ident", nil, nil, 0, 0, false},
	7:  {"Firmware Code", "Firmware version identifier", 0, "", "ident", nil, nil, 0, 0, false},

	// Time registers - no range validation
	12: {"Time Month/Year", "Clock: month (hi byte) / year-2000 (lo byte)", 0, "", "clock_my", nil, nil, 0, 0, false},
	13: {"Time Hour/Day", "Clock: hour (hi byte) / day (lo byte)", 0, "", "clock_hd", nil, nil, 0, 0, false},
	14: {"Time Sec/Min", "Clock: second (hi byte) / minute (lo byte)", 0, "", "clock_sm", nil, nil, 0, 0, false},

	15: {"Communication Address", "Modbus communication address", 0, "", "ident", nil, nil, 1, 247, true},
	16: {"Language", "Display language setting", 0, "", "enum", LanguageCodes, nil, 0, 0, false},
	19: {"Device Type", "Device type identifier", 0, "", "ident", nil, nil, 0, 0, false},
	20: {"PV Input Mode", "PV string configuration mode", 0, "", "enum", PVInputModes, nil, 0, 0, false},

	// Master Function Bitmask
	21: {"Master Function Flags", "Primary mode control flags (EPS, AC charge, discharge, etc.)", 0, "", "bitmask", nil, MasterFunctionBits, 0, 0, false},

	22: {"PV Start Voltage", "Min PV voltage to start inverter", 10, "V", "", nil, nil, 100, 200, true},
	23: {"Grid Connect Time", "Delay before grid connection", 0, "s", "", nil, nil, 0, 600, true},
	24: {"Grid Reconnect Time", "Delay before grid reconnection", 0, "s", "", nil, nil, 0, 600, true},

	// Grid protection registers (25-53) - utility/region dependent, no validation
	25: {"Grid V High Limit 1", "Grid over-voltage trip level 1 (DO NOT MODIFY)", 10, "V", "", nil, nil, 0, 0, false},
	26: {"Grid V High Limit 1 Time", "Grid OV1 trip time (DO NOT MODIFY)", 0, "ms", "", nil, nil, 0, 0, false},
	27: {"Grid V High Limit 2", "Grid over-voltage trip level 2 (DO NOT MODIFY)", 10, "V", "", nil, nil, 0, 0, false},
	28: {"Grid V High Limit 2 Time", "Grid OV2 trip time (DO NOT MODIFY)", 0, "ms", "", nil, nil, 0, 0, false},
	29: {"Grid V High Limit 3", "Grid over-voltage trip level 3 (DO NOT MODIFY)", 10, "V", "", nil, nil, 0, 0, false},
	30: {"Grid V High Limit 3 Time", "Grid OV3 trip time (DO NOT MODIFY)", 0, "ms", "", nil, nil, 0, 0, false},
	31: {"Grid V Low Limit 1", "Grid under-voltage trip level 1 (DO NOT MODIFY)", 10, "V", "", nil, nil, 0, 0, false},
	32: {"Grid V Low Limit 1 Time", "Grid UV1 trip time (DO NOT MODIFY)", 0, "ms", "", nil, nil, 0, 0, false},
	33: {"Grid V Low Limit 2", "Grid under-voltage trip level 2 (DO NOT MODIFY)", 10, "V", "", nil, nil, 0, 0, false},
	34: {"Grid V Low Limit 2 Time", "Grid UV2 trip time (DO NOT MODIFY)", 0, "ms", "", nil, nil, 0, 0, false},
	35: {"Grid V Low Limit 3", "Grid under-voltage trip level 3 (DO NOT MODIFY)", 10, "V", "", nil, nil, 0, 0, false},
	36: {"Grid V Low Limit 3 Time", "Grid UV3 trip time (DO NOT MODIFY)", 0, "ms", "", nil, nil, 0, 0, false},
	37: {"Grid V Moving Avg High", "Grid voltage moving avg high limit (DO NOT MODIFY)", 10, "V", "", nil, nil, 0, 0, false},
	38: {"Grid F High Limit 1", "Grid over-frequency trip level 1 (DO NOT MODIFY)", 100, "Hz", "", nil, nil, 0, 0, false},
	39: {"Grid F High Limit 1 Time", "Grid OF1 trip time (DO NOT MODIFY)", 0, "ms", "", nil, nil, 0, 0, false},
	40: {"Grid F High Limit 2", "Grid over-frequency trip level 2 (DO NOT MODIFY)", 100, "Hz", "", nil, nil, 0, 0, false},
	41: {"Grid F High Limit 2 Time", "Grid OF2 trip time (DO NOT MODIFY)", 0, "ms", "", nil, nil, 0, 0, false},
	42: {"Grid F High Limit 3", "Grid over-frequency trip level 3 (DO NOT MODIFY)", 100, "Hz", "", nil, nil, 0, 0, false},
	43: {"Grid F High Limit 3 Time", "Grid OF3 trip time (DO NOT MODIFY)", 0, "ms", "", nil, nil, 0, 0, false},
	44: {"Grid F Low Limit 1", "Grid under-frequency trip level 1 (DO NOT MODIFY)", 100, "Hz", "", nil, nil, 0, 0, false},
	45: {"Grid F Low Limit 1 Time", "Grid UF1 trip time (DO NOT MODIFY)", 0, "ms", "", nil, nil, 0, 0, false},
	46: {"Grid F Low Limit 2", "Grid under-frequency trip level 2 (DO NOT MODIFY)", 100, "Hz", "", nil, nil, 0, 0, false},
	47: {"Grid F Low Limit 2 Time", "Grid UF2 trip time (DO NOT MODIFY)", 0, "ms", "", nil, nil, 0, 0, false},
	48: {"Grid F Low Limit 3", "Grid under-frequency trip level 3 (DO NOT MODIFY)", 100, "Hz", "", nil, nil, 0, 0, false},
	49: {"Grid F Low Limit 3 Time", "Grid UF3 trip time (DO NOT MODIFY)", 0, "ms", "", nil, nil, 0, 0, false},
	50: {"Grid F Moving Avg High", "Grid freq moving avg high (DO NOT MODIFY)", 100, "Hz", "", nil, nil, 0, 0, false},
	51: {"Grid F Moving Avg Low", "Grid freq moving avg low (DO NOT MODIFY)", 100, "Hz", "", nil, nil, 0, 0, false},
	52: {"Grid F Rate Limit", "Grid freq rate of change limit (DO NOT MODIFY)", 100, "Hz/s", "", nil, nil, 0, 0, false},
	53: {"Grid Reconnect F High", "Grid reconnect frequency high (DO NOT MODIFY)", 100, "Hz", "", nil, nil, 0, 0, false},

	// Power control (60-65)
	60: {"Active Power Percent", "Max active power output limit", 0, "%", "", nil, nil, 0, 0, false},
	61: {"Reactive Power Percent", "Reactive power setpoint", 0, "%", "", nil, nil, 0, 0, false},
	62: {"Power Factor Command", "Target power factor (1.0 = unity)", 1000, "", "", nil, nil, 0, 0, false},
	63: {"Soft Start Slope", "Power ramp rate at startup", 0, "%/s", "", nil, nil, 0, 0, false},
	64: {"Charge Power Percent", "Max battery charge rate", 0, "%", "", nil, nil, 0, 0, false},
	65: {"Discharge Power Percent", "Max battery discharge rate", 0, "%", "", nil, nil, 0, 0, false},

	// AC Charge schedule (66-73)
	66: {"AC Charge Power", "Grid charge power rate", 0, "%", "", nil, nil, 0, 0, false},
	67: {"AC Charge SOC Limit", "Stop AC charging at this SOC", 0, "%", "", nil, nil, 0, 100, true},
	68: {"AC Charge Period 1 Start", "AC charge window 1 start time", 0, "", "time", nil, nil, 0, 0, false},
	69: {"AC Charge Period 1 End", "AC charge window 1 end time", 0, "", "time", nil, nil, 0, 0, false},
	70: {"AC Charge Period 2 Start", "AC charge window 2 start time", 0, "", "time", nil, nil, 0, 0, false},
	71: {"AC Charge Period 2 End", "AC charge window 2 end time", 0, "", "time", nil, nil, 0, 0, false},
	72: {"AC Charge Period 3 Start", "AC charge window 3 start time", 0, "", "time", nil, nil, 0, 0, false},
	73: {"AC Charge Period 3 End", "AC charge window 3 end time", 0, "", "time", nil, nil, 0, 0, false},

	// Charge Priority schedule (74-81)
	74: {"Charge Priority Power", "Charge priority power rate", 0, "%", "", nil, nil, 0, 0, false},
	75: {"Charge Priority SOC Limit", "Charge priority target SOC", 0, "%", "", nil, nil, 0, 100, true},
	76: {"Charge Priority Period 1 Start", "Charge priority window 1 start", 0, "", "time", nil, nil, 0, 0, false},
	77: {"Charge Priority Period 1 End", "Charge priority window 1 end", 0, "", "time", nil, nil, 0, 0, false},
	78: {"Charge Priority Period 2 Start", "Charge priority window 2 start", 0, "", "time", nil, nil, 0, 0, false},
	79: {"Charge Priority Period 2 End", "Charge priority window 2 end", 0, "", "time", nil, nil, 0, 0, false},
	80: {"Charge Priority Period 3 Start", "Charge priority window 3 start", 0, "", "time", nil, nil, 0, 0, false},
	81: {"Charge Priority Period 3 End", "Charge priority window 3 end", 0, "", "time", nil, nil, 0, 0, false},

	// Forced Discharge schedule (82-89)
	82: {"Forced Discharge Power", "Forced discharge power rate", 0, "%", "", nil, nil, 0, 0, false},
	83: {"Forced Discharge SOC Limit", "Forced discharge minimum SOC", 0, "%", "", nil, nil, 0, 100, true},
	84: {"Forced Discharge Period 1 Start", "Forced discharge window 1 start", 0, "", "time", nil, nil, 0, 0, false},
	85: {"Forced Discharge Period 1 End", "Forced discharge window 1 end", 0, "", "time", nil, nil, 0, 0, false},
	86: {"Forced Discharge Period 2 Start", "Forced discharge window 2 start", 0, "", "time", nil, nil, 0, 0, false},
	87: {"Forced Discharge Period 2 End", "Forced discharge window 2 end", 0, "", "time", nil, nil, 0, 0, false},
	88: {"Forced Discharge Period 3 Start", "Forced discharge window 3 start", 0, "", "time", nil, nil, 0, 0, false},
	89: {"Forced Discharge Period 3 End", "Forced discharge window 3 end", 0, "", "time", nil, nil, 0, 0, false},

	// Battery & SOC limits - ranges depend on battery system config
	99:  {"Lead Acid Charge Voltage", "Lead-acid battery charge voltage", 10, "V", "", nil, nil, 0, 0, false},
	100: {"Lead Acid Discharge Cutoff", "Lead-acid discharge cutoff voltage", 10, "V", "", nil, nil, 0, 0, false},
	103: {"Grid Feed-in Power Limit", "Max power export to grid", 0, "%", "", nil, nil, 0, 100, true},
	105: {"Discharge Cutoff SOC", "Stop discharge at this SOC (on-grid)", 0, "%", "", nil, nil, 0, 100, true},
	110: {"Secondary Function Flags", "Secondary mode flags (fast zero export, micro grid)", 0, "", "bitmask", nil, SecondaryFunctionBits, 0, 0, false},
	116: {"Grid Import Start Discharge", "Grid import threshold to start battery discharge", 0, "W", "", nil, nil, 0, 0, false},
	125: {"EPS Discharge Cutoff SOC", "Stop discharge at this SOC (off-grid/EPS)", 0, "%", "", nil, nil, 0, 100, true},
	144: {"Floating Voltage", "Battery float charge voltage", 10, "V", "", nil, nil, 0, 0, false},
	147: {"Battery Capacity", "Configured battery capacity", 0, "Ah", "", nil, nil, 0, 0, false},
	148: {"Nominal Battery Voltage", "Nominal battery voltage", 10, "V", "", nil, nil, 0, 0, false},
	160: {"AC Charge Start SOC", "Begin AC charging below this SOC", 0, "%", "", nil, nil, 0, 100, true},
	161: {"AC Charge End SOC", "Stop AC charging at this SOC", 0, "%", "", nil, nil, 0, 100, true},
	162: {"Battery Warning Voltage", "Low battery warning voltage", 10, "V", "", nil, nil, 0, 0, false},
	164: {"Battery Warning SOC", "Low battery warning SOC", 0, "%", "", nil, nil, 0, 100, true},
	166: {"Switch to Grid Voltage", "Switch to grid at this voltage", 10, "V", "", nil, nil, 0, 0, false},
	167: {"Switch to Grid SOC", "Switch to grid at this SOC", 0, "%", "", nil, nil, 0, 100, true},
	168: {"AC Charge Current Limit", "Max AC charging current", 0, "A", "", nil, nil, 0, 0, false},
	169: {"On-Grid EOD Voltage", "On-grid end-of-discharge voltage", 10, "V", "", nil, nil, 0, 0, false},
}

// Legacy name-only maps for backward compatibility
var HoldingRegisterNames = make(map[uint16]string)
var InputRegisterNames = make(map[uint16]string)

func init() {
	for k, v := range HoldingRegisters {
		HoldingRegisterNames[k] = v.Name
	}
	for k, v := range InputRegisters {
		InputRegisterNames[k] = v.Name
	}
}

// GetRegisterDef returns the register definition for a given register type and number.
func GetRegisterDef(regType string, num uint16) (RegisterDef, bool) {
	switch regType {
	case "holding":
		def, ok := HoldingRegisters[num]
		return def, ok
	case "input":
		def, ok := InputRegisters[num]
		return def, ok
	}
	return RegisterDef{}, false
}

// RegisterName returns the human-readable name for a register.
func RegisterName(regType string, num uint16) string {
	if def, ok := GetRegisterDef(regType, num); ok {
		return def.Name
	}
	return ""
}

// FormatRegister returns a formatted string with name, scaled value, and unit.
func FormatRegister(regType string, num uint16, raw uint16) string {
	def, ok := GetRegisterDef(regType, num)
	if !ok {
		return fmt.Sprintf("r%d: %d", num, raw)
	}
	return fmt.Sprintf("%s: %s", def.Name, def.FormatValue(raw))
}

// CombineHiLo combines a hi/lo register pair into a 32-bit value.
// Energy totals use this encoding and should be divided by 10 for kWh.
func CombineHiLo(lo, hi uint16) uint32 {
	return (uint32(hi) << 16) | uint32(lo)
}

// FormatEnergy32 formats a 32-bit energy value (from hi/lo pair) as kWh.
func FormatEnergy32(lo, hi uint16) string {
	val := CombineHiLo(lo, hi)
	kwh := float64(val) / 10.0
	return fmt.Sprintf("%.1f kWh", kwh)
}

// ValidateValue checks if a register value is within expected range.
// Returns a warning message if suspicious, empty string if OK.
func ValidateValue(regType string, num uint16, raw uint16) string {
	def, ok := GetRegisterDef(regType, num)
	if !ok {
		return ""
	}

	scaled := def.ScaledValue(raw)

	// Check for obviously wrong values based on unit
	switch def.Unit {
	case "V":
		if scaled > 1000 || (scaled > 0 && scaled < 1) {
			return fmt.Sprintf("suspicious voltage: %.1fV", scaled)
		}
	case "°C":
		if scaled > 100 || scaled < -50 {
			return fmt.Sprintf("suspicious temp: %.1f°C", scaled)
		}
	case "Hz":
		if scaled > 70 || scaled < 40 {
			return fmt.Sprintf("suspicious freq: %.2fHz", scaled)
		}
	case "%":
		if scaled > 200 || scaled < 0 {
			return fmt.Sprintf("suspicious percent: %.0f%%", scaled)
		}
	}

	return ""
}

// HaveAllRegisters returns true if the collected maps contain every defined register.
func HaveAllRegisters(holding, input map[uint16]uint16) bool {
	for reg := range HoldingRegisters {
		if _, ok := holding[reg]; !ok {
			return false
		}
	}
	for reg := range InputRegisters {
		if _, ok := input[reg]; !ok {
			return false
		}
	}
	return true
}

// HaveMinimumRegisters returns true if we've received at least minHolding holding
// registers and minInput input registers. Use this for early exit when we don't
// need to wait for every defined register.
func HaveMinimumRegisters(holding, input map[uint16]uint16, minHolding, minInput int) bool {
	return len(holding) >= minHolding && len(input) >= minInput
}

// IsProtectedRegister returns true if the register is in the protected range (25-53).
// These are grid voltage/frequency protection registers that must NEVER be written.
func IsProtectedRegister(reg uint16) bool {
	return reg >= 25 && reg <= 53
}

// RegisterMatch represents a register found by name search.
type RegisterMatch struct {
	Type   string      // "holding" or "input"
	Number uint16
	Def    RegisterDef
}

// FindRegisterByName searches both holding and input register maps for registers
// whose Name matches the query (case-insensitive). Exact matches take priority
// over substring matches.
func FindRegisterByName(query string) []RegisterMatch {
	q := strings.ToLower(query)
	var exact, partial []RegisterMatch

	for num, def := range HoldingRegisters {
		name := strings.ToLower(def.Name)
		if name == q {
			exact = append(exact, RegisterMatch{"holding", num, def})
		} else if strings.Contains(name, q) {
			partial = append(partial, RegisterMatch{"holding", num, def})
		}
	}
	for num, def := range InputRegisters {
		name := strings.ToLower(def.Name)
		if name == q {
			exact = append(exact, RegisterMatch{"input", num, def})
		} else if strings.Contains(name, q) {
			partial = append(partial, RegisterMatch{"input", num, def})
		}
	}

	if len(exact) > 0 {
		return exact
	}
	return partial
}
