"""Automatic gap-filling of lux-mon's live InfluxDB data from the EG4 data export.

The live transport writes one `luxmon_register` point per EG4 upload (every
1-2 min on the cloud transport). When lux-mon or the house internet is down
the live data gets a hole, but EG4 often still has the readings:

  - lux-mon offline, dongle online: EG4 has everything -> fillable.
  - house internet down: once offline for ~20 min the dongle stores a reading
    every 5 min and uploads them on reconnect -> fillable later, usually
    without the first ~20 min.
  - inverter/dongle without power: nothing anywhere -> unfillable.

GapFiller finds the holes (detect_gaps, UTC, on the soc points), downloads
only the plant-local days they span through the backfill's ExportClient, and
writes only the export rows strictly inside a hole, through the same
decode/clamp/temperature/line path and safety checks as collector.backfill
(unit tags vs the live data, serial, inverter-clock time zone check). The
collector runs it in the background (GapFillThread); on demand:

    docker exec lux-collector python -m collector.backfill --fill-gaps --dry-run

Safety:
  - Writes only InfluxDB: `luxmon_register`, the SolarAssistant-style
    measurements and `luxmon_backfill` records (tag mode=gapfill). Never
    `luxmon_cloud`, MariaDB, MQTT, alerts or the hourly energy rollup.
  - Only rows with gap_start + margin < t < gap_end - margin are written,
    never at or after now - settle and never before the first live point.
    The backfill's live_start cutoff is lifted for exactly those rows. A
    closed gap ends at its closing live point's reading (upload) time, not
    its write time: a cloud reading is written up to minutes after EG4
    received it.
  - A gap whose surrounding live points use other unit tags than the fill
    would (temperature_unit changed since) is given up, not filled.
  - Every download goes through ExportClient (read-only allowlist, 3 s
    pacing, bounded retries, a rejected login is never retried) and at most
    max_downloads per run. A gap without export rows is retried at most every
    retry_sec and given up once older than giveup_sec; the attempts are kept
    in `luxmon_backfill`, so a restart does not download everything again.
  - Each run uses its own portal session (a new ExportClient), never the live
    transport's, and no two runs overlap (exclusive_run).
"""

import logging
import math
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import fcntl
except ImportError:  # not POSIX: the in-process lock only
    fcntl = None  # type: ignore[assignment]

from .backfill import (
    BACKFILL_MEASUREMENT,
    DEFAULT_REQUEST_DELAY,
    MAX_WINDOW_DAYS,
    UNIT_CHECK_DAYS,
    Backfiller,
    BackfillError,
    ExportClient,
    InfluxTarget,
    MapContext,
    RowPoint,
    RunStopped,
    _iso_utc,
    _local_day,
    _units,
    download_sheets,
    iter_windows,
    verify_timezone,
)
from .collector import CollectorConfig, _env_bool, _load_db_setting
from .comm.cloud_http import (
    DEFAULT_POLL_SEC,
    FIRST_UPLOAD_SLACK_SEC,
    MIN_POLL_SEC,
    CloudApiError,
    CloudAuthError,
    CloudSessionExpired,
    CloudTransientError,
)
from .drivers.registry import get_driver
from .outputs import OutputConfig, _to_line
from .settings import DEFAULTS

logger = logging.getLogger("luxmon.gapfill")


# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_LOOKBACK_DAYS = 7
MAX_LOOKBACK_DAYS = 30
DEFAULT_MIN_GAP_MIN = 10.0
# Export rows are ~4-5 min apart: a smaller threshold would find the filled
# stretches again as gaps.
MIN_GAP_FLOOR_MIN = 6.0
MAX_MIN_GAP_MIN = 24 * 60.0
DEFAULT_SETTLE_SEC = 1800.0   # EG4's export and the dongle's buffered upload lag the live data
DEFAULT_MARGIN_SEC = 60.0     # never write an export row within this of a live point
DEFAULT_RETRY_HOURS = 6.0
DEFAULT_GIVEUP_HOURS = 48.0
DEFAULT_MAX_DOWNLOADS = 6     # export requests (windows of <= 10 days) per run
DEFAULT_INTERVAL_MIN = 180.0
MIN_INTERVAL_MIN = 30.0
MAX_INTERVAL_MIN = 7 * 24 * 60.0
DEFAULT_STARTUP_DELAY_SEC = 600.0
AUTH_PAUSE_SEC = 86400.0      # no portal login for a day after a rejected one
STOP_JOIN_SEC = 5.0
MATCH_TOL_SEC = 1.0           # gap boundaries are live-point times; allow float noise
UNIT_CONTEXT_SEC = 3600.0     # live points this far around a gap set the unit tags it must use


def max_reading_age(poll_sec: float = DEFAULT_POLL_SEC) -> float:
    """The oldest reading cloud_http writes (its first upload after a start: 3 polls + slack)."""
    poll = float(poll_sec) if math.isfinite(poll_sec) else float(DEFAULT_POLL_SEC)
    return 3 * max(poll, float(MIN_POLL_SEC)) + FIRST_UPLOAD_SLACK_SEC

GAPFILL_MODE = "gapfill"
LOCK_FILE = os.path.join(tempfile.gettempdir(), "lux-mon-gapfill.lock")

# What a run does with a detected gap.
ATTEMPT = "attempt"       # download its days now
WAIT = "wait"             # checked before without rows; retry later
GIVE_UP = "give_up"       # checked, still empty, and too old: give up now
GIVEN_UP = "given_up"     # given up in an earlier run
DEFERRED = "deferred"     # due, but over this run's download budget
BLOCKED = "blocked"       # due, but the portal must not be used now

# Status of an attempt record.
FILLED = "filled"         # export rows were written inside the gap
EMPTY = "empty"           # the export had no rows inside the gap
GAVE_UP = "gave_up"

Gap = Tuple[float, float]


class GapFillStopped(RunStopped):
    """The collector is stopping: abandon the run."""


# ── Detection ───────────────────────────────────────────────────────────────

def detect_gaps(times: Iterable[float], start: float, end: float,
                min_gap: float = DEFAULT_MIN_GAP_MIN * 60,
                live_start: Optional[float] = None) -> List[Gap]:
    """Find the holes in the live data between start and end (UTC epoch seconds).

    `times` are the luxmon_register soc timestamps in [start, end] plus the
    last one before start, if any (it anchors a gap that began earlier). A gap
    is two consecutive points more than min_gap apart, plus a trailing gap
    from the last point to `end` when that is longer than min_gap (callers
    pass end = now - settle). Only time after live_start counts (before it is
    the one-off backfill's job): every gap is clipped to
    [max(start, live_start), end] and dropped unless still longer than
    min_gap. Two gaps that meet at one live point stay separate, so the
    margin keeps export rows off that point. Without any point there is
    nothing to anchor a gap to, so no live data means no gaps.

    Returns sorted, non-overlapping (gap_start, gap_end) intervals.
    """
    if end <= start:
        return []
    lower = start if live_start is None else max(start, live_start)
    points = sorted({float(t) for t in times if t is not None and math.isfinite(t) and t <= end})
    if not points:
        return []
    raw = [(a, b) for a, b in zip(points, points[1:]) if b - a > min_gap]
    if end - points[-1] > min_gap:
        raw.append((points[-1], end))
    gaps: List[Gap] = []
    for a, b in raw:
        s, e = max(a, lower), min(b, end)
        if e - s > min_gap:
            gaps.append((s, e))
    return gaps


def gap_days(gap: Gap, tz: tzinfo, margin: float = DEFAULT_MARGIN_SEC) -> List[date]:
    """Plant-local days that can hold rows of the gap (start + margin < t < end - margin)."""
    first_ts, last_ts = gap[0] + margin, gap[1] - margin
    if last_ts <= first_ts:
        return []
    first, last = _local_day(first_ts, tz), _local_day(last_ts, tz)
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def day_windows(days: Iterable[date], max_days: int = MAX_WINDOW_DAYS) -> List[Tuple[date, date]]:
    """Export windows for the days: each run of consecutive days, split into <= max_days."""
    runs: List[List[date]] = []
    for day in sorted(set(days)):
        if runs and day == runs[-1][1] + timedelta(days=1):
            runs[-1][1] = day
        else:
            runs.append([day, day])
    return [window for first, last in runs for window in iter_windows(first, last, max_days)]


def _window_days(window: Tuple[date, date]) -> List[date]:
    first, last = window
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


# ── Attempts (retry / give-up state) ────────────────────────────────────────

@dataclass(frozen=True)
class GapAttempt:
    """An earlier gap-fill attempt: a luxmon_backfill record with tag mode=gapfill."""

    gap_start: float
    gap_end: float
    attempted_at: float
    status: str            # filled | empty | gave_up
    rows_written: int = 0
    trailing: bool = False

    @classmethod
    def from_fields(cls, fields: Dict[str, Any]) -> Optional["GapAttempt"]:
        """Build from a record's fields; None when the record is incomplete."""
        try:
            start = float(fields["gap_start"])
            end = float(fields["gap_end"])
            at = float(fields["attempted_at"])
        except (KeyError, TypeError, ValueError):
            return None
        rows = fields.get("rows_written")
        trailing = fields.get("trailing")
        if not isinstance(trailing, bool):
            trailing = str(trailing).lower() in ("true", "t", "1", "1.0")
        return cls(
            gap_start=start, gap_end=end, attempted_at=at, status=str(fields.get("status") or ""),
            rows_written=int(rows) if isinstance(rows, (int, float)) and not isinstance(rows, bool) else 0,
            trailing=trailing,
        )


def plan_gap(gap: Gap, attempts: Sequence[GapAttempt], now: float, trailing: bool = False,
             retry_sec: float = DEFAULT_RETRY_HOURS * 3600,
             giveup_sec: float = DEFAULT_GIVEUP_HOURS * 3600) -> Tuple[str, Optional[float]]:
    """What to do with a gap now, given the earlier attempts: (action, next retry time).

    A gap was already checked when an earlier attempt covered all of it (the
    export had no rows there then, or they would have been written) or, for
    a trailing gap that is still open, when an attempt during the same
    ongoing outage found nothing. A gap never checked is attempted whatever
    its age. A checked one is retried at most every retry_sec and given up
    once older than giveup_sec: since its end, or for a still-open trailing
    gap since its start. A trailing gap that has closed since (the live data
    came back) counts as new, since the dongle uploads its buffered readings
    on reconnect.
    """
    start, end = gap
    checked = [a for a in attempts
               if a.gap_start <= start + MATCH_TOL_SEC and a.gap_end >= end - MATCH_TOL_SEC]
    if trailing:
        checked += [a for a in attempts
                    if a.trailing and a.status != FILLED and a not in checked
                    and a.gap_start <= start + MATCH_TOL_SEC and a.gap_end >= start - MATCH_TOL_SEC]
    if not checked:
        return ATTEMPT, None
    if any(a.status == GAVE_UP for a in checked):
        return GIVEN_UP, None
    age = now - (start if trailing else end)
    if age > giveup_sec:
        return GIVE_UP, None
    retry_at = max(a.attempted_at for a in checked) + retry_sec
    if now >= retry_at:
        return ATTEMPT, None
    return WAIT, retry_at


# ── Options ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GapFillOptions:
    """Tunables of one gap-fill run (seconds unless named otherwise)."""

    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    min_gap_sec: float = DEFAULT_MIN_GAP_MIN * 60
    settle_sec: float = DEFAULT_SETTLE_SEC
    margin_sec: float = DEFAULT_MARGIN_SEC
    retry_sec: float = DEFAULT_RETRY_HOURS * 3600
    giveup_sec: float = DEFAULT_GIVEUP_HOURS * 3600
    max_downloads: int = DEFAULT_MAX_DOWNLOADS
    # How old the closing live point's reading may be when its upload time is
    # unknown (no luxmon_cloud point at it): the gap then ends this much earlier.
    reading_age_fallback_sec: float = field(default_factory=max_reading_age)


def _bounded(name: str, value: Optional[float], default: float, minimum: float,
             maximum: Optional[float] = None) -> float:
    """value within [minimum, maximum] (with a warning when changed); default when None."""
    if value is None:
        return default
    if not math.isfinite(value):
        logger.warning("%s %s is not a finite number; using %s", name, value, default)
        return default
    if value < minimum:
        logger.warning("%s %s is below the minimum %s; using %s", name, value, minimum, minimum)
        return minimum
    if maximum is not None and value > maximum:
        logger.warning("%s %s is above the maximum %s; using %s", name, value, maximum, maximum)
        return maximum
    return value


def make_options(lookback_days: Optional[float] = None, min_gap_min: Optional[float] = None,
                 names: Tuple[str, str] = ("--lookback-days", "--min-gap-min"),
                 **overrides: Any) -> GapFillOptions:
    """GapFillOptions from user-facing values (days, minutes), bounded to safe ranges."""
    lookback = int(_bounded(names[0], lookback_days, DEFAULT_LOOKBACK_DAYS, 1, MAX_LOOKBACK_DAYS))
    min_gap = _bounded(names[1], min_gap_min, DEFAULT_MIN_GAP_MIN, MIN_GAP_FLOOR_MIN, MAX_MIN_GAP_MIN)
    return GapFillOptions(lookback_days=lookback, min_gap_sec=min_gap * 60, **overrides)


@dataclass(frozen=True)
class GapFillSettings:
    """The collector's background gap-filler settings (LUX_GAPFILL_* environment)."""

    enabled: bool = True
    interval_sec: float = DEFAULT_INTERVAL_MIN * 60
    options: GapFillOptions = field(default_factory=GapFillOptions)


def _env_float(name: str) -> Optional[float]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using the default", name, raw)
        return None


def settings_from_env() -> GapFillSettings:
    """LUX_GAPFILL_ENABLED / _INTERVAL_MIN / _LOOKBACK_DAYS / _MIN_GAP_MIN (env-only)."""
    interval = _bounded("LUX_GAPFILL_INTERVAL_MIN", _env_float("LUX_GAPFILL_INTERVAL_MIN"),
                        DEFAULT_INTERVAL_MIN, MIN_INTERVAL_MIN, MAX_INTERVAL_MIN)
    return GapFillSettings(
        enabled=_env_bool("LUX_GAPFILL_ENABLED", True),
        interval_sec=interval * 60,
        options=make_options(
            lookback_days=_env_float("LUX_GAPFILL_LOOKBACK_DAYS"),
            min_gap_min=_env_float("LUX_GAPFILL_MIN_GAP_MIN"),
            names=("LUX_GAPFILL_LOOKBACK_DAYS", "LUX_GAPFILL_MIN_GAP_MIN"),
        ),
    )


def gapfill_disabled_reason(cfg: CollectorConfig, settings: Optional[GapFillSettings] = None) -> Optional[str]:
    """Why the background gap-filler must not run for this collector, or None."""
    enabled = settings.enabled if settings is not None else _env_bool("LUX_GAPFILL_ENABLED", True)
    if not enabled:
        return "LUX_GAPFILL_ENABLED is off"
    if not cfg.cloud_username or not cfg.cloud_password:
        return "no EG4 portal login (LUX_CLOUD_USERNAME / LUX_CLOUD_PASSWORD)"
    if (cfg.transport or "").lower() == "replay" or cfg.replay_file:
        return "the transport is replay"
    return None


# ── One run at a time ───────────────────────────────────────────────────────

_RUN_LOCK = threading.Lock()


@contextmanager
def exclusive_run(lock_file: Optional[str] = LOCK_FILE) -> Iterator[bool]:
    """Yield True when no other gap-fill run is in progress, else False.

    An in-process lock (the collector's thread) plus, where the OS has flock,
    an advisory lock file, so a manual --fill-gaps in the collector container
    does not run alongside the background thread.
    """
    if not _RUN_LOCK.acquire(blocking=False):
        yield False
        return
    handle = None
    alone = True
    try:
        if lock_file and fcntl is not None:
            try:
                handle = open(lock_file, "a")
            except OSError as exc:
                logger.debug("Gap-fill lock file %s unavailable (%s); in-process lock only", lock_file, exc)
            else:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    handle.close()
                    handle = None
                    alone = False
        yield alone
    finally:
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            handle.close()
        _RUN_LOCK.release()


# ── Run ─────────────────────────────────────────────────────────────────────

@dataclass
class GapPlan:
    """A detected gap and what this run did with it."""

    start: float
    end: float
    trailing: bool
    action: str
    days: List[date] = field(default_factory=list)
    next_retry: Optional[float] = None
    status: Optional[str] = None     # filled | empty | gave_up once all its days were downloaded
    rows: int = 0                    # export rows written (dry run: that would be) inside the gap
    windows: List[str] = field(default_factory=list)
    skip_reason: Optional[str] = None  # why its rows were not written (unit tags differ)

    @property
    def gap(self) -> Gap:
        return self.start, self.end


@dataclass
class GapFillReport:
    """The outcome of one GapFiller run."""

    run_id: str
    dry_run: bool
    start: float                     # detection window (UTC epoch seconds)
    end: float                       # now - settle: nothing at or after it is written
    min_gap_sec: float = DEFAULT_MIN_GAP_MIN * 60
    live_start: Optional[float] = None
    gaps: List[GapPlan] = field(default_factory=list)
    downloads: int = 0
    points: int = 0                  # line-protocol points written (dry run: counted)
    blocked: Optional[str] = None
    finished: bool = False           # False: the run stopped with an error part-way

    def count(self, *, action: Optional[str] = None, status: Optional[str] = None) -> int:
        return sum(1 for g in self.gaps
                   if (action is None or g.action == action) and (status is None or g.status == status))


def _local(ts: float, tz: tzinfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def describe_gap(plan: GapPlan, tz: tzinfo) -> str:
    """'2026-09-30 15:31:03 PDT..15:50:54 PDT (20 min, still open)'."""
    first, last = _local(plan.start, tz), _local(plan.end, tz)
    if first[:10] == last[:10]:
        last = last[11:]
    minutes = (plan.end - plan.start) / 60
    return f"{first}..{last} ({minutes:.0f} min{', still open' if plan.trailing else ''})"


class GapFiller:
    """One gap-fill run: detect the gaps, plan them, download the due days, write the rows inside."""

    def __init__(self, ctx: MapContext, target: InfluxTarget, client_factory: Callable[[], ExportClient],
                 options: Optional[GapFillOptions] = None, dry_run: bool = False,
                 serial: Optional[str] = None, tz_explicit: bool = False,
                 portal_blocked: Optional[Callable[[], Optional[str]]] = None,
                 stop: Optional[threading.Event] = None, clock: Callable[[], float] = time.time,
                 run_id: Optional[str] = None,
                 save: Optional[Callable[[bytes, date, date], None]] = None):
        self.ctx = ctx
        self.target = target
        self._save = save
        self.options = options or GapFillOptions()
        self.dry_run = dry_run
        self.serial = serial
        self.tz_explicit = tz_explicit
        self._client_factory = client_factory
        self._portal_blocked = portal_blocked
        self._stop = stop
        self._clock = clock
        self.run_id = run_id or (
            "gapfill-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6])
        self.report = GapFillReport(run_id=self.run_id, dry_run=dry_run, start=0.0, end=0.0,
                                    min_gap_sec=self.options.min_gap_sec)
        self.backfiller: Optional[Backfiller] = None

    def run(self) -> GapFillReport:
        """Detect, plan and fill. Raises on portal/InfluxDB/safety errors (self.report keeps what was done)."""
        report = self._run()
        report.finished = True
        return report

    def _run(self) -> GapFillReport:
        o = self.options
        now = self._clock()
        report = self.report = GapFillReport(
            run_id=self.run_id, dry_run=self.dry_run, start=now - o.lookback_days * 86400,
            end=now - o.settle_sec, min_gap_sec=o.min_gap_sec,
        )
        live_start = report.live_start = self._live_start()
        if live_start is None:
            logger.info("No live luxmon_register data yet: no gaps to fill")
            return report
        gaps = self.detect(report.start, report.end, live_start)
        if not gaps:
            logger.debug("No gaps in the live data since %s", _iso_utc(max(report.start, live_start)))
            return report

        attempts = self._attempts(report.start - o.lookback_days * 86400)
        for gap in gaps:
            trailing = gap[1] >= report.end - MATCH_TOL_SEC
            if not trailing:
                gap = (gap[0], self._reading_end(gap[1]))
            action, retry_at = plan_gap(gap, attempts, now, trailing, o.retry_sec, o.giveup_sec)
            report.gaps.append(GapPlan(start=gap[0], end=gap[1], trailing=trailing, action=action,
                                       days=gap_days(gap, self.ctx.tz, o.margin_sec), next_retry=retry_at))

        for plan in report.gaps:
            if plan.action == GIVE_UP:
                logger.info("Giving up on the gap %s: the EG4 export had no rows in it and it is older "
                            "than %.0f h", describe_gap(plan, self.ctx.tz), o.giveup_sec / 3600)
                if not self.dry_run:
                    self._record(plan, GAVE_UP, now)

        due = [p for p in report.gaps if p.action == ATTEMPT and p.days]
        if not due:
            return report
        windows = self._budget(due)
        due = [p for p in due if p.action == ATTEMPT]
        if not due or not windows:
            return report
        blocked = self._blocked()
        if blocked:
            report.blocked = blocked
            for plan in due:
                plan.action = BLOCKED
            logger.info("Gap-fill downloads skipped: %s", blocked)
            return report
        self._fill(due, windows, live_start, now)
        return report

    # Steps
    def detect(self, start: float, end: float, live_start: float) -> List[Gap]:
        """detect_gaps on the soc points after live_start in [start, end] (plus the one before)."""
        lower = max(start, live_start)
        if lower >= end:
            return []
        times = self.target.soc_times(lower, end)
        if lower > live_start:
            before = self.target.last_soc_before(lower, since=live_start)
            if before is not None:
                times.append(before)
        return detect_gaps(times, start, end, self.options.min_gap_sec, live_start)

    def _reading_end(self, live_ts: float) -> float:
        """Where a gap closed by the live point written at live_ts ends: that point's reading time.

        The upload time from its luxmon_cloud point, else live_ts minus the
        oldest reading age the live transport writes. Never after live_ts.
        """
        try:
            reading = self.target.reading_time(live_ts)
        except Exception as exc:
            raise BackfillError(f"could not read the live upload times: {type(exc).__name__}: {exc}")
        if reading is None or not math.isfinite(reading):
            return live_ts - self.options.reading_age_fallback_sec
        return min(live_ts, reading)

    def _gap_units(self, plan: GapPlan) -> Dict[str, set]:
        """name -> the unit tags of the live points within UNIT_CONTEXT_SEC around the gap."""
        try:
            return self.target.unit_sets(plan.start - UNIT_CONTEXT_SEC, plan.end + UNIT_CONTEXT_SEC)
        except Exception as exc:
            raise BackfillError(f"could not read the live unit tags: {type(exc).__name__}: {exc}")

    def _live_start(self) -> Optional[float]:
        recorded = self.target.recorded_live_start()
        return recorded if recorded is not None else self.target.first_soc_time()

    def _attempts(self, since: float) -> List[GapAttempt]:
        attempts = [GapAttempt.from_fields(r) for r in self.target.gapfill_records(since)]
        return [a for a in attempts if a is not None]

    def _live_units(self, live_start: float, now: float) -> Dict[str, str]:
        try:
            return self.target.live_units(max(live_start, now - UNIT_CHECK_DAYS * 86400))
        except Exception as exc:
            raise BackfillError(f"could not read the live unit tags: {type(exc).__name__}: {exc}")

    def _budget(self, due: List[GapPlan]) -> List[Tuple[date, date]]:
        """The export windows of this run (<= max_downloads); gaps not fully covered are DEFERRED."""
        o = self.options
        windows = day_windows(d for p in due for d in p.days)
        if len(windows) > o.max_downloads:
            logger.info("Gap-fill needs %d export downloads; doing %d now, the rest in the next run",
                        len(windows), o.max_downloads)
            windows = windows[:max(0, o.max_downloads)]
        fetched = {d for w in windows for d in _window_days(w)}
        for plan in due:
            if not set(plan.days) <= fetched:
                plan.action = DEFERRED
        needed = {d for p in due if p.action == ATTEMPT for d in p.days}
        return [w for w in windows if needed.intersection(_window_days(w))]

    def _blocked(self) -> Optional[str]:
        if self._portal_blocked is None:
            return None
        try:
            return self._portal_blocked()
        except Exception as exc:
            logger.debug("Portal check failed (%s); going ahead", type(exc).__name__)
            return None

    def _check_stop(self) -> None:
        if self._stop is not None and self._stop.is_set():
            raise GapFillStopped("collector stopping")

    def _fill(self, due: List[GapPlan], windows: List[Tuple[date, date]], live_start: float, now: float) -> None:
        o = self.options
        report = self.report
        bf = self.backfiller = Backfiller(
            self.ctx, target=None if self.dry_run else self.target, cutoff=live_start, live_start=live_start,
            live_units=self._live_units(live_start, now), serial=self.serial, dry_run=self.dry_run,
            run_id=self.run_id, out=lambda text: None,
            gaps=[p.gap for p in due], gap_margin=o.margin_sec, not_after=report.end,
        )
        pending = {i: {w for w in windows if set(p.days).intersection(_window_days(w))} for i, p in enumerate(due)}
        gap_units = [self._gap_units(p) for p in due]
        self._check_stop()
        client = self._client_factory()
        try:
            # A wrong zone would shift every row: verified before any download.
            verify_timezone(client, self.ctx.tz, strict=not self.dry_run and not self.tz_explicit)
            for window in windows:
                self._check_stop()
                first, last = window
                sheets = download_sheets(client, first, last, save=self._save)
                report.downloads += 1
                bf.stats.windows += 1
                _, points = bf.window_points(sheets, first, last)
                points = self._unit_checked(due, gap_units, points)
                bf.check_units(points)
                written = bf.write_points(points)
                bf.stats.snapshots += len(points)
                bf.stats.points += written
                report.points += written
                for p in points:
                    i = bf.gap_of(p.ts)
                    if i is not None:
                        due[i].rows += 1
                for i, plan in enumerate(due):
                    if window not in pending[i]:
                        continue
                    pending[i].discard(window)
                    plan.windows.append(f"{first}..{last}")
                    if not pending[i]:
                        self._finish(plan, now)
        finally:
            _close_session(client)

    def _unit_checked(self, due: List[GapPlan], gap_units: List[Dict[str, set]],
                      points: List[RowPoint]) -> List[RowPoint]:
        """The points minus those of gaps whose live neighbours use other unit tags.

        Such a gap (temperature_unit changed since, or around it) would get a
        fill in another series than the live points around it: it is skipped.
        """
        bf = self.backfiller
        by_gap: Dict[int, List[RowPoint]] = {}
        for p in points:
            i = bf.gap_of(p.ts)
            if i is not None:
                by_gap.setdefault(i, []).append(p)
        for i, gap_points in by_gap.items():
            plan = due[i]
            if plan.skip_reason is not None:
                continue
            conflicts = []
            for name, unit in _units(gap_points).items():
                live = gap_units[i].get(name)
                if live and (len(live) > 1 or unit not in live):
                    conflicts.append(f"{name}: fill {unit!r} vs live {'/'.join(sorted(live))!r}")
            if conflicts:
                plan.skip_reason = "unit tags differ from the live data around it (" + "; ".join(conflicts) + ")"
        return [p for p in points
                if (i := bf.gap_of(p.ts)) is None or due[i].skip_reason is None]

    def _finish(self, plan: GapPlan, now: float) -> None:
        """All of the gap's days were downloaded: log and record the attempt."""
        o = self.options
        if plan.skip_reason is not None:
            plan.status = GAVE_UP
            logger.warning("Not filling the gap %s: %s; given up (check the temperature_unit setting)",
                           describe_gap(plan, self.ctx.tz), plan.skip_reason)
            if not self.dry_run:
                self._record(plan, GAVE_UP, now)
            return
        plan.status = FILLED if plan.rows else EMPTY
        text = describe_gap(plan, self.ctx.tz)
        if plan.status == FILLED:
            logger.info("%s gap %s with %d export rows", "Would fill" if self.dry_run else "Filled",
                        text, plan.rows)
        else:
            logger.info("No EG4 export rows yet for the gap %s; retrying in %.0f h at the earliest",
                        text, o.retry_sec / 3600)
        if not self.dry_run:
            self._record(plan, plan.status, now)

    def _record(self, plan: GapPlan, status: str, now: float) -> None:
        """Write the attempt to luxmon_backfill (tags mode=gapfill, run_id; at the gap start)."""
        fields: Dict[str, Any] = {
            "gap_start": plan.start,
            "gap_end": plan.end,
            "attempted_at": now,
            "status": status,
            "rows_written": plan.rows,
            "trailing": plan.trailing,
            "source": "eg4_export",
        }
        if plan.windows:
            fields["window"] = ",".join(plan.windows)
        if plan.skip_reason:
            fields["reason"] = plan.skip_reason
        line = _to_line(BACKFILL_MEASUREMENT, {"mode": GAPFILL_MODE, "run_id": self.run_id}, fields,
                        int(round(plan.start * 1e9)))
        self.target.write([line])


def _close_session(client: Any) -> None:
    try:
        session = getattr(client, "_session", None)
        if session is not None:
            session.close()
    except Exception:
        pass


def format_report(report: GapFillReport, tz: tzinfo) -> List[str]:
    """The --fill-gaps summary."""
    lines = [f"── Gap fill{' (dry run: nothing written)' if report.dry_run else ''} ──",
             f"run_id {report.run_id}"]
    if report.live_start is None:
        lines.append("no live luxmon_register data yet: nothing to fill" if report.finished
                     else "stopped before the gaps were known (see the error)")
        return lines
    lines.append(
        f"live data since {_iso_utc(report.live_start)[:19]}Z; checked "
        f"{_local(max(report.start, report.live_start), tz)}..{_local(report.end, tz)} for gaps over "
        f"{report.min_gap_sec / 60:g} min (nothing at or after the end is written)")
    if not report.gaps:
        lines.append("no gaps")
    else:
        lines.append(f"{len(report.gaps)} gap{'s' if len(report.gaps) != 1 else ''}:")
    for plan in report.gaps:
        lines.append(f"  {describe_gap(plan, tz)} -> {_outcome(plan, report, tz)}")
    if report.blocked:
        lines.append(f"downloads skipped: {report.blocked}")
    if not report.finished:
        lines.append("the run stopped part-way (see the error); gaps already done are kept")
    lines.append(f"export downloads {report.downloads}, points {'counted' if report.dry_run else 'written'} "
                 f"{report.points}")
    return lines


def _outcome(plan: GapPlan, report: GapFillReport, tz: tzinfo) -> str:
    if plan.action == ATTEMPT:
        if plan.status == FILLED:
            return (f"{plan.rows} export rows would be written" if report.dry_run
                    else f"wrote {plan.rows} export rows")
        if plan.status == EMPTY:
            return "no export rows in it yet"
        if plan.status == GAVE_UP:
            return f"not filled: {plan.skip_reason or 'given up'}" + (" (dry run: not recorded)" if report.dry_run else "")
        return "not done (the run stopped before its days were downloaded)"
    if plan.action == WAIT:
        when = _local(plan.next_retry, tz) if plan.next_retry else "later"
        return f"checked before without export rows; next try after {when}"
    if plan.action == GIVE_UP:
        return "no export rows and too old: given up" + (" (dry run: not recorded)" if report.dry_run else "")
    if plan.action == GIVEN_UP:
        return "given up earlier"
    if plan.action == DEFERRED:
        return "left for the next run (download budget)"
    if plan.action == BLOCKED:
        return "skipped (portal not used now)"
    return plan.action


# ── Background thread ───────────────────────────────────────────────────────

class GapFillThread:
    """The collector's background gap-filler: one GapFiller run every interval.

    Never raises into the collector, never blocks the writer loop (its own
    thread, its own InfluxDB client and portal session per run), never
    overlaps another run, and stops at the next pause or request when the
    collector stops (a daemon thread: an HTTP request in flight never delays
    the process exit). Writes nothing but InfluxDB.
    """

    def __init__(self, cfg: CollectorConfig, model: str, settings: Optional[GapFillSettings] = None,
                 startup_delay: float = DEFAULT_STARTUP_DELAY_SEC,
                 portal_blocked: Optional[Callable[[], Optional[str]]] = None,
                 target_factory: Callable[[OutputConfig], InfluxTarget] = InfluxTarget,
                 client_factory: Callable[..., ExportClient] = ExportClient,
                 tz_loader: Optional[Callable[[], Optional[str]]] = None,
                 clock: Callable[[], float] = time.time,
                 lock_file: Optional[str] = LOCK_FILE):
        self.cfg = cfg
        self.model = model
        self.settings = settings or settings_from_env()
        self.startup_delay = max(0.0, float(startup_delay))
        self._portal_blocked = portal_blocked
        self._target_factory = target_factory
        self._client_factory = client_factory
        self._tz_loader = tz_loader or (lambda: _load_db_setting("timezone", cfg))
        self._clock = clock
        self._lock_file = lock_file
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._auth_paused_until = 0.0
        self.runs = 0
        self.last_report: Optional[GapFillReport] = None

    # Lifecycle
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        o = self.settings.options
        logger.info("Automatic gap-filling on: first check in %.0f min, then every %.0f min "
                    "(last %d days, gaps over %g min)", self.startup_delay / 60,
                    self.settings.interval_sec / 60, o.lookback_days, o.min_gap_sec / 60)
        self._thread = threading.Thread(target=self._run, name="lux-gapfill", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = STOP_JOIN_SEC) -> None:
        """Ask the thread to stop and wait up to `timeout` s for it."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        try:
            if self._stop.wait(self.startup_delay):
                return
            while not self._stop.is_set():
                self.run_once()
                if self._stop.wait(min(self.settings.interval_sec, threading.TIMEOUT_MAX)):
                    break
        finally:
            logger.info("Gap-fill thread exiting")

    # One run
    def run_once(self) -> Optional[GapFillReport]:
        """One guarded run. Never raises; returns the report (None when skipped or failed)."""
        try:
            with exclusive_run(self._lock_file) as alone:
                if not alone:
                    logger.info("Another gap-fill run is in progress; skipping this one")
                    return None
                self.runs += 1
                report = self._run_filler()
                self.last_report = report
                return report
        except GapFillStopped:
            logger.info("Gap-fill run stopped: the collector is shutting down")
        except CloudAuthError as exc:
            self._auth_paused_until = self._clock() + AUTH_PAUSE_SEC
            logger.warning("EG4 portal rejected the gap-fill login (%s); no gap-fill downloads for %.0f h. "
                           "Check LUX_CLOUD_USERNAME/LUX_CLOUD_PASSWORD", exc, AUTH_PAUSE_SEC / 3600)
        except (BackfillError, CloudApiError, CloudTransientError, CloudSessionExpired) as exc:
            logger.warning("Gap-fill run failed: %s", exc)
        except Exception:
            logger.exception("Gap-fill run failed unexpectedly")
        return None

    def _run_filler(self) -> Optional[GapFillReport]:
        cfg = self.cfg
        out = cfg.outputs
        if not out.influx_enabled:
            logger.debug("InfluxDB output is off: nothing to gap-fill")
            return None
        if not cfg.inverter_serial:
            logger.info("No inverter serial set: gap-filling skipped")
            return None
        ctx = MapContext(model=self.model, driver=get_driver(self.model),
                         temperature_unit=str(out.temperature_unit or "celsius").lower(), tz=self._timezone())
        target = self._target_factory(out)
        try:
            options = replace(self.settings.options,
                              reading_age_fallback_sec=max_reading_age(cfg.cloud_poll_interval))
            filler = GapFiller(
                ctx, target, client_factory=self._new_client, options=options,
                dry_run=False, serial=cfg.inverter_serial, tz_explicit=False,
                portal_blocked=self._blocked, stop=self._stop, clock=self._clock,
            )
            report = filler.run()
        finally:
            target.close()
        self._log_summary(report)
        return report

    def _timezone(self) -> tzinfo:
        """The plant time zone (the DB timezone setting, read-only); verified against the inverter clock later."""
        try:
            name = self._tz_loader()
        except Exception as exc:
            logger.debug("Could not read the timezone setting (%s)", type(exc).__name__)
            name = None
        name = name or DEFAULTS["timezone"]
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise BackfillError(f"unknown time zone {name!r} in the timezone setting") from exc

    def _blocked(self) -> Optional[str]:
        now = self._clock()
        if now < self._auth_paused_until:
            return f"the EG4 portal rejected the last login; paused until {_iso_utc(self._auth_paused_until)[:19]}Z"
        if self._portal_blocked is not None:
            return self._portal_blocked()
        return None

    def _new_client(self) -> ExportClient:
        """A new ExportClient (its own session) whose waits end at once when the collector stops."""
        cfg = self.cfg
        try:
            return self._client_factory(
                base_url=cfg.cloud_base_url, username=cfg.cloud_username, password=cfg.cloud_password,
                inverter_serial=cfg.inverter_serial, model=self.model,
                request_delay=DEFAULT_REQUEST_DELAY, sleep=self._sleep,
            )
        except ValueError as exc:
            raise BackfillError(str(exc)) from exc

    def _sleep(self, seconds: float) -> None:
        if self._stop.wait(max(0.0, seconds)):
            raise GapFillStopped("collector stopping")

    def _log_summary(self, report: GapFillReport) -> None:
        if report.downloads or report.count(action=GIVE_UP):
            logger.info(
                "Gap-fill run %s: %d gaps (%d filled, %d without export rows, %d waiting, %d given up, "
                "%d deferred), %d downloads, %d points written",
                report.run_id, len(report.gaps), report.count(status=FILLED), report.count(status=EMPTY),
                report.count(action=WAIT), report.count(action=GIVE_UP) + report.count(action=GIVEN_UP),
                report.count(action=DEFERRED), report.downloads, report.points,
            )
        elif report.gaps:
            logger.debug("Gap-fill run %s: %d gaps, none due", report.run_id, len(report.gaps))
