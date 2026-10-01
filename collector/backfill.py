"""Backfill lux-mon's InfluxDB history from the EG4 portal's data export.

The EG4 portal keeps the inverter's full history and offers it as a legacy
BIFF .xls download (one worksheet per day, one row per ~4 min log interval,
values in display units). This tool downloads that export in windows of at
most 10 days, maps every row to the SAME raw input-register dict the live
cloud transport (collector/comm/cloud_http.py) would build, and then runs the
live decode -> clamp -> temperature-unit path, so the backfilled
`luxmon_register` and SolarAssistant-style points have byte-identical
measurement and tag sets (same name, same unit tag) as the live ones.

Run it inside the collector container (same environment and DB-authoritative
settings as the collector):

    docker exec lux-collector python -m collector.backfill --find-start --dry-run
    docker exec lux-collector python -m collector.backfill --start 2025-06-01

--fill-gaps fills holes in the live data instead (collector.gapfill, which
the collector also runs in the background):

    docker exec lux-collector python -m collector.backfill --fill-gaps --dry-run

Safety:
  - Read-only towards EG4: the login and runtime POSTs of cloud_http (its
    allowlist is unchanged) plus one GET, the data export, which is checked
    against the backfill's own allowlist (_EXPORT_GET_ALLOWLIST).
  - Writes only InfluxDB: `luxmon_register`, the SolarAssistant-style
    measurements and one `luxmon_backfill` provenance point per window.
    Never `luxmon_cloud`, MariaDB, MQTT, alerts or the hourly energy rollup.
  - Never writes at or after the first live point (the earliest
    `luxmon_register` soc point, remembered in `luxmon_backfill.live_start`
    before each window's data, so re-runs keep the same cutoff even after an
    interrupted run). A later --cutoff needs --allow-live-overlap. Re-running
    a range overwrites the same points (same series and timestamp); it never
    duplicates them. The only exception is --fill-gaps: rows strictly inside
    a detected hole in the live data (Backfiller `gaps`), nothing else.
  - Write runs need a verified plant time zone (the export's Time column is
    plant-local): portal runs check it against the inverter clock and stop
    when it differs or cannot be read; --from-xls runs need --tz. Without
    MariaDB they also need --model and --temp-unit.
  - LUX_CLOUD_USERNAME / LUX_CLOUD_PASSWORD come from the environment only and
    are never logged.
"""

import argparse
import json
import logging
import math
import random
import re
import sys
import time
import uuid
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Iterable, Iterator, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from .collector import (
    CollectorConfig,
    _env_or,
    _load_db_output_settings,
    _load_db_serials,
    _load_db_setting,
    clamp_values,
    config_from_env,
)
from .comm.cloud_http import (
    RUNTIME_PATH,
    SESSION_MAX_AGE_SEC,
    _SESSION_MARKERS,
    _TRANSIENT_MARKERS,
    CloudApiError,
    CloudAuthError,
    CloudHttpTransport,
    CloudRateLimited,
    CloudSessionExpired,
    CloudTransientError,
    _model_quirks,
    _parse_server_time,
    _put16,
    energy_to_registers,
    runtime_to_registers,
)
from .drivers import ModelDriver
from .drivers.registry import DEFAULT_MODEL, get_driver
from .outputs import OutputConfig, _to_line, convert_temperatures, influx_lines
from .settings import DEFAULTS

logger = logging.getLogger("luxmon.backfill")


# ── EG4 export endpoint ─────────────────────────────────────────────────────
# GET {base}/WManage/web/analyze/data/export/{serial}/{startDate}?endDateText={endDate}
EXPORT_PATH = "/WManage/web/analyze/data/export/{serial}/{day}"
# Read-only GET allowlist for the backfill only. cloud_http's POST allowlist
# (_ALLOWED_PATHS) is unchanged: the live transport can never call this.
_EXPORT_GET_ALLOWLIST = (
    re.compile(r"/WManage/web/analyze/data/export/[A-Za-z0-9]{1,32}/\d{4}-\d{2}-\d{2}"),
)
_EXPORT_GET_PARAMS = frozenset({"endDateText"})
_EXPORT_HEADERS = {
    "Accept": "application/vnd.ms-excel, application/octet-stream;q=0.9, */*;q=0.1",
    "User-Agent": "lux-mon backfill",
}
# OLE2 compound document signature: every BIFF .xls workbook starts with it.
_XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

MAX_WINDOW_DAYS = 10         # the server returns at most 10 day-sheets per request
DEFAULT_REQUEST_DELAY = 3.0  # seconds between export requests
MIN_REQUEST_DELAY = 1.0
EXPORT_TIMEOUT = (10, 180)   # (connect, read) seconds; a 10-day export is ~5 MB
MAX_ATTEMPTS = 4             # per request, for transient failures
RETRY_BASE_SEC = 5.0         # 5, 10, 20 s (x2 per attempt, ±10 % jitter)
RATE_LIMIT_BASE_SEC = 30.0   # after HTTP 429: 30, 60, 120 s
FIND_START_FLOOR = date(2020, 1, 1)

# ── InfluxDB ────────────────────────────────────────────────────────────────
BATCH_LINES = 5000
BACKFILL_MEASUREMENT = "luxmon_backfill"
MATCH_WINDOW_SEC = 180       # --compare-live: nearest live point within ±3 min
UNIT_CHECK_DAYS = 30         # live unit tags are read from the last 30 days
TZ_CHECK_TOLERANCE_SEC = 900

# ── Status text -> register 0 (working mode) ────────────────────────────────
# Codes are the collector.outputs._STATE_LABELS values. 'Battery Grid off' =
# 64 is confirmed by the portal's inverter list (status 64 with that
# statusText); 'PV&Battery Grid off' = 192 matches the 12000XP's live
# register 0 while running on PV + battery. An unknown text leaves `state`
# out of the snapshot (never guessed); unknown texts are counted in the summary.
STATUS_CODES: Dict[str, int] = {
    "Standby": 0x00,               # Standby
    "PV Charge": 0x08,             # PV charging battery (backup output off)
    "Battery Grid off": 0x40,      # Battery off-grid
    "PV&Battery Grid off": 0xC0,   # PV + battery off-grid
}


def _status_key(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


_STATUS_BY_KEY = {_status_key(text): code for text, code in STATUS_CODES.items()}


def status_code(text: Any) -> Optional[int]:
    """Return the register-0 code for a portal Status text, or None if unknown."""
    if not isinstance(text, str) or not text.strip():
        return None
    return _STATUS_BY_KEY.get(_status_key(text))


# ── Export column -> cloud runtime field ────────────────────────────────────
# (export columns (first present wins), getInverterRuntime key, factor from
# display units to the runtime's raw units: 0.1 V, 0.01 Hz, W, VA, °C).
# factor None passes the display value through (runtime_to_registers takes
# the *CurrValue keys in whole amps). Columns absent here are not mapped,
# exactly as cloud_http does not map them: vacs/vact/vepss/vepst (split-phase
# garbage), pLoad (reg 170), genPower/eGen* (a garbage counter on the
# 12000XP), inverter/EPS-leg energy, Vbat_Inv, vBusP, BMS status words,
# fault/warning codes and Debug*.
_RUNTIME_COLUMNS: Tuple[Tuple[Tuple[str, ...], str, Optional[float]], ...] = (
    (("vpv1",), "vpv1", 10),
    (("vpv2",), "vpv2", 10),
    (("vpv3",), "vpv3", 10),
    (("vBat(V)", "vBat"), "vBat", 10),
    (("SOC(%)", "SOC", "soc"), "soc", 1),
    (("ppv1",), "ppv1", 1),
    (("ppv2",), "ppv2", 1),
    (("ppv3",), "ppv3", 1),
    (("pCharge",), "pCharge", 1),
    (("pDisCharge",), "pDisCharge", 1),
    (("vacr",), "vacr", 10),
    (("fac",), "fac", 100),
    (("pinv",), "pinv", 1),
    (("prec",), "prec", 1),
    (("vepsr",), "vepsr", 10),
    (("feps",), "feps", 100),
    (("peps",), "peps", 1),
    (("seps",), "seps", 1),
    (("pToGrid",), "pToGrid", 1),
    (("pToUser",), "pToUser", 1),
    (("vBus1",), "vBus1", 10),
    (("vBus2",), "vBus2", 10),
    (("tinner",), "tinner", 1),
    (("tradiator1",), "tradiator1", 1),
    (("tradiator2",), "tradiator2", 1),
    (("tBat",), "tBat", 1),
    (("maxChgCurr",), "maxChgCurrValue", None),
    (("maxDischgCurr",), "maxDischgCurrValue", None),
    (("genVolt",), "genVolt", 10),
    (("genFreq",), "genFreq", 100),
    (("pEpsL1N",), "pEpsL1N", 1),
    (("pEpsL2N",), "pEpsL2N", 1),
)


# ── Errors ──────────────────────────────────────────────────────────────────

class BackfillError(Exception):
    """A condition that stops the backfill (reported without a traceback)."""


class RunStopped(Exception):
    """The process is stopping: abandon the run (raised by a stop-aware sleep)."""


class ExportFormatError(BackfillError):
    """The download is not a readable BIFF .xls export."""


# ── Cell parsing ────────────────────────────────────────────────────────────

def cell_number(value: Any) -> Optional[float]:
    """Return an export cell as a finite float, or None.

    Cells are mixed floats and strings. Handles '23%' (percent suffix),
    '0x4000000' (hex), and treats '', '[0]' (the 12000XP's power factor) and
    any other non-numeric text as missing.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
    elif isinstance(value, str):
        s = value.strip()
        if s.endswith("%"):
            s = s[:-1].strip()
        if not s:
            return None
        try:
            f = float(int(s, 16)) if s[:2].lower() == "0x" else float(s)
        except ValueError:
            return None
    else:
        return None
    return f if math.isfinite(f) else None


def _col(row: Dict[str, Any], *names: str) -> Optional[float]:
    """Numeric value of the first of names present in row."""
    for name in names:
        if name in row:
            return cell_number(row[name])
    return None


def _scaled(value: Optional[float], factor: Optional[float]) -> Optional[float]:
    """Display value -> raw register units, rounded (0.3 x 10 is 2.9999...)."""
    if value is None or factor is None:
        return value
    return float(round(value * factor))


def _sum(values: Iterable[Optional[float]]) -> Optional[float]:
    """Sum of the present values; None when none is present."""
    present = [v for v in values if v is not None]
    return sum(present) if present else None


# ── Workbook parsing ────────────────────────────────────────────────────────

@dataclass
class ExportSheet:
    """One day of the export: the worksheet name and its data rows.

    Each row maps header -> cell value (float, str, or '' when empty); a
    date-typed cell is rendered as 'YYYY-MM-DD HH:MM:SS'.
    """

    day: str
    rows: List[Dict[str, Any]] = field(default_factory=list)


def _disambiguate_headers(headers: List[str]) -> List[str]:
    """Suffix repeated headers ('SOC', 'SOC.1', ...) so no column is lost."""
    seen: set = set()
    unique: List[str] = []
    for header in headers:
        candidate, suffix = header, 1
        while candidate in seen:
            candidate = f"{header}.{suffix}"
            suffix += 1
        seen.add(candidate)
        unique.append(candidate)
    return unique


def _xlrd():
    try:
        import xlrd
    except ImportError as exc:  # the running image predates docker/requirements.txt's xlrd
        raise BackfillError(
            "the backfill needs xlrd (docker/requirements.txt); rebuild the image: "
            "docker compose -f docker/docker-compose.yml build collector"
        ) from exc
    return xlrd


def _cell_value(cell: Any, datemode: int) -> Any:
    xlrd = _xlrd()
    if cell.ctype == xlrd.XL_CELL_DATE:
        from xlrd.xldate import xldate_as_datetime
        return xldate_as_datetime(float(cell.value), datemode).strftime("%Y-%m-%d %H:%M:%S")
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
        return ""
    if cell.ctype == xlrd.XL_CELL_TEXT:
        return str(cell.value).strip()
    return cell.value  # number (float) or boolean (int)


def parse_book(book: Any) -> List[ExportSheet]:
    """Read an xlrd Book (or look-alike) into day sheets, oldest day first.

    Worksheets not named YYYY-MM-DD are ignored, as are rows whose cells are
    all empty. The portal may list the sheets newest first.
    """
    sheets: List[ExportSheet] = []
    for index in range(book.nsheets):
        sheet = book.sheet_by_index(index)
        name = str(sheet.name).strip()
        try:
            date.fromisoformat(name)
        except ValueError:
            logger.warning("Ignoring worksheet %r (not a YYYY-MM-DD day)", name)
            continue
        rows: List[Dict[str, Any]] = []
        if sheet.nrows >= 1:
            headers = _disambiguate_headers(
                [str(sheet.cell_value(0, col)).strip() for col in range(sheet.ncols)]
            )
            for r in range(1, sheet.nrows):
                row = {h: _cell_value(sheet.cell(r, c), book.datemode) for c, h in enumerate(headers)}
                if all(v in ("", None) for v in row.values()):
                    continue
                rows.append(row)
        sheets.append(ExportSheet(day=name, rows=rows))
    sheets.sort(key=lambda s: s.day)
    return sheets


def parse_workbook(content: bytes) -> List[ExportSheet]:
    """Parse the bytes of an EG4 .xls export into day sheets (oldest first)."""
    if not content or not content.startswith(_XLS_MAGIC):
        raise ExportFormatError("not a BIFF .xls workbook (bad signature)")
    xlrd = _xlrd()
    try:
        book = xlrd.open_workbook(file_contents=content)
    except Exception as exc:  # xlrd raises a grab-bag of types on corrupt input
        raise ExportFormatError(f"could not read the .xls export: {type(exc).__name__}: {exc}") from exc
    return parse_book(book)


# ── Timestamps ──────────────────────────────────────────────────────────────

_TIME_FORMATS = ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M")


def parse_local_time(value: Any) -> Optional[datetime]:
    """Parse the export's Time cell ('2026/09/25 00:03:14') as a naive datetime."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _wall_kind(naive: datetime, tz: tzinfo) -> str:
    """'ok', 'ambiguous' (repeated at a DST fall-back) or 'gap' (skipped at spring-forward)."""
    first = naive.replace(tzinfo=tz, fold=0)
    second = naive.replace(tzinfo=tz, fold=1)
    if first.utcoffset() == second.utcoffset():
        return "ok"
    back = first.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
    return "ambiguous" if back == naive else "gap"


@dataclass
class Stats:
    """Counters for one run (and the final summary)."""

    windows: int = 0
    sheets: int = 0
    rows: int = 0
    snapshots: int = 0
    points: int = 0
    skipped_time: int = 0
    skipped_no_battery: int = 0
    skipped_overlap: int = 0
    skipped_outside_gaps: int = 0
    skipped_serial: int = 0
    duplicates: int = 0
    dst_fold: int = 0
    dst_gap: int = 0
    dst_dropped: int = 0
    unknown_status: Counter = field(default_factory=Counter)


def localize_rows(rows: Sequence[Dict[str, Any]], tz: tzinfo,
                  stats: Optional[Stats] = None,
                  drop_ambiguous: bool = False) -> List[Tuple[int, str, Dict[str, Any]]]:
    """Convert one sheet's plant-local Time cells to UTC epoch seconds.

    Returns (utc_seconds, local_text, row) in row order. At a DST fall-back
    the repeated hour is told apart by row order: once the wall time steps
    back inside the ambiguous hour, rows are the second occurrence (fold=1)
    until the ambiguity ends. A wall time inside a spring-forward gap is read
    with the pre-transition offset (fold=0) and counted. A sheet listed
    newest first is reversed.

    Row order cannot tell the passes apart when the sheet lacks rows of one
    of them (the dongle was offline), and neither can the gaps: the other
    instant of a repeated wall time may be a live point's reading. That is
    exactly what a gap-fill looks at, so with drop_ambiguous (gap-fill mode)
    every row with a repeated wall time is dropped (counted in
    stats.dst_dropped): at most an hour a year, never an hour off.
    """
    stats = stats if stats is not None else Stats()
    parsed: List[Tuple[datetime, Dict[str, Any]]] = []
    for row in rows:
        naive = parse_local_time(row.get("Time"))
        if naive is None:
            stats.skipped_time += 1
            continue
        parsed.append((naive, row))
    if len(parsed) >= 2 and parsed[0][0] > parsed[-1][0]:
        parsed.reverse()

    out: List[Tuple[int, str, Dict[str, Any]]] = []
    prev: Optional[datetime] = None
    second_pass = False
    for naive, row in parsed:
        kind = _wall_kind(naive, tz)
        fold = 0
        if kind == "ambiguous":
            if second_pass or (prev is not None and naive < prev):
                second_pass = True
                fold = 1
                stats.dst_fold += 1
        else:
            second_pass = False
            if kind == "gap":
                stats.dst_gap += 1
        prev = naive
        if kind == "ambiguous" and drop_ambiguous:
            stats.dst_dropped += 1
            continue
        ts = int(naive.replace(tzinfo=tz, fold=fold).timestamp())
        out.append((ts, naive.strftime("%Y-%m-%d %H:%M:%S"), row))
    return out


def _day_start_utc(day: date, tz: tzinfo) -> int:
    """UTC epoch seconds of the plant-local midnight starting day."""
    return int(datetime(day.year, day.month, day.day, tzinfo=tz).timestamp())


def _local_day(ts: float, tz: tzinfo) -> date:
    return datetime.fromtimestamp(ts, tz).date()


def _iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def gap_index(ts: float, gaps: Sequence[Tuple[float, float]], margin: float,
              not_after: Optional[float] = None) -> Optional[int]:
    """Index of the gap that holds ts strictly inside its margins, else None.

    A row belongs to gap (start, end) only when start + margin < ts <
    end - margin (and ts < not_after): never on or next to the live points
    that bound the gap.
    """
    if not_after is not None and ts >= not_after:
        return None
    for i, (start, end) in enumerate(gaps):
        if start + margin < ts < end - margin:
            return i
    return None


# ── Row -> registers -> decoded ─────────────────────────────────────────────

def _energy_payload(row: Dict[str, Any], pv_strings: int) -> Dict[str, float]:
    """Build a getInverterEnergyInfo-shaped payload (0.1 kWh) from a row.

    Same semantics as the live transport: total PV (all of the model's PV
    strings) in the yielding slot, and usage = backup-port (EPS) energy plus
    grid-to-user energy (the portal's "Consumption").
    """
    en: Dict[str, float] = {}

    def put(key: str, *columns: str) -> None:
        total = _sum(_col(row, c) for c in columns)
        if total is not None:
            en[key] = float(round(total * 10))

    put("todayYielding", *[f"ePv{i}Day" for i in range(1, pv_strings + 1)])
    put("todayCharging", "eChgDay")
    put("todayDischarging", "eDisChgDay")
    put("todayExport", "eToGridDay")
    put("todayImport", "eToUserDay")
    put("todayUsage", "eEpsDay", "eToUserDay")
    put("totalYielding", *[f"ePv{i}All" for i in range(1, pv_strings + 1)])
    put("totalCharging", "eChgAll")
    put("totalDischarging", "eDisChgAll")
    put("totalExport", "eToGridAll")
    put("totalImport", "eToUserAll")
    put("totalUsage", "eEpsAll", "eToUserAll")
    return en


def _cell_volts(value: Optional[float]) -> Optional[float]:
    """A cell voltage in V (the export shows V; accept mV), None if 0/implausible."""
    if not value:
        return None
    if 1.0 <= value <= 5.0:
        return value
    if 1000 <= value <= 5000:
        return value / 1000
    return None


def row_to_registers(row: Dict[str, Any], model: str) -> Tuple[Dict[int, int], FrozenSet[str], Optional[str]]:
    """Map one export row to the raw input registers the live transport would emit.

    The row's display values are turned back into the cloud runtime/energy
    payloads' raw units and passed through cloud_http's own
    runtime_to_registers / energy_to_registers, so register numbers, the
    fields left out and the model quirks (tinner/tBat 0, pf '[0]', 2-MPPT
    PV3, EPS legs) are exactly the live ones. Added from the export: the
    battery current (BatCurrent, A, signed) and, only when non-zero, BMS cell
    voltages/temperatures, cycle count and SOH.

    Returns (registers, fields to drop after decoding, unknown status text).
    """
    quirks = _model_quirks(model)
    rt: Dict[str, Any] = {}
    for columns, key, factor in _RUNTIME_COLUMNS:
        value = _scaled(_col(row, *columns), factor)
        if value is not None:
            rt[key] = value
    if "pf" in row:
        rt["pf"] = row["pf"]  # runtime_to_registers drops '[0]'
    if "pEpsL1N" in rt or "pEpsL2N" in rt:
        rt["haspEpsLNValue"] = True

    text = row.get("Status")
    code = status_code(text)
    if code is not None:
        rt["status"] = code
    unknown = text.strip() if isinstance(text, str) and text.strip() and code is None else None

    regs = runtime_to_registers(rt, model)
    regs.update(energy_to_registers(_energy_payload(row, quirks["pv_strings"])))

    # Register 98: bank current, signed 0.1 A (negative = discharging).
    current = _col(row, "BatCurrent")
    if current is not None:
        _put16(regs, 98, round(current * 10), signed=True)

    # BMS cell data and cycles: a 0 means "not reported" in the export.
    for reg, column in ((101, "MaxCellVoltage"), (102, "MinCellVoltage")):
        volts = _cell_volts(_col(row, column))
        if volts is not None:
            _put16(regs, reg, round(volts * 1000))
    for reg, column in ((103, "MaxCellTemp"), (104, "MinCellTemp")):
        temp = _col(row, column)
        if temp and -40 <= temp <= 100:
            _put16(regs, reg, round(temp * 10), signed=True)
    cycles = _col(row, "CycleCnt")
    if cycles and cycles > 0:
        _put16(regs, 106, round(cycles))

    # SOH shares register 5 with SOC (high byte). Without a real value the
    # decoded 0 is dropped, as the live transport does.
    suppressed: FrozenSet[str] = frozenset({"soh"})
    soh = _col(row, "SOH")
    if 5 in regs and soh is not None and 0 < soh <= 100:
        regs[5] = (int(round(soh)) << 8) | (regs[5] & 0xFF)
        suppressed = frozenset()
    return regs, suppressed, unknown


@dataclass
class MapContext:
    """What turns registers into the exact names/units the live writer stores."""

    model: str
    driver: ModelDriver
    temperature_unit: str
    tz: tzinfo


def decode_registers_like_live(regs: Dict[int, int], suppressed: Iterable[str],
                               ctx: MapContext) -> Optional[dict]:
    """Decode -> drop suppressed -> clamp -> display temperature unit.

    The same steps as PassiveCollector._write_once + Outputs.write for the
    InfluxDB backend. None when the battery voltage is missing (the live
    writer skips such snapshots too).
    """
    if 4 not in regs:
        return None
    decoded = ctx.driver.decode(regs)
    for name in suppressed:
        decoded.pop(name, None)
    clamp_values(decoded, ctx.driver)
    return convert_temperatures(decoded, ctx.temperature_unit)


@dataclass
class RowPoint:
    """One export row, decoded, at its UTC timestamp."""

    ts: int                # UTC epoch seconds
    local: str             # plant-local wall time
    decoded: dict

    def lines(self) -> List[str]:
        # No source_meta: backfilled snapshots never get a luxmon_cloud point.
        return influx_lines(self.decoded, self.ts * 1_000_000_000)


def _row_serial(row: Dict[str, Any]) -> str:
    value = row.get("Serial number", "")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value or "").strip()


def sheet_points(sheet: ExportSheet, ctx: MapContext, stats: Optional[Stats] = None,
                 serial: Optional[str] = None,
                 drop_ambiguous: bool = False) -> List[RowPoint]:
    """Decode every usable row of a sheet, in time order (see localize_rows for drop_ambiguous)."""
    stats = stats if stats is not None else Stats()
    points: List[RowPoint] = []
    for ts, local, row in localize_rows(sheet.rows, ctx.tz, stats, drop_ambiguous):
        row_serial = _row_serial(row)
        if serial and row_serial and row_serial != serial:
            stats.skipped_serial += 1
            continue
        regs, suppressed, unknown = row_to_registers(row, ctx.model)
        if unknown:
            stats.unknown_status[unknown] += 1
        decoded = decode_registers_like_live(regs, suppressed, ctx)
        if decoded is None:
            stats.skipped_no_battery += 1
            continue
        points.append(RowPoint(ts=ts, local=local, decoded=decoded))
    return points


def _units(points: Iterable[RowPoint]) -> Dict[str, str]:
    units: Dict[str, str] = {}
    for p in points:
        for name, info in p.decoded.items():
            if isinstance(info, dict) and info.get("value") is not None:
                units.setdefault(name, str(info.get("unit", "") or ""))
    return units


# ── InfluxDB ────────────────────────────────────────────────────────────────

def _flux_str(value: str) -> str:
    return json.dumps(value)


def _flux_time(ts: float) -> str:
    return _iso_utc(ts)


class InfluxTarget:
    """The collector's InfluxDB: a few read-only queries and batched writes.

    Uses the v2 API (LUX_INFLUX_TOKEN), or the 1.8+ compatibility API with
    username:password and database/retention as the bucket.
    """

    def __init__(self, out: OutputConfig, client: Any = None):
        if out.influx_token:
            token, bucket = out.influx_token, out.influx_bucket
        else:
            token = f"{out.influx_username}:{out.influx_password}" if out.influx_username else ""
            bucket = f"{out.influx_database}/{out.influx_retention}"
        self.bucket = bucket
        self.org = out.influx_org
        self.url = out.influx_url
        if client is None:
            from influxdb_client import InfluxDBClient
            client = InfluxDBClient(url=out.influx_url, token=token, org=out.influx_org, timeout=60_000)
        self._client = client
        self._write_api: Any = None

    def _query(self, flux: str) -> list:
        tables = self._client.query_api().query(flux, org=self.org)
        return [record for table in tables for record in table.records]

    def _from(self) -> str:
        return f"from(bucket: {_flux_str(self.bucket)})"

    def recorded_live_start(self) -> Optional[float]:
        """The first-live-point time recorded by an earlier backfill run, if any."""
        records = self._query(
            f'{self._from()} |> range(start: 0) '
            f'|> filter(fn: (r) => r._measurement == "{BACKFILL_MEASUREMENT}" and r._field == "live_start")'
        )
        values = [float(r.get_value()) for r in records if isinstance(r.get_value(), (int, float))]
        return min(values) if values else None

    def first_soc_time(self) -> Optional[float]:
        """Time of the earliest luxmon_register soc point (UTC epoch seconds)."""
        records = self._query(
            f'{self._from()} |> range(start: 0) '
            f'|> filter(fn: (r) => r._measurement == "luxmon_register" and r.name == "soc" and r._field == "value") '
            f'|> first() |> group() |> sort(columns: ["_time"]) |> limit(n: 1)'
        )
        if not records:
            return None
        return records[0].get_time().timestamp()

    def live_units(self, since: float) -> Dict[str, str]:
        """name -> unit tag of the newest live luxmon_register point since `since`."""
        records = self._query(
            f'{self._from()} |> range(start: {_flux_time(since)}) '
            f'|> filter(fn: (r) => r._measurement == "luxmon_register" and r._field == "value") '
            f'|> last()'
        )
        newest: Dict[str, Tuple[float, str]] = {}
        for r in records:
            name = r.values.get("name")
            if not name:
                continue
            t = r.get_time().timestamp()
            if name not in newest or t > newest[name][0]:
                newest[name] = (t, str(r.values.get("unit") or ""))
        return {name: unit for name, (_, unit) in newest.items()}

    def unit_sets(self, start: float, stop: float) -> Dict[str, set]:
        """name -> every unit tag of the live luxmon_register points in [start, stop)."""
        if stop <= start:
            return {}
        records = self._query(
            f'{self._from()} |> range(start: {_flux_time(start)}, stop: {_flux_time(stop)}) '
            f'|> filter(fn: (r) => r._measurement == "luxmon_register" and r._field == "value") '
            f'|> last()'
        )
        out: Dict[str, set] = {}
        for r in records:
            name = r.values.get("name")
            if name:
                out.setdefault(name, set()).add(str(r.values.get("unit") or ""))
        return out

    def reading_time(self, ts: float) -> Optional[float]:
        """When the reading of the live point written at ts was uploaded (UTC epoch seconds).

        A cloud_http point is written when it is polled, data_age_s after
        EG4 received the upload (serverTime); its luxmon_cloud point shares
        the timestamp. None when there is no luxmon_cloud point at ts.
        """
        records = self._query(
            f'{self._from()} |> range(start: {_flux_time(ts - 1)}, stop: {_flux_time(ts + 1)}) '
            f'|> filter(fn: (r) => r._measurement == "luxmon_cloud" and '
            f'(r._field == "server_time" or r._field == "data_age_s"))'
        )
        fields: Dict[str, Tuple[float, Any]] = {}
        for r in records:
            name = r.values.get("_field")
            t = r.get_time().timestamp()
            if name and (name not in fields or abs(t - ts) < abs(fields[name][0] - ts)):
                fields[name] = (t, r.get_value())
        if "server_time" in fields:
            server = _parse_server_time(fields["server_time"][1])
            if server is not None:
                return server
        if "data_age_s" in fields:
            t, age = fields["data_age_s"]
            if isinstance(age, (int, float)) and not isinstance(age, bool) and math.isfinite(age):
                return t - float(age)
        return None

    def register_points(self, start: float, stop: float) -> Dict[str, List[Tuple[float, float, str]]]:
        """name -> [(utc seconds, value, unit)] of luxmon_register in [start, stop), time-sorted."""
        records = self._query(
            f'{self._from()} |> range(start: {_flux_time(start)}, stop: {_flux_time(stop)}) '
            f'|> filter(fn: (r) => r._measurement == "luxmon_register" and r._field == "value")'
        )
        out: Dict[str, List[Tuple[float, float, str]]] = {}
        for r in records:
            name = r.values.get("name")
            value = r.get_value()
            if not name or not isinstance(value, (int, float)):
                continue
            out.setdefault(name, []).append((r.get_time().timestamp(), float(value), str(r.values.get("unit") or "")))
        for series in out.values():
            series.sort()
        return out

    def _soc_filter(self) -> str:
        return '|> filter(fn: (r) => r._measurement == "luxmon_register" and r.name == "soc" and r._field == "value")'

    def soc_times(self, start: float, stop: float) -> List[float]:
        """Sorted UTC epoch seconds of the luxmon_register soc points in [start, stop)."""
        if stop <= start:
            return []
        records = self._query(
            f'{self._from()} |> range(start: {_flux_time(start)}, stop: {_flux_time(stop)}) '
            f'{self._soc_filter()} |> keep(columns: ["_time", "_value"])'
        )
        return sorted(r.get_time().timestamp() for r in records)

    def last_soc_before(self, before: float, since: float) -> Optional[float]:
        """Time of the last luxmon_register soc point in [since, before), if any."""
        if before <= since:
            return None
        records = self._query(
            f'{self._from()} |> range(start: {_flux_time(since)}, stop: {_flux_time(before)}) '
            f'{self._soc_filter()} |> last()'
        )
        times = [r.get_time().timestamp() for r in records]
        return max(times) if times else None

    def gapfill_records(self, since: float) -> List[Dict[str, Any]]:
        """The gap-fill attempt records (luxmon_backfill, tag mode=gapfill) since `since`.

        One dict of fields per record (run_id and timestamp), in no particular order.
        """
        records = self._query(
            f'{self._from()} |> range(start: {_flux_time(since)}) '
            f'|> filter(fn: (r) => r._measurement == "{BACKFILL_MEASUREMENT}" and r.mode == "gapfill")'
        )
        merged: Dict[Tuple[str, float], Dict[str, Any]] = {}
        for r in records:
            name = r.values.get("_field")
            if not name:
                continue
            key = (str(r.values.get("run_id") or ""), r.get_time().timestamp())
            merged.setdefault(key, {})[name] = r.get_value()
        return list(merged.values())

    def write(self, lines: List[str]) -> None:
        """Write one batch synchronously; any error is raised to the caller."""
        from influxdb_client import WritePrecision
        from influxdb_client.client.write_api import SYNCHRONOUS
        if self._write_api is None:
            self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
        self._write_api.write(bucket=self.bucket, org=self.org, record=lines, write_precision=WritePrecision.NS)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


# ── Backfill core ───────────────────────────────────────────────────────────

class Backfiller:
    """Turns day sheets into InfluxDB points and writes them (unless dry_run).

    With `gaps` (gap-fill mode, collector.gapfill) only rows strictly inside
    one of those (start, end) intervals are kept (see gap_index: at least
    gap_margin from both ends and before not_after), and only those rows are
    exempt from the cutoff; every other row is dropped.
    """

    def __init__(self, ctx: MapContext, target: Optional[InfluxTarget] = None,
                 cutoff: Optional[float] = None, live_start: Optional[float] = None,
                 live_units: Optional[Dict[str, str]] = None, serial: Optional[str] = None,
                 dry_run: bool = True, run_id: Optional[str] = None, samples: int = 0,
                 batch_lines: int = BATCH_LINES, out: Callable[[str], None] = print,
                 gaps: Optional[Sequence[Tuple[float, float]]] = None, gap_margin: float = 60.0,
                 not_after: Optional[float] = None):
        if not dry_run and target is None:
            raise ValueError("a write run needs an InfluxDB target")
        self.ctx = ctx
        self.gaps = list(gaps) if gaps is not None else None
        self.gap_margin = float(gap_margin)
        self.not_after = not_after
        self.target = target
        self.cutoff = cutoff
        self.live_start = live_start
        self.live_units = live_units
        self.serial = serial
        self.dry_run = dry_run
        self.run_id = run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
        self.samples = samples
        self.batch_lines = batch_lines
        self.stats = Stats()
        self._out = out
        self._checked_units: Dict[str, str] = {}

    def window_points(self, sheets: Sequence[ExportSheet], start: Optional[date] = None,
                      end: Optional[date] = None) -> Tuple[int, List[RowPoint]]:
        """Decode the sheets inside [start, end]: (data rows, points before the cutoff)."""
        rows = 0
        by_ts: Dict[int, RowPoint] = {}
        for sheet in sheets:
            day = date.fromisoformat(sheet.day)
            if (start is not None and day < start) or (end is not None and day > end):
                logger.debug("Ignoring sheet %s outside %s..%s", sheet.day, start, end)
                continue
            self.stats.sheets += 1
            rows += len(sheet.rows)
            # Gap-fill mode: a repeated DST wall time cannot be placed safely (localize_rows).
            for p in sheet_points(sheet, self.ctx, self.stats, self.serial, drop_ambiguous=self.gaps is not None):
                if p.ts in by_ts:
                    self.stats.duplicates += 1  # same UTC second: the later row wins
                by_ts[p.ts] = p
        points = [by_ts[t] for t in sorted(by_ts)]
        if self.gaps is not None:
            kept = [p for p in points if self.gap_of(p.ts) is not None]
            self.stats.skipped_outside_gaps += len(points) - len(kept)
            points = kept
        if self.cutoff is not None:
            # A row inside a gap fills a hole in the live data, so it is the
            # only kind of row that may lie after the cutoff.
            kept = [p for p in points if p.ts < self.cutoff or self.gap_of(p.ts) is not None]
            self.stats.skipped_overlap += len(points) - len(kept)
            points = kept
        self.stats.rows += rows
        return rows, points

    def gap_of(self, ts: float) -> Optional[int]:
        """Index of the gap (gap-fill mode) that holds ts, else None."""
        if self.gaps is None:
            return None
        return gap_index(ts, self.gaps, self.gap_margin, self.not_after)

    def check_units(self, points: Sequence[RowPoint]) -> None:
        """Refuse to write a name under a different unit tag than live data uses."""
        if not self.live_units:
            return
        conflicts = []
        for name, unit in _units(points).items():
            if name in self._checked_units:
                continue
            self._checked_units[name] = unit
            live = self.live_units.get(name)
            if live is None:
                logger.info("%s is not in the live data; the backfill adds it for the past only", name)
            elif live != unit:
                conflicts.append(f"{name}: backfill {unit!r} vs live {live!r}")
        if conflicts:
            raise BackfillError(
                "unit tags differ from the live data (this would create duplicate series): "
                + "; ".join(conflicts)
                + ". Check the temperature_unit setting / --temp-unit."
            )

    def process(self, sheets: Sequence[ExportSheet], start: Optional[date] = None,
                end: Optional[date] = None, label: Optional[str] = None) -> Tuple[int, int, int]:
        """Decode, check and write one window. Returns (rows, snapshots, points)."""
        if label is None:
            days = [s.day for s in sheets]
            label = f"{start or (min(days) if days else '?')}..{end or (max(days) if days else '?')}"
        self.stats.windows += 1
        rows, points = self.window_points(sheets, start, end)
        if self.samples and points:
            self.print_samples(points, label)
        self.check_units(points)

        prov_ts = self._provenance_ts(start, points)
        if not self.dry_run and points:
            # Record live_start before any data: if the run stops part-way, the
            # next one still finds it instead of taking the earliest (by then
            # backfilled) soc point as the first live point.
            self._write_provenance(prov_ts, label)
        written = self.write_points(points)
        if not self.dry_run:
            self._write_provenance(prov_ts, label, rows=rows, snapshots=len(points), points=written)
        self.stats.snapshots += len(points)
        self.stats.points += written
        logger.info(
            "%s %s: %d rows -> %d snapshots, %d points%s",
            "Checked" if self.dry_run else "Wrote", label, rows, len(points), written,
            " (dry run, nothing written)" if self.dry_run else "",
        )
        return rows, len(points), written

    def write_points(self, points: Sequence[RowPoint]) -> int:
        """Write the points' lines in batches (count only in a dry run). Returns the line count."""
        written = 0
        batch: List[str] = []
        for p in points:
            batch.extend(p.lines())
            if len(batch) >= self.batch_lines:
                written += self._flush(batch)
                batch = []
        if batch:
            written += self._flush(batch)
        return written

    def _flush(self, lines: List[str]) -> int:
        if not self.dry_run:
            self.target.write(lines)
        return len(lines)

    def _provenance_ts(self, start: Optional[date], window_points: Sequence[RowPoint]) -> int:
        if start is not None:
            return _day_start_utc(start, self.ctx.tz)
        if window_points:
            return window_points[0].ts
        return int(time.time())

    def _write_provenance(self, ts: int, label: str, **counts: int) -> None:
        """Write this run's luxmon_backfill point for a window.

        Written before the window's data (live_start, cutoff) and again after
        it with the counts (rows, snapshots, points). Both share the series
        and timestamp, so InfluxDB merges them into one point per window.
        """
        fields: Dict[str, Any] = dict(counts)
        fields["window"] = label
        fields["source"] = "eg4_export"
        if self.live_start is not None:
            fields["live_start"] = self.live_start
        if self.cutoff is not None:
            fields["cutoff"] = float(self.cutoff)
        line = _to_line(BACKFILL_MEASUREMENT, {"run_id": self.run_id}, fields, ts * 1_000_000_000)
        self.target.write([line])

    def print_samples(self, points: Sequence[RowPoint], label: str) -> None:
        """Print the first row, the peak-PV row and the last row of a window."""
        def pv(p: RowPoint) -> float:
            return sum(float(p.decoded.get(k, {}).get("value") or 0) for k in ("pv1_power", "pv2_power", "pv3_power"))

        peak = max(range(len(points)), key=lambda i: pv(points[i]))
        picks = sorted({0, peak, len(points) - 1})[: self.samples]
        self._out(f"── Sample rows ({label}) ──")
        for i in picks:
            p = points[i]
            self._out(f"{p.local} {self.ctx.tz} = {_iso_utc(p.ts)[:19]}Z")
            parts = [f"{name}={_fmt_value(info['value'])}{(' ' + info['unit']) if info.get('unit') else ''}"
                     for name, info in p.decoded.items() if isinstance(info, dict) and "value" in info]
            self._out("  " + ", ".join(parts))


def _fmt_value(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{f:.6g}"


def iter_windows(start: date, end: date, size: int = MAX_WINDOW_DAYS) -> Iterator[Tuple[date, date]]:
    """Consecutive [start, end] windows of at most `size` days."""
    day = start
    while day <= end:
        last = min(end, day + timedelta(days=size - 1))
        yield day, last
        day = last + timedelta(days=1)


# ── --compare-live ──────────────────────────────────────────────────────────

@dataclass
class FieldComparison:
    name: str
    unit: str
    live_unit: str
    exported: int
    matched: int
    mean_abs: Optional[float]
    max_abs: Optional[float]


def compare_points(points: Sequence[RowPoint], live: Dict[str, List[Tuple[float, float, str]]],
                   window: float = MATCH_WINDOW_SEC) -> List[FieldComparison]:
    """Per field: match each export row to the nearest live point within ±window s."""
    units = _units(points)
    result: List[FieldComparison] = []
    for name in sorted(set(units) | set(live)):
        series = live.get(name, [])
        times = [t for t, _, _ in series]
        exported = matched = 0
        total = 0.0
        worst: Optional[float] = None
        live_units: set = set()
        for p in points:
            info = p.decoded.get(name)
            if not isinstance(info, dict) or info.get("value") is None:
                continue
            exported += 1
            i = bisect_left(times, p.ts)
            best: Optional[int] = None
            for j in (i - 1, i):
                if 0 <= j < len(times) and abs(times[j] - p.ts) <= window:
                    if best is None or abs(times[j] - p.ts) < abs(times[best] - p.ts):
                        best = j
            if best is None:
                continue
            diff = abs(float(info["value"]) - series[best][1])
            live_units.add(series[best][2])
            matched += 1
            total += diff
            worst = diff if worst is None else max(worst, diff)
        if not live_units and series:
            live_units = {series[0][2]}
        result.append(FieldComparison(
            name=name,
            unit=units.get(name, "-"),
            live_unit="/".join(sorted(live_units)) if live_units else "-",
            exported=exported,
            matched=matched,
            mean_abs=(total / matched) if matched else None,
            max_abs=worst,
        ))
    return result


def format_comparison(rows: Sequence[FieldComparison]) -> List[str]:
    def num(v: Optional[float]) -> str:
        return "-" if v is None else f"{v:.4g}"

    header = f"{'field':<26} {'unit':<5} {'live unit':<9} {'export':>6} {'matched':>7} {'mean|diff|':>10} {'max|diff|':>10}  note"
    lines = [header, "-" * len(header)]
    for r in rows:
        if r.exported == 0:
            note = "live only (not in the export mapping)"
        elif r.live_unit == "-":
            note = "export only (not in live data)"
        elif r.unit != r.live_unit:
            note = "UNIT MISMATCH"
        elif r.matched == 0:
            note = "no live point within ±3 min"
        else:
            note = ""
        lines.append(
            f"{r.name:<26} {r.unit:<5} {r.live_unit:<9} {r.exported:>6} {r.matched:>7} "
            f"{num(r.mean_abs):>10} {num(r.max_abs):>10}  {note}"
        )
    return lines


# ── EG4 export client ───────────────────────────────────────────────────────

def export_get_allowed(path: str, params: Optional[Dict[str, str]]) -> bool:
    """True only for the data-export GET with at most an endDateText parameter."""
    if not any(p.fullmatch(path) for p in _EXPORT_GET_ALLOWLIST):
        return False
    return set(params or {}) <= _EXPORT_GET_PARAMS


def classify_export(resp: Any) -> Tuple[str, Optional[bytes], str]:
    """Classify an export response as ok / session / rate_limited / transient / api_error."""
    status = resp.status_code
    if 300 <= status < 400 or status == 401:
        return "session", None, f"HTTP {status}"
    if status == 429:
        return "rate_limited", None, f"HTTP {status}"
    if status >= 500:
        return "transient", None, f"HTTP {status}"
    if status >= 400:
        return "api_error", None, f"HTTP {status}"
    body = resp.content or b""
    ctype = str((getattr(resp, "headers", None) or {}).get("Content-Type", "")).lower()
    if body.startswith(_XLS_MAGIC):
        return "ok", body, ""
    if not body:
        return "transient", None, "empty response"
    head = body.lstrip()[:1]
    if "html" in ctype or head == b"<":
        # An expired session returns HTTP 200 with the HTML login page.
        return "session", None, "HTML response (login page?)"
    if "json" in ctype or head == b"{":
        try:
            doc = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            msg = str(doc.get("msg") or doc.get("message") or "")
            if any(marker in msg.upper() for marker in _TRANSIENT_MARKERS):
                return "transient", None, msg
            if not msg or any(marker in msg.lower() for marker in _SESSION_MARKERS):
                return "session", None, msg or "JSON response without data"
            return "api_error", None, msg
    return "api_error", None, f"not an .xls workbook (Content-Type {ctype or 'none'}, {len(body)} bytes)"


class ExportClient(CloudHttpTransport):
    """cloud_http's login/session handling plus the read-only data-export GET.

    Never started as a transport: only _login (the login POST), the runtime
    POST (for the time-zone check) and export() are used. Requests are paced
    request_delay apart; transient failures are retried with backoff; an
    HTML/401/redirect response triggers one re-login; a rejected login raises
    CloudAuthError at once (never retried: account lockout risk).
    """

    def __init__(self, base_url: str, username: str, password: str, inverter_serial: str,
                 model: str = "", request_delay: float = DEFAULT_REQUEST_DELAY,
                 session_factory: Callable[[], Any] = requests.Session,
                 clock: Callable[[], float] = time.time,
                 rand: Callable[[], float] = random.random,
                 sleep: Callable[[float], None] = time.sleep):
        super().__init__(
            lambda frame: None, base_url=base_url, username=username, password=password,
            inverter_serial=inverter_serial, model=model,
            session_factory=session_factory, clock=clock, rand=rand,
        )
        self.request_delay = max(0.0, float(request_delay))
        self._sleep = sleep
        self._last_request_at: Optional[float] = None
        self.downloads = 0
        self.bytes_downloaded = 0

    # Requests
    def _pace(self) -> None:
        if self._last_request_at is not None:
            wait = self.request_delay - (self._clock() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._clock()

    def _ensure_login(self) -> None:
        now = self._clock()
        if self._session is None or not self._logged_in_at or now - self._logged_in_at >= SESSION_MAX_AGE_SEC:
            self._login()

    def _get(self, path: str, params: Optional[Dict[str, str]] = None):
        """GET an allow-listed export path (redirects disabled)."""
        if not export_get_allowed(path, params):
            raise ValueError(f"refusing to call non-allow-listed EG4 cloud path {path!r}")
        if self._session is None:
            self._session = self._session_factory()
        self._pace()
        return self._session.get(
            self.base_url + path,
            params=params,
            headers=dict(_EXPORT_HEADERS),
            allow_redirects=False,
            timeout=EXPORT_TIMEOUT,
        )

    def _fetch_export(self, path: str, params: Dict[str, str], relogin: bool = True) -> bytes:
        self._ensure_login()
        resp = self._get(path, params)
        kind, body, msg = classify_export(resp)
        if kind == "ok":
            return body
        if kind == "session":
            if relogin:
                self._relogins += 1
                logger.info("EG4 cloud session rejected (%s); logging in again", self._redact(msg))
                self._login()
                return self._fetch_export(path, params, relogin=False)
            raise CloudSessionExpired(self._redact(f"export: session rejected after re-login ({msg})"))
        if kind == "rate_limited":
            raise CloudRateLimited(self._redact(f"export: {msg}"))
        if kind == "transient":
            raise CloudTransientError(self._redact(f"export: {msg}"))
        raise CloudApiError(self._redact(f"export: {msg}"))

    def _with_retries(self, fn: Callable[[], Any], what: str) -> Any:
        attempt = 0
        while True:
            attempt += 1
            try:
                return fn()
            except CloudAuthError:
                raise
            except (CloudTransientError, CloudSessionExpired, requests.RequestException) as exc:
                if attempt >= MAX_ATTEMPTS:
                    raise CloudTransientError(self._redact(
                        f"{what} failed after {attempt} attempts: {type(exc).__name__}: {exc}"))
                base = RATE_LIMIT_BASE_SEC if isinstance(exc, CloudRateLimited) else RETRY_BASE_SEC
                delay = base * 2 ** (attempt - 1) * (1 + 0.1 * (2 * self._rand() - 1))
                logger.warning("EG4 %s failed (attempt %d/%d): %s; retrying in %.0fs", what, attempt,
                               MAX_ATTEMPTS, self._redact(f"{type(exc).__name__}: {exc}"), delay)
                self._sleep(delay)

    def export(self, start: date, end: date) -> bytes:
        """Download the .xls export for the plant-local days start..end (inclusive)."""
        days = (end - start).days + 1
        if days < 1 or days > MAX_WINDOW_DAYS:
            raise ValueError(f"export window must be 1-{MAX_WINDOW_DAYS} days, got {days}")
        path = EXPORT_PATH.format(serial=self.inverter_serial, day=start.isoformat())
        params = {"endDateText": end.isoformat()}
        content = self._with_retries(lambda: self._fetch_export(path, params), f"export {start}..{end}")
        self.downloads += 1
        self.bytes_downloaded += len(content)
        return content

    def device_clock(self) -> Optional[Tuple[float, float]]:
        """(upload time as UTC epoch, inverter clock UTC offset in seconds), if known.

        From the latest runtime upload: serverTime is UTC, deviceTime is the
        inverter's own clock (the export's Time column).
        """
        def fetch() -> dict:
            self._ensure_login()
            return self._call(RUNTIME_PATH)

        rt = self._with_retries(fetch, "runtime")
        server = _parse_server_time(rt.get("serverTime"))
        device = _parse_server_time(rt.get("deviceTime"))
        if server is None or device is None:
            return None
        return server, device - server


def find_first_day(has_rows: Callable[[date], bool], upper: date,
                   floor: date = FIND_START_FLOOR) -> Optional[date]:
    """Binary-search the first day with export rows in [floor, upper].

    Assumes days after the first data day have rows; a long outage can make
    the search land after it (the earlier data can still be backfilled with
    --start). Returns None when `upper` itself has no rows.
    """
    if not has_rows(upper):
        return None
    if has_rows(floor):
        logger.warning("Export rows exist on %s already (the search floor); starting there", floor)
        return floor
    lo, hi = floor, upper  # lo: no rows, hi: rows
    while (hi - lo).days > 1:
        mid = lo + timedelta(days=(hi - lo).days // 2)
        if has_rows(mid):
            hi = mid
        else:
            lo = mid
    return hi


def download_sheets(client: ExportClient, start: date, end: date,
                    save: Optional[Callable[[bytes, date, date], None]] = None) -> List[ExportSheet]:
    """Download and parse the export for the plant-local days start..end (inclusive)."""
    content = client.export(start, end)
    if save is not None:
        save(content, start, end)
    sheets = parse_workbook(content)
    got = {s.day for s in sheets}
    missing = [d for d in (start + timedelta(days=i) for i in range((end - start).days + 1))
               if d.isoformat() not in got]
    if missing:
        logger.info("No worksheet for %d of %d days (%s%s)", len(missing), (end - start).days + 1,
                    ", ".join(d.isoformat() for d in missing[:5]), " ..." if len(missing) > 5 else "")
    return sheets


def verify_timezone(client: ExportClient, tz: tzinfo, strict: bool) -> None:
    """Compare the inverter clock's UTC offset with the plant time zone in use.

    strict (a write run without an explicit --tz): a mismatch or a clock that
    cannot be read raises BackfillError, since a wrong zone shifts every
    point written. Otherwise both are logged as warnings. A rejected login
    (CloudAuthError) and a stop request (RunStopped) are always raised.
    """
    try:
        clock = client.device_clock()
    except (CloudAuthError, RunStopped):
        raise
    except Exception as exc:
        detail = client._redact(f"{type(exc).__name__}: {exc}")
        reason, clock = f"could not read the inverter clock ({detail})", None
    else:
        reason = "the portal did not report deviceTime/serverTime"
    if clock is None:
        msg = f"{reason}; the plant time zone {tz} is not verified"
        if strict:
            raise BackfillError(f"{msg}: pass --tz with the inverter's time zone")
        logger.warning(msg)
        return
    at, offset = clock
    expected = datetime.fromtimestamp(at, tz).utcoffset().total_seconds()
    logger.info("Inverter clock is UTC%+.2fh; %s was UTC%+.2fh at that upload",
                offset / 3600, tz, expected / 3600)
    if abs(offset - expected) > TZ_CHECK_TOLERANCE_SEC:
        msg = (f"the inverter clock (UTC{offset / 3600:+.2f}h) does not match the plant time zone "
               f"{tz} (UTC{expected / 3600:+.2f}h); pass --tz with the inverter's time zone")
        if strict:
            raise BackfillError(msg)
        logger.warning(msg)


# ── Configuration ───────────────────────────────────────────────────────────

@dataclass
class Settings:
    cfg: CollectorConfig
    model: str
    temperature_unit: str
    tz: tzinfo
    tz_explicit: bool
    db_ok: bool
    db_temperature_unit: Optional[str] = None
    # Flags a write run needs because a value is a fallback guess (MariaDB not reachable).
    fallbacks: Tuple[str, ...] = ()


def _db_reachable(cfg: CollectorConfig) -> bool:
    """Probe MariaDB with a short timeout (read-only: connect and close)."""
    try:
        import pymysql
        conn = pymysql.connect(
            host=cfg.outputs.mariadb_host,
            port=cfg.outputs.mariadb_port,
            user=cfg.outputs.mariadb_user,
            password=cfg.outputs.mariadb_password,
            database=cfg.outputs.mariadb_database,
            connect_timeout=5,
        )
        conn.close()
        return True
    except Exception as exc:
        logger.debug("MariaDB probe failed: %s", type(exc).__name__)
        return False


def load_settings(args: argparse.Namespace) -> Settings:
    """Collector config: env, then the DB-authoritative settings when reachable.

    Nothing is written to MariaDB (no env seeding). Without the DB, --model,
    --temp-unit and --tz (or their environment/default fallbacks) are used; a
    model or temperature unit that is only a fallback is listed in
    Settings.fallbacks and refused for write runs. The environment is no
    stand-in for the DB: compose defaults LUX_INVERTER_MODEL to eg4_6000xp,
    and the collector uses the DB value whenever the DB is up.
    """
    cfg = config_from_env()
    db_ok = _db_reachable(cfg)
    db_model = db_tz = None
    db_temp: Optional[str] = None
    if db_ok:
        _load_db_serials(cfg)
        _load_db_output_settings(cfg)
        db_model = _load_db_setting("inverter_model", cfg)
        db_tz = _load_db_setting("timezone", cfg)
        db_temp = cfg.outputs.temperature_unit
    else:
        logger.warning(
            "MariaDB (%s:%s) is not reachable: using --model/--temp-unit/--tz or their fallbacks",
            cfg.outputs.mariadb_host, cfg.outputs.mariadb_port,
        )

    fallbacks: List[str] = []
    model = args.model or db_model
    if not model:
        model = _env_or("LUX_INVERTER_MODEL") or DEFAULT_MODEL
        fallbacks.append("--model")
        logger.warning("Inverter model not read from the DB; using %s (pass --model)", model)

    if args.temp_unit:
        temp_unit = args.temp_unit
    elif db_temp:
        temp_unit = db_temp
    else:
        # What the collector ends up with: env (seeded into the DB) else the DB default.
        temp_unit = _env_or("LUX_TEMPERATURE_UNIT") or DEFAULTS["temperature_unit"]
        fallbacks.append("--temp-unit")
        logger.warning("Temperature unit not read from the DB; using %s (pass --temp-unit)", temp_unit)
    temp_unit = temp_unit.lower()
    if temp_unit not in ("celsius", "fahrenheit"):
        raise BackfillError(f"unknown temperature unit {temp_unit!r}")

    # The `timezone` setting only drives schedules: nothing ties it to the
    # inverter clock, and a missing row reads as the default. Write runs check
    # it against the inverter clock (portal) or need --tz (--from-xls).
    tz_name = args.tz or db_tz or DEFAULTS["timezone"]
    if not args.tz and (not db_tz or db_tz == DEFAULTS["timezone"]):
        logger.warning("Plant time zone %s is the lux-mon default, possibly never set (pass --tz)", tz_name)
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise BackfillError(f"unknown time zone {tz_name!r}") from exc

    return Settings(cfg=cfg, model=model, temperature_unit=temp_unit, tz=tz,
                    tz_explicit=bool(args.tz), db_ok=db_ok, db_temperature_unit=db_temp,
                    fallbacks=tuple(fallbacks))


def _parse_day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {text!r}")


def _parse_cutoff(text: str, tz: tzinfo) -> float:
    """ISO 8601 date-time; without an offset it is plant-local time."""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise BackfillError(f"--cutoff: expected an ISO 8601 date-time, got {text!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.timestamp()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m collector.backfill",
        description="Backfill lux-mon's InfluxDB history from the EG4 portal's data export "
                    "(luxmon_register + SolarAssistant-style measurements only).",
    )
    p.add_argument("--start", type=_parse_day, help="first plant-local day (YYYY-MM-DD)")
    p.add_argument("--end", type=_parse_day,
                   help="last plant-local day, inclusive (default: the day of the first live point)")
    p.add_argument("--find-start", action="store_true",
                   help="probe single-day exports (binary search, not before 2020-01-01) for the first "
                        "day with data and use it as --start")
    p.add_argument("--compare-live", type=_parse_day, metavar="DATE",
                   help="compare that day's export with the live luxmon_register points (writes nothing)")
    p.add_argument("--fill-gaps", action="store_true",
                   help="find holes in the live data of the last days and fill them from the export "
                        "(what the collector's background gap-filler does; see collector/gapfill.py)")
    p.add_argument("--lookback-days", type=int, metavar="N",
                   help="--fill-gaps: how many days back to look for gaps (default 7)")
    p.add_argument("--min-gap-min", type=float, metavar="M",
                   help="--fill-gaps: minutes between two live points that count as a gap (default 10, min 6)")
    p.add_argument("--dry-run", action="store_true", help="download/parse/map/count, write nothing")
    p.add_argument("--save-xls", metavar="DIR", help="keep the raw .xls downloads in DIR")
    p.add_argument("--from-xls", metavar="FILE", help="read a saved export instead of the portal (no network)")
    p.add_argument("--request-delay", type=float, default=DEFAULT_REQUEST_DELAY, metavar="SEC",
                   help=f"seconds between portal requests (default {DEFAULT_REQUEST_DELAY:g}, min {MIN_REQUEST_DELAY:g})")
    p.add_argument("--cutoff", metavar="DATETIME",
                   help="write only before this time instead of the first live point "
                        "(ISO 8601; no offset = plant-local; not later than the first live point)")
    p.add_argument("--allow-live-overlap", action="store_true",
                   help="let --cutoff be later than the first live point (the rows in between are "
                        "written alongside the live points)")
    p.add_argument("--temp-unit", choices=("celsius", "fahrenheit"),
                   help="temperature unit when the DB is unreachable (must match the live data)")
    p.add_argument("--model", help="inverter_model when the DB is unreachable (e.g. eg4_12000xp)")
    p.add_argument("--tz", help="plant time zone of the export's Time column (default: the timezone setting, "
                                "checked against the inverter clock; required to write from --from-xls)")
    p.add_argument("--verbose", "-v", action="store_true", help="debug logging and sample decoded rows")
    return p


# ── Runner ──────────────────────────────────────────────────────────────────

class Runner:
    """Wires settings, InfluxDB, the EG4 client and the Backfiller for one CLI run."""

    def __init__(self, args: argparse.Namespace, settings: Settings,
                 target_factory: Callable[[OutputConfig], InfluxTarget] = InfluxTarget,
                 client_factory: Callable[..., ExportClient] = ExportClient,
                 out: Callable[[str], None] = print):
        self.args = args
        self.s = settings
        self.cfg = settings.cfg
        self.ctx = MapContext(model=settings.model, driver=get_driver(settings.model),
                              temperature_unit=settings.temperature_unit, tz=settings.tz)
        self._target_factory = target_factory
        self._client_factory = client_factory
        self._out = out
        self.target: Optional[InfluxTarget] = None
        self.client: Optional[ExportClient] = None
        self.live_start: Optional[float] = None

    @property
    def writes(self) -> bool:
        return not self.args.dry_run and self.args.compare_live is None

    # Setup
    def open_influx(self) -> None:
        """Connect and read the first live point. Required for writes, --compare-live and --fill-gaps."""
        required = self.writes or self.args.compare_live is not None or self.args.fill_gaps
        out = self.cfg.outputs
        if self.writes and not out.influx_enabled:
            raise BackfillError("InfluxDB output is disabled in the lux-mon settings; nothing to backfill into")
        try:
            target = self._target_factory(out)
            recorded = target.recorded_live_start()
            self.live_start = recorded if recorded is not None else target.first_soc_time()
        except Exception as exc:
            if required:
                raise BackfillError(f"InfluxDB at {out.influx_url} is not usable: {type(exc).__name__}: {exc}")
            logger.warning("InfluxDB at %s is not usable (%s); dry run without the live cutoff",
                           out.influx_url, type(exc).__name__)
            return
        self.target = target
        if self.live_start is None:
            logger.info("No live luxmon_register data yet: no cutoff from InfluxDB")
        else:
            logger.info("First live point: %s (%s local)", _iso_utc(self.live_start),
                        datetime.fromtimestamp(self.live_start, self.ctx.tz).strftime("%Y-%m-%d %H:%M:%S"))

    def open_client(self) -> ExportClient:
        cfg = self.cfg
        if not cfg.cloud_username or not cfg.cloud_password:
            raise BackfillError("LUX_CLOUD_USERNAME and LUX_CLOUD_PASSWORD must be set (environment only)")
        if not cfg.inverter_serial:
            raise BackfillError("LUX_INVERTER_SERIAL (or the inverter_serial setting) is required")
        delay = self.args.request_delay
        if delay < MIN_REQUEST_DELAY:
            logger.warning("--request-delay %.1fs is below the %.0fs minimum; using %.0fs",
                           delay, MIN_REQUEST_DELAY, MIN_REQUEST_DELAY)
            delay = MIN_REQUEST_DELAY
        try:
            self.client = self._client_factory(
                base_url=cfg.cloud_base_url, username=cfg.cloud_username, password=cfg.cloud_password,
                inverter_serial=cfg.inverter_serial, model=self.ctx.model, request_delay=delay,
            )
        except ValueError as exc:
            raise BackfillError(str(exc))
        return self.client

    def check_timezone(self, strict: bool) -> None:
        """Compare the inverter clock's UTC offset with the plant time zone in use.

        strict (a write run): without --tz, a mismatch or a clock that cannot
        be read stops the run, since a wrong zone shifts every backfilled point.
        """
        verify_timezone(self.client, self.ctx.tz, strict=strict and not self.s.tz_explicit)

    def _serial(self) -> Optional[str]:
        return self.cfg.inverter_serial or None

    def _save(self, content: bytes, start: date, end: date) -> None:
        if not self.args.save_xls:
            return
        folder = Path(self.args.save_xls)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"eg4_export_{self.cfg.inverter_serial}_{start}_{end}.xls"
        path.write_bytes(content)
        logger.debug("Saved %s (%d bytes)", path, len(content))

    def download(self, start: date, end: date) -> List[ExportSheet]:
        return download_sheets(self.client, start, end, save=self._save)

    def backfiller(self) -> Backfiller:
        cutoff = _parse_cutoff(self.args.cutoff, self.ctx.tz) if self.args.cutoff else self.live_start
        if self.args.cutoff:
            logger.info("Cutoff overridden: writing only before %s", _iso_utc(cutoff))
            if self.live_start is not None and cutoff > self.live_start:
                # Export rows (inverter-clock seconds) never share a timestamp with
                # live points (collector write times): they would interleave, not overwrite.
                msg = (f"--cutoff {_iso_utc(cutoff)} is after the first live point {_iso_utc(self.live_start)}: "
                       f"export rows between them would be written alongside the live points")
                if self.writes and not self.args.allow_live_overlap:
                    raise BackfillError(msg + " (pass --allow-live-overlap to do it anyway)")
                logger.warning(msg)
        live_units = None
        if self.target is not None and self.live_start is not None:
            since = max(self.live_start, time.time() - UNIT_CHECK_DAYS * 86400)
            try:
                live_units = self.target.live_units(since)
            except Exception as exc:
                if self.writes:
                    raise BackfillError(f"could not read the live unit tags: {type(exc).__name__}: {exc}")
                logger.warning("Could not read the live unit tags (%s)", type(exc).__name__)
        return Backfiller(
            self.ctx, target=self.target if self.writes else None, cutoff=cutoff,
            live_start=self.live_start, live_units=live_units, serial=self._serial(),
            dry_run=not self.writes, samples=3 if self.args.verbose else 0, out=self._out,
        )

    # Modes
    def run(self) -> int:
        args = self.args
        if args.fill_gaps:
            clashing = [flag for flag, value in (
                ("--start", args.start), ("--end", args.end), ("--find-start", args.find_start),
                ("--compare-live", args.compare_live), ("--from-xls", args.from_xls),
                ("--cutoff", args.cutoff), ("--allow-live-overlap", args.allow_live_overlap),
            ) if value]
            if clashing:
                raise BackfillError(f"--fill-gaps picks its own days and cutoff; do not combine it with "
                                    f"{', '.join(clashing)}")
        elif args.lookback_days is not None or args.min_gap_min is not None:
            raise BackfillError("--lookback-days and --min-gap-min only apply to --fill-gaps")
        if args.compare_live is not None and (args.find_start or args.start or args.end):
            raise BackfillError("--compare-live takes one DATE; do not combine it with --start/--end/--find-start")
        if args.from_xls and args.find_start:
            raise BackfillError("--find-start needs the portal; it cannot be combined with --from-xls")
        if args.start and args.find_start:
            raise BackfillError("use either --start or --find-start")
        if self.s.db_temperature_unit and args.temp_unit and args.temp_unit != self.s.db_temperature_unit:
            msg = (f"--temp-unit {args.temp_unit} differs from the collector's temperature_unit "
                   f"{self.s.db_temperature_unit}: temperatures would get a different unit tag")
            if self.writes:
                raise BackfillError(msg)
            logger.warning(msg)
        if self.writes and self.s.fallbacks:
            raise BackfillError(
                f"MariaDB is not reachable, so the collector's settings are unknown: pass "
                f"{' and '.join(self.s.fallbacks)} for a write run (a dry run works without)")
        if self.writes and args.from_xls and not self.s.tz_explicit:
            # Offline there is no inverter clock to check the zone against, and a
            # wrong zone shifts every point (a re-run cannot move them back).
            raise BackfillError(
                f"pass --tz to write from --from-xls: the export's Time column is plant-local and "
                f"the time zone in use ({self.ctx.tz}) cannot be checked against the inverter clock offline")

        self.open_influx()
        if args.fill_gaps:
            return self.run_fill_gaps()
        if args.compare_live is not None:
            return self.run_compare(args.compare_live)
        if args.from_xls:
            return self.run_file(Path(args.from_xls))
        return self.run_portal()

    def run_file(self, path: Path) -> int:
        started = time.monotonic()
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise BackfillError(f"cannot read {path}: {exc}")
        sheets = parse_workbook(content)
        file_serials = {_row_serial(r) for s in sheets for r in s.rows} - {""}
        if self.cfg.inverter_serial and file_serials and file_serials != {self.cfg.inverter_serial}:
            msg = f"{path.name} is for serial {', '.join(sorted(file_serials))}, not {self.cfg.inverter_serial}"
            if self.writes:
                raise BackfillError(msg)
            logger.warning("%s; the rows are checked anyway (dry run)", msg)
            self.cfg.inverter_serial = ""
        logger.info("%s: %d day sheets (%s)", path.name, len(sheets), ", ".join(s.day for s in sheets))
        bf = self.backfiller()
        bf.process(sheets, self.args.start, self.args.end)
        self.summary(bf, started)
        return 0

    def run_portal(self) -> int:
        args = self.args
        end = args.end
        if end is None:
            if self.live_start is None:
                raise BackfillError("pass --end (no first live point in InfluxDB to default to)")
            end = _local_day(self.live_start, self.ctx.tz)
        started = time.monotonic()
        bf = self.backfiller()  # cutoff and live unit tags checked before any portal request
        self.open_client()
        self.check_timezone(strict=self.writes)

        start = args.start
        if args.find_start:
            start = self.find_start(end)
            if start is None:
                raise BackfillError(f"the export has no rows for {end}; cannot search backwards from it")
            self._out(f"First day with export data: {start}")
        if start is None:
            raise BackfillError("pass --start YYYY-MM-DD or --find-start")
        if start > end:
            raise BackfillError(f"--start {start} is after the end day {end}")

        windows = list(iter_windows(start, end))
        logger.info("Backfilling %s..%s (%d days, %d windows, run_id %s)%s", start, end,
                    (end - start).days + 1, len(windows), bf.run_id, " - dry run" if bf.dry_run else "")
        for i, (ws, we) in enumerate(windows, 1):
            sheets = self.download(ws, we)
            logger.info("Window %d/%d %s..%s: %d sheets", i, len(windows), ws, we, len(sheets))
            bf.process(sheets, ws, we, label=f"{ws}..{we}")
        self.summary(bf, started)
        return 0

    def run_fill_gaps(self) -> int:
        """--fill-gaps: fill the holes in the live data from the export (collector.gapfill)."""
        from .gapfill import GapFiller, exclusive_run, format_report, make_options

        started = time.monotonic()
        options = make_options(lookback_days=self.args.lookback_days, min_gap_min=self.args.min_gap_min)
        filler = GapFiller(
            self.ctx, self.target, client_factory=self.open_client, options=options,
            dry_run=not self.writes, serial=self._serial(), tz_explicit=self.s.tz_explicit,
            save=self._save,
        )
        with exclusive_run() as alone:
            if not alone:
                raise BackfillError("another gap-fill run is in progress (the collector's background "
                                    "gap-filler?); try again later")
            try:
                filler.run()
            finally:
                # Also after an error: the gaps found and what was already done.
                for line in format_report(filler.report, self.ctx.tz):
                    self._out(line)
                if self.client is not None:
                    self._out(f"downloaded {self.client.bytes_downloaded / 1e6:.1f} MB")
                self._out(f"elapsed {time.monotonic() - started:.0f}s")
        return 0

    def find_start(self, upper: date) -> Optional[date]:
        def has_rows(day: date) -> bool:
            sheets = self.download(day, day)
            rows = sum(len(s.rows) for s in sheets if s.day == day.isoformat())
            logger.info("find-start: %s has %d rows", day, rows)
            return rows > 0

        return find_first_day(has_rows, upper)

    def run_compare(self, day: date) -> int:
        if self.args.from_xls:
            sheets = parse_workbook(Path(self.args.from_xls).read_bytes())
        else:
            self.open_client()
            self.check_timezone(strict=False)
            sheets = self.download(day, day)
        sheet = next((s for s in sheets if s.day == day.isoformat()), None)
        if sheet is None:
            raise BackfillError(f"the export has no worksheet for {day}")
        stats = Stats()
        points = sheet_points(sheet, self.ctx, stats, self._serial())
        if not points:
            raise BackfillError(f"no usable rows for {day}")
        if self.live_start is None:
            raise BackfillError("InfluxDB has no live luxmon_register data to compare with")
        # Only the overlap with live data: points before live_start may be backfilled ones.
        overlap = [p for p in points if p.ts >= self.live_start - MATCH_WINDOW_SEC]
        if not overlap:
            raise BackfillError(f"{day} ends before the first live point ({_iso_utc(self.live_start)})")
        start = max(self.live_start, overlap[0].ts - MATCH_WINDOW_SEC)
        stop = overlap[-1].ts + MATCH_WINDOW_SEC + 1
        live = self.target.register_points(start, stop)
        self._out(f"Compare {day}: {len(overlap)} export rows {overlap[0].local}..{overlap[-1].local} "
                  f"({self.ctx.tz}) vs live luxmon_register (nearest point within ±{MATCH_WINDOW_SEC // 60} min); "
                  f"model {self.ctx.model}, {self.ctx.temperature_unit}")
        for line in format_comparison(compare_points(overlap, live)):
            self._out(line)
        self._out("Nothing was written.")
        return 0

    def summary(self, bf: Backfiller, started: float) -> None:
        st = bf.stats
        lines = [
            "── Backfill summary ──",
            f"run_id {bf.run_id}{' (dry run: nothing written)' if bf.dry_run else ''}",
            f"model {self.ctx.model}, temperatures {self.ctx.temperature_unit}, time zone {self.ctx.tz}",
            f"cutoff {_iso_utc(bf.cutoff) if bf.cutoff is not None else 'none'}",
            f"windows {st.windows}, day sheets {st.sheets}, rows {st.rows}",
            f"snapshots {st.snapshots}, points {'counted' if bf.dry_run else 'written'} {st.points}",
            f"skipped: {st.skipped_overlap} at/after the cutoff, {st.skipped_no_battery} without battery voltage, "
            f"{st.skipped_time} without a time, {st.skipped_serial} other serial, {st.duplicates} duplicate timestamps",
            f"DST: {st.dst_fold} repeated-hour rows, {st.dst_gap} rows in a skipped hour, "
            f"{st.dst_dropped} repeated-hour rows dropped (gap-fill)",
        ]
        if st.unknown_status:
            lines.append("unknown Status texts (state left out): "
                         + ", ".join(f"{k!r} x{v}" for k, v in st.unknown_status.most_common()))
        if self.client is not None:
            lines.append(f"downloads {self.client.downloads} ({self.client.bytes_downloaded / 1e6:.1f} MB)")
        lines.append(f"elapsed {time.monotonic() - started:.0f}s")
        for line in lines:
            self._out(line)

    def close(self) -> None:
        if self.target is not None:
            self.target.close()
        if self.client is not None:
            try:
                if self.client._session is not None:
                    self.client._session.close()
            except Exception:
                pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Library debug logs may carry request headers (the InfluxDB token).
    for name in ("urllib3", "influxdb_client", "requests"):
        logging.getLogger(name).setLevel(logging.WARNING)

    runner: Optional[Runner] = None
    try:
        runner = Runner(args, load_settings(args))
        return runner.run()
    except CloudAuthError as exc:
        redact = runner.client._redact if runner and runner.client else str
        logger.error("EG4 cloud rejected the login (%s); stopping without further requests - "
                     "check LUX_CLOUD_USERNAME/LUX_CLOUD_PASSWORD", redact(str(exc)))
        return 2
    except BackfillError as exc:
        logger.error("%s", exc)
        return 1
    except (CloudApiError, CloudTransientError, CloudSessionExpired) as exc:
        logger.error("EG4 portal request failed: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.warning("Interrupted; points written so far are kept (re-running the range is safe)")
        return 130
    finally:
        if runner is not None:
            runner.close()


if __name__ == "__main__":
    sys.exit(main())
