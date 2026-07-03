"""Slim data fetch for the scenic weather variant.

Shares the upstream contract with ``weather_now`` (same Open-Meteo
endpoint, same WMO code mapping), but only returns the fields the
scenic presentation actually paints: current temperature, condition
label + semantic icon, day/night flag, sunrise + sunset, location
label. No metrics grid, the scenic layout doesn't have room for it.

Caching mirrors ``weather_now`` (10 min TTL per location/units) so
running both widgets side by side doesn't double the upstream load
unless they request different locations.
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

from app.plugin_http import fetch_json

CACHE_TTL_S = 600
# See weather_now/server.py for the reasoning; short-fail so the
# composer's hydration cap can't be blown by an Open-Meteo outage.
HTTP_TIMEOUT_S = 5
USER_AGENT = "tesserae/0.1 (+weather_now_scenic)"


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
    cache_path = data_dir / f"scenic_{lat:.3f}_{lon:.3f}_{units}.json"
    cached = _cached(cache_path)
    if cached is not None:
        # ``label`` is a UI string from the cell editor, not part of
        # the upstream API response. Overlay the current options'
        # label so a rename on the same ``(lat, lon, units)`` shows
        # up on the next preview instead of waiting for the cache
        # TTL.
        cached["label"] = options.get("label", "")
        return cached

    temp_unit = "fahrenheit" if units == "imperial" else "celsius"
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,weather_code,is_day"
        "&daily=sunrise,sunset"
        f"&temperature_unit={temp_unit}"
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
    preset = _preset(icon, is_day)

    result: dict[str, Any] = {
        "label": options.get("label", ""),
        "units": units,
        "temp": current.get("temperature_2m"),
        "code": code,
        "is_day": is_day,
        "cond": cond,
        "icon": icon,
        "preset": preset,
        "sunrise": _hhmm(_first(daily.get("sunrise"))),
        "sunset": _hhmm(_first(daily.get("sunset"))),
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result


# ----------------------------------------------------------------------
# Condition + preset mapping. Kept local rather than importing from
# weather_now so each widget folder stays self-contained, matches the
# drop-a-folder mental model.
# ----------------------------------------------------------------------


def _hhmm(iso: Any) -> str:
    if not isinstance(iso, str) or "T" not in iso:
        return ""
    try:
        return iso.split("T", 1)[1][:5]
    except (ValueError, IndexError):
        return ""


# WMO code, see https://open-meteo.com/en/docs#weathervariables
_WMO: dict[int, tuple[str, str, str]] = {
    0: ("Sunny", "sun", "moon"),
    1: ("Mostly clear", "sun", "moon"),
    2: ("Partly cloudy", "partly", "partly-night"),
    3: ("Cloudy", "cloud", "cloud"),
    45: ("Fog", "fog", "fog"),
    48: ("Fog", "fog", "fog"),
    51: ("Drizzle", "drizzle", "drizzle"),
    53: ("Drizzle", "drizzle", "drizzle"),
    55: ("Drizzle", "drizzle", "drizzle"),
    61: ("Rain", "rain", "rain"),
    63: ("Rain", "rain", "rain"),
    65: ("Heavy Rain", "rain-heavy", "rain-heavy"),
    71: ("Snow", "snow", "snow"),
    73: ("Snow", "snow", "snow"),
    75: ("Heavy Snow", "snow", "snow"),
    80: ("Showers", "rain", "rain"),
    81: ("Showers", "rain", "rain"),
    82: ("Showers", "rain-heavy", "rain-heavy"),
    95: ("Storm", "storm", "storm"),
    96: ("Storm", "storm", "storm"),
    99: ("Storm", "storm", "storm"),
}


def _condition(code: Any, is_day: bool) -> tuple[str, str]:
    try:
        c = int(code) if code is not None else -1
    except (TypeError, ValueError):
        c = -1
    entry = _WMO.get(c)
    if entry is None:
        return ("Cloudy", "cloud")
    label, day_icon, night_icon = entry
    return (label, day_icon if is_day else night_icon)


# Semantic icon → preset name. Presets are the visual themes the
# client paints; each one is a background + accent + decoration set.
# Keeping this on the server side means the client only has to dispatch
# on a single ``preset`` string rather than re-derive from icon + is_day.
_PRESETS_BY_ICON: dict[str, str] = {
    "sun": "sunny_day",
    "moon": "clear_night",
    "partly": "partly_day",
    "partly-night": "partly_night",
    "cloud": "cloudy_day",
    "drizzle": "rain",
    "rain": "rain",
    "rain-heavy": "rain",
    "snow": "snow",
    "storm": "storm",
    "fog": "cloudy_day",
}


def _preset(icon: str, is_day: bool) -> str:
    """Choose a visual preset. Most map cleanly off the icon; ``cloud``
    needs day/night disambiguation because the night sky vs day-cloud
    palettes are different."""
    if icon == "cloud":
        return "cloudy_day" if is_day else "cloudy_night"
    return _PRESETS_BY_ICON.get(icon, "cloudy_day" if is_day else "cloudy_night")
