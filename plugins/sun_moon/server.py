"""Sunrise / sunset for a configured lat-lon. Uses open-meteo (no API key).

Caches the daily response under ``ctx.data_dir`` for an hour — sunrise /
sunset only shift a couple of seconds per day at any given latitude, so
re-fetching every push is wasteful and risks rate-limiting on a tight
schedule.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL = 3600  # 1 hour


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    lat = float(options.get("latitude", -37.6494))
    lon = float(options.get("longitude", 145.1004))

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"sun_{lat:.3f}_{lon:.3f}.json"

    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL:
        try:
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=sunrise,sunset&timezone=auto&forecast_days=1"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tesserae/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    daily = payload.get("daily", {})
    sunrises = daily.get("sunrise", [])
    sunsets = daily.get("sunset", [])
    out = {
        "sunrise": sunrises[0] if sunrises else None,
        "sunset": sunsets[0] if sunsets else None,
        "timezone": payload.get("timezone", "UTC"),
    }
    cache.write_text(json.dumps(out))
    return out
