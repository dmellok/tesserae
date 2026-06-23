"""Hourly temperature + rain probability fetch for weather_hourly.

Open-Meteo's hourly array starts at midnight today, so we trim from the
current local hour onward to expose a "next N hours" window aligned with
the user's expectation. Cached for 10 minutes per (lat, lon, units, hours)
in the plugin's data_dir.
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
USER_AGENT = "tesserae/0.1 (+weather_hourly)"

ALLOWED_HOURS = (12, 24, 48)


def _cached(path: Path) -> dict[str, Any] | None:
    if not path.exists() or time.time() - path.stat().st_mtime >= CACHE_TTL_S:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _window_start_index(times: list[str], now_iso: str | None) -> int:
    """Find the first index in ``times`` whose hour is >= now. Falls back
    to 0 if the parse fails, the chart will then start at midnight."""
    if not now_iso:
        return 0
    try:
        now_hour = datetime.fromisoformat(now_iso).replace(minute=0, second=0, microsecond=0)
    except (ValueError, TypeError):
        return 0
    for i, iso in enumerate(times):
        try:
            t = datetime.fromisoformat(iso)
        except (ValueError, TypeError):
            continue
        if t >= now_hour:
            return i
    return 0


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
    try:
        hours = int(options.get("hours", 24))
    except (TypeError, ValueError):
        hours = 24
    if hours not in ALLOWED_HOURS:
        hours = 24

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / f"hourly_{lat:.3f}_{lon:.3f}_{units}_{hours}.json"
    cached = _cached(cache_path)
    if cached is not None:
        # ``label`` is a UI string from the cell editor, not part of
        # the upstream API response. Overlay the current options'
        # label so a rename on the same cache key shows up on the
        # next preview instead of waiting for the cache TTL.
        cached["label"] = options.get("label", "")
        return cached

    temp_unit = "fahrenheit" if units == "imperial" else "celsius"
    # forecast_days=3 gives ~72 hourly slots, enough to cover a 48h
    # window starting from late in the current day.
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m"
        "&hourly=temperature_2m,precipitation_probability,weather_code"
        f"&temperature_unit={temp_unit}"
        "&forecast_days=3&timezone=auto"
    )
    try:
        payload = fetch_json(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT_S)
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    hourly = payload.get("hourly") or {}
    current = payload.get("current") or {}
    times: list[str] = hourly.get("time") or []
    temps: list[float | None] = hourly.get("temperature_2m") or []
    rain: list[float | None] = hourly.get("precipitation_probability") or []
    codes: list[int | None] = hourly.get("weather_code") or []

    start = _window_start_index(times, current.get("time"))
    end = start + hours
    points: list[dict[str, Any]] = []
    max_t: float | None = None
    min_t: float | None = None
    for iso, temp, p, code in zip(
        times[start:end],
        temps[start:end],
        rain[start:end],
        codes[start:end],
        strict=False,
    ):
        if temp is None:
            continue
        try:
            parsed_dt = datetime.fromisoformat(iso)
            hour = parsed_dt.strftime("%H")
            hour_int = parsed_dt.hour
        except (ValueError, TypeError):
            hour = ""
            hour_int = -1
        try:
            t_val = float(temp)
        except (TypeError, ValueError):
            continue
        max_t = t_val if max_t is None else max(max_t, t_val)
        min_t = t_val if min_t is None else min(min_t, t_val)
        # Approximate day/night from local hour, Open-Meteo's `is_day`
        # is only on the `current` reading, not per-hour. 6-18 = day,
        # otherwise night.
        is_day = 6 <= hour_int < 18 if hour_int >= 0 else True
        points.append(
            {
                "iso": iso,
                "hour": hour,
                "temp": t_val,
                "rain": int(p) if isinstance(p, int | float) else None,
                "code": int(code) if isinstance(code, int | float) else None,
                "is_day": is_day,
            }
        )

    # Structured 24-slot arrays the new variants (r1/g2/s3/d4) paint
    # from. The handoff design always reads 24 hours regardless of the
    # ``hours`` option, the option only affects the legacy renderer's
    # window length. We slice off the front of ``points`` (already
    # trimmed to start at the current local hour) and pad if upstream
    # came up short.
    structured_n = 24
    sliced = points[:structured_n]
    temps_arr: list[float] = []
    rain_arr: list[int] = []
    hours_arr: list[dict[str, Any]] = []
    for p in sliced:
        temps_arr.append(float(p["temp"]))
        rain_arr.append(int(p["rain"]) if isinstance(p.get("rain"), int | float) else 0)
        hours_arr.append(
            {"t": p.get("hour", ""), "icon": _icon_name(p.get("code"), p.get("is_day", True))}
        )

    # Axis ticks: 8 evenly-spaced labels with :00 minute suffix.
    axis_arr: list[str] = []
    if hours_arr:
        n_ticks = min(8, len(hours_arr))
        for i in range(n_ticks):
            idx = round(i * (len(hours_arr) - 1) / max(1, n_ticks - 1)) if n_ticks > 1 else 0
            axis_arr.append(f"{hours_arr[idx]['t']}:00")

    # hi/lo/now over the structured 24h window so the chips align with
    # the chart the user is actually looking at.
    structured_max = max(temps_arr) if temps_arr else max_t
    structured_min = min(temps_arr) if temps_arr else min_t

    result = {
        "label": options.get("label", ""),
        "units": units,
        "hours": hours,
        "points": points,
        "max": max_t,
        "min": min_t,
        "current": current.get("temperature_2m"),
        # Structured fields the new variants paint from. Always present
        # so a cell can flip variant without re-fetching.
        "place": options.get("label", ""),
        "time": _hhmm(current.get("time")),
        "hi": round(structured_max) if isinstance(structured_max, int | float) else None,
        "lo": round(structured_min) if isinstance(structured_min, int | float) else None,
        "now": round(float(current.get("temperature_2m")))
        if isinstance(current.get("temperature_2m"), int | float)
        else None,
        "hoursArr": hours_arr,
        "axis": axis_arr,
        "temps": temps_arr,
        "rain": rain_arr,
        "tMin": structured_min,
        "tMax": structured_max,
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result


# ----------------------------------------------------------------------
# Helpers for the structured / variant data path.
# ----------------------------------------------------------------------


# WMO weather code → semantic icon name (matches wx-common.js PH map).
# Each entry is (day-icon, night-icon). Night-icon only varies for
# clear/partly conditions; rain/snow/storm read the same either way.
_WMO_ICONS: dict[int, tuple[str, str]] = {
    0: ("sun", "moon"),
    1: ("sun", "moon"),
    2: ("partly", "partly-night"),
    3: ("cloud", "cloud"),
    45: ("fog", "fog"),
    48: ("fog", "fog"),
    51: ("drizzle", "drizzle"),
    53: ("drizzle", "drizzle"),
    55: ("drizzle", "drizzle"),
    61: ("rain", "rain"),
    63: ("rain", "rain"),
    65: ("rain-heavy", "rain-heavy"),
    71: ("snow", "snow"),
    73: ("snow", "snow"),
    75: ("snow", "snow"),
    80: ("showers", "showers"),
    81: ("showers", "showers"),
    82: ("rain-heavy", "rain-heavy"),
    95: ("storm", "storm"),
    96: ("storm", "storm"),
    99: ("storm", "storm"),
}


def _icon_name(code: Any, is_day: bool) -> str:
    """Map WMO code → semantic icon name for the structured hours[]
    array. Falls back to ``cloud`` for unknown codes."""
    try:
        c = int(code) if code is not None else -1
    except (TypeError, ValueError):
        return "cloud"
    entry = _WMO_ICONS.get(c)
    if entry is None:
        return "cloud"
    day_icon, night_icon = entry
    return day_icon if is_day else night_icon


def _hhmm(iso: Any) -> str:
    """Trim ``2026-06-03T06:45`` to ``06:45`` for display."""
    if not isinstance(iso, str) or "T" not in iso:
        return ""
    try:
        return iso.split("T", 1)[1][:5]
    except (ValueError, IndexError):
        return ""
