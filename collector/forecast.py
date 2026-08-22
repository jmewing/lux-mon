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

The module is designed so Option B (historical-PV calibration) and Option C
(battery/SoC simulation) can build on the same predicted-PV series.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

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


# ── Persistence ────────────────────────────────────────────────────────────

def _ensure_table(conn, prefix: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {prefix}solar_forecast (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                ts DATETIME NOT NULL,
                predicted_watts DOUBLE NOT NULL,
                cloud_cover DOUBLE DEFAULT NULL,
                source VARCHAR(32) DEFAULT NULL,
                UNIQUE KEY idx_ts (ts)
            ) ENGINE=InnoDB
            """
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
                    (ts, predicted_watts, cloud_cover, source)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    predicted_watts = VALUES(predicted_watts),
                    cloud_cover = VALUES(cloud_cover),
                    source = VALUES(source)
                """,
                (ts, r["predicted_watts"], r["cloud_cover"], r["source"]),
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

    written = store_forecast(conn, prefix, rows, influx_cfg)
    logger.info("Forecast refreshed: %d rows stored", written)
    return written
