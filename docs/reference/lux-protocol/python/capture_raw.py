#!/usr/bin/env python3
"""
Raw TCP capture from EG4/LuxPower WiFi dongle.
Connects to the dongle on port 8000 and dumps raw hex packets.

Usage:
    python3 capture_raw.py [--host HOST] [--port PORT] [--duration SECONDS]
"""

import socket
import sys
import time
import argparse
from datetime import datetime


def capture(host: str, port: int, duration: int):
    """Connect to dongle and capture raw TCP data."""
    print(f"Connecting to {host}:{port}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((host, port))
    print(f"Connected. Capturing for {duration}s...\n")

    start = time.time()
    packet_count = 0

    try:
        while time.time() - start < duration:
            sock.settimeout(5)
            try:
                data = sock.recv(4096)
                if not data:
                    print("Connection closed by dongle.")
                    break
                packet_count += 1
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                print(f"[{ts}] Packet #{packet_count} ({len(data)} bytes)")
                # Print hex dump in 16-byte rows
                for i in range(0, len(data), 16):
                    chunk = data[i:i+16]
                    hex_str = " ".join(f"{b:02x}" for b in chunk)
                    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                    print(f"  {i:04x}: {hex_str:<48s}  {ascii_str}")
                print()
            except socket.timeout:
                continue
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        sock.close()
        elapsed = time.time() - start
        print(f"Captured {packet_count} packets in {elapsed:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="Capture raw TCP from EG4 dongle")
    parser.add_argument("--host", required=True, help="Dongle IP address")
    parser.add_argument("--port", type=int, default=8000, help="Dongle port")
    parser.add_argument("--duration", type=int, default=30, help="Capture duration in seconds")
    args = parser.parse_args()
    capture(args.host, args.port, args.duration)


if __name__ == "__main__":
    main()
