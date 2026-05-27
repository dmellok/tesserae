"""Next-departures fetch for the public_transport_times widget.

PTV Timetable API v3. Auth scheme: append ``devid=<id>`` to the query,
HMAC-SHA1 the path-plus-query, append ``signature=<hex_upper>``.

We cache for 60 seconds per (stop, mode) pair — PTV's rate limits are
generous but a busy dashboard re-renders constantly and the timetable
moves on a minute granularity anyway.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 60
HTTP_TIMEOUT_S = 10
USER_AGENT = "tesserae/0.1 (+public_transport_times)"
BASE = "https://timetableapi.ptv.vic.gov.au"


def _sign_url(path_with_query: str, devid: str, key: str) -> str:
    sep = "&" if "?" in path_with_query else "?"
    path_with_devid = f"{path_with_query}{sep}devid={devid}"
    sig = (
        hmac.new(
            key.encode("utf-8"),
            path_with_devid.encode("utf-8"),
            hashlib.sha1,
        )
        .hexdigest()
        .upper()
    )
    return f"{BASE}{path_with_devid}&signature={sig}"


def _cached(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime >= CACHE_TTL_S:
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _slim_departure(
    d: dict[str, Any],
    routes: dict[str, Any],
    directions: dict[str, Any],
) -> dict[str, Any]:
    route = routes.get(str(d.get("route_id"))) or {}
    direction = directions.get(str(d.get("direction_id"))) or {}
    return {
        "scheduled": d.get("scheduled_departure_utc"),
        "estimated": d.get("estimated_departure_utc"),
        "platform": d.get("platform_number") or "",
        "at_platform": bool(d.get("at_platform")),
        "route_number": route.get("route_number") or "",
        "route_name": route.get("route_name") or "",
        "direction_name": direction.get("direction_name") or "",
    }


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    devid = (settings.get("devid") or "").strip()
    key = (settings.get("key") or "").strip()
    if not devid or not key:
        return {
            "error": "PTV credentials not set. Visit Settings → Plugins → Public Transport "
            "and paste your developer ID and signing key from the PTV registration "
            "email."
        }

    try:
        stop_id = int(options.get("stop_id") or 0)
    except (TypeError, ValueError):
        stop_id = 0
    if stop_id <= 0:
        return {"error": "Set a stop_id on the cell (numeric PTV stop ID)."}

    route_type = int(options.get("route_type") or 0)
    max_results = int(options.get("max_results") or 5)
    direction_id = options.get("direction_id")
    stop_label = (options.get("stop_label") or "").strip()

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_key = f"dep_{route_type}_{stop_id}_{direction_id or 'all'}_{max_results}.json"
    cache_path = data_dir / cache_key
    cached = _cached(cache_path)
    if cached is not None:
        return cached

    qs = f"max_results={max_results}&include_cancelled=true&expand=Route&expand=Direction"
    if direction_id:
        with contextlib.suppress(TypeError, ValueError):
            qs += f"&direction_id={int(direction_id)}"
    path = f"/v3/departures/route_type/{route_type}/stop/{stop_id}?{qs}"
    url = _sign_url(path, devid, key)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    departures = payload.get("departures") or []
    routes = payload.get("routes") or {}
    directions = payload.get("directions") or {}
    stops = payload.get("stops") or {}
    stop_info = stops.get(str(stop_id)) or {}

    result: dict[str, Any] = {
        "stop_id": stop_id,
        "stop_name": stop_label or stop_info.get("stop_name") or f"Stop {stop_id}",
        "route_type": route_type,
        "departures": [_slim_departure(d, routes, directions) for d in departures[:max_results]],
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result))
    return result
