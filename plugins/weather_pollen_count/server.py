"""Pollen fetch for the weather_pollen_count widget.

Primary source is Open-Meteo's CAMS Air Quality endpoint, which only
returns pollen for Europe (CAMS Europe coverage). When the upstream
returns nulls AND the coordinates fall inside Australia, fall back to a
HTML scrape of melbournepollen.com.au — it only carries grass pollen
and only at a Low/Moderate/High level (no numeric count), so the
fallback fills in `grass_label` instead of grams/m³.

Cache TTL is 30 min because both sources update at most hourly and a
dashboard re-render shouldn't pull twice in a row.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 1800
HTTP_TIMEOUT_S = 10
USER_AGENT = "tesserae/0.1 (+weather_pollen_count)"

# Australia bounding box — used to decide whether the MPC fallback is
# even worth attempting. Loose box; the scrape itself will return None
# off-season anyway.
AU_LAT = (-45.0, -10.0)
AU_LON = (110.0, 155.0)

# Levels Melbourne Pollen Count publishes, in descending severity so we
# match the strongest one when scanning the page.
MPC_LEVELS = ["Off Season", "Extreme", "Very High", "High", "Moderate", "Low"]

# Approx numeric mid-point per Melbourne Pollen Count band, so the
# scraped category can map back onto the same colour-coded bands the
# Open-Meteo branch uses. Grains/m³ rough midpoints.
MPC_LEVEL_TO_COUNT = {
    "Low": 15,
    "Moderate": 50,
    "High": 150,
    "Very High": 250,
    "Extreme": 400,
}


def _cached(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime >= CACHE_TTL_S:
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _open_meteo(lat: float, lon: float) -> dict[str, Any] | None:
    url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={lat}&longitude={lon}"
        "&current=alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,"
        "olive_pollen,ragweed_pollen"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    current = payload.get("current") or {}

    # Aggregate the individual species into broad categories the widget
    # actually displays.
    def _agg(*keys: str) -> float | None:
        vals = [current.get(k) for k in keys]
        nums = [v for v in vals if isinstance(v, (int, float))]
        return max(nums) if nums else None

    tree = _agg("alder_pollen", "birch_pollen", "olive_pollen")
    grass = _agg("grass_pollen")
    weed = _agg("mugwort_pollen", "ragweed_pollen")
    if tree is None and grass is None and weed is None:
        return None
    return {
        "grass": grass,
        "tree": tree,
        "weed": weed,
        "grass_label": None,
        "source": "open-meteo.com",
    }


def _scrape_melbourne_pollen() -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(
            "https://www.melbournepollen.com.au/",
            headers={"User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

    # The home page repeats "Today's Grass Pollen Forecast for Melbourne"
    # followed shortly by one of the MPC_LEVELS strings. Scan the first
    # 1500 chars after that anchor for the strongest level we find.
    anchor = re.search(r"Today.{0,4}s Grass Pollen Forecast", html, re.IGNORECASE)
    if not anchor:
        return None
    tail = html[anchor.end() : anchor.end() + 1500]
    tail = re.sub(r"<[^>]+>", " ", tail)  # strip tags so we match on text
    for level in MPC_LEVELS:
        if re.search(rf"\b{re.escape(level)}\b", tail, re.IGNORECASE):
            count = MPC_LEVEL_TO_COUNT.get(level)
            return {
                "grass": count,
                "tree": None,
                "weed": None,
                "grass_label": level,
                "source": "melbournepollen.com.au",
            }
    return None


def _is_australia(lat: float, lon: float) -> bool:
    return AU_LAT[0] <= lat <= AU_LAT[1] and AU_LON[0] <= lon <= AU_LON[1]


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    lat = float(options.get("latitude", 0.0))
    lon = float(options.get("longitude", 0.0))

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / f"pollen_{lat:.3f}_{lon:.3f}.json"
    cached = _cached(cache_path)
    if cached is not None:
        return cached

    primary = _open_meteo(lat, lon)
    if primary is None and _is_australia(lat, lon):
        primary = _scrape_melbourne_pollen()
    if primary is None:
        # No upstream returned anything — surface a friendly empty state
        # so the cell still renders the widget shell.
        primary = {
            "grass": None,
            "tree": None,
            "weed": None,
            "grass_label": None,
            "source": "",
        }

    result: dict[str, Any] = {
        "label": options.get("label", ""),
        **primary,
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result))
    return result
