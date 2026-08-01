package lux

import (
	"strings"
	"testing"
)

func TestFormatValueDivisor(t *testing.T) {
	def := RegisterDef{Name: "Voltage", Divisor: 10, Unit: "V"}
	got := def.FormatValue(1234)
	if got != "123.40 V" {
		t.Errorf("FormatValue(1234, div=10) = %q, want '123.40 V'", got)
	}
}

func TestFormatValueDivisorNoUnit(t *testing.T) {
	def := RegisterDef{Name: "PowerFactor", Divisor: 1000}
	got := def.FormatValue(950)
	if got != "0.95" {
		t.Errorf("FormatValue(950, div=1000, no unit) = %q, want '0.95'", got)
	}
}

func TestFormatValueRawWithUnit(t *testing.T) {
	def := RegisterDef{Name: "Power", Unit: "W"}
	got := def.FormatValue(5000)
	if got != "5000 W" {
		t.Errorf("FormatValue(5000, no div, unit=W) = %q, want '5000 W'", got)
	}
}

func TestFormatValueRawNoUnit(t *testing.T) {
	def := RegisterDef{Name: "Count"}
	got := def.FormatValue(42)
	if got != "42" {
		t.Errorf("FormatValue(42, raw) = %q, want '42'", got)
	}
}

func TestFormatValueSocSoh(t *testing.T) {
	def := RegisterDef{Name: "SOC/SOH", Special: "soc_soh"}
	// SOC = low byte = 85, SOH = high byte = 99
	raw := uint16(99<<8 | 85)
	got := def.FormatValue(raw)
	if got != "SOC=85% SOH=99%" {
		t.Errorf("FormatValue(soc_soh) = %q, want 'SOC=85%% SOH=99%%'", got)
	}
}

func TestFormatValueTime(t *testing.T) {
	def := RegisterDef{Name: "Time", Special: "time"}
	// Time encoding: (minute<<8) | hour — so 14:30 = (30<<8)|14
	raw := uint16(30<<8 | 14)
	got := def.FormatValue(raw)
	if got != "14:30" {
		t.Errorf("FormatValue(time) = %q, want '14:30'", got)
	}
}

func TestFormatValueBitmask(t *testing.T) {
	def := RegisterDef{
		Name:    "Flags",
		Special: "bitmask",
		Bits:    map[int]string{0: "EPS", 7: "ACCharge"},
	}
	// Set bits 0 and 7
	raw := uint16(1<<0 | 1<<7)
	got := def.FormatValue(raw)
	if !strings.Contains(got, "EPS") || !strings.Contains(got, "ACCharge") {
		t.Errorf("FormatValue(bitmask) = %q, want to contain 'EPS' and 'ACCharge'", got)
	}
}

func TestFormatValueBitmaskNone(t *testing.T) {
	def := RegisterDef{
		Name:    "Flags",
		Special: "bitmask",
		Bits:    map[int]string{0: "EPS"},
	}
	got := def.FormatValue(0)
	if got != "(none)" {
		t.Errorf("FormatValue(bitmask=0) = %q, want '(none)'", got)
	}
}

func TestFormatValueBitmaskNilBits(t *testing.T) {
	def := RegisterDef{Name: "Flags", Special: "bitmask"}
	got := def.FormatValue(0x00FF)
	if got != "0x00FF" {
		t.Errorf("FormatValue(bitmask, nil bits) = %q, want '0x00FF'", got)
	}
}

func TestFormatValueBitmaskUnknownBit(t *testing.T) {
	def := RegisterDef{
		Name:    "Flags",
		Special: "bitmask",
		Bits:    map[int]string{0: "EPS"},
	}
	// Set bit 0 (known) and bit 3 (unknown)
	raw := uint16(1<<0 | 1<<3)
	got := def.FormatValue(raw)
	if !strings.Contains(got, "EPS") || !strings.Contains(got, "bit3") {
		t.Errorf("FormatValue(bitmask with unknown bit) = %q, want 'EPS' and 'bit3'", got)
	}
}

func TestFormatValueEnum(t *testing.T) {
	def := RegisterDef{
		Name:    "Mode",
		Special: "enum",
		Enum:    map[uint16]string{0: "Independent", 1: "Parallel"},
	}
	got := def.FormatValue(1)
	if got != "Parallel" {
		t.Errorf("FormatValue(enum=1) = %q, want 'Parallel'", got)
	}
}

func TestFormatValueEnumUnknown(t *testing.T) {
	def := RegisterDef{
		Name:    "Mode",
		Special: "enum",
		Enum:    map[uint16]string{0: "Independent"},
	}
	got := def.FormatValue(99)
	if got != "Unknown(99)" {
		t.Errorf("FormatValue(enum=99) = %q, want 'Unknown(99)'", got)
	}
}

func TestFormatValueIdent(t *testing.T) {
	def := RegisterDef{Name: "Model", Special: "ident"}
	got := def.FormatValue(12345)
	if got != "12345" {
		t.Errorf("FormatValue(ident) = %q, want '12345'", got)
	}
}

func TestFormatValueStatus(t *testing.T) {
	def := RegisterDef{Name: "Status", Special: "status"}
	got := def.FormatValue(0x04)
	if got != "PV On-grid" {
		t.Errorf("FormatValue(status=0x04) = %q, want 'PV On-grid'", got)
	}
}

func TestFormatValueStatusUnknown(t *testing.T) {
	def := RegisterDef{Name: "Status", Special: "status"}
	got := def.FormatValue(0xFF)
	if !strings.Contains(got, "Unknown") {
		t.Errorf("FormatValue(status=0xFF) = %q, want 'Unknown(...)'", got)
	}
}

func TestFormatValueOnlyDivisor(t *testing.T) {
	def := RegisterDef{Name: "V", Divisor: 10, Unit: "V"}
	got := def.FormatValueOnly(1234)
	if got != "123.40" {
		t.Errorf("FormatValueOnly(1234) = %q, want '123.40'", got)
	}
}

func TestFormatValueOnlyRaw(t *testing.T) {
	def := RegisterDef{Name: "Power"}
	got := def.FormatValueOnly(5000)
	if got != "5000" {
		t.Errorf("FormatValueOnly(5000, raw) = %q, want '5000'", got)
	}
}

func TestFormatValueOnlySocSoh(t *testing.T) {
	def := RegisterDef{Name: "SOC/SOH", Special: "soc_soh"}
	raw := uint16(99<<8 | 85)
	got := def.FormatValueOnly(raw)
	if got != "SOC=85% SOH=99%" {
		t.Errorf("FormatValueOnly(soc_soh) = %q", got)
	}
}

func TestFormatValueOnlyTime(t *testing.T) {
	def := RegisterDef{Name: "T", Special: "time"}
	// Time encoding: (minute<<8) | hour — so 08:00 = (0<<8)|8
	raw := uint16(0<<8 | 8)
	got := def.FormatValueOnly(raw)
	if got != "08:00" {
		t.Errorf("FormatValueOnly(time) = %q, want '08:00'", got)
	}
}

func TestFormatValueOnlyBitmask(t *testing.T) {
	def := RegisterDef{Name: "F", Special: "bitmask", Bits: map[int]string{0: "A"}}
	got := def.FormatValueOnly(1)
	if got != "A" {
		t.Errorf("FormatValueOnly(bitmask) = %q, want 'A'", got)
	}
}

func TestFormatValueOnlyEnum(t *testing.T) {
	def := RegisterDef{Name: "M", Special: "enum", Enum: map[uint16]string{0: "Off"}}
	if got := def.FormatValueOnly(0); got != "Off" {
		t.Errorf("FormatValueOnly(enum=0) = %q, want 'Off'", got)
	}
	if got := def.FormatValueOnly(5); got != "Unknown(5)" {
		t.Errorf("FormatValueOnly(enum=5) = %q, want 'Unknown(5)'", got)
	}
}

func TestFormatValueOnlyIdent(t *testing.T) {
	def := RegisterDef{Name: "I", Special: "ident"}
	if got := def.FormatValueOnly(42); got != "42" {
		t.Errorf("FormatValueOnly(ident) = %q, want '42'", got)
	}
}

func TestFormatValueOnlyStatus(t *testing.T) {
	def := RegisterDef{Name: "S", Special: "status"}
	if got := def.FormatValueOnly(0x00); got != "Standby" {
		t.Errorf("FormatValueOnly(status=0) = %q, want 'Standby'", got)
	}
}

func TestScaledValue(t *testing.T) {
	def := RegisterDef{Divisor: 10}
	if got := def.ScaledValue(250); got != 25.0 {
		t.Errorf("ScaledValue(250, div=10) = %f, want 25.0", got)
	}
	def2 := RegisterDef{}
	if got := def2.ScaledValue(42); got != 42.0 {
		t.Errorf("ScaledValue(42, no div) = %f, want 42.0", got)
	}
}

func TestValidate(t *testing.T) {
	tests := []struct {
		name    string
		def     RegisterDef
		raw     uint16
		wantMsg bool
	}{
		{"no range", RegisterDef{}, 9999, false},
		{"within range", RegisterDef{Min: 10, Max: 100, HasRange: true}, 50, false},
		{"below min", RegisterDef{Min: 10, Max: 100, HasRange: true}, 5, true},
		{"above max", RegisterDef{Min: 10, Max: 100, HasRange: true}, 200, true},
		{"with divisor below", RegisterDef{Min: 10, Max: 50, HasRange: true, Divisor: 10}, 5, true},
		{"with divisor above", RegisterDef{Min: 10, Max: 50, HasRange: true, Divisor: 10}, 600, true},
		{"min=0 ignored", RegisterDef{Max: 100, HasRange: true}, 0, false},
	}
	for _, tc := range tests {
		got := tc.def.Validate(tc.raw)
		if tc.wantMsg && got == "" {
			t.Errorf("%s: Validate(%d) returned empty, want warning", tc.name, tc.raw)
		}
		if !tc.wantMsg && got != "" {
			t.Errorf("%s: Validate(%d) = %q, want empty", tc.name, tc.raw, got)
		}
	}
}

func TestGetRegisterDef(t *testing.T) {
	def, ok := GetRegisterDef("holding", 21)
	if !ok || def.Name != "Master Function Flags" {
		t.Errorf("GetRegisterDef(holding, 21) = (%q, %v)", def.Name, ok)
	}
	def, ok = GetRegisterDef("input", 0)
	if !ok || def.Name != "Status" {
		t.Errorf("GetRegisterDef(input, 0) = (%q, %v)", def.Name, ok)
	}
	_, ok = GetRegisterDef("invalid", 0)
	if ok {
		t.Error("GetRegisterDef(invalid, 0) should return false")
	}
	_, ok = GetRegisterDef("holding", 9999)
	if ok {
		t.Error("GetRegisterDef(holding, 9999) should return false")
	}
}

func TestFormatRegister(t *testing.T) {
	// Known register
	got := FormatRegister("input", 1, 3500)
	if !strings.Contains(got, "PV1 Voltage") && !strings.Contains(got, "350") {
		t.Errorf("FormatRegister(input, 1, 3500) = %q", got)
	}

	// Unknown register
	got = FormatRegister("input", 9999, 42)
	if got != "r9999: 42" {
		t.Errorf("FormatRegister(input, 9999, 42) = %q, want 'r9999: 42'", got)
	}
}

func TestCombineHiLo(t *testing.T) {
	got := CombineHiLo(0x1234, 0x0001)
	if got != 0x00011234 {
		t.Errorf("CombineHiLo(0x1234, 0x0001) = %08x, want 00011234", got)
	}
	got = CombineHiLo(0, 0)
	if got != 0 {
		t.Errorf("CombineHiLo(0, 0) = %d, want 0", got)
	}
}

func TestFormatEnergy32(t *testing.T) {
	got := FormatEnergy32(1000, 0)
	if got != "100.0 kWh" {
		t.Errorf("FormatEnergy32(1000, 0) = %q, want '100.0 kWh'", got)
	}
	got = FormatEnergy32(0, 1)
	if got != "6553.6 kWh" {
		t.Errorf("FormatEnergy32(0, 1) = %q, want '6553.6 kWh'", got)
	}
}

func TestValidateValue(t *testing.T) {
	// Known register within range
	got := ValidateValue("input", 1, 3500) // 350V
	if got != "" {
		t.Errorf("ValidateValue(PV1 Voltage=350V) = %q, want empty", got)
	}

	// Suspicious voltage
	got = ValidateValue("input", 1, 15000) // 1500V
	if got == "" {
		t.Error("ValidateValue(PV1 Voltage=1500V) should warn")
	}

	// Unknown register
	got = ValidateValue("input", 9999, 42)
	if got != "" {
		t.Errorf("ValidateValue(unknown) = %q, want empty", got)
	}

	// Suspicious temperature
	got = ValidateValue("input", 64, 150) // 150°C
	if got == "" {
		t.Error("ValidateValue(InternalTemp=150) should warn")
	}

	// Suspicious frequency
	got = ValidateValue("input", 15, 8000) // 80Hz
	if got == "" {
		t.Error("ValidateValue(freq=80Hz) should warn")
	}

	// Suspicious percent
	got = ValidateValue("holding", 60, 300) // 300%
	if got == "" {
		t.Error("ValidateValue(percent=300) should warn")
	}

	// Temperature below -50
	got = ValidateValue("input", 64, 65500) // wraps to large number, but as °C it's > 100
	if got == "" {
		t.Error("ValidateValue(temp>100) should warn")
	}
}

func TestStatusCodes(t *testing.T) {
	if StatusCodes[0x00] != "Standby" {
		t.Errorf("StatusCodes[0x00] = %q, want 'Standby'", StatusCodes[0x00])
	}
	if StatusCodes[0x04] != "PV On-grid" {
		t.Errorf("StatusCodes[0x04] = %q", StatusCodes[0x04])
	}
}

func TestLegacyNameMaps(t *testing.T) {
	if HoldingRegisterNames[21] != "Master Function Flags" {
		t.Errorf("HoldingRegisterNames[21] = %q", HoldingRegisterNames[21])
	}
	if InputRegisterNames[0] != "Status" {
		t.Errorf("InputRegisterNames[0] = %q", InputRegisterNames[0])
	}
}
