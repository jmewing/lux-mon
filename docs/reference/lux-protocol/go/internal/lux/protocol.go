package lux

import "encoding/binary"

// Header is the magic bytes that start every LuxPower TCP frame.
var Header = [2]byte{0xa1, 0x1a}

// TCP function codes
const (
	FuncHeartbeat      = 0xC1
	FuncTranslatedData = 0xC2
	FuncReadParam      = 0xC3
	FuncWriteParam     = 0xC4
)

// Modbus function codes
const (
	ModbusReadHolding  = 0x03
	ModbusReadInput    = 0x04
	ModbusWriteSingle  = 0x06
	ModbusWriteMulti   = 0x10
)

// Packet represents a decoded LuxPower TCP frame.
type Packet struct {
	Protocol    uint16
	FrameLen    uint16
	TCPFunction byte
	Datalog     string
	Inverter    string // empty for heartbeats

	// TranslatedData fields
	DataLength uint16 // inner data length at offset 18-19
	Address    byte   // 0=client, 1=inverter

	// Modbus payload (for TranslatedData)
	ModbusFunc    byte
	RegisterType  string // "holding", "input", or "write_confirm"
	StartRegister uint16
	RegisterCount uint16
	Registers     map[uint16]uint16
	CRCValid      bool
}

// CRC16Modbus computes the CRC-16/Modbus checksum.
func CRC16Modbus(data []byte) uint16 {
	crc := uint16(0xFFFF)
	for _, b := range data {
		crc ^= uint16(b)
		for i := 0; i < 8; i++ {
			if crc&0x0001 != 0 {
				crc = (crc >> 1) ^ 0xA001
			} else {
				crc >>= 1
			}
		}
	}
	return crc
}

// Decode parses a raw LuxPower TCP frame into a Packet.
// Returns nil if the data is too short or has an invalid header.
//
// Frame layout (verified against Python decoder and PROTOCOL.md):
//
//	[0-1]   Header: 0xa1 0x1a
//	[2-3]   Protocol version: u16 LE
//	[4-5]   Frame length: u16 LE (bytes after offset 6)
//	[6]     Unknown (always 0x01)
//	[7]     TCP Function: 0xC1=Heartbeat, 0xC2=TranslatedData
//	[8-17]  Datalog serial: 10 bytes ASCII
//
// For TranslatedData (0xC2), inner payload at offset 18:
//
//	[18-19] Data length: u16 LE
//	[20]    Address: 0=client, 1=inverter
//	[21]    Device function: 3=ReadHold, 4=ReadInput, 6=WriteSingle, 16=WriteMulti
//	[22-31] Inverter serial: 10 bytes ASCII
//	[32-33] Start register: u16 LE
//	[34]    Value length byte (conditional - present for protocol!=1 reads from inverter)
//	[35+]   Register values: u16 LE each
//	[-2:]   CRC-16/Modbus on data[20:len-2]
func Decode(data []byte) *Packet {
	if len(data) < 8 {
		return nil
	}
	if data[0] != Header[0] || data[1] != Header[1] {
		return nil
	}

	p := &Packet{
		Protocol:    binary.LittleEndian.Uint16(data[2:4]),
		FrameLen:    binary.LittleEndian.Uint16(data[4:6]),
		TCPFunction: data[7], // offset 7, not 6
	}

	switch p.TCPFunction {
	case FuncHeartbeat:
		if len(data) >= 18 {
			p.Datalog = string(data[8:18])
		}

	case FuncTranslatedData:
		if len(data) < 36 {
			return p
		}
		p.Datalog = string(data[8:18])
		p.DataLength = binary.LittleEndian.Uint16(data[18:20])
		p.Address = data[20]
		p.ModbusFunc = data[21]
		p.Inverter = string(data[22:32])
		p.StartRegister = binary.LittleEndian.Uint16(data[32:34])

		p.decodeTranslatedPayload(data)
	}

	return p
}

// decodeTranslatedPayload decodes the register data from a TranslatedData packet.
// Handles VLB (Value Length Byte) conditional logic per protocol spec.
func (p *Packet) decodeTranslatedPayload(data []byte) {
	switch p.ModbusFunc {
	case ModbusReadHolding:
		p.RegisterType = "holding"
	case ModbusReadInput:
		p.RegisterType = "input"
	case ModbusWriteSingle:
		p.RegisterType = "write_confirm"
		// WriteSingle has no register data to decode, just the echoed value
		if len(data) >= 36 {
			p.RegisterCount = 1
			p.Registers = map[uint16]uint16{
				p.StartRegister: binary.LittleEndian.Uint16(data[34:36]),
			}
		}
		return
	case ModbusWriteMulti:
		p.RegisterType = "write_confirm"
		// WriteMulti confirmation has register count at offset 34
		if len(data) >= 36 {
			p.RegisterCount = binary.LittleEndian.Uint16(data[34:36])
		}
		return
	default:
		return
	}

	// Client-originated read requests (Address=0, protocol=1) have register count
	// at offset 34-35, not register data. Detect and handle separately.
	if p.Address == 0 && p.Protocol == 1 {
		// Request packet: [32-33] start reg, [34-35] count, [36-37] CRC
		if len(data) < 38 {
			return
		}
		p.RegisterCount = binary.LittleEndian.Uint16(data[34:36])
		// Verify CRC on data frame [20:36]
		crcData := data[20:36]
		crcCalc := CRC16Modbus(crcData)
		crcRecv := binary.LittleEndian.Uint16(data[36:38])
		p.CRCValid = crcCalc == crcRecv
		return
	}

	// VLB (Value Length Byte) is present for protocol != 1 with read functions.
	hasVLB := p.Protocol != 1

	var valueLen int
	var valuesStart int

	if hasVLB {
		if len(data) < 37 {
			return
		}
		valueLen = int(data[34])
		valuesStart = 35
	} else {
		// Protocol=1 response - no VLB, values start at 34
		valuesStart = 34
		valueLen = len(data) - valuesStart - 2 // subtract CRC
		if valueLen < 0 {
			return
		}
	}

	if len(data) < valuesStart+valueLen+2 {
		return
	}

	p.RegisterCount = uint16(valueLen / 2)

	// Verify CRC: scope is data[20:len-2] (from address byte to before CRC)
	crcData := data[20 : len(data)-2]
	crcCalc := CRC16Modbus(crcData)
	crcRecv := binary.LittleEndian.Uint16(data[len(data)-2:])
	p.CRCValid = crcCalc == crcRecv

	// Decode register values (LE u16 each)
	regData := data[valuesStart : valuesStart+valueLen]
	p.Registers = make(map[uint16]uint16, p.RegisterCount)
	for i := 0; i < valueLen; i += 2 {
		reg := p.StartRegister + uint16(i/2)
		val := binary.LittleEndian.Uint16(regData[i : i+2])
		p.Registers[reg] = val
	}
}

// FindPackets scans a byte buffer for complete LuxPower TCP frames.
// Returns decoded packets and the number of bytes consumed.
func FindPackets(buf []byte) ([]*Packet, int) {
	var packets []*Packet
	consumed := 0
	pos := 0

	for pos < len(buf)-6 {
		if buf[pos] != Header[0] || buf[pos+1] != Header[1] {
			pos++
			consumed = pos
			continue
		}
		frameLen := int(binary.LittleEndian.Uint16(buf[pos+4 : pos+6]))
		totalLen := 6 + frameLen
		if pos+totalLen > len(buf) {
			break // incomplete packet
		}
		if pkt := Decode(buf[pos : pos+totalLen]); pkt != nil {
			packets = append(packets, pkt)
		}
		pos += totalLen
		consumed = pos
	}

	return packets, consumed
}

// serialToBytes converts a serial string to a fixed 10-byte array.
// Pads with spaces if too short, truncates if too long.
func serialToBytes(s string) [10]byte {
	var b [10]byte
	for i := 0; i < 10; i++ {
		if i < len(s) {
			b[i] = s[i]
		} else {
			b[i] = ' '
		}
	}
	return b
}

// buildReadRequest builds a ReadHold or ReadInput request packet.
// Uses protocol=1 (no VLB) per spec for client requests.
//
// Packet layout (38 bytes total):
//
//	[0-1]   Header: 0xa1 0x1a
//	[2-3]   Protocol: 1 (LE u16)
//	[4-5]   Frame length: 32 (LE u16)
//	[6]     Unknown: 0x01
//	[7]     TCP function: 0xC2 (TranslatedData)
//	[8-17]  Datalog serial (10 bytes)
//	[18-19] Data length: 18 (LE u16)
//	[20]    Address: 0x00 (client)
//	[21]    Device function (0x03 or 0x04)
//	[22-31] Inverter serial (10 bytes)
//	[32-33] Start register (LE u16)
//	[34-35] Register count (LE u16)
//	[36-37] CRC-16/Modbus over bytes [20:36]
func buildReadRequest(datalog, inverter string, devFunc byte, startReg, count uint16) []byte {
	pkt := make([]byte, 38)

	// Header
	pkt[0], pkt[1] = Header[0], Header[1]

	// Protocol = 1 (for requests)
	binary.LittleEndian.PutUint16(pkt[2:4], 1)

	// Frame length = 32 (38 - 6)
	binary.LittleEndian.PutUint16(pkt[4:6], 32)

	// Unknown byte
	pkt[6] = 0x01

	// TCP function
	pkt[7] = FuncTranslatedData

	// Datalog serial
	datalogBytes := serialToBytes(datalog)
	copy(pkt[8:18], datalogBytes[:])

	// Data length = 18 (addr + func + serial + reg + count + crc = 1+1+10+2+2+2)
	binary.LittleEndian.PutUint16(pkt[18:20], 18)

	// Address (client = 0)
	pkt[20] = 0x00

	// Device function
	pkt[21] = devFunc

	// Inverter serial
	inverterBytes := serialToBytes(inverter)
	copy(pkt[22:32], inverterBytes[:])

	// Start register
	binary.LittleEndian.PutUint16(pkt[32:34], startReg)

	// Register count
	binary.LittleEndian.PutUint16(pkt[34:36], count)

	// CRC over data frame [20:36]
	crc := CRC16Modbus(pkt[20:36])
	binary.LittleEndian.PutUint16(pkt[36:38], crc)

	return pkt
}

// BuildReadHold builds a ReadHold (function 0x03) request packet.
func BuildReadHold(datalog, inverter string, startReg, count uint16) []byte {
	return buildReadRequest(datalog, inverter, ModbusReadHolding, startReg, count)
}

// BuildReadInput builds a ReadInput (function 0x04) request packet.
func BuildReadInput(datalog, inverter string, startReg, count uint16) []byte {
	return buildReadRequest(datalog, inverter, ModbusReadInput, startReg, count)
}

// BuildWriteSingle builds a WriteSingle (function 0x06) request packet.
// Uses protocol=1 (no VLB) per spec for client requests.
//
// Packet layout (38 bytes total):
//
//	[0-1]   Header: 0xa1 0x1a
//	[2-3]   Protocol: 1 (LE u16)
//	[4-5]   Frame length: 32 (LE u16)
//	[6]     Unknown: 0x01
//	[7]     TCP function: 0xC2 (TranslatedData)
//	[8-17]  Datalog serial (10 bytes)
//	[18-19] Data length: 18 (LE u16)
//	[20]    Address: 0x00 (client)
//	[21]    Device function: 0x06 (WriteSingle)
//	[22-31] Inverter serial (10 bytes)
//	[32-33] Register number (LE u16)
//	[34-35] Value (LE u16)
//	[36-37] CRC-16/Modbus over bytes [20:36]
func BuildWriteSingle(datalog, inverter string, reg, value uint16) []byte {
	pkt := make([]byte, 38)

	// Header
	pkt[0], pkt[1] = Header[0], Header[1]

	// Protocol = 1 (for requests)
	binary.LittleEndian.PutUint16(pkt[2:4], 1)

	// Frame length = 32 (38 - 6)
	binary.LittleEndian.PutUint16(pkt[4:6], 32)

	// Unknown byte
	pkt[6] = 0x01

	// TCP function
	pkt[7] = FuncTranslatedData

	// Datalog serial
	datalogBytes := serialToBytes(datalog)
	copy(pkt[8:18], datalogBytes[:])

	// Data length = 18
	binary.LittleEndian.PutUint16(pkt[18:20], 18)

	// Address (client = 0)
	pkt[20] = 0x00

	// Device function
	pkt[21] = ModbusWriteSingle

	// Inverter serial
	inverterBytes := serialToBytes(inverter)
	copy(pkt[22:32], inverterBytes[:])

	// Register number
	binary.LittleEndian.PutUint16(pkt[32:34], reg)

	// Value
	binary.LittleEndian.PutUint16(pkt[34:36], value)

	// CRC over data frame [20:36]
	crc := CRC16Modbus(pkt[20:36])
	binary.LittleEndian.PutUint16(pkt[36:38], crc)

	return pkt
}
