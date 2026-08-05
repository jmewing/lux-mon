#!/usr/bin/env python3
"""Discovery helper for RS-485 / serial devices connected to lux-mon.

Tries common baud rates and listens for raw traffic, then attempts to parse
any response with the supported device drivers (JK BMS, Modbus RTU).

Usage:
    python3 scripts/discover-rs485.py /dev/ttyUSB0
    LUX_RS485_PORT=/dev/ttyUSB0 python3 scripts/discover-rs485.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo))

from collector.rs485 import Rs485DeviceConfig
from collector.rs485.eg4_a5_bms import Eg4A5BmsDevice
from collector.rs485.jk_bms import JkBmsDevice, STATUS_REQUEST, STATUS_REQUEST_FALLBACK, _append_checksum


def probe_eg4_a5_bms(port: str, baud: int, timeout: float = 1.0) -> bool:
    """Listen for EG4 A5/5A broadcast frames and see if they parse."""
    try:
        import serial
    except ImportError:
        return False

    try:
        dev = serial.Serial(port, baudrate=baud, timeout=timeout)
    except Exception as exc:
        print(f"  Could not open {port} at {baud}: {exc}")
        return False

    found = False
    try:
        dev.reset_input_buffer()
        # Send a minimal poll probe to wake a quiet BMS.
        dev.write(bytes([0xA5, 0x5A, 0x05, 0x82, 0x21, 0x13, 0x00, 0x00]))
        time.sleep(0.2)
        data = dev.read(4096)
        if data:
            print(f"  [{baud}] EG4 A5 probe got {len(data)} bytes: {data[:60].hex()}")
            drv = Eg4A5BmsDevice(Rs485DeviceConfig(port=port, baudrate=baud, timeout=timeout))
            parsed = drv.read()
            if parsed:
                print(f"  [{baud}] EG4 A5 BMS parsed successfully:")
                for key in ("voltage", "current", "power", "temperature_pcb", "cell_count", "cell_delta_voltage"):
                    info = parsed.get(key)
                    if info:
                        print(f"    {key}: {info['value']} {info.get('unit', '')}")
                found = True
            else:
                print(f"  [{baud}] EG4 A5 BMS parse failed")
    finally:
        dev.close()

    return found


def probe_jk_bms(port: str, baud: int, timeout: float = 1.0) -> bool:
    """Send JK BMS status requests and see if anything parses."""
    try:
        import serial
    except ImportError:
        print("pyserial not installed; cannot probe serial devices")
        return False

    try:
        dev = serial.Serial(port, baudrate=baud, timeout=timeout, write_timeout=timeout)
    except Exception as exc:
        print(f"  Could not open {port} at {baud}: {exc}")
        return False

    found = False
    try:
        dev.reset_input_buffer()
        for req_name, request in [("standard", STATUS_REQUEST), ("fallback", STATUS_REQUEST_FALLBACK)]:
            dev.reset_input_buffer()
            dev.write(_append_checksum(bytearray(request)))
            time.sleep(0.2)
            data = dev.read(4096)
            if data:
                print(f"  [{baud}] {req_name} request got {len(data)} bytes: {data[:60].hex()}")
                drv = JkBmsDevice(Rs485DeviceConfig(port=port, baudrate=baud, timeout=timeout))
                parsed = drv._parse_frame(data)
                if parsed:
                    print(f"  [{baud}] JK BMS parsed successfully:")
                    for key in ("total_voltage", "current", "soc", "cell_count", "mosfet_temperature"):
                        info = parsed.get(key)
                        if info:
                            print(f"    {key}: {info['value']} {info.get('unit', '')}")
                    found = True
                else:
                    print(f"  [{baud}] JK BMS parse failed")
    except serial.SerialException as exc:
        print(f"  [{baud}] Serial error while probing JK BMS: {exc}")
    finally:
        dev.close()

    return found


def main():
    parser = argparse.ArgumentParser(description="Discover RS-485 serial devices")
    parser.add_argument("port", nargs="?", default=os.environ.get("LUX_RS485_PORT", "/dev/ttyUSB0"))
    parser.add_argument("--baud", type=int, nargs="+", default=[9600, 19200, 38400, 57600, 115200])
    parser.add_argument("--timeout", type=float, default=1.0)
    args = parser.parse_args()

    print(f"Probing {args.port} for RS-485 devices...")
    for baud in args.baud:
        print(f"\nBaud {baud}:")
        if probe_eg4_a5_bms(args.port, baud, args.timeout):
            print(f"  -> EG4 A5/5A BMS detected at {baud} baud")
        if probe_jk_bms(args.port, baud, args.timeout):
            print(f"  -> JK BMS detected at {baud} baud")

    print("\nDone.")


if __name__ == "__main__":
    main()
