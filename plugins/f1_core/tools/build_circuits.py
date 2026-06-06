"""Preprocess bacinger/f1-circuits GeoJSONs into a single circuits.json
keyed by Jolpica circuitId, the runtime artifact every f1_* widget reads.

Run from anywhere:

    python plugins/f1_core/tools/build_circuits.py

Re-run when:
  * the F1 calendar gains a new circuit (add it to CIRCUIT_MAP below)
  * bacinger publishes a new track layout (the file is the same name, so
    just re-run and commit the updated circuits.json)

Output: plugins/f1_core/static/circuits.json
Source: https://github.com/bacinger/f1-circuits (MIT)
"""

from __future__ import annotations

import json
import math
import urllib.request
from pathlib import Path
from typing import Any

# Jolpica circuitId -> bacinger filename (without .geojson).
# Covers the 2026 calendar and a handful of recently-active circuits
# so older results / standings widgets can still draw a track.
CIRCUIT_MAP: dict[str, str] = {
    "albert_park": "au-1953",
    "shanghai": "cn-2004",
    "suzuka": "jp-1962",
    "bahrain": "bh-2002",
    "jeddah": "sa-2021",
    "miami": "us-2022",
    "imola": "it-1953",
    "monaco": "mc-1929",
    "catalunya": "es-1991",
    "villeneuve": "ca-1978",
    "red_bull_ring": "at-1969",
    "silverstone": "gb-1948",
    "spa": "be-1925",
    "hungaroring": "hu-1986",
    "zandvoort": "nl-1948",
    "monza": "it-1922",
    "madring": "es-2026",
    "baku": "az-2016",
    "marina_bay": "sg-2008",
    "americas": "us-2012",
    "rodriguez": "mx-1962",
    "interlagos": "br-1977",
    "vegas": "us-2023",
    "losail": "qa-2004",
    "yas_marina": "ae-2009",
}

BACINGER_RAW = "https://raw.githubusercontent.com/bacinger/f1-circuits/master/circuits/{}.geojson"
VIEWBOX_W = 1000
PADDING = 20  # SVG units on each side so the path doesn't kiss the edge


def fetch(name: str) -> dict[str, Any]:
    url = BACINGER_RAW.format(name)
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))  # type: ignore[no-any-return]


def project(coords: list[list[float]]) -> tuple[str, str]:
    """Equirectangular projection with a cos(lat) x-scale, accurate
    enough at the scale of a single track. Returns (viewBox, path)."""
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)
    center_lat = (min_lat + max_lat) / 2
    x_scale = math.cos(math.radians(center_lat))

    # Convert to metres-ish units relative to the bbox origin.
    pts = []
    for lon, lat in coords:
        x = (lon - min_lon) * x_scale
        y = -(lat - min_lat)  # invert: SVG y points down
        pts.append((x, y))

    span_x = (max_lon - min_lon) * x_scale
    span_y = max_lat - min_lat
    if span_x == 0 or span_y == 0:
        raise ValueError("degenerate bbox")

    # Fit into VIEWBOX_W wide, preserve aspect.
    inner_w = VIEWBOX_W - 2 * PADDING
    scale = inner_w / span_x
    inner_h = span_y * scale
    h = inner_h + 2 * PADDING

    out_pts = [(PADDING + px * scale, PADDING + inner_h + py * scale) for px, py in pts]
    # Round to 1dp, invisible at any reasonable cell size, ~30% smaller JSON.
    d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in out_pts) + " Z"
    viewBox = f"0 0 {VIEWBOX_W} {h:.1f}"
    return viewBox, d


def main() -> None:
    out: dict[str, dict[str, Any]] = {}
    for circuit_id, file_id in CIRCUIT_MAP.items():
        try:
            geo = fetch(file_id)
        except Exception as err:
            print(f"  ! {circuit_id} ({file_id}): {type(err).__name__}: {err}")
            continue

        feat = (geo.get("features") or [None])[0]
        if not feat:
            print(f"  ! {circuit_id}: no features")
            continue
        geom = feat.get("geometry") or {}
        if geom.get("type") != "LineString":
            print(f"  ! {circuit_id}: geometry is {geom.get('type')}, skipping")
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 4:
            print(f"  ! {circuit_id}: too few coords ({len(coords)})")
            continue

        viewBox, d = project(coords)
        props = feat.get("properties") or {}
        out[circuit_id] = {
            "name": props.get("Name") or "",
            "location": props.get("Location") or "",
            "length_m": props.get("length") or 0,
            "viewBox": viewBox,
            "d": d,
        }
        print(f"  ok {circuit_id} ({file_id}) -> {len(coords)} pts")

    target = Path(__file__).resolve().parents[1] / "static" / "circuits.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"\nWrote {len(out)} circuits to {target} ({target.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
