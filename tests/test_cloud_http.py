"""Tests for the read-only EG4 cloud transport (transport=cloud_http).

No network and no database: HTTP is faked with FakeSession / MagicMock
responses built from real portal payloads in tests/fixtures/cloud/ (sources in
the README there). tests/fixtures/cloud/session_expired.html is SYNTHETIC: a
minimal stand-in for the HTML login page the portal returns (HTTP 200) when a
session has expired.
"""
import copy
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import quote_plus, urlparse

import pytest
import requests

from collector.collector import (
    CollectorConfig,
    PassiveCollector,
    _create_transport,
    config_from_env,
)
from collector.comm import cloud_http as ch
from collector.comm.cloud_http import (
    BATTERY_PATH,
    ENERGY_PATH,
    LOGIN_PATH,
    RUNTIME_PATH,
    CloudApiError,
    CloudHttpTransport,
    CloudSessionExpired,
    CloudTransientError,
    _num,
    _put16,
    _put32,
    battery_to_registers,
    energy_to_registers,
    registers_to_frames,
    runtime_to_registers,
)
from collector.drivers.registry import get_driver
from collector.outputs import OutputConfig, Outputs, _build_computed
from collector.registers import decode_battery_serial, decode_registers
from collector.settings import (
    DEFAULTS,
    SETTING_ENV,
    SETTING_META,
    TRANSPORT_OPTIONS,
    validate_setting,
)

FIXTURES = Path(__file__).parent / "fixtures" / "cloud"
SERIAL = "TEST000001"
USERNAME = "user@example.com"
PASSWORD = "s3cr3t-P@ss&=x"
BASE_URL = "https://monitor.eg4electronics.com"
# 30 s after the 12000XP capture's serverTime (2026-01-09 23:09:49 UTC).
T0 = datetime(2026, 1, 9, 23, 9, 49, tzinfo=timezone.utc).timestamp() + 30


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _html() -> str:
    return (FIXTURES / "session_expired.html").read_text()


def _resp(status=200, body=None, text=None, ctype="application/json"):
    """A fake requests.Response; .json() raises ValueError for non-JSON."""
    r = MagicMock()
    r.status_code = status
    r.headers = {"Content-Type": ctype}
    if body is not None:
        r.text = json.dumps(body)
        r.json.return_value = body
    else:
        r.text = text if text is not None else ""
        r.json.side_effect = ValueError("not JSON")
    return r


class FakeSession:
    """requests.Session stand-in: .post is routed by URL path and recorded.

    A route is a list of responses/exceptions (the last one repeats) or a
    callable returning a response.
    """

    def __init__(self, routes):
        self.routes = {p: (v if callable(v) else list(v)) for p, v in routes.items()}
        self.cookies = MagicMock()
        self.post = MagicMock(side_effect=self._route)
        self.closed = False

    def _route(self, url, data=None, headers=None, allow_redirects=True, timeout=None):
        path = urlparse(url).path
        route = self.routes.get(path)
        if route is None:
            raise AssertionError(f"unexpected request to {path}")
        if callable(route):
            item = route()
        else:
            item = route.pop(0) if len(route) > 1 else route[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def paths(self):
        return [urlparse(c.args[0]).path for c in self.post.call_args_list]

    def count(self, path):
        return self.paths().count(path)

    def close(self):
        self.closed = True


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def _routes(runtime=None, energy=None, battery=None, login=None):
    return {
        LOGIN_PATH: login if login is not None else [_resp(body=_load("login"))],
        RUNTIME_PATH: runtime if runtime is not None else [_resp(body=_load("runtime_12000xp"))],
        ENERGY_PATH: energy if energy is not None else [_resp(body=_load("energy_18kpv"))],
        BATTERY_PATH: battery if battery is not None else [_resp(body=_load("battery_no_array"))],
    }


def _make(routes=None, model="eg4_12000xp", clock=None, username=USERNAME, password=PASSWORD, **kw):
    fake = FakeSession(routes if routes is not None else _routes())
    frames = []
    t = CloudHttpTransport(
        frames.append,
        base_url=BASE_URL,
        username=username,
        password=password,
        inverter_serial=SERIAL,
        model=model,
        session_factory=lambda: fake,
        clock=clock or Clock(),
        rand=lambda: 0.5,  # zero jitter
        **kw,
    )
    return t, fake, frames


def _decoded(regs):
    return {k: v["value"] for k, v in decode_registers(regs).items()}


# ── Pure conversion functions ────────────────────────────────────────────────

def test_pure_helpers():
    assert _num({"a": 5}, "a") == 5.0
    assert _num({"a": "628"}, "a") == 628.0
    assert _num({"a": " 0.7 "}, "a") == pytest.approx(0.7)
    for bad in (True, None, "", "[0]", float("nan"), float("inf"), [1], {"x": 1}):
        assert _num({"a": bad}, "a") is None
    assert _num({}, "a") is None
    assert _num(None, "a") is None

    regs = {}
    _put16(regs, 1, 65535)
    _put16(regs, 2, 65536)
    _put16(regs, 3, -1)
    _put16(regs, 4, -47, signed=True)
    _put16(regs, 5, 32768, signed=True)
    _put16(regs, 6, -32769, signed=True)
    _put16(regs, 7, None)
    assert regs == {1: 65535, 4: (-47) & 0xFFFF}

    regs = {}
    _put32(regs, 40, 70000)
    _put32(regs, 50, -1)
    _put32(regs, 52, 0x100000000)
    assert regs == {40: 4464, 41: 1}


def test_runtime_12000xp_values():
    d = _decoded(runtime_to_registers(_load("runtime_12000xp"), "eg4_12000xp"))
    expected = {
        "state": 192, "pv1_voltage": 323.7, "pv2_voltage": 280.8,
        "battery_voltage": 52.9, "soc": 89,
        "pv1_power": 266, "pv2_power": 5, "charge_power": 0, "discharge_power": 1967,
        "grid_voltage_r": 0, "grid_frequency": 0,
        "eps_voltage_r": 238.6, "eps_frequency": 60.0, "eps_power": 2140,
        "eps_apparent_power": 2143,
        "bus1_voltage": 369.3, "bus2_voltage": 313.7,
        "temp_radiator_1": 46, "temp_radiator_2": 54,
        "bms_max_charge_current": 160.0, "bms_max_discharge_current": 170.0,
        "battery_count": 1, "battery_capacity": 628,
        "eps_power_l1": 1072, "eps_power_l2": 1072,
    }
    for key, value in expected.items():
        assert d[key] == pytest.approx(value), key
    absent = (
        "pv3_voltage", "pv3_power", "temp_inverter", "temp_battery", "power_factor",
        "grid_voltage_s", "grid_voltage_t", "eps_voltage_s", "eps_voltage_t",
        "gen_power", "load_power", "fault_code", "warning_code", "internal_fault",
    )
    for key in absent:
        assert key not in d, key

    computed = _build_computed(decode_registers(runtime_to_registers(_load("runtime_12000xp"), "eg4_12000xp")))
    assert computed["load_power"] == pytest.approx(2238)
    assert computed["battery_power_net"] == pytest.approx(-1967)


def test_runtime_18kpv_values():
    d = _decoded(runtime_to_registers(_load("runtime_18kpv"), "eg4_18kpv"))
    assert "pv3_voltage" in d
    assert d["temp_inverter"] == pytest.approx(39)
    assert d["temp_battery"] == pytest.approx(2)
    assert d["power_factor"] == pytest.approx(1.0)
    assert d["grid_voltage_r"] == pytest.approx(241.1)
    assert d["grid_frequency"] == pytest.approx(59.98)
    assert d["rec_power"] == pytest.approx(1067)
    assert d["grid_import_power"] == pytest.approx(1030)
    assert d["bms_max_charge_current"] == pytest.approx(600.0)
    assert d["bms_max_discharge_current"] == pytest.approx(600.0)


def test_runtime_quirks_and_fallbacks():
    rt = _load("runtime_12000xp")
    # tBat 0 is only "unknown" on the 12000XP; 127 is always the no-sensor sentinel.
    assert 67 in runtime_to_registers(rt, "eg4_6000xp")
    rt["tBat"] = 127
    assert 67 not in runtime_to_registers(rt, "eg4_6000xp")
    rt["tBat"] = -5
    assert 67 not in runtime_to_registers(rt, "eg4_6000xp")
    # Raw 0.1 A fallback when *Value is absent; omitted when both are absent.
    rt = _load("runtime_12000xp")
    del rt["maxChgCurrValue"]
    del rt["maxDischgCurrValue"]
    del rt["maxDischgCurr"]
    regs = runtime_to_registers(rt, "eg4_12000xp")
    assert regs[81] == 16000
    assert 82 not in regs
    # Too large for a 16-bit register: omitted, never clamped.
    rt = _load("runtime_12000xp")
    rt["maxChgCurrValue"] = 700
    assert 81 not in runtime_to_registers(rt, "eg4_12000xp")
    # Per-leg EPS power only when the portal says it has it.
    rt = _load("runtime_12000xp")
    rt["haspEpsLNValue"] = False
    regs = runtime_to_registers(rt, "eg4_12000xp")
    assert 129 not in regs and 130 not in regs


def test_tinner_zero_is_unknown_even_below_freezing():
    # 12000XP in an unheated shed in winter: the constant tinner 0 is not a reading.
    rt = _load("runtime_12000xp")
    rt.update({"tradiator1": -3, "tradiator2": 0})
    assert 64 not in runtime_to_registers(rt, "eg4_12000xp")
    rt.update({"tradiator1": 0, "tradiator2": 0})
    assert 64 not in runtime_to_registers(rt, "eg4_12000xp")

    # Other models: any non-zero radiator (also sub-zero) marks tinner 0 as fake...
    rt = _load("runtime_18kpv")
    rt.update({"tinner": 0, "tradiator1": -3, "tradiator2": None})
    assert 64 not in runtime_to_registers(rt, "eg4_18kpv")
    # ...but 0 everywhere can be a real 0 °C, and real readings pass through.
    rt.update({"tradiator1": 0, "tradiator2": 0})
    assert runtime_to_registers(rt, "eg4_18kpv")[64] == 0
    assert runtime_to_registers(_load("runtime_18kpv"), "eg4_18kpv")[64] == 39


def test_cloud_extras_placeholder_loads_left_out():
    rt = _load("runtime_12000xp")  # hideConsumption=true, *Show=false, peps=2140
    extras = ch.cloud_extras(rt, None)
    for key in ("consumption_power", "eps_load_power", "grid_load_power", "smart_load_power"):
        assert extras[key] is None, key
    assert extras["bat_power"] == pytest.approx(-1967)

    o = _bare_outputs(influx_enabled=True)
    decoded = decode_registers(runtime_to_registers(rt, "eg4_12000xp"))
    o.write(decoded, {}, source_meta={"data_source": "cloud", "serial": SERIAL, **extras})
    cloud = [line for line in o._post_influx_v1.call_args.args[0].splitlines()
             if line.startswith("luxmon_cloud")]
    assert len(cloud) == 1
    for field in ("consumption_power", "eps_load_power", "grid_load_power", "smart_load_power"):
        assert field not in cloud[0]

    rt.update({
        "hideConsumption": False, "consumptionPower": 2140,
        "epsLoadPowerShow": True, "epsLoadPower": 1800,
        "gridLoadPowerShow": True, "gridLoadPower": 0,
        "smartLoadInverterEnable": True, "smartLoadPower": 340,
    })
    extras = ch.cloud_extras(rt, None)
    assert extras["consumption_power"] == pytest.approx(2140)
    assert extras["eps_load_power"] == pytest.approx(1800)
    assert extras["grid_load_power"] == pytest.approx(0)
    assert extras["smart_load_power"] == pytest.approx(340)


def test_portal_snapshot_sanity():
    rt = _load("runtime_12000xp")
    rt.update({
        "vpv1": 3605, "ppv1": 900, "vpv2": 1290, "ppv2": 83, "pCharge": 553,
        "pDisCharge": 0, "soc": 82, "vBat": 537, "maxChgCurrValue": 400,
        "maxDischgCurrValue": 400, "pEpsL1N": 12, "pEpsL2N": 393, "peps": 356,
    })
    regs = runtime_to_registers(rt, "eg4_12000xp")
    assert regs[81] == 40000
    decoded = decode_registers(regs)
    d = {k: v["value"] for k, v in decoded.items()}
    assert d["bms_max_charge_current"] == pytest.approx(400.0)
    assert d["bms_max_discharge_current"] == pytest.approx(400.0)
    assert d["pv1_voltage"] == pytest.approx(360.5)
    assert d["battery_voltage"] == pytest.approx(53.7)
    assert _build_computed(decoded)["load_power"] == pytest.approx(430)

    en = _load("energy_18kpv")
    en.update({
        "todayYielding": 122, "totalYielding": 16100, "todayDischarging": 2,
        "totalDischarging": 5765, "todayUsage": 29, "totalUsage": 10500,
    })
    e = _decoded(energy_to_registers(en))
    assert e["pv1_energy_today"] == pytest.approx(12.2)
    assert e["pv1_energy_total"] == pytest.approx(1610.0)
    assert e["discharge_energy_today"] == pytest.approx(0.2)
    assert e["discharge_energy_total"] == pytest.approx(576.5)
    assert e["load_energy_today"] == pytest.approx(2.9)
    assert e["load_energy_total"] == pytest.approx(1050.0)


def test_energy_32bit_splits():
    en = _load("energy_18kpv")
    regs = energy_to_registers(en)
    e = _decoded(regs)
    assert e["charge_energy_total"] == pytest.approx(1811.1)
    assert e["discharge_energy_total"] == pytest.approx(1577.8)
    assert e["grid_import_total"] == pytest.approx(4847.5)
    assert e["grid_export_total"] == pytest.approx(3339.0)
    assert e["load_energy_total"] == pytest.approx(6926.9)
    assert e["grid_import_today"] == pytest.approx(19.1)
    # Cross-check the ÷10 divisor against the *Text siblings.
    assert e["charge_energy_today"] == pytest.approx(float(en["todayChargingText"]))
    assert e["grid_import_total"] == pytest.approx(float(en["totalImportText"]))
    # Every pair emits both words.
    for low in (40, 50, 52, 56, 58, 172):
        assert low in regs and low + 1 in regs, low
    # The cloud has no per-string energy.
    for reg in (29, 30, 42, 43, 44, 45):
        assert reg not in regs

    en = {"totalUsage": 70000}
    regs = energy_to_registers(en)
    assert regs[172] == 4464 and regs[173] == 1
    assert _decoded(regs)["load_energy_total"] == pytest.approx(7000.0)

    for bad in (-1, 0x100000000):
        regs = energy_to_registers({"totalUsage": bad, "totalCharging": bad})
        assert regs == {}


def test_battery_modules():
    bat = _load("battery_18kpv")
    regs, soh = battery_to_registers(bat)
    d = _decoded(regs)
    assert soh == 100
    assert d["battery_current"] == pytest.approx(18.1)
    assert d["cell_voltage_max"] == pytest.approx(3.317)
    assert d["cell_voltage_min"] == pytest.approx(3.314)
    assert d["cell_temp_max"] == pytest.approx(25.0)
    assert d["cell_temp_min"] == pytest.approx(24.0)
    assert d["cycle_count"] == pytest.approx(58)
    assert d["bms_charge_voltage_ref"] == pytest.approx(56.0)
    assert d["battery_1_voltage"] == pytest.approx(53.05)
    assert d["battery_1_current"] == pytest.approx(6.0)
    assert d["battery_1_soc"] == pytest.approx(67)
    assert regs[5010] >> 8 == 100
    assert d["battery_2_current"] == pytest.approx(5.4)
    assert d["battery_3_capacity"] == pytest.approx(280)
    assert d["battery_1_max_charge_current"] == pytest.approx(200.0)
    assert regs[5018] == 0x0211
    assert decode_battery_serial(regs, 1) == "Battery_ID_01"
    assert decode_battery_serial(regs, 3) == "Battery_ID_03"
    # base+16/17: max cell number in the high byte, min in the low byte.
    assert regs[5016] == (4 << 8) | 4
    assert regs[5017] == (1 << 8) | 4
    # No cloud field for max discharge current.
    assert 5006 not in regs

    bat = _load("battery_18kpv")
    bat["batteryArray"][2]["current"] = -47
    regs, _ = battery_to_registers(bat)
    assert _decoded(regs)["battery_3_current"] == pytest.approx(-4.7)

    bat = _load("battery_18kpv")
    bat["batteryArray"][1]["lost"] = True
    regs, _ = battery_to_registers(bat)
    decoded = _decoded(regs)
    # (Battery N's 14-register serial runs into block N+1's unused base+0..2,
    # so check decoded names rather than raw register ranges.)
    assert not any(k.startswith("battery_2_") for k in decoded)
    assert decode_battery_serial(regs, 2) == ""
    assert "battery_1_voltage" in decoded and "battery_3_voltage" in decoded

    # Negative cell temperatures would decode as ~6550 °C on the unsigned defs.
    bat = _load("battery_18kpv")
    bat["batteryArray"][0]["batMinCellTemp"] = -20
    regs, _ = battery_to_registers(bat)
    assert 5013 not in regs
    assert _decoded(regs)["cell_temp_min"] == pytest.approx(-2.0)


def test_battery_no_array():
    bat = _load("battery_no_array")
    regs, soh = battery_to_registers(bat)
    # currentText 0.7 with currentType 'unknown' and batPower 0: sign unknown.
    assert regs == {}
    assert soh is None

    bat["currentType"] = "discharge"
    regs, soh = battery_to_registers(bat)
    assert set(regs) == {98}
    assert _decoded(regs)["battery_current"] == pytest.approx(-0.7)
    assert soh is None

    t, _fake, frames = _make()
    assert t.suppressed_fields == frozenset({"soh"})
    assert t.poll_once() is True
    assert t.suppressed_fields == frozenset({"soh"})
    emitted = {f.register + i for f in frames for i in range(len(f.values))}
    assert not any(r >= 5000 for r in emitted)
    assert not {83, 98, 101, 102, 103, 104, 106} & emitted

    # With a module array, SOH is known and lands in the reg 5 high byte.
    t, _fake, frames = _make(_routes(battery=[_resp(body=_load("battery_18kpv"))]))
    assert t.poll_once() is True
    assert t.suppressed_fields == frozenset()
    reg5 = [f.values[5 - f.register] for f in frames if f.register <= 5 < f.register + len(f.values)]
    assert reg5 == [(100 << 8) | 89]


# ── Frames ───────────────────────────────────────────────────────────────────

def test_frames_contiguous_no_zero_fill():
    t, _fake, frames = _make(_routes(battery=[_resp(body=_load("battery_18kpv"))]))
    assert t.poll_once() is True
    assert frames
    for f in frames:
        assert f.is_translated_data
        assert f.is_read_input
        assert f.inverter_serial == SERIAL
        assert f.values
        assert all(0 <= v <= 0xFFFF for v in f.values)
    emitted = [f.register + i for f in frames for i in range(len(f.values))]
    assert len(emitted) == len(set(emitted))

    expected = set(runtime_to_registers(_load("runtime_12000xp"), "eg4_12000xp"))
    expected |= set(energy_to_registers(_load("energy_18kpv")))
    expected |= set(battery_to_registers(_load("battery_18kpv"))[0])
    assert set(emitted) == expected
    for reg in (6, 13, 14, 60, 61, 62, 63, 123, 170):
        assert reg not in emitted, reg

    runtime_frames = registers_to_frames(runtime_to_registers(_load("runtime_12000xp"), "eg4_12000xp"), SERIAL)
    assert [f.register for f in runtime_frames] == [0, 4, 7, 10, 15, 20, 23, 38, 65, 81, 96, 121, 129]
    # Contiguous runs: no run touches the next one.
    for a, b in zip(runtime_frames, runtime_frames[1:]):
        assert a.register + len(a.values) < b.register


# ── Session handling ─────────────────────────────────────────────────────────

def test_session_expiry_html_relogin():
    routes = _routes(runtime=[
        _resp(text=_html(), ctype="text/html;charset=UTF-8"),
        _resp(body=_load("runtime_12000xp")),
    ])
    t, fake, frames = _make(routes)
    assert t.poll_once() is True
    assert fake.count(LOGIN_PATH) == 2
    assert t.stats()["relogins"] == 1
    assert fake.cookies.clear.call_count == 2
    assert frames
    assert t.data_seq == 1


def test_http_401_relogin_once():
    routes = _routes(runtime=[_resp(401, text="Unauthorized", ctype="text/plain")])
    t, fake, frames = _make(routes)
    with pytest.raises(CloudSessionExpired):
        t.poll_once()
    assert t.stats()["relogins"] == 1
    assert fake.count(LOGIN_PATH) == 2
    assert fake.count(RUNTIME_PATH) == 2
    assert frames == []

    delay = t._run_cycle()
    assert delay == pytest.approx(1.0)
    assert t.stats()["consecutive_failures"] == 1
    assert t.stats()["relogins"] == 2  # one more re-login for that cycle, still no loop


def test_success_false():
    rt = _load("runtime_12000xp")
    t, fake, frames = _make(_routes(runtime=[_resp(body={"success": False, "msg": ""}), _resp(body=rt)]))
    assert t.poll_once() is True
    assert t.stats()["relogins"] == 1
    assert fake.count(LOGIN_PATH) == 2

    t, fake, frames = _make(_routes(runtime=[_resp(body={"success": False, "msg": "apiBlocked"})]))
    with pytest.raises(CloudApiError):
        t.poll_once()
    assert t.stats()["relogins"] == 0
    assert fake.count(LOGIN_PATH) == 1
    assert frames == []

    t, fake, frames = _make(_routes(runtime=[_resp(body={"success": False, "message": "DATAFRAME_TIMEOUT"})]))
    with pytest.raises(CloudTransientError):
        t.poll_once()
    assert t.stats()["relogins"] == 0

    t, fake, frames = _make(_routes(runtime=[_resp(503, text="unavailable", ctype="text/html")]))
    with pytest.raises(CloudTransientError):
        t.poll_once()
    t, fake, frames = _make(_routes(runtime=[_resp(403, text="forbidden", ctype="text/html")]))
    with pytest.raises(CloudApiError):
        t.poll_once()


def test_login_rejected_opens_breaker():
    clock = Clock()
    routes = _routes(login=[_resp(body={"success": False, "msg": "Invalid username or password"})])
    t, fake, frames = _make(routes, clock=clock)
    assert t.stats()["auth_failed"] is False
    delay = t._run_cycle()
    assert delay == pytest.approx(ch.AUTH_PAUSE_STEPS[0])
    assert fake.paths() == [LOGIN_PATH]
    assert t.stats()["breaker_open_until"] == pytest.approx(T0 + ch.AUTH_PAUSE_STEPS[0])
    assert "Invalid username or password" in t.stats()["last_error"]
    assert t.stats()["auth_failed"] is True

    clock.t += 100
    calls = fake.post.call_count
    delay = t._run_cycle()
    assert fake.post.call_count == calls
    assert delay == pytest.approx(ch.AUTH_PAUSE_STEPS[0] - 100)
    assert frames == []


def test_login_rejections_escalate():
    """A rejected password is replayed ever more rarely, never every 15 min forever."""
    clock = Clock()
    login = {"ok": False}
    rt = {"n": 0}

    def login_route():
        if login["ok"]:
            return _resp(body=_load("login"))
        return _resp(body={"success": False, "msg": "Invalid username or password"})

    def runtime_route():
        rt["n"] += 1
        body = _load("runtime_12000xp")
        body["serverTime"] = f"2026-01-09 23:{10 + rt['n']:02d}:00"
        return _resp(body=body)

    t, fake, _frames = _make(_routes(login=login_route, runtime=runtime_route), clock=clock)
    delays = []
    for _ in range(6):
        delays.append(t._run_cycle())
        clock.t += delays[-1]
    assert delays == [pytest.approx(d) for d in (900, 3600, 21600, 86400, 86400, 86400)]
    assert fake.count(LOGIN_PATH) == 6
    assert t.stats()["auth_failures"] == 6

    # Over the first day: 4 login attempts, not 96.
    day = Clock()
    t, fake, _frames = _make(_routes(login=login_route, runtime=runtime_route), clock=day)
    while day.t - T0 < 86400:
        day.t += t._run_cycle()
    assert fake.count(LOGIN_PATH) == 4

    # A portal busy message on login is transient, not a rejected password.
    t, fake, _frames = _make(_routes(login=[_resp(body={"success": False, "msg": "DEVICE_BUSY"})]))
    assert t._run_cycle() == pytest.approx(1.0)
    assert t.stats()["auth_failures"] == 0

    # A successful login resets the escalation.
    clock = Clock()
    rt["n"] = 0
    t, fake, _frames = _make(_routes(login=login_route, runtime=runtime_route), clock=clock)
    t._run_cycle()
    login["ok"] = True
    clock.t = t._breaker_until
    assert t._run_cycle() == pytest.approx(t.poll_interval)
    assert t.stats()["auth_failed"] is False
    login["ok"] = False
    t._logged_in_at = 0.0
    assert t._run_cycle() == pytest.approx(ch.AUTH_PAUSE_STEPS[0])


def test_backoff_and_breaker():
    clock = Clock()
    routes = _routes(runtime=[_resp(503, text="busy", ctype="text/html")])
    t, fake, frames = _make(routes, clock=clock)
    # Quick retries, then poll-interval steps capped at BREAKER_PAUSE_SEC.
    delays = []
    for _ in range(8):
        delays.append(t._run_cycle())
        if len(delays) < 8:
            clock.t += delays[-1]
    assert delays == [pytest.approx(d) for d in (1, 2, 4, 60, 120, 240, 300, 300)]
    assert ch.BREAKER_PAUSE_SEC < 900  # below the freeze-check freshness limit
    assert t.stats()["breaker_open_until"] == pytest.approx(clock.t + ch.BREAKER_PAUSE_SEC)
    assert t.stats()["consecutive_failures"] == 8
    assert fake.count(RUNTIME_PATH) == 8
    assert t._backoff_delay(40) == pytest.approx(ch.BREAKER_PAUSE_SEC)

    # Breaker open: no requests until it expires.
    calls = fake.post.call_count
    clock.t += 10
    assert t._run_cycle() == pytest.approx(ch.BREAKER_PAUSE_SEC - 10)
    assert fake.post.call_count == calls

    # A success resets the failure count.
    clock.t += ch.BREAKER_PAUSE_SEC
    fake.routes[RUNTIME_PATH] = [_resp(body=_load("runtime_12000xp"))]
    assert t._run_cycle() == pytest.approx(t.poll_interval)
    assert t.stats()["consecutive_failures"] == 0
    assert t.stats()["breaker_open_until"] == 0
    fake.routes[RUNTIME_PATH] = [_resp(503, text="busy", ctype="text/html")]
    assert t._run_cycle() == pytest.approx(1.0)

    # HTTP 429 skips the quick retries.
    t, fake, frames = _make(_routes(runtime=[_resp(429, text="slow down", ctype="text/plain")]))
    assert [t._run_cycle() for _ in range(3)] == [pytest.approx(d) for d in (60, 120, 240)]


def test_short_outage_recovers_within_a_poll():
    """A ~20 s outage costs about one poll interval of data, not a 15 min pause."""
    clock = Clock()
    outage_end = T0 + 20
    n = {"up": 0}

    def runtime():
        if clock.t < outage_end:
            raise requests.ConnectionError("network unreachable")
        n["up"] += 1
        body = _load("runtime_12000xp")
        body["serverTime"] = f"2026-01-09 23:{10 + n['up']:02d}:00"
        return _resp(body=body)

    t, _fake, _frames = _make(_routes(runtime=runtime), clock=clock)
    while True:
        delay = t._run_cycle()
        if t.data_seq:
            break
        clock.t += delay
    # Failures at +0, +1, +3 and +7 s, then data again one poll interval later.
    assert clock.t - T0 == pytest.approx(7 + t.poll_interval)


def test_jitter_bounds():
    t, _fake, _frames = _make()
    t._rand = lambda: 0.0
    assert t._backoff_delay(3) == pytest.approx(4 * (1 - ch.BACKOFF_JITTER))
    t._rand = lambda: 1.0
    assert t._backoff_delay(3) == pytest.approx(4 * (1 + ch.BACKOFF_JITTER))


# ── Freshness gating ─────────────────────────────────────────────────────────

def test_lost_true_skipped():
    t, fake, frames = _make(_routes(runtime=[_resp(body=_load("runtime_offline"))]))
    assert t.poll_once() is False
    assert frames == []
    assert t.data_seq == 0
    assert t.stats()["lost_skips"] == 1
    assert fake.count(ENERGY_PATH) == 0
    assert fake.count(BATTERY_PATH) == 0

    rt = _load("runtime_12000xp")
    del rt["vBat"]
    t, fake, frames = _make(_routes(runtime=[_resp(body=rt)]))
    assert t.poll_once() is False
    assert frames == [] and t.data_seq == 0

    rt = _load("runtime_12000xp")
    rt["hasRuntimeData"] = False
    t, fake, frames = _make(_routes(runtime=[_resp(body=rt)]))
    assert t.poll_once() is False
    assert frames == [] and t.data_seq == 0

    t, fake, frames = _make(_routes(runtime=[_resp(body={"success": False, "msg": "apiBlocked"})]))
    t._run_cycle()
    assert frames == [] and t.data_seq == 0
    assert t.stats()["errors"] == 1


def test_dedup_unchanged_server_time():
    clock = Clock()
    holder = {"rt": _load("runtime_12000xp")}
    routes = _routes(runtime=lambda: _resp(body=copy.deepcopy(holder["rt"])))
    t, fake, frames = _make(routes, clock=clock)

    assert t.poll_once() is True
    n = len(frames)
    assert t.data_seq == 1
    assert fake.count(ENERGY_PATH) == 1
    assert fake.count(BATTERY_PATH) == 1

    clock.t += 60
    assert t.poll_once() is False
    assert len(frames) == n
    assert t.data_seq == 1
    assert t.stats()["stale_skips"] == 1
    assert fake.count(ENERGY_PATH) == 1

    clock.t += 60
    holder["rt"]["serverTime"] = "2026-01-09 23:11:49"
    holder["rt"]["deviceTime"] = "2026-01-09 17:11:49"
    assert t.poll_once() is True
    assert t.data_seq == 2
    assert fake.count(ENERGY_PATH) == 1  # not due yet (same day, < energy interval)
    assert fake.count(BATTERY_PATH) == 2  # battery: every new upload

    # Local midnight rollover forces an energy poll.
    clock.t += 60
    holder["rt"]["serverTime"] = "2026-01-10 06:00:49"
    holder["rt"]["deviceTime"] = "2026-01-10 00:00:49"
    assert t.poll_once() is True
    assert t.data_seq == 3
    assert fake.count(ENERGY_PATH) == 2
    assert fake.count(BATTERY_PATH) == 3

    # ...and so does the energy interval.
    clock.t += t.energy_interval
    holder["rt"]["serverTime"] = "2026-01-10 06:05:49"
    holder["rt"]["deviceTime"] = "2026-01-10 00:05:49"
    assert t.poll_once() is True
    assert fake.count(ENERGY_PATH) == 3

    meta = t.source_meta()
    assert meta["data_source"] == "cloud"
    assert meta["serial"] == SERIAL
    assert meta["seq"] == t.data_seq == 4
    assert meta["server_time"] == "2026-01-10 06:05:49"
    assert meta["status_text"] == "normal"
    assert meta["fw_code"] == "ceaa-0508"
    assert meta["bat_power"] == pytest.approx(-1967)
    assert meta["bat_status"] == "StandBy"


def test_live_registers_age_out():
    clock = Clock()
    holder = {"rt": _load("runtime_12000xp"), "n": 0}

    def runtime():
        holder["n"] += 1
        rt = copy.deepcopy(holder["rt"])
        rt["serverTime"] = f"2026-01-09 23:{10 + holder['n']:02d}:00"
        return _resp(body=rt)

    t, fake, frames = _make(_routes(runtime=runtime), clock=clock)
    assert t.poll_once() is True
    energy_keys = set(energy_to_registers(_load("energy_18kpv")))
    assert energy_keys <= t.live_registers

    # Energy fetch now fails: the previous group is kept (runtime still emitted)...
    fake.routes[ENERGY_PATH] = [_resp(503, text="busy", ctype="text/html")]
    clock.t += t.energy_interval + 1
    assert t.poll_once() is True
    assert t.data_seq == 2
    assert energy_keys <= t.live_registers

    # ...until it is older than GROUP_MAX_AGE_FACTOR energy intervals.
    clock.t = T0 + ch.GROUP_MAX_AGE_FACTOR * t.energy_interval + 1
    live = t.live_registers
    assert not energy_keys & live
    assert set(runtime_to_registers(_load("runtime_12000xp"), "eg4_12000xp")) <= live


def _epoch(server_time: str) -> float:
    return datetime.strptime(server_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def test_older_upload_never_emitted():
    clock = Clock()
    times = iter(["2026-01-09 23:09:49", "2026-01-09 23:10:49", "2026-01-09 23:09:49", "2026-01-09 23:10:49"])

    def runtime():
        body = _load("runtime_12000xp")
        body["serverTime"] = next(times)
        return _resp(body=body)

    t, _fake, _frames = _make(_routes(runtime=runtime), clock=clock)
    results = []
    for _ in range(4):
        results.append(t.poll_once())
        clock.t += 60
    assert results == [True, True, False, False]
    assert t.data_seq == 2
    assert t.data_time == _epoch("2026-01-09 23:10:49")
    assert t.stats()["stale_skips"] == 2


def test_first_upload_after_start_must_be_recent():
    # One hour after the dongle's last upload (the portal still says lost=false).
    clock = Clock(_epoch("2026-01-09 23:09:49") + 3600)
    holder = {"st": "2026-01-09 23:09:49"}

    def runtime():
        body = _load("runtime_12000xp")
        body["serverTime"] = holder["st"]
        return _resp(body=body)

    t, fake, frames = _make(_routes(runtime=runtime), clock=clock)
    assert t.poll_once() is False
    assert frames == [] and t.data_seq == 0
    assert fake.count(ENERGY_PATH) == 0 and fake.count(BATTERY_PATH) == 0
    assert t.stats()["stale_skips"] == 1
    clock.t += 60
    assert t.poll_once() is False  # still the same frozen upload
    assert t.data_seq == 0

    # The next real upload is emitted.
    holder["st"] = "2026-01-10 00:10:30"
    clock.t += 60
    assert t.poll_once() is True
    assert t.data_seq == 1

    # Within 3 poll intervals + FIRST_UPLOAD_SLACK_SEC the first upload is live data.
    limit = 3 * 60 + ch.FIRST_UPLOAD_SLACK_SEC
    t, _fake, _frames = _make(clock=Clock(_epoch("2026-01-09 23:09:49") + limit - 1))
    assert t.poll_once() is True


def test_midnight_energy_failure_drops_today_counters():
    holder = {"st": "2026-01-10 05:58:00", "dt": "2026-01-09 23:58:00"}
    clock = Clock(_epoch(holder["st"]) + 30)

    def runtime():
        body = _load("runtime_12000xp")
        body["serverTime"] = holder["st"]
        body["deviceTime"] = holder["dt"]
        return _resp(body=body)

    en = _load("energy_18kpv")
    en.update({"todayYielding": 122, "todayUsage": 29})
    t, fake, frames = _make(_routes(runtime=runtime, energy=[_resp(body=en)]), clock=clock)
    assert t.poll_once() is True
    energy_regs = set(energy_to_registers(en))
    today = energy_regs & ch._TODAY_REGS
    totals = energy_regs - ch._TODAY_REGS
    assert today == ch._TODAY_REGS
    assert today <= t.live_registers

    # 00:01 local: the forced energy fetch fails (HTTP 503).
    fake.routes[ENERGY_PATH] = [_resp(503, text="unavailable", ctype="text/html")]
    holder.update(st="2026-01-10 06:01:00", dt="2026-01-10 00:01:00")
    clock.t += 180
    assert t.poll_once() is True
    live = t.live_registers
    assert not today & live  # yesterday's today* values are never written as today's
    assert totals <= live    # lifetime totals are still valid
    assert fake.count(ENERGY_PATH) == 2

    # Retried with the next upload, not a whole energy interval later.
    en_new = dict(en, todayYielding=1, todayUsage=0)
    fake.routes[ENERGY_PATH] = [_resp(body=en_new)]
    holder.update(st="2026-01-10 06:02:00", dt="2026-01-10 00:02:00")
    clock.t += 60
    n = len(frames)
    assert t.poll_once() is True
    assert fake.count(ENERGY_PATH) == 3
    assert today <= t.live_registers
    regs = {f.register + i: v for f in frames[n:] for i, v in enumerate(f.values)}
    assert _decoded(regs)["pv1_energy_today"] == pytest.approx(0.1)

    # Same day again: back to the energy interval.
    holder.update(st="2026-01-10 06:03:00", dt="2026-01-10 00:03:00")
    clock.t += 60
    assert t.poll_once() is True
    assert fake.count(ENERGY_PATH) == 3


def test_battery_current_only_with_its_own_cycle():
    clock = Clock()
    n = {"i": 0}

    def runtime():
        n["i"] += 1
        body = _load("runtime_12000xp")
        body["serverTime"] = f"2026-01-09 23:{10 + n['i']:02d}:00"
        return _resp(body=body)

    bat = _load("battery_18kpv")
    bat.update({"currentText": "37.2", "currentType": "discharge"})
    t, fake, frames = _make(_routes(runtime=runtime, battery=[_resp(body=bat)]), clock=clock)
    assert t.poll_once() is True
    assert 98 in t.live_registers
    assert t.source_meta()["bat_status"] == "Charging"

    def current():
        f = [f for f in frames if f.register <= 98 < f.register + len(f.values)][-1]
        return _decoded({98: f.values[98 - f.register]})["battery_current"]

    assert current() == pytest.approx(-37.2)

    # The next upload's battery fetch fails: the old current is not carried
    # over next to the new charge/discharge power.
    fake.routes[BATTERY_PATH] = [_resp(503, text="unavailable", ctype="text/html")]
    clock.t += 60
    assert t.poll_once() is True
    live = t.live_registers
    assert 98 not in live
    slow = set(battery_to_registers(bat)[0]) - {98}
    assert slow <= live  # cells, SOH and module blocks are kept while young
    meta = t.source_meta()
    assert meta["bat_status"] is None and meta["remaining_ah"] is None

    # Fetched again with the next upload, with the new sign.
    fake.routes[BATTERY_PATH] = [_resp(body=dict(bat, currentType="charge"))]
    clock.t += 60
    assert t.poll_once() is True
    assert 98 in t.live_registers
    assert current() == pytest.approx(37.2)
    assert fake.count(BATTERY_PATH) == 3


def test_stopped_transport_does_not_emit():
    t, _fake, frames = _make()
    t._stop.set()
    assert t.poll_once() is False
    assert frames == [] and t.data_seq == 0


# ── Safety ───────────────────────────────────────────────────────────────────

def test_never_calls_remote_endpoints():
    clock = Clock()
    holder = {"n": 0}
    html_first = [_resp(text=_html(), ctype="text/html")]

    def runtime():
        if html_first:
            return html_first.pop()
        holder["n"] += 1
        rt = _load("runtime_12000xp")
        rt["serverTime"] = f"2026-01-09 23:{10 + holder['n']:02d}:00"
        return _resp(body=rt)

    t, fake, frames = _make(_routes(runtime=runtime), clock=clock)
    for _ in range(4):
        t._run_cycle()
        clock.t += t.energy_interval
    assert t.stats()["relogins"] == 1
    assert t.data_seq == 4
    for call in fake.post.call_args_list:
        url = call.args[0]
        assert urlparse(url).path in ch._ALLOWED_PATHS
        for bad in ("remoteRead", "remoteSet", "maintain"):
            assert bad not in url
        assert call.kwargs["allow_redirects"] is False
        assert call.kwargs["timeout"] == ch.HTTP_TIMEOUT

    calls = fake.post.call_count
    with pytest.raises(ValueError):
        t._post("/WManage/web/maintain/remoteRead/read", {})
    with pytest.raises(ValueError):
        t._post("/WManage/web/maintain/remoteSet/write", {})
    assert fake.post.call_count == calls


def test_credentials_redacted_and_urlencoded(caplog):
    caplog.set_level(logging.DEBUG)
    secrets = (PASSWORD, USERNAME, quote_plus(PASSWORD), quote_plus(USERNAME))

    # Successful cycle: the form dict is passed as-is and requests encodes it.
    t, fake, _frames = _make()
    assert t.poll_once() is True
    login_call = fake.post.call_args_list[0]
    assert urlparse(login_call.args[0]).path == LOGIN_PATH
    assert login_call.kwargs["data"] == {"account": USERNAME, "password": PASSWORD, "language": "ENGLISH"}
    body = requests.Request("POST", BASE_URL + LOGIN_PATH, data=login_call.kwargs["data"]).prepare().body
    assert "password=s3cr3t-P%40ss%26%3Dx" in body
    transports = [t]

    # The server echoes the password in a rejected login.
    t, _fake, _frames = _make(_routes(login=[_resp(body={
        "success": False, "msg": f"bad password {PASSWORD} for {USERNAME}",
    })]))
    t._run_cycle()
    transports.append(t)

    # A connection error carrying the credentials (e.g. in a URL).
    err = requests.ConnectionError(
        f"failed https://x/?account={quote_plus(USERNAME)}&password={quote_plus(PASSWORD)} {PASSWORD} "
        "Cookie: JSESSIONID=ABC123DEF"
    )
    t, _fake, _frames = _make(_routes(runtime=[err]))
    t._run_cycle()
    assert "JSESSIONID=***" in t.stats()["last_error"]
    transports.append(t)

    # An unexpected crash whose message (and traceback) carries the password.
    t, _fake, _frames = _make()
    t.poll_once = MagicMock(side_effect=RuntimeError(f"boom {PASSWORD} {USERNAME}"))
    t._run_cycle()
    transports.append(t)

    assert "cloud poll crashed" in caplog.text
    cfg = CollectorConfig(cloud_username=USERNAME, cloud_password=PASSWORD)
    for secret in secrets:
        assert secret not in caplog.text
        assert secret not in repr(cfg)
        for tr in transports:
            assert secret not in str(tr.stats())
            assert secret not in repr(tr)


def test_thread_survives_exception(caplog):
    t, _fake, _frames = _make()
    t.poll_once = MagicMock(side_effect=RuntimeError("boom"))
    delay = t._run_cycle()
    assert delay == pytest.approx(1.0)
    assert "cloud poll crashed" in caplog.text

    t.start()
    try:
        time.sleep(0.2)
        assert t._thread.is_alive()
    finally:
        t.stop()
    assert not t._thread.is_alive()


# ── Collector integration ────────────────────────────────────────────────────

class FakeCloudTransport:
    """Minimal transport exposing the cloud writer-gating interface."""

    def __init__(self):
        self.data_seq = 0
        self.data_time = None
        self.poll_interval = 60.0
        self.inverter_serial = SERIAL
        self.live_registers = frozenset()
        self.emit_lock = Lock()
        self.suppressed_fields = frozenset({"soh"})
        self.meta = {"data_source": "cloud", "serial": SERIAL, "seq": 0}

    def source_meta(self):
        return dict(self.meta)

    def stats(self):
        return {}

    def stop(self):
        pass


def _collector(monkeypatch, transport_name, driver="eg4_12000xp"):
    monkeypatch.setattr("collector.collector.signal.signal", lambda *a, **k: None)
    cfg = CollectorConfig(
        transport=transport_name,
        datalog_serial="DL00000001",
        inverter_serial=SERIAL,
        outputs=OutputConfig(mariadb_enabled=False, influx_enabled=False, mqtt_enabled=False),
    )
    c = PassiveCollector(cfg, driver=get_driver(driver))
    c._outputs = MagicMock()
    c._quick_charge = MagicMock()
    c._automation = MagicMock()
    monkeypatch.setattr(c, "_maybe_refresh_forecast", lambda: None)
    return c


def test_writer_gating(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    c = _collector(monkeypatch, "cloud_http")
    t = FakeCloudTransport()
    c._transport = t
    regs = runtime_to_registers(_load("runtime_12000xp"), "eg4_12000xp")
    t.live_registers = frozenset(regs)
    c._latest_input_raw = dict(regs)
    c._latest_input_raw[28] = 999  # stale register the cloud no longer reports

    c._write_once()
    assert c._outputs.write.call_count == 0  # seq 0: nothing delivered yet

    t.data_seq = 1
    t.meta["seq"] = 1
    c._write_once()
    assert c._outputs.write.call_count == 1
    call = c._outputs.write.call_args
    decoded, raw = call.args
    assert call.kwargs["interval_sec"] == pytest.approx(c.cfg.cloud_poll_interval)
    assert call.kwargs["source_meta"] == t.meta
    assert 28 not in raw
    assert set(raw) == set(regs)
    assert "pv1_energy_today" not in decoded
    assert "soh" not in decoded
    assert decoded["battery_voltage"]["value"] == pytest.approx(52.9)

    c._write_once()
    assert c._outputs.write.call_count == 1  # same seq: no rewrite of stale data

    t.data_seq = 2
    c._write_once()
    assert c._outputs.write.call_count == 2
    interval = c._outputs.write.call_args.kwargs["interval_sec"]
    assert 0 <= interval <= c._GATED_INTERVAL_CAP_SEC

    c._quick_charge.tick.assert_not_called()
    c._automation.evaluate_and_apply.assert_not_called()
    assert caplog.text.count("quick-charge and automation ticks disabled") == 1

    # Control: a dongle transport (no data_seq) writes every tick, as before.
    c = _collector(monkeypatch, "tcp_active")
    monkeypatch.setattr("collector.collector._load_db_setting", lambda name, cfg: None)
    c._transport = SimpleNamespace(stats=lambda: {}, stop=lambda: None)
    c._latest_input_raw = dict(regs)
    c._latest_input_raw[28] = 999
    c._write_once()
    c._write_once()
    assert c._outputs.write.call_count == 2
    call = c._outputs.write.call_args
    assert call.args[1] is c._latest_input_raw
    assert call.kwargs["interval_sec"] is None
    assert call.kwargs["source_meta"] is None
    assert "soh" in call.args[0]
    assert c._quick_charge.tick.call_count == 2
    assert c._automation.evaluate_and_apply.call_count == 2


def _gated_collector(monkeypatch, poll_interval=60.0):
    c = _collector(monkeypatch, "cloud_http")
    t = FakeCloudTransport()
    t.poll_interval = poll_interval
    c._transport = t
    regs = runtime_to_registers(_load("runtime_12000xp"), "eg4_12000xp")
    t.live_registers = frozenset(regs)
    c._latest_input_raw = dict(regs)
    return c, t


def test_hourly_interval_follows_upload_spacing(monkeypatch):
    # Uploads every 300 s with a 30 s poll: the whole hour is integrated.
    c, t = _gated_collector(monkeypatch, poll_interval=30.0)
    c.cfg.cloud_poll_interval = 10  # raw setting below the minimum; the transport uses 30
    intervals = []
    for i in range(13):
        t.data_seq = i + 1
        t.data_time = T0 + 300 * i
        c._write_once()
        intervals.append(c._outputs.write.call_args.kwargs["interval_sec"])
    assert intervals[0] == pytest.approx(30.0)  # first write: the transport's effective poll
    assert sum(intervals[1:]) == pytest.approx(3600)

    # An outage longer than the cap counts as the cap, not as one long reading.
    t.data_seq += 1
    t.data_time += 3 * 3600
    c._write_once()
    assert c._outputs.write.call_args.kwargs["interval_sec"] == pytest.approx(c._GATED_INTERVAL_CAP_SEC)

    # Without upload times: time since the last write, capped at the same limit.
    c, t = _gated_collector(monkeypatch)
    wall = {"t": T0}
    monkeypatch.setattr("collector.collector.time.time", lambda: wall["t"])
    t.data_seq = 1
    c._write_once()
    wall["t"] += 300
    t.data_seq = 2
    c._write_once()
    assert c._outputs.write.call_args.kwargs["interval_sec"] == pytest.approx(300)


def test_redelivered_upload_not_written_again(monkeypatch):
    c, t = _gated_collector(monkeypatch)
    t.data_seq, t.data_time = 5, T0
    c._write_once()
    assert c._outputs.write.call_count == 1

    # A new transport (config reload) re-emits the same upload as its seq 1.
    c._last_written_seq = None
    t.data_seq = 1
    c._write_once()
    c._write_once()
    assert c._outputs.write.call_count == 1

    t.data_seq, t.data_time = 2, T0 + 60
    c._write_once()
    assert c._outputs.write.call_count == 2
    assert c._outputs.write.call_args.kwargs["interval_sec"] == pytest.approx(60)

    # A different inverter's (older) upload is still new data.
    t.inverter_serial = "TEST000002"
    t.data_seq, t.data_time = 3, T0 - 600
    c._write_once()
    assert c._outputs.write.call_count == 3


def test_gated_write_consumed_when_a_later_step_fails(monkeypatch):
    c, t = _gated_collector(monkeypatch)
    # e.g. paho: "Publish topic cannot contain wildcards" (MQTT prefix with '+').
    c._outputs.evaluate_alerts.side_effect = ValueError("Publish topic cannot contain wildcards")
    t.data_seq, t.data_time = 1, T0
    for _ in range(12):  # one minute of 5 s writer ticks
        c._write_once()
    assert c._outputs.write.call_count == 1

    c._outputs.write.side_effect = ValueError("Publish topic cannot contain wildcards")
    t.data_seq, t.data_time = 2, T0 + 60
    for _ in range(12):
        c._write_once()
    assert c._outputs.write.call_count == 2
    assert c._last_written_seq == 2


def test_writer_requires_battery_voltage(monkeypatch):
    c = _collector(monkeypatch, "cloud_http")
    t = FakeCloudTransport()
    c._transport = t
    regs = runtime_to_registers(_load("runtime_12000xp"), "eg4_12000xp")
    c._latest_input_raw = dict(regs)
    t.live_registers = frozenset(r for r in regs if r != 4)
    t.data_seq = 1
    c._write_once()
    assert c._outputs.write.call_count == 0


def _clamp(model, values):
    c = PassiveCollector.__new__(PassiveCollector)
    c.driver = get_driver(model)
    decoded = {k: {"value": float(v)} for k, v in values.items()}
    c._clamp_values(decoded)
    return {k: d["value"] for k, d in decoded.items()}


def test_clamps_driver_aware():
    before = dict(PassiveCollector._SANITY_LIMITS)
    assert _clamp("eg4_12000xp", {"pv1_power": 12000, "discharge_power": 9000}) == {
        "pv1_power": 12000, "discharge_power": 9000,
    }
    assert _clamp("eg4_12000xp", {"pv1_power": 20000})["pv1_power"] == 14000
    assert _clamp("eg4_12000xp", {"bms_max_charge_current": 400})["bms_max_charge_current"] == 400
    assert _clamp("eg4_12000xp", {"battery_voltage": 70})["battery_voltage"] == 65
    assert _clamp("eg4_6000xp", {"pv1_power": 12000})["pv1_power"] == 8000
    assert PassiveCollector._SANITY_LIMITS == before
    assert get_driver("eg4_6000xp").sanity_limits is None
    assert get_driver("luxpower_sna").sanity_limits is None
    a, b = get_driver("eg4_12000xp"), get_driver("eg4_12000xp")
    assert a.sanity_limits == b.sanity_limits and a.sanity_limits is not b.sanity_limits


def _bare_outputs(**cfg):
    o = Outputs.__new__(Outputs)
    o.cfg = OutputConfig(mariadb_enabled=False, mqtt_enabled=False, **cfg)
    o._influx_client = None
    o._mqtt_client = None
    o._post_influx_v1 = MagicMock()
    return o


def test_influx_luxmon_cloud_line():
    o = _bare_outputs(influx_enabled=True)
    decoded = decode_registers(runtime_to_registers(_load("runtime_12000xp"), "eg4_12000xp"))
    meta = {
        "data_source": "cloud", "serial": SERIAL, "seq": 3,
        "server_time": "2026-01-09 23:09:49", "data_age_s": 30.0,
        "status_text": "normal", "fw_code": "ceaa-0508", "ppv_total": 271.0,
        "bat_power": -1967.0, "smart_load_power": 0.0, "eps_load_power": 0.0,
        "grid_load_power": 0.0, "consumption_power": 0.0, "ac_couple_power": 0.0,
        "bat_status": None, "remaining_ah": None,
    }
    o.write(decoded, {}, source_meta=meta)
    lines = o._post_influx_v1.call_args.args[0].splitlines()
    cloud = [line for line in lines if line.startswith("luxmon_cloud")]
    assert len(cloud) == 1
    assert cloud[0].startswith("luxmon_cloud,data_source=cloud,serial=TEST000001 ")
    assert 'server_time="2026-01-09 23:09:49"' in cloud[0]
    assert "seq=3.0" in cloud[0]
    assert "bat_status" not in cloud[0]  # None fields are skipped

    o._post_influx_v1.reset_mock()
    o.write(decoded, {})
    lines = o._post_influx_v1.call_args.args[0].splitlines()
    assert not any(line.startswith("luxmon_cloud") for line in lines)
    register_lines = [line for line in lines if line.startswith("luxmon_register")]
    assert register_lines
    assert not any("data_source" in line for line in register_lines)


def test_hourly_energy_interval():
    o = _bare_outputs()
    o.cfg._write_interval = 5.0
    decoded = {"pv1_power": {"value": 900.0}, "pv2_power": {"value": 83.0}}

    def pv_value(cur):
        rows = [c.args[1] for c in cur.execute.call_args_list if c.args[1][1] == "PV power"]
        assert len(rows) == 1
        return rows[0][2]

    cur = MagicMock()
    o._update_hourly_energy(cur, decoded, "lux_", interval_sec=60)
    assert pv_value(cur) == pytest.approx(983 * 60 / 3600)

    cur = MagicMock()
    o._update_hourly_energy(cur, decoded, "lux_")
    assert pv_value(cur) == pytest.approx(983 * 5 / 3600)


# ── Configuration ────────────────────────────────────────────────────────────

def test_config_and_factory(monkeypatch):
    monkeypatch.setenv("LUX_CLOUD_BASE_URL", "https://portal.example.com")
    monkeypatch.setenv("LUX_CLOUD_USERNAME", "someone@example.com")
    monkeypatch.setenv("LUX_CLOUD_PASSWORD", "pw")
    monkeypatch.setenv("LUX_CLOUD_POLL_SEC", "45")
    monkeypatch.setenv("LUX_CLOUD_ENERGY_SEC", "600")
    cfg = config_from_env()
    assert cfg.cloud_base_url == "https://portal.example.com"
    assert cfg.cloud_username == "someone@example.com"
    assert cfg.cloud_password == "pw"
    assert cfg.cloud_poll_interval == 45.0
    assert cfg.cloud_energy_interval == 600.0

    drv = get_driver("eg4_12000xp")
    noop = lambda frame: None  # noqa: E731
    with pytest.raises(ValueError, match="LUX_CLOUD_USERNAME"):
        _create_transport(CollectorConfig(transport="cloud_http", inverter_serial=SERIAL), noop, drv)
    with pytest.raises(ValueError, match="LUX_INVERTER_SERIAL"):
        _create_transport(
            CollectorConfig(transport="cloud_http", cloud_username="u", cloud_password="p"), noop, drv,
        )
    t = _create_transport(
        CollectorConfig(transport="cloud_http", cloud_username="u", cloud_password="p",
                        inverter_serial=SERIAL, cloud_poll_interval=10),
        noop, drv,
    )
    assert isinstance(t, CloudHttpTransport)
    assert t.poll_interval == 30
    assert t.energy_interval == 300
    assert t.model == "eg4_12000xp"
    assert t.base_url == BASE_URL

    with pytest.raises(ValueError, match="https"):
        CloudHttpTransport(noop, "http://monitor.eg4electronics.com", "u", "p", SERIAL)
    with pytest.raises(ValueError, match="LUX_CLOUD_PASSWORD"):
        CloudHttpTransport(noop, BASE_URL, "u", "", SERIAL)

    assert "cloud_http" in TRANSPORT_OPTIONS
    assert "cloud_http" in [value for value, _label in SETTING_META["transport"]["options"]]
    assert validate_setting("transport", "bogus")
    assert validate_setting("transport", "cloud_http") is None
    assert validate_setting("timezone", "Anything/Goes") is None
    assert not any("cloud" in key for key in SETTING_ENV)
    assert not any(env.startswith("LUX_CLOUD") for env, _cast in SETTING_ENV.values())
    assert not any("cloud" in key for key in DEFAULTS)


def test_api_guards(monkeypatch):
    api = pytest.importorskip("api")
    from fastapi import HTTPException

    monkeypatch.setattr(api, "_load_db_setting", lambda name: "cloud_http" if name == "transport" else None)
    with pytest.raises(HTTPException) as exc:
        api._require_dongle_transport()
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        api.api_quick_charge_start(api.QuickChargeBody(minutes=5, dry_run=True))
    assert exc.value.status_code == 409

    monkeypatch.setattr(api, "_load_db_setting", lambda name: "tcp_active" if name == "transport" else None)
    api._require_dongle_transport()

    # An invalid transport is rejected before any DB access.
    monkeypatch.setattr(api, "_get_conn", MagicMock(side_effect=AssertionError("no DB access expected")))
    with pytest.raises(HTTPException) as exc:
        api.api_setting_put("transport", api.SettingUpdate(value="bogus"))
    assert exc.value.status_code == 422


def test_api_batteries_unknown_module_soh(monkeypatch):
    api = pytest.importorskip("api")

    def serve(regs):
        cur = MagicMock()
        cur.fetchone.return_value = (1, json.dumps({str(k): v for k, v in regs.items()}))
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        monkeypatch.setattr(api, "_get_conn", lambda: conn)
        return api.api_batteries()["batteries"]

    # Offline / non-EG4 payloads send soh as an empty string.
    bat = _load("battery_18kpv")
    for module in bat["batteryArray"]:
        module["soh"] = ""
    regs, soh = battery_to_registers(bat)
    assert soh is None
    assert regs[5010] == 67  # SOC 67 in the low byte, SOH byte 0 (unknown)
    batteries = serve(regs)
    assert [b["soh_pct"] for b in batteries] == [None, None, None]
    assert [b["soc_pct"] for b in batteries] == [67, 71, 76]

    # A reported SOH is unchanged.
    batteries = serve(battery_to_registers(_load("battery_18kpv"))[0])
    assert [b["soh_pct"] for b in batteries] == [100, 100, 100]
