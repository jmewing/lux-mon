package lux

import (
	"fmt"
	"sync"
	"time"
)

// WriteGuard provides safety checks for register writes.
// It enforces rate limiting and logs all write operations.
type WriteGuard struct {
	mu                sync.Mutex
	writes            []time.Time
	maxWritesPerMin   int
	writeLog          []WriteLogEntry
	maxLogEntries     int
}

// WriteLogEntry records a single write operation.
type WriteLogEntry struct {
	Timestamp time.Time
	Register  uint16
	OldValue  uint16
	NewValue  uint16
	Success   bool
}

// NewWriteGuard creates a new WriteGuard with default settings.
// Default: max 10 writes per minute, keep last 100 log entries.
func NewWriteGuard() *WriteGuard {
	return &WriteGuard{
		maxWritesPerMin: 10,
		maxLogEntries:   100,
	}
}

// Check validates that a write to the given register is allowed.
// Returns an error if the register is protected or rate limit exceeded.
func (g *WriteGuard) Check(reg uint16) error {
	// Check protected registers
	if IsProtectedRegister(reg) {
		return fmt.Errorf("register %d is protected (grid protection range 25-53)", reg)
	}

	g.mu.Lock()
	defer g.mu.Unlock()

	// Clean up old writes (older than 1 minute)
	cutoff := time.Now().Add(-time.Minute)
	var recent []time.Time
	for _, t := range g.writes {
		if t.After(cutoff) {
			recent = append(recent, t)
		}
	}
	g.writes = recent

	// Check rate limit
	if len(g.writes) >= g.maxWritesPerMin {
		return fmt.Errorf("rate limit exceeded: %d writes in the last minute (max %d)",
			len(g.writes), g.maxWritesPerMin)
	}

	return nil
}

// Record logs a write operation and updates the rate limit counter.
func (g *WriteGuard) Record(reg uint16, oldVal, newVal uint16, success bool) {
	g.mu.Lock()
	defer g.mu.Unlock()

	now := time.Now()

	// Add to rate limit tracking
	g.writes = append(g.writes, now)

	// Add to log
	entry := WriteLogEntry{
		Timestamp: now,
		Register:  reg,
		OldValue:  oldVal,
		NewValue:  newVal,
		Success:   success,
	}
	g.writeLog = append(g.writeLog, entry)

	// Trim log if too long
	if len(g.writeLog) > g.maxLogEntries {
		g.writeLog = g.writeLog[len(g.writeLog)-g.maxLogEntries:]
	}
}

// RecentWrites returns the most recent write log entries (up to n).
func (g *WriteGuard) RecentWrites(n int) []WriteLogEntry {
	g.mu.Lock()
	defer g.mu.Unlock()

	if n > len(g.writeLog) {
		n = len(g.writeLog)
	}
	if n <= 0 {
		return nil
	}

	result := make([]WriteLogEntry, n)
	copy(result, g.writeLog[len(g.writeLog)-n:])
	return result
}

// WritesInLastMinute returns the count of writes in the last minute.
func (g *WriteGuard) WritesInLastMinute() int {
	g.mu.Lock()
	defer g.mu.Unlock()

	cutoff := time.Now().Add(-time.Minute)
	count := 0
	for _, t := range g.writes {
		if t.After(cutoff) {
			count++
		}
	}
	return count
}
