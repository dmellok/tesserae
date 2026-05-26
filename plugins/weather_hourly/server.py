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
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

CACHE_TTL_S = 600
HTTP_TIMEOUT_S = 10
USER_AGENT = "tesserae/0.1 (+weather_hourly)"

ALLOWED_HOURS = (12, 24, 48)


def _cached(path: Path) -> dict[str, Any] | None:
    if not path.exists() or time.time() - path.stat().st_mtime >= CACHE_TTL_S:
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _window_start_index(times: list[str], now_iso: str | None) -> int:
    """Find the first index in ``times`` whose hour is >= now. Falls back
    to 0 if the parse fails — the chart will then start at midnight."""
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
    lat = float(options.get("latitude", 0.0))
    lon = float(options.get("longitude", 0.0))
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
        return cached

    temp_unit = "fahrenheit" if units == "imperial" else "celsius"
    # forecast_days=3 gives ~72 hourly slots — enough to cover a 48h
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
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    hourly = payload.get("hourly") or {}
    current = payload.get("current") or {}
    times: list[str] = hourly.get("time") or []
    temps: list[float | None] = hourly.get("temperature_2m") or []
    rain: list[float | None] = hourly.get("precipitation_probability") or []

    start = _window_start_index(times, current.get("time"))
    end = start + hours
    points: list[dict[str, Any]] = []
    max_t: float | None = None
    min_t: float | None = None
    for iso, temp, p in zip(times[start:end], temps[start:end], rain[start:end], strict=False):
        if temp is None:
            continue
        try:
            hour = datetime.fromisoformat(iso).strftime("%H")
        except (ValueError, TypeError):
            hour = ""
        try:
            t_val = float(temp)
        except (TypeError, ValueError):
            continue
        max_t = t_val if max_t is None else max(max_t, t_val)
        min_t = t_val if min_t is None else min(min_t, t_val)
        points.append(
            {
                "iso": iso,
                "hour": hour,
                "temp": t_val,
                "rain": int(p) if isinstance(p, int | float) else None,
            }
        )

    result = {
        "label": options.get("label", ""),
        "units": units,
        "hours": hours,
        "points": points,
        "max": max_t,
        "min": min_t,
        "current": current.get("temperature_2m"),
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result))
    return result
