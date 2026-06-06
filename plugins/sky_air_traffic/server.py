"""sky_air_traffic, flights overhead via OpenSky."""

from __future__ import annotations

import contextlib
import json
import math
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 30  # OpenSky updates every ~10s for anon; cache to stay polite
HTTP_TIMEOUT_S = 15
USER_AGENT = "tesserae/0.1 (+sky_air_traffic)"

# OpenSky state-vector tuple positions (0-indexed):
#   0 icao24, 1 callsign, 2 origin_country, 3 time_position, 4 last_contact,
#   5 longitude, 6 latitude, 7 baro_altitude (m), 8 on_ground, 9 velocity (m/s),
#   10 true_track (deg), 11 vertical_rate, 13 geo_altitude, ...
S_CALLSIGN = 1
S_COUNTRY = 2
S_LON = 5
S_LAT = 6
S_BARO_ALT = 7
S_ON_GROUND = 8
S_VELOCITY = 9
S_TRACK = 10
S_VERTICAL = 11
S_GEO_ALT = 13


def _bbox(lat: float, lon: float, km: float) -> tuple[float, float, float, float]:
    dlat = km / 111.0
    dlon = km / (111.0 * max(0.1, math.cos(math.radians(lat))))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    lat = float(options.get("latitude") or 0.0)
    lon = float(options.get("longitude") or 0.0)
    radius = max(5.0, float(options.get("radius_km") or 60))
    max_results = max(1, int(options.get("max_results") or 8))

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"at_{lat:.3f}_{lon:.3f}_{int(radius)}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    la_min, lo_min, la_max, lo_max = _bbox(lat, lon, radius)
    url = (
        "https://opensky-network.org/api/states/all"
        f"?lamin={la_min:.4f}&lomin={lo_min:.4f}&lamax={la_max:.4f}&lomax={lo_max:.4f}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "flights": []}

    states = payload.get("states") or []
    flights = []
    for s in states:
        try:
            f_lat = s[S_LAT]
            f_lon = s[S_LON]
        except (IndexError, TypeError):
            continue
        if f_lat is None or f_lon is None:
            continue
        d = _haversine_km(lat, lon, f_lat, f_lon)
        if d > radius:
            continue
        flights.append(
            {
                "callsign": (s[S_CALLSIGN] or "").strip() if len(s) > S_CALLSIGN else "",
                "country": (s[S_COUNTRY] or "").strip() if len(s) > S_COUNTRY else "",
                "altitude": s[S_BARO_ALT] if len(s) > S_BARO_ALT else None,
                "velocity": s[S_VELOCITY] if len(s) > S_VELOCITY else None,
                "track": s[S_TRACK] if len(s) > S_TRACK else None,
                "vertical_rate": s[S_VERTICAL] if len(s) > S_VERTICAL else None,
                "on_ground": bool(s[S_ON_GROUND]) if len(s) > S_ON_GROUND else False,
                "lat": f_lat,
                "lon": f_lon,
                "distance_km": round(d, 1),
            }
        )
    flights.sort(key=lambda f: f["distance_km"])
    flights = flights[:max_results]

    result = {
        "lat": lat,
        "lon": lon,
        "radius": radius,
        "count": len(states),  # total in bounding box (incl out-of-radius)
        "shown": len(flights),
        "flights": flights,
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result
