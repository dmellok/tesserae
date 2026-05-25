"""Rich weather card from open-meteo (no API key).

Returns everything the client needs to render the full layout: current
conditions + feels-like + UV + sunrise/sunset, the next ~6 hours of hourly
temps, and a 4-day daily outlook. Cached on disk for 10 minutes per
lat/lon/units to stay polite with the public API.
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

CACHE_TTL = 600  # 10 minutes
HOURLY_LOOKAHEAD = 6  # next N hours rendered as a sparkline


def _trim(arr: list[Any] | None, n: int) -> list[Any]:
    if not arr:
        return []
    return arr[:n]


def _hour_label(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M")
    except (ValueError, TypeError):
        return ""


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    lat = float(options.get("latitude", -37.6494))
    lon = float(options.get("longitude", 145.1004))
    units = options.get("units", "metric")

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"wx_{lat:.3f}_{lon:.3f}_{units}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL:
        try:
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    temp_unit = "fahrenheit" if units == "imperial" else "celsius"
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        # Current snapshot — temp, condition code, feels-like, wind, humidity.
        "&current=temperature_2m,weather_code,apparent_temperature,"
        "wind_speed_10m,relative_humidity_2m"
        # Daily — 4 days of min/max + code, plus today's UV/precip/sun times.
        "&daily=temperature_2m_max,temperature_2m_min,weather_code,"
        "precipitation_probability_max,uv_index_max,sunrise,sunset"
        # Hourly sparkline for the next few hours.
        "&hourly=temperature_2m,weather_code"
        f"&temperature_unit={temp_unit}"
        f"&forecast_days=4&forecast_hours={HOURLY_LOOKAHEAD}"
        "&timezone=auto"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tesserae/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    current = payload.get("current", {}) or {}
    daily = payload.get("daily", {}) or {}
    hourly = payload.get("hourly", {}) or {}

    # Hourly sparkline — only the lookahead window, with HH:MM labels.
    h_times = _trim(hourly.get("time"), HOURLY_LOOKAHEAD)
    h_temps = _trim(hourly.get("temperature_2m"), HOURLY_LOOKAHEAD)
    h_codes = _trim(hourly.get("weather_code"), HOURLY_LOOKAHEAD)
    hourly_points = []
    for i, t in enumerate(h_times):
        hourly_points.append(
            {
                "label": _hour_label(t),
                "temp": h_temps[i] if i < len(h_temps) else None,
                "code": h_codes[i] if i < len(h_codes) else None,
            }
        )

    # Daily list — 4 days, weekday name + min/max + code.
    d_times = _trim(daily.get("time"), 4)
    d_max = _trim(daily.get("temperature_2m_max"), 4)
    d_min = _trim(daily.get("temperature_2m_min"), 4)
    d_codes = _trim(daily.get("weather_code"), 4)
    daily_points = []
    for i, t in enumerate(d_times):
        try:
            day_label = datetime.fromisoformat(t).strftime("%a").upper()
        except ValueError:
            day_label = ""
        daily_points.append(
            {
                "label": day_label,
                "max": d_max[i] if i < len(d_max) else None,
                "min": d_min[i] if i < len(d_min) else None,
                "code": d_codes[i] if i < len(d_codes) else None,
            }
        )

    # Today-only stats: UV, precip prob, sun times all live in `daily[0]`.
    sunrise_arr = daily.get("sunrise") or []
    sunset_arr = daily.get("sunset") or []
    today_stats = {
        "uv_index_max": (daily.get("uv_index_max") or [None])[0],
        "precipitation_probability_max": (daily.get("precipitation_probability_max") or [None])[0],
        "sunrise": _hour_label(sunrise_arr[0]) if sunrise_arr else "",
        "sunset": _hour_label(sunset_arr[0]) if sunset_arr else "",
    }

    result = {
        "current": current,
        "today": today_stats,
        "hourly": hourly_points,
        "daily": daily_points,
        "units": units,
        "fetched_at": int(time.time()),
    }
    cache.write_text(json.dumps(result))
    return result
