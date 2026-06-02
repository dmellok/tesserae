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
from pathlib import Path
from typing import Any

from app.plugin_http import fetch_json, fetch_text

CACHE_TTL_S = 1800
HTTP_TIMEOUT_S = 15
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

# Overall-band thresholds — applied to the max of (tree, grass, weed) so
# the headline level word matches whichever species is worst. Aligned
# with the per-species bands the legacy widget already paints.
OVERALL_BANDS: list[tuple[float, str]] = [
    (30.0, "Low"),
    (100.0, "Moderate"),
    (300.0, "High"),
    (float("inf"), "Very High"),
]

# Per-species presentation metadata used by the variant renderers. The
# icon names map through wx-common.PH so they render as Phosphor glyphs.
# Accents follow the design handoff: tree=green, grass=yellow, weed=red.
SPECIES_PRESENTATION = [
    {"key": "tree", "label": "Tree", "icon": "tree", "accent": "green"},
    {"key": "grass", "label": "Grass", "icon": "grass", "accent": "yellow"},
    {"key": "weed", "label": "Weed", "icon": "weed", "accent": "red"},
]


def _cached(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime >= CACHE_TTL_S:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
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
        payload = fetch_json(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT_S)
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
    # Best-effort fallback — short timeout, no retries. melbournepollen
    # is third-party and not reliably fast; the upstream open-meteo
    # data is the canonical source. A slow scrape can't be allowed to
    # blow past the page hydration budget.
    try:
        html = fetch_text(
            "https://www.melbournepollen.com.au/",
            headers={"User-Agent": USER_AGENT},
            timeout=5.0,
        )
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


def _overall_level(values: list[float | None]) -> str:
    """Pick the overall headline level from the worst species reading.
    Returns "—" when nothing numeric is available so the variant
    renderers can still print something legible."""
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return "—"
    worst = max(nums)
    for ceiling, name in OVERALL_BANDS:
        if worst <= ceiling:
            return name
    return "Very High"


def _dominant_type(values: dict[str, float | None]) -> str:
    """The species driving the headline level — used by Refined/Geometric
    variants as the sub-title under the level word."""
    nums = {k: v for k, v in values.items() if isinstance(v, (int, float))}
    if not nums:
        return "Mixed"
    top_key = max(nums, key=lambda k: nums[k])
    return {"tree": "Tree", "grass": "Grass", "weed": "Weed"}.get(top_key, "Mixed")


def _hhmm_now() -> str:
    """Local wall-clock HH:MM — matches the headers on weather_now."""
    from datetime import datetime as _dt

    return _dt.now().strftime("%H:%M")


def _scale_max(values: list[float | None]) -> float:
    """Scale ceiling for the species bars. Anchor at 300 (High) so a
    typical day's bar fills meaningfully without an outlier flattening
    everything else; bump higher when we genuinely exceed it."""
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return 300.0
    return max(300.0, max(nums))


def _build_breakdown(values: dict[str, float | None], scale_max: float) -> list[dict[str, Any]]:
    """Per-species rows for the variant grid / bars / chips. ``level``
    is a 0-100 normalised position on the bar, ``value`` is the raw
    count (or em-dash when missing)."""
    rows: list[dict[str, Any]] = []
    for spec in SPECIES_PRESENTATION:
        raw = values.get(spec["key"])
        if isinstance(raw, (int, float)):
            display = round(raw)
            level = max(0.0, min(100.0, (raw / scale_max) * 100.0)) if scale_max else 0.0
        else:
            display = "—"
            level = 0
        rows.append(
            {
                "label": spec["label"],
                "value": display,
                "level": level,
                "accent": spec["accent"],
                "icon": spec["icon"],
            }
        )
    return rows


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

    # Structured fields for the variant renderers (Refined / Geometric /
    # Swiss / Data). When the scraper supplied a `grass_label` (text-only
    # MPC band), prefer that as the headline level word.
    species_vals = {
        "tree": primary.get("tree"),
        "grass": primary.get("grass"),
        "weed": primary.get("weed"),
    }
    overall = _overall_level(list(species_vals.values()))
    if primary.get("grass_label"):
        # MPC text levels normalise to title-case; "Extreme" → "Very High"
        # so the colour band matches the four-band design system.
        raw = str(primary["grass_label"]).strip()
        norm = {"Extreme": "Very High", "Off Season": "Low"}.get(raw, raw)
        if norm in {"Low", "Moderate", "High", "Very High"}:
            overall = norm
    scale_max = _scale_max(list(species_vals.values()))
    breakdown = _build_breakdown(species_vals, scale_max)

    label = options.get("label", "")
    result: dict[str, Any] = {
        "label": label,
        **primary,
        # New structured fields the variant renderers paint from.
        "place": label,
        "time": _hhmm_now(),
        "level": overall,
        "type": _dominant_type(species_vals),
        "breakdown": breakdown,
        "scaleMax": scale_max,
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result
