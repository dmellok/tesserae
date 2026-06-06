"""sky_bom_warnings, current severe weather warnings from the BoM.

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

CACHE_TTL_S = 300  # 5 min, warnings update on the order of 10s of minutes
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
            wtype = str(w.get("type") or "")
            group = str(w.get("warning_group_type") or "")
            short_title = str(w.get("short_title") or wtype or "Warning")
            title = str(w.get("title") or "")
            state_str = str(w.get("state") or "")
            states_list = [str(s) for s in states if s]
            issued = str(w.get("issue_time") or "")
            # Map BoM type → semantic icon role from wx-common.js PH map,
            # severity bucket (hazard/caution/advisory → red/yellow/blue),
            # and a short tag for the badge.
            sem = _semantics(wtype, group, phase)
            out.append(
                {
                    "id": str(w.get("id") or ""),
                    "type": wtype,
                    "short_title": short_title,
                    "title": title,
                    "state": state_str,
                    "states": states_list,
                    "group": group,
                    "phase": phase,
                    "issued": issued,
                    "expires": str(w.get("expiry_time") or ""),
                    # New variant fields (design-handoff shape).
                    "tag": sem["tag"],
                    "severity": sem["severity"],
                    "icon": sem["icon"],
                    "area": title or short_title,
                    "highlight": sem["severity"] == "red",
                }
            )
        except (AttributeError, TypeError):
            continue
    # Order by phase (new > update > cancelled), then issue time desc.
    phase_order = {"new": 0, "update": 1, "cancelled": 2}
    out.sort(key=lambda w: (phase_order.get(w["phase"], 9), -_iso_ts(w["issued"])))
    shown = out[:max_results]
    region = "Australia" if state == "ALL" else state
    place = region
    # Use the most-recent issued time as the "data freshness" marker.
    time_label = _hhmm(shown[0]["issued"]) if shown else ""
    return {
        "state": state,
        "total": len(out),
        "shown": min(len(out), max_results),
        "warnings": shown,
        "fetched_at": cache_record.get("fetched_at"),
        # Design-handoff WARNINGS shape, variants read these.
        "region": region,
        "place": place,
        "time": time_label,
        "count": len(out),
        "items": shown,
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


# BoM warning type → (tag, severity, icon-role) used by the variant
# render path. ``severity`` maps to the wx-common accent vocabulary:
#   red    = hazard   (life-threatening / major impact)
#   yellow = caution  (significant but generally not life-threatening)
#   blue   = advisory (informational / marine / flood watch)
# ``icon`` names come from the PH semantic-name map in wx-common.js.
_TYPE_SEMANTICS: dict[str, tuple[str, str, str]] = {
    # Fire / heat
    "bushfire": ("FIRE", "red", "fire"),
    "total_fire_ban": ("FIRE BAN", "red", "fire"),
    "fire_weather_warning": ("FIRE WX", "red", "fire"),
    "heat": ("HEAT", "red", "heat"),
    "heatwave": ("HEAT", "red", "heat"),
    # Flood
    "flood_warning": ("FLOOD", "red", "flood"),
    "flood_watch": ("FLOOD WATCH", "blue", "flood"),
    "minor_flood_warning": ("FLOOD", "yellow", "flood"),
    "moderate_flood_warning": ("FLOOD", "yellow", "flood"),
    "major_flood_warning": ("FLOOD", "red", "flood"),
    # Storm / severe weather
    "severe_thunderstorm_warning": ("STORM", "red", "storm"),
    "severe_weather_warning": ("SEVERE WX", "red", "warning"),
    "damaging_winds": ("WIND", "yellow", "wind"),
    # Marine / coastal
    "marine_wind_warning": ("MARINE", "blue", "marine"),
    "coastal": ("COASTAL", "blue", "marine"),
    "coastal_hazard": ("COASTAL", "blue", "marine"),
    # Cyclone
    "tropical_cyclone_warning": ("CYCLONE", "red", "warning"),
    "tropical_cyclone_advice": ("CYCLONE", "yellow", "warning"),
    # Cold / frost / graziers
    "frost_warning": ("FROST", "yellow", "frost"),
    "sheep_graziers_warning": ("GRAZIERS", "yellow", "thermometer"),
}


def _semantics(wtype: str, group: str, phase: str) -> dict[str, str]:
    """Map a BoM type → tag/severity/icon used by the new variants.

    Falls back on the warning_group_type ("major" → red, anything else →
    yellow). Cancelled warnings demote to blue (advisory) regardless of
    type so the colour-blocks read as low-priority.
    """
    tag, sev, icon = _TYPE_SEMANTICS.get(wtype.lower(), (_default_tag(wtype), "yellow", "warning"))
    g = (group or "").lower()
    if g == "major":
        sev = "red"
    elif g == "minor" and sev == "red":
        # If the upstream tags a fire/flood as "minor" prefer caution.
        sev = "yellow"
    if phase == "cancelled":
        sev = "blue"
    return {"tag": tag, "severity": sev, "icon": icon}


def _default_tag(wtype: str) -> str:
    """Make a short uppercase tag from an unknown BoM warning type."""
    if not wtype:
        return "ALERT"
    # Strip "_warning" / "_watch" suffixes for compactness.
    base = wtype.lower().replace("_warning", "").replace("_watch", "")
    return base.replace("_", " ").upper()[:14] or "ALERT"


def _hhmm(iso: str) -> str:
    """ISO-8601 → 'HH:MM' (best-effort, returns '' on parse failure)."""
    if not iso:
        return ""
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except (ValueError, TypeError):
        # Fall back to slicing the ISO string if datetime can't parse it.
        if "T" in iso:
            tail = iso.split("T", 1)[1]
            return tail[:5] if len(tail) >= 5 else ""
        return ""
