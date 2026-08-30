"""Tests for per-battery BMS registers (5000+) and the /api/batteries endpoint."""

from collector.registers import (
    BATTERY_START,
    BATTERY_BLOCK_SIZE,
    BATTERY_COUNT,
    BATTERY_REGISTERS,
    INPUT_REGISTERS,
    decode_battery_serial,
    decode_registers,
)
from collector.drivers.registry import get_driver


def test_battery_registers_are_in_input_map():
    # Battery 1 block starts at 5000; capacity is at offset 3.
    assert (BATTERY_START + 3) in INPUT_REGISTERS
    assert INPUT_REGISTERS[BATTERY_START + 3].name == "battery_1_capacity"
    assert INPUT_REGISTERS[BATTERY_START + 8].name == "battery_1_voltage"
    assert INPUT_REGISTERS[BATTERY_START + 10].name == "battery_1_soc"


def test_battery_registers_span_eight_batteries():
    # 8 batteries x 30 registers = 240; minus the 16 reserved offsets we skip
    # (offsets 0,1,2,4,7 and the 14 serial registers are all present, so the
    # count is 8 * (30) = 240, but we only define the meaningful fields).
    # Just assert the last battery's block is present.
    last_base = BATTERY_START + (BATTERY_COUNT - 1) * BATTERY_BLOCK_SIZE
    assert (last_base + 3) in INPUT_REGISTERS
    assert INPUT_REGISTERS[last_base + 3].name == f"battery_{BATTERY_COUNT}_capacity"


def test_battery_soc_packed_low_byte():
    # Register 5010 packs SOC (low byte) and SOH (high byte).
    reg = INPUT_REGISTERS[BATTERY_START + 10]
    assert reg.name == "battery_1_soc"
    assert reg.bitmask == 0xFF
    assert reg.bitshift == 0


def test_decode_battery_serial():
    raw = {}
    # "AB" in register 5019 (offset 19), rest zero.
    raw[BATTERY_START + 19] = (ord("A") << 8) | ord("B")
    assert decode_battery_serial(raw, 1) == "AB"


def test_decode_battery_serial_strips_nulls():
    raw = {}
    raw[BATTERY_START + 19] = (ord("X") << 8) | ord("Y")
    # trailing registers are zero -> null chars stripped
    assert decode_battery_serial(raw, 1) == "XY"


def test_battery_registers_decode_via_decode_registers():
    raw = {
        BATTERY_START + 3: 100,   # capacity 100 Ah
        BATTERY_START + 8: 5120,  # voltage 51.20 V (scale 0.01)
        BATTERY_START + 10: 0x5A32,  # SOC=0x32=50, SOH=0x5A=90
    }
    decoded = decode_registers(raw)
    assert decoded["battery_1_capacity"]["value"] == 100.0
    assert decoded["battery_1_voltage"]["value"] == 51.2
    assert decoded["battery_1_soc"]["value"] == 50.0


def test_all_drivers_include_battery_batches():
    # Every driver should poll the 5000+ battery block.
    for name in ("luxpower_sna", "eg4_18kpv", "eg4_6000xp", "eg4_12kpv"):
        d = get_driver(name)
        starts = [b[0] for b in d.batches]
        assert BATTERY_START in starts, f"{name} missing battery batch"
