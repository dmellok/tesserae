"""sky_aurora, Kp index + visibility hint via NOAA SWPC."""

from __future__ import annotations

import contextlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 900  # 15 min, Kp updates roughly every 3 hours
HTTP_TIMEOUT_S = 12
USER_AGENT = "tesserae/0.1 (+sky_aurora)"

# https://services.swpc.noaa.gov/json/planetary_k_index_1m.json
# Array of {"time_tag", "kp_index", "estimated_kp", ...}, most recent last.
KP_NOWCAST_URL = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"

# https://services.swpc.noaa.gov/text/3-day-forecast.txt, text-format,
# we'll grab the structured forecast JSON instead:
KP_FORECAST_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"


def _get(url: str) -> Any:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _visibility_band(kp: float) -> tuple[str, float]:
    """Approximate equatorward limit of the auroral oval in degrees of
    magnetic latitude for a given Kp. Returns (label, lat_threshold).
    Tables are commonly published by NOAA; using rounded midpoints."""
    table = [
        (0, "Polar only", 67.5),
        (1, "Polar only", 66.5),
        (2, "Polar only", 65.0),
        (3, "Sub-polar", 63.0),
        (4, "High-latitude", 60.0),
        (5, "Mid-latitude", 57.5),
        (6, "Mid-latitude (G2)", 54.5),
        (7, "Lower mid (G3)", 51.5),
        (8, "Far south/north (G4)", 47.5),
        (9, "Extreme (G5)", 42.0),
    ]
    n = max(0, min(9, int(kp)))
    return table[n][1], table[n][2]


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    lat = float(options.get("latitude") or 0.0)
    abs_lat = abs(lat)

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"aurora_{lat:.2f}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    try:
        now = _get(KP_NOWCAST_URL)
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}
    if not isinstance(now, list) or not now:
        return {"error": "NOAA nowcast empty."}
    latest = now[-1]
    current_kp = float(latest.get("kp_index") or latest.get("estimated_kp") or 0)

    # 3-day forecast, NOAA returns a list of dicts:
    # [{"time_tag": ..., "kp": ..., "observed": "observed|predicted",
    #   "noaa_scale": ...}, ...]. Earlier the endpoint shipped a
    # list-of-lists with a header row; we accept both shapes so an
    # accidentally-rolled-back API doesn't break the widget.
    forecast_rows: list[dict[str, Any]] = []
    try:
        fc = _get(KP_FORECAST_URL)
    except Exception:
        fc = []
    if isinstance(fc, list):
        for entry in fc:
            with contextlib.suppress(ValueError, TypeError, KeyError):
                if isinstance(entry, dict):
                    forecast_rows.append(
                        {
                            "time": str(entry.get("time_tag") or ""),
                            "kp": float(entry.get("kp") or 0),
                            "kind": str(entry.get("observed") or "predicted"),
                        }
                    )
                elif isinstance(entry, list) and len(entry) >= 3 and entry[0] != "time_tag":
                    forecast_rows.append(
                        {
                            "time": str(entry[0]),
                            "kp": float(entry[1]),
                            "kind": str(entry[2]),
                        }
                    )
    forecast_rows.sort(key=lambda r: r.get("time") or "")
    # Future-only window: drop rows in the past.
    now_ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    future = [r for r in forecast_rows if r["time"] >= now_ts]
    max_kp_3d = max((r["kp"] for r in future), default=current_kp)

    band_label, oval_lat = _visibility_band(current_kp)
    forecast_label, forecast_oval = _visibility_band(max_kp_3d)
    visible_now = abs_lat >= oval_lat
    visible_soon = abs_lat >= forecast_oval

    result = {
        "lat": lat,
        "current_kp": round(current_kp, 1),
        "max_kp_3d": round(max_kp_3d, 1),
        "band_label": band_label,
        "forecast_band": forecast_label,
        "visible_now": visible_now,
        "visible_soon": visible_soon,
        "oval_lat": oval_lat,
        "forecast_oval": forecast_oval,
        "forecast": future[:24],  # 24 3-hour blocks = 3 days
        "fetched_at": int(time.time()),
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result
