package cmd

import (
	"encoding/json"
	"testing"

	"github.com/jefflaplante/lux/internal/lux"
)

// --- Time encoding/decoding ---

func TestEncodeDecodeTime(t *testing.T) {
	tests := []struct {
		hour, min int
	}{
		{0, 0},
		{6, 30},
		{12, 0},
		{22, 30},
		{23, 59},
	}
	for _, tc := range tests {
		raw := encodeTime(tc.hour, tc.min)
		gotH, gotM := decodeTime(raw)
		if gotH != tc.hour || gotM != tc.min {
			t.Errorf("encodeTime(%d,%d)=%d → decodeTime=%d:%d", tc.hour, tc.min, raw, gotH, gotM)
		}
	}
}

func TestEncodeTimeValues(t *testing.T) {
	// 22:30 should encode as (30<<8)|22 = 7702
	raw := encodeTime(22, 30)
	if raw != 7702 {
		t.Errorf("encodeTime(22,30) = %d, want 7702", raw)
	}
	// 00:00 should be 0
	if encodeTime(0, 0) != 0 {
		t.Errorf("encodeTime(0,0) = %d, want 0", encodeTime(0, 0))
	}
}

// --- Time range parsing ---

func TestParseTimeRange(t *testing.T) {
	start, end, err := parseTimeRange("22:00-06:30")
	if err != nil {
		t.Fatal(err)
	}
	if start != [2]int{22, 0} {
		t.Errorf("start = %v, want [22,0]", start)
	}
	if end != [2]int{6, 30} {
		t.Errorf("end = %v, want [6,30]", end)
	}
}

func TestParseTimeRangeDisabled(t *testing.T) {
	start, end, err := parseTimeRange("00:00-00:00")
	if err != nil {
		t.Fatal(err)
	}
	if start != [2]int{0, 0} || end != [2]int{0, 0} {
		t.Errorf("disabled range: start=%v end=%v", start, end)
	}
}

func TestParseTimeRangeInvalid(t *testing.T) {
	cases := []string{
		"22:00",        // no dash
		"25:00-06:00",  // invalid hour
		"22:60-06:00",  // invalid minute
		"22:00-06:99",  // invalid end minute
		"abc-def",      // not numbers
	}
	for _, c := range cases {
		if _, _, err := parseTimeRange(c); err == nil {
			t.Errorf("parseTimeRange(%q) should fail", c)
		}
	}
}

func TestParseHHMM(t *testing.T) {
	result, err := parseHHMM("14:30")
	if err != nil {
		t.Fatal(err)
	}
	if result != [2]int{14, 30} {
		t.Errorf("parseHHMM(14:30) = %v", result)
	}
}

func TestParseHHMMInvalid(t *testing.T) {
	cases := []string{"", "14", "aa:bb", "-1:00", "24:00", "12:60"}
	for _, c := range cases {
		if _, err := parseHHMM(c); err == nil {
			t.Errorf("parseHHMM(%q) should fail", c)
		}
	}
}

// --- Register argument parsing ---

func TestParseRegArgHolding(t *testing.T) {
	targets, err := parseRegArg("h66")
	if err != nil {
		t.Fatal(err)
	}
	if len(targets) != 1 || targets[0].regType != "holding" || targets[0].regNum != 66 {
		t.Errorf("parseRegArg(h66) = %v", targets)
	}
}

func TestParseRegArgInput(t *testing.T) {
	targets, err := parseRegArg("i5")
	if err != nil {
		t.Fatal(err)
	}
	if len(targets) != 1 || targets[0].regType != "input" || targets[0].regNum != 5 {
		t.Errorf("parseRegArg(i5) = %v", targets)
	}
}

func TestParseRegArgBareNumber(t *testing.T) {
	targets, err := parseRegArg("21")
	if err != nil {
		t.Fatal(err)
	}
	if len(targets) != 1 || targets[0].regType != "holding" || targets[0].regNum != 21 {
		t.Errorf("parseRegArg(21) = %v, want holding:21", targets)
	}
}

func TestParseRegArgName(t *testing.T) {
	targets, err := parseRegArg("SOC/SOH")
	if err != nil {
		t.Fatal(err)
	}
	if len(targets) == 0 {
		t.Error("parseRegArg(SOC/SOH) returned no matches")
	}
}

func TestParseRegArgUnknown(t *testing.T) {
	_, err := parseRegArg("zzz_nonexistent_zzz")
	if err == nil {
		t.Error("parseRegArg(nonexistent) should fail")
	}
}

func TestParseRegArgsComma(t *testing.T) {
	targets, err := parseRegArgs([]string{"h66,h67,h68"})
	if err != nil {
		t.Fatal(err)
	}
	if len(targets) != 3 {
		t.Errorf("parseRegArgs comma-separated: got %d targets, want 3", len(targets))
	}
}

// --- Batch targets ---

func TestBatchTargetsMerge(t *testing.T) {
	targets := []regTarget{
		{"holding", 66}, {"holding", 67}, {"holding", 68},
	}
	batches := batchTargets(targets)
	if len(batches) != 1 {
		t.Fatalf("batchTargets: got %d batches, want 1", len(batches))
	}
	if batches[0].min != 66 || batches[0].max != 68 {
		t.Errorf("batch = %d-%d, want 66-68", batches[0].min, batches[0].max)
	}
}

func TestBatchTargetsSplit(t *testing.T) {
	targets := []regTarget{
		{"holding", 21}, {"holding", 66},
	}
	batches := batchTargets(targets)
	if len(batches) != 2 {
		t.Errorf("batchTargets: got %d batches, want 2 (gap > 4)", len(batches))
	}
}

func TestBatchTargetsMixedTypes(t *testing.T) {
	targets := []regTarget{
		{"holding", 21}, {"input", 5},
	}
	batches := batchTargets(targets)
	if len(batches) != 2 {
		t.Errorf("batchTargets mixed types: got %d batches, want 2", len(batches))
	}
}

// --- Schedule types consistency ---

func TestScheduleTypesComplete(t *testing.T) {
	for _, key := range scheduleTypeOrder {
		sched, ok := scheduleTypes[key]
		if !ok {
			t.Errorf("scheduleTypeOrder has %q but scheduleTypes doesn't", key)
			continue
		}
		if sched.name == "" {
			t.Errorf("scheduleTypes[%q].name is empty", key)
		}
		// Verify register ordering: powerReg < socReg < periods
		if sched.socReg != sched.powerReg+1 {
			t.Errorf("%s: socReg (%d) != powerReg+1 (%d)", key, sched.socReg, sched.powerReg+1)
		}
		if sched.periods[0][0] != sched.socReg+1 {
			t.Errorf("%s: period1Start (%d) != socReg+1 (%d)", key, sched.periods[0][0], sched.socReg+1)
		}
		// Each period should be 2 consecutive registers
		for i, p := range sched.periods {
			if p[1] != p[0]+1 {
				t.Errorf("%s period %d: end reg (%d) != start reg + 1 (%d)", key, i+1, p[1], p[0]+1)
			}
		}
	}
}

func TestScheduleTypeOrderMatchesMap(t *testing.T) {
	if len(scheduleTypeOrder) != len(scheduleTypes) {
		t.Errorf("scheduleTypeOrder has %d entries, scheduleTypes has %d",
			len(scheduleTypeOrder), len(scheduleTypes))
	}
}

// --- collectRegEntries ---

func TestCollectRegEntriesHolding(t *testing.T) {
	entries := collectRegEntries("holding", "h", lux.HoldingRegisters, "")
	if len(entries) == 0 {
		t.Fatal("collectRegEntries returned no entries for holding registers")
	}
	// Verify structure
	for _, e := range entries {
		if e.Type != "holding" {
			t.Errorf("entry type = %q, want holding", e.Type)
		}
		if e.Name == "" {
			t.Errorf("entry for h%d has empty name", e.Register)
		}
	}
}

func TestCollectRegEntriesFilter(t *testing.T) {
	all := collectRegEntries("holding", "h", lux.HoldingRegisters, "")
	filtered := collectRegEntries("holding", "h", lux.HoldingRegisters, "charge")
	if len(filtered) >= len(all) {
		t.Error("filter should reduce results")
	}
	if len(filtered) == 0 {
		t.Error("filter 'charge' should match some holding registers")
	}
	for _, e := range filtered {
		if e.Name == "" {
			t.Error("filtered entry has empty name")
		}
	}
}

func TestCollectRegEntriesSorted(t *testing.T) {
	entries := collectRegEntries("input", "i", lux.InputRegisters, "")
	for i := 1; i < len(entries); i++ {
		if entries[i].Register < entries[i-1].Register {
			t.Errorf("not sorted: reg %d before %d", entries[i-1].Register, entries[i].Register)
		}
	}
}

// --- formatRegValue ---

func TestFormatRegValue(t *testing.T) {
	// Time register — should format as HH:MM
	raw := encodeTime(14, 30)
	got := formatRegValue("holding", 68, raw) // AC Charge Period 1 Start
	if got != "14:30" {
		t.Errorf("formatRegValue(h68, 14:30) = %q, want '14:30'", got)
	}
}

func TestFormatRegValueUnknown(t *testing.T) {
	got := formatRegValue("holding", 9999, 42)
	if got != "42" {
		t.Errorf("formatRegValue(unknown) = %q, want '42'", got)
	}
}

// --- fillSlots ---

func TestFillSlotsNormal(t *testing.T) {
	var arr [slots]bool
	fillSlots(&arr, 6, 0, 12, 0) // 06:00-12:00
	// Slot 12 (06:00) through 23 (11:30) should be active
	for s := 0; s < slots; s++ {
		expected := s >= 12 && s < 24
		if arr[s] != expected {
			t.Errorf("slot %d: got %v, want %v (06:00-12:00)", s, arr[s], expected)
		}
	}
}

func TestFillSlotsWrap(t *testing.T) {
	var arr [slots]bool
	fillSlots(&arr, 22, 0, 6, 0) // 22:00-06:00 wraps midnight
	for s := 0; s < slots; s++ {
		expected := s >= 44 || s < 12 // 22:00+ or <06:00
		if arr[s] != expected {
			t.Errorf("slot %d: got %v, want %v (22:00-06:00 wrap)", s, arr[s], expected)
		}
	}
}

func TestFillSlotsFullDay(t *testing.T) {
	// 00:00-00:00 is treated as wrap-around (fills all slots).
	// Callers check for disabled (00:00-00:00) before calling fillSlots.
	var arr [slots]bool
	fillSlots(&arr, 0, 0, 0, 0)
	for s := 0; s < slots; s++ {
		if !arr[s] {
			t.Errorf("slot %d should be true for 00:00-00:00 (full wrap)", s)
		}
	}
}

// --- JSON structure validation ---

func TestRegListEntryJSON(t *testing.T) {
	e := regListEntry{
		Type:        "holding",
		Register:    21,
		Name:        "Master Function Flags",
		Description: "Bitmask of operating mode flags",
		Unit:        "",
	}
	data, err := json.Marshal(e)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]interface{}
	json.Unmarshal(data, &m)
	if m["type"] != "holding" {
		t.Errorf("type = %v", m["type"])
	}
	if m["register"] != float64(21) {
		t.Errorf("register = %v", m["register"])
	}
	// Unit should be omitted when empty (omitempty)
	if _, ok := m["unit"]; ok {
		t.Error("empty unit should be omitted")
	}
}

func TestRegListEntryJSONWithUnit(t *testing.T) {
	e := regListEntry{
		Type:        "input",
		Register:    1,
		Name:        "PV1 Voltage",
		Description: "PV string 1 voltage",
		Unit:        "V",
	}
	data, _ := json.Marshal(e)
	var m map[string]interface{}
	json.Unmarshal(data, &m)
	if m["unit"] != "V" {
		t.Errorf("unit = %v, want V", m["unit"])
	}
}

// --- Mode aliases ---

func TestModeAliasesValid(t *testing.T) {
	for alias, bit := range modeAliases {
		if bit < 0 || bit > 15 {
			t.Errorf("modeAliases[%q] = %d, out of range 0-15", alias, bit)
		}
		// Verify the bit has a name in MasterFunctionBits
		if _, ok := lux.MasterFunctionBits[bit]; !ok {
			t.Errorf("modeAliases[%q] = bit %d, not in MasterFunctionBits", alias, bit)
		}
	}
}
