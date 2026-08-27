"""Tests for model driver registry and the 18KPV driver."""
import pytest

from collector.drivers.registry import get_driver, DRIVERS, DEFAULT_MODEL
from collector.drivers.eg4_18kpv import INPUT_REGISTERS as _18KPV_INPUT


def test_registry_has_sna_family():
    assert "eg4_6000xp" in DRIVERS
    assert "luxpower_sna" in DRIVERS
    assert "eg4_18kpv" in DRIVERS


def test_unsupported_model_raises():
    with pytest.raises(ValueError):
        get_driver("growatt")
    with pytest.raises(ValueError):
        get_driver("solis")


def test_eg4_6000xp_is_sna_alias():
    d = get_driver("eg4_6000xp")
    assert d.name == "eg4_6000xp"
    assert d.label == "EG4 6000XP"


def test_18kpv_has_more_registers_than_sna():
    sna = get_driver("luxpower_sna")
    kpv = get_driver("eg4_18kpv")
    assert kpv.total_registers() > sna.total_registers()
    # 18KPV adds AFCI registers
    assert 140 in kpv.input_registers
    assert kpv.input_registers[140].name == "afci_current_ch1"


def test_18kpv_decodes_afci():
    d = get_driver("eg4_18kpv")
    raw = {140: 5, 144: 0x01}
    dec = d.decode(raw)
    assert dec["afci_current_ch1"]["value"] == 5.0
    assert dec["afci_flag"]["value"] == 1.0


def test_18kpv_decodes_shared_sna_registers():
    d = get_driver("eg4_18kpv")
    raw = {1: 3500, 7: 1200}
    dec = d.decode(raw)
    assert dec["pv1_voltage"]["value"] == 350.0
    assert dec["pv1_power"]["value"] == 1200.0
