"""Current-weather fetch for the weather_now widget.

Open-Meteo, no API key. Caches per ``(lat, lon, units)`` for 10 minutes
in the plugin's data_dir so the upstream API isn't hammered when several
dashboards share the same location, or when the composer re-renders.
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 600
HTTP_TIMEOUT_S = 10
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
    lat = float(options.get("latitude", 0.0))
    lon = float(options.get("longitude", 0.0))
    units = str(options.get("units", "metric"))

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / f"now_{lat:.3f}_{lon:.3f}_{units}.json"
    cached = _cached(cache_path)
    if cached is not None:
        return cached

    temp_unit = "fahrenheit" if units == "imperial" else "celsius"
    wind_unit = "mph" if units == "imperial" else "kmh"
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,weather_code,apparent_temperature,"
        "wind_speed_10m,wind_direction_10m,relative_humidity_2m,is_day"
        "&daily=temperature_2m_max,temperature_2m_min,"
        "uv_index_max,sunrise,sunset,precipitation_probability_max"
        f"&temperature_unit={temp_unit}"
        f"&wind_speed_unit={wind_unit}"
        "&forecast_days=1&timezone=auto"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    current = payload.get("current") or {}
    daily = payload.get("daily") or {}

    def _first(arr: object) -> Any:
        if isinstance(arr, list) and arr:
            return arr[0]
        return None

    result: dict[str, Any] = {
        "label": options.get("label", ""),
        "units": units,
        "temp": current.get("temperature_2m"),
        "feels": current.get("apparent_temperature"),
        "humidity": current.get("relative_humidity_2m"),
        "wind": current.get("wind_speed_10m"),
        "wind_dir": current.get("wind_direction_10m"),
        "code": current.get("weather_code"),
        "is_day": bool(current.get("is_day", 1)),
        "uv": _first(daily.get("uv_index_max")),
        "sunrise": _first(daily.get("sunrise")),
        "sunset": _first(daily.get("sunset")),
        "today_max": _first(daily.get("temperature_2m_max")),
        "today_min": _first(daily.get("temperature_2m_min")),
        "rain_chance": _first(daily.get("precipitation_probability_max")),
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result
