"""Next-race fetch for the f1_next widget.

Jolpica-F1, no API key. Caches the next-race lookup for an hour in the
plugin's data_dir — the calendar barely changes, and every dashboard
that mounts the widget would otherwise hit the API on every render.
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 3600
HTTP_TIMEOUT_S = 10
USER_AGENT = "tesserae/0.1 (+f1_next)"
ENDPOINT = "https://api.jolpi.ca/ergast/f1/current/next.json"


def _cached(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime >= CACHE_TTL_S:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _session(obj: dict[str, Any] | None) -> dict[str, str] | None:
    if not obj:
        return None
    date = obj.get("date")
    if not date:
        return None
    return {"date": date, "time": obj.get("time") or ""}


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del options, settings
    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / "next.json"
    cached = _cached(cache_path)
    if cached is not None:
        return cached

    try:
        req = urllib.request.Request(ENDPOINT, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}

    races = ((payload.get("MRData") or {}).get("RaceTable") or {}).get("Races") or []
    if not races:
        return {"error": "no upcoming race in calendar"}
    race = races[0]
    circuit = race.get("Circuit") or {}
    location = circuit.get("Location") or {}

    result: dict[str, Any] = {
        "season": race.get("season"),
        "round": race.get("round"),
        "raceName": race.get("raceName"),
        "date": race.get("date"),
        "time": race.get("time") or "",
        "circuitId": circuit.get("circuitId"),
        "circuitName": circuit.get("circuitName"),
        "locality": location.get("locality"),
        "country": location.get("country"),
        "sessions": {
            "fp1": _session(race.get("FirstPractice")),
            "fp2": _session(race.get("SecondPractice")),
            "fp3": _session(race.get("ThirdPractice")),
            "sprint": _session(race.get("Sprint")),
            "qualifying": _session(race.get("Qualifying")),
        },
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result
