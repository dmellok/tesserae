"""Last-race fetch for the f1_last_race widget.

Jolpica-F1, no API key. Caches for 12 hours — results don't change once
the race is over, and a stale cache between race-end and the next
refresh is harmless (we'll get the next race's podium on the next pull).
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 12 * 3600
HTTP_TIMEOUT_S = 10
USER_AGENT = "tesserae/0.1 (+f1_last_race)"
ENDPOINT = "https://api.jolpi.ca/ergast/f1/current/last/results.json"


def _cached(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime >= CACHE_TTL_S:
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _slim_result(r: dict[str, Any]) -> dict[str, Any]:
    driver = r.get("Driver") or {}
    constructor = r.get("Constructor") or {}
    time_obj = r.get("Time") or {}
    fastest = r.get("FastestLap") or {}
    return {
        "position":   r.get("position"),
        "code":       driver.get("code") or "",
        "given":      driver.get("givenName") or "",
        "family":     driver.get("familyName") or "",
        "constructor":     constructor.get("name") or "",
        "constructorId":   constructor.get("constructorId") or "",
        "time":       time_obj.get("time") or "",  # "1:28:15.758" for P1, "+10.768" for others
        "points":     r.get("points"),
        "status":     r.get("status") or "",
        "fastest":    fastest.get("rank") == "1",
    }


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del options, settings
    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / "last.json"
    cached = _cached(cache_path)
    if cached is not None:
        return cached

    try:
        req = urllib.request.Request(ENDPOINT, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    races = (((payload.get("MRData") or {}).get("RaceTable") or {}).get("Races") or [])
    if not races:
        return {"error": "no completed race in calendar"}
    race = races[0]
    circuit = race.get("Circuit") or {}
    location = circuit.get("Location") or {}
    results = race.get("Results") or []

    result: dict[str, Any] = {
        "season":      race.get("season"),
        "round":       race.get("round"),
        "raceName":    race.get("raceName"),
        "date":        race.get("date"),
        "circuitId":   circuit.get("circuitId"),
        "circuitName": circuit.get("circuitName"),
        "locality":    location.get("locality"),
        "country":     location.get("country"),
        "podium":      [_slim_result(r) for r in results[:3]],
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result))
    return result
