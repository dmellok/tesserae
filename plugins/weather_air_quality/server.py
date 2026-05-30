"""Air-quality fetch for the weather_air_quality widget.

Open-Meteo CAMS air-quality endpoint, no API key. Caches per
``(lat, lon)`` for 30 minutes — AQI numbers update hourly upstream and
the dashboard re-renders constantly, so keeping a short cache stops us
from hammering the API.
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 1800
HTTP_TIMEOUT_S = 10
USER_AGENT = "tesserae/0.1 (+weather_air_quality)"


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
    scale = str(options.get("scale", "european"))

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / f"aqi_{lat:.3f}_{lon:.3f}.json"
    cached = _cached(cache_path)
    if cached is not None:
        # Re-apply current scale choice from options — cached payload has
        # both EAQI and US AQI, only the surfaced one depends on options.
        cached["scale"] = scale
        return cached

    url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={lat}&longitude={lon}"
        "&current=european_aqi,us_aqi,pm10,pm2_5,carbon_monoxide,"
        "nitrogen_dioxide,sulphur_dioxide,ozone"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    current = payload.get("current") or {}

    result: dict[str, Any] = {
        "label": options.get("label", ""),
        "scale": scale,
        "european_aqi": current.get("european_aqi"),
        "us_aqi": current.get("us_aqi"),
        "pm2_5": current.get("pm2_5"),
        "pm10": current.get("pm10"),
        "ozone": current.get("ozone"),
        "no2": current.get("nitrogen_dioxide"),
        "so2": current.get("sulphur_dioxide"),
        "co": current.get("carbon_monoxide"),
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result
