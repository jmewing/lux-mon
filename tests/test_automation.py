"""Tests for the rebuilt automation engine (rule-table model).

Covers the four automation types, nested subset columns, time-of-day parsing,
and restore-on-exit semantics.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from collector.automation import (
    _parse_automation,
    _hm_to_minutes,
    _normalize_range_value,
    RuleTable,
    BatterySocAutomation,
    BatteryProtectionAutomation,
    NotifyAutomation,
)

TZ = ZoneInfo("America/Chicago")


def _t(h, m):
    return datetime(2026, 8, 23, h, m, tzinfo=TZ)


# ── helpers ─────────────────────────────────────────────────────────────────

def test_hm_to_minutes():
    assert _hm_to_minutes("00:00") == 0
    assert _hm_to_minutes("21:00") == 1260
    assert _hm_to_minutes("23:59") == 1439


def test_normalize_time_range():
    assert _normalize_range_value("21:00", "time") == 1260.0
    assert _normalize_range_value("23:59", "time") == 1439.0
    assert _normalize_range_value(None, "time") is None
    assert _normalize_range_value("", "time") is None
    assert _normalize_range_value(54.0, "number") == 54.0


# ── rule table ──────────────────────────────────────────────────────────────

def _grid_charge_rule():
    return _parse_automation({
        "id": "a2", "name": "Grid charge current", "type": "rule_table",
        "target": "ac_charge_battery_current",
        "columns": [
            {"kind": "time_of_day", "ranges": [
                {"from": "21:00", "to": "23:59"},
                {"from": "23:59", "to": "23:59"},
            ]},
            {"kind": "battery_voltage", "ranges": [
                {"from": 0.0, "to": 54.0, "value": 85},
                {"from": 55.0, "to": 56.0, "value": 45},
                {"from": 57.0, "to": 58.0, "value": 1},
            ]},
        ],
        "restore": None,
    })


def test_rule_table_nested_evaluation():
    rt = _grid_charge_rule()
    assert rt.evaluate({"battery_voltage": {"value": 55.5}}, _t(22, 0)) == 45
    assert rt.evaluate({"battery_voltage": {"value": 53.0}}, _t(22, 0)) == 85
    assert rt.evaluate({"battery_voltage": {"value": 57.5}}, _t(22, 0)) == 1


def test_rule_table_time_window_excludes():
    rt = _grid_charge_rule()
    # 12:00 is outside both time ranges -> no match
    assert rt.evaluate({"battery_voltage": {"value": 53.0}}, _t(12, 0)) is None


def test_rule_table_restore_on_exit():
    rt = _parse_automation({
        "id": "rt", "name": "Absorption voltage", "type": "rule_table",
        "target": "floating_voltage",
        "columns": [
            {"kind": "time_of_day", "ranges": [{"from": "00:00", "to": "23:59"}]},
            {"kind": "battery_voltage", "ranges": [{"from": 0.0, "to": 60.0, "value": 57.6}]},
        ],
        "restore": 58.4,
    })
    assert rt.evaluate({"battery_voltage": {"value": 50}}, _t(10, 0)) == 57.6
    # No match -> engine should apply restore (58.4); evaluate returns None here.
    assert rt.evaluate({"battery_voltage": {"value": 99}}, _t(10, 0)) is None
    assert rt.restore == 58.4


# ── battery SOC control ─────────────────────────────────────────────────────

def test_battery_soc_boundary_line():
    bs = _parse_automation({
        "id": "bs", "name": "SOC control", "type": "battery_soc",
        "points": [{"time": "08:00", "soc": 30}, {"time": "15:00", "soc": 70}],
    })
    assert bs.evaluate({"soc": {"value": 25}}, _t(8, 0)) == "grid"
    assert bs.evaluate({"soc": {"value": 35}}, _t(8, 0)) == "battery"
    # Midpoint 11:30 -> threshold ~50
    assert bs.evaluate({"soc": {"value": 45}}, _t(11, 30)) == "grid"
    assert bs.evaluate({"soc": {"value": 55}}, _t(11, 30)) == "battery"
    assert bs.evaluate({"soc": {"value": 75}}, _t(15, 0)) == "battery"


# ── battery protection ──────────────────────────────────────────────────────

def test_battery_protection_shutdown_and_restore():
    bp = _parse_automation({
        "id": "bp", "name": "Battery protection", "type": "battery_protection",
        "threshold_soc": 25, "shutdown_register": "shutdown_battery_voltage",
        "restore_value": 40.0,
    })
    assert bp.evaluate({"soc": {"value": 20}}, _t(10, 0)) == 0.0  # shutdown
    assert bp.evaluate({"soc": {"value": 60}}, _t(10, 0)) is None  # recovered -> restore


# ── notify ──────────────────────────────────────────────────────────────────

def test_notify_condition():
    nt = _parse_automation({
        "id": "nt", "name": "Low SOC", "type": "notify",
        "condition_kind": "battery_soc", "operator": "<=", "threshold": 20,
    })
    assert nt.evaluate({"soc": {"value": 15}}, _t(10, 0)) is True
    assert nt.evaluate({"soc": {"value": 50}}, _t(10, 0)) is False


# ── notify dispatch ─────────────────────────────────────────────────────────

class _MockNotifiers:
    def __init__(self):
        self.sent = []

    def send(self, alert_name, active, value, message):
        self.sent.append((alert_name, active, value, message))


def test_notify_dispatch():
    from collector.automation import AutomationEngine

    eng = AutomationEngine.__new__(AutomationEngine)  # bypass __init__ (no DB)
    eng._notifiers = _MockNotifiers()
    eng._notify_last_sent = {}
    eng._notify_min_interval_sec = 0  # disable throttle for test

    nt = _parse_automation({
        "id": "nt", "name": "Low SOC", "type": "notify",
        "condition_kind": "battery_soc", "operator": "<=", "threshold": 20,
    })
    snap = {"soc": {"value": 15}}
    res = eng._apply_notify(nt, snap, _t(10, 0))

    assert res and res[0]["notified"] is True
    assert eng._notifiers.sent
    assert eng._notifiers.sent[0][0] == "Low SOC"
    assert eng._notifiers.sent[0][2] == 15.0


def test_notify_dispatch_throttled():
    from collector.automation import AutomationEngine

    eng = AutomationEngine.__new__(AutomationEngine)
    eng._notifiers = _MockNotifiers()
    eng._notify_last_sent = {}
    eng._notify_min_interval_sec = 300

    nt = _parse_automation({
        "id": "nt", "name": "Low SOC", "type": "notify",
        "condition_kind": "battery_soc", "operator": "<=", "threshold": 20,
    })
    snap = {"soc": {"value": 15}}

    first = eng._apply_notify(nt, snap, _t(10, 0))
    second = eng._apply_notify(nt, snap, _t(10, 1))  # 1 min later, still throttled

    assert first and first[0]["notified"] is True
    assert second == []  # throttled
    assert len(eng._notifiers.sent) == 1
