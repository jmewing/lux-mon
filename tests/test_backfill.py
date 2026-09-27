"""Tests for the EG4 data-export backfill (python -m collector.backfill).

No network, no database: rows come from tests/fixtures/backfill/ (scrubbed
real export rows, see the README there), HTTP is faked, and InfluxDB is a
recording stand-in. The real workbook at REAL_XLS is parsed only when present.
"""
import copy
import json
import logging
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pytest
import xlrd

from collector import backfill as bf
from collector.backfill import (
    Backfiller,
    BackfillError,
    ExportClient,
    ExportSheet,
    MapContext,
    Runner,
    Settings,
    build_parser,
    cell_number,
    classify_export,
    compare_points,
    decode_registers_like_live,
    export_get_allowed,
    find_first_day,
    format_comparison,
    iter_windows,
    load_settings,
    localize_rows,
    parse_book,
    row_to_registers,
    sheet_points,
    status_code,
)
from collector.collector import CollectorConfig
from collector.comm import cloud_http as ch
from collector.comm.cloud_http import (
    LOGIN_PATH,
    RUNTIME_PATH,
    CloudApiError,
    CloudAuthError,
    battery_to_registers,
    energy_to_registers,
    runtime_to_registers,
)
from collector.drivers.registry import get_driver
from collector.outputs import (
    _REGISTER_TO_SA,
    OutputConfig,
    Outputs,
    convert_temperatures,
)

FIXTURES = Path(__file__).parent / "fixtures"
ROWS = json.loads((FIXTURES / "backfill" / "export_rows_12000xp.json").read_text())
REAL_XLS = Path(os.environ.get(
    "LUX_BACKFILL_REAL_XLS",
    "/private/tmp/claude-501/-Users-rob-Code-Claude/ca86b008-4cea-4020-a9ec-a0d06fd0175a/scratchpad/probe/export_2026-09-24.xls",
))
SERIAL = "TEST000001"
LA = ZoneInfo("America/Los_Angeles")
SA_MEASUREMENTS = {m for m, _ in _REGISTER_TO_SA.values()}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _row(time_text: str) -> dict:
    for rows in ROWS["sheets"].values():
        for row in rows:
            if row["Time"].endswith(time_text):
                return copy.deepcopy(row)
    raise KeyError(time_text)


def _sheets() -> list:
    return [ExportSheet(day=day, rows=copy.deepcopy(rows)) for day, rows in sorted(ROWS["sheets"].items())]


def _ctx(model="eg4_12000xp", unit="fahrenheit", tz=LA) -> MapContext:
    return MapContext(model=model, driver=get_driver(model), temperature_unit=unit, tz=tz)


def _decoded(row: dict, ctx: MapContext = None) -> dict:
    ctx = ctx or _ctx()
    regs, suppressed, _ = row_to_registers(row, ctx.model)
    return decode_registers_like_live(regs, suppressed, ctx)


def _values(decoded: dict) -> dict:
    return {k: v["value"] for k, v in decoded.items()}


def _load_cloud(name: str) -> dict:
    return json.loads((FIXTURES / "cloud" / f"{name}.json").read_text())


def _utc(*args) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


def _unescaped(text: str, stop: str) -> int:
    """Index of the first `stop` char not escaped with a backslash (or len)."""
    i = 0
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == stop:
            return i
        i += 1
    return len(text)


def _series(line: str) -> str:
    """Line-protocol series key: measurement plus tags."""
    return line[:_unescaped(line, " ")]


def _measurement(line: str) -> str:
    series = _series(line)
    return series[:_unescaped(series, ",")].replace("\\ ", " ").replace("\\,", ",")


def _stamp(line: str) -> int:
    return int(line.rsplit(" ", 1)[1])


class FakeTarget:
    """InfluxTarget stand-in that records writes."""

    def __init__(self, first_soc=None, recorded=None, units=None, live=None):
        self.first_soc = first_soc
        self.recorded = recorded
        self.units = units or {}
        self.live = live or {}
        self.writes = []
        self.closed = False

    def recorded_live_start(self):
        return self.recorded

    def first_soc_time(self):
        return self.first_soc

    def live_units(self, since):
        return dict(self.units)

    def register_points(self, start, stop):
        return self.live

    def write(self, lines):
        self.writes.append(list(lines))

    def close(self):
        self.closed = True

    def lines(self):
        return [line for batch in self.writes for line in batch]


class StoreTarget(FakeTarget):
    """FakeTarget whose cutoff queries read the written lines, as InfluxDB would.

    `store` is shared between runs; the live data's first soc point is
    `live_first_soc`. With fail_on_batch, that data batch raises
    KeyboardInterrupt (Ctrl-C part-way through a window).
    """

    def __init__(self, store, live_first_soc, fail_on_batch=None, **kw):
        super().__init__(**kw)
        self.store = store
        self.live_first_soc = live_first_soc
        self.fail_on_batch = fail_on_batch
        self.data_batches = 0

    def recorded_live_start(self):
        values = [float(v) for line in self.store if line.startswith(bf.BACKFILL_MEASUREMENT)
                  for v in re.findall(r"[ ,]live_start=([-0-9.e+]+)", line)]
        return min(values) if values else None

    def first_soc_time(self):
        stored = [_stamp(line) / 10**9 for line in self.store if line.startswith("luxmon_register,name=soc,")]
        return min(stored + [self.live_first_soc])

    def write(self, lines):
        if not lines[0].startswith(bf.BACKFILL_MEASUREMENT):
            self.data_batches += 1
            if self.data_batches == self.fail_on_batch:
                raise KeyboardInterrupt
        super().write(lines)
        self.store.extend(lines)


# ── Cell parsing ─────────────────────────────────────────────────────────────

def test_cell_number():
    assert cell_number("23%") == 23
    assert cell_number(" 100% ") == 100
    assert cell_number("0x4000000") == 0x4000000
    assert cell_number("0xC003") == 0xC003
    assert cell_number("-0.5") == -0.5
    assert cell_number(22.0) == 22
    assert cell_number("262.2") == pytest.approx(262.2)
    for bad in ("", "  ", "[0]", "abc", "%", None, True, float("nan"), float("inf"), [1]):
        assert cell_number(bad) is None, bad


class FakeCell:
    def __init__(self, ctype, value):
        self.ctype, self.value = ctype, value


class FakeSheet:
    def __init__(self, name, grid):
        self.name = name
        self._grid = grid
        self.nrows = len(grid)
        self.ncols = max((len(r) for r in grid), default=0)

    def cell(self, r, c):
        return self._grid[r][c]

    def cell_value(self, r, c):
        return self._grid[r][c].value


class FakeBook:
    datemode = 0

    def __init__(self, sheets):
        self._sheets = sheets
        self.nsheets = len(sheets)

    def sheet_by_index(self, i):
        return self._sheets[i]


def _t(v):
    return FakeCell(xlrd.XL_CELL_TEXT, v)


def _n(v):
    return FakeCell(xlrd.XL_CELL_NUMBER, float(v))


def _e():
    return FakeCell(xlrd.XL_CELL_EMPTY, "")


def test_parse_book_headers_dates_blanks_and_order():
    serial_date = xlrd.xldate.xldate_from_datetime_tuple((2026, 9, 25, 0, 3, 14), 0)
    day2 = FakeSheet("2026-09-25", [
        [_t("Serial number"), _t("Time"), _t("SOC(%)"), _t("SOC(%)"), _t("Debug140")],
        [_t(SERIAL), FakeCell(xlrd.XL_CELL_DATE, serial_date), _t("23%"), _n(24), _e()],
        [_e(), _e(), _e(), _e(), _e()],  # blank row
        [_t(SERIAL), _t("2026/09/25 00:07:16"), _t("23%"), _n(24), _e()],
    ])
    day1 = FakeSheet("2026-09-24", [[_t("Serial number"), _t("Time")], [_t(SERIAL), _t("2026/09/24 23:59:12")]])
    other = FakeSheet("Summary", [[_t("x")], [_t("y")]])
    sheets = parse_book(FakeBook([day2, other, day1]))
    assert [s.day for s in sheets] == ["2026-09-24", "2026-09-25"]  # sorted; non-day sheet ignored
    rows = sheets[1].rows
    assert len(rows) == 2
    assert rows[0]["Time"] == "2026-09-25 00:03:14"  # date cell -> text
    assert rows[0]["SOC(%)"] == "23%" and rows[0]["SOC(%).1"] == 24.0  # duplicate header kept
    assert rows[0]["Debug140"] == ""
    assert bf.parse_local_time(rows[0]["Time"]) == datetime(2026, 9, 25, 0, 3, 14)
    assert bf.parse_local_time(rows[1]["Time"]) == datetime(2026, 9, 25, 0, 7, 16)


def test_parse_workbook_rejects_non_xls():
    with pytest.raises(bf.ExportFormatError):
        bf.parse_workbook(b"<html>login</html>")
    with pytest.raises(bf.ExportFormatError):
        bf.parse_workbook(b"PK\x03\x04 an xlsx")
    with pytest.raises(bf.ExportFormatError):
        bf.parse_workbook(bf._XLS_MAGIC + b"\0" * 64)  # right signature, corrupt body


# ── Row mapping ──────────────────────────────────────────────────────────────

def test_display_values_round_trip():
    row = _row("11:59:32")  # vpv1 262.2, SOC 30%, tradiator1 49 °C
    assert row["vpv1"] == "262.2" and row["SOC(%)"] == "30%" and row["tradiator1"] == 49.0
    d = _decoded(row, _ctx(unit="fahrenheit"))
    v = _values(d)
    assert v["pv1_voltage"] == pytest.approx(262.2) and d["pv1_voltage"]["unit"] == "V"
    assert v["soc"] == 30 and d["soc"]["unit"] == "%"
    assert v["temp_radiator_1"] == pytest.approx(120.2) and d["temp_radiator_1"]["unit"] == "°F"
    assert v["battery_voltage"] == pytest.approx(52.9)
    assert v["eps_frequency"] == pytest.approx(cell_number(row["feps"]))
    assert v["eps_voltage_r"] == pytest.approx(cell_number(row["vepsr"]))
    assert v["bms_max_charge_current"] == pytest.approx(400)

    c = _decoded(row, _ctx(unit="celsius"))
    assert c["temp_radiator_1"]["value"] == pytest.approx(49) and c["temp_radiator_1"]["unit"] == "°C"

    # Every display column that is mapped comes back unchanged.
    for column, name in (("vpv2", "pv2_voltage"), ("ppv1", "pv1_power"), ("ppv2", "pv2_power"),
                         ("pCharge", "charge_power"), ("pDisCharge", "discharge_power"),
                         ("peps", "eps_power"), ("seps", "eps_apparent_power"),
                         ("vBus1", "bus1_voltage"), ("vBus2", "bus2_voltage"),
                         ("pEpsL1N", "eps_power_l1"), ("pEpsL2N", "eps_power_l2")):
        assert v[name] == pytest.approx(cell_number(row[column])), column


def test_energy_semantics_match_live():
    row = _row("2026/09/25 23:56:51")
    v = _values(_decoded(row))
    # Total PV yield in the pv1 slot (the cloud has no per-string energy).
    assert v["pv1_energy_today"] == pytest.approx(4.8 + 1.9)
    assert v["pv1_energy_total"] == pytest.approx(1123.8 + 476.1)
    # Load = backup-port energy + grid-to-user energy (the portal's Consumption).
    assert v["load_energy_today"] == pytest.approx(cell_number(row["eEpsDay"]) + cell_number(row["eToUserDay"]))
    assert v["load_energy_total"] == pytest.approx(1049.3)
    assert v["charge_energy_today"] == pytest.approx(5.7)
    assert v["charge_energy_total"] == pytest.approx(961.6)
    assert v["discharge_energy_total"] == pytest.approx(576.3)
    assert v["grid_import_total"] == 0 and v["grid_export_total"] == 0
    for absent in ("pv2_energy_today", "pv3_energy_today", "pv2_energy_total", "pv3_energy_total",
                   "eps_energy_today", "eps_energy_total", "inv_energy_total", "rec_energy_total",
                   "gen_energy_today", "gen_energy_total", "eps_energy_l1_today"):
        assert absent not in v, absent

    # Same registers the live energy mapping produces for the same totals.
    regs, _, _ = row_to_registers(row, "eg4_12000xp")
    live = energy_to_registers({
        "todayYielding": 67, "todayCharging": 57, "todayDischarging": 5, "todayExport": 0,
        "todayImport": 0, "todayUsage": 5, "totalYielding": 15999, "totalCharging": 9616,
        "totalDischarging": 5763, "totalExport": 0, "totalImport": 0, "totalUsage": 10493,
    })
    assert {r: regs[r] for r in live} == live


def test_register_set_matches_live_transport():
    """Backfilled names/units equal what the live cloud transport writes for a 12000XP."""
    ctx = _ctx()
    live_regs = runtime_to_registers(_load_cloud("runtime_12000xp"), "eg4_12000xp")
    live_regs.update(energy_to_registers(_load_cloud("energy_18kpv")))
    live_regs.update(battery_to_registers({"currentText": "7.5", "currentType": "discharge"})[0])
    live = convert_temperatures(get_driver("eg4_12000xp").decode(live_regs), "fahrenheit")
    live.pop("soh")  # suppressed live: no battery modules

    back = _decoded(_row("13:24:05"), ctx)
    live_units = {k: v["unit"] for k, v in live.items()}
    back_units = {k: v["unit"] for k, v in back.items()}
    # Runtime-only fields the export has no column for; export-only BMS data.
    assert set(live_units) - set(back_units) == {"battery_count", "battery_capacity"}
    assert set(back_units) - set(live_units) == {"soh", "cell_temp_max"}
    for name in set(live_units) & set(back_units):
        assert back_units[name] == live_units[name], name


def test_quirks_and_unmapped_columns():
    row = _row("00:03:14")
    assert row["pf"] == "[0]" and row["tinner"] == 0.0 and row["tBat"] == 0.0
    v = _values(_decoded(row))
    for absent in ("temp_inverter", "temp_battery", "power_factor", "pv3_voltage", "pv3_power",
                   "grid_voltage_s", "grid_voltage_t", "eps_voltage_s", "eps_voltage_t",
                   "gen_power", "load_power", "fault_code", "warning_code", "internal_fault",
                   "battery_voltage_inv", "bus_p_voltage", "eps_voltage_l1n", "bms_status_0",
                   "bms_charge_voltage_ref", "cell_voltage_max", "cell_voltage_min", "cycle_count",
                   "battery_count"):
        assert absent not in v, absent
    assert v["gen_voltage"] == 0 and v["gen_frequency"] == 0

    # tBat 0 is only "unknown" on the 12000XP, exactly as live.
    assert _values(_decoded(row, _ctx(model="eg4_6000xp")))["temp_battery"] == pytest.approx(32.0)
    # A real pf string is mapped like live.
    row["pf"] = "0.98"
    assert _values(_decoded(row))["power_factor"] == pytest.approx(0.98)


def test_state_from_status_text():
    assert status_code("Standby") == 0
    assert status_code("PV Charge") == 0x08
    assert status_code("Battery Grid off") == 0x40
    assert status_code("PV&Battery Grid off") == 0xC0
    assert status_code(" pv & battery  grid OFF ") == 0xC0
    for bad in ("", None, "Fault?", 64):
        assert status_code(bad) is None

    assert _values(_decoded(_row("07:15:18")))["state"] == 0
    assert _values(_decoded(_row("08:43:51")))["state"] == 0x08
    assert _values(_decoded(_row("00:03:14")))["state"] == 0x40
    assert _values(_decoded(_row("13:24:05")))["state"] == 0xC0

    row = _row("13:24:05")
    row["Status"] = "Something new"
    regs, _, unknown = row_to_registers(row, "eg4_12000xp")
    assert 0 not in regs and unknown == "Something new"
    stats = bf.Stats()
    sheet_points(ExportSheet("2026-09-25", [row]), _ctx(), stats)
    assert stats.unknown_status == {"Something new": 1}


def test_battery_current_cells_and_soh():
    assert _values(_decoded(_row("00:03:14")))["battery_current"] == 0
    assert _values(_decoded(_row("00:07:16")))["battery_current"] == pytest.approx(-0.5)
    assert _values(_decoded(_row("13:24:05")))["battery_current"] == pytest.approx(22.6)

    # Zero cell voltages / cycles are "not reported"; the real MaxCellTemp is kept.
    row = _row("13:24:05")
    d = _decoded(row)
    assert "cell_voltage_max" not in d and "cell_voltage_min" not in d and "cycle_count" not in d
    assert "cell_temp_min" not in d
    assert d["cell_temp_max"]["value"] == pytest.approx(cell_number(row["MaxCellTemp"]))
    assert d["cell_temp_max"]["unit"] == "°C"  # not a display temperature: never °F
    assert d["soh"]["value"] == 100

    row.update({"MaxCellVoltage": "3.325", "MinCellVoltage": "3301", "CycleCnt": "12",
                "MinCellTemp": "-2.5", "SOH": ""})
    v = _values(_decoded(row))
    assert v["cell_voltage_max"] == pytest.approx(3.325)
    assert v["cell_voltage_min"] == pytest.approx(3.301)  # mV accepted
    assert v["cycle_count"] == 12
    assert v["cell_temp_min"] == pytest.approx(-2.5)
    assert "soh" not in v  # unknown SOH is dropped, never a fake 0
    row["SOH"] = "0%"
    assert "soh" not in _values(_decoded(row))

    row["vBat(V)"] = ""  # the live writer skips snapshots without battery voltage
    regs, suppressed, _ = row_to_registers(row, "eg4_12000xp")
    assert decode_registers_like_live(regs, suppressed, _ctx()) is None


# ── Lines ────────────────────────────────────────────────────────────────────

def test_lines_match_the_live_outputs_path():
    ctx = _ctx()
    points = sheet_points(_sheets()[1], ctx)
    p = next(p for p in points if p.local.endswith("11:59:32"))
    ts = _utc(2026, 9, 25, 18, 59, 32)  # 11:59:32 PDT
    assert p.ts == ts
    lines = p.lines()
    assert f"luxmon_register,name=temp_radiator_1,unit=°F value=120.2 {ts}000000000" in lines
    assert f"luxmon_register,name=soc,unit=% value=30.0 {ts}000000000" in lines
    assert f"luxmon_register,name=state value=192.0 {ts}000000000" in lines  # empty unit: no tag
    assert {_measurement(line) for line in lines} - {"luxmon_register"} <= SA_MEASUREMENTS
    assert "Battery state of charge" in {_measurement(line) for line in lines}
    assert all(_stamp(line) == ts * 1_000_000_000 for line in lines)

    # Byte-identical to what Outputs._write_influxdb posts for the same snapshot.
    o = Outputs.__new__(Outputs)
    o.cfg = OutputConfig(mariadb_enabled=False, mqtt_enabled=False, influx_enabled=True)
    o._influx_client = None
    o._mqtt_client = None
    o._post_influx_v1 = MagicMock()
    raw_decoded = get_driver("eg4_12000xp").decode(row_to_registers(_row("11:59:32"), "eg4_12000xp")[0])
    bf.clamp_values(raw_decoded, ctx.driver)
    o.cfg.temperature_unit = "fahrenheit"
    o._write_influxdb(o._convert_temperatures(raw_decoded), ts_ns=ts * 1_000_000_000)
    assert o._post_influx_v1.call_args.args[0] == "\n".join(lines)


def test_idempotent_lines():
    def run():
        target = FakeTarget()
        b = Backfiller(_ctx(), target=target, dry_run=False, run_id="fixed")
        b.process(_sheets(), date(2026, 9, 24), date(2026, 9, 25))
        return target.lines()

    first, second = run(), run()
    assert first == second
    data = [line for line in first if not line.startswith(bf.BACKFILL_MEASUREMENT)]
    keys = [(_series(line), _stamp(line)) for line in data]
    assert len(keys) == len(set(keys))  # one point per series and timestamp: re-runs overwrite
    assert len({_stamp(line) for line in data}) == 7


# ── Time ─────────────────────────────────────────────────────────────────────

def _time_rows(*times):
    return [{"Time": t, "Serial number": SERIAL} for t in times]


def test_dst_fall_back_and_spring_forward():
    stats = bf.Stats()
    rows = _time_rows("2026/11/01 00:58:00", "2026/11/01 01:02:00", "2026/11/01 01:58:00",
                      "2026/11/01 01:02:30", "2026/11/01 01:58:30", "2026/11/01 02:02:00")
    ts = [t for t, _, _ in localize_rows(rows, LA, stats)]
    assert ts == [
        _utc(2026, 11, 1, 7, 58), _utc(2026, 11, 1, 8, 2), _utc(2026, 11, 1, 8, 58),  # PDT
        _utc(2026, 11, 1, 9, 2, 30), _utc(2026, 11, 1, 9, 58, 30),                   # PST, repeated hour
        _utc(2026, 11, 1, 10, 2),
    ]
    assert ts == sorted(ts) and stats.dst_fold == 2

    stats = bf.Stats()
    rows = _time_rows("2026/03/08 01:58:00", "2026/03/08 02:30:00", "2026/03/08 03:02:00")
    ts = [t for t, _, _ in localize_rows(rows, LA, stats)]
    assert ts == [_utc(2026, 3, 8, 9, 58), _utc(2026, 3, 8, 10, 30), _utc(2026, 3, 8, 10, 2)]
    assert stats.dst_gap == 1

    # A sheet listed newest first is put back in time order.
    rows = _time_rows("2026/09/25 00:07:16", "2026/09/25 00:03:14", "bad time")
    stats = bf.Stats()
    out = localize_rows(rows, LA, stats)
    assert [local for _, local, _ in out] == ["2026-09-25 00:03:14", "2026-09-25 00:07:16"]
    assert out[0][0] == _utc(2026, 9, 25, 7, 3, 14)
    assert stats.skipped_time == 1


def test_overlap_cutoff_and_window_filter():
    cutoff = _utc(2026, 9, 25, 18, 59, 32)  # 11:59:32 PDT: that row and later are live
    target = FakeTarget()
    b = Backfiller(_ctx(), target=target, cutoff=cutoff, live_start=cutoff, dry_run=False, run_id="r1")
    rows, snapshots, points = b.process(_sheets(), date(2026, 9, 24), date(2026, 9, 25))
    assert rows == 7 and snapshots == 4 and b.stats.skipped_overlap == 3
    data = [line for line in target.lines() if not line.startswith(bf.BACKFILL_MEASUREMENT)]
    stamps = {int(line.rsplit(" ", 1)[1]) // 1_000_000_000 for line in data}
    assert max(stamps) < cutoff and len(data) == points

    # live_start/cutoff are written before any data, the counts after it; both
    # lines share series and timestamp, so InfluxDB keeps one point per window.
    prov = [line for line in target.lines() if line.startswith(bf.BACKFILL_MEASUREMENT)]
    assert len(prov) == 2
    assert target.writes[0] == [prov[0]] and target.writes[-1] == [prov[1]]
    assert {_series(line) for line in prov} == {"luxmon_backfill,run_id=r1"}
    assert _stamp(prov[0]) == _stamp(prov[1])
    assert f"live_start={float(cutoff)}" in prov[0] and f"cutoff={float(cutoff)}" in prov[0]
    assert "points=" not in prov[0]
    assert 'window="2026-09-24..2026-09-25"' in prov[1] and 'source="eg4_export"' in prov[1]
    assert f"points={float(points)}" in prov[1] and "rows=7.0" in prov[1]
    assert f"live_start={float(cutoff)}" in prov[1]

    # Sheets outside the requested days are ignored.
    b = Backfiller(_ctx(), dry_run=True)
    rows, snapshots, _ = b.process(_sheets(), date(2026, 9, 25), date(2026, 9, 25))
    assert rows == 5 and snapshots == 5


def test_units_conflict_aborts_before_writing():
    target = FakeTarget()
    b = Backfiller(_ctx(unit="celsius"), target=target, dry_run=False,
                   live_units={"temp_radiator_1": "°F", "soc": "%"})
    with pytest.raises(BackfillError, match="temp_radiator_1"):
        b.process(_sheets())
    assert target.writes == []

    b = Backfiller(_ctx(unit="fahrenheit"), target=target, dry_run=False,
                   live_units={"temp_radiator_1": "°F", "soc": "%", "state": ""})
    b.process(_sheets())
    assert target.writes


def test_iter_windows():
    windows = list(iter_windows(date(2026, 9, 1), date(2026, 9, 25)))
    assert windows == [(date(2026, 9, 1), date(2026, 9, 10)), (date(2026, 9, 11), date(2026, 9, 20)),
                       (date(2026, 9, 21), date(2026, 9, 25))]
    assert list(iter_windows(date(2026, 9, 5), date(2026, 9, 5))) == [(date(2026, 9, 5), date(2026, 9, 5))]


def test_find_first_day():
    first = date(2025, 4, 17)
    probes = []

    def has_rows(day):
        probes.append(day)
        return day >= first

    assert find_first_day(has_rows, date(2026, 9, 26)) == first
    assert len(probes) <= 16 and min(probes) == bf.FIND_START_FLOOR
    assert find_first_day(lambda d: False, date(2026, 9, 26)) is None
    assert find_first_day(lambda d: True, date(2026, 9, 26)) == bf.FIND_START_FLOOR


# ── Compare ──────────────────────────────────────────────────────────────────

def test_compare_points():
    ctx = _ctx()
    points = sheet_points(_sheets()[1], ctx)[:3]  # 00:03:14, 00:07:16, 11:59:32
    t0, t1, t2 = (p.ts for p in points)
    live = {
        "soc": [(t0 + 60, 24.0, "%"), (t1 - 170, 23.0, "%"), (t2 + 400, 30.0, "%")],  # last one too far
        "temp_radiator_1": [(t0, 20.0, "°C")],
        "battery_count": [(t0, 4.0, "")],
    }
    rows = {r.name: r for r in compare_points(points, live)}
    assert rows["soc"].exported == 3 and rows["soc"].matched == 2
    assert rows["soc"].mean_abs == pytest.approx(0.5) and rows["soc"].max_abs == pytest.approx(1.0)
    assert rows["battery_count"].exported == 0
    assert rows["pv1_voltage"].matched == 0 and rows["pv1_voltage"].live_unit == "-"
    text = "\n".join(format_comparison(list(rows.values())))
    assert "UNIT MISMATCH" in text and "live only" in text and "export only" in text


# ── HTTP ─────────────────────────────────────────────────────────────────────

XLS_BODY = bf._XLS_MAGIC + b"\0" * 32


def _resp(status=200, content=b"", ctype="application/vnd.ms-excel", body=None):
    r = MagicMock()
    r.status_code = status
    r.headers = {"Content-Type": ctype}
    if body is not None:
        r.content = json.dumps(body).encode()
        r.json.return_value = body
    else:
        r.content = content
        r.json.side_effect = ValueError("not JSON")
    return r


def test_classify_export():
    assert classify_export(_resp(content=XLS_BODY)) == ("ok", XLS_BODY, "")
    assert classify_export(_resp(content=XLS_BODY, ctype="application/octet-stream"))[0] == "ok"
    assert classify_export(_resp(content=b"<!DOCTYPE html><html>", ctype="text/html"))[0] == "session"
    assert classify_export(_resp(302))[0] == "session"
    assert classify_export(_resp(401))[0] == "session"
    assert classify_export(_resp(429))[0] == "rate_limited"
    assert classify_export(_resp(503))[0] == "transient"
    assert classify_export(_resp(404))[0] == "api_error"
    assert classify_export(_resp(content=b""))[0] == "transient"
    assert classify_export(_resp(body={"success": False, "msg": "please login"}, ctype="application/json"))[0] == "session"
    assert classify_export(_resp(body={"success": False, "msg": "DEVICE_BUSY"}, ctype="application/json"))[0] == "transient"
    assert classify_export(_resp(body={"success": False, "msg": "apiBlocked"}, ctype="application/json"))[0] == "api_error"
    assert classify_export(_resp(content=b"PK\x03\x04xlsx", ctype="application/vnd.ms-excel"))[0] == "api_error"


def test_allowlist():
    good = "/WManage/web/analyze/data/export/TEST000001/2026-09-24"
    assert export_get_allowed(good, {"endDateText": "2026-09-25"})
    assert export_get_allowed(good, None)
    for path, params in (
        ("/WManage/web/maintain/remoteRead/read", None),
        ("/WManage/api/inverter/getInverterRuntime", None),
        ("/WManage/web/analyze/data/export/TEST000001/2026-09-24/../../maintain", None),
        ("/WManage/web/analyze/data/export/TEST/0001/2026-09-24", None),
        ("/WManage/web/analyze/energy/dayColumn/TEST000001/2026-09-24", None),
        (good, {"endDateText": "2026-09-25", "action": "set"}),
    ):
        assert not export_get_allowed(path, params), path

    # The live transport's POST allowlist is unchanged; the export is GET-only here.
    assert not any("export" in p for p in ch._ALLOWED_PATHS)
    client = _client(FakeSession({}))
    with pytest.raises(ValueError, match="non-allow-listed"):
        client._get("/WManage/web/maintain/remoteSet/write", None)
    with pytest.raises(ValueError, match="non-allow-listed"):
        client._post("/WManage/web/analyze/data/export/TEST000001/2026-09-24", {})


class FakeSession:
    """requests.Session stand-in: POST routed by path, GET by a response queue."""

    def __init__(self, posts, gets=()):
        self.posts = {p: list(v) for p, v in posts.items()}
        self.gets = list(gets)
        self.cookies = MagicMock()
        self.calls = []

    def post(self, url, data=None, headers=None, allow_redirects=True, timeout=None):
        path = urlparse(url).path
        self.calls.append(("POST", path, None))
        queue = self.posts[path]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def get(self, url, params=None, headers=None, allow_redirects=True, timeout=None):
        assert allow_redirects is False
        self.calls.append(("GET", urlparse(url).path, dict(params or {})))
        item = self.gets.pop(0) if len(self.gets) > 1 else self.gets[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        pass


LOGIN_OK = {"success": True, "plants": []}


def _client(session, clock=None, sleeps=None):
    t = {"now": 1_000_000.0}
    sleeps = sleeps if sleeps is not None else []

    def sleep(sec):
        sleeps.append(sec)
        t["now"] += sec

    return ExportClient(
        base_url="https://monitor.eg4electronics.com", username="user@example.com",
        password="s3cr3t-P@ss", inverter_serial=SERIAL, model="eg4_12000xp", request_delay=3.0,
        session_factory=lambda: session, clock=clock or (lambda: t["now"]), rand=lambda: 0.5, sleep=sleep,
    )


def test_export_client_relogin_retry_and_pacing():
    session = FakeSession(
        {LOGIN_PATH: [_resp(body=LOGIN_OK, ctype="application/json")]},
        gets=[_resp(content=b"<html>login</html>", ctype="text/html"),  # expired session
              _resp(content=XLS_BODY),
              _resp(503), _resp(503), _resp(content=XLS_BODY)],
    )
    sleeps = []
    client = _client(session, sleeps=sleeps)
    assert client.export(date(2026, 9, 14), date(2026, 9, 23)) == XLS_BODY
    posts = [c for c in session.calls if c[0] == "POST"]
    gets = [c for c in session.calls if c[0] == "GET"]
    assert len(posts) == 2  # initial login + one re-login
    assert gets[0][1] == "/WManage/web/analyze/data/export/TEST000001/2026-09-14"
    assert gets[0][2] == {"endDateText": "2026-09-23"}
    assert sleeps == [3.0]  # the replay after re-login waits the request delay

    sleeps.clear()
    assert client.export(date(2026, 9, 24), date(2026, 9, 24)) == XLS_BODY
    # Request delay, then two 503s: backoff 5 s and 10 s (no jitter with rand 0.5).
    assert sleeps == [3.0, 5.0, 10.0]
    assert client.downloads == 2

    with pytest.raises(ValueError):
        client.export(date(2026, 9, 1), date(2026, 9, 11))  # 11 days


def test_export_client_gives_up_and_never_retries_auth():
    session = FakeSession({LOGIN_PATH: [_resp(body=LOGIN_OK, ctype="application/json")]}, gets=[_resp(502)])
    client = _client(session)
    with pytest.raises(ch.CloudTransientError, match="after 4 attempts"):
        client.export(date(2026, 9, 24), date(2026, 9, 24))
    assert len([c for c in session.calls if c[0] == "GET"]) == bf.MAX_ATTEMPTS

    rejected = {"success": False, "msg": "account or password error"}
    session = FakeSession({LOGIN_PATH: [_resp(body=rejected, ctype="application/json")]}, gets=[_resp(content=XLS_BODY)])
    client = _client(session)
    with pytest.raises(CloudAuthError):
        client.export(date(2026, 9, 24), date(2026, 9, 24))
    assert [c[0] for c in session.calls] == ["POST"]  # one login attempt, no export request

    session = FakeSession({LOGIN_PATH: [_resp(body=LOGIN_OK, ctype="application/json")]}, gets=[_resp(403)])
    with pytest.raises(CloudApiError):
        _client(session).export(date(2026, 9, 24), date(2026, 9, 24))


def test_device_clock_and_timezone_check():
    runtime = _load_cloud("runtime_12000xp")  # serverTime 23:09:49 UTC, deviceTime 17:09:49
    session = FakeSession({LOGIN_PATH: [_resp(body=LOGIN_OK, ctype="application/json")],
                           RUNTIME_PATH: [_resp(body=runtime, ctype="application/json")]})
    client = _client(session)
    at, offset = client.device_clock()
    assert offset == -6 * 3600 and at == _utc(2026, 1, 9, 23, 9, 49)

    def runner(tz, explicit):
        settings = _settings(tz=ZoneInfo(tz), tz_explicit=explicit)
        r = Runner(build_parser().parse_args(["--start", "2026-01-01"]), settings, target_factory=lambda out: FakeTarget())
        r.client = client
        return r

    runner("America/Chicago", False).check_timezone(strict=True)  # CST in January: matches
    with pytest.raises(BackfillError, match="--tz"):
        runner("America/Los_Angeles", False).check_timezone(strict=True)
    runner("America/Los_Angeles", True).check_timezone(strict=True)  # explicit --tz: warning only


class ClockClient:
    """ExportClient stand-in whose runtime call fails, or lacks deviceTime/serverTime."""

    def __init__(self, error=None):
        self.error = error

    def device_clock(self):
        if self.error:
            raise self.error
        return None

    def export(self, start, end):
        raise AssertionError("no download before the time zone is verified")

    def _redact(self, text):
        return text


def test_unverified_timezone_stops_write_runs(caplog):
    def runner(client, explicit):
        r = Runner(build_parser().parse_args(["--start", "2026-09-01"]),
                   _settings(tz=ZoneInfo("America/Chicago"), tz_explicit=explicit),
                   target_factory=lambda out: FakeTarget())
        r.client = client
        return r

    for client in (ClockClient(ch.CloudTransientError("runtime failed after 4 attempts")), ClockClient()):
        with pytest.raises(BackfillError, match="not verified: pass --tz"):
            runner(client, False).check_timezone(strict=True)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="luxmon.backfill"):
            runner(client, False).check_timezone(strict=False)  # dry run / --compare-live
            runner(client, True).check_timezone(strict=True)    # explicit --tz
        assert sum("not verified" in r.getMessage() for r in caplog.records) == 2
    with pytest.raises(CloudAuthError):
        runner(ClockClient(CloudAuthError("rejected")), True).check_timezone(strict=False)

    # A portal write run stops before any download or write.
    settings = _settings(tz=ZoneInfo("America/Chicago"), tz_explicit=False)
    settings.cfg.cloud_username, settings.cfg.cloud_password = "user@example.com", "s3cr3t-P@ss"
    target = FakeTarget(first_soc=_utc(2026, 9, 27, 1, 26, 6))
    r = Runner(build_parser().parse_args(["--start", "2026-09-01"]), settings,
               target_factory=lambda out: target, client_factory=lambda **kw: ClockClient())
    with pytest.raises(BackfillError, match="not verified"):
        r.run()
    assert target.writes == []


# ── Runner / no side effects ─────────────────────────────────────────────────

def _settings(**kw):
    base = dict(
        cfg=CollectorConfig(
            inverter_serial=SERIAL,
            outputs=OutputConfig(mariadb_enabled=True, influx_enabled=True, mqtt_enabled=True),
        ),
        model="eg4_12000xp", temperature_unit="fahrenheit", tz=LA, tz_explicit=True, db_ok=True,
        db_temperature_unit="fahrenheit",
    )
    base.update(kw)
    return Settings(**base)


def _forbid_side_effects(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the backfill must not touch MariaDB/MQTT/Outputs")

    import paho.mqtt.client as mqtt
    import pymysql
    monkeypatch.setattr(pymysql, "connect", boom)
    monkeypatch.setattr(mqtt, "Client", boom)
    monkeypatch.setattr(Outputs, "__init__", boom)
    monkeypatch.setattr(Outputs, "write", boom)
    monkeypatch.setattr("collector.collector._seed_db_from_env", boom)


def test_run_from_file_writes_only_influx(monkeypatch, tmp_path):
    _forbid_side_effects(monkeypatch)
    xls = tmp_path / "export.xls"
    xls.write_bytes(XLS_BODY)
    monkeypatch.setattr(bf, "parse_workbook", lambda content: _sheets())
    # An earlier run recorded the first live point (11:00 PDT); the earliest soc
    # point is now a backfilled one and must not become the cutoff.
    recorded = _utc(2026, 9, 25, 18, 0, 0) + 0.5
    target = FakeTarget(first_soc=_utc(2026, 9, 24, 7, 0), recorded=recorded,
                        units={"soc": "%", "temp_radiator_1": "°F"})
    out = []
    args = build_parser().parse_args(["--from-xls", str(xls)])
    runner = Runner(args, _settings(), target_factory=lambda o: target, out=out.append)
    assert runner.run() == 0
    runner.close()

    lines = target.lines()
    measurements = {_measurement(line) for line in lines}
    assert measurements - SA_MEASUREMENTS == {"luxmon_register", "luxmon_backfill"}
    # The recorded live start (not the earliest, backfilled, soc point) is the cutoff.
    stamps = {_stamp(line) // 10**9 for line in lines if not line.startswith("luxmon_backfill")}
    assert max(stamps) < recorded
    assert len(stamps) == 4  # 09-24 07:15, 08:43; 09-25 00:03, 00:07 (before 11:00 PDT)
    assert [line for line in lines if line.startswith("luxmon_backfill")][0].count("live_start=") == 1
    assert target.closed
    assert any(line.startswith("── Backfill summary") for line in out)


def test_dry_run_writes_nothing_and_works_offline(monkeypatch, tmp_path):
    _forbid_side_effects(monkeypatch)
    xls = tmp_path / "export.xls"
    xls.write_bytes(XLS_BODY)
    monkeypatch.setattr(bf, "parse_workbook", lambda content: _sheets())

    def unreachable(out):
        raise ConnectionError("no InfluxDB")

    out = []
    args = build_parser().parse_args(["--from-xls", str(xls), "--dry-run", "--verbose"])
    runner = Runner(args, _settings(db_ok=False, db_temperature_unit=None), target_factory=unreachable,
                    out=out.append)
    assert runner.run() == 0
    text = "\n".join(out)
    assert "dry run" in text and "points counted" in text
    assert "── Sample rows" in text and "temp_radiator_1=" in text and "°F" in text

    # Writes need InfluxDB.
    args = build_parser().parse_args(["--from-xls", str(xls)])
    with pytest.raises(BackfillError, match="not usable"):
        Runner(args, _settings(), target_factory=unreachable).run()


def test_settings_read_db_without_writing(monkeypatch, caplog):
    executed = []

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            executed.append(sql)

        def fetchone(self):
            return None

        def fetchall(self):
            return []

    class Conn:
        def cursor(self):
            return Cur()

        def close(self):
            pass

    import pymysql
    for var in ("LUX_TEMPERATURE_UNIT", "LUX_INVERTER_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(pymysql, "connect", lambda **kw: Conn())
    with caplog.at_level(logging.WARNING, logger="luxmon.backfill"):
        s = load_settings(build_parser().parse_args(["--dry-run"]))
    assert s.db_ok and executed
    assert all(sql.strip().upper().startswith("SELECT") for sql in executed)
    # Missing rows read as the defaults: what the collector runs with too, so no
    # fallback for the model; the default zone is flagged (it is verified later).
    assert s.temperature_unit == "fahrenheit" and s.model == "eg4_6000xp" and s.fallbacks == ()
    assert str(s.tz) == "America/Chicago" and not s.tz_explicit
    assert any("lux-mon default" in r.getMessage() for r in caplog.records)

    def refuse(**kw):
        raise pymysql.err.OperationalError(2003, "unreachable")

    monkeypatch.setattr(pymysql, "connect", refuse)
    s = load_settings(build_parser().parse_args(["--dry-run", "--temp-unit", "celsius", "--model", "eg4_12000xp",
                                                 "--tz", "America/Los_Angeles"]))
    assert not s.db_ok and s.temperature_unit == "celsius" and s.model == "eg4_12000xp" and s.tz == LA
    assert s.fallbacks == ()

    # Compose sets LUX_INVERTER_MODEL=eg4_6000xp by default: not the collector's model.
    monkeypatch.setenv("LUX_INVERTER_MODEL", "eg4_6000xp")
    s = load_settings(build_parser().parse_args(["--dry-run"]))
    assert s.model == "eg4_6000xp" and s.fallbacks == ("--model", "--temp-unit")


def test_writes_refuse_fallback_settings(monkeypatch, tmp_path):
    xls = tmp_path / "export.xls"
    xls.write_bytes(XLS_BODY)
    monkeypatch.setattr(bf, "parse_workbook", lambda content: _sheets())
    target = FakeTarget(first_soc=_utc(2026, 9, 27, 1, 26, 6), units={"soc": "%"})
    settings = _settings(model="eg4_6000xp", db_ok=False, db_temperature_unit=None,
                         fallbacks=("--model", "--temp-unit"))
    args = build_parser().parse_args(["--from-xls", str(xls), "--tz", "America/Los_Angeles"])
    with pytest.raises(BackfillError, match="pass --model and --temp-unit"):
        Runner(args, settings, target_factory=lambda o: target).run()
    assert target.writes == []
    dry = build_parser().parse_args(["--from-xls", str(xls), "--tz", "America/Los_Angeles", "--dry-run"])
    assert Runner(dry, settings, target_factory=lambda o: target, out=lambda s: None).run() == 0
    assert target.writes == []


def test_temp_unit_flag_must_match_db_for_writes(tmp_path):
    xls = tmp_path / "x.xls"
    xls.write_bytes(XLS_BODY)
    args = build_parser().parse_args(["--from-xls", str(xls), "--temp-unit", "celsius"])
    with pytest.raises(BackfillError, match="temperature_unit"):
        Runner(args, _settings(), target_factory=lambda o: FakeTarget()).run()


def _xls_runner(monkeypatch, tmp_path):
    """run(target, *argv, settings=None): a --from-xls Runner over the fixture sheets."""
    xls = tmp_path / "export.xls"
    xls.write_bytes(XLS_BODY)
    monkeypatch.setattr(bf, "parse_workbook", lambda content: _sheets())

    def run(target, *argv, settings=None, out=None):
        args = build_parser().parse_args(["--from-xls", str(xls), *argv])
        return Runner(args, settings or _settings(), target_factory=lambda o: target,
                      out=out.append if out is not None else (lambda s: None)).run()

    return run


def test_from_xls_write_needs_tz(monkeypatch, tmp_path):
    _forbid_side_effects(monkeypatch)
    run = _xls_runner(monkeypatch, tmp_path)
    target = FakeTarget(first_soc=_utc(2026, 9, 27, 1, 26, 6), units={"soc": "%"})
    # The zone from the timezone setting cannot be checked offline.
    db_zone = _settings(tz=ZoneInfo("America/Chicago"), tz_explicit=False)
    with pytest.raises(BackfillError, match="pass --tz to write from --from-xls"):
        run(target, settings=db_zone)
    assert target.writes == []
    assert run(target, "--dry-run", settings=db_zone) == 0 and target.writes == []
    assert run(target) == 0 and target.writes  # _settings(): explicit --tz


def test_interrupted_run_keeps_the_cutoff(monkeypatch, tmp_path):
    run = _xls_runner(monkeypatch, tmp_path)
    live = _utc(2026, 9, 25, 18, 0)  # 11:00 PDT: 4 fixture rows are before it
    store = []
    args = build_parser().parse_args(["--from-xls", "unused.xls"])
    first = Runner(args, _settings(), target_factory=lambda o: StoreTarget(store, live, fail_on_batch=2))
    first.open_influx()
    b = first.backfiller()
    b.batch_lines = 1  # one snapshot per batch; Ctrl-C during the 2nd
    with pytest.raises(KeyboardInterrupt):
        b.process(_sheets())
    soc = [line for line in store if line.startswith("luxmon_register,name=soc,")]
    assert len(soc) == 1
    assert StoreTarget(store, live).first_soc_time() < live  # the earliest soc point is a backfilled one
    assert StoreTarget(store, live).recorded_live_start() == live  # but live_start was recorded first

    out = []
    assert run(StoreTarget(store, live), out=out) == 0
    assert f"cutoff {bf._iso_utc(live)}" in out
    soc = {_stamp(line) for line in store if line.startswith("luxmon_register,name=soc,")}
    assert len(soc) == 4 and max(soc) // 10**9 < live


def test_cutoff_after_first_live_point(monkeypatch, tmp_path):
    run = _xls_runner(monkeypatch, tmp_path)
    live = _utc(2026, 9, 25, 18, 0)  # 11:00 PDT
    target = FakeTarget(first_soc=live, units={"soc": "%"})

    def data_stamps():
        return {_stamp(line) // 10**9 for line in target.lines() if not line.startswith(bf.BACKFILL_MEASUREMENT)}

    # Local midnight 2026-09-26 is after 11:00 PDT on the 25th.
    with pytest.raises(BackfillError, match="--allow-live-overlap"):
        run(target, "--cutoff", "2026-09-26")
    assert target.writes == []
    assert run(target, "--cutoff", "2026-09-26", "--dry-run") == 0 and target.writes == []  # warning only
    assert run(target, "--cutoff", "2026-09-25T05:00") == 0  # earlier than live: allowed
    assert max(data_stamps()) < _utc(2026, 9, 25, 12, 0)
    target.writes.clear()
    assert run(target, "--cutoff", "2026-09-26", "--allow-live-overlap") == 0
    assert max(data_stamps()) >= live

    # A portal run stops before its first EG4 request.
    settings = _settings()
    settings.cfg.cloud_username, settings.cfg.cloud_password = "user@example.com", "s3cr3t-P@ss"
    clients = []
    args = build_parser().parse_args(["--start", "2026-09-20", "--cutoff", "2026-09-26"])
    with pytest.raises(BackfillError, match="--allow-live-overlap"):
        Runner(args, settings, target_factory=lambda o: target,
               client_factory=lambda **kw: clients.append(kw) or ClockClient()).run()
    assert clients == []


# ── InfluxDB wrapper ─────────────────────────────────────────────────────────

class FakeRecord:
    def __init__(self, t, value, **tags):
        self._t, self._v = t, value
        self.values = {**tags, "_time": t, "_value": value}

    def get_time(self):
        return self._t

    def get_value(self):
        return self._v


class FakeInfluxClient:
    def __init__(self, responder):
        self.queries = []
        self.writes = []
        self.write_options = None
        self._responder = responder

    def query_api(self):
        client = self

        class Q:
            def query(self, flux, org=None):
                client.queries.append(flux)
                return [SimpleNamespace(records=client._responder(flux))]

        return Q()

    def write_api(self, write_options=None):
        self.write_options = write_options
        client = self

        class W:
            def write(self, bucket, org, record, write_precision):
                client.writes.append((bucket, org, list(record), str(write_precision)))

        return W()

    def close(self):
        pass


def test_influx_target_queries_and_writes():
    t0 = datetime(2026, 9, 27, 1, 26, 6, 244511, tzinfo=timezone.utc)
    t1 = t0 + (datetime(2026, 1, 1, 0, 2) - datetime(2026, 1, 1))

    def responder(flux):
        if "luxmon_backfill" in flux:
            return [FakeRecord(t0, 1790472400.0), FakeRecord(t0, 1790472366.2), FakeRecord(t0, "bad")]
        if 'r.name == "soc"' in flux:
            return [FakeRecord(t0, 80.0, name="soc", unit="%")]
        if "|> last()" in flux:
            return [FakeRecord(t0, 60.0, name="temp_radiator_1", unit="°C"),
                    FakeRecord(t1, 140.0, name="temp_radiator_1", unit="°F"),
                    FakeRecord(t1, 192.0, name="state")]
        return [FakeRecord(t1, 81.0, name="soc", unit="%"), FakeRecord(t0, 80.0, name="soc", unit="%"),
                FakeRecord(t0, "x", name="soc", unit="%")]

    client = FakeInfluxClient(responder)
    out = OutputConfig(influx_token="tok", influx_bucket="luxmon", influx_org="luxmon")
    target = bf.InfluxTarget(out, client=client)
    assert target.recorded_live_start() == pytest.approx(1790472366.2)
    assert target.first_soc_time() == pytest.approx(t0.timestamp())
    assert target.live_units(t0.timestamp()) == {"temp_radiator_1": "°F", "state": ""}
    points = target.register_points(t0.timestamp(), t1.timestamp() + 1)
    assert points == {"soc": [(t0.timestamp(), 80.0, "%"), (t1.timestamp(), 81.0, "%")]}
    assert all(q.startswith('from(bucket: "luxmon")') for q in client.queries)
    assert "range(start: 2026-09-27T01:26:06.244511Z)" in client.queries[2]

    target.write(["a 1", "b 2"])
    assert client.writes == [("luxmon", "luxmon", ["a 1", "b 2"], "ns")]
    assert client.write_options is not None  # synchronous write API, errors raise

    v1 = bf.InfluxTarget(OutputConfig(influx_token="", influx_username="u", influx_password="p",
                                      influx_database="luxmon", influx_retention="autogen"), client=client)
    assert v1.bucket == "luxmon/autogen"


def test_compare_live_writes_nothing(monkeypatch, tmp_path):
    _forbid_side_effects(monkeypatch)
    xls = tmp_path / "export.xls"
    xls.write_bytes(XLS_BODY)
    monkeypatch.setattr(bf, "parse_workbook", lambda content: _sheets())
    noon = _utc(2026, 9, 25, 18, 59, 32)  # 11:59:32 PDT
    live = {"soc": [(noon + 30, 30.0, "%")], "temp_radiator_1": [(noon - 20, 120.0, "°F")]}
    target = FakeTarget(first_soc=_utc(2026, 9, 25, 18, 0), live=live)
    out = []
    args = build_parser().parse_args(["--compare-live", "2026-09-25", "--from-xls", str(xls)])
    assert Runner(args, _settings(), target_factory=lambda o: target, out=out.append).run() == 0
    text = "\n".join(out)
    assert "3 export rows 2026-09-25 11:59:32..2026-09-25 23:56:51" in text
    soc = next(line for line in out if line.startswith("soc "))
    assert soc.split()[3:7] == ["3", "1", "0", "0"]
    assert "Nothing was written." in text and target.writes == []

    with pytest.raises(BackfillError, match="--compare-live"):
        Runner(build_parser().parse_args(["--compare-live", "2026-09-25", "--start", "2026-09-01"]),
               _settings(), target_factory=lambda o: target).run()


# ── Real workbook (only when the file is present) ────────────────────────────

@pytest.mark.skipif(not REAL_XLS.exists(), reason="real EG4 export not available")
def test_real_workbook():
    sheets = bf.parse_workbook(REAL_XLS.read_bytes())
    assert [s.day for s in sheets] == ["2026-09-24", "2026-09-25"]
    assert all(len(s.rows) > 300 and len(s.rows[0]) == 114 for s in sheets)
    ctx = _ctx()
    stats = bf.Stats()
    points = [p for s in sheets for p in sheet_points(s, ctx, stats)]
    assert len(points) == sum(len(s.rows) for s in sheets)
    assert not stats.unknown_status and not stats.skipped_no_battery
    assert [p.ts for p in points] == sorted(p.ts for p in points)
    last = _values(points[-1].decoded)
    assert last["pv1_energy_total"] == pytest.approx(1599.9)
    assert last["load_energy_total"] == pytest.approx(1049.3)
    # Lifetime counters never go backwards; daily ones reset at local midnight.
    totals = [_values(p.decoded)["pv1_energy_total"] for p in points]
    assert totals == sorted(totals)
    states = {_values(p.decoded)["state"] for p in points}
    assert states == {0, 0x08, 0x40, 0xC0}
