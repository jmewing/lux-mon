#!/usr/bin/env python3

import base64
import struct
import json
import argparse
from typing import Dict, Any, Tuple

def load_register_definitions() -> Dict[int, Any]:
    """Load register definitions from JSON file."""
    with open('doc/eg4_registers.json', 'r') as f:
        data = json.load(f)
        # Create a dictionary mapping register numbers to their definitions
        registers = {}
        # Store the summary information for register type determination
        summary = data.get('summary', {})
        input_range = summary.get('input_registers', {}).get('range', {'min': 0, 'max': 0})
        hold_range = summary.get('hold_registers', {}).get('range', {'min': 0, 'max': 0})
        
        for reg_type in data['registers']:
            if 'register_map' in reg_type:
                for reg in reg_type['register_map']:
                    if 'register_number' in reg:
                        reg_num = reg['register_number']
                        # Add register type information
                        reg['register_type'] = reg_type['register_type']
                        # Add range information
                        reg['is_input'] = input_range['min'] <= reg_num <= input_range['max']
                        reg['is_hold'] = hold_range['min'] <= reg_num <= hold_range['max']
                        registers[reg_num] = reg
        print(f"Loaded {len(registers)} register definitions")
        return registers

def decode_value_frame(value_frame):
    """Decode a base64 value frame string and analyze its structure."""
    try:
        # Decode base64 string
        decoded = base64.b64decode(value_frame)
        print(f"\nDecoded value frame length: {len(decoded)} bytes (0x{len(decoded):x})")
        
        # Print first 32 bytes in hex format
        print("\nFirst 32 bytes:")
        for i in range(0, min(32, len(decoded)), 16):
            hex_bytes = ' '.join(f'{b:02x}' for b in decoded[i:i+16])
            print(f"Offset {i:3d}: {hex_bytes}")
            
        # Analyze header structure
        print("\nHeader Analysis:")
        if len(decoded) >= 4:
            frame_id = decoded[0:2]
            data_len = decoded[2:4]
            print(f"Frame identifier: 0x{frame_id.hex()}")
            print(f"Data length: 0x{data_len.hex()} ({int.from_bytes(data_len, 'little')} bytes)")
            
        # Look for ASCII text in first 20 bytes
        ascii_text = ''
        for b in decoded[:20]:
            if 32 <= b <= 126:  # Printable ASCII range
                ascii_text += chr(b)
            else:
                ascii_text += '.'
        print(f"\nASCII text in first 20 bytes: {ascii_text}")
        
        # Extract device function from offset 18
        if len(decoded) >= 19:
            device_func = decoded[18]
            func_desc = {
                0x03: "ReadHold",
                0x04: "ReadInput",
                0x00: "Unknown",
                0x14: "Unknown"
            }.get(device_func, "Unknown")
            print(f"\nDevice function at offset 18: 0x{device_func:02x} ({func_desc})")
            
        # Look for potential register values
        print("\nPotential Register Values:")
        for i in range(20, min(32, len(decoded)-1), 2):
            if i+1 < len(decoded):
                value_le = int.from_bytes(decoded[i:i+2], 'little')
                value_be = int.from_bytes(decoded[i:i+2], 'big')
                print(f"Offset {i:3d}: LE={value_le:5d}, BE={value_be:5d}")
                
        return decoded[20:] if len(decoded) > 20 else b''
    except Exception as e:
        print(f"Error decoding value frame: {e}")
        return b''

def parse_register_value(data: bytes, reg_def: Dict[str, Any], offset: int) -> Any:
    """Parse a register value according to its definition."""
    if 'num_values' in reg_def:
        # Handle multi-value registers
        values = {}
        for value_map in reg_def['value_map']:
            if value_map['value_unit'] == 'bit':
                # Extract bits from the value
                mask = (1 << value_map['value_size']) - 1
                value = (int.from_bytes(data[offset:offset+2], 'little') >> value_map['value_location']) & mask
                if 'value_map' in value_map:
                    value = value_map['value_map'].get(str(value), value)
                values[value_map['shortname']] = value
            elif value_map['value_unit'] == 'byte':
                # Extract bytes from the value
                value = data[offset + value_map['value_location']]
                if 'value_map' in value_map:
                    value = value_map['value_map'].get(str(value), value)
                values[value_map['shortname']] = value
        return values
    else:
        # Handle single-value registers
        datatype = reg_def.get('datatype', 'uint16')
        if datatype == 'uint16':
            value = struct.unpack('<H', data[offset:offset+2])[0]
        elif datatype == 'float':
            value = struct.unpack('<f', data[offset:offset+4])[0]
        elif datatype == 'uint8':
            value = data[offset]
        else:
            value = struct.unpack('<H', data[offset:offset+2])[0]

        # Apply unit scale if specified
        if 'unit_scale' in reg_def:
            value *= float(reg_def['unit_scale'])

        # Apply value mapping if specified
        if 'value_map' in reg_def:
            value = reg_def['value_map'].get(str(value), value)

        return value

def process_portal_file(filename: str, registers: Dict[int, Any]):
    """Process a single portal file and print its register values."""
    print(f"\nProcessing file: {filename}")
    print("=" * 50)
    
    # Read valueFrame and startRegister from portal file
    with open(filename, 'r') as f:
        value_frame = None
        start_register = 0
        for line in f:
            if line.startswith('valueFrame'):
                value_frame = line.split('\t')[1].strip().strip('"')
            elif line.startswith('startRegister'):
                start_register = int(line.split('\t')[1].strip())
        
        if not value_frame:
            print(f"Error: No valueFrame found in {filename}")
            return
        print(f"Starting at register {start_register}")

    # Decode valueFrame
    data_hex = decode_value_frame(value_frame)
    
    # Skip header (looks like 4 bytes of header + serial number)
    offset = 0
    while offset < len(data_hex) and data_hex[offset] != '00':
        offset += 1
    offset += 1  # Skip the null terminator
    
    # Parse and display register values
    print("\nRegister Values:")
    print("-" * 50)
    
    register_number = start_register  # Start at the specified register number
    while offset < len(data_hex):
        try:
            if register_number in registers:
                reg_def = registers[register_number]
                value = parse_register_value(bytes.fromhex(data_hex[offset:]), reg_def, 0)
                
                # Format the output
                if isinstance(value, dict):
                    print(f"Register {register_number} ({reg_def['register_type']}) ({reg_def.get('description', 'Unknown')}):")
                    if 'num_values' in reg_def and reg_def.get('display_as') == 'flags':
                        # For flag registers, show each bit's meaning
                        raw_value = struct.unpack('<H', bytes.fromhex(data_hex[offset:offset+4])[0:2])[0]
                        print(f"  Raw value: {raw_value} (0x{raw_value:04X}, 0b{raw_value:016b})")
                        print("  Flags:")
                        for flag_name, flag_value in value.items():
                            print(f"    {flag_name}: {flag_value}")
                    else:
                        for k, v in value.items():
                            print(f"  {k}: {v}")
                else:
                    unit = reg_def.get('unit', '')
                    print(f"Register {register_number} ({reg_def['register_type']}) ({reg_def.get('description', 'Unknown')}): {value} {unit}")
                
                # Update offset based on register type
                if 'num_values' in reg_def:
                    offset += 2  # Multi-value registers use 2 bytes
                elif reg_def.get('datatype') == 'float':
                    offset += 4  # Float values use 4 bytes
                else:
                    offset += 2  # Default to 2 bytes for other types
            else:
                # Print unknown registers with both big-endian and little-endian interpretations
                value_le = struct.unpack('<H', bytes.fromhex(data_hex[offset:offset+4])[0:2])[0]  # little-endian
                value_be = struct.unpack('>H', bytes.fromhex(data_hex[offset:offset+4])[0:2])[0]  # big-endian
                print(f"Register unknown-{register_number} ({reg_def['register_type']}):")
                print(f"  LE: {value_le} (0x{value_le:04X}, 0b{value_le:016b})")
                print(f"  BE: {value_be} (0x{value_be:04X}, 0b{value_be:016b})")
                print(f"  Bytes at offset {offset}:")
                print(f"    Byte 0: {bytes.fromhex(data_hex[offset:offset+2])[0]} (0x{bytes.fromhex(data_hex[offset:offset+2])[0]:02X}, 0b{bytes.fromhex(data_hex[offset:offset+2])[0]:08b})")
                print(f"    Byte 1: {bytes.fromhex(data_hex[offset+2:offset+4])[0]} (0x{bytes.fromhex(data_hex[offset+2:offset+4])[0]:02X}, 0b{bytes.fromhex(data_hex[offset+2:offset+4])[0]:08b})")
                offset += 2  # Unknown registers use 2 bytes
            
            register_number += 1
            
        except Exception as e:
            print(f"Error at offset {offset}: {e}")
            break

def main():
    # Set up command line argument parsing
    parser = argparse.ArgumentParser(description='Parse EG4 portal files and display register values')
    parser.add_argument('files', nargs='+', help='One or more portal files to process')
    args = parser.parse_args()

    # Load register definitions
    registers = load_register_definitions()

    # Process each file
    for filename in args.files:
        process_portal_file(filename, registers)

if __name__ == '__main__':
    main() 
