package lux

import (
	"fmt"
	"net"
	"sync"
	"time"
)

// Client connects to a LuxPower WiFi dongle and provides
// read/write access to inverter registers.
type Client struct {
	host    string
	port    int
	conn    net.Conn
	mu      sync.Mutex
	datalog string
	serial  string

	// Write safety
	guard *WriteGuard

	// Latest register snapshots
	holdingMu sync.RWMutex
	holding   map[uint16]uint16

	inputMu sync.RWMutex
	input   map[uint16]uint16
}

// NewClient creates a new LuxPower client.
func NewClient(host string, port int, datalog, serial string) *Client {
	return &Client{
		host:    host,
		port:    port,
		datalog: datalog,
		serial:  serial,
		guard:   NewWriteGuard(),
		holding: make(map[uint16]uint16),
		input:   make(map[uint16]uint16),
	}
}

// Connect establishes a TCP connection to the dongle.
func (c *Client) Connect() error {
	addr := net.JoinHostPort(c.host, fmt.Sprintf("%d", c.port))
	conn, err := net.DialTimeout("tcp", addr, 10*time.Second)
	if err != nil {
		return fmt.Errorf("connect to %s: %w", addr, err)
	}
	c.conn = conn
	return nil
}

// Close closes the connection.
func (c *Client) Close() error {
	if c.conn != nil {
		return c.conn.Close()
	}
	return nil
}

// Listen reads packets from the dongle and updates internal register state.
// It blocks until the connection is closed or an error occurs.
// The callback is called for each decoded packet (can be nil).
func (c *Client) Listen(callback func(*Packet)) error {
	buf := make([]byte, 0, 8192)
	tmp := make([]byte, 4096)

	for {
		if err := c.conn.SetReadDeadline(time.Now().Add(10 * time.Second)); err != nil {
			return err
		}
		n, err := c.conn.Read(tmp)
		if err != nil {
			return fmt.Errorf("read: %w", err)
		}
		buf = append(buf, tmp[:n]...)

		packets, consumed := FindPackets(buf)
		if consumed > 0 {
			buf = buf[consumed:]
		}

		for _, pkt := range packets {
			c.updateRegisters(pkt)
			if callback != nil {
				callback(pkt)
			}
		}
	}
}

func (c *Client) updateRegisters(pkt *Packet) {
	if pkt.Registers == nil {
		return
	}
	switch pkt.RegisterType {
	case "holding":
		c.holdingMu.Lock()
		for k, v := range pkt.Registers {
			c.holding[k] = v
		}
		c.holdingMu.Unlock()
	case "input":
		c.inputMu.Lock()
		for k, v := range pkt.Registers {
			c.input[k] = v
		}
		c.inputMu.Unlock()
	}
}

// GetHolding returns the current value of a holding register.
func (c *Client) GetHolding(reg uint16) (uint16, bool) {
	c.holdingMu.RLock()
	defer c.holdingMu.RUnlock()
	v, ok := c.holding[reg]
	return v, ok
}

// GetInput returns the current value of an input register.
func (c *Client) GetInput(reg uint16) (uint16, bool) {
	c.inputMu.RLock()
	defer c.inputMu.RUnlock()
	v, ok := c.input[reg]
	return v, ok
}

// AllHolding returns a copy of all holding registers.
func (c *Client) AllHolding() map[uint16]uint16 {
	c.holdingMu.RLock()
	defer c.holdingMu.RUnlock()
	m := make(map[uint16]uint16, len(c.holding))
	for k, v := range c.holding {
		m[k] = v
	}
	return m
}

// AllInput returns a copy of all input registers.
func (c *Client) AllInput() map[uint16]uint16 {
	c.inputMu.RLock()
	defer c.inputMu.RUnlock()
	m := make(map[uint16]uint16, len(c.input))
	for k, v := range c.input {
		m[k] = v
	}
	return m
}

// ReadHold sends a ReadHold request to the inverter.
// The response will be received asynchronously via Listen().
func (c *Client) ReadHold(startReg, count uint16) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.conn == nil {
		return fmt.Errorf("not connected")
	}

	pkt := BuildReadHold(c.datalog, c.serial, startReg, count)
	_, err := c.conn.Write(pkt)
	if err != nil {
		return fmt.Errorf("write ReadHold request: %w", err)
	}
	return nil
}

// ReadInput sends a ReadInput request to the inverter.
// The response will be received asynchronously via Listen().
func (c *Client) ReadInput(startReg, count uint16) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.conn == nil {
		return fmt.Errorf("not connected")
	}

	pkt := BuildReadInput(c.datalog, c.serial, startReg, count)
	_, err := c.conn.Write(pkt)
	if err != nil {
		return fmt.Errorf("write ReadInput request: %w", err)
	}
	return nil
}

// WriteSingle sends a WriteSingle request to write a single holding register.
// Performs safety checks before writing:
// - Rejects writes to protected registers (25-53)
// - Enforces rate limiting (max 10 writes/minute)
// - Logs all write attempts
func (c *Client) WriteSingle(reg, value uint16) error {
	// Safety check
	if err := c.guard.Check(reg); err != nil {
		return err
	}

	c.mu.Lock()
	defer c.mu.Unlock()

	if c.conn == nil {
		return fmt.Errorf("not connected")
	}

	// Get current value for logging
	c.holdingMu.RLock()
	oldVal := c.holding[reg]
	c.holdingMu.RUnlock()

	// Build and send packet
	pkt := BuildWriteSingle(c.datalog, c.serial, reg, value)
	_, err := c.conn.Write(pkt)

	// Record the write attempt
	c.guard.Record(reg, oldVal, value, err == nil)

	if err != nil {
		return fmt.Errorf("write register %d: %w", reg, err)
	}
	return nil
}

// WriteGuardStats returns the current write guard statistics.
func (c *Client) WriteGuardStats() (writesInLastMinute int, recentWrites []WriteLogEntry) {
	return c.guard.WritesInLastMinute(), c.guard.RecentWrites(10)
}
