"""Driver-standings fetch for the f1_standings_drivers widget.

Jolpica-F1, no API key. Caches for 3 hours — standings only change
after a race, and a slightly-stale view between race-end and the next
cache rebuild is fine.
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 3 * 3600
HTTP_TIMEOUT_S = 10
USER_AGENT = "tesserae/0.1 (+f1_standings_drivers)"
ENDPOINT = "https://api.jolpi.ca/ergast/f1/current/driverStandings.json"


def _cached(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime >= CACHE_TTL_S:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _slim_standing(s: dict[str, Any]) -> dict[str, Any]:
    driver = s.get("Driver") or {}
    constructors = s.get("Constructors") or []
    constructor = constructors[0] if constructors else {}
    return {
        "position": s.get("position"),
        "points": s.get("points"),
        "wins": s.get("wins"),
        "code": driver.get("code") or "",
        "given": driver.get("givenName") or "",
        "family": driver.get("familyName") or "",
        "constructor": constructor.get("name") or "",
        "constructorId": constructor.get("constructorId") or "",
    }


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del options, settings
    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / "drivers.json"
    cached = _cached(cache_path)
    if cached is not None:
        return cached

    # Bump the default limit so we always receive the full grid (Jolpica
    # defaults to 30 which is plenty for 20-driver F1, but be explicit).
    url = f"{ENDPOINT}?limit=40"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    lists = ((payload.get("MRData") or {}).get("StandingsTable") or {}).get("StandingsLists") or []
    if not lists:
        return {"error": "no standings yet"}
    table = lists[0]
    standings = table.get("DriverStandings") or []

    result: dict[str, Any] = {
        "season": table.get("season"),
        "round": table.get("round"),
        "standings": [_slim_standing(s) for s in standings],
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result
