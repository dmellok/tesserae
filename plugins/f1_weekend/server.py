"""Race-weekend fetch for the f1_weekend widget.

Same upstream as f1_next (Jolpica /current/next.json) but kept in its
own plugin so each widget has independent caching + cell options. One
HTTP call per plugin per hour — the calendar barely changes.
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
USER_AGENT = "tesserae/0.1 (+f1_weekend)"
ENDPOINT = "https://api.jolpi.ca/ergast/f1/current/next.json"


def _cached(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime >= CACHE_TTL_S:
        return None
    try:
        return json.loads(path.read_text())
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
    cache_path = data_dir / "weekend.json"
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

    # Build an ordered list of sessions actually present. Jolpica omits
    # FP3 on sprint weekends and Sprint on non-sprint weekends, so we
    # can't assume a fixed schedule.
    raw_sessions = [
        ("FP1", _session(race.get("FirstPractice"))),
        ("FP2", _session(race.get("SecondPractice"))),
        ("FP3", _session(race.get("ThirdPractice"))),
        ("SPRINT_Q", _session(race.get("SprintQualifying"))),
        ("SPRINT", _session(race.get("Sprint"))),
        ("QUAL", _session(race.get("Qualifying"))),
        (
            "RACE",
            {"date": race.get("date"), "time": race.get("time") or ""}
            if race.get("date")
            else None,
        ),
    ]
    sessions = [
        {"label": label, "date": s["date"], "time": s["time"]} for label, s in raw_sessions if s
    ]

    result: dict[str, Any] = {
        "season": race.get("season"),
        "round": race.get("round"),
        "raceName": race.get("raceName"),
        "circuitId": circuit.get("circuitId"),
        "circuitName": circuit.get("circuitName"),
        "locality": location.get("locality"),
        "country": location.get("country"),
        "sessions": sessions,
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result))
    return result
