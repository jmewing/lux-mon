#!/usr/bin/env python3
import yaml
import json
import socket
import struct
import time
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class EG4ModbusClient:
    """Custom Modbus client that handles EG4 protocol specifics."""
    def __init__(self, host: str, port: int = 8000, connect_timeout: float = 5.0, read_timeout: float = 30.0, delay_ms: int = 200, buffer_size: int = 16384):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.delay_ms = delay_ms
        self.buffer_size = max(16384, buffer_size)  # Ensure minimum 16KB buffer
        self.sock = None
        self.datalog_id = bytearray(10)  # Initialize with zeros
        self.inverter_serial = bytearray(10)  # Initialize with zeros
        self.last_heartbeat = 0
        self.logger = logging.getLogger(__name__)
        self.max_buffer_size = 65536  # 64KB max buffer size

    def connect(self) -> bool:
        """Establish connection to the inverter."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(self.connect_timeout)
            self.sock.connect((self.host, self.port))
            self.sock.settimeout(self.read_timeout)
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to {self.host}:{self.port}: {e}")
            return False

    def close(self):
        """Close the connection."""
        if self.sock:
            self.sock.close()
            self.sock = None

    def build_packet(self, register_address: int, count: int, is_input: bool = False) -> bytearray:
        """Build a packet to read registers.
        
        Args:
            register_address: Starting register address
            count: Number of registers to read
            is_input: True for input registers, False for holding registers
        """
        # Build data section
        data = bytearray()
        data.append(0x00)  # Source (0=client)
        data.append(0x04 if is_input else 0x03)  # Function code (4=read input registers, 3=read holding registers)
        data.extend(self.inverter_serial)  # Inverter serial (10 bytes)
        
        # Convert register address and count to bytes, ensuring they're in valid range
        data.extend((register_address & 0xFFFF).to_bytes(2, byteorder='little'))  # Register address
        data.extend((count & 0xFFFF).to_bytes(2, byteorder='little'))  # Register count
        
        # Calculate data section length
        data_len = len(data)
        self.logger.debug(f"Data section length: {data_len}")
        self.logger.debug(f"Data section: {data.hex()}")
        
        # Build header
        header = bytearray()
        header.extend(b'\xa1\x1a')  # Magic bytes
        header.extend((0x01).to_bytes(2, byteorder='little'))  # Protocol version (1 for read)
        header.extend((data_len + 12).to_bytes(2, byteorder='little'))  # Frame length (data_len + 12 for header)
        header.append(0x01)  # Unknown, always 1
        header.append(0xc3 if is_input else 0xc2)  # TCP function (0xc3=read input registers, 0xc2=read holding registers)
        header.extend(self.datalog_id)  # Datalog ID
        
        self.logger.debug(f"Header length: {len(header)}")
        self.logger.debug(f"Header: {header.hex()}")
        
        packet = header + data
        self.logger.debug(f"Full packet length: {len(packet)}")
        self.logger.debug(f"Full packet: {packet.hex()}")
        
        return packet

    def read_response(self) -> Optional[bytearray]:
        """Read response from the inverter."""
        try:
            # Read header
            header = self.sock.recv(self.buffer_size)
            if not header:
                self.logger.error("No header received")
                return None
                
            # Log raw header for debugging
            self.logger.debug(f"Raw header: {header.hex()}")
            
            # Try to find magic bytes in the response
            magic_pos = header.find(b'\xa1\x1a')
            if magic_pos == -1:
                # If magic bytes not found, try reading more data
                self.logger.debug("Magic bytes not found in initial header, reading more data")
                more_data = self.sock.recv(self.buffer_size)  # Read more data
                if more_data:
                    header = header + more_data
                    magic_pos = header.find(b'\xa1\x1a')
                    if magic_pos == -1:
                        self.logger.error("Magic bytes not found in extended response")
                        self.logger.debug(f"Extended response: {header.hex()}")
                        return None
                    # Adjust header to start from magic bytes
                    header = header[magic_pos:]
                else:
                    self.logger.error("No additional data received")
                    return None
            
            # Get frame length from 4 bytes after magic bytes
            frame_len = int.from_bytes(header[4:6], byteorder='little')
            self.logger.debug(f"Frame length: {frame_len}")
            
            # Log function code and error code
            function_code = header[7]
            self.logger.info(f"Received function code: 0x{function_code:02x}")
            
            # Check if this is a heartbeat packet (TCP function 0xc1)
            if function_code == 0xc1:
                self.logger.info("Received heartbeat packet")
                # Extract datalog ID from header
                datalog_id = header[8:18]
                self.logger.debug(f"Heartbeat datalog ID: {datalog_id.decode()}")
                
                # Build and send heartbeat response
                response = bytearray()
                response.extend(b'\xa1\x1a')  # Magic bytes
                response.extend((2).to_bytes(2, byteorder='little'))  # Protocol version 2
                response.extend((13).to_bytes(2, byteorder='little'))  # Frame length
                response.append(1)  # Unknown
                response.append(0xc1)  # TCP function (Heartbeat)
                response.extend(datalog_id)  # Datalog ID
                response.append(0)  # Zero byte for heartbeat
                
                self.logger.debug(f"Sending heartbeat response: {response.hex()}")
                self.sock.sendall(response)
                return None
            
            # Read remaining data
            remaining = frame_len - 8
            if remaining > 0:
                data = self.sock.recv(self.buffer_size)
                if not data:
                    self.logger.error("No data received after header")
                    return None
                self.logger.debug(f"Received {len(data)} bytes of data")
                
                # Log error code if present (first byte of data section)
                if len(data) > 0:
                    error_code = data[0]
                    error_msg = {
                        0x01: "Illegal function",
                        0x02: "Data address error",
                        0x03: "Data value out of bounds or wrong number of registers"
                    }.get(error_code, "Unknown error")
                    self.logger.info(f"Received error code: 0x{error_code:02x} ({error_msg})")
                    if error_code != 0:
                        self.logger.error(f"Error code indicates failure: 0x{error_code:02x} ({error_msg})")
                
                return data
            else:
                self.logger.error("Invalid frame length: {frame_len}")
                return None
            
        except socket.timeout:
            self.logger.error("Timeout reading response")
            return None
        except Exception as e:
            self.logger.error(f"Error reading response: {e}")
            return None

    def read_registers(self, start_reg: int, count: int, is_input: bool = False) -> Optional[List[int]]:
        """Read registers from the inverter.
        
        Args:
            start_reg: Starting register address
            count: Number of registers to read
            is_input: True for input registers, False for holding registers
        """
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                # Build and send the request packet
                packet = self.build_packet(start_reg, count, is_input)
                self.sock.sendall(packet)
                
                # Read the response
                response = self.read_response()
                if response is None:
                    retry_count += 1
                    if retry_count < max_retries:
                        self.logger.warning(f"Retry {retry_count}/{max_retries} for registers {start_reg}-{start_reg+count-1}")
                        time.sleep(1)  # Wait before retry
                    continue
                    
                # Process the response
                if len(response) < 2:  # Need at least 2 bytes for a register value
                    raise Exception(f"Response too short: {len(response)} bytes")
                    
                # Extract register values - each register is 2 bytes in little-endian order
                values = []
                num_registers = len(response) // 2
                for i in range(num_registers):
                    if i * 2 + 1 < len(response):
                        value = int.from_bytes(response[i*2:i*2+2], byteorder='little')
                        values.append(value)
                
                # Add delay between reads
                time.sleep(self.delay_ms / 1000)  # Convert ms to seconds
                
                return values
                
            except socket.timeout:
                self.logger.error(f"Timeout reading registers {start_reg}-{start_reg+count-1}")
                retry_count += 1
                if retry_count < max_retries:
                    self.logger.warning(f"Retry {retry_count}/{max_retries} after timeout")
                    time.sleep(1)  # Wait before retry
            except Exception as e:
                self.logger.error(f"Error reading registers {start_reg}-{start_reg+count-1}: {e}")
                retry_count += 1
                if retry_count < max_retries:
                    self.logger.warning(f"Retry {retry_count}/{max_retries} after error")
                    time.sleep(1)  # Wait before retry
                    
        return None

def decode_register_value(reg_def: Dict[str, Any], value: int) -> Any:
    """Decode a register value based on its definition."""
    if not reg_def:
        return value
        
    # Apply unit scale
    scaled_value = value * reg_def.get('unit_scale', 1.0)
    
    # Handle different display types
    if reg_def.get('display_as') == 'enum':
        for enum_value in reg_def.get('enum_values', []):
            if enum_value['value'] == value:
                return enum_value['name']
        return value
    elif reg_def.get('display_as') == 'flags':
        flags = []
        for flag in reg_def.get('flags', []):
            if value & (1 << flag['bit']):
                flags.append(flag['name'])
        return flags
    elif reg_def.get('display_as') == 'fields':
        fields = {}
        for field in reg_def.get('fields', []):
            byte_value = (value >> (field['byte'] * 8)) & 0xFF
            fields[field['name']] = byte_value
        return fields
        
    return scaled_value

def get_register_definition(registers: Dict[str, Any], reg_type: str, reg_num: int) -> Optional[Dict[str, Any]]:
    """Get register definition from schema."""
    for reg_group in registers['registers']:
        if reg_group['register_type'] == reg_type:
            for reg in reg_group['register_map']:
                if reg['register_number'] == reg_num:
                    return reg
    return None

def read_inverter_data(client: EG4ModbusClient, registers: Dict[str, Any]) -> Dict[str, Any]:
    """Read and decode all registers from inverter."""
    data = {}
    block_size = 40  # Fixed block size of 40 registers
    
    # Read holding registers (0-1000)
    logger.info("Reading holding registers...")
    for start_reg in range(0, 1001, block_size):
        logger.debug(f"Reading holding registers {start_reg}-{start_reg + block_size - 1}")
        hold_values = client.read_registers(start_reg, block_size, is_input=False)
        if hold_values:
            for i, value in enumerate(hold_values):
                reg_num = start_reg + i
                reg_def = get_register_definition(registers, 'hold', reg_num)
                if reg_def:
                    decoded_value = decode_register_value(reg_def, value)
                    data[reg_def['shortname']] = {
                        'name': reg_def['name'],
                        'value': decoded_value,
                        'unit': reg_def.get('unit', ''),
                        'description': reg_def['description']
                    }
                    print(f"Holding Register {reg_num:4d}: {reg_def['shortname']:<30} = {value:5d} (0x{value:04x}) -> {decoded_value}")
                else:
                    print(f"Holding Register {reg_num:4d}: <unknown> = {value:5d} (0x{value:04x})")
    
    # Read input registers (0-1000)
    logger.info("Reading input registers...")
    for start_reg in range(0, 1001, block_size):
        logger.debug(f"Reading input registers {start_reg}-{start_reg + block_size - 1}")
        input_values = client.read_registers(start_reg, block_size, is_input=True)
        if input_values:
            for i, value in enumerate(input_values):
                reg_num = start_reg + i
                reg_def = get_register_definition(registers, 'input', reg_num)
                if reg_def:
                    decoded_value = decode_register_value(reg_def, value)
                    data[reg_def['shortname']] = {
                        'name': reg_def['name'],
                        'value': decoded_value,
                        'unit': reg_def.get('unit', ''),
                        'description': reg_def['description']
                    }
                    print(f"Input Register {reg_num:4d}: {reg_def['shortname']:<30} = {value:5d} (0x{value:04x}) -> {decoded_value}")
                else:
                    print(f"Input Register {reg_num:4d}: <unknown> = {value:5d} (0x{value:04x})")
    
    return data

def bcd_to_int(bcd: int) -> int:
    """Convert a BCD value to integer."""
    return ((bcd >> 4) * 10) + (bcd & 0x0F)

def check_time_delta(client: EG4ModbusClient) -> Optional[float]:
    """Check the time delta between system and inverter time.
    
    This function reads the time from holding registers 12-14 (6 bytes total).
    The time is stored in BCD format in holding registers, not input registers.
    Each register contains 2 bytes in little-endian order, with each byte being a BCD value.
    
    Returns:
        float: Time difference in seconds (positive means inverter is ahead)
        None: If time check failed
    """
    try:
        # Read time from holding registers 12-14 (6 bytes total)
        # Note: We use holding registers (is_input=False) as the time is stored in holding registers
        values = client.read_registers(12, 3, is_input=False)  # Read 3 holding registers (6 bytes)
        if not values or len(values) < 3:
            logger.error("Failed to read time holding registers")
            return None
            
        # Log raw values for debugging
        logger.debug(f"Raw time values from holding registers: {values}")
        
        # Each register contains 2 bytes in little-endian order
        # We need to handle each byte as a separate BCD value
        # Register 12: [year_high, year_low, month_high, month_low]
        # Register 13: [day_high, day_low, hour_high, hour_low]
        # Register 14: [minute_high, minute_low, second_high, second_low]
        
        # Extract and decode each BCD value
        year = bcd_to_int(values[0] & 0xFF) + (bcd_to_int((values[0] >> 8) & 0xFF) * 100)  # Years since 2000
        month = bcd_to_int(values[1] & 0xFF)
        day = bcd_to_int((values[1] >> 8) & 0xFF)
        hour = bcd_to_int(values[2] & 0xFF)
        minute = bcd_to_int((values[2] >> 8) & 0xFF)
        second = bcd_to_int(values[3] & 0xFF)
        
        # Log decoded values for debugging
        logger.debug(f"Decoded time values: year={year}, month={month}, day={day}, hour={hour}, minute={minute}, second={second}")
        
        # Convert inverter time to datetime
        from datetime import datetime
        inverter_time = datetime(2000 + year, month, day, hour, minute, second)
        
        # Get current system time
        system_time = datetime.now()
        
        # Calculate time difference in seconds
        time_diff = (inverter_time - system_time).total_seconds()
        
        logger.info(f"Time delta for inverter {client.host}: {time_diff:.1f} seconds")
        logger.info(f"System time: {system_time}")
        logger.info(f"Inverter time (from holding registers): {inverter_time}")
        
        return time_diff
        
    except Exception as e:
        logger.error(f"Error checking time delta from holding registers: {e}")
        return None

def main():
    # Load configuration
    config_path = Path('config.yaml')
    if not config_path.exists():
        logger.error("config.yaml not found")
        return
        
    with open(config_path) as f:
        config = yaml.safe_load(f)
        
    # Load register schema
    schema_path = Path('doc/eg4_registers.json')
    if not schema_path.exists():
        logger.error("eg4_registers.json not found")
        return
        
    with open(schema_path) as f:
        registers = json.load(f)
        
    # Process each inverter
    for inverter in config.get('inverters', []):
        if not inverter.get('enabled', True):
            continue
            
        host = inverter['host']
        port = inverter.get('port', 8000)
        delay_ms = inverter.get('delay_ms', 200)
        
        logger.info(f"Connecting to inverter at {host}:{port}")
        
        client = EG4ModbusClient(host, port, delay_ms=delay_ms, buffer_size=256)
        if not client.connect():
            logger.error(f"Failed to connect to {host}:{port}")
            continue
            
        try:
            # Check time delta first
            time_diff = check_time_delta(client)
            if time_diff is not None:
                if abs(time_diff) > 600:  # More than 10 minutes difference
                    logger.warning(f"Large time difference detected for {host}: {time_diff:.1f} seconds")
                elif abs(time_diff) > 30:  # More than 30 seconds difference
                    logger.warning(f"Significant time difference detected for {host}: {time_diff:.1f} seconds")
            
            # Read all registers
            data = read_inverter_data(client, registers)
            
            # Print summary
            print(f"\nInverter {host} Summary:")
            print("=" * 80)
            for reg_name, reg_data in data.items():
                print(f"{reg_name:<30} = {reg_data['value']} {reg_data['unit']}")
                
        except Exception as e:
            logger.error(f"Error reading from {host}:{port}: {e}")
        finally:
            client.close()
            logger.info(f"Connection to {host}:{port} closed")

if __name__ == '__main__':
    main() 
