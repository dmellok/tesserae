"""Driver-standings fetch for the f1_standings_drivers widget.

Jolpica-F1, no API key. Caches for 3 hours — standings only change
after a race, and a slightly-stale view between race-end and the next
cache rebuild is fine.

Also fetches the previous round's standings (if available) and
computes a per-driver position delta so the client can show "moved up
2 / down 1 / unchanged" arrows in the row.
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

from app.plugin_http import fetch_json

CACHE_TTL_S = 3 * 3600
HTTP_TIMEOUT_S = 15
USER_AGENT = "tesserae/0.1 (+f1_standings_drivers)"
ENDPOINT = "https://api.jolpi.ca/ergast/f1/current/driverStandings.json"
PREV_ENDPOINT = "https://api.jolpi.ca/ergast/f1/current/{round}/driverStandings.json"


def _cached(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime >= CACHE_TTL_S:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _slim_standing(s: dict[str, Any], prev_positions: dict[str, int]) -> dict[str, Any]:
    driver = s.get("Driver") or {}
    constructors = s.get("Constructors") or []
    constructor = constructors[0] if constructors else {}
    driver_id = str(driver.get("driverId") or "")
    position_str = s.get("position")
    try:
        position_int = int(position_str) if position_str is not None else None
    except (TypeError, ValueError):
        position_int = None
    prev_position = prev_positions.get(driver_id)
    # delta convention: positive = moved UP the table (lower position
    # number is better). None when there's no previous-round data
    # (first race of the season, new driver, etc.) so the client knows
    # to draw a neutral chip rather than guessing.
    if prev_position is not None and position_int is not None:
        delta = prev_position - position_int
    else:
        delta = None
    return {
        "position": position_str,
        "points": s.get("points"),
        "wins": s.get("wins"),
        "code": driver.get("code") or "",
        "given": driver.get("givenName") or "",
        "family": driver.get("familyName") or "",
        "constructor": constructor.get("name") or "",
        "constructorId": constructor.get("constructorId") or "",
        "delta": delta,
    }


def _fetch_prev_positions(round_num: int) -> dict[str, int]:
    """Map driverId → finishing position after the previous round.

    Best-effort: returns {} on any error so the position-delta column
    just degrades to neutral instead of failing the whole widget. We
    pull from /current/{round-1}/driverStandings.json so the delta is
    "vs immediately previous race" — over the season the deltas
    aggregate naturally as the user watches the season progress."""
    if round_num <= 1:
        return {}
    url = f"{PREV_ENDPOINT.format(round=round_num - 1)}?limit=40"
    try:
        payload = fetch_json(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT_S)
    except Exception:
        return {}
    lists = ((payload.get("MRData") or {}).get("StandingsTable") or {}).get("StandingsLists") or []
    if not lists:
        return {}
    prev: dict[str, int] = {}
    for entry in lists[0].get("DriverStandings") or []:
        did = str(((entry.get("Driver") or {}).get("driverId")) or "")
        pos_raw = entry.get("position")
        try:
            pos_int = int(pos_raw) if pos_raw is not None else None
        except (TypeError, ValueError):
            pos_int = None
        if did and pos_int is not None:
            prev[did] = pos_int
    return prev


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

    url = f"{ENDPOINT}?limit=40"
    try:
        payload = fetch_json(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT_S)
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    lists = ((payload.get("MRData") or {}).get("StandingsTable") or {}).get("StandingsLists") or []
    if not lists:
        return {"error": "no standings yet"}
    table = lists[0]
    standings = table.get("DriverStandings") or []

    round_str = table.get("round")
    try:
        round_int = int(round_str) if round_str is not None else 0
    except (TypeError, ValueError):
        round_int = 0
    prev_positions = _fetch_prev_positions(round_int)

    result: dict[str, Any] = {
        "season": table.get("season"),
        "round": table.get("round"),
        "standings": [_slim_standing(s, prev_positions) for s in standings],
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result
