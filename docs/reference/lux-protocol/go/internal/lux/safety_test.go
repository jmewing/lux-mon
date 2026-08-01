package lux

import "testing"

func TestWriteGuardRecentWrites(t *testing.T) {
	guard := NewWriteGuard()

	// No writes yet
	writes := guard.RecentWrites(5)
	if len(writes) != 0 {
		t.Errorf("RecentWrites(5) with no writes = %d entries, want 0", len(writes))
	}

	// Add some writes
	guard.Record(60, 0, 100, true)
	guard.Record(64, 50, 80, true)
	guard.Record(67, 0, 50, false)

	// Request more than available
	writes = guard.RecentWrites(10)
	if len(writes) != 3 {
		t.Errorf("RecentWrites(10) = %d entries, want 3", len(writes))
	}

	// Request fewer
	writes = guard.RecentWrites(2)
	if len(writes) != 2 {
		t.Errorf("RecentWrites(2) = %d entries, want 2", len(writes))
	}
	// Should be the last 2 entries
	if writes[0].Register != 64 {
		t.Errorf("RecentWrites[0].Register = %d, want 64", writes[0].Register)
	}
	if writes[1].Register != 67 {
		t.Errorf("RecentWrites[1].Register = %d, want 67", writes[1].Register)
	}

	// Request 0
	writes = guard.RecentWrites(0)
	if writes != nil {
		t.Errorf("RecentWrites(0) = %v, want nil", writes)
	}

	// Negative
	writes = guard.RecentWrites(-1)
	if writes != nil {
		t.Errorf("RecentWrites(-1) = %v, want nil", writes)
	}
}

func TestWritesInLastMinute(t *testing.T) {
	guard := NewWriteGuard()

	if got := guard.WritesInLastMinute(); got != 0 {
		t.Errorf("WritesInLastMinute() = %d, want 0", got)
	}

	guard.Record(60, 0, 100, true)
	guard.Record(64, 50, 80, true)

	if got := guard.WritesInLastMinute(); got != 2 {
		t.Errorf("WritesInLastMinute() = %d, want 2", got)
	}
}

func TestWriteGuardLogTrim(t *testing.T) {
	guard := &WriteGuard{
		maxWritesPerMin: 200, // high limit so we don't hit rate limit
		maxLogEntries:   5,
	}

	// Write more than maxLogEntries
	for i := 0; i < 10; i++ {
		guard.Record(60, 0, uint16(i), true)
	}

	writes := guard.RecentWrites(100)
	if len(writes) != 5 {
		t.Errorf("After 10 records with maxLog=5, got %d entries, want 5", len(writes))
	}
	// Should have the last 5 entries (values 5-9)
	if writes[0].NewValue != 5 {
		t.Errorf("First entry NewValue = %d, want 5", writes[0].NewValue)
	}
}

func TestWriteLogEntry(t *testing.T) {
	guard := NewWriteGuard()
	guard.Record(60, 100, 200, true)

	writes := guard.RecentWrites(1)
	if len(writes) != 1 {
		t.Fatal("Expected 1 write log entry")
	}
	entry := writes[0]
	if entry.Register != 60 || entry.OldValue != 100 || entry.NewValue != 200 || !entry.Success {
		t.Errorf("Entry = {Reg:%d Old:%d New:%d Success:%v}, want {60 100 200 true}",
			entry.Register, entry.OldValue, entry.NewValue, entry.Success)
	}
	if entry.Timestamp.IsZero() {
		t.Error("Entry timestamp should not be zero")
	}
}
