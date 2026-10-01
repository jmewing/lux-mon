"""Tests for automatic gap-filling (collector.gapfill, backfill --fill-gaps).

No network, no database: export rows are cloned from the backfill fixture with
new times, the EG4 client is faked, and InfluxDB is an in-memory store that
answers the gap-fill queries from the lines written to it.
"""
import copy
import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from collector import backfill as bf
from collector import gapfill as gf
from collector.backfill import Backfiller, BackfillError, ExportSheet, MapContext, Runner, Settings, build_parser
from collector.collector import CollectorConfig, PassiveCollector
from collector.comm.cloud_http import CloudAuthError, CloudTransientError
from collector.drivers.registry import get_driver
from collector.gapfill import (
    ATTEMPT,
    BLOCKED,
    DEFERRED,
    EMPTY,
    FILLED,
    GAVE_UP,
    GIVE_UP,
    GIVEN_UP,
    WAIT,
    GapAttempt,
    GapFiller,
    GapFillOptions,
    GapFillSettings,
    GapFillStopped,
    GapFillThread,
    day_windows,
    detect_gaps,
    exclusive_run,
    gap_days,
    gapfill_disabled_reason,
    make_options,
    plan_gap,
    settings_from_env,
)
from collector.outputs import _REGISTER_TO_SA, OutputConfig, Outputs

FIXTURES = Path(__file__).parent / "fixtures"
ROWS = json.loads((FIXTURES / "backfill" / "export_rows_12000xp.json").read_text())
TEMPLATE = next(r for rows in ROWS["sheets"].values() for r in rows if r["Time"].endswith("13:24:05"))
SERIAL = "TEST000001"
LA = ZoneInfo("America/Los_Angeles")
SA_MEASUREMENTS = {m for m, _ in _REGISTER_TO_SA.values()}
MIN, HOUR, DAY = 60, 3600, 86400


def _utc(*args) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


def _local(*args) -> int:
    return int(datetime(*args, tzinfo=LA).timestamp())


# 2026-09-30 13:00 PDT
NOW = _utc(2026, 9, 30, 20, 0, 0)
LIVE_STEP = 120                    # the live transport: one point per upload, every 2 min here
LIVE_FROM = NOW - 9 * DAY
EXPORT_STEP = 240                  # export rows, ~4 min apart
EXPORT_OFFSET = 37                 # inverter-clock seconds: never on a live point's second


def _ctx(unit="fahrenheit") -> MapContext:
    return MapContext(model="eg4_12000xp", driver=get_driver("eg4_12000xp"), temperature_unit=unit, tz=LA)


# ── In-memory InfluxDB ───────────────────────────────────────────────────────

def _stamp(line: str) -> int:
    return int(line.rsplit(" ", 1)[1])


_FIELD_RE = re.compile(r'([A-Za-z_]+)=("(?:[^"\\]|\\.)*"|[^,]*)')


def _fields(line: str) -> dict:
    """Fields of a luxmon_backfill line (quoted strings may hold commas, no spaces)."""
    middle = line.split(" ", 1)[1].rsplit(" ", 1)[0]
    out = {}
    for key, raw in _FIELD_RE.findall(middle):
        if raw.startswith('"'):
            out[key] = raw[1:-1]
        elif raw in ("t", "f"):
            out[key] = raw == "t"
        else:
            out[key] = float(raw)
    return out


def _is_record(line: str) -> bool:
    return line.startswith("luxmon_backfill,mode=gapfill,")


def _measurement(line: str) -> str:
    series = re.split(r"(?<!\\) ", line, maxsplit=1)[0]
    return re.split(r"(?<!\\),", series, maxsplit=1)[0].replace("\\ ", " ")


class Store:
    """InfluxTarget stand-in: answers the gap-fill queries from what was written.

    `soc` holds the live soc points (UTC seconds); written luxmon_register soc
    lines are added to it, as InfluxDB would. With feed_back=False written
    points stay invisible to the gap detection (the gap "persists").
    """

    def __init__(self, soc=(), recorded=None, units=None, feed_back=True):
        self.soc = sorted(soc)
        self.recorded = recorded
        self.units = {"soc": "%", "temp_radiator_1": "°F"} if units is None else units
        self.feed_back = feed_back
        self.ages = None
        self.writes = []
        self.lines = []
        self.closed = False

    def write(self, lines):
        self.writes.append(list(lines))
        self.lines.extend(lines)
        if self.feed_back:
            self.soc.extend(_stamp(line) / 1e9 for line in lines if line.startswith("luxmon_register,name=soc,"))
            self.soc.sort()

    def recorded_live_start(self):
        return self.recorded

    def first_soc_time(self):
        return self.soc[0] if self.soc else None

    def soc_times(self, start, stop):
        return [t for t in self.soc if start <= t < stop]

    def last_soc_before(self, before, since):
        earlier = [t for t in self.soc if since <= t < before]
        return max(earlier) if earlier else None

    def live_units(self, since):
        return dict(self.units)

    def unit_sets(self, start, stop):
        units = self.units_around(start, stop) if callable(getattr(self, "units_around", None)) else self.units
        return {name: {unit} if isinstance(unit, str) else set(unit) for name, unit in units.items()}

    def reading_time(self, ts):
        """Upload time of the live point at ts: `ages` (ts -> data_age_s), else fresh (age 0)."""
        if self.ages is None:
            return ts
        age = self.ages(ts) if callable(self.ages) else self.ages.get(ts)
        return None if age is None else ts - age

    def gapfill_records(self, since):
        return [_fields(line) for line in self.lines if _is_record(line) and _stamp(line) / 1e9 >= since]

    def close(self):
        self.closed = True

    def data_lines(self):
        return [line for line in self.lines if not line.startswith("luxmon_backfill")]

    def records(self):
        return [_fields(line) for line in self.lines if _is_record(line)]


# ── Fake EG4 portal ──────────────────────────────────────────────────────────

def _export_row(ts: float) -> dict:
    row = copy.deepcopy(TEMPLATE)
    row["Time"] = datetime.fromtimestamp(ts, LA).strftime("%Y/%m/%d %H:%M:%S")
    return row


def _fake_parse(content: bytes) -> list:
    assert content.startswith(b"FAKEXLS")
    days = json.loads(content[7:])
    return [ExportSheet(day=day, rows=[_export_row(t) for t in times]) for day, times in sorted(days.items())]


@pytest.fixture(autouse=True)
def fake_workbooks(monkeypatch):
    monkeypatch.setattr(bf, "parse_workbook", _fake_parse)


class FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeClient:
    """ExportClient stand-in over a {local day: [row UTC seconds]} portal."""

    def __init__(self, rows=(), offset=None, clock_error=None, log=None):
        self.portal = {}
        self.add(rows)
        self.offset = offset
        self.clock_error = clock_error
        self.log = log if log is not None else []
        self.calls = []
        self.downloads = 0
        self.bytes_downloaded = 0
        self._session = FakeSession()

    def add(self, rows):
        for t in rows:
            self.portal.setdefault(datetime.fromtimestamp(t, LA).date().isoformat(), []).append(t)
        for times in self.portal.values():
            times.sort()

    def device_clock(self):
        self.log.append("clock")
        if self.clock_error is not None:
            raise self.clock_error
        at = NOW
        offset = datetime.fromtimestamp(at, LA).utcoffset().total_seconds() if self.offset is None else self.offset
        return at, offset

    def export(self, start, end):
        self.log.append(("export", start, end))
        self.calls.append((start, end))
        days = {}
        day = start
        while day <= end:
            if day.isoformat() in self.portal:
                days[day.isoformat()] = self.portal[day.isoformat()]
            day += timedelta(days=1)
        content = b"FAKEXLS" + json.dumps(days).encode()
        self.downloads += 1
        self.bytes_downloaded += len(content)
        return content

    def _redact(self, text):
        return text


def _live(holes=(), start=LIVE_FROM, stop=NOW + 3 * DAY):
    """Live soc points every LIVE_STEP s in [start, stop], minus those strictly inside the holes."""
    return [t for t in range(start, stop + 1, LIVE_STEP) if not any(a < t < b for a, b in holes)]


def _export(around, before=HOUR, after=HOUR):
    """Export rows every EXPORT_STEP s from `before` s before to `after` s after each (a, b)."""
    rows = set()
    for a, b in around:
        rows.update(range(a - before + EXPORT_OFFSET, b + after, EXPORT_STEP))
    return sorted(rows)


def _filler(store, client, now=NOW, dry_run=False, options=None, **kw):
    return GapFiller(_ctx(), store, client_factory=lambda: client, options=options or GapFillOptions(),
                     dry_run=dry_run, serial=SERIAL, clock=lambda: now, run_id=kw.pop("run_id", "gf-test"), **kw)


def _inside(rows, gap, margin=60):
    return [t for t in rows if gap[0] + margin < t < gap[1] - margin]


# ── Detection ────────────────────────────────────────────────────────────────

def test_detect_no_data_and_single_point():
    assert detect_gaps([], 0, 10_000) == []
    assert detect_gaps([5000], 0, 10_000, min_gap=600) == [(5000, 10_000)]  # trailing gap to the end
    assert detect_gaps([9500], 0, 10_000, min_gap=600) == []                 # 500 s: not a gap
    assert detect_gaps([100, 200], 1000, 500) == []                          # empty window


def test_detect_interior_gap_and_regular_data():
    times = list(range(0, 10_001, 120))
    assert detect_gaps(times, 0, 10_000, 600) == []
    holed = [t for t in times if not 2000 < t < 4000]
    assert detect_gaps(holed, 0, 10_000, 600) == [(1920, 4080)]
    # Unsorted, duplicated and non-finite input is normalised.
    assert detect_gaps(list(reversed(holed)) + [1920, float("nan")], 0, 10_000, 600) == [(1920, 4080)]
    # Two gaps meeting at one stray live point stay separate (the margin guards that point).
    assert detect_gaps([0, 1000, 2000], 0, 2000, 600) == [(0, 1000), (1000, 2000)]


def test_detect_exact_threshold():
    assert detect_gaps([0, 600, 1200], 0, 1200, min_gap=600) == []          # exactly min_gap: not a gap
    assert detect_gaps([0, 600.5, 1200], 0, 1200, min_gap=600) == [(0, 600.5)]
    assert detect_gaps([0], 0, 600, 600) == []                                # trailing, exactly min_gap
    assert detect_gaps([0], 0, 601, 600) == [(0, 601)]


def test_detect_leading_gap_and_lookback_bounds():
    # The last point before the window anchors a gap that began earlier: clipped to the window.
    assert detect_gaps([-5000, 3000, 3100], 0, 3200, 600) == [(0, 3000)]
    # A gap entirely before the window is not reported.
    assert detect_gaps([-5000, -1000, 0, 100], 0, 200, 600) == []
    # A clipped remnant no longer than min_gap is dropped.
    assert detect_gaps([-5000, 500, 520], 0, 600, 600) == []
    # Points after the end are ignored: the trailing gap runs to the end.
    assert detect_gaps([0, 100, 5000], 0, 3000, 600) == [(100, 3000)]


def test_detect_only_after_live_start():
    # Backfilled rows before live_start (with a hole) and the step to the first live point are ignored.
    times = [0, 240, 1000, 2000, 2240, 4000, 4120, 6000, 6120]
    assert detect_gaps(times, 0, 6120, 600, live_start=4000) == [(4120, 6000)]
    assert detect_gaps([4000, 4120], 0, 5000, 600, live_start=4000) == [(4120, 5000)]
    assert detect_gaps([100, 5000], 0, 6000, 600, live_start=5200) == [(5200, 6000)]
    assert detect_gaps([100, 5000], 0, 6000, 600, live_start=5500) == []  # 500 s left after live_start


def test_detect_across_dst_is_utc():
    # Points every 2 min through the fall-back night (01:00-02:00 happens twice): no gap.
    start, end = _utc(2026, 11, 1, 7, 0), _utc(2026, 11, 1, 11, 0)
    times = list(range(start, end + 1, 120))
    assert detect_gaps(times, start, end, 600) == []
    # A hole over the repeated hour has its real length: 2 h, not the 1 h of wall clock.
    hole = (_utc(2026, 11, 1, 8, 0), _utc(2026, 11, 1, 10, 0))  # 01:00 PDT .. 02:00 PST
    holed = [t for t in times if not hole[0] < t < hole[1]]
    assert detect_gaps(holed, start, end, 600) == [hole]
    assert gap_days(hole, LA) == [date(2026, 11, 1)]
    # Spring forward: 01:59 PST -> 03:00 PDT is one minute, not a gap.
    spring = list(range(_utc(2026, 3, 8, 9, 0), _utc(2026, 3, 8, 11, 0) + 1, 120))
    assert detect_gaps(spring, spring[0], spring[-1], 600) == []


# ── Days and windows ─────────────────────────────────────────────────────────

def test_gap_days():
    s, e = _local(2026, 9, 28, 23, 50), _local(2026, 9, 29, 0, 20)
    assert gap_days((s, e), LA) == [date(2026, 9, 28), date(2026, 9, 29)]
    # Rows must lie before end - margin: a gap ending 00:00:30 needs no next-day sheet.
    assert gap_days((s, _local(2026, 9, 29, 0, 0, 30)), LA) == [date(2026, 9, 28)]
    assert gap_days((s, s + 100), LA, margin=60) == []  # no room for a row
    # Spring forward (a 23 h day) and fall back (a 25 h day).
    assert gap_days((_local(2026, 3, 7, 22, 0), _local(2026, 3, 9, 1, 0)), LA) == [
        date(2026, 3, 7), date(2026, 3, 8), date(2026, 3, 9)]
    s = _utc(2026, 11, 2, 7, 30)  # 2026-11-01 23:30 PST
    assert gap_days((s, s + HOUR), LA) == [date(2026, 11, 1), date(2026, 11, 2)]


def test_day_windows():
    days = [date(2026, 9, d) for d in (8, 1, 2, 3, 5, 7, 2)]
    assert day_windows(days) == [(date(2026, 9, 1), date(2026, 9, 3)), (date(2026, 9, 5), date(2026, 9, 5)),
                                 (date(2026, 9, 7), date(2026, 9, 8))]
    long = [date(2026, 8, 1) + timedelta(days=i) for i in range(23)]
    assert day_windows(long) == [(date(2026, 8, 1), date(2026, 8, 10)), (date(2026, 8, 11), date(2026, 8, 20)),
                                 (date(2026, 8, 21), date(2026, 8, 23))]
    assert day_windows([]) == []


# ── Row selection ────────────────────────────────────────────────────────────

def test_gap_index_margins():
    gaps = [(1000.0, 5000.0), (8000.0, 9000.0)]
    assert bf.gap_index(1000, gaps, 60) is None     # the live point itself
    assert bf.gap_index(1060, gaps, 60) is None     # exactly start + margin
    assert bf.gap_index(1060.5, gaps, 60) == 0
    assert bf.gap_index(4940, gaps, 60) is None     # exactly end - margin
    assert bf.gap_index(4939.5, gaps, 60) == 0
    assert bf.gap_index(6000, gaps, 60) is None     # between gaps
    assert bf.gap_index(8500, gaps, 60) == 1
    assert bf.gap_index(8500, gaps, 60, not_after=8500) is None
    assert bf.gap_index(8499, gaps, 60, not_after=8500) == 1


def _fixture_sheets():
    return [ExportSheet(day=day, rows=copy.deepcopy(rows)) for day, rows in sorted(ROWS["sheets"].items())]


def test_cutoff_is_lifted_only_inside_gaps():
    # Fixture rows (PDT): 09-24 07:15:18, 08:43:51; 09-25 00:03:14, 00:07:16, 11:59:32, 13:24:05, 23:56:51.
    live = _local(2026, 9, 25, 0, 5)
    gap = (_local(2026, 9, 25, 11, 0), _local(2026, 9, 25, 14, 0))
    store = Store()
    b = Backfiller(_ctx(), target=store, cutoff=live, live_start=live, dry_run=False, run_id="g",
                   gaps=[gap], gap_margin=60, not_after=_local(2026, 9, 26, 0, 0))
    _, points = b.window_points(_fixture_sheets())
    # Inside the gap and after the cutoff: kept. Outside it: dropped, before the cutoff or not.
    assert [p.local[11:] for p in points] == ["11:59:32", "13:24:05"]
    assert b.stats.skipped_outside_gaps == 5 and b.stats.skipped_overlap == 0
    b.write_points(points)
    assert {_stamp(line) // 10**9 for line in store.lines} == {p.ts for p in points}

    # Without gaps the normal cutoff applies to every row.
    plain = Backfiller(_ctx(), cutoff=live, dry_run=True)
    _, points = plain.window_points(_fixture_sheets())
    assert [p.local for p in points] == ["2026-09-24 07:15:18", "2026-09-24 08:43:51", "2026-09-25 00:03:14"]

    # The margin keeps rows off the live points at the gap's ends; not_after bounds them too.
    near = Backfiller(_ctx(), cutoff=live, dry_run=True, gap_margin=60,
                      gaps=[(_local(2026, 9, 25, 11, 59, 0), _local(2026, 9, 25, 13, 25, 0))])
    assert near.window_points(_fixture_sheets())[1] == []  # 32 s after the start, 55 s before the end
    capped = Backfiller(_ctx(), cutoff=live, dry_run=True, gaps=[gap], not_after=_local(2026, 9, 25, 12, 0))
    assert [p.local[11:] for p in capped.window_points(_fixture_sheets())[1]] == ["11:59:32"]


# ── Planning ─────────────────────────────────────────────────────────────────

def _attempt(start, end, at, status=EMPTY, trailing=False):
    return GapAttempt(gap_start=start, gap_end=end, attempted_at=at, status=status, trailing=trailing)


def test_plan_gap_retry_and_give_up():
    gap = (NOW - 10 * HOUR, NOW - 8 * HOUR)
    assert plan_gap(gap, [], NOW) == (ATTEMPT, None)
    assert plan_gap(gap, [], NOW + 30 * DAY) == (ATTEMPT, None)  # never checked: tried once, however old
    checked = [_attempt(gap[0] - 0.5, gap[1] + 0.5, NOW - HOUR)]
    assert plan_gap(gap, checked, NOW) == (WAIT, NOW + 5 * HOUR)
    assert plan_gap(gap, checked, NOW + 5 * HOUR) == (ATTEMPT, None)
    assert plan_gap(gap, checked, gap[1] + 48 * HOUR + 1) == (GIVE_UP, None)
    assert plan_gap(gap, checked + [_attempt(*gap, NOW, status=GAVE_UP)], NOW) == (GIVEN_UP, None)
    # A covering attempt that filled other parts: this remnant had no rows then either.
    assert plan_gap(gap, [_attempt(gap[0], gap[1] + HOUR, NOW - HOUR, status=FILLED)], NOW)[0] == WAIT
    # Only part of the gap was checked (it grew): new territory, attempt now.
    assert plan_gap(gap, [_attempt(gap[0], gap[1] - HOUR, NOW - HOUR)], NOW) == (ATTEMPT, None)


def test_plan_trailing_gap():
    start = NOW - 5 * HOUR
    open_gap = (start, NOW - 1800)
    earlier = _attempt(start, NOW - 4 * HOUR, NOW - 2 * HOUR, trailing=True)
    # Same ongoing outage, nothing found: retry at most every retry interval.
    assert plan_gap(open_gap, [earlier], NOW, trailing=True) == (WAIT, NOW + 4 * HOUR)
    assert plan_gap(open_gap, [earlier], NOW + 4 * HOUR, trailing=True)[0] == ATTEMPT
    # It filled rows last time: the export has data, keep going.
    filled = _attempt(start, NOW - 4 * HOUR, NOW - 2 * HOUR, status=FILLED, trailing=True)
    assert plan_gap(open_gap, [filled], NOW, trailing=True) == (ATTEMPT, None)
    # An earlier, already closed outage is another gap.
    other = _attempt(start - 5 * HOUR, start - 4 * HOUR, NOW - HOUR, trailing=True)
    assert plan_gap(open_gap, [other], NOW, trailing=True) == (ATTEMPT, None)
    # Open for over giveup hours without any rows: give up while it stays open ...
    old_gap = (NOW - 50 * HOUR, NOW - 1800)
    old = _attempt(old_gap[0], NOW - 20 * HOUR, NOW - 19 * HOUR, trailing=True)
    assert plan_gap(old_gap, [old], NOW, trailing=True) == (GIVE_UP, None)
    gave_up = _attempt(old_gap[0], NOW - 3 * HOUR, NOW - 3 * HOUR, status=GAVE_UP, trailing=True)
    assert plan_gap(old_gap, [old, gave_up], NOW, trailing=True) == (GIVEN_UP, None)
    # ... but once it closes (the dongle is back and uploads its buffer) it is tried again.
    closed = (old_gap[0], NOW - HOUR)
    assert plan_gap(closed, [old, gave_up], NOW + HOUR) == (ATTEMPT, None)


def test_attempt_from_fields():
    a = GapAttempt.from_fields({"gap_start": 1.0, "gap_end": 2.0, "attempted_at": 3.0, "status": "empty",
                                "rows_written": 4.0, "trailing": True})
    assert a == GapAttempt(1.0, 2.0, 3.0, "empty", 4, True)
    assert GapAttempt.from_fields({"gap_start": 1.0, "gap_end": 2.0, "attempted_at": 3.0,
                                   "trailing": "false"}).trailing is False
    assert GapAttempt.from_fields({"gap_start": 1.0, "gap_end": "x", "attempted_at": 3.0}) is None
    assert GapAttempt.from_fields({"gap_end": 2.0}) is None


# ── Filling ──────────────────────────────────────────────────────────────────

def _forbid_side_effects(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("gap-filling must not touch MariaDB/MQTT/Outputs")

    import paho.mqtt.client as mqtt
    import pymysql
    monkeypatch.setattr(pymysql, "connect", boom)
    monkeypatch.setattr(mqtt, "Client", boom)
    monkeypatch.setattr(Outputs, "__init__", boom)
    monkeypatch.setattr(Outputs, "write", boom)


def test_fill_writes_only_rows_inside_the_gap(monkeypatch):
    _forbid_side_effects(monkeypatch)
    hole = (NOW - 2 * DAY, NOW - 2 * DAY + 2 * HOUR)  # 2026-09-28 13:00..15:00 PDT
    rows = _export([hole])
    store, log = Store(_live([hole])), []
    client = FakeClient(rows, log=log)
    report = _filler(store, client).run()

    assert [(g.gap, g.action, g.status) for g in report.gaps] == [(hole, ATTEMPT, FILLED)]
    expected = _inside(rows, hole)
    assert report.gaps[0].rows == len(expected) > 20
    assert client.calls == [(date(2026, 9, 28), date(2026, 9, 28))]
    assert log[0] == "clock"  # the time zone is verified before the download
    assert client._session.closed

    data = store.data_lines()
    assert {_stamp(line) // 10**9 for line in data} == set(expected)
    assert {_measurement(line) for line in data} - {"luxmon_register"} <= SA_MEASUREMENTS
    assert not any(line.startswith("luxmon_cloud") for line in store.lines)
    assert report.points == len(data)

    [record] = store.records()
    assert record["gap_start"] == hole[0] and record["gap_end"] == hole[1]
    assert record["status"] == FILLED and record["rows_written"] == len(expected)
    assert record["trailing"] is False and record["window"] == "2026-09-28..2026-09-28"
    assert "live_start" not in record  # the one-off backfill's cutoff record is left alone
    [line] = [line for line in store.lines if line.startswith("luxmon_backfill")]
    assert line.startswith("luxmon_backfill,mode=gapfill,run_id=gf-test ")
    assert _stamp(line) == hole[0] * 10**9
    # Records are written after the gap's data.
    assert store.writes[-1] == [line]


def test_nothing_new_once_filled_and_rerun_writes_identical_points(monkeypatch):
    hole = (NOW - 2 * DAY, NOW - 2 * DAY + 2 * HOUR)
    rows = _export([hole])
    store = Store(_live([hole]))
    first = _filler(store, FakeClient(rows)).run()
    assert first.gaps[0].status == FILLED
    lines = list(store.lines)

    # The filled rows (4 min apart, within 5 min of the live edges) close the gap.
    client = FakeClient(rows)
    again = _filler(store, client, now=NOW + 3 * HOUR).run()
    assert again.gaps == [] and client.calls == [] and store.lines == lines

    # Where the fill stays invisible to detection, a retry rewrites exactly the same points.
    recent = (NOW - 10 * HOUR, NOW - 8 * HOUR)
    rows = _export([recent])
    blind = Store(_live([recent]), feed_back=False)
    _filler(blind, FakeClient(rows), run_id="one").run()
    one = blind.data_lines()
    assert one
    second = _filler(blind, FakeClient(rows), now=NOW + 7 * HOUR, run_id="two").run()
    assert second.gaps[0].action == ATTEMPT and second.gaps[0].status == FILLED
    assert blind.data_lines() == one + one  # the same series and timestamps: InfluxDB overwrites


def test_dry_run_writes_nothing():
    hole = (NOW - 2 * DAY, NOW - 2 * DAY + 2 * HOUR)
    rows = _export([hole])
    store, client = Store(_live([hole])), FakeClient(rows)
    report = _filler(store, client, dry_run=True).run()
    assert report.gaps[0].status == FILLED and report.gaps[0].rows == len(_inside(rows, hole))
    assert report.points > 0 and report.downloads == 1
    assert store.writes == []

    # A dry run records no attempt: the next real run still does the work.
    real = _filler(store, FakeClient(rows)).run()
    assert real.gaps[0].action == ATTEMPT and store.writes


def test_unfillable_gap_retry_and_give_up(caplog):
    hole = (NOW - 6 * HOUR, NOW - 4 * HOUR)
    store, calls = Store(_live([hole])), []

    def run(at):
        client = FakeClient(())
        report = _filler(store, client, now=at).run()
        calls.extend(client.calls)
        return report.gaps[0]

    first = run(NOW)
    assert first.action == ATTEMPT and first.status == EMPTY and first.rows == 0
    assert store.records()[-1]["status"] == EMPTY and store.records()[-1]["rows_written"] == 0
    assert len(calls) == 1
    after_first = list(store.lines)
    waiting = run(NOW + HOUR)
    assert waiting.action == WAIT and waiting.next_retry == NOW + 6 * HOUR and len(calls) == 1
    assert run(NOW + 6 * HOUR).action == ATTEMPT and len(calls) == 2
    assert run(NOW + 12 * HOUR).action == ATTEMPT and len(calls) == 3
    assert len(store.records()) == 3

    with caplog.at_level(logging.INFO, logger="luxmon.gapfill"):
        assert run(hole[1] + 48 * HOUR + 1).action == GIVE_UP
        assert run(hole[1] + 60 * HOUR).action == GIVEN_UP
        assert run(hole[1] + 70 * HOUR).action == GIVEN_UP
    assert len(calls) == 3  # giving up downloads nothing
    assert sum("Giving up" in r.getMessage() for r in caplog.records) == 1
    assert [r["status"] for r in store.records()] == [EMPTY, EMPTY, EMPTY, GAVE_UP]

    # A restart re-reads the attempts from InfluxDB: nothing is downloaded again.
    restarted = Store(_live([hole]))
    restarted.lines = after_first
    client = FakeClient(())
    assert _filler(restarted, client, now=NOW + HOUR).run().gaps[0].action == WAIT
    assert client.calls == []


def test_internet_outage_trailing_gap_then_buffered_upload():
    down = NOW - 3 * HOUR
    live = _live(start=LIVE_FROM, stop=down)
    store, calls = Store(live), []

    def run(at, client):
        report = _filler(store, client, now=at).run()
        calls.extend(client.calls)
        return report

    # While the internet is down: a still-open trailing gap, nothing at EG4 yet.
    first = run(NOW, FakeClient(()))
    assert [(g.gap, g.trailing, g.status) for g in first.gaps] == [((down, NOW - 1800), True, EMPTY)]
    assert run(NOW + 3 * HOUR, FakeClient(())).gaps[0].action == WAIT
    assert run(NOW + 6 * HOUR, FakeClient(())).gaps[0].status == EMPTY
    assert len(calls) == 2

    # Back online at NOW + 7 h: live data resumes and the dongle uploads its 5-min buffer,
    # which starts ~20 min after the outage began.
    back = NOW + 7 * HOUR
    store.soc.extend(_live(start=back, stop=back + DAY))
    store.soc.sort()
    buffered = list(range(down + 20 * MIN + 13, back, 300))
    report = run(NOW + 8 * HOUR, FakeClient(buffered))
    [gap] = report.gaps
    assert gap.gap == (down, back) and not gap.trailing and gap.action == ATTEMPT and gap.status == FILLED
    assert gap.rows == len(buffered)
    # The first ~20 min stay a (smaller) gap, already checked by that attempt: it waits.
    later = run(NOW + 11 * HOUR, FakeClient(buffered))
    assert [(g.start, g.action) for g in later.gaps] == [(down, WAIT)]
    assert later.gaps[0].end == min(buffered)


def test_download_budget(monkeypatch):
    holes = [(NOW - d * DAY, NOW - d * DAY + HOUR) for d in (6, 4, 2)]  # three separate days
    rows = _export(holes)
    store = Store(_live(holes))
    client = FakeClient(rows)
    report = _filler(store, client, options=GapFillOptions(max_downloads=2)).run()
    assert client.calls == [(date(2026, 9, 24), date(2026, 9, 24)), (date(2026, 9, 26), date(2026, 9, 26))]
    assert [g.action for g in report.gaps] == [ATTEMPT, ATTEMPT, DEFERRED]
    assert [g.status for g in report.gaps] == [FILLED, FILLED, None]
    assert len(store.records()) == 2  # a deferred gap is not recorded as attempted

    client = FakeClient(rows)
    report = _filler(store, client, now=NOW + 3 * HOUR, options=GapFillOptions(max_downloads=2)).run()
    assert client.calls == [(date(2026, 9, 28), date(2026, 9, 28))]
    assert [(g.gap, g.status) for g in report.gaps] == [(holes[2], FILLED)]
    assert _filler(store, FakeClient(rows), now=NOW + 6 * HOUR).run().gaps == []


def test_consecutive_gap_days_share_one_download():
    holes = [(NOW - 3 * DAY, NOW - 3 * DAY + HOUR), (NOW - 2 * DAY, NOW - 2 * DAY + HOUR)]
    client = FakeClient(_export(holes))
    report = _filler(Store(_live(holes)), client).run()
    assert client.calls == [(date(2026, 9, 27), date(2026, 9, 28))]
    assert [g.status for g in report.gaps] == [FILLED, FILLED]


def test_safety_checks_stop_before_writing():
    hole = (NOW - 2 * DAY, NOW - 2 * DAY + 2 * HOUR)
    rows = _export([hole])
    # The inverter clock disagrees with the plant time zone: no download, nothing written.
    store, client = Store(_live([hole])), FakeClient(rows, offset=-6 * 3600)
    with pytest.raises(BackfillError, match="does not match"):
        _filler(store, client).run()
    assert client.calls == [] and store.writes == []
    # Unreadable clock: same.
    store, client = Store(_live([hole])), FakeClient(rows, clock_error=CloudTransientError("down"))
    with pytest.raises(BackfillError, match="not verified"):
        _filler(store, client).run()
    assert store.writes == []
    # The newest live unit tags differ from the fill's (while the gap's own
    # neighbours match it): a settings mismatch, nothing written.
    store = Store(_live([hole]), units={"soc": "%", "temp_radiator_1": "°C"})
    store.units_around = lambda start, stop: {"soc": "%", "temp_radiator_1": "°F"}
    with pytest.raises(BackfillError, match="unit tags"):
        _filler(store, FakeClient(rows)).run()
    assert store.writes == []
    # No live data at all: nothing to fill, no portal access.
    client = FakeClient(rows)
    report = _filler(Store(()), client).run()
    assert report.live_start is None and report.gaps == [] and client.log == []


def test_gap_with_other_units_around_it_is_given_up(caplog):
    # temperature_unit was celsius around the gap and is fahrenheit now: the
    # fill would land in another series than the live points around it.
    hole = (NOW - 2 * DAY, NOW - 2 * DAY + 2 * HOUR)
    recent = (NOW - 10 * HOUR, NOW - 8 * HOUR)
    rows = _export([hole, recent])
    store = Store(_live([hole, recent]), feed_back=False)
    store.units_around = lambda start, stop: (
        {"soc": "%", "temp_radiator_1": "°C"} if start < hole[1] else {"soc": "%", "temp_radiator_1": "°F"})
    with caplog.at_level(logging.WARNING, logger="luxmon.gapfill"):
        report = _filler(store, FakeClient(rows)).run()
    old, new = report.gaps
    assert (old.status, old.rows, new.status) == (GAVE_UP, 0, FILLED)
    assert "temp_radiator_1" in old.skip_reason and any("Not filling" in r.getMessage() for r in caplog.records)
    stamps = {_stamp(line) // 10**9 for line in store.data_lines()}
    assert stamps == set(_inside(rows, recent))
    gave_up = [r for r in store.records() if r["status"] == GAVE_UP]
    assert len(gave_up) == 1 and gave_up[0]["gap_start"] == hole[0] and "°C" in gave_up[0]["reason"]
    # Never tried again.
    client = FakeClient(rows)
    again = _filler(store, client, now=NOW + 7 * HOUR).run()
    assert again.gaps[0].action == GIVEN_UP

    # A unit change inside the gap's surroundings (two unit tags): not filled either.
    store = Store(_live([hole]))
    store.units_around = lambda start, stop: {"soc": "%", "temp_radiator_1": {"°C", "°F"}}
    report = _filler(store, FakeClient(rows)).run()
    assert report.gaps[0].status == GAVE_UP and store.data_lines() == []


def test_gap_ends_at_the_closing_readings_upload_time():
    # The closing live point at hole[1] holds a reading EG4 received 300 s
    # earlier (a first upload after a restart): export rows after that are
    # newer readings and must not be written before it.
    hole = (NOW - 2 * DAY, NOW - 2 * DAY + 2 * HOUR)
    rows = _export([hole])
    store = Store(_live([hole]))
    store.ages = lambda ts: 300.0 if ts == hole[1] else 0.0
    report = _filler(store, FakeClient(rows)).run()
    reading = hole[1] - 300
    assert report.gaps[0].gap == (hole[0], reading)
    stamps = {_stamp(line) // 10**9 for line in store.data_lines()}
    assert stamps == set(_inside(rows, (hole[0], reading))) and max(stamps) < reading - 60
    assert store.records()[0]["gap_end"] == reading

    # No luxmon_cloud point at the closing point: assume the oldest reading the live transport writes.
    store = Store(_live([hole]))
    store.ages = {}
    report = _filler(store, FakeClient(rows)).run()
    assert report.gaps[0].end == hole[1] - gf.max_reading_age() == hole[1] - 480
    assert max(_stamp(line) // 10**9 for line in store.data_lines()) < hole[1] - 540
    # A trailing gap ends at now - settle, not at a live point: unchanged.
    trailing = _filler(Store(_live([], stop=NOW - 3 * HOUR)), FakeClient(()), dry_run=True).run()
    assert trailing.gaps[0].trailing and trailing.gaps[0].end == NOW - 1800


def test_dst_second_pass_only_is_not_read_as_the_first(monkeypatch):
    # 2026-11-01 fall-back: live every 2 min until 00:50 PDT, then from 01:00 PST.
    # The export has 00:00-00:48 and only the second 01:00-01:56 pass: those
    # rows must not be written an hour early (as PDT) into the gap.
    monkeypatch.setattr(gf, "gap_days", lambda gap, tz, margin=60: [date(2026, 11, 1)])
    now = _utc(2026, 11, 2, 20, 0)
    gap = (_utc(2026, 11, 1, 7, 50), _utc(2026, 11, 1, 9, 0))
    live = [t for t in range(now - 3 * DAY, now + 1, LIVE_STEP) if not gap[0] < t < gap[1]]
    store = Store(live)
    first = list(range(_utc(2026, 11, 1, 7, 0), _utc(2026, 11, 1, 7, 49), 240))       # 00:00-00:48 PDT
    second = list(range(_utc(2026, 11, 1, 9, 0), _utc(2026, 11, 1, 9, 57), 240))      # 01:00-01:56 PST
    client = FakeClient(first + second)
    report = _filler(store, client, now=now).run()
    assert [g.gap for g in report.gaps] == [gap]
    assert store.data_lines() == [] and report.gaps[0].status == EMPTY

    # Even rows of the first pass (lux-mon down, dongle online) cannot be told
    # apart from second-pass rows: repeated wall times are dropped, the rest kept.
    gap2 = (_utc(2026, 11, 1, 7, 30), _utc(2026, 11, 1, 8, 50))   # 00:30 PDT .. 01:50 PDT
    live = [t for t in range(now - 3 * DAY, now + 1, LIVE_STEP) if not gap2[0] < t < gap2[1]]
    store = Store(live)
    rows = list(range(_utc(2026, 11, 1, 7, 30) + 37, _utc(2026, 11, 1, 8, 50), 240))  # 00:30:37-01:46 PDT
    filler = _filler(store, FakeClient(rows), now=now)
    report = filler.run()
    stamps = {_stamp(line) // 10**9 for line in store.data_lines()}
    assert report.gaps[0].status == FILLED
    assert stamps == {t for t in _inside(rows, gap2) if t < _utc(2026, 11, 1, 8, 0)}
    assert filler.backfiller.stats.dst_dropped == len([t for t in rows if t >= _utc(2026, 11, 1, 8, 0)])


def test_live_start_is_respected():
    hole = (NOW - 2 * DAY, NOW - 2 * DAY + 2 * HOUR)
    # Live data began inside the hole's day, after it: before live_start is the backfill's job.
    live = _live([hole], start=hole[1])
    client = FakeClient(_export([hole]))
    report = _filler(Store(live), client).run()
    assert report.live_start == hole[1] and report.gaps == [] and client.calls == []
    # A recorded live_start (luxmon_backfill) wins over the earliest soc point.
    store = Store(_live([hole]), recorded=hole[1] + HOUR)
    report = _filler(store, FakeClient(_export([hole]))).run()
    assert report.live_start == hole[1] + HOUR and report.gaps == []


def test_portal_blocked_and_stop():
    hole = (NOW - 2 * DAY, NOW - 2 * DAY + 2 * HOUR)
    store, created = Store(_live([hole])), []

    def factory():
        created.append(1)
        return FakeClient(_export([hole]))

    report = GapFiller(_ctx(), store, client_factory=factory, clock=lambda: NOW,
                       portal_blocked=lambda: "login rejected").run()
    assert report.blocked == "login rejected" and report.gaps[0].action == BLOCKED
    assert created == [] and store.writes == []

    stop = threading.Event()
    stop.set()
    with pytest.raises(GapFillStopped):
        GapFiller(_ctx(), store, client_factory=factory, clock=lambda: NOW, stop=stop).run()
    assert created == [] and store.writes == []


def test_influx_target_gap_queries():
    t0 = datetime(2026, 9, 29, 13, 0, 0, 250000, tzinfo=timezone.utc)

    class Rec:
        def __init__(self, t, value, **values):
            self._t, self._v = t, value
            self.values = {**values, "_time": t, "_value": value}

        def get_time(self):
            return self._t

        def get_value(self):
            return self._v

    queries = []

    def responder(flux):
        if "luxmon_backfill" in flux:
            return [Rec(t0, 1.5, _field="gap_start", run_id="a"), Rec(t0, 9.0, _field="gap_end", run_id="a"),
                    Rec(t0, "empty", _field="status", run_id="a"), Rec(t0, 2.0, _field="gap_start", run_id="b")]
        if "|> last()" in flux:
            return [Rec(t0, 50.0), Rec(t0 - timedelta(minutes=5), 49.0)]
        return [Rec(t0 + timedelta(minutes=2), 51.0), Rec(t0, 50.0)]

    class Client:
        def query_api(self):
            class Q:
                def query(self, flux, org=None):
                    queries.append(flux)
                    return [SimpleNamespace(records=responder(flux))]
            return Q()

    target = bf.InfluxTarget(OutputConfig(influx_token="tok", influx_bucket="luxmon", influx_org="luxmon"),
                             client=Client())
    assert target.soc_times(t0.timestamp(), t0.timestamp() + 600) == [t0.timestamp(), t0.timestamp() + 120]
    assert 'r.name == "soc"' in queries[-1] and "range(start: 2026-09-29T13:00:00.250000Z, stop:" in queries[-1]
    assert target.last_soc_before(t0.timestamp() + 60, t0.timestamp() - 3600) == t0.timestamp()
    assert target.last_soc_before(10.0, 10.0) is None and target.soc_times(10.0, 5.0) == []
    records = sorted(target.gapfill_records(0.0), key=lambda r: r["gap_start"])
    assert records == [{"gap_start": 1.5, "gap_end": 9.0, "status": "empty"}, {"gap_start": 2.0}]
    assert 'r.mode == "gapfill"' in queries[-1]


def test_influx_target_reading_time_and_unit_sets():
    t0 = datetime(2026, 9, 30, 23, 45, 1, 554007, tzinfo=timezone.utc)
    answers = {}

    def rec(t, value, **values):
        return SimpleNamespace(values={**values, "_time": t, "_value": value},
                               get_time=lambda: t, get_value=lambda: value)

    class Client:
        def query_api(self):
            return SimpleNamespace(query=lambda flux, org=None: [SimpleNamespace(records=answers["now"](flux))])

    target = bf.InfluxTarget(OutputConfig(influx_token="tok", influx_bucket="luxmon", influx_org="luxmon"),
                             client=Client())
    ts = t0.timestamp()
    answers["now"] = lambda flux: [rec(t0, "2026-09-30 23:44:30", _field="server_time"),
                                   rec(t0, 27.0, _field="data_age_s")]
    assert target.reading_time(ts) == _utc(2026, 9, 30, 23, 44, 30)
    answers["now"] = lambda flux: [rec(t0, 27.0, _field="data_age_s")]
    assert target.reading_time(ts) == pytest.approx(ts - 27.0)
    answers["now"] = lambda flux: []
    assert target.reading_time(ts) is None

    answers["now"] = lambda flux: [rec(t0, 80.0, name="temp_radiator_1", unit="°F"),
                                   rec(t0, 27.0, name="temp_radiator_1", unit="°C"), rec(t0, 50.0, name="soc", unit="%")]
    assert target.unit_sets(ts - 3600, ts) == {"temp_radiator_1": {"°F", "°C"}, "soc": {"%"}}
    assert target.unit_sets(ts, ts) == {}


# ── Thread / collector ───────────────────────────────────────────────────────

def _cfg(**kw):
    base = dict(inverter_serial=SERIAL, transport="cloud_http", cloud_username="user@example.com",
                cloud_password="s3cr3t-P@ss",
                outputs=OutputConfig(mariadb_enabled=False, mqtt_enabled=False, influx_enabled=True,
                                     temperature_unit="fahrenheit"))
    base.update(kw)
    return CollectorConfig(**base)


def test_disabled_reasons(monkeypatch):
    monkeypatch.delenv("LUX_GAPFILL_ENABLED", raising=False)
    assert gapfill_disabled_reason(_cfg()) is None
    assert gapfill_disabled_reason(_cfg(transport="tcp_active")) is None  # the dongle uploads to EG4 anyway
    assert "LUX_CLOUD_USERNAME" in gapfill_disabled_reason(_cfg(cloud_username=""))
    assert "LUX_CLOUD_USERNAME" in gapfill_disabled_reason(_cfg(cloud_password=""))
    assert "replay" in gapfill_disabled_reason(_cfg(transport="replay"))
    assert "replay" in gapfill_disabled_reason(_cfg(replay_file="capture.bin"))
    monkeypatch.setenv("LUX_GAPFILL_ENABLED", "false")
    assert "LUX_GAPFILL_ENABLED" in gapfill_disabled_reason(_cfg())
    assert gapfill_disabled_reason(_cfg(), GapFillSettings(enabled=True)) is None


def test_settings_from_env(monkeypatch, caplog):
    for var in ("LUX_GAPFILL_ENABLED", "LUX_GAPFILL_INTERVAL_MIN", "LUX_GAPFILL_LOOKBACK_DAYS",
                "LUX_GAPFILL_MIN_GAP_MIN"):
        monkeypatch.delenv(var, raising=False)
    s = settings_from_env()
    assert s.enabled and s.interval_sec == 180 * 60
    assert s.options == GapFillOptions(lookback_days=7, min_gap_sec=600)
    assert s.options.settle_sec == 1800 and s.options.margin_sec == 60 and s.options.max_downloads == 6
    assert s.options.retry_sec == 6 * HOUR and s.options.giveup_sec == 48 * HOUR

    monkeypatch.setenv("LUX_GAPFILL_ENABLED", "0")
    monkeypatch.setenv("LUX_GAPFILL_INTERVAL_MIN", "60")
    monkeypatch.setenv("LUX_GAPFILL_LOOKBACK_DAYS", "3")
    monkeypatch.setenv("LUX_GAPFILL_MIN_GAP_MIN", "15")
    s = settings_from_env()
    assert not s.enabled and s.interval_sec == 3600 and s.options.lookback_days == 3
    assert s.options.min_gap_sec == 900

    monkeypatch.setenv("LUX_GAPFILL_INTERVAL_MIN", "1")      # would hammer the portal
    monkeypatch.setenv("LUX_GAPFILL_LOOKBACK_DAYS", "400")
    monkeypatch.setenv("LUX_GAPFILL_MIN_GAP_MIN", "2")       # filled 4-min rows would be gaps again
    with caplog.at_level(logging.WARNING, logger="luxmon.gapfill"):
        s = settings_from_env()
    assert s.interval_sec == 30 * 60 and s.options.lookback_days == 30 and s.options.min_gap_sec == 360
    assert len(caplog.records) == 3
    monkeypatch.setenv("LUX_GAPFILL_INTERVAL_MIN", "1e9")    # "never": Event.wait would overflow
    assert settings_from_env().interval_sec == 7 * 24 * 3600
    monkeypatch.setenv("LUX_GAPFILL_INTERVAL_MIN", "often")
    assert settings_from_env().interval_sec == 180 * 60
    assert make_options(min_gap_min=float("nan")).min_gap_sec == 600


def _thread(tmp_path, store=None, client=None, **kw):
    kw.setdefault("settings", GapFillSettings())
    kw.setdefault("startup_delay", 0)
    kw.setdefault("target_factory", lambda out: store)
    kw.setdefault("client_factory", lambda **k: client)
    kw.setdefault("tz_loader", lambda: "America/Los_Angeles")
    kw.setdefault("clock", lambda: NOW)
    return GapFillThread(_cfg(), "eg4_12000xp", lock_file=str(tmp_path / "gapfill.lock"), **kw)


def test_thread_run_uses_its_own_client_and_writes_only_influx(monkeypatch, tmp_path):
    _forbid_side_effects(monkeypatch)
    hole = (NOW - 2 * DAY, NOW - 2 * DAY + 2 * HOUR)
    store, clients = Store(_live([hole])), []

    def client_factory(**kw):
        clients.append(kw)
        return FakeClient(_export([hole]))

    t = _thread(tmp_path, store, client_factory=client_factory)
    report = t.run_once()
    assert report.gaps[0].status == FILLED and store.data_lines() and store.closed
    [kw] = clients
    assert kw["username"] == "user@example.com" and kw["inverter_serial"] == SERIAL
    assert kw["model"] == "eg4_12000xp" and kw["request_delay"] == bf.DEFAULT_REQUEST_DELAY
    assert kw["sleep"] == t._sleep  # waits end when the collector stops
    assert "session_factory" not in kw  # a fresh session per run, never the live transport's

    # The DB time zone differs from the inverter clock: the run fails safely, nothing written.
    store = Store(_live([hole]))
    assert _thread(tmp_path, store, FakeClient(_export([hole])), tz_loader=lambda: "America/Chicago").run_once() is None
    assert store.writes == []
    # InfluxDB output off: nothing at all.
    t = _thread(tmp_path, Store(_live([hole])), FakeClient(()))
    t.cfg.outputs.influx_enabled = False
    assert t.run_once() is None


def test_thread_survives_errors_and_stops_promptly(tmp_path, caplog):
    calls = []

    def broken(out):
        calls.append(1)
        raise RuntimeError("InfluxDB exploded")

    t = _thread(tmp_path, target_factory=broken, settings=GapFillSettings(interval_sec=0.01))
    with caplog.at_level(logging.ERROR, logger="luxmon.gapfill"):
        t.start()
        deadline = time.monotonic() + 5
        while len(calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert t.is_alive() and len(calls) >= 3
        started = time.monotonic()
        t.stop()
    assert not t.is_alive() and time.monotonic() - started < 2
    assert any("failed unexpectedly" in r.getMessage() for r in caplog.records)

    # A long startup delay does not delay the stop.
    t = _thread(tmp_path, target_factory=broken, startup_delay=3600)
    calls.clear()
    t.start()
    started = time.monotonic()
    t.stop()
    assert not t.is_alive() and time.monotonic() - started < 2 and calls == []

    # Waits inside a run (request pacing, retry backoff) end at once on stop.
    with pytest.raises(GapFillStopped):
        t._sleep(30)
    # Also while the inverter-clock check is retrying: a stop, not a time zone error.
    with pytest.raises(GapFillStopped):
        bf.verify_timezone(FakeClient(clock_error=GapFillStopped("collector stopping")), LA, strict=True)

    # An interval beyond threading.TIMEOUT_MAX does not kill the thread after its first run.
    t = _thread(tmp_path, target_factory=broken, startup_delay=0,
                settings=GapFillSettings(interval_sec=1e12))
    calls.clear()
    t.start()
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    assert calls and t.is_alive()
    t.stop()
    assert not t.is_alive()


def test_thread_auth_rejection_pauses_the_portal(tmp_path, caplog):
    hole = (NOW - 2 * DAY, NOW - 2 * DAY + 2 * HOUR)
    clients = []

    def factory(**kw):
        clients.append(kw)
        return FakeClient((), clock_error=CloudAuthError("account or password error"))

    store = Store(_live([hole]))
    t = _thread(tmp_path, store, client_factory=factory)
    with caplog.at_level(logging.WARNING, logger="luxmon.gapfill"):
        assert t.run_once() is None
    assert any("rejected the gap-fill login" in r.getMessage() for r in caplog.records)
    report = t.run_once()  # a day-long pause: no second login
    assert report.gaps[0].action == BLOCKED and "rejected" in report.blocked and len(clients) == 1
    # The collector reports the live transport's login as rejected: no gap-fill login either.
    t = _thread(tmp_path, store, client_factory=factory, portal_blocked=lambda: "live login rejected")
    assert t.run_once().blocked == "live login rejected" and len(clients) == 1


def test_runs_never_overlap(tmp_path, caplog):
    store = Store(())
    t = _thread(tmp_path, store)
    with exclusive_run(str(tmp_path / "other.lock")) as alone:
        assert alone
        with caplog.at_level(logging.INFO, logger="luxmon.gapfill"):
            assert t.run_once() is None
    assert t.runs == 0 and any("in progress" in r.getMessage() for r in caplog.records)
    assert t.run_once() is not None and t.runs == 1

    # Another process (a manual --fill-gaps) holding the lock file.
    fcntl = pytest.importorskip("fcntl")
    path = tmp_path / "gapfill.lock"
    with open(path, "a") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with exclusive_run(str(path)) as alone:
            assert not alone
        assert t.run_once() is None and t.runs == 1
    with exclusive_run(str(path)) as alone:
        assert alone


def _bare_collector(cfg):
    c = PassiveCollector.__new__(PassiveCollector)
    c.cfg = cfg
    c.driver = get_driver("eg4_12000xp")
    c._stop = threading.Event()
    c._transport = None
    c._outputs = None
    c._gapfill = None
    return c


def test_collector_starts_gapfill_only_with_a_login(monkeypatch):
    monkeypatch.delenv("LUX_GAPFILL_ENABLED", raising=False)
    started, stopped = [], []
    monkeypatch.setattr(GapFillThread, "start", lambda self: started.append(self))
    monkeypatch.setattr(GapFillThread, "stop", lambda self, timeout=5.0: stopped.append(self))

    c = _bare_collector(_cfg(cloud_password=""))
    c._start_gapfill()
    assert c._gapfill is None and started == []

    c = _bare_collector(_cfg(transport="replay"))
    c._start_gapfill()
    assert c._gapfill is None

    c = _bare_collector(_cfg())
    c._start_gapfill()
    assert started == [c._gapfill] and c._gapfill.model == "eg4_12000xp" and c._gapfill.cfg is c.cfg
    c.stop()
    assert stopped == [c._gapfill]

    monkeypatch.setenv("LUX_GAPFILL_ENABLED", "false")
    c = _bare_collector(_cfg())
    c._start_gapfill()
    assert c._gapfill is None

    # A failure while starting never takes the collector down.
    monkeypatch.delenv("LUX_GAPFILL_ENABLED")
    monkeypatch.setattr(gf, "settings_from_env", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    c = _bare_collector(_cfg())
    c._start_gapfill()
    assert c._gapfill is None


def test_collector_blocks_gapfill_while_live_login_is_rejected():
    c = _bare_collector(_cfg())
    assert c._gapfill_portal_blocked() is None
    c._transport = SimpleNamespace(stats=lambda: {"auth_failed": False})
    assert c._gapfill_portal_blocked() is None
    c._transport = SimpleNamespace(stats=lambda: {"auth_failed": True})
    assert "rejected" in c._gapfill_portal_blocked()
    c._transport = SimpleNamespace(stats=lambda: 1 / 0)
    assert c._gapfill_portal_blocked() is None


# ── CLI ──────────────────────────────────────────────────────────────────────

def _settings(**kw):
    cfg = _cfg()
    base = dict(cfg=cfg, model="eg4_12000xp", temperature_unit="fahrenheit", tz=LA, tz_explicit=False,
                db_ok=True, db_temperature_unit="fahrenheit")
    base.update(kw)
    return Settings(**base)


def test_cli_fill_gaps_flags():
    args = build_parser().parse_args(["--fill-gaps", "--lookback-days", "3", "--min-gap-min", "15", "--dry-run"])
    assert args.fill_gaps and args.lookback_days == 3 and args.min_gap_min == 15
    for extra in (["--start", "2026-09-01"], ["--find-start"], ["--compare-live", "2026-09-01"],
                  ["--from-xls", "x.xls"], ["--cutoff", "2026-09-01"]):
        with pytest.raises(BackfillError, match="--fill-gaps"):
            Runner(build_parser().parse_args(["--fill-gaps", *extra]), _settings(),
                   target_factory=lambda o: Store()).run()
    with pytest.raises(BackfillError, match="only apply to --fill-gaps"):
        Runner(build_parser().parse_args(["--start", "2026-09-01", "--lookback-days", "3"]), _settings(),
               target_factory=lambda o: Store()).run()


def test_cli_fill_gaps_dry_run_prints_and_writes_nothing(monkeypatch, tmp_path):
    _forbid_side_effects(monkeypatch)
    now = int(time.time()) // LIVE_STEP * LIVE_STEP
    hole = (now - 2 * DAY, now - 2 * DAY + HOUR)
    rows = _export([hole])
    store, clients, out = Store(_live([hole], start=now - 9 * DAY, stop=now)), [], []

    def factory(**kw):
        clients.append(FakeClient(rows))
        return clients[-1]

    args = build_parser().parse_args(["--fill-gaps", "--dry-run", "--save-xls", str(tmp_path / "xls")])
    runner = Runner(args, _settings(), target_factory=lambda o: store, client_factory=factory, out=out.append)
    assert runner.run() == 0
    runner.close()
    assert len(list((tmp_path / "xls").glob("eg4_export_*.xls"))) == 1
    text = "\n".join(out)
    assert "── Gap fill (dry run: nothing written) ──" in text
    assert f"{len(_inside(rows, hole))} export rows would be written" in text
    assert "(60 min)" in text and "export downloads 1" in text
    assert store.writes == [] and len(clients) == 1 and clients[0]._session.closed

    # Real run: data plus the attempt record; a second run finds nothing to do.
    out.clear()
    args = build_parser().parse_args(["--fill-gaps"])
    assert Runner(args, _settings(), target_factory=lambda o: store, client_factory=factory,
                  out=out.append).run() == 0
    assert any("wrote" in line for line in out) and store.records()[0]["status"] == FILLED
    out.clear()
    assert Runner(args, _settings(), target_factory=lambda o: store, client_factory=factory,
                  out=out.append).run() == 0
    assert "no gaps" in out and len(clients) == 2


def test_cli_prints_the_plan_when_the_portal_step_fails():
    now = int(time.time()) // LIVE_STEP * LIVE_STEP
    hole = (now - 2 * DAY, now - 2 * DAY + HOUR)
    store, out = Store(_live([hole], start=now - 9 * DAY, stop=now)), []
    settings = _settings()
    settings.cfg.cloud_password = ""
    runner = Runner(build_parser().parse_args(["--fill-gaps", "--dry-run"]), settings,
                    target_factory=lambda o: store, out=out.append)
    with pytest.raises(BackfillError, match="LUX_CLOUD_PASSWORD"):
        runner.run()
    text = "\n".join(out)
    assert "(60 min) -> not done" in text and "stopped part-way" in text
    assert store.writes == []
