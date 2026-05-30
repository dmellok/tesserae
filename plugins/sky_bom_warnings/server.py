"""sky_bom_warnings — current severe weather warnings from the BoM.

Hits api.weather.bom.gov.au/v1/warnings (the same backend powering the
BoM website itself; no key, treat as personal/non-redistribution per
BoM's terms). Filters by state and returns a slim per-warning dict
the client renders into a colour-blocked card list.
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 300  # 5 min — warnings update on the order of 10s of minutes
HTTP_TIMEOUT_S = 12
USER_AGENT = "tesserae/0.1 (+sky_bom_warnings)"
WARNINGS_URL = "https://api.weather.bom.gov.au/v1/warnings"

VALID_STATES = {"ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA", "ALL"}


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    state = str(options.get("state") or "VIC").upper()
    if state not in VALID_STATES:
        state = "VIC"
    max_results = max(1, min(12, int(options.get("max_results") or 5)))
    hide_cancelled = bool(options.get("hide_cancelled", True))

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"bom_warnings_{state}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            # Honour the freshly-set max_results / hide_cancelled even
            # when serving cached raw data.
            return _apply_view(cached, max_results, hide_cancelled, state)
        except (json.JSONDecodeError, OSError):
            pass

    try:
        req = urllib.request.Request(WARNINGS_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "warnings": []}

    raw_warnings = payload.get("data") or []
    cache_record = {
        "fetched_at": int(time.time()),
        "raw": raw_warnings,
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(cache_record), encoding="utf-8")
    return _apply_view(cache_record, max_results, hide_cancelled, state)


def _apply_view(
    cache_record: dict[str, Any], max_results: int, hide_cancelled: bool, state: str
) -> dict[str, Any]:
    raw = cache_record.get("raw") or []
    out: list[dict[str, Any]] = []
    for w in raw:
        try:
            states = w.get("states") or [w.get("state")]
            if state != "ALL" and state not in states:
                continue
            phase = (w.get("phase") or "").lower()
            if hide_cancelled and phase == "cancelled":
                continue
            out.append(
                {
                    "id": str(w.get("id") or ""),
                    "type": str(w.get("type") or ""),
                    "short_title": str(w.get("short_title") or w.get("type") or "Warning"),
                    "title": str(w.get("title") or ""),
                    "state": str(w.get("state") or ""),
                    "states": [str(s) for s in states if s],
                    "group": str(w.get("warning_group_type") or ""),
                    "phase": phase,
                    "issued": str(w.get("issue_time") or ""),
                    "expires": str(w.get("expiry_time") or ""),
                }
            )
        except (AttributeError, TypeError):
            continue
    # Order by phase (new > update > cancelled), then issue time desc.
    phase_order = {"new": 0, "update": 1, "cancelled": 2}
    out.sort(key=lambda w: (phase_order.get(w["phase"], 9), -_iso_ts(w["issued"])))
    return {
        "state": state,
        "total": len(out),
        "shown": min(len(out), max_results),
        "warnings": out[:max_results],
        "fetched_at": cache_record.get("fetched_at"),
    }


def _iso_ts(iso: str) -> int:
    """ISO-8601 -> unix epoch (0 on parse failure). Used as a sort key."""
    if not iso:
        return 0
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return 0
