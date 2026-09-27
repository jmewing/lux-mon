"""EG4 cloud transport: read-only polling of the EG4 monitoring portal over HTTPS.

Some EG4 WiFi dongles (e.g. the "E Wi-Fi ENC" firmware) refuse every local TCP
connection, so the only copy of the inverter's data is the one the dongle
uploads to the vendor cloud. This transport logs in to
monitor.eg4electronics.com, polls the runtime / energy / battery endpoints,
and converts the JSON into synthetic READ_INPUT LuxFrames so the existing
decode, clamp and outputs path is reused unchanged.

Read-only by construction: only the login endpoint and three read endpoints
are ever requested (see _ALLOWED_PATHS). remoteRead / remoteSet /
web/maintain are never called, and nothing is written to the inverter.

Registers are only emitted when the cloud actually reports them; a value the
portal does not provide is omitted (never zero-filled), and the collector's
writer only writes a snapshot once per new portal upload (see data_seq and
live_registers).
"""

import hashlib
import json
import logging
import math
import random
import re
import time
import traceback
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple
from urllib.parse import quote, quote_plus, urlparse

import requests

from . import BaseTransport
from ..protocol import LuxFrame, MODBUS_READ_INPUT, TCP_FUNC_TRANSLATED_DATA
from ..registers import BATTERY_BLOCK_SIZE, BATTERY_COUNT, BATTERY_START

logger = logging.getLogger("luxmon.comm.cloud_http")


# ── Endpoints ───────────────────────────────────────────────────────────────
LOGIN_PATH = "/WManage/api/login"
RUNTIME_PATH = "/WManage/api/inverter/getInverterRuntime"
ENERGY_PATH = "/WManage/api/inverter/getInverterEnergyInfo"
BATTERY_PATH = "/WManage/api/battery/getBatteryInfo"

# Defense in depth: _post() refuses anything else, so remoteRead / remoteSet /
# web/maintain (which relay through the dongle or write settings) can never be
# requested, whatever a caller passes.
_ALLOWED_PATHS = frozenset({LOGIN_PATH, RUNTIME_PATH, ENERGY_PATH, BATTERY_PATH})

# ── Polling ─────────────────────────────────────────────────────────────────
DEFAULT_BASE_URL = "https://monitor.eg4electronics.com"
MIN_POLL_SEC = 30          # same floor as pylxpweb; the portal updates every ~20 s - 5 min
DEFAULT_POLL_SEC = 60
DEFAULT_ENERGY_SEC = 300
MAX_ENERGY_SEC = 3600
# The first upload seen after a start is only emitted when it is at most
# 3 poll intervals + this many seconds old; an older one is the portal's
# frozen mirror of a dongle that stopped uploading, not live data.
FIRST_UPLOAD_SLACK_SEC = 300

# ── Backoff / circuit breaker ───────────────────────────────────────────────
# A failed poll is retried quickly a few times (a blip within one poll cycle),
# then in poll-interval steps (x1, x2, x4 ...) capped at BREAKER_PAUSE_SEC, so
# an outage never stops polling for longer than that. The cap stays well below
# the freeze-check's 900 s freshness limit for cloud_http.
BACKOFF_BASE = 1.0
BACKOFF_JITTER = 0.1
QUICK_RETRIES = 3          # fast retries after 1, 2 and 4 s
BREAKER_PAUSE_SEC = 300    # longest wait between polls while the portal keeps failing
# A rejected login is retried after 15 min, 1 h, 6 h, then daily; only a
# successful login resets this, so a wrong or changed password is not replayed
# every few minutes (account lockout risk).
AUTH_PAUSE_STEPS = (900, 3600, 21600, 86400)

# ── Session / HTTP ──────────────────────────────────────────────────────────
SESSION_MAX_AGE_SEC = 7200  # proactive re-login (the server publishes no TTL)
HTTP_TIMEOUT = (10, 30)     # (connect, read) seconds

# Energy / battery groups drop out of live_registers after this many energy
# intervals without a successful fetch.
GROUP_MAX_AGE_FACTOR = 3

# today* energy counters, which reset at the plant's local midnight.
_TODAY_REGS = frozenset({28, 33, 34, 36, 37, 171})
# Fast-changing getBatteryInfo values (the signed bank current): published
# only with the snapshot of the poll cycle that fetched them, never carried
# over like the slow battery data (cells, SOH, module blocks).
_CYCLE_ONLY_REGS = frozenset({98})

# ── Error classification (success:false messages) ───────────────────────────
_TRANSIENT_MARKERS = ("DATAFRAME_TIMEOUT", "TIMEOUT", "BUSY", "COMMUNICATION_ERROR", "DEVICE_BUSY")
_SESSION_MARKERS = ("login", "session", "unauthor", "not log")

_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "User-Agent": "lux-mon cloud_http",
}

_JSESSION_RE = re.compile(r"JSESSIONID=[^;\s]+")

# ── Model quirks (keyed by driver name) ─────────────────────────────────────
# SNA-family hardware has 2 MPPTs: the cloud always sends vpv3/ppv3 = 0 (or
# noise), so PV3 registers are never emitted for these models.
_SNA_FAMILY = frozenset({
    "eg4_6000xp", "luxpower_sna", "eg4_12000xp", "eg4_6500ex",
    "eg4_3000ehv", "lxp_6k", "bigbattery_sna_6k",
})
# The 12000XP has no battery temperature sensor in the cloud feed: tBat is a
# constant 0 there, which would read as a real 0 °C. Its tinner (inverter
# temperature) is a constant 0 too.
_TBAT_ZERO_UNKNOWN = frozenset({"eg4_12000xp"})
_TINNER_ZERO_UNKNOWN = frozenset({"eg4_12000xp"})


def _model_quirks(model: str) -> Dict[str, Any]:
    """Return the cloud-mapping quirks for a driver name."""
    return {
        "pv_strings": 2 if model in _SNA_FAMILY else 3,
        "tbat_zero_unknown": model in _TBAT_ZERO_UNKNOWN,
        "tinner_zero_unknown": model in _TINNER_ZERO_UNKNOWN,
    }


# ── Exceptions ──────────────────────────────────────────────────────────────
class CloudAuthError(Exception):
    """The portal rejected the credentials (never retried quickly: lockout risk)."""


class CloudSessionExpired(Exception):
    """The session was rejected even after one re-login."""


class CloudTransientError(Exception):
    """A retryable failure (5xx, 429, redirect, portal busy/timeout)."""


class CloudRateLimited(CloudTransientError):
    """HTTP 429: retried in poll-interval steps, never with the fast retries."""


class CloudApiError(Exception):
    """A non-retryable application error (e.g. apiBlocked, HTTP 403/4xx)."""


# ── Pure conversion helpers ─────────────────────────────────────────────────

def _num(d: Optional[dict], key: str) -> Optional[float]:
    """Return d[key] as a finite float, or None.

    Accepts int, float or a numeric string (several cloud fields arrive as
    strings, e.g. batCapacity "628"). Rejects bool, None, '', non-numeric
    strings such as the 12000XP's pf "[0]", NaN and infinity.
    """
    if not isinstance(d, dict):
        return None
    v = d.get(key)
    if v is None or isinstance(v, bool):
        return None
    try:
        if isinstance(v, (int, float)):
            f = float(v)
        elif isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            f = float(s)
        else:
            return None
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _put16(regs: Dict[int, int], reg: int, value: Optional[float], signed: bool = False) -> None:
    """Store a 16-bit register value, omitting it when out of range.

    Unsigned: 0..0xFFFF. Signed: -32768..32767, stored as two's complement.
    """
    if value is None:
        return
    if signed:
        if -0x8000 <= value <= 0x7FFF:
            regs[reg] = int(value) & 0xFFFF
    elif 0 <= value <= 0xFFFF:
        regs[reg] = int(value)


def _put32(regs: Dict[int, int], reg: int, value: Optional[float]) -> None:
    """Store a 32-bit value as a low/high word pair (both words or neither).

    decode_registers drops a pair whose high word is missing, so the two
    words are always written together.
    """
    if value is None or value < 0 or value > 0xFFFFFFFF:
        return
    v = int(value)
    regs[reg] = v & 0xFFFF
    regs[reg + 1] = (v >> 16) & 0xFFFF


def _str_or_none(value: Any) -> Optional[str]:
    """Return a non-empty string, else None."""
    if isinstance(value, str) and value != "":
        return value
    return None


def _parse_server_time(value: Any) -> Optional[float]:
    """Parse a portal 'YYYY-MM-DD HH:MM:SS' timestamp as UTC epoch seconds."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc).timestamp()


def _date_part(value: Any) -> Optional[str]:
    """Return the 'YYYY-MM-DD' part of a portal timestamp, or None."""
    if isinstance(value, str) and len(value) >= 10:
        return value[:10]
    return None


def runtime_to_registers(rt: dict, model: str) -> Dict[int, int]:
    """Map a getInverterRuntime payload to input registers.

    Cloud values use the same raw units as the registers (0.1 V, 0.01 Hz,
    W, °C), so most fields pass through unchanged. Register 5 carries SOC in
    the low byte only; the SOH high byte is added by the transport when a
    battery poll has provided it.
    """
    quirks = _model_quirks(model)
    three_mppt = quirks["pv_strings"] >= 3
    regs: Dict[int, int] = {}

    def put(reg: int, key: str) -> None:
        _put16(regs, reg, _num(rt, key))

    put(0, "status")
    put(1, "vpv1")
    put(2, "vpv2")
    if three_mppt:
        put(3, "vpv3")
    put(4, "vBat")
    soc = _num(rt, "soc")
    if soc is not None and 0 <= soc <= 0xFF:
        regs[5] = int(soc) & 0xFF
    put(7, "ppv1")
    put(8, "ppv2")
    if three_mppt:
        put(9, "ppv3")
    put(10, "pCharge")
    put(11, "pDisCharge")
    put(12, "vacr")
    put(15, "fac")
    put(16, "pinv")
    put(17, "prec")

    # Power factor arrives as a string; the 12000XP sends "[0]" (omitted).
    pf = _num(rt, "pf")
    if pf is not None and 0 < pf <= 1:
        regs[19] = round(pf * 1000)

    put(20, "vepsr")
    put(23, "feps")
    put(24, "peps")   # combined backup output (EPS loads + smart-load port)
    put(25, "seps")
    put(26, "pToGrid")
    put(27, "pToUser")
    put(38, "vBus1")
    put(39, "vBus2")

    # Temperatures: unsigned defs, so negatives are omitted by _put16.
    t1 = _num(rt, "tradiator1")
    t2 = _num(rt, "tradiator2")
    tinner = _num(rt, "tinner")
    # tinner is a constant 0 on some firmware (the 12000XP) while the
    # radiators read real temperatures: that 0 means "unknown". Known models
    # drop it always; otherwise any non-zero radiator reading (including a
    # sub-zero one in winter) marks it as fake.
    radiator_live = any(t is not None and t != 0 for t in (t1, t2))
    if tinner is not None and not (tinner == 0 and (quirks["tinner_zero_unknown"] or radiator_live)):
        _put16(regs, 64, tinner)
    _put16(regs, 65, t1)
    _put16(regs, 66, t2)
    tbat = _num(rt, "tBat")
    if tbat is not None and tbat != 127 and not (tbat == 0 and quirks["tbat_zero_unknown"]):
        # 127 is the "no sensor" sentinel.
        _put16(regs, 67, tbat)

    # BMS current limits. *Value is whole amps; the raw keys are 0.1 A.
    # lux-mon's registers 81/82 are 0.01 A. Omitted (never clamped) if absent
    # or too large for a 16-bit register.
    for reg, value_key, raw_key in (
        (81, "maxChgCurrValue", "maxChgCurr"),
        (82, "maxDischgCurrValue", "maxDischgCurr"),
    ):
        amps = _num(rt, value_key)
        if amps is None:
            raw_amps = _num(rt, raw_key)
            amps = raw_amps / 10 if raw_amps is not None else None
        if amps is not None:
            _put16(regs, reg, round(amps * 100))

    count = _num(rt, "batParallelNum")
    if count is not None and count > 0:
        _put16(regs, 96, count)
    capacity = _num(rt, "batCapacity")
    if capacity is None or capacity == 0:
        capacity = _num(rt, "batteryCapacity")
    if capacity is not None and capacity > 0:
        _put16(regs, 97, capacity)

    put(121, "genVolt")
    put(122, "genFreq")
    if rt.get("haspEpsLNValue") is True:
        put(129, "pEpsL1N")
        put(130, "pEpsL2N")
    return regs


def energy_to_registers(en: dict) -> Dict[int, int]:
    """Map a getInverterEnergyInfo payload (0.1 kWh units) to input registers.

    The cloud has no per-string PV energy: the PV total goes in the pv1 slot
    (regs 28 and 40/41) and the pv2/pv3 slots are not emitted.
    """
    regs: Dict[int, int] = {}
    for reg, key in (  # today* counters (_TODAY_REGS)
        (28, "todayYielding"),
        (33, "todayCharging"),
        (34, "todayDischarging"),
        (36, "todayExport"),
        (37, "todayImport"),
        (171, "todayUsage"),
    ):
        _put16(regs, reg, _num(en, key))
    for reg, key in (
        (40, "totalYielding"),
        (50, "totalCharging"),
        (52, "totalDischarging"),
        (56, "totalExport"),
        (58, "totalImport"),
        (172, "totalUsage"),
    ):
        _put32(regs, reg, _num(en, key))
    return regs


def _module_values(mods: List[dict], key: str) -> List[float]:
    """Return the numeric values of key across modules, skipping missing ones."""
    return [v for v in (_num(m, key) for m in mods) if v is not None]


def _parse_fw(text: Any) -> Optional[int]:
    """Parse a module firmware string 'M.m' into (M << 8) | m."""
    if not isinstance(text, str):
        return None
    parts = text.strip().split(".")
    if len(parts) != 2:
        return None
    try:
        major, minor = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= major <= 0xFF and 0 <= minor <= 0xFF):
        return None
    return (major << 8) | minor


def battery_to_registers(bat: dict) -> Tuple[Dict[int, int], Optional[int]]:
    """Map a getBatteryInfo payload to input registers.

    Returns (registers, soh). Bank-level cell data and the per-battery 5000+
    blocks only come from a module array; units without one (the 12000XP
    case) get register 98 at most, and soh None. A module whose SOH is
    unknown gets 0 in the SOH byte of base+10 (packed with its SOC);
    /api/batteries reports that 0 as unknown.
    """
    regs: Dict[int, int] = {}

    # Register 98: bank current, signed 0.1 A (negative = discharging).
    mag = _num(bat, "currentText")
    if mag is not None:
        if mag == 0:
            _put16(regs, 98, 0, signed=True)
        else:
            current_type = str(bat.get("currentType") or "").lower()
            sign: Optional[int] = None
            if current_type == "charge":
                sign = 1
            elif current_type == "discharge":
                sign = -1
            else:
                bat_power = _num(bat, "batPower")
                if bat_power:
                    sign = 1 if bat_power > 0 else -1
            if sign is not None:
                _put16(regs, 98, int(round(abs(mag) * 10)) * sign, signed=True)

    array = bat.get("batteryArray")
    mods = [
        m for m in (array if isinstance(array, list) else [])
        if isinstance(m, dict) and m.get("lost") is not True
    ]
    if not mods:
        return regs, None

    soh_values = [v for v in _module_values(mods, "soh") if 0 <= v <= 0xFF]
    soh = int(min(soh_values)) if soh_values else None

    def agg(reg: int, key: str, fn: Callable, signed: bool = False) -> None:
        values = _module_values(mods, key)
        if values:
            _put16(regs, reg, fn(values), signed=signed)

    agg(83, "batChargeVoltRef", max)
    agg(101, "batMaxCellVoltage", max)
    agg(102, "batMinCellVoltage", min)
    agg(103, "batMaxCellTemp", max, signed=True)
    agg(104, "batMinCellTemp", min, signed=True)
    agg(106, "cycleCnt", max)

    for m in mods:
        idx = _num(m, "batIndex")
        if idx is None or idx != int(idx) or not 0 <= idx < BATTERY_COUNT:
            continue
        base = BATTERY_START + BATTERY_BLOCK_SIZE * int(idx)
        _put16(regs, base + 3, _num(m, "currentFullCapacity"))
        _put16(regs, base + 5, _num(m, "batChargeMaxCur"))
        # base+6 (max discharge current) has no cloud field.
        _put16(regs, base + 8, _num(m, "totalVoltage"))
        _put16(regs, base + 9, _num(m, "current"), signed=True)
        m_soc = _num(m, "soc")
        m_soh = _num(m, "soh")
        if m_soc is not None and 0 <= m_soc <= 0xFF:
            high = int(m_soh) & 0xFF if m_soh is not None and 0 <= m_soh <= 0xFF else 0
            regs[base + 10] = (high << 8) | (int(m_soc) & 0xFF)
        _put16(regs, base + 11, _num(m, "cycleCnt"))
        # Unsigned defs: negative cell temperatures are omitted by _put16.
        _put16(regs, base + 12, _num(m, "batMaxCellTemp"))
        _put16(regs, base + 13, _num(m, "batMinCellTemp"))
        _put16(regs, base + 14, _num(m, "batMaxCellVoltage"))
        _put16(regs, base + 15, _num(m, "batMinCellVoltage"))
        for offset, hi_key, lo_key in (
            (16, "batMaxCellNumTemp", "batMinCellNumTemp"),
            (17, "batMaxCellNumVolt", "batMinCellNumVolt"),
        ):
            hi = _num(m, hi_key)
            lo = _num(m, lo_key)
            if hi is not None and lo is not None and hi >= 0 and lo >= 0:
                regs[base + offset] = ((int(hi) & 0xFF) << 8) | (int(lo) & 0xFF)
        fw = _parse_fw(m.get("fwVersionText"))
        if fw is not None:
            regs[base + 18] = fw
        sn = m.get("batterySn")
        if isinstance(sn, str) and sn.strip():
            b = sn.encode("ascii", "replace")[:28].ljust(28, b"\0")
            for k in range(14):
                regs[base + 19 + k] = (b[2 * k] << 8) | b[2 * k + 1]
    return regs, soh


def registers_to_frames(regs: Dict[int, int], inverter_serial: str) -> List[LuxFrame]:
    """Split registers into contiguous runs, one READ_INPUT frame per run.

    Gaps are never filled: a register the cloud did not report is simply
    absent from every frame.
    """
    frames: List[LuxFrame] = []
    start: Optional[int] = None
    values: List[int] = []

    def flush() -> None:
        if start is not None:
            frames.append(LuxFrame(
                protocol=0,
                tcp_function=TCP_FUNC_TRANSLATED_DATA,
                datalog_serial="",
                raw=b"",
                device_function=MODBUS_READ_INPUT,
                inverter_serial=inverter_serial,
                register=start,
                values=list(values),
            ))

    for reg in sorted(regs):
        if start is not None and reg == start + len(values):
            values.append(regs[reg])
        else:
            flush()
            start, values = reg, [regs[reg]]
    flush()
    return frames


def _flagged(rt: dict, key: str, flag: str) -> Optional[float]:
    """Return rt[key] only when the payload's display flag is true, else None."""
    return _num(rt, key) if rt.get(flag) is True else None


def cloud_extras(rt: dict, bat: Optional[dict]) -> Dict[str, Any]:
    """Return the cloud-only fields for the luxmon_cloud Influx measurement.

    The load split and consumption values are placeholder 0s unless the
    portal flags them as shown (the 12000XP sends hideConsumption=true and
    *Show=false); those are returned as None so the field is left out.
    bat is the battery payload fetched in this cycle (None if there is none).
    """
    hide_consumption = rt.get("hideConsumption") is True
    extras: Dict[str, Any] = {
        "status_text": _str_or_none(rt.get("statusText")),
        "fw_code": _str_or_none(rt.get("fwCode")),
        "ppv_total": _num(rt, "ppv"),
        "bat_power": _num(rt, "batPower"),
        "smart_load_power": _flagged(rt, "smartLoadPower", "smartLoadInverterEnable"),
        "eps_load_power": _flagged(rt, "epsLoadPower", "epsLoadPowerShow"),
        "grid_load_power": _flagged(rt, "gridLoadPower", "gridLoadPowerShow"),
        "consumption_power": None if hide_consumption else _num(rt, "consumptionPower"),
        "ac_couple_power": _num(rt, "acCouplePower"),
        "bat_status": None,
        "remaining_ah": None,
    }
    if isinstance(bat, dict):
        extras["bat_status"] = _str_or_none(bat.get("batStatus"))
        remaining = _num(bat, "currentBatteryCharge")
        extras["remaining_ah"] = round(remaining, 2) if remaining is not None else None
    return extras


# ── Transport ───────────────────────────────────────────────────────────────

class CloudHttpTransport(BaseTransport):
    """
    Read-only EG4 cloud polling transport.

    Logs in to the EG4 monitoring portal and polls the inverter runtime every
    poll_interval seconds; each new upload also fetches the battery info, and
    the energy counters every energy_interval. Each new portal upload (a
    later serverTime) is emitted once as synthetic READ_INPUT frames and bumps
    data_seq; unchanged, older or offline payloads are skipped so the
    collector never re-stores stale values as fresh.
    """

    def __init__(
        self,
        on_frame: Callable[[LuxFrame], None],
        base_url: str,
        username: str,
        password: str,
        inverter_serial: str,
        model: str = "",
        poll_interval: float = DEFAULT_POLL_SEC,
        energy_interval: float = DEFAULT_ENERGY_SEC,
        session_factory: Callable[[], Any] = requests.Session,
        clock: Callable[[], float] = time.time,
        rand: Callable[[], float] = random.random,
    ):
        super().__init__(on_frame)
        base = (base_url or "").strip().rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("LUX_CLOUD_BASE_URL must be an https:// URL")
        if not username:
            raise ValueError("LUX_CLOUD_USERNAME is required when transport=cloud_http")
        if not password:
            raise ValueError("LUX_CLOUD_PASSWORD is required when transport=cloud_http")
        if not inverter_serial:
            raise ValueError(
                "LUX_INVERTER_SERIAL (or the inverter_serial setting) is required "
                "when transport=cloud_http"
            )

        self.base_url = base
        self.inverter_serial = inverter_serial
        self.model = model or ""
        self._username = username
        self._password = password

        poll = float(poll_interval)
        if not math.isfinite(poll) or poll < MIN_POLL_SEC:
            logger.warning(
                "EG4 cloud poll interval %ss is below the %ss minimum; using %ss",
                poll_interval, MIN_POLL_SEC, MIN_POLL_SEC,
            )
            poll = float(MIN_POLL_SEC)
        self.poll_interval = poll
        self._first_upload_max_age = 3 * poll + FIRST_UPLOAD_SLACK_SEC
        energy = float(energy_interval)
        if not math.isfinite(energy):
            energy = float(DEFAULT_ENERGY_SEC)
        self.energy_interval = min(max(energy, poll), float(MAX_ENERGY_SEC))

        self._session_factory = session_factory
        self._clock = clock
        self._rand = rand

        # Session / login
        self._session: Any = None
        self._logged_in_at = 0.0
        self._serial_checked = False

        # Freshness
        self._last_fresh_key: Optional[str] = None
        self._last_energy_at = 0.0
        self._last_energy_day: Optional[str] = None
        self._last_server_time: Optional[str] = None
        self._last_server_epoch: Optional[float] = None
        self._offline = False
        self._vbat_warned = False

        # Battery
        self._soh: Optional[int] = None

        # Register groups: name -> (frozenset of register numbers, emit time)
        self._groups: Dict[str, Tuple[FrozenSet[int], float]] = {}
        # Registers valid for the latest emit only (_CYCLE_ONLY_REGS fetched in it).
        self._cycle_regs: FrozenSet[int] = frozenset()

        # Emission / output
        self._emit_lock = Lock()
        self._data_seq = 0
        self._data_time: Optional[float] = None
        self._meta: Optional[Dict[str, Any]] = None

        # Counters
        self._polls = 0
        self._fresh = 0
        self._stale_skips = 0
        self._lost_skips = 0
        self._errors = 0
        self._relogins = 0
        self._frames_emitted = 0

        # Failure handling
        self._consecutive_failures = 0
        self._breaker_until = 0.0
        self._breaker_logged = False
        self._auth_failures = 0
        self._group_failures: Dict[str, int] = {}
        self._last_error = ""
        self._last_success = 0.0

        # Thread control
        self._thread: Optional[Thread] = None
        self._stop = Event()

    def __repr__(self) -> str:
        return f"CloudHttpTransport(base_url={self.base_url!r}, serial={self.inverter_serial!r})"

    # ── Lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        logger.info(
            "Starting EG4 cloud transport (%s, serial %s, poll %ss, energy %ss)",
            self.base_url, self.inverter_serial, self.poll_interval, self.energy_interval,
        )
        self._running = True
        self._stop.clear()
        self._thread = Thread(target=self._run, name="lux-cloud-http", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        logger.info("Stopping EG4 cloud transport")
        self._running = False
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        try:
            if self._session is not None:
                self._session.close()
        except Exception:
            pass

    def stats(self) -> dict:
        # Never include the username, password or session cookie.
        return {
            "type": "cloud_http",
            "base_url": self.base_url,
            "serial": self.inverter_serial,
            "logged_in": self._session is not None and self._logged_in_at > 0,
            "polls": self._polls,
            "fresh_updates": self._fresh,
            "stale_skips": self._stale_skips,
            "lost_skips": self._lost_skips,
            "errors": self._errors,
            "relogins": self._relogins,
            "frames_emitted": self._frames_emitted,
            "consecutive_failures": self._consecutive_failures,
            "breaker_open_until": self._breaker_until,
            # True while the portal keeps rejecting the login (polling is
            # paused with an escalating delay; see AUTH_PAUSE_STEPS).
            "auth_failed": self._auth_failures > 0,
            "auth_failures": self._auth_failures,
            "last_server_time": self._last_server_time,
            "last_success": self._last_success,
            "last_error": self._last_error,
            "data_seq": self._data_seq,
        }

    # ── Collector-facing state ───────────────────────────────────────────
    @property
    def data_seq(self) -> int:
        """Incremented once per new portal upload (0 until the first one)."""
        return self._data_seq

    @property
    def data_time(self) -> Optional[float]:
        """Portal upload time (serverTime, UTC epoch) of the latest emit, if known."""
        return self._data_time

    @property
    def emit_lock(self) -> Lock:
        """Held while a cycle's frames are emitted; the writer snapshots under it."""
        return self._emit_lock

    @property
    def live_registers(self) -> FrozenSet[int]:
        """Registers the cloud currently reports.

        The runtime group is always live; energy / battery groups are live
        only while younger than GROUP_MAX_AGE_FACTOR energy intervals, so a
        register the cloud stops reporting drops out of snapshots instead of
        being rewritten stale forever. Cycle-only registers (the battery
        current) are live only if the latest emit fetched them.
        """
        now = self._clock()
        max_age = GROUP_MAX_AGE_FACTOR * self.energy_interval
        live: set = set(self._cycle_regs)
        for name, (keys, ts) in list(self._groups.items()):
            if name == "runtime" or now - ts < max_age:
                live |= keys
        return frozenset(live)

    @property
    def suppressed_fields(self) -> FrozenSet[str]:
        """Decoded fields the writer must drop (SOH is unknown without modules)."""
        return frozenset({"soh"}) if self._soh is None else frozenset()

    def source_meta(self) -> Optional[Dict[str, Any]]:
        """Metadata for the luxmon_cloud measurement of the latest emit."""
        return dict(self._meta) if self._meta is not None else None

    # ── Poll loop ────────────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self._run_cycle())

    def _run_cycle(self) -> float:
        """Run one poll (unless the breaker is open); return the delay until the next."""
        now = self._clock()
        if self._breaker_until:
            if now < self._breaker_until:
                return self._breaker_until - now
            logger.info("EG4 cloud: pause over, resuming polls")
            self._breaker_until = 0.0

        try:
            self.poll_once()
        except CloudAuthError as exc:
            return self._record_auth_failure(exc)
        except (requests.RequestException, CloudSessionExpired, CloudTransientError,
                CloudApiError, ValueError) as exc:
            return self._record_failure(exc)
        except Exception as exc:  # the poll thread must never die
            logger.error("cloud poll crashed: %s", self._redact(f"{type(exc).__name__}: {exc}"))
            logger.debug("cloud poll traceback: %s", self._redact(traceback.format_exc(), limit=4000))
            return self._record_failure(exc, logged=True)

        if self._consecutive_failures:
            logger.info("EG4 cloud polls recovered after %d failures", self._consecutive_failures)
        self._consecutive_failures = 0
        self._breaker_logged = False
        return self.poll_interval

    def _backoff_base(self, failures: int, slow: bool = False) -> float:
        """Delay before the next attempt after `failures` failed polls in a row.

        The first QUICK_RETRIES retries are fast (1, 2, 4 s) to ride out a
        blip; later ones wait poll-interval steps (x1, x2, x4 ...) capped at
        BREAKER_PAUSE_SEC. After a rate limit (slow) there are no fast
        retries: every failure in the streak counts as a step.
        """
        failures = max(1, failures)
        if slow:
            step = failures
        elif failures <= QUICK_RETRIES:
            return BACKOFF_BASE * 2 ** (failures - 1)
        else:
            step = failures - QUICK_RETRIES
        return min(float(BREAKER_PAUSE_SEC), self.poll_interval * 2 ** min(step - 1, 16))

    def _backoff_delay(self, failures: int, slow: bool = False) -> float:
        """_backoff_base with ±BACKOFF_JITTER jitter."""
        delay = self._backoff_base(failures, slow)
        return delay * (1 + BACKOFF_JITTER * (2 * self._rand() - 1))

    def _record_failure(self, exc: BaseException, logged: bool = False) -> float:
        """Count a failed poll and return the delay until the next attempt."""
        self._consecutive_failures += 1
        self._errors += 1
        self._last_error = self._redact(f"{type(exc).__name__}: {exc}")
        slow = isinstance(exc, CloudRateLimited)
        delay = self._backoff_delay(self._consecutive_failures, slow)
        if not logged:
            logger.warning(
                "EG4 cloud poll failed (%d in a row): %s; retrying in %.0fs",
                self._consecutive_failures, self._last_error, delay,
            )
        if self._backoff_base(self._consecutive_failures, slow) >= BREAKER_PAUSE_SEC:
            self._breaker_until = self._clock() + delay
            if not self._breaker_logged:
                self._breaker_logged = True
                logger.warning(
                    "EG4 cloud: still failing after %d attempts; polling every %d min until it recovers",
                    self._consecutive_failures, BREAKER_PAUSE_SEC // 60,
                )
        return delay

    def _record_auth_failure(self, exc: CloudAuthError) -> float:
        """Pause after a rejected login, longer after each rejection in a row."""
        self._errors += 1
        self._auth_failures += 1
        self._consecutive_failures = 0
        self._last_error = self._redact(f"{type(exc).__name__}: {exc}")
        pause = float(AUTH_PAUSE_STEPS[min(self._auth_failures, len(AUTH_PAUSE_STEPS)) - 1])
        self._breaker_until = self._clock() + pause
        # "EG4 cloud rejected the login" is matched by scripts/lux-mon-freeze-check.sh.
        logger.error(
            "EG4 cloud rejected the login (%s; %d in a row); next attempt in %s - check "
            "LUX_CLOUD_USERNAME/LUX_CLOUD_PASSWORD, then recreate the collector",
            self._redact(str(exc)), self._auth_failures,
            f"{pause / 3600:.0f} h" if pause >= 3600 else f"{pause / 60:.0f} min",
        )
        return pause

    def poll_once(self) -> bool:
        """Run one full poll cycle. Returns True when new data was emitted."""
        now = self._clock()
        if self._session is None or not self._logged_in_at or now - self._logged_in_at >= SESSION_MAX_AGE_SEC:
            self._login()

        rt = self._call(RUNTIME_PATH)
        self._polls += 1
        self._last_success = now

        # Gate: a lost / empty payload is a frozen mirror, never fresh data.
        if rt.get("success") is False or rt.get("lost") is True or rt.get("hasRuntimeData") is False:
            self._lost_skips += 1
            if not self._offline:
                self._offline = True
                logger.info(
                    "EG4 cloud reports no live data for %s (lost=%s, hasRuntimeData=%s, last upload %s); "
                    "skipping until it uploads again",
                    self.inverter_serial, rt.get("lost"), rt.get("hasRuntimeData"), rt.get("serverTime"),
                )
            return False
        if _num(rt, "vBat") is None:
            if not self._vbat_warned:
                self._vbat_warned = True
                logger.warning("EG4 cloud runtime payload has no battery voltage (vBat); skipping it")
            return False

        regs_rt = runtime_to_registers(rt, self.model)

        # Freshness: a new upload only if serverTime changed to a later time.
        server_time = _str_or_none(rt.get("serverTime"))
        server_epoch = _parse_server_time(server_time)
        data_age = round(now - server_epoch, 1) if server_epoch is not None else None
        key = server_time or _str_or_none(rt.get("deviceTime"))
        if key is None:
            blob = json.dumps(sorted(regs_rt.items()))
            key = "sha1:" + hashlib.sha1(blob.encode()).hexdigest()
        if key == self._last_fresh_key:
            self._stale_skips += 1
            logger.debug("EG4 cloud data unchanged (serverTime %s)", key)
            return False
        if (server_epoch is not None and self._last_server_epoch is not None
                and server_epoch <= self._last_server_epoch):
            # An older upload served again (e.g. by a lagging portal node) is
            # never new data.
            self._stale_skips += 1
            logger.debug("EG4 cloud served an older upload (serverTime %s, latest %s); skipping",
                         server_time, self._last_server_time)
            return False
        if (self._last_fresh_key is None and data_age is not None
                and abs(data_age) > self._first_upload_max_age):
            # First payload after a start: an upload this old is the portal's
            # frozen mirror (the dongle stopped uploading), not live data. It
            # becomes the baseline and the next upload is emitted. (A serverTime
            # that is not UTC also lands here, costing one upload of delay.)
            self._stale_skips += 1
            self._last_fresh_key = key
            self._last_server_time = server_time
            self._last_server_epoch = server_epoch
            logger.info(
                "EG4 cloud: latest upload for %s is not recent (serverTime %s, assumed UTC; "
                "data_age_s=%s, limit %ss); waiting for a new upload before writing",
                self.inverter_serial, server_time, data_age, self._first_upload_max_age,
            )
            return False

        groups: Dict[str, Dict[int, int]] = {"runtime": regs_rt}
        cycle_regs: Dict[int, int] = {}

        # Energy: first fresh cycle, every energy_interval, and after the
        # plant's local midnight until a fetch succeeds (today* counters reset).
        device_day = _date_part(rt.get("deviceTime"))
        new_day = device_day is not None and device_day != self._last_energy_day
        if new_day and self._last_energy_day is not None:
            # Yesterday's today* totals must never be written as today's, even
            # when the fetch below fails.
            self._drop_group_registers("energy", _TODAY_REGS)
        if self._fresh == 0 or new_day or now - self._last_energy_at >= self.energy_interval:
            energy_regs = self._fetch_energy()
            self._last_energy_at = now
            if energy_regs is not None:
                groups["energy"] = energy_regs
                if device_day is not None:
                    self._last_energy_day = device_day

        # Battery: every fresh cycle. The bank current changes fast, so it
        # (and the battery extras in the meta) is published only with the
        # snapshot of the cycle that fetched it; the slow values form the
        # "battery" group, which is kept while young like the energy group.
        battery_regs, battery_payload = self._fetch_battery()
        if battery_regs is not None:
            cycle_regs = {r: v for r, v in battery_regs.items() if r in _CYCLE_ONLY_REGS}
            groups["battery"] = {r: v for r, v in battery_regs.items() if r not in _CYCLE_ONLY_REGS}

        if self._soh is not None and 5 in regs_rt:
            regs_rt[5] = ((self._soh & 0xFF) << 8) | (regs_rt[5] & 0xFF)

        # A replaced transport (config reload) must not write into the collector.
        if self._stop.is_set():
            return False

        with self._emit_lock:
            emitted = 0
            for name, regs in groups.items():
                for frame in registers_to_frames(regs, self.inverter_serial):
                    self._emit(frame)
                    emitted += 1
                self._groups[name] = (frozenset(regs), now)
            for frame in registers_to_frames(cycle_regs, self.inverter_serial):
                self._emit(frame)
                emitted += 1
            self._cycle_regs = frozenset(cycle_regs)
            self._frames_emitted += emitted
            self._last_fresh_key = key
            self._data_seq += 1
            self._data_time = server_epoch
            self._meta = {
                "data_source": "cloud",
                "serial": self.inverter_serial,
                "seq": self._data_seq,
                "server_time": server_time,
                "data_age_s": data_age,
                **cloud_extras(rt, battery_payload),
            }

        self._fresh += 1
        if self._offline:
            self._offline = False
            logger.info("EG4 cloud: live data for %s resumed", self.inverter_serial)
        self._vbat_warned = False
        if self._fresh == 1:
            logger.info(
                "First EG4 cloud update: serverTime=%s deviceTime=%s, collector UTC now %s "
                "(serverTime is assumed to be UTC; data_age_s=%s)",
                server_time, rt.get("deviceTime"),
                datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), data_age,
            )
        elif server_epoch is not None and self._last_server_epoch is not None:
            logger.debug(
                "EG4 cloud update #%d: serverTime %s (+%.0fs since previous upload)",
                self._fresh, server_time, server_epoch - self._last_server_epoch,
            )
        self._last_server_time = server_time
        self._last_server_epoch = server_epoch
        return True

    def _drop_group_registers(self, name: str, regs: FrozenSet[int]) -> None:
        """Remove registers from a group so they leave live_registers at once."""
        with self._emit_lock:
            entry = self._groups.get(name)
            if entry is not None and entry[0] & regs:
                self._groups[name] = (entry[0] - regs, entry[1])

    def _fetch_energy(self) -> Optional[Dict[int, int]]:
        """Fetch and convert the energy payload; None keeps the previous group."""
        try:
            payload = self._call(ENERGY_PATH)
            if not self._group_payload_ok(payload, "energy"):
                return None
            regs = energy_to_registers(payload)
        except CloudAuthError:
            raise
        except Exception as exc:
            self._note_group_error("energy", exc)
            return None
        self._note_group_ok("energy")
        return regs

    def _fetch_battery(self) -> Tuple[Optional[Dict[int, int]], Optional[dict]]:
        """Fetch and convert the battery payload.

        Returns (registers, payload), or (None, None) on failure: the slow
        battery group is then kept while young, the cycle-only values are not.
        """
        try:
            payload = self._call(BATTERY_PATH)
            if not self._group_payload_ok(payload, "battery"):
                return None, None
            regs, soh = battery_to_registers(payload)
        except CloudAuthError:
            raise
        except Exception as exc:
            self._note_group_error("battery", exc)
            return None, None
        self._note_group_ok("battery")
        if soh is not None:
            self._soh = soh
        return regs, payload

    def _group_payload_ok(self, payload: dict, name: str) -> bool:
        if payload.get("success") is False or payload.get("lost") is True:
            self._note_group_problem(name, "payload is offline/unsuccessful", logging.INFO)
            return False
        return True

    def _note_group_error(self, name: str, exc: BaseException) -> None:
        self._errors += 1
        self._last_error = self._redact(f"{type(exc).__name__}: {exc}")
        self._note_group_problem(name, f"poll failed: {self._last_error}", logging.WARNING)

    def _note_group_problem(self, name: str, what: str, level: int) -> None:
        """Log a failed group fetch at `level` the first time in a row, then at DEBUG."""
        count = self._group_failures.get(name, 0) + 1
        self._group_failures[name] = count
        logger.log(level if count == 1 else logging.DEBUG,
                   "EG4 cloud %s %s (%d in a row)", name, what, count)

    def _note_group_ok(self, name: str) -> None:
        count = self._group_failures.pop(name, 0)
        if count:
            logger.info("EG4 cloud %s poll recovered after %d failures", name, count)

    # ── HTTP ─────────────────────────────────────────────────────────────
    def _post(self, path: str, data: Dict[str, str]):
        """POST form data to an allow-listed portal path (redirects disabled)."""
        if path not in _ALLOWED_PATHS:
            raise ValueError(f"refusing to call non-allow-listed EG4 cloud path {path!r}")
        if self._session is None:
            self._session = self._session_factory()
        return self._session.post(
            self.base_url + path,
            data=data,
            headers=dict(_HEADERS),
            allow_redirects=False,
            timeout=HTTP_TIMEOUT,
        )

    def _login(self) -> None:
        """Log in (fresh cookie jar). Raises CloudAuthError on rejected credentials."""
        self._logged_in_at = 0.0
        if self._session is None:
            self._session = self._session_factory()
        try:
            self._session.cookies.clear()
        except Exception:
            pass

        # requests form-urlencodes the dict, so special characters are safe.
        # With redirects disabled, a 307/308 can never re-POST the password
        # to another host.
        resp = self._post(LOGIN_PATH, {
            "account": self._username,
            "password": self._password,
            "language": "ENGLISH",
        })
        status = resp.status_code
        if status == 429:
            raise CloudRateLimited("login returned HTTP 429")
        if 300 <= status < 400 or status >= 500:
            raise CloudTransientError(f"login returned HTTP {status}")
        try:
            body = resp.json()
        except ValueError:
            raise CloudTransientError(f"login returned a non-JSON response (HTTP {status})")
        if not isinstance(body, dict):
            raise CloudTransientError("login returned unexpected JSON")
        if body.get("success") is False:
            msg = str(body.get("msg") or body.get("message") or "login rejected")
            if any(marker in msg.upper() for marker in _TRANSIENT_MARKERS):
                # Portal busy/timeout: not a credential rejection.
                raise CloudTransientError(self._redact(f"login: {msg}"))
            raise CloudAuthError(self._redact(msg))
        if status >= 400:
            raise CloudApiError(f"login returned HTTP {status}")

        self._logged_in_at = self._clock()
        if self._auth_failures:
            logger.info("EG4 cloud accepted the login after %d rejections", self._auth_failures)
        self._auth_failures = 0
        logger.info("Logged in to the EG4 cloud (%s)", self.base_url)
        # The body holds PII (email, phone, address): it is never logged.
        if not self._serial_checked:
            self._serial_checked = True
            self._check_serial_listed(body)

    def _check_serial_listed(self, body: dict) -> None:
        """Warn if the configured serial is not among the account's inverters."""
        plants = body.get("plants")
        if not isinstance(plants, list):
            return
        serials = set()
        for plant in plants:
            if not isinstance(plant, dict):
                continue
            for inv in plant.get("inverters") or []:
                if isinstance(inv, dict) and inv.get("serialNum"):
                    serials.add(str(inv["serialNum"]))
        if serials and self.inverter_serial not in serials:
            logger.warning(
                "EG4 cloud: inverter serial %s is not listed on this account; "
                "check LUX_INVERTER_SERIAL / the inverter_serial setting",
                self.inverter_serial,
            )

    def _classify(self, resp) -> Tuple[str, Optional[dict], str]:
        """Classify a data response as ok / session / rate_limited / transient / api_error."""
        status = resp.status_code
        if 300 <= status < 400 or status == 401:
            return "session", None, f"HTTP {status}"
        if status == 429:
            return "rate_limited", None, f"HTTP {status}"
        if status >= 500:
            return "transient", None, f"HTTP {status}"
        if status >= 400:
            return "api_error", None, f"HTTP {status}"
        try:
            body = resp.json()
        except ValueError:
            # An expired session usually returns HTTP 200 with the HTML login page.
            return "session", None, "non-JSON response (login page?)"
        if not isinstance(body, dict):
            return "api_error", None, "unexpected JSON response"
        if body.get("success") is False:
            msg = str(body.get("message") or body.get("msg") or "")
            if any(marker in msg.upper() for marker in _TRANSIENT_MARKERS):
                return "transient", None, msg
            if not msg or any(marker in msg.lower() for marker in _SESSION_MARKERS):
                return "session", None, msg
            return "api_error", None, msg
        # A missing success key is treated as OK (as pylxpweb does).
        return "ok", body, ""

    def _call(self, path: str, relogin: bool = True) -> dict:
        """POST serialNum to a read endpoint; re-login and replay at most once."""
        resp = self._post(path, {"serialNum": self.inverter_serial})
        kind, body, msg = self._classify(resp)
        if kind == "ok":
            return body
        if kind == "session":
            if relogin:
                self._relogins += 1
                logger.info("EG4 cloud session rejected (%s); logging in again", self._redact(msg))
                self._login()
                return self._call(path, relogin=False)
            raise CloudSessionExpired(self._redact(f"{path}: session rejected after re-login ({msg})"))
        if kind == "rate_limited":
            raise CloudRateLimited(self._redact(f"{path}: {msg}"))
        if kind == "transient":
            raise CloudTransientError(self._redact(f"{path}: {msg}"))
        raise CloudApiError(self._redact(f"{path}: {msg}"))

    def _redact(self, text: Any, limit: int = 300) -> str:
        """Remove credentials and session cookies from text bound for logs/stats."""
        s = str(text)
        secrets = set()
        for secret in (self._password, self._username):
            if secret:
                secrets.update({secret, quote_plus(secret), quote(secret, safe="")})
        # Longest first, so a secret containing another is fully removed.
        for secret in sorted(secrets, key=len, reverse=True):
            s = s.replace(secret, "***")
        s = _JSESSION_RE.sub("JSESSIONID=***", s)
        if len(s) > limit:
            s = s[:limit] + "..."
        return s
