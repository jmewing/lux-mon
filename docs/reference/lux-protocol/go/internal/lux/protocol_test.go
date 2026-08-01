package lux

import (
	"encoding/binary"
	"testing"
)

func TestCRC16Modbus(t *testing.T) {
	// Standard Modbus CRC test vector
	// Request: slave=1, func=3, start=0, count=10
	// CRC bytes in frame: C5 CD (low byte first), which as LE u16 = 0xCDC5
	data := []byte{0x01, 0x03, 0x00, 0x00, 0x00, 0x0A}
	expected := uint16(0xCDC5)
	got := CRC16Modbus(data)
	if got != expected {
		t.Errorf("CRC16Modbus(%x) = %04x, want %04x", data, got, expected)
	}

	// Empty data
	got = CRC16Modbus([]byte{})
	if got != 0xFFFF {
		t.Errorf("CRC16Modbus([]) = %04x, want FFFF", got)
	}

	// Single byte
	got = CRC16Modbus([]byte{0x00})
	if got == 0xFFFF {
		t.Error("CRC16Modbus([0x00]) should not be FFFF")
	}
}

func TestDecodeHeartbeat(t *testing.T) {
	// Build a heartbeat packet
	pkt := make([]byte, 18)
	pkt[0], pkt[1] = 0xa1, 0x1a // Header
	binary.LittleEndian.PutUint16(pkt[2:4], 5)  // Protocol
	binary.LittleEndian.PutUint16(pkt[4:6], 12) // Frame length
	pkt[6] = 0x01                               // Unknown
	pkt[7] = FuncHeartbeat                      // TCP function
	copy(pkt[8:18], "AAAAAAAAAA")               // Datalog

	decoded := Decode(pkt)
	if decoded == nil {
		t.Fatal("Decode returned nil for valid heartbeat")
	}
	if decoded.TCPFunction != FuncHeartbeat {
		t.Errorf("TCPFunction = %02x, want %02x", decoded.TCPFunction, FuncHeartbeat)
	}
	if decoded.Datalog != "AAAAAAAAAA" {
		t.Errorf("Datalog = %q, want %q", decoded.Datalog, "AAAAAAAAAA")
	}
	if decoded.Protocol != 5 {
		t.Errorf("Protocol = %d, want 5", decoded.Protocol)
	}
}

func TestDecodeTranslatedData(t *testing.T) {
	// Build a TranslatedData packet (protocol=5, ReadHold, 2 registers)
	// This simulates an inverter response with VLB
	pkt := make([]byte, 41)
	pkt[0], pkt[1] = 0xa1, 0x1a                   // Header
	binary.LittleEndian.PutUint16(pkt[2:4], 5)    // Protocol=5
	binary.LittleEndian.PutUint16(pkt[4:6], 35)   // Frame length (41-6)
	pkt[6] = 0x01                                 // Unknown
	pkt[7] = FuncTranslatedData                   // TCP function
	copy(pkt[8:18], "AAAAAAAAAA")                 // Datalog
	binary.LittleEndian.PutUint16(pkt[18:20], 21) // Data length
	pkt[20] = 0x01                                // Address (inverter)
	pkt[21] = ModbusReadHolding                   // Device function
	copy(pkt[22:32], "1234567890")                // Inverter serial
	binary.LittleEndian.PutUint16(pkt[32:34], 21) // Start register
	pkt[34] = 4                                   // Value length (4 bytes = 2 registers)
	// Register values (LE u16)
	binary.LittleEndian.PutUint16(pkt[35:37], 0x1234) // Reg 21 = 0x1234
	binary.LittleEndian.PutUint16(pkt[37:39], 0x5678) // Reg 22 = 0x5678
	// CRC over data frame [20:39]
	crc := CRC16Modbus(pkt[20:39])
	binary.LittleEndian.PutUint16(pkt[39:41], crc)

	decoded := Decode(pkt)
	if decoded == nil {
		t.Fatal("Decode returned nil for valid TranslatedData")
	}
	if decoded.TCPFunction != FuncTranslatedData {
		t.Errorf("TCPFunction = %02x, want %02x", decoded.TCPFunction, FuncTranslatedData)
	}
	if decoded.Datalog != "AAAAAAAAAA" {
		t.Errorf("Datalog = %q, want %q", decoded.Datalog, "AAAAAAAAAA")
	}
	if decoded.Inverter != "1234567890" {
		t.Errorf("Inverter = %q, want %q", decoded.Inverter, "1234567890")
	}
	if decoded.RegisterType != "holding" {
		t.Errorf("RegisterType = %q, want %q", decoded.RegisterType, "holding")
	}
	if decoded.StartRegister != 21 {
		t.Errorf("StartRegister = %d, want 21", decoded.StartRegister)
	}
	if decoded.RegisterCount != 2 {
		t.Errorf("RegisterCount = %d, want 2", decoded.RegisterCount)
	}
	if !decoded.CRCValid {
		t.Error("CRCValid = false, want true")
	}
	if decoded.Registers[21] != 0x1234 {
		t.Errorf("Registers[21] = %04x, want 1234", decoded.Registers[21])
	}
	if decoded.Registers[22] != 0x5678 {
		t.Errorf("Registers[22] = %04x, want 5678", decoded.Registers[22])
	}
}

func TestDecodeInvalidHeader(t *testing.T) {
	pkt := []byte{0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00}
	if Decode(pkt) != nil {
		t.Error("Decode should return nil for invalid header")
	}
}

func TestDecodeTooShort(t *testing.T) {
	pkt := []byte{0xa1, 0x1a, 0x05, 0x00}
	if Decode(pkt) != nil {
		t.Error("Decode should return nil for packet too short")
	}
}

func TestBuildReadHold(t *testing.T) {
	pkt := BuildReadHold("AAAAAAAAAA", "1234567890", 0, 40)

	// Verify length
	if len(pkt) != 38 {
		t.Fatalf("BuildReadHold length = %d, want 38", len(pkt))
	}

	// Verify header
	if pkt[0] != 0xa1 || pkt[1] != 0x1a {
		t.Errorf("Header = %02x %02x, want a1 1a", pkt[0], pkt[1])
	}

	// Verify protocol = 1
	protocol := binary.LittleEndian.Uint16(pkt[2:4])
	if protocol != 1 {
		t.Errorf("Protocol = %d, want 1", protocol)
	}

	// Verify frame length = 32
	frameLen := binary.LittleEndian.Uint16(pkt[4:6])
	if frameLen != 32 {
		t.Errorf("FrameLen = %d, want 32", frameLen)
	}

	// Verify unknown byte
	if pkt[6] != 0x01 {
		t.Errorf("Unknown = %02x, want 01", pkt[6])
	}

	// Verify TCP function
	if pkt[7] != FuncTranslatedData {
		t.Errorf("TCPFunction = %02x, want %02x", pkt[7], FuncTranslatedData)
	}

	// Verify datalog serial
	if string(pkt[8:18]) != "AAAAAAAAAA" {
		t.Errorf("Datalog = %q, want AAAAAAAAAA", string(pkt[8:18]))
	}

	// Verify data length = 18
	dataLen := binary.LittleEndian.Uint16(pkt[18:20])
	if dataLen != 18 {
		t.Errorf("DataLen = %d, want 18", dataLen)
	}

	// Verify address = 0 (client)
	if pkt[20] != 0x00 {
		t.Errorf("Address = %02x, want 00", pkt[20])
	}

	// Verify device function = ReadHolding
	if pkt[21] != ModbusReadHolding {
		t.Errorf("DeviceFunction = %02x, want %02x", pkt[21], ModbusReadHolding)
	}

	// Verify inverter serial
	if string(pkt[22:32]) != "1234567890" {
		t.Errorf("Inverter = %q, want 1234567890", string(pkt[22:32]))
	}

	// Verify start register = 0
	startReg := binary.LittleEndian.Uint16(pkt[32:34])
	if startReg != 0 {
		t.Errorf("StartRegister = %d, want 0", startReg)
	}

	// Verify count = 40
	count := binary.LittleEndian.Uint16(pkt[34:36])
	if count != 40 {
		t.Errorf("Count = %d, want 40", count)
	}

	// Verify CRC
	crcCalc := CRC16Modbus(pkt[20:36])
	crcInPkt := binary.LittleEndian.Uint16(pkt[36:38])
	if crcCalc != crcInPkt {
		t.Errorf("CRC mismatch: calculated %04x, in packet %04x", crcCalc, crcInPkt)
	}
}

func TestBuildReadInput(t *testing.T) {
	pkt := BuildReadInput("AAAAAAAAAA", "1234567890", 0, 40)

	if len(pkt) != 38 {
		t.Fatalf("BuildReadInput length = %d, want 38", len(pkt))
	}

	// Verify device function = ReadInput
	if pkt[21] != ModbusReadInput {
		t.Errorf("DeviceFunction = %02x, want %02x", pkt[21], ModbusReadInput)
	}
}

func TestBuildWriteSingle(t *testing.T) {
	pkt := BuildWriteSingle("AAAAAAAAAA", "1234567890", 67, 80)

	if len(pkt) != 38 {
		t.Fatalf("BuildWriteSingle length = %d, want 38", len(pkt))
	}

	// Verify device function = WriteSingle
	if pkt[21] != ModbusWriteSingle {
		t.Errorf("DeviceFunction = %02x, want %02x", pkt[21], ModbusWriteSingle)
	}

	// Verify register = 67
	reg := binary.LittleEndian.Uint16(pkt[32:34])
	if reg != 67 {
		t.Errorf("Register = %d, want 67", reg)
	}

	// Verify value = 80
	value := binary.LittleEndian.Uint16(pkt[34:36])
	if value != 80 {
		t.Errorf("Value = %d, want 80", value)
	}

	// Verify CRC
	crcCalc := CRC16Modbus(pkt[20:36])
	crcInPkt := binary.LittleEndian.Uint16(pkt[36:38])
	if crcCalc != crcInPkt {
		t.Errorf("CRC mismatch: calculated %04x, in packet %04x", crcCalc, crcInPkt)
	}
}

func TestBuildReadHoldRoundTrip(t *testing.T) {
	// Build a request packet
	pkt := BuildReadHold("AAAAAAAAAA", "1234567890", 21, 10)

	// Decode it
	decoded := Decode(pkt)
	if decoded == nil {
		t.Fatal("Decode returned nil for BuildReadHold packet")
	}

	if decoded.TCPFunction != FuncTranslatedData {
		t.Errorf("TCPFunction = %02x, want %02x", decoded.TCPFunction, FuncTranslatedData)
	}
	if decoded.Datalog != "AAAAAAAAAA" {
		t.Errorf("Datalog = %q, want AAAAAAAAAA", decoded.Datalog)
	}
	if decoded.ModbusFunc != ModbusReadHolding {
		t.Errorf("ModbusFunc = %02x, want %02x", decoded.ModbusFunc, ModbusReadHolding)
	}
	if decoded.Inverter != "1234567890" {
		t.Errorf("Inverter = %q, want 1234567890", decoded.Inverter)
	}
	if decoded.StartRegister != 21 {
		t.Errorf("StartRegister = %d, want 21", decoded.StartRegister)
	}
	// For client requests (Address=0), RegisterCount is set from offset 34-35
	if decoded.RegisterCount != 10 {
		t.Errorf("RegisterCount = %d, want 10", decoded.RegisterCount)
	}
}

func TestFindPackets(t *testing.T) {
	// Build two packets
	pkt1 := BuildReadHold("AAAAAAAAAA", "1234567890", 0, 40)
	pkt2 := BuildReadInput("AAAAAAAAAA", "1234567890", 0, 40)

	// Concatenate with some garbage
	buf := append([]byte{0x00, 0x00, 0x00}, pkt1...)
	buf = append(buf, []byte{0xFF, 0xFF}...)
	buf = append(buf, pkt2...)

	packets, consumed := FindPackets(buf)

	if len(packets) != 2 {
		t.Fatalf("FindPackets found %d packets, want 2", len(packets))
	}

	if packets[0].ModbusFunc != ModbusReadHolding {
		t.Errorf("Packet 0 ModbusFunc = %02x, want %02x", packets[0].ModbusFunc, ModbusReadHolding)
	}
	if packets[1].ModbusFunc != ModbusReadInput {
		t.Errorf("Packet 1 ModbusFunc = %02x, want %02x", packets[1].ModbusFunc, ModbusReadInput)
	}

	// Should have consumed all bytes
	expectedConsumed := len(buf)
	if consumed != expectedConsumed {
		t.Errorf("Consumed = %d, want %d", consumed, expectedConsumed)
	}
}

func TestFindPacketsIncomplete(t *testing.T) {
	pkt := BuildReadHold("AAAAAAAAAA", "1234567890", 0, 40)

	// Send incomplete packet (missing last 5 bytes)
	incomplete := pkt[:len(pkt)-5]

	packets, consumed := FindPackets(incomplete)

	if len(packets) != 0 {
		t.Errorf("FindPackets found %d packets for incomplete data, want 0", len(packets))
	}
	if consumed != 0 {
		t.Errorf("Consumed = %d for incomplete data, want 0", consumed)
	}
}

func TestSerialToBytes(t *testing.T) {
	// Test exact length
	b := serialToBytes("1234567890")
	if string(b[:]) != "1234567890" {
		t.Errorf("serialToBytes(10 chars) = %q", string(b[:]))
	}

	// Test short string (should pad with spaces)
	b = serialToBytes("ABC")
	if string(b[:]) != "ABC       " {
		t.Errorf("serialToBytes(3 chars) = %q, want 'ABC       '", string(b[:]))
	}

	// Test long string (should truncate)
	b = serialToBytes("12345678901234")
	if string(b[:]) != "1234567890" {
		t.Errorf("serialToBytes(14 chars) = %q, want '1234567890'", string(b[:]))
	}
}

func TestIsProtectedRegister(t *testing.T) {
	// Protected range is 25-53
	tests := []struct {
		reg      uint16
		expected bool
	}{
		{24, false},
		{25, true},
		{30, true},
		{53, true},
		{54, false},
		{60, false},
		{21, false},
	}

	for _, tc := range tests {
		got := IsProtectedRegister(tc.reg)
		if got != tc.expected {
			t.Errorf("IsProtectedRegister(%d) = %v, want %v", tc.reg, got, tc.expected)
		}
	}
}

func TestRegisterName(t *testing.T) {
	// Holding register
	name := RegisterName("holding", 21)
	if name != "Master Function Flags" {
		t.Errorf("RegisterName(holding, 21) = %q, want 'Master Function Flags'", name)
	}

	// Input register
	name = RegisterName("input", 5)
	if name != "SOC/SOH" {
		t.Errorf("RegisterName(input, 5) = %q, want 'SOC/SOH'", name)
	}

	// Unknown register
	name = RegisterName("holding", 9999)
	if name != "" {
		t.Errorf("RegisterName(holding, 9999) = %q, want ''", name)
	}

	// Invalid type
	name = RegisterName("invalid", 21)
	if name != "" {
		t.Errorf("RegisterName(invalid, 21) = %q, want ''", name)
	}
}

func TestWriteGuardProtectedRegister(t *testing.T) {
	guard := NewWriteGuard()

	err := guard.Check(30) // Protected register
	if err == nil {
		t.Error("WriteGuard.Check(30) should return error for protected register")
	}

	err = guard.Check(60) // Not protected
	if err != nil {
		t.Errorf("WriteGuard.Check(60) returned unexpected error: %v", err)
	}
}

func TestWriteGuardRateLimit(t *testing.T) {
	guard := NewWriteGuard()

	// Should allow first 10 writes
	for i := 0; i < 10; i++ {
		if err := guard.Check(60); err != nil {
			t.Errorf("Write %d should be allowed: %v", i+1, err)
		}
		guard.Record(60, 0, uint16(i), true)
	}

	// 11th write should be blocked
	err := guard.Check(60)
	if err == nil {
		t.Error("Write 11 should be rate limited")
	}
}

func TestDecodeWriteSingleConfirm(t *testing.T) {
	// Build a WriteSingle confirmation packet
	pkt := make([]byte, 38)
	pkt[0], pkt[1] = 0xa1, 0x1a
	binary.LittleEndian.PutUint16(pkt[2:4], 5)
	binary.LittleEndian.PutUint16(pkt[4:6], 32)
	pkt[6] = 0x01
	pkt[7] = FuncTranslatedData
	copy(pkt[8:18], "AAAAAAAAAA")
	binary.LittleEndian.PutUint16(pkt[18:20], 18)
	pkt[20] = 0x01 // inverter
	pkt[21] = ModbusWriteSingle
	copy(pkt[22:32], "1234567890")
	binary.LittleEndian.PutUint16(pkt[32:34], 60)  // register
	binary.LittleEndian.PutUint16(pkt[34:36], 100) // value
	crc := CRC16Modbus(pkt[20:36])
	binary.LittleEndian.PutUint16(pkt[36:38], crc)

	decoded := Decode(pkt)
	if decoded == nil {
		t.Fatal("Decode returned nil for WriteSingle confirm")
	}
	if decoded.RegisterType != "write_confirm" {
		t.Errorf("RegisterType = %q, want 'write_confirm'", decoded.RegisterType)
	}
	if decoded.RegisterCount != 1 {
		t.Errorf("RegisterCount = %d, want 1", decoded.RegisterCount)
	}
	if decoded.Registers[60] != 100 {
		t.Errorf("Registers[60] = %d, want 100", decoded.Registers[60])
	}
}

func TestDecodeWriteMultiConfirm(t *testing.T) {
	pkt := make([]byte, 38)
	pkt[0], pkt[1] = 0xa1, 0x1a
	binary.LittleEndian.PutUint16(pkt[2:4], 5)
	binary.LittleEndian.PutUint16(pkt[4:6], 32)
	pkt[6] = 0x01
	pkt[7] = FuncTranslatedData
	copy(pkt[8:18], "AAAAAAAAAA")
	binary.LittleEndian.PutUint16(pkt[18:20], 18)
	pkt[20] = 0x01 // inverter
	pkt[21] = ModbusWriteMulti
	copy(pkt[22:32], "1234567890")
	binary.LittleEndian.PutUint16(pkt[32:34], 60) // start register
	binary.LittleEndian.PutUint16(pkt[34:36], 5)  // count
	crc := CRC16Modbus(pkt[20:36])
	binary.LittleEndian.PutUint16(pkt[36:38], crc)

	decoded := Decode(pkt)
	if decoded == nil {
		t.Fatal("Decode returned nil for WriteMulti confirm")
	}
	if decoded.RegisterType != "write_confirm" {
		t.Errorf("RegisterType = %q, want 'write_confirm'", decoded.RegisterType)
	}
	if decoded.RegisterCount != 5 {
		t.Errorf("RegisterCount = %d, want 5", decoded.RegisterCount)
	}
}

func TestDecodeTranslatedDataTooShort(t *testing.T) {
	// TranslatedData packet that's too short for payload (< 36 bytes)
	pkt := make([]byte, 30)
	pkt[0], pkt[1] = 0xa1, 0x1a
	binary.LittleEndian.PutUint16(pkt[2:4], 5)
	binary.LittleEndian.PutUint16(pkt[4:6], 24) // frame len
	pkt[6] = 0x01
	pkt[7] = FuncTranslatedData
	copy(pkt[8:18], "AAAAAAAAAA")

	decoded := Decode(pkt)
	if decoded == nil {
		t.Fatal("Decode returned nil, want partial packet")
	}
	if decoded.TCPFunction != FuncTranslatedData {
		t.Errorf("TCPFunction = %02x, want %02x", decoded.TCPFunction, FuncTranslatedData)
	}
	// Should have no register data since packet is too short
	if decoded.RegisterType != "" {
		t.Errorf("RegisterType = %q, want empty for short packet", decoded.RegisterType)
	}
}

func TestDecodeUnknownTCPFunction(t *testing.T) {
	pkt := make([]byte, 18)
	pkt[0], pkt[1] = 0xa1, 0x1a
	binary.LittleEndian.PutUint16(pkt[2:4], 5)
	binary.LittleEndian.PutUint16(pkt[4:6], 12)
	pkt[6] = 0x01
	pkt[7] = 0xFF // unknown function
	copy(pkt[8:18], "AAAAAAAAAA")

	decoded := Decode(pkt)
	if decoded == nil {
		t.Fatal("Decode returned nil for unknown function")
	}
	if decoded.TCPFunction != 0xFF {
		t.Errorf("TCPFunction = %02x, want FF", decoded.TCPFunction)
	}
}

func TestDecodeUnknownModbusFunc(t *testing.T) {
	pkt := make([]byte, 38)
	pkt[0], pkt[1] = 0xa1, 0x1a
	binary.LittleEndian.PutUint16(pkt[2:4], 5)
	binary.LittleEndian.PutUint16(pkt[4:6], 32)
	pkt[6] = 0x01
	pkt[7] = FuncTranslatedData
	copy(pkt[8:18], "AAAAAAAAAA")
	binary.LittleEndian.PutUint16(pkt[18:20], 18)
	pkt[20] = 0x01
	pkt[21] = 0xFF // unknown Modbus function
	copy(pkt[22:32], "1234567890")

	decoded := Decode(pkt)
	if decoded == nil {
		t.Fatal("Decode returned nil")
	}
	if decoded.RegisterType != "" {
		t.Errorf("RegisterType = %q, want empty for unknown modbus func", decoded.RegisterType)
	}
}

func TestDecodeReadInputResponse(t *testing.T) {
	// Protocol=5 ReadInput response with VLB
	pkt := make([]byte, 41)
	pkt[0], pkt[1] = 0xa1, 0x1a
	binary.LittleEndian.PutUint16(pkt[2:4], 5)
	binary.LittleEndian.PutUint16(pkt[4:6], 35)
	pkt[6] = 0x01
	pkt[7] = FuncTranslatedData
	copy(pkt[8:18], "AAAAAAAAAA")
	binary.LittleEndian.PutUint16(pkt[18:20], 21)
	pkt[20] = 0x01 // inverter
	pkt[21] = ModbusReadInput
	copy(pkt[22:32], "1234567890")
	binary.LittleEndian.PutUint16(pkt[32:34], 0) // start reg
	pkt[34] = 4                                   // VLB: 4 bytes = 2 regs
	binary.LittleEndian.PutUint16(pkt[35:37], 0x04)
	binary.LittleEndian.PutUint16(pkt[37:39], 3500)
	crc := CRC16Modbus(pkt[20:39])
	binary.LittleEndian.PutUint16(pkt[39:41], crc)

	decoded := Decode(pkt)
	if decoded == nil {
		t.Fatal("Decode returned nil")
	}
	if decoded.RegisterType != "input" {
		t.Errorf("RegisterType = %q, want 'input'", decoded.RegisterType)
	}
	if decoded.RegisterCount != 2 {
		t.Errorf("RegisterCount = %d, want 2", decoded.RegisterCount)
	}
	if decoded.Registers[0] != 0x04 {
		t.Errorf("Registers[0] = %d, want 4", decoded.Registers[0])
	}
	if decoded.Registers[1] != 3500 {
		t.Errorf("Registers[1] = %d, want 3500", decoded.Registers[1])
	}
}

func TestDecodeVLBTooShort(t *testing.T) {
	// Protocol=5 packet too short for VLB
	pkt := make([]byte, 36)
	pkt[0], pkt[1] = 0xa1, 0x1a
	binary.LittleEndian.PutUint16(pkt[2:4], 5)
	binary.LittleEndian.PutUint16(pkt[4:6], 30)
	pkt[6] = 0x01
	pkt[7] = FuncTranslatedData
	copy(pkt[8:18], "AAAAAAAAAA")
	binary.LittleEndian.PutUint16(pkt[18:20], 16)
	pkt[20] = 0x01
	pkt[21] = ModbusReadHolding
	copy(pkt[22:32], "1234567890")
	binary.LittleEndian.PutUint16(pkt[32:34], 0)

	decoded := Decode(pkt)
	if decoded == nil {
		t.Fatal("Decode returned nil")
	}
	// Should have no registers because packet too short for VLB data
	if decoded.RegisterCount != 0 {
		t.Errorf("RegisterCount = %d, want 0 for short VLB packet", decoded.RegisterCount)
	}
}

func TestDecodeInvalidCRC(t *testing.T) {
	pkt := make([]byte, 41)
	pkt[0], pkt[1] = 0xa1, 0x1a
	binary.LittleEndian.PutUint16(pkt[2:4], 5)
	binary.LittleEndian.PutUint16(pkt[4:6], 35)
	pkt[6] = 0x01
	pkt[7] = FuncTranslatedData
	copy(pkt[8:18], "AAAAAAAAAA")
	binary.LittleEndian.PutUint16(pkt[18:20], 21)
	pkt[20] = 0x01
	pkt[21] = ModbusReadHolding
	copy(pkt[22:32], "1234567890")
	binary.LittleEndian.PutUint16(pkt[32:34], 0)
	pkt[34] = 4
	binary.LittleEndian.PutUint16(pkt[35:37], 100)
	binary.LittleEndian.PutUint16(pkt[37:39], 200)
	// Write bad CRC
	binary.LittleEndian.PutUint16(pkt[39:41], 0xDEAD)

	decoded := Decode(pkt)
	if decoded == nil {
		t.Fatal("Decode returned nil")
	}
	if decoded.CRCValid {
		t.Error("CRCValid = true, want false for bad CRC")
	}
}

func TestFindPacketsEmpty(t *testing.T) {
	packets, consumed := FindPackets([]byte{})
	if len(packets) != 0 || consumed != 0 {
		t.Errorf("FindPackets([]) = (%d packets, %d consumed)", len(packets), consumed)
	}
}

func TestFindPacketsSingle(t *testing.T) {
	pkt := BuildReadHold("AAAAAAAAAA", "1234567890", 0, 40)
	packets, consumed := FindPackets(pkt)
	if len(packets) != 1 {
		t.Errorf("FindPackets(single) = %d packets, want 1", len(packets))
	}
	if consumed != len(pkt) {
		t.Errorf("consumed = %d, want %d", consumed, len(pkt))
	}
}

func TestDecodeHeartbeatShort(t *testing.T) {
	// Heartbeat with exactly 8 bytes (too short for datalog)
	pkt := make([]byte, 8)
	pkt[0], pkt[1] = 0xa1, 0x1a
	binary.LittleEndian.PutUint16(pkt[2:4], 5)
	binary.LittleEndian.PutUint16(pkt[4:6], 2)
	pkt[6] = 0x01
	pkt[7] = FuncHeartbeat

	decoded := Decode(pkt)
	if decoded == nil {
		t.Fatal("Decode returned nil")
	}
	if decoded.Datalog != "" {
		t.Errorf("Datalog = %q, want empty for short heartbeat", decoded.Datalog)
	}
}

func TestBuildWriteSingleRoundTrip(t *testing.T) {
	pkt := BuildWriteSingle("AAAAAAAAAA", "1234567890", 60, 100)
	decoded := Decode(pkt)
	if decoded == nil {
		t.Fatal("Decode returned nil")
	}
	if decoded.RegisterType != "write_confirm" {
		t.Errorf("RegisterType = %q, want 'write_confirm'", decoded.RegisterType)
	}
	if decoded.Registers[60] != 100 {
		t.Errorf("Registers[60] = %d, want 100", decoded.Registers[60])
	}
}

func TestMasterFunctionBits(t *testing.T) {
	// Test that the bit constants are correct
	if MasterACChargeEnable != 1<<7 {
		t.Errorf("MasterACChargeEnable = %d, want %d", MasterACChargeEnable, 1<<7)
	}
	if MasterForcedDischarge != 1<<10 {
		t.Errorf("MasterForcedDischarge = %d, want %d", MasterForcedDischarge, 1<<10)
	}
	if MasterChargePriority != 1<<11 {
		t.Errorf("MasterChargePriority = %d, want %d", MasterChargePriority, 1<<11)
	}
	if MasterFeedInGrid != 1<<15 {
		t.Errorf("MasterFeedInGrid = %d, want %d", MasterFeedInGrid, 1<<15)
	}

	// Test combining bits
	flags := uint16(MasterACChargeEnable | MasterForcedDischarge)
	if flags&MasterACChargeEnable == 0 {
		t.Error("ACChargeEnable bit should be set")
	}
	if flags&MasterForcedDischarge == 0 {
		t.Error("ForcedDischarge bit should be set")
	}
	if flags&MasterChargePriority != 0 {
		t.Error("ChargePriority bit should not be set")
	}
}
