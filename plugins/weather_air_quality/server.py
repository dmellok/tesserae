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
from pathlib import Path
from typing import Any

from app.plugin_http import fetch_json

CACHE_TTL_S = 1800
HTTP_TIMEOUT_S = 15
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
        payload = fetch_json(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT_S)
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    current = payload.get("current") or {}

    eaqi = current.get("european_aqi")
    band, band_index = _eaqi_band(eaqi)
    pollutants = _pollutants(current)
    dominant = _dominant(pollutants)

    result: dict[str, Any] = {
        "label": options.get("label", ""),
        "scale": scale,
        # Legacy fields — the old client.js render path still uses these.
        "european_aqi": eaqi,
        "us_aqi": current.get("us_aqi"),
        "pm2_5": current.get("pm2_5"),
        "pm10": current.get("pm10"),
        "ozone": current.get("ozone"),
        "no2": current.get("nitrogen_dioxide"),
        "so2": current.get("sulphur_dioxide"),
        "co": current.get("carbon_monoxide"),
        # Structured fields the new variants paint from.
        "eaqi": eaqi,
        "band": band,
        "bandIndex": band_index,
        "dominant": dominant,
        "bands": ["Good", "Fair", "Moderate", "Poor", "Very poor", "Extreme"],
        "edges": [0, 20, 40, 60, 80, 100],
        "pollutants": pollutants,
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result


# ----------------------------------------------------------------------
# Helpers used only by the structured / variant data path.
# ----------------------------------------------------------------------


# European AQI overall scale (0–100+), 6 bands.
_EAQI_NAMES = ("Good", "Fair", "Moderate", "Poor", "Very poor", "Extreme")
_EAQI_EDGES = (20, 40, 60, 80, 100)

# Per-pollutant EAQI breakpoints (μg/m³ except CO in mg/m³ → μg/m³).
# Source: European Environment Agency EAQI definitions. Each tuple lists
# the upper bound for bands 0..4; anything above 4 is band 5 (Extreme).
_POLLUTANT_BANDS: dict[str, tuple[float, ...]] = {
    "pm2_5": (10, 20, 25, 50, 75),
    "pm10": (20, 40, 50, 100, 150),
    "o3": (50, 100, 130, 240, 380),
    "no2": (40, 90, 120, 230, 340),
    "so2": (100, 200, 350, 500, 750),
    "co": (4400, 9400, 12400, 15400, 30400),
}

# Display metadata for each pollutant — label, unit, icon name (wx-common
# vocabulary) and accent. Order matches the design handoff's 6-tile grid.
_POLLUTANT_META = (
    ("pm2_5", "PM2.5", "μg/m³", "pm2", "ink"),
    ("pm10", "PM10", "μg/m³", "pm", "ink"),
    ("ozone", "O3", "μg/m³", "ozone", "blue"),
    ("nitrogen_dioxide", "NO2", "μg/m³", "no2", "red"),
    ("sulphur_dioxide", "SO2", "μg/m³", "so2", "yellow"),
    ("carbon_monoxide", "CO", "μg/m³", "co", "muted"),
)


def _eaqi_band(value: Any) -> tuple[str, int]:
    """Map an overall EAQI 0–100+ to (band name, band index 0..5)."""
    if not isinstance(value, (int, float)):
        return ("—", 0)
    for i, edge in enumerate(_EAQI_EDGES):
        if value <= edge:
            return (_EAQI_NAMES[i], i)
    return (_EAQI_NAMES[-1], len(_EAQI_NAMES) - 1)


def _pollutant_band(key: str, value: Any) -> tuple[str, int, float]:
    """For a pollutant, return (band name, band index 0..5, band max).

    ``band max`` is the upper bound of the *active* band — used by the
    D4 bar so the bar fills to "your band's ceiling" rather than the
    chart-wide max, which would compress every reading near zero."""
    edges = _POLLUTANT_BANDS.get(key)
    if edges is None or not isinstance(value, (int, float)):
        return ("—", 0, 1.0)
    for i, edge in enumerate(edges):
        if value <= edge:
            return (_EAQI_NAMES[i], i, float(edge))
    return (_EAQI_NAMES[-1], len(_EAQI_NAMES) - 1, float(edges[-1] * 2))


def _pollutants(current: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the 6-pollutant list the new variants paint from."""
    band_key = {
        "pm2_5": "pm2_5",
        "pm10": "pm10",
        "ozone": "o3",
        "nitrogen_dioxide": "no2",
        "sulphur_dioxide": "so2",
        "carbon_monoxide": "co",
    }
    out: list[dict[str, Any]] = []
    for api_key, label, unit, icon, accent in _POLLUTANT_META:
        v = current.get(api_key)
        rounded: Any = v
        if isinstance(v, (int, float)):
            rounded = round(v, 1) if v < 100 else round(v)
        band, band_index, band_max = _pollutant_band(band_key[api_key], v)
        out.append(
            {
                "label": label,
                "value": rounded,
                "unit": unit,
                # ``level`` mirrors ``value`` numerically — kept separate so
                # the D4 bar always reads from a numeric field even if value
                # is a string placeholder.
                "level": v if isinstance(v, (int, float)) else 0,
                "max": band_max,
                "accent": accent,
                "icon": icon,
                "band": band,
                "bandIndex": band_index,
            }
        )
    return out


def _dominant(pollutants: list[dict[str, Any]]) -> str:
    """Return the label of the pollutant with the highest band index —
    the design's "dominant" call-out. Ties break to whichever appears
    first in the list (PM2.5 / PM10 lead the order intentionally)."""
    best = -1
    name = "—"
    for p in pollutants:
        idx = p.get("bandIndex", 0)
        if isinstance(idx, int) and idx > best:
            best = idx
            name = p.get("label", "—")
    return name
