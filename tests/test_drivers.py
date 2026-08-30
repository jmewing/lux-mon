"""Tests for model driver registry and the 18KPV driver."""
import pytest

from collector.drivers.registry import get_driver, DRIVERS, DEFAULT_MODEL
from collector.drivers.eg4_18kpv import INPUT_REGISTERS as _18KPV_INPUT


def test_registry_has_sna_family():
    assert "eg4_6000xp" in DRIVERS
    assert "luxpower_sna" in DRIVERS
    assert "eg4_18kpv" in DRIVERS
    assert "eg4_12kpv" in DRIVERS
    assert "eg4_12000xp" in DRIVERS
    assert "eg4_6500ex" in DRIVERS
    assert "eg4_3000ehv" in DRIVERS


def test_registry_has_lxp_and_rebrands():
    assert "lxp_6k" in DRIVERS
    assert "lxp_12k" in DRIVERS
    assert "lxp_18k" in DRIVERS
    assert "fortress_envy_12k" in DRIVERS
    assert "bigbattery_sna_6k" in DRIVERS


def test_lxp_6k_is_sna_family():
    base = get_driver("luxpower_sna")
    d = get_driver("lxp_6k")
    assert d.input_registers is base.input_registers
    assert d.batches == base.batches
    assert d.label == "LuxPower LXP 6K"


def test_bigbattery_is_sna_family():
    base = get_driver("luxpower_sna")
    d = get_driver("bigbattery_sna_6k")
    assert d.input_registers is base.input_registers
    assert d.batches == base.batches
    assert d.label == "BigBattery SNA-US 6K"


def test_lxp_12k_18k_are_18kpv_family():
    kpv = get_driver("eg4_18kpv")
    for model, label in (("lxp_12k", "LuxPower LXP 12K"), ("lxp_18k", "LuxPower LXP 18K")):
        d = get_driver(model)
        assert d.input_registers is kpv.input_registers
        assert d.batches == kpv.batches
        assert d.label == label


def test_fortress_envy_is_18kpv_family():
    kpv = get_driver("eg4_18kpv")
    d = get_driver("fortress_envy_12k")
    assert d.input_registers is kpv.input_registers
    assert d.batches == kpv.batches
    assert d.label == "Fortress Envy True 12K"


def test_sna_aliases_share_register_family():
    # 12000XP / 6500EX / 3000EHV are SNA rebadges → same map as 6000XP
    base = get_driver("eg4_6000xp")
    for model in ("eg4_12000xp", "eg4_6500ex", "eg4_3000ehv"):
        d = get_driver(model)
        assert d.input_registers is base.input_registers
        assert d.batches == base.batches


def test_12kpv_is_18kpv_family():
    kpv = get_driver("eg4_18kpv")
    pv = get_driver("eg4_12kpv")
    assert pv.input_registers is kpv.input_registers
    assert pv.batches == kpv.batches
    assert pv.label == "EG4 12kPV"


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


def test_sna_has_shared_extended_registers():
    # Serial, load power, reactive, NTC temps are shared across the family.
    sna = get_driver("luxpower_sna")
    assert 115 in sna.input_registers  # serial
    assert 170 in sna.input_registers  # load power
    assert 139 in sna.input_registers  # reactive power
    assert 214 in sna.input_registers  # NTC temp


def test_sna_does_not_claim_split_phase_or_three_phase():
    # 2-MPPT single-phase SNA hardware has no PV4-6, split-phase, or 3-phase regs.
    sna = get_driver("luxpower_sna")
    assert 217 not in sna.input_registers  # PV4
    assert 193 not in sna.input_registers  # split-phase L1-N
    assert 180 not in sna.input_registers  # three-phase S


def test_18kpv_has_split_phase_and_pv4_6():
    kpv = get_driver("eg4_18kpv")
    assert 217 in kpv.input_registers  # PV4
    assert 193 in kpv.input_registers  # split-phase L1-N
    assert 153 in kpv.input_registers  # AC couple
    assert 232 in kpv.input_registers  # smart load


def test_18kpv_decodes_pv4_and_split_phase():
    d = get_driver("eg4_18kpv")
    raw = {217: 3500, 220: 1200, 193: 2400, 232: 500}
    dec = d.decode(raw)
    assert dec["pv4_voltage"]["value"] == 350.0
    assert dec["pv4_power"]["value"] == 1200.0
    assert dec["grid_voltage_l1n"]["value"] == 240.0
    assert dec["smart_load_power"]["value"] == 500.0


def test_18kpv_decodes_32bit_pair():
    d = get_driver("eg4_18kpv")
    # PV4 total energy: low word 224, high word 225
    raw = {224: 0x1234, 225: 0x0001}
    dec = d.decode(raw)
    # 0x00011234 = 70196, scale 0.1 -> 7019.6 kWh
    assert dec["pv4_energy_total"]["value"] == 7019.6
