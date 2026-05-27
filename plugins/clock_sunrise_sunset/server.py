"""clock_sunrise_sunset — today's sun + golden hour via Open-Meteo."""

from __future__ import annotations

import contextlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 6 * 3600  # sunrise/set don't change intraday
HTTP_TIMEOUT_S = 12
USER_AGENT = "tesserae/0.1 (+clock_sunrise_sunset)"


def fetch(options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]) -> dict[str, Any]:
    del settings
    lat = float(options.get("latitude", 0.0))
    lon = float(options.get("longitude", 0.0))

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"sun_{lat:.3f}_{lon:.3f}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=sunrise,sunset,daylight_duration,sunshine_duration"
        "&timezone=auto"
        "&forecast_days=1"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    daily = payload.get("daily") or {}
    sunrise = (daily.get("sunrise") or [None])[0]
    sunset  = (daily.get("sunset") or [None])[0]
    daylight_s = (daily.get("daylight_duration") or [None])[0]

    result = {
        "label":           options.get("label") or "",
        "tz":              payload.get("timezone") or "UTC",
        "sunrise":         sunrise,
        "sunset":          sunset,
        "daylight_seconds": daylight_s,
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result))
    return result
