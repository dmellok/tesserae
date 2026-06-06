"""5-day forecast fetch for weather_forecast.

Open-Meteo daily endpoint, cached per (lat, lon, units) for 10 minutes.
Returns one row per day: ISO date, day-of-week index, high/low temps,
WMO weather code, and max precipitation probability.

The new visual directions (r1/g2/s3/d4 from the design handoff) paint
from extra structured fields layered on top of the legacy shape, see
the ``days[*].day``/``icon``/``cond``/``today`` block, plus the
top-level ``rangeLo`` / ``rangeHi`` / ``time``. Legacy keeps reading
the original flat fields, so both render paths stay alive.
"""

from __future__ import annotations

import contextlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.plugin_http import fetch_json

CACHE_TTL_S = 600
HTTP_TIMEOUT_S = 15
USER_AGENT = "tesserae/0.1 (+weather_forecast)"
FORECAST_DAYS = 5

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _cached(path: Path) -> dict[str, Any] | None:
    if not path.exists() or time.time() - path.stat().st_mtime >= CACHE_TTL_S:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _safe_get(arr: object, idx: int) -> Any:
    if isinstance(arr, list) and 0 <= idx < len(arr):
        return arr[idx]
    return None


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    lat = float(options.get("latitude", 0.0))
    lon = float(options.get("longitude", 0.0))
    units = str(options.get("units", "metric"))

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / f"forecast_{lat:.3f}_{lon:.3f}_{units}.json"
    cached = _cached(cache_path)
    if cached is not None:
        return cached

    temp_unit = "fahrenheit" if units == "imperial" else "celsius"
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=temperature_2m_max,temperature_2m_min,weather_code,"
        "precipitation_probability_max"
        f"&temperature_unit={temp_unit}"
        f"&forecast_days={FORECAST_DAYS}&timezone=auto"
    )
    try:
        payload = fetch_json(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT_S)
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    daily = payload.get("daily") or {}
    times: list[str] = daily.get("time") or []
    today_iso = datetime.now().date().isoformat()
    days: list[dict[str, Any]] = []
    for i, date_iso in enumerate(times[:FORECAST_DAYS]):
        try:
            d = datetime.fromisoformat(date_iso)
            weekday = d.weekday()  # 0 = Monday
        except (ValueError, TypeError):
            weekday = -1

        code = _safe_get(daily.get("weather_code"), i)
        # Forecast cells render the daytime glyph, there's no per-day
        # is_day in the daily endpoint, so we trust the day side of the
        # WMO map for all five entries.
        cond, icon = _condition(code, True)

        high = _safe_get(daily.get("temperature_2m_max"), i)
        low = _safe_get(daily.get("temperature_2m_min"), i)
        rain = _safe_get(daily.get("precipitation_probability_max"), i)

        # ``day`` is the short label (Today / Tom / Mon …) the new
        # variants paint above the column. Legacy uses ``weekday`` +
        # its own JS lookup, so both stay supplied.
        if i == 0:
            day_label = "Today"
        elif i == 1:
            day_label = "Tom"
        else:
            day_label = DAY_NAMES[weekday] if 0 <= weekday < 7 else "-"

        days.append(
            {
                # Legacy fields, original client.js still reads these.
                "date": date_iso,
                "weekday": weekday,
                "high": high,
                "low": low,
                "code": code,
                "rain": rain,
                # Structured fields the new variants read.
                "day": day_label,
                "icon": icon,
                "cond": cond,
                "hi": _round_int(high),
                "lo": _round_int(low),
                "today": date_iso == today_iso,
            }
        )

    # Week-wide range, used by the F1 / F4 range bars. Computed server-
    # side so every variant sees the same min/max even if the client
    # filters or reorders days.
    lows = [d["lo"] for d in days if isinstance(d.get("lo"), (int, float))]
    highs = [d["hi"] for d in days if isinstance(d.get("hi"), (int, float))]
    range_lo = min(lows) if lows else None
    range_hi = max(highs) if highs else None

    result = {
        "label": options.get("label", ""),
        "units": units,
        "days": days,
        # Structured top-level fields the new variants paint from.
        "place": options.get("label", ""),
        "time": _now_hhmm(),
        "rangeLo": range_lo,
        "rangeHi": range_hi,
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result


# ----------------------------------------------------------------------
# Helpers used by the structured / variant data path.
# ----------------------------------------------------------------------


def _round_int(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return round(value)
    return value


def _iso_to_min(iso: Any) -> int | None:
    """Open-Meteo returns ISO timestamps like ``2026-06-03T06:45``. Trim
    the date and return minutes-since-midnight."""
    if not isinstance(iso, str) or "T" not in iso:
        return None
    try:
        _, t = iso.split("T", 1)
        h, m = t.split(":", 2)[:2]
        return int(h) * 60 + int(m)
    except (ValueError, IndexError):
        return None


def _now_hhmm() -> str:
    n = datetime.now()
    return f"{n.hour:02d}:{n.minute:02d}"


# WMO weather codes, see https://open-meteo.com/en/docs#weathervariables
# Mirrors the dict in weather_now/server.py so the two widgets agree
# on label + icon glyph for the same code.
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
    56: ("Freezing drizzle", "sleet", "sleet"),
    57: ("Freezing drizzle", "sleet", "sleet"),
    61: ("Light rain", "rain", "rain"),
    63: ("Rain", "rain", "rain"),
    65: ("Heavy rain", "rain-heavy", "rain-heavy"),
    66: ("Freezing rain", "sleet", "sleet"),
    67: ("Freezing rain", "sleet", "sleet"),
    71: ("Light snow", "snow", "snow"),
    73: ("Snow", "snow", "snow"),
    75: ("Heavy snow", "snow", "snow"),
    77: ("Snow grains", "snow", "snow"),
    80: ("Showers", "showers", "showers"),
    81: ("Showers", "showers", "showers"),
    82: ("Violent showers", "rain-heavy", "rain-heavy"),
    85: ("Snow showers", "snow", "snow"),
    86: ("Heavy snow showers", "snow", "snow"),
    95: ("Thunderstorm", "storm", "storm"),
    96: ("Thunderstorm", "storm", "storm"),
    99: ("Thunderstorm", "storm", "storm"),
}


def _condition(code: Any, is_day: bool) -> tuple[str, str]:
    """Map WMO weather code → (human-readable condition, icon name)."""
    try:
        c = int(code) if code is not None else -1
    except (TypeError, ValueError):
        c = -1
    entry = _WMO.get(c)
    if entry is None:
        return ("Cloudy", "cloud")
    label, day_icon, night_icon = entry
    return (label, day_icon if is_day else night_icon)
