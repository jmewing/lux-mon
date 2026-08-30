"""Tests for the model capability layer and hold-register expansion."""
import pytest

from collector.capabilities import (
    capabilities_for,
    filter_holding_registers,
    register_applies,
    MODEL_CAPABILITIES,
    DEFAULT_CAPABILITIES,
)
from collector.protocol import HOLDING_REGISTERS, HOLDING_BY_NAME


def test_all_models_have_capabilities():
    for model in MODEL_CAPABILITIES:
        assert capabilities_for(model) is not None


def test_unknown_model_defaults_to_full_capabilities():
    assert capabilities_for("growatt") == DEFAULT_CAPABILITIES


def test_register_applies_no_requirement():
    assert register_applies(frozenset(), None) is True
    assert register_applies(frozenset(), set()) is True


def test_register_applies_subset():
    caps = frozenset({"pv1", "pv2"})
    assert register_applies(caps, {"pv1"}) is True
    assert register_applies(caps, {"pv1", "pv2"}) is True
    assert register_applies(caps, {"pv3"}) is False


def test_sna_hides_18kpv_only_registers():
    sna = filter_holding_registers(HOLDING_REGISTERS, "eg4_6000xp")
    kpv = filter_holding_registers(HOLDING_REGISTERS, "eg4_18kpv")
    # AFCI, smart load, generator, QV/QP curves are 18KPV-only
    assert 180 not in sna  # afci_arc_threshold
    assert 180 in kpv
    assert 215 not in sna  # smart_load_on_soc
    assert 215 in kpv
    assert 194 not in sna  # generator_charge_start_voltage
    assert 194 in kpv
    assert 54 not in sna  # qv_max_q_percent
    assert 54 in kpv


def test_shared_registers_present_in_both():
    sna = filter_holding_registers(HOLDING_REGISTERS, "eg4_6000xp")
    kpv = filter_holding_registers(HOLDING_REGISTERS, "eg4_18kpv")
    # Shared across the whole family
    assert 22 in sna and 22 in kpv  # pv_start_voltage
    assert 60 in sna and 60 in kpv  # active_power_percent
    assert 168 in sna and 168 in kpv  # ac_charge_battery_current


def test_12kpv_has_no_afci_but_has_generator():
    pv = filter_holding_registers(HOLDING_REGISTERS, "eg4_12kpv")
    assert 180 not in pv  # AFCI is 18KPV-only
    assert 194 in pv  # generator charge is shared with 12kPV


def test_holding_register_count_grew():
    # Part 4 added a large number of registers; the map should be well over 100.
    assert len(HOLDING_REGISTERS) > 150


def test_new_registers_present():
    assert 22 in HOLDING_REGISTERS  # pv_start_voltage
    assert 29 in HOLDING_REGISTERS  # grid_volt_limit_1_low
    assert 42 in HOLDING_REGISTERS  # grid_freq_limit_1_low
    assert 145 in HOLDING_REGISTERS  # output_priority
    assert 171 in HOLDING_REGISTERS  # soc_curve_battery_volt_1
    assert 220 in HOLDING_REGISTERS  # ac_couple_start_soc
    assert 248 in HOLDING_REGISTERS  # wattnode_ct_amps_phase_1
    assert 261 in HOLDING_REGISTERS  # discharge_recovery


def test_no_duplicate_register_numbers():
    # Every register number must appear exactly once.
    from collections import Counter
    counts = Counter(HOLDING_REGISTERS.keys())
    dups = {k: v for k, v in counts.items() if v > 1}
    assert not dups, f"Duplicate register numbers: {dups}"


def test_holding_by_name_matches_registers():
    # HOLDING_BY_NAME must be a 1:1 inverse of HOLDING_REGISTERS.
    assert len(HOLDING_BY_NAME) == len(HOLDING_REGISTERS)
    for reg, info in HOLDING_REGISTERS.items():
        assert HOLDING_BY_NAME[info["name"]] == reg


def test_capability_tagged_registers_have_valid_flags():
    # Any register with a "capabilities" key must use known flags.
    valid = {
        "pv1", "pv2", "pv3", "pv4", "pv5", "pv6",
        "single_phase", "split_phase", "three_phase",
        "afci", "generator", "smart_load", "ac_couple", "wattnode",
        "grid_peak_shaving", "volt_watt", "qv_curve", "qp_curve",
        "seven_day_schedule", "battery_bms",
    }
    for reg, info in HOLDING_REGISTERS.items():
        caps = info.get("capabilities")
        if caps:
            assert caps.issubset(valid), f"Register {reg} has unknown capability {caps - valid}"
