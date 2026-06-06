"""weather_wind, wind speed, gust, direction + Beaufort + 12h gust trace.

Open-Meteo, no API key. Caches per ``(lat, lon, units)`` for 10 minutes
in the plugin's data_dir; refreshes when the cache is older.

Wire shape the client renders from:

    {
      "place": str, "time": str,
      "speed": float, "unit": str,                  # current wind
      "gust": float,                                # current gust
      "dir": str, "bearing": int,                   # human + numeric
      "beaufort": int, "beaufortLabel": str,
      "rose": [{"d": "N", "v": int}, ...]           # 8-point histogram
      "hours": [{"t": "12:00", "s": int, "dir": int}, ...]  # 6 fcst hours
      "gustSeries": [...]                           # next 12 hour gusts
    }
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
USER_AGENT = "tesserae/0.1 (+weather_wind)"


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
    lat = float(options.get("latitude", 0.0))
    lon = float(options.get("longitude", 0.0))
    units = str(options.get("units", "metric"))
    speed_unit = "mph" if units == "imperial" else "km/h"

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / f"wind_{lat:.3f}_{lon:.3f}_{units}.json"
    cached = _cached(cache_path)
    if cached is not None:
        return cached

    wind_unit_api = "mph" if units == "imperial" else "kmh"
    # 24h of hourly data lets us build both the 8-point rose (binning
    # the next 24h) and the 12-hour gust trace from one call.
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=wind_speed_10m,wind_direction_10m,wind_gusts_10m"
        "&hourly=wind_speed_10m,wind_direction_10m,wind_gusts_10m"
        f"&wind_speed_unit={wind_unit_api}"
        "&forecast_days=2&timezone=auto"
    )
    try:
        payload = fetch_json(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT_S)
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    current = payload.get("current") or {}
    hourly = payload.get("hourly") or {}

    speed = current.get("wind_speed_10m")
    gust = current.get("wind_gusts_10m")
    bearing = current.get("wind_direction_10m")
    beaufort_n, beaufort_label = _beaufort(speed, units)
    direction_word = _bearing_to_word(bearing)

    times = hourly.get("time") or []
    speeds = hourly.get("wind_speed_10m") or []
    dirs = hourly.get("wind_direction_10m") or []
    gusts = hourly.get("wind_gusts_10m") or []

    # Trim to the next 24 hours starting at the first ``time`` >= now.
    start = _first_future_index(times)
    end = min(len(times), start + 24)
    fwd_times = times[start:end]
    fwd_speeds = speeds[start:end]
    fwd_dirs = dirs[start:end]
    fwd_gusts = gusts[start:end]

    rose = _wind_rose(fwd_speeds, fwd_dirs)
    hour_strip = _hour_strip(fwd_times, fwd_speeds, fwd_dirs, every=4, count=6)
    gust_series = [round(_safe_float(g), 1) for g in fwd_gusts[:12] if g is not None]

    result: dict[str, Any] = {
        "label": options.get("label", ""),
        "units": units,
        "place": options.get("label", ""),
        "time": datetime.now().strftime("%H:%M"),
        "speed": _round1(speed),
        "unit": speed_unit,
        "gust": _round1(gust),
        "dir": direction_word,
        "bearing": int(_safe_float(bearing)) if bearing is not None else 0,
        "beaufort": beaufort_n,
        "beaufortLabel": beaufort_label,
        "rose": rose,
        "hours": hour_strip,
        "gustSeries": gust_series,
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

# Beaufort scale, km/h-based. Boundaries from the international
# definition; mph variants picked so the same descriptor lands in the
# same bucket on either unit system.
_BEAUFORT_KMH: list[tuple[float, int, str]] = [
    (1, 0, "Calm"),
    (5, 1, "Light air"),
    (11, 2, "Light breeze"),
    (19, 3, "Gentle breeze"),
    (28, 4, "Moderate breeze"),
    (38, 5, "Fresh breeze"),
    (49, 6, "Strong breeze"),
    (61, 7, "Near gale"),
    (74, 8, "Gale"),
    (88, 9, "Strong gale"),
    (102, 10, "Storm"),
    (117, 11, "Violent storm"),
    (999, 12, "Hurricane"),
]


def _beaufort(speed: Any, units: str) -> tuple[int, str]:
    """Return (beaufort_index, descriptor) for a wind speed.

    ``speed`` is in whatever units the user picked; we convert mph→km/h
    for the table lookup so the descriptors line up internationally."""
    s = _safe_float(speed)
    if units == "imperial":
        s = s * 1.609344
    for threshold, n, label in _BEAUFORT_KMH:
        if s < threshold:
            return n, label
    return 12, "Hurricane"


# 16-point compass, we report the closest 16th rather than degrees so
# the widget reads at a glance. The handoff uses 16-letter abbreviations
# (NNE, ENE, …) which we collapse into the 8-point set for the rose.
_COMPASS_16 = [
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
]


def _bearing_to_word(bearing: Any) -> str:
    b = _safe_float(bearing) % 360
    idx = round(b / 22.5) % 16
    return _COMPASS_16[idx]


def _wind_rose(speeds: list[Any], dirs: list[Any]) -> list[dict[str, Any]]:
    """Build an 8-point histogram weighted by speed.

    Each cardinal+intercardinal bucket gets the sum of speeds for hours
    whose bearing falls in its 45° arc. The result is purely relative
    (the widget normalises to the max); units cancel out."""
    bins = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    weights = [0.0] * 8
    for s, d in zip(speeds, dirs, strict=False):
        sv = _safe_float(s)
        dv = _safe_float(d) % 360
        idx = round(dv / 45.0) % 8
        weights[idx] += sv
    return [{"d": label, "v": round(w, 1)} for label, w in zip(bins, weights, strict=True)]


def _hour_strip(
    times: list[Any], speeds: list[Any], dirs: list[Any], *, every: int, count: int
) -> list[dict[str, Any]]:
    """Pick ``count`` evenly-spaced hours for the Refined direction's
    bottom row. ``every`` is in hours (so every=4 gives next +4h, +8h,
    +12h, +16h, +20h, +24h)."""
    out: list[dict[str, Any]] = []
    for i in range(count):
        k = i * every
        if k >= len(times):
            break
        t = times[k]
        out.append(
            {
                "t": _hhmm_from_iso(t),
                "s": round(_safe_float(speeds[k])),
                "dir": int(_safe_float(dirs[k])),
            }
        )
    return out


def _safe_float(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _round1(v: Any) -> Any:
    if not isinstance(v, (int, float)):
        return v
    return round(v, 1)


def _hhmm_from_iso(iso: Any) -> str:
    if not isinstance(iso, str) or "T" not in iso:
        return ""
    try:
        return iso.split("T", 1)[1][:5]
    except (ValueError, IndexError):
        return ""


def _first_future_index(times: list[Any]) -> int:
    """Index of the first ``hourly.time`` entry that's at or after now."""
    now = datetime.now()
    for idx, raw in enumerate(times):
        if not isinstance(raw, str) or "T" not in raw:
            continue
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if dt >= now:
            return idx
    return 0
