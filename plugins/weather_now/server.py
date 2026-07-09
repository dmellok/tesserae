"""Current-weather fetch for the weather_now widget.

Open-Meteo, no API key. Caches per ``(lat, lon, units)`` for 10 minutes
in the plugin's data_dir so the upstream API isn't hammered when several
dashboards share the same location, or when the composer re-renders.
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

from app.plugin_http import fetch_json

CACHE_TTL_S = 600
# Deliberately shorter than the composer's per-widget hydration cap
# (6s in v0.64.72+). ``fetch_json``'s default retry (one) would compound
# to ~11s on an Open-Meteo outage and blow the composer's budget, so
# we override to ``retries=0`` at the call site as well; a hard
# upstream outage returns an error to the widget within ~5s and the
# cell paints its ``data.error`` state instead of stalling the whole
# page render.
HTTP_TIMEOUT_S = 5
USER_AGENT = "tesserae/0.1 (+weather_now)"


def _cached(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime >= CACHE_TTL_S:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    # Coordinates come from the cell's Location pick (composer's
    # ``_resolved_options`` promotes ``location.latitude`` / ``location.longitude``
    # into the top-level options keys). When the user hasn't picked a
    # location yet, surface a friendly empty-state instead of fetching
    # for the equator. The widget's client.js handles the ``error`` key.
    lat_raw = options.get("latitude")
    lon_raw = options.get("longitude")
    if lat_raw in (None, "") or lon_raw in (None, ""):
        return {
            "error": "Pick a location in the cell editor.",
            "label": options.get("label", ""),
        }
    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except (TypeError, ValueError):
        return {
            "error": "Location has invalid coordinates.",
            "label": options.get("label", ""),
        }
    units = str(options.get("units", "metric"))

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / f"now_{lat:.3f}_{lon:.3f}_{units}.json"
    cached = _cached(cache_path)
    if cached is not None:
        # ``label`` is a UI string set in the cell editor, not part of
        # the upstream API response. The cache key is keyed only on
        # ``(lat, lon, units)``, so a label rename on the same
        # coordinates would otherwise stay stale until the 10-min
        # TTL expires (or the user toggled units, which was the only
        # way out before). Overlay the current options' label so
        # renames take effect on the next preview.
        cached["label"] = options.get("label", "")
        return cached

    temp_unit = "fahrenheit" if units == "imperial" else "celsius"
    wind_unit = "mph" if units == "imperial" else "kmh"
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,weather_code,apparent_temperature,"
        "wind_speed_10m,wind_direction_10m,relative_humidity_2m,is_day,"
        "dew_point_2m,wind_gusts_10m,cloud_cover,pressure_msl,visibility,"
        "uv_index"
        "&daily=temperature_2m_max,temperature_2m_min,"
        "uv_index_max,sunrise,sunset,precipitation_probability_max"
        f"&temperature_unit={temp_unit}"
        f"&wind_speed_unit={wind_unit}"
        "&forecast_days=1&timezone=auto"
    )
    try:
        payload = fetch_json(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT_S,
            retries=0,
        )
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    current = payload.get("current") or {}
    daily = payload.get("daily") or {}

    def _first(arr: object) -> Any:
        if isinstance(arr, list) and arr:
            return arr[0]
        return None

    code = current.get("weather_code")
    is_day = bool(current.get("is_day", 1))
    cond, icon = _condition(code, is_day)
    sunrise_iso = _first(daily.get("sunrise"))
    sunset_iso = _first(daily.get("sunset"))
    rise_min = _iso_to_min(sunrise_iso)
    set_min = _iso_to_min(sunset_iso)
    now_min = _now_min()

    speed_unit = "mph" if units == "imperial" else "km/h"
    rain_chance = _first(daily.get("precipitation_probability_max"))
    uv_index = current.get("uv_index")

    # The 8-metric grid the Refined / Geometric / Swiss variants paint.
    # Order is hand-tuned: most-useful first so a 4-cell variant
    # showing top-4 still leads with humidity / wind / rain prob / UV.
    metrics = [
        _metric("Humidity", current.get("relative_humidity_2m"), "%", "humidity", "blue"),
        _metric("Wind", current.get("wind_speed_10m"), speed_unit, "wind", "ink"),
        _metric("Rain (today)", rain_chance, "%", "rainprob", "blue"),
        _metric("UV Index", uv_index, "", "uv", "yellow"),
        _metric("Pressure", current.get("pressure_msl"), "hPa", "pressure", "ink"),
        _metric("Dew", current.get("dew_point_2m"), "°", "dew", "blue"),
        _metric(
            "Visibility", _round_div(current.get("visibility"), 1000), "km", "visibility", "ink"
        ),
        _metric("Cloud", current.get("cloud_cover"), "%", "cloud", "muted"),
    ]

    result: dict[str, Any] = {
        "label": options.get("label", ""),
        "units": units,
        # Legacy fields, the old client.js render path still uses these.
        "temp": current.get("temperature_2m"),
        "feels": current.get("apparent_temperature"),
        "humidity": current.get("relative_humidity_2m"),
        "wind": current.get("wind_speed_10m"),
        "wind_dir": current.get("wind_direction_10m"),
        "code": code,
        "is_day": is_day,
        "uv": uv_index,
        "sunrise": sunrise_iso,
        "sunset": sunset_iso,
        "today_max": _first(daily.get("temperature_2m_max")),
        "today_min": _first(daily.get("temperature_2m_min")),
        "rain_chance": rain_chance,
        # Structured fields the new variants paint from.
        "cond": cond,
        "icon": icon,
        "high": _first(daily.get("temperature_2m_max")),
        "low": _first(daily.get("temperature_2m_min")),
        "gust": current.get("wind_gusts_10m"),
        "rainChance": rain_chance,
        "metrics": metrics,
        "sun": {
            "rise": _hhmm(sunrise_iso),
            "set": _hhmm(sunset_iso),
            "riseMin": rise_min,
            "setMin": set_min,
            "nowMin": now_min,
        },
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result


# ----------------------------------------------------------------------
# Helpers used only by the structured / variant data path. Kept below
# fetch() because Python reads files top-to-bottom and fetch() is the
# entry point readers care about.
# ----------------------------------------------------------------------


def _metric(
    label: str, value: Any, unit: str, icon: str, accent: str, sub: str | None = None
) -> dict[str, Any]:
    """Shape for one tile in the metrics grid. Returns ``None``-valued
    cells as-is so the client can render an em-dash placeholder rather
    than dropping the cell entirely."""
    rounded = value
    if isinstance(value, (int, float)):
        rounded = round(value, 1) if value < 100 else round(value)
    out: dict[str, Any] = {
        "label": label,
        "value": rounded,
        "unit": unit,
        "icon": icon,
        "accent": accent,
    }
    if sub:
        out["sub"] = sub
    return out


def _round_div(value: Any, divisor: float) -> Any:
    if not isinstance(value, (int, float)):
        return value
    return round(value / divisor, 1) if divisor else value


def _iso_to_min(iso: Any) -> int | None:
    """Open-Meteo returns ISO timestamps like ``2026-06-03T06:45`` (no
    seconds, no tz, the API treats them as local once we pass
    ``timezone=auto``). Parse just the hour/minute and convert to
    minutes-since-midnight for the sun-arc charts."""
    if not isinstance(iso, str) or "T" not in iso:
        return None
    try:
        _, t = iso.split("T", 1)
        h, m = t.split(":", 2)[:2]
        return int(h) * 60 + int(m)
    except (ValueError, IndexError):
        return None


def _hhmm(iso: Any) -> str:
    """Trim ``2026-06-03T06:45`` to ``06:45`` for display."""
    if not isinstance(iso, str) or "T" not in iso:
        return ""
    try:
        return iso.split("T", 1)[1][:5]
    except (ValueError, IndexError):
        return ""


def _now_min() -> int:
    """Wall-clock minutes-since-midnight. Open-Meteo with ``timezone=auto``
    aligns rise/set to the panel's local time, so we use system local
    too, server and panel are typically in the same TZ for a
    single-household Tesserae install."""
    from datetime import datetime as _dt

    n = _dt.now()
    return n.hour * 60 + n.minute


# WMO weather codes, see https://open-meteo.com/en/docs#weathervariables
# Each entry is (label, day-icon, night-icon). Night-icon is only used
# for clear/partly conditions; rain/snow/storm read the same either way.
_WMO: dict[int, tuple[str, str, str]] = {
    0: ("Clear", "sun", "moon"),
    1: ("Mostly clear", "sun", "moon"),
    2: ("Partly cloudy", "partly", "partly-night"),
    3: ("Overcast", "cloud", "cloud"),
    45: ("Fog", "fog", "fog"),
    48: ("Rime fog", "fog", "fog"),
    51: ("Light drizzle", "drizzle", "drizzle"),
    53: ("Drizzle", "drizzle", "drizzle"),
    55: ("Dense drizzle", "drizzle", "drizzle"),
    61: ("Light rain", "rain", "rain"),
    63: ("Rain", "rain", "rain"),
    65: ("Heavy rain", "rain-heavy", "rain-heavy"),
    71: ("Light snow", "snow", "snow"),
    73: ("Snow", "snow", "snow"),
    75: ("Heavy snow", "snow", "snow"),
    80: ("Showers", "showers", "showers"),
    81: ("Showers", "showers", "showers"),
    82: ("Violent showers", "rain-heavy", "rain-heavy"),
    95: ("Thunderstorm", "storm", "storm"),
    96: ("Thunderstorm", "storm", "storm"),
    99: ("Thunderstorm", "storm", "storm"),
}


def _condition(code: Any, is_day: bool) -> tuple[str, str]:
    """Map WMO weather code → (human-readable condition, icon name).
    Falls back to a generic "Cloudy"/"cloud" pair when the upstream
    returns an unknown code so widgets render something readable."""
    try:
        c = int(code) if code is not None else -1
    except (TypeError, ValueError):
        c = -1
    entry = _WMO.get(c)
    if entry is None:
        return ("Cloudy", "cloud")
    label, day_icon, night_icon = entry
    return (label, day_icon if is_day else night_icon)
