"""5-day forecast fetch for weather_forecast.

Open-Meteo daily endpoint, cached per (lat, lon, units) for 10 minutes.
Returns one row per day: ISO date, day-of-week index, high/low temps,
WMO weather code, and max precipitation probability.
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
    days: list[dict[str, Any]] = []
    for i, date_iso in enumerate(times[:FORECAST_DAYS]):
        try:
            d = datetime.fromisoformat(date_iso)
            weekday = d.weekday()  # 0 = Monday
        except (ValueError, TypeError):
            weekday = -1
        days.append(
            {
                "date": date_iso,
                "weekday": weekday,
                "high": _safe_get(daily.get("temperature_2m_max"), i),
                "low": _safe_get(daily.get("temperature_2m_min"), i),
                "code": _safe_get(daily.get("weather_code"), i),
                "rain": _safe_get(daily.get("precipitation_probability_max"), i),
            }
        )

    result = {
        "label": options.get("label", ""),
        "units": units,
        "days": days,
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result
