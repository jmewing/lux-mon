#!/usr/bin/env python3
"""
Decode LuxPower TCP v5 packets from EG4 18kPV WiFi dongle.

Protocol layout (verified against live wire data + lxp-bridge Rust source):
  Outer frame:
    [0-1]   Header: 0xa1 0x1a
    [2-3]   Protocol version: u16 LE (5 for current firmware)
    [4-5]   Frame length: u16 LE (bytes after this field)
    [6]     Unknown (always 0x01)
    [7]     TCP Function: 0xc1=Heartbeat, 0xc2=TranslatedData, 0xc3=ReadParam, 0xc4=WriteParam
    [8-17]  Datalog serial: 10 bytes ASCII

  For TranslatedData (0xc2), inner payload at offset 18:
    [0-1]   Inner data length: u16 LE
    [2]     Address: 0=from dongle/client, 1=from inverter
    [3]     Device function: 3=ReadHold, 4=ReadInput, 6=WriteSingle, 16=WriteMulti
    [4-13]  Inverter serial: 10 bytes ASCII
    [14-15] Start register: u16 LE
    [16]    Value length in bytes (present for protocol v5 ReadHold/ReadInput)
    [17..]  Register data: u16 LE values
    [-2:]   CRC-16/Modbus on inner[2:-2]

Usage:
    python3 decode_capture.py [--host HOST] [--port PORT] [--duration SECONDS]
"""

import socket
import struct
import time
import argparse
import json
from datetime import datetime


HEADER = bytes([0xa1, 0x1a])

TCP_FUNCTIONS = {
    0xc1: 'Heartbeat',
    0xc2: 'TranslatedData',
    0xc3: 'ReadParam',
    0xc4: 'WriteParam',
}

DEV_FUNCTIONS = {
    3: 'ReadHold',
    4: 'ReadInput',
    6: 'WriteSingle',
    16: 'WriteMulti',
}

# Register names from lxp-bridge packet.rs (LE u16 unless noted)
# Holding registers: configuration (read/write)
HOLDING_NAMES = {
    0: "hold_soc_target",
    21: "register_21_flags",
    40: "device_type",
    60: "date_year", 61: "date_month_day", 62: "date_hour_min", 63: "date_sec",
    64: "charge_power_pct", 65: "discharge_power_pct",
    66: "ac_charge_power_pct", 67: "ac_charge_soc_limit",
    68: "charge_1_start_h", 69: "charge_1_start_m",
    70: "charge_1_end_h", 71: "charge_1_end_m",
    72: "charge_2_start_h", 73: "charge_2_start_m",
    74: "charge_priority_power_pct", 75: "charge_priority_soc_limit",
    76: "charge_2_end_h", 77: "charge_2_end_m",
    78: "charge_3_start_h", 79: "charge_3_start_m",
    80: "charge_3_end_h", 81: "charge_3_end_m",
    83: "forced_dischg_soc_limit",
    84: "dischg_1_start_h", 85: "dischg_1_start_m",
    86: "dischg_1_end_h", 87: "dischg_1_end_m",
    88: "dischg_2_start_h", 89: "dischg_2_start_m",
    90: "dischg_2_end_h", 91: "dischg_2_end_m",
    92: "dischg_3_start_h", 93: "dischg_3_start_m",
    94: "dischg_3_end_h", 95: "dischg_3_end_m",
    105: "dischg_cutoff_soc",
    110: "register_110_flags",
    125: "eps_dischg_cutoff_soc",
    160: "ac_charge_start_soc", 161: "ac_charge_end_soc",
}

# Input registers: live data (read-only)
# Scaling from lxp-bridge: div10 = /10.0, div100 = /100.0, div1000 = /1000.0
INPUT_NAMES = {
    0: ("status", None),
    1: ("v_pv_1", 10), 2: ("v_pv_2", 10), 3: ("v_pv_3", 10),
    4: ("v_bat", 10),
    5: ("soc_soh", None),  # low byte = soc, high byte = soh
    6: ("internal_fault", None),
    7: ("p_pv_1", None), 8: ("p_pv_2", None), 9: ("p_pv_3", None),
    10: ("p_charge", None), 11: ("p_discharge", None),
    12: ("v_ac_r", 10), 13: ("v_ac_s", 10), 14: ("v_ac_t", 10),
    15: ("f_ac", 100),
    16: ("p_inv", None), 17: ("p_rec", None),
    18: ("i_inv_rms", None),
    19: ("pf", 1000),
    20: ("v_eps_r", 10), 21: ("v_eps_s", 10), 22: ("v_eps_t", 10),
    23: ("f_eps", 100),
    24: ("p_eps", None), 25: ("s_eps", None),
    26: ("p_to_grid", None), 27: ("p_to_user", None),
    28: ("e_pv_day_1", 10), 29: ("e_pv_day_2", 10), 30: ("e_pv_day_3", 10),
    31: ("e_inv_day", 10), 32: ("e_rec_day", 10),
    33: ("e_chg_day", 10), 34: ("e_dischg_day", 10),
    35: ("e_eps_day", 10),
    36: ("e_to_grid_day", 10), 37: ("e_to_user_day", 10),
    38: ("v_bus_1", 10), 39: ("v_bus_2", 10),
    # 40-59: all-time energy (u32 pairs, div10)
    40: ("e_pv_all_1_lo", None), 41: ("e_pv_all_1_hi", None),
    42: ("e_pv_all_2_lo", None), 43: ("e_pv_all_2_hi", None),
    44: ("e_pv_all_3_lo", None), 45: ("e_pv_all_3_hi", None),
    46: ("e_inv_all_lo", None), 47: ("e_inv_all_hi", None),
    48: ("e_rec_all_lo", None), 49: ("e_rec_all_hi", None),
    50: ("e_chg_all_lo", None), 51: ("e_chg_all_hi", None),
    52: ("e_dischg_all_lo", None), 53: ("e_dischg_all_hi", None),
    54: ("e_eps_all_lo", None), 55: ("e_eps_all_hi", None),
    56: ("e_to_grid_all_lo", None), 57: ("e_to_grid_all_hi", None),
    58: ("e_to_user_all_lo", None), 59: ("e_to_user_all_hi", None),
    # 60+: fault/warning/temperature
    60: ("fault_code_lo", None), 61: ("fault_code_hi", None),
    62: ("warning_code_lo", None), 63: ("warning_code_hi", None),
    64: ("t_inner", None), 65: ("t_rad_1", None), 66: ("t_rad_2", None),
    67: ("t_bat", None),
    69: ("runtime_lo", None), 70: ("runtime_hi", None),
    # 80+: BMS
    89: ("max_chg_curr", 10), 90: ("max_dischg_curr", 10),
    91: ("charge_volt_ref", 10), 92: ("dischg_cut_volt", 10),
    93: ("bat_status_0", None), 94: ("bat_status_1", None),
    95: ("bat_status_2", None), 96: ("bat_status_3", None),
    97: ("bat_status_4", None), 98: ("bat_status_5", None),
    99: ("bat_status_6", None), 100: ("bat_status_7", None),
    101: ("bat_status_8", None), 102: ("bat_status_9", None),
    103: ("bat_status_inv", None),
    104: ("bat_count", None), 105: ("bat_capacity_ah", None),
    106: ("bat_current", 100),
    107: ("bms_event_1", None), 108: ("bms_event_2", None),
    109: ("max_cell_voltage", 1000), 110: ("min_cell_voltage", 1000),
    111: ("max_cell_temp", 10), 112: ("min_cell_temp", 10),
    114: ("cycle_count", None),
    115: ("vbat_inv", 10),
}


def crc16_modbus(data: bytes) -> int:
    """CRC-16/Modbus."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def format_reg_value(name, raw, divisor):
    """Format a register value with optional scaling."""
    if divisor:
        return f"{raw / divisor:.{len(str(divisor))-1}f}"
    return str(raw)


def decode_packet(pkt: bytes) -> dict:
    """Decode a single LuxPower TCP v5 frame."""
    if len(pkt) < 18:
        return None

    if pkt[0:2] != HEADER:
        return None

    protocol = struct.unpack_from('<H', pkt, 2)[0]
    frame_len = struct.unpack_from('<H', pkt, 4)[0]
    tcp_func = pkt[7]
    datalog = pkt[8:18].decode('ascii', errors='replace')

    result = {
        'timestamp': datetime.now().isoformat(),
        'protocol': protocol,
        'frame_len': frame_len,
        'tcp_function': TCP_FUNCTIONS.get(tcp_func, f'0x{tcp_func:02x}'),
        'datalog': datalog,
    }

    if tcp_func == 0xc1:
        result['type'] = 'heartbeat'
        return result

    if tcp_func != 0xc2:
        result['type'] = 'unknown'
        return result

    # TranslatedData
    if len(pkt) < 37:  # minimum: 18 outer + 17 inner (len+addr+func+serial+reg+vlen) + 2 CRC
        result['error'] = 'packet too short for TranslatedData'
        return result

    inner = pkt[18:]
    inner_len = struct.unpack_from('<H', inner, 0)[0]
    address = inner[2]
    dev_func = inner[3]
    inverter = inner[4:14].decode('ascii', errors='replace')
    start_reg = struct.unpack_from('<H', inner, 14)[0]

    result['type'] = 'translated_data'
    result['address'] = address
    result['device_function'] = DEV_FUNCTIONS.get(dev_func, f'0x{dev_func:02x}')
    result['inverter'] = inverter
    result['start_register'] = start_reg

    # Value length byte: always present in broadcast stream (protocol v5)
    value_len = inner[16]
    values = inner[17:17 + value_len]
    result['register_count'] = value_len // 2

    # CRC validation
    crc_data = inner[2:len(inner) - 2]
    crc_recv = struct.unpack_from('<H', inner, len(inner) - 2)[0]
    crc_calc = crc16_modbus(crc_data)
    result['crc_valid'] = crc_recv == crc_calc

    # Decode registers
    registers = {}
    is_input = (dev_func == 4)
    name_map = INPUT_NAMES if is_input else HOLDING_NAMES

    for i in range(0, min(len(values), value_len), 2):
        reg_num = start_reg + (i // 2)
        raw = struct.unpack_from('<H', values, i)[0]

        if is_input and reg_num in name_map:
            name, divisor = name_map[reg_num]
            # Special handling for soc_soh packed field
            if name == "soc_soh":
                soc = raw & 0xFF  # low byte (signed i8 in lxp-bridge)
                soh = (raw >> 8) & 0xFF
                registers["soc"] = soc
                registers["soh"] = soh
            elif divisor:
                registers[name] = round(raw / divisor, 3)
            else:
                registers[name] = raw
        elif not is_input and reg_num in name_map:
            registers[name_map[reg_num]] = raw
        else:
            prefix = "input" if is_input else "hold"
            registers[f"{prefix}_{reg_num}"] = raw

    result['registers'] = registers
    return result


def find_packets(buf: bytes):
    """Find complete packet boundaries in buffer."""
    packets = []
    i = 0
    while i < len(buf) - 5:
        if buf[i] == 0xa1 and buf[i + 1] == 0x1a:
            frame_len = struct.unpack_from('<H', buf, i + 4)[0]
            total_len = 6 + frame_len
            if i + total_len <= len(buf):
                packets.append((buf[i:i + total_len], i + total_len))
                i += total_len
                continue
            else:
                break  # incomplete packet
        i += 1
    return packets


def pretty_print(decoded: dict):
    """Print a decoded packet in human-readable format."""
    ts = decoded.get('timestamp', '')
    ptype = decoded.get('type', 'unknown')

    if ptype == 'heartbeat':
        print(f"[{ts}] HEARTBEAT datalog={decoded['datalog']}")
        return

    if ptype != 'translated_data':
        print(f"[{ts}] {ptype}: {decoded}")
        return

    fn = decoded['device_function']
    reg = decoded['start_register']
    cnt = decoded['register_count']
    crc = "OK" if decoded['crc_valid'] else "FAIL"

    print(f"[{ts}] {fn} reg={reg}-{reg+cnt-1} ({cnt} regs) CRC={crc}")

    regs = decoded.get('registers', {})
    for name, val in regs.items():
        print(f"  {name:30s} = {val}")
    print()


def capture_and_decode(host: str, port: int, duration: int, json_output: bool = False):
    """Connect to dongle and decode broadcast stream."""
    print(f"Connecting to {host}:{port}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)

    try:
        sock.connect((host, port))
    except (socket.timeout, ConnectionRefusedError) as e:
        print(f"Connection failed: {e}")
        return

    print(f"Connected. Decoding for {duration}s...")
    buf = b''
    packet_count = 0
    start = time.time()

    try:
        while time.time() - start < duration:
            sock.settimeout(5)
            try:
                data = sock.recv(4096)
                if not data:
                    print("Connection closed.")
                    break
                buf += data

                packets = find_packets(buf)
                for pkt_data, end_offset in packets:
                    decoded = decode_packet(pkt_data)
                    if decoded:
                        packet_count += 1
                        if json_output:
                            print(json.dumps(decoded, indent=2))
                        else:
                            pretty_print(decoded)

                if packets:
                    buf = buf[packets[-1][1]:]

            except socket.timeout:
                continue
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        sock.close()
        elapsed = time.time() - start
        print(f"\nDecoded {packet_count} packets in {elapsed:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="Decode EG4 LuxPower TCP v5 packets")
    parser.add_argument("--host", required=True, help="Dongle IP address")
    parser.add_argument("--port", type=int, default=8000, help="Dongle port")
    parser.add_argument("--duration", type=int, default=30, help="Capture duration (seconds)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()
    capture_and_decode(args.host, args.port, args.duration, args.json)


if __name__ == "__main__":
    main()
