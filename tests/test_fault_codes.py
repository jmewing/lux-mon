"""Tests for the fault/warning code decode tables."""

import pytest

from collector.fault_codes import (
    FAULT_CODES,
    WARNING_CODES,
    decode_fault_code,
    decode_warning_code,
    fault_code_text,
    warning_code_text,
)


def test_fault_code_single_register():
    # Internal fault register 6 = 19 -> BUS overvoltage
    assert fault_code_text(19) == "BUS overvoltage"


def test_fault_code_bitmask_multiple():
    # Bits 19 (BUS overvoltage) + 21 (PV overvoltage)
    raw = (1 << 19) | (1 << 21)
    names = decode_fault_code(raw)
    assert "BUS overvoltage" in names
    assert "PV overvoltage" in names


def test_warning_code_bitmask_multiple():
    # Bits 16 (no grid) + 17 (grid voltage out of range)
    raw = (1 << 16) | (1 << 17)
    names = decode_warning_code(raw)
    assert "No grid connection" in names
    assert "Grid voltage out of range" in names


def test_zero_code_returns_empty():
    assert decode_fault_code(0) == []
    assert decode_warning_code(0) == []
    assert fault_code_text(0) == ""
    assert warning_code_text(0) == ""


def test_none_code_returns_empty():
    assert decode_fault_code(None) == []
    assert decode_warning_code(None) == []


def test_unknown_code_returns_empty():
    # A bit with no known mapping (e.g. bit 30 for fault) -> empty
    assert decode_fault_code(1 << 30) == []


def test_tables_are_complete():
    # Sanity: the tables contain the known code ranges from the protocol doc.
    assert 19 in FAULT_CODES  # BUS overvoltage
    assert 23 in FAULT_CODES  # Neutral fault
    assert 16 in WARNING_CODES  # No grid connection
    assert 31 in WARNING_CODES  # DCV exceeded standard


def test_api_fault_label():
    from api import _fault_label, _warning_label
    assert _fault_label(19) == "BUS overvoltage"
    assert _fault_label(0) == ""
    assert _warning_label(16) == "No grid connection"
    assert _warning_label(0) == ""
    # 32-bit packed fault code (bits 19 + 21)
    packed = (1 << 19) | (1 << 21)
    assert "BUS overvoltage" in _fault_label(packed)
    assert "PV overvoltage" in _fault_label(packed)
