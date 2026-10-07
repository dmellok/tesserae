"""clock_sunrise_sunset, today's sun + golden hour via Open-Meteo."""

from __future__ import annotations

import contextlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from app.location_time import location_now
from app.plugin_http import decode_body

CACHE_TTL_S = 6 * 3600  # sunrise/set don't change intraday
HTTP_TIMEOUT_S = 12
USER_AGENT = "tesserae/0.1 (+clock_sunrise_sunset)"


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

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"sun_{lat:.3f}_{lon:.3f}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cached = None
        if isinstance(cached, dict) and _is_for_today(cached):
            # ``label`` is a UI string from the cell editor, not part
            # of the upstream API response. Overlay current label so
            # a rename on the same ``(lat, lon)`` shows up on the
            # next preview instead of waiting for the cache TTL.
            cached["label"] = options.get("label") or ""
            return cached

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=sunrise,sunset,daylight_duration,sunshine_duration"
        "&timezone=auto"
        "&forecast_days=1"
    )
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(decode_body(resp).decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    daily = payload.get("daily") or {}
    sunrise = (daily.get("sunrise") or [None])[0]
    sunset = (daily.get("sunset") or [None])[0]
    daylight_s = (daily.get("daylight_duration") or [None])[0]

    result = {
        "label": options.get("label") or "",
        # The location's zone; sunrise / sunset are wall-clock times in
        # it and the client takes "now" in it too (#351).
        "tz": payload.get("timezone") or None,
        "utc_offset_seconds": payload.get("utc_offset_seconds"),
        # The location-local date these values are for; the cache is
        # only reused while it is still this date at the location.
        "date": (daily.get("time") or [None])[0],
        "sunrise": sunrise,
        "sunset": sunset,
        "daylight_seconds": daylight_s,
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result


def _is_for_today(cached: dict[str, Any]) -> bool:
    """True when the cached sunrise is for the location's current date.

    The cache lives up to six hours, which can straddle the location's
    midnight and would otherwise keep painting yesterday's sunrise and
    sunset (#351). Uses the response's daily date, else the sunrise's
    date part; an entry with neither is treated as stale.
    """
    day = cached.get("date")
    if not isinstance(day, str) or not day:
        sunrise = cached.get("sunrise")
        if not isinstance(sunrise, str) or "T" not in sunrise:
            return False
        day = sunrise.split("T", 1)[0]
    now = location_now(cached.get("tz"), cached.get("utc_offset_seconds"))
    return day[:10] == now.date().isoformat()
