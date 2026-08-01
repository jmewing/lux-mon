package lux

import (
	"encoding/binary"
	"net"
	"testing"
	"time"
)

func TestNewClient(t *testing.T) {
	c := NewClient("192.168.1.100", 8000, "AAAAAAAAAA", "1234567890")
	if c.host != "192.168.1.100" {
		t.Errorf("host = %q, want '192.168.1.100'", c.host)
	}
	if c.port != 8000 {
		t.Errorf("port = %d, want 8000", c.port)
	}
	if c.datalog != "AAAAAAAAAA" {
		t.Errorf("datalog = %q, want 'AAAAAAAAAA'", c.datalog)
	}
	if c.serial != "1234567890" {
		t.Errorf("serial = %q, want '1234567890'", c.serial)
	}
	if c.guard == nil {
		t.Error("guard should not be nil")
	}
	if c.holding == nil {
		t.Error("holding map should be initialized")
	}
	if c.input == nil {
		t.Error("input map should be initialized")
	}
}

func TestCloseNilConn(t *testing.T) {
	c := NewClient("1.2.3.4", 8000, "AAAAAAAAAA", "BBBBBBBBBB")
	if err := c.Close(); err != nil {
		t.Errorf("Close with nil conn returned error: %v", err)
	}
}

func TestConnectInvalidHost(t *testing.T) {
	c := NewClient("192.0.2.1", 1, "AAAAAAAAAA", "BBBBBBBBBB") // non-routable
	c.conn = nil
	// We can't easily test a real connect failure without a long timeout,
	// but we can test that ReadHold/ReadInput/WriteSingle fail when not connected.
	if err := c.ReadHold(0, 10); err == nil {
		t.Error("ReadHold without connection should return error")
	}
	if err := c.ReadInput(0, 10); err == nil {
		t.Error("ReadInput without connection should return error")
	}
	if err := c.WriteSingle(60, 100); err == nil {
		t.Error("WriteSingle without connection should return error")
	}
}

func TestUpdateRegisters(t *testing.T) {
	c := NewClient("1.2.3.4", 8000, "AAAAAAAAAA", "BBBBBBBBBB")

	// Update holding registers
	holdingPkt := &Packet{
		RegisterType: "holding",
		Registers:    map[uint16]uint16{21: 0x1234, 60: 100},
	}
	c.updateRegisters(holdingPkt)

	v, ok := c.GetHolding(21)
	if !ok || v != 0x1234 {
		t.Errorf("GetHolding(21) = (%d, %v), want (0x1234, true)", v, ok)
	}
	v, ok = c.GetHolding(60)
	if !ok || v != 100 {
		t.Errorf("GetHolding(60) = (%d, %v), want (100, true)", v, ok)
	}

	// Update input registers
	inputPkt := &Packet{
		RegisterType: "input",
		Registers:    map[uint16]uint16{0: 0x04, 1: 3500},
	}
	c.updateRegisters(inputPkt)

	v, ok = c.GetInput(0)
	if !ok || v != 0x04 {
		t.Errorf("GetInput(0) = (%d, %v), want (4, true)", v, ok)
	}
	v, ok = c.GetInput(1)
	if !ok || v != 3500 {
		t.Errorf("GetInput(1) = (%d, %v), want (3500, true)", v, ok)
	}

	// Unknown register should return false
	_, ok = c.GetHolding(9999)
	if ok {
		t.Error("GetHolding(9999) should return false")
	}
	_, ok = c.GetInput(9999)
	if ok {
		t.Error("GetInput(9999) should return false")
	}
}

func TestUpdateRegistersNilPacket(t *testing.T) {
	c := NewClient("1.2.3.4", 8000, "AAAAAAAAAA", "BBBBBBBBBB")
	// Should not panic
	c.updateRegisters(&Packet{})
	c.updateRegisters(&Packet{RegisterType: "holding", Registers: nil})
}

func TestAllHoldingAllInput(t *testing.T) {
	c := NewClient("1.2.3.4", 8000, "AAAAAAAAAA", "BBBBBBBBBB")

	c.updateRegisters(&Packet{
		RegisterType: "holding",
		Registers:    map[uint16]uint16{21: 100, 60: 200},
	})
	c.updateRegisters(&Packet{
		RegisterType: "input",
		Registers:    map[uint16]uint16{0: 1, 1: 2, 2: 3},
	})

	holding := c.AllHolding()
	if len(holding) != 2 {
		t.Errorf("AllHolding() has %d entries, want 2", len(holding))
	}
	if holding[21] != 100 || holding[60] != 200 {
		t.Errorf("AllHolding() = %v", holding)
	}
	// Verify it's a copy
	holding[21] = 999
	v, _ := c.GetHolding(21)
	if v != 100 {
		t.Error("AllHolding should return a copy, not the internal map")
	}

	input := c.AllInput()
	if len(input) != 3 {
		t.Errorf("AllInput() has %d entries, want 3", len(input))
	}
}

func TestWriteGuardStats(t *testing.T) {
	c := NewClient("1.2.3.4", 8000, "AAAAAAAAAA", "BBBBBBBBBB")
	count, writes := c.WriteGuardStats()
	if count != 0 || len(writes) != 0 {
		t.Errorf("WriteGuardStats() = (%d, %d entries), want (0, 0)", count, len(writes))
	}
}

// buildResponsePacket builds a protocol=5 TranslatedData response with register data.
func buildResponsePacket(regType byte, startReg uint16, values []uint16) []byte {
	valueLen := len(values) * 2
	// Total: header(2) + proto(2) + framelen(2) + unknown(1) + tcpfunc(1) +
	//        datalog(10) + datalen(2) + addr(1) + devfunc(1) + inverter(10) +
	//        startreg(2) + vlb(1) + values(N) + crc(2)
	total := 35 + valueLen + 2
	pkt := make([]byte, total)
	pkt[0], pkt[1] = 0xa1, 0x1a
	binary.LittleEndian.PutUint16(pkt[2:4], 5)                    // Protocol=5
	binary.LittleEndian.PutUint16(pkt[4:6], uint16(total-6))      // Frame length
	pkt[6] = 0x01                                                  // Unknown
	pkt[7] = FuncTranslatedData
	copy(pkt[8:18], "AAAAAAAAAA")
	binary.LittleEndian.PutUint16(pkt[18:20], uint16(total-20))   // Data length
	pkt[20] = 0x01                                                 // Address=inverter
	pkt[21] = regType
	copy(pkt[22:32], "1234567890")
	binary.LittleEndian.PutUint16(pkt[32:34], startReg)
	pkt[34] = byte(valueLen) // VLB
	for i, v := range values {
		binary.LittleEndian.PutUint16(pkt[35+i*2:35+i*2+2], v)
	}
	crc := CRC16Modbus(pkt[20 : total-2])
	binary.LittleEndian.PutUint16(pkt[total-2:], crc)
	return pkt
}

func TestListenWithPipe(t *testing.T) {
	c := NewClient("1.2.3.4", 8000, "AAAAAAAAAA", "1234567890")
	server, client := net.Pipe()
	c.conn = client

	// Build a holding response packet with 2 registers
	pkt := buildResponsePacket(ModbusReadHolding, 21, []uint16{0xABCD, 0x1111})

	var listenErr error
	received := make(chan struct{})
	go func() {
		listenErr = c.Listen(func(p *Packet) {
			close(received)
		})
	}()

	// Write the packet from server side
	_, err := server.Write(pkt)
	if err != nil {
		t.Fatalf("server.Write: %v", err)
	}

	// Wait for callback
	select {
	case <-received:
	case <-time.After(2 * time.Second):
		t.Fatal("Listen callback not called within timeout")
	}

	// Verify registers were stored
	v, ok := c.GetHolding(21)
	if !ok || v != 0xABCD {
		t.Errorf("GetHolding(21) = (%04x, %v), want (ABCD, true)", v, ok)
	}

	// Close to stop Listen
	server.Close()
	client.Close()
	_ = listenErr
}

func TestListenInputRegisters(t *testing.T) {
	c := NewClient("1.2.3.4", 8000, "AAAAAAAAAA", "1234567890")
	server, client := net.Pipe()
	c.conn = client

	pkt := buildResponsePacket(ModbusReadInput, 0, []uint16{0x04, 3500, 3200})

	received := make(chan struct{})
	go func() {
		c.Listen(func(p *Packet) {
			close(received)
		})
	}()

	server.Write(pkt)

	select {
	case <-received:
	case <-time.After(2 * time.Second):
		t.Fatal("Listen callback not called")
	}

	v, ok := c.GetInput(0)
	if !ok || v != 0x04 {
		t.Errorf("GetInput(0) = (%d, %v), want (4, true)", v, ok)
	}
	v, ok = c.GetInput(1)
	if !ok || v != 3500 {
		t.Errorf("GetInput(1) = (%d, %v), want (3500, true)", v, ok)
	}

	server.Close()
	client.Close()
}

func TestListenNilCallback(t *testing.T) {
	c := NewClient("1.2.3.4", 8000, "AAAAAAAAAA", "1234567890")
	server, client := net.Pipe()
	c.conn = client

	pkt := buildResponsePacket(ModbusReadHolding, 60, []uint16{100})

	done := make(chan error, 1)
	go func() {
		done <- c.Listen(nil)
	}()

	server.Write(pkt)
	// Give time for processing
	time.Sleep(50 * time.Millisecond)

	// Verify register was still stored despite nil callback
	v, ok := c.GetHolding(60)
	if !ok || v != 100 {
		t.Errorf("GetHolding(60) = (%d, %v), want (100, true)", v, ok)
	}

	server.Close()
	client.Close()
	<-done
}

func TestReadHoldWithPipe(t *testing.T) {
	c := NewClient("1.2.3.4", 8000, "AAAAAAAAAA", "1234567890")
	server, client := net.Pipe()
	c.conn = client

	received := make(chan []byte, 1)
	go func() {
		buf := make([]byte, 1024)
		n, _ := server.Read(buf)
		received <- buf[:n]
	}()

	err := c.ReadHold(21, 10)
	if err != nil {
		t.Fatalf("ReadHold: %v", err)
	}

	data := <-received
	if len(data) != 38 {
		t.Fatalf("ReadHold packet length = %d, want 38", len(data))
	}
	if data[21] != ModbusReadHolding {
		t.Errorf("DeviceFunction = %02x, want %02x", data[21], ModbusReadHolding)
	}

	server.Close()
	client.Close()
}

func TestReadInputWithPipe(t *testing.T) {
	c := NewClient("1.2.3.4", 8000, "AAAAAAAAAA", "1234567890")
	server, client := net.Pipe()
	c.conn = client

	received := make(chan []byte, 1)
	go func() {
		buf := make([]byte, 1024)
		n, _ := server.Read(buf)
		received <- buf[:n]
	}()

	err := c.ReadInput(0, 40)
	if err != nil {
		t.Fatalf("ReadInput: %v", err)
	}

	data := <-received
	if data[21] != ModbusReadInput {
		t.Errorf("DeviceFunction = %02x, want %02x", data[21], ModbusReadInput)
	}

	server.Close()
	client.Close()
}

func TestWriteSingleWithPipe(t *testing.T) {
	c := NewClient("1.2.3.4", 8000, "AAAAAAAAAA", "1234567890")
	server, client := net.Pipe()
	c.conn = client

	// Pre-populate holding so we can verify old value logging
	c.updateRegisters(&Packet{
		RegisterType: "holding",
		Registers:    map[uint16]uint16{60: 50},
	})

	received := make(chan []byte, 1)
	go func() {
		buf := make([]byte, 1024)
		n, _ := server.Read(buf)
		received <- buf[:n]
	}()

	err := c.WriteSingle(60, 100)
	if err != nil {
		t.Fatalf("WriteSingle: %v", err)
	}

	data := <-received
	if data[21] != ModbusWriteSingle {
		t.Errorf("DeviceFunction = %02x, want %02x", data[21], ModbusWriteSingle)
	}
	reg := binary.LittleEndian.Uint16(data[32:34])
	val := binary.LittleEndian.Uint16(data[34:36])
	if reg != 60 || val != 100 {
		t.Errorf("Write reg=%d val=%d, want reg=60 val=100", reg, val)
	}

	// Verify write was logged
	count, writes := c.WriteGuardStats()
	if count != 1 {
		t.Errorf("WritesInLastMinute = %d, want 1", count)
	}
	if len(writes) != 1 || writes[0].OldValue != 50 || writes[0].NewValue != 100 {
		t.Errorf("Write log = %+v", writes)
	}

	server.Close()
	client.Close()
}

func TestWriteSingleProtected(t *testing.T) {
	c := NewClient("1.2.3.4", 8000, "AAAAAAAAAA", "1234567890")
	server, client := net.Pipe()
	c.conn = client

	err := c.WriteSingle(30, 100) // Protected register
	if err == nil {
		t.Error("WriteSingle to protected register should return error")
	}

	server.Close()
	client.Close()
}
