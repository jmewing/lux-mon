"""
Solar PV production forecast for lux-mon.

Option A: weather-based PV forecast.

Pipeline:
  1. Fetch hourly weather (cloud cover + shortwave radiation) from Open-Meteo
     (free, no API key) for the configured site.
  2. Compute a clear-sky PV power curve using pvlib (sun position + clear-sky
     irradiance transposed onto the tilted array plane).
  3. Scale the clear-sky curve by a cloud factor derived from the weather
     forecast, then apply the bifacial back-side gain.
  4. Persist the resulting predicted-watts time series to MariaDB
     (table `{prefix}solar_forecast`).

Option B: historical-PV calibration.

Pipeline:
  1. Compare the last N days of actual PV production (pv_power_total) against
     the forecasted predicted_watts for the same hours.
  2. Bucket the error by (hour-of-day, cloud-cover bucket) and compute a
     rolling bias ratio (actual / predicted) per bucket.
  3. Apply that bias ratio to the freshly built Option A forecast so today's
     numbers reflect what the array actually produces under similar conditions.

Option C (battery/SoC simulation) can build on the corrected predicted-PV series.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger("luxmon.forecast")


# ── Configuration dataclass ────────────────────────────────────────────────

@dataclass
class ForecastConfig:
    enabled: bool = False
    latitude: float = 30.0591
    longitude: float = -95.9324
    array_kwp: float = 1.56
    array_azimuth: float = 180.0
    array_tilt: float = 21.0
    bifacial_gain: float = 0.10
    provider: str = "open-meteo"
    hours: int = 48
    refresh_min: int = 120
    # Option B: historical calibration
    bias_enabled: bool = True
    bias_lookback_days: int = 7
    bias_min_samples: int = 3


def config_from_settings(settings: Dict[str, str]) -> ForecastConfig:
    """Build a ForecastConfig from the lux-mon settings dict (string values)."""

    def _f(name: str, default: float) -> float:
        try:
            return float(settings.get(name, default))
        except (TypeError, ValueError):
            return default

    def _i(name: str, default: int) -> int:
        try:
            return int(float(settings.get(name, default)))
        except (TypeError, ValueError):
            return default

    def _b(name: str, default: bool) -> bool:
        raw = str(settings.get(name, default)).strip().lower()
        return raw in ("1", "true", "yes", "on")

    return ForecastConfig(
        enabled=_b("forecast_enabled", False),
        latitude=_f("forecast_latitude", 30.0591),
        longitude=_f("forecast_longitude", -95.9324),
        array_kwp=_f("array_kwp", 1.56),
        array_azimuth=_f("array_azimuth", 180.0),
        array_tilt=_f("array_tilt", 21.0),
        bifacial_gain=_f("array_bifacial_gain", 0.10),
        provider=str(settings.get("forecast_provider", "open-meteo")).strip(),
        hours=_i("forecast_hours", 48),
        refresh_min=_i("forecast_refresh_min", 120),
        bias_enabled=_b("forecast_bias_enabled", True),
        bias_lookback_days=_i("forecast_bias_lookback_days", 7),
        bias_min_samples=_i("forecast_bias_min_samples", 3),
    )


# ── Weather fetch (Open-Meteo) ─────────────────────────────────────────────

def fetch_open_meteo(cfg: ForecastConfig) -> Optional[Dict[str, Any]]:
    """Fetch hourly cloud cover + shortwave radiation from Open-Meteo.

    Returns a dict with keys:
      - times: list of ISO-8601 timestamps (UTC)
      - cloud_cover: list of 0-100 percentages
      - shortwave: list of W/m^2 (global horizontal irradiance)
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": cfg.latitude,
        "longitude": cfg.longitude,
        "hourly": "cloud_cover,shortwave_radiation",
        "forecast_days": max(1, (cfg.hours + 23) // 24),
        "timezone": "UTC",
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        hourly = data.get("hourly", {})
        return {
            "times": hourly.get("time", []),
            "cloud_cover": hourly.get("cloud_cover", []),
            "shortwave": hourly.get("shortwave_radiation", []),
        }
    except Exception:
        logger.exception("Open-Meteo fetch failed")
        return None


# ── Clear-sky PV model (pvlib) ────────────────────────────────────────────

def _clear_sky_pv(cfg: ForecastConfig, times_utc: List[str]) -> Optional[List[float]]:
    """Compute clear-sky PV power (watts) for each timestamp using pvlib.

    Returns a list of predicted watts aligned with `times_utc`, or None if
    pvlib is unavailable.
    """
    try:
        import pandas as pd
        import pvlib
    except ImportError:
        logger.warning("pvlib not installed; clear-sky model unavailable")
        return None

    try:
        # Build a DatetimeIndex in UTC.
        index = pd.DatetimeIndex([pd.Timestamp(t) for t in times_utc], tz="UTC")

        # Solar position.
        solpos = pvlib.solarposition.get_solarposition(
            index, cfg.latitude, cfg.longitude
        )

        # Clear-sky irradiance (simplified Solis model) on the horizontal plane.
        # Simplified Solis does not require external Linke turbidity data.
        clearsky = pvlib.clearsky.simplified_solis(
            solpos["apparent_elevation"],
        )

        # Transpose GHI/DNI/DHI onto the tilted plane (plane-of-array).
        poa = pvlib.irradiance.get_total_irradiance(
            surface_tilt=cfg.array_tilt,
            surface_azimuth=cfg.array_azimuth,
            dni=clearsky["dni"],
            ghi=clearsky["ghi"],
            dhi=clearsky["dhi"],
            solar_zenith=solpos["apparent_zenith"],
            solar_azimuth=solpos["azimuth"],
        )

        # Effective irradiance on the array (W/m^2).
        poa_global = poa["poa_global"].fillna(0.0)

        # Simple linear power model: P = kWp * (POA / 1000) * 1000 W.
        # (Assumes 1000 W/m^2 STC reference; ignores temperature derating for
        #  now — Option B calibration will absorb the residual.)
        watts = cfg.array_kwp * (poa_global / 1000.0) * 1000.0

        return [float(max(0.0, w)) for w in watts]
    except Exception:
        logger.exception("Clear-sky PV computation failed")
        return None


# ── Cloud factor ───────────────────────────────────────────────────────────

def _cloud_factor(cloud_cover: Optional[float]) -> float:
    """Map cloud cover (0-100%) to a transmission factor (0-1).

    A simple linear attenuation: 0% cloud -> 1.0, 100% cloud -> ~0.15.
    """
    if cloud_cover is None:
        return 1.0
    cc = max(0.0, min(100.0, float(cloud_cover)))
    return 1.0 - 0.85 * (cc / 100.0)


# ── Forecast assembly ──────────────────────────────────────────────────────

def build_forecast(cfg: ForecastConfig) -> Optional[List[Dict[str, Any]]]:
    """Build the full predicted-PV series.

    Returns a list of dicts:
      { "ts": ISO-8601 (UTC), "predicted_watts": float, "cloud_cover": float,
        "source": "open-meteo" }
    or None on failure.
    """
    weather = fetch_open_meteo(cfg)
    if not weather or not weather.get("times"):
        logger.warning("No weather data returned; forecast aborted")
        return None

    times = weather["times"]
    cloud = weather.get("cloud_cover") or [None] * len(times)
    shortwave = weather.get("shortwave") or [None] * len(times)

    # Truncate to the requested horizon.
    times = times[: cfg.hours]
    cloud = cloud[: cfg.hours]
    shortwave = shortwave[: cfg.hours]

    clear_sky = _clear_sky_pv(cfg, times)

    rows: List[Dict[str, Any]] = []
    for i, t in enumerate(times):
        cc = cloud[i] if i < len(cloud) else None
        sw = shortwave[i] if i < len(shortwave) else None

        if clear_sky is not None:
            base = clear_sky[i]
            # Cloud factor from forecast cloud cover.
            factor = _cloud_factor(cc)
            # If shortwave radiation is available, use it as a direct
            # irradiance signal (more accurate than cloud cover alone).
            if sw is not None and sw > 0:
                # Normalize: shortwave is GHI in W/m^2; scale clear-sky by
                # the ratio of forecast GHI to a nominal clear-sky GHI.
                # Fall back to cloud factor when shortwave is zero/None.
                factor = min(1.0, max(0.0, float(sw) / 1000.0))
            watts = base * factor
        else:
            # No pvlib: fall back to a crude shortwave-based estimate.
            watts = cfg.array_kwp * (float(sw or 0) / 1000.0) * 1000.0

        # Bifacial back-side gain.
        watts = watts * (1.0 + cfg.bifacial_gain)

        rows.append(
            {
                "ts": t,
                "predicted_watts": round(float(watts), 1),
                "cloud_cover": cc if cc is not None else None,
                "source": cfg.provider,
            }
        )

    return rows


# ── Option B: historical-PV calibration ────────────────────────────────────

def _cloud_bucket(cloud_cover: Optional[float]) -> int:
    """Bucket cloud cover into coarse bins for bias matching.

    0-20% -> 0 (clear), 21-60% -> 1 (partly cloudy), 61-100% -> 2 (overcast).
    None -> -1 (unknown).
    """
    if cloud_cover is None:
        return -1
    cc = max(0.0, min(100.0, float(cloud_cover)))
    if cc <= 20.0:
        return 0
    if cc <= 60.0:
        return 1
    return 2


def _fetch_actual_hourly(conn, prefix: str, since: datetime) -> Dict[Tuple[int, int], List[float]]:
    """Fetch actual PV production (pv_power_total) averaged per hour.

    Returns a dict keyed by (hour_of_day, cloud_bucket) -> list of hourly
    average watts. Cloud bucket is derived from the forecast table's
    cloud_cover for that hour (best available proxy for conditions).
    """
    # Actual PV total is stored in lux_registers as pv_power_total (and
    # pv1_power/pv2_power/pv3_power). We average pv_power_total per hour.
    #
    # NOTE: lux_registers stores one row per register per snapshot, so we
    # aggregate with AVG over hourly buckets.
    query = f"""
        SELECT
            DATE_FORMAT(r.ts, '%%Y-%%m-%%d %%H:00:00') AS hour_ts,
            HOUR(r.ts) AS hour_of_day,
            AVG(r.value) AS avg_watts
        FROM {prefix}registers r
        WHERE r.name = 'pv_power_total'
          AND r.ts >= %s
        GROUP BY hour_ts, hour_of_day
        ORDER BY hour_ts
    """
    with conn.cursor() as cur:
        cur.execute(query, (since,))
        rows = cur.fetchall()

    # Fetch forecast cloud_cover for the same hours to bucket actuals.
    fc_query = f"""
        SELECT
            DATE_FORMAT(ts, '%%Y-%%m-%%d %%H:00:00') AS hour_ts,
            cloud_cover
        FROM {prefix}solar_forecast
        WHERE ts >= %s
    """
    with conn.cursor() as cur:
        cur.execute(fc_query, (since,))
        fc_rows = {r[0]: r[1] for r in cur.fetchall()}

    buckets: Dict[Tuple[int, int], List[float]] = defaultdict(list)
    for hour_ts, hour_of_day, avg_watts in rows:
        if avg_watts is None:
            continue
        cc = fc_rows.get(hour_ts)
        bucket = _cloud_bucket(cc)
        buckets[(hour_of_day, bucket)].append(float(avg_watts))
    return buckets


def _fetch_forecast_hourly(conn, prefix: str, since: datetime) -> Dict[Tuple[int, int], List[float]]:
    """Fetch forecasted predicted_watts per hour, bucketed by (hour, cloud)."""
    query = f"""
        SELECT
            DATE_FORMAT(ts, '%%Y-%%m-%%d %%H:00:00') AS hour_ts,
            HOUR(ts) AS hour_of_day,
            predicted_watts,
            cloud_cover
        FROM {prefix}solar_forecast
        WHERE ts >= %s
    """
    with conn.cursor() as cur:
        cur.execute(query, (since,))
        rows = cur.fetchall()

    buckets: Dict[Tuple[int, int], List[float]] = defaultdict(list)
    for _hour_ts, hour_of_day, predicted_watts, cloud_cover in rows:
        if predicted_watts is None:
            continue
        bucket = _cloud_bucket(cloud_cover)
        buckets[(hour_of_day, bucket)].append(float(predicted_watts))
    return buckets


def _compute_bias_map(conn, prefix: str, cfg: ForecastConfig) -> Dict[Tuple[int, int], float]:
    """Compute rolling bias ratio (actual / predicted) per (hour, cloud) bucket.

    Uses the last `cfg.bias_lookback_days` days of data. Returns a dict mapping
    (hour_of_day, cloud_bucket) -> bias ratio. Buckets with fewer than
    `cfg.bias_min_samples` samples are omitted (no correction applied).
    """
    since = datetime.now(timezone.utc) - timedelta(days=cfg.bias_lookback_days)
    # MariaDB stores naive UTC datetimes; convert to naive for the query.
    since_naive = since.replace(tzinfo=None)

    actual = _fetch_actual_hourly(conn, prefix, since_naive)
    forecast = _fetch_forecast_hourly(conn, prefix, since_naive)

    bias_map: Dict[Tuple[int, int], float] = {}
    for key in set(actual.keys()) & set(forecast.keys()):
        actual_vals = actual[key]
        forecast_vals = forecast[key]
        # Align by index (both are hourly, ordered by hour_ts).
        # Use min length to avoid misalignment from partial hours.
        n = min(len(actual_vals), len(forecast_vals))
        if n < cfg.bias_min_samples:
            continue
        actual_avg = sum(actual_vals[:n]) / n
        forecast_avg = sum(forecast_vals[:n]) / n
        if forecast_avg <= 0:
            continue
        ratio = actual_avg / forecast_avg
        # Clamp to a sane range to avoid wild swings from bad data.
        ratio = max(0.0, min(3.0, ratio))
        bias_map[key] = ratio

    return bias_map


def _apply_correction(rows: List[Dict[str, Any]],
                      bias_map: Dict[Tuple[int, int], float]) -> List[Dict[str, Any]]:
    """Apply the bias ratio to each forecast row, adding corrected_watts.

    Rows without a matching bucket keep corrected_watts = predicted_watts.
    """
    for r in rows:
        ts = r["ts"]
        # Parse hour-of-day from the ISO timestamp (UTC).
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            hour_of_day = dt.hour
        except (ValueError, AttributeError):
            hour_of_day = 0
        bucket = _cloud_bucket(r.get("cloud_cover"))
        ratio = bias_map.get((hour_of_day, bucket))
        predicted = float(r["predicted_watts"])
        corrected = predicted * ratio if ratio is not None else predicted
        r["corrected_watts"] = round(corrected, 1)
    return rows


# ── Persistence ────────────────────────────────────────────────────────────

def _ensure_table(conn, prefix: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {prefix}solar_forecast (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                ts DATETIME NOT NULL,
                predicted_watts DOUBLE NOT NULL,
                corrected_watts DOUBLE DEFAULT NULL,
                cloud_cover DOUBLE DEFAULT NULL,
                source VARCHAR(32) DEFAULT NULL,
                UNIQUE KEY idx_ts (ts)
            ) ENGINE=InnoDB
            """
        )
        # Add corrected_watts column if the table predates Option B.
        cur.execute(
            f"""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = '{prefix}solar_forecast'
              AND COLUMN_NAME = 'corrected_watts'
            """
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                f"ALTER TABLE {prefix}solar_forecast ADD COLUMN corrected_watts DOUBLE DEFAULT NULL"
            )


def store_forecast(conn, prefix: str, rows: List[Dict[str, Any]],
                   influx_cfg: Optional[Dict[str, str]] = None) -> int:
    """Upsert forecast rows into MariaDB (and optionally InfluxDB). Returns rows written."""
    if not rows:
        return 0
    _ensure_table(conn, prefix)
    written = 0
    with conn.cursor() as cur:
        for r in rows:
            # Convert ISO-8601 UTC -> naive DATETIME (stored in UTC).
            ts = r["ts"]
            if ts.endswith("Z"):
                ts = ts[:-1]
            cur.execute(
                f"""
                INSERT INTO {prefix}solar_forecast
                    (ts, predicted_watts, corrected_watts, cloud_cover, source)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    predicted_watts = VALUES(predicted_watts),
                    corrected_watts = VALUES(corrected_watts),
                    cloud_cover = VALUES(cloud_cover),
                    source = VALUES(source)
                """,
                (ts, r["predicted_watts"], r.get("corrected_watts"), r["cloud_cover"], r["source"]),
            )
            written += 1

    if influx_cfg is not None:
        try:
            _store_influxdb(
                influx_cfg["url"], influx_cfg["token"], influx_cfg["org"], influx_cfg["bucket"], rows
            )
        except Exception:
            logger.exception("Forecast InfluxDB write failed")

    return written


def _escape_measurement(name: str) -> str:
    """Escape spaces and commas in an InfluxDB measurement name."""
    return name.replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,")


def _store_influxdb(influx_url: str, influx_token: str, influx_org: str,
                    influx_bucket: str, rows: List[Dict[str, Any]]) -> None:
    """Write forecast rows to InfluxDB via synchronous HTTP POST.

    Uses the InfluxDB v2 write API directly so data is flushed immediately,
    avoiding async-batch flush issues in short-lived contexts.

    The existing Grafana panels in lux-mon-charts.json expect:
      - measurement "PV power predicted", field "combined"
      - measurement "Cloud cover", field "combined"
    """
    lines: List[str] = []
    for r in rows:
        # InfluxDB line protocol timestamp in nanoseconds (UTC).
        ts = r["ts"]
        if ts.endswith("Z"):
            ts = ts[:-1]
        dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
        ts_ns = int(dt.timestamp() * 1_000_000_000)

        # Escape tag value (provider name) for line protocol safety.
        source = str(r.get("source", "open-meteo")).replace(" ", "\\ ").replace(",", "\\,")
        predicted_watts = float(r.get("predicted_watts") or 0)
        lines.append(
            f'{_escape_measurement("PV power predicted")},source={source} combined={predicted_watts} {ts_ns}'
        )

        corrected_watts = r.get("corrected_watts")
        if corrected_watts is not None:
            lines.append(
                f'{_escape_measurement("PV power predicted corrected")},source={source} combined={float(corrected_watts)} {ts_ns}'
            )

        cc = r.get("cloud_cover")
        if cc is not None:
            lines.append(
                f'{_escape_measurement("Cloud cover")},source={source} combined={cc} {ts_ns}'
            )

    if not lines:
        return
    import requests
    payload = "\n".join(lines)
    url = influx_url.rstrip("/") + "/api/v2/write"
    params = {"bucket": influx_bucket, "org": influx_org, "precision": "ns"}
    headers = {"Authorization": f"Token {influx_token}"}
    resp = requests.post(url, params=params, data=payload, headers=headers, timeout=20)
    resp.raise_for_status()
    logger.debug("Wrote %d InfluxDB forecast lines to %s", len(lines), url)


# ── High-level refresh entrypoint ──────────────────────────────────────────

def refresh(conn, prefix: str, settings: Dict[str, str],
            influx_cfg: Optional[Dict[str, str]] = None) -> Optional[int]:
    """Fetch, build, and store a fresh forecast. Returns rows written or None.

    `settings` is the full lux-mon settings dict (string values).
    """
    cfg = config_from_settings(settings)
    if not cfg.enabled:
        logger.debug("Forecast disabled; skipping refresh")
        return None

    rows = build_forecast(cfg)
    if not rows:
        return None

    # Option B: apply historical calibration if enabled.
    if cfg.bias_enabled:
        try:
            bias_map = _compute_bias_map(conn, prefix, cfg)
            if bias_map:
                rows = _apply_correction(rows, bias_map)
                logger.info(
                    "Forecast bias correction applied from %d buckets",
                    len(bias_map),
                )
            else:
                logger.info("No bias buckets available; storing raw forecast")
        except Exception:
            logger.exception("Bias correction failed; storing raw forecast")

    written = store_forecast(conn, prefix, rows, influx_cfg)
    logger.info("Forecast refreshed: %d rows stored", written)
    return written
