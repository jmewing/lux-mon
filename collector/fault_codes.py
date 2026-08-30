"""Fault and warning code tables for the LuxPower / EG4 inverter family.

These tables translate the raw integer codes reported in the input registers
(6 = internal fault, 60-61 = fault code, 62-63 = warning code, 99 = BMS fault,
100 = BMS warning) into human-readable descriptions.

Source: the official LuxPower Modbus protocol PDF (06-01-01-PTC-Luxpower
MODBUS Protocol_2024.04.26.pdf) and the EG4 18KPV Modbus protocol PDF, as
extracted into `docs/reference/luxpower-ha-register-map.md`. LuxPower and EG4
share the protocol, so these tables apply across the whole family.

The codes are bit-packed 32-bit values (low word + high word). A single
register can carry multiple simultaneous faults/warnings, so the decode
helpers return a list of descriptions rather than a single string.
"""
from __future__ import annotations

from typing import List, Optional

# ── Fault codes (input registers 6, 60-61) ─────────────────────────────────

FAULT_CODES: dict[int, str] = {
    0: "Internal communication failure 1",
    1: "Model fault",
    8: "Parallel CAN communication failure",
    9: "The host is missing",
    10: "Inconsistent rated power",
    11: "Inconsistent AC or safety settings",
    12: "UPS short circuit",
    13: "UPS reverse current",
    14: "BUS short circuit",
    15: "Abnormal phase in three-phase system",
    16: "Relay failure",
    17: "Internal communication failure 2",
    18: "Internal communication failure 3",
    19: "BUS overvoltage",
    20: "EPS connection fault",
    21: "PV overvoltage",
    22: "Overcurrent protection",
    23: "Neutral fault",
    24: "PV short circuit",
    25: "Heatsink temperature out of range",
    26: "Internal failure",
    27: "Consistency failure",
    28: "Inconsistent generator connection",
    29: "Parallel sync signal loss",
    31: "Internal communication failure 4",
}

# ── Warning codes (input registers 62-63, 100) ─────────────────────────────

WARNING_CODES: dict[int, str] = {
    0: "Battery communication failed",
    1: "AFCI communication failure",
    2: "Battery low temperature",
    3: "Meter communication failed",
    4: "Battery cannot be charged/discharged",
    5: "Automated test failed",
    6: "RSD active",
    7: "LCD communication failure",
    8: "Software version mismatch",
    9: "Fan is stuck",
    10: "Grid overload",
    11: "Parallel secondaries exceed limit",
    12: "Battery reverse MOS abnormal",
    13: "Radiator temperature out of range",
    14: "Multiple primary units in parallel",
    15: "Battery reverse",
    16: "No grid connection",
    17: "Grid voltage out of range",
    18: "Grid frequency out of range",
    20: "Insulation resistance low",
    21: "Leakage current too high",
    22: "DCI exceeded standard",
    23: "PV short circuit",
    25: "Battery overvoltage",
    26: "Battery undervoltage",
    27: "Battery open circuit",
    28: "EPS overload",
    29: "EPS voltage high",
    30: "Meter reversed",
    31: "DCV exceeded standard",
}


def _decode_bits(value: int, table: dict[int, str]) -> List[str]:
    """Decode a bit-packed code value into a list of descriptions.

    Each set bit (or, for the single-register internal/BMS codes, the raw
    integer value) maps to a description. Returns an empty list when the
    value is 0 (no fault/warning) or has no known bits set.
    """
    if value is None or value == 0:
        return []

    # Single-register codes (internal fault, BMS fault/warning) are plain
    # integers, not bitmasks. Try a direct lookup first.
    if value in table:
        return [table[value]]

    # Bit-packed 32-bit codes: each set bit is a distinct fault/warning.
    names: List[str] = []
    for bit in range(32):
        if value & (1 << bit):
            name = table.get(bit)
            if name:
                names.append(name)
    return names


def decode_fault_code(value: Optional[int]) -> List[str]:
    """Return the list of fault descriptions for a raw fault code value."""
    return _decode_bits(value, FAULT_CODES)


def decode_warning_code(value: Optional[int]) -> List[str]:
    """Return the list of warning descriptions for a raw warning code value."""
    return _decode_bits(value, WARNING_CODES)


def fault_code_text(value: Optional[int]) -> str:
    """Return a comma-joined human-readable fault string ('' when none)."""
    return ", ".join(decode_fault_code(value))


def warning_code_text(value: Optional[int]) -> str:
    """Return a comma-joined human-readable warning string ('' when none)."""
    return ", ".join(decode_warning_code(value))
