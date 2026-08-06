"""ha_automation_history — which Home Assistant automations fired, when, how often.

Thin widget over ha_core. ``get_states`` gives every ``automation.*`` entity
with its ``last_triggered`` attribute in one call; a per-automation logbook
fetch over a rolling 7-day window gives the trigger history we bucket into
1h / 24h / 7d counts, a most-/least-fired weekly ranking, and a most-recent-
first "recently active" feed (one row per automation). An automation whose
last trigger is older than the configured cadence is flagged stale.

Cost control: we only hit the logbook for automations that actually fired in
the last 7 days (per ``last_triggered``), capped at ``MAX_LOGBOOK`` of them, so
a big install with hundreds of dormant automations stays a couple of REST
calls. Results are cached in ctx["data_dir"] for the refresh window so composer
re-renders don't re-poll.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from flask import current_app

WINDOW_DAYS = 7
MAX_LOGBOOK = 12  # cap per-automation logbook fetches per render
RECENT_LIMIT = 8  # rows in the recently-active feed
HOUR_S = 3600
DAY_S = 86400


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


def choices(name: str) -> list[dict[str, str]]:
    """Automation entity multi-select for the editor."""
    core = _core()
    if name == "entity" and core is not None:
        return core.entity_choices(domains=("automation",))
    return []


def _entity_list(raw: Any) -> list[str]:
    """Accept the multiselect's list, or a legacy comma/newline string."""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [tok for tok in re.split(r"[\s,]+", str(raw or "").strip()) if tok]


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _is_trigger(entry: dict[str, Any]) -> bool:
    """A logbook entry for the automation actually firing, not it being
    enabled/disabled (those carry an on/off state) or an unrelated note."""
    if entry.get("state") in ("on", "off"):
        return False
    msg = str(entry.get("message") or "").lower()
    return not (msg and "trigger" not in msg)


def _refresh_s(raw: Any) -> int:
    try:
        return max(30, int(float(raw)))
    except (TypeError, ValueError):
        return 60


def _stale_hours(raw: Any) -> float:
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _cache_slug(eids: list[str], stale_h: float) -> str:
    basis = ",".join(sorted(eids)) + f"|{stale_h}"
    return re.sub(r"[^A-Za-z0-9]", "_", basis)[:80] or "all"


def _cache_path(data_dir: Any, eids: list[str], stale_h: float) -> Path | None:
    """Cache file for this option set, or None when there's no data_dir
    (e.g. unit tests pass ctx={}), so caching stays best-effort."""
    if not data_dir:
        return None
    try:
        d = Path(data_dir)
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return d / f"result_{_cache_slug(eids, stale_h)}.json"


def _rank(a: dict[str, Any] | None) -> dict[str, Any] | None:
    if not a:
        return None
    return {"name": a["name"], "eid": a["eid"], "c7": a["c7"]}


def _logbook(
    core: Any, eid: str, start_dt: datetime, end_dt: datetime
) -> tuple[list[dict[str, Any]], str | None]:
    """Logbook entries for one automation over [start, end] → (entries, err)."""
    path = (
        f"/api/logbook/{quote(start_dt.isoformat())}"
        f"?entity={quote(eid)}&end_time={quote(end_dt.isoformat())}"
    )
    try:
        data = core.request_json(path, timeout=12)
    except Exception as err:
        return [], core.coerce_error(err)
    return ([e for e in data if isinstance(e, dict)] if isinstance(data, list) else []), None


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    core = _core()
    if core is None:
        return {"error": "Install the Home Assistant Core plugin to use this widget."}

    title = str(options.get("title") or "").strip()
    wanted = _entity_list(options.get("automations"))
    stale_h = _stale_hours(options.get("stale_hours"))
    refresh_s = _refresh_s(options.get("refresh"))

    if not core.is_configured():
        return {
            "error": "Set your Home Assistant URL + token in Settings → Widgets → Home Assistant Core.",
            "title": title,
        }

    now = int(time.time())
    cache_path = _cache_path(ctx.get("data_dir"), wanted, stale_h)
    if cache_path and cache_path.exists() and now - int(cache_path.stat().st_mtime) < refresh_s:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if title:
                cached["title"] = title
            return cached  # type: ignore[no-any-return]
        except (OSError, json.JSONDecodeError):
            pass

    result, err = _build(core, wanted, stale_h, now)
    if err or result is None:
        return {"error": err or "Couldn't read Home Assistant automations.", "title": title}
    if title:
        result["title"] = title
    if cache_path:
        with contextlib.suppress(OSError):
            cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result


def _build(
    core: Any, wanted: list[str], stale_h: float, now: int
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        states = core.get_states()
    except Exception as err:
        return None, core.coerce_error(err)

    now_dt = datetime.now(UTC)
    window_start = now_dt - timedelta(days=WINDOW_DAYS)

    autos: list[dict[str, Any]] = []
    for st in states:
        eid = str(st.get("entity_id") or "")
        if not eid.startswith("automation."):
            continue
        if wanted and eid not in wanted:
            continue
        attrs = st.get("attributes") or {}
        last_dt = _parse_ts(attrs.get("last_triggered"))
        autos.append(
            {
                "eid": eid,
                "name": core.friendly_name(st),
                "on": str(st.get("state") or "").lower() == "on",
                "last_dt": last_dt,
                "c1": 0,
                "c24": 0,
                "c7": 0,
            }
        )

    if not autos:
        return {"empty": True, "title": ""}, None

    # Only the automations that fired within the window need a logbook read;
    # dormant ones keep their zero counts for free. Cap the fetch count.
    active = sorted(
        (a for a in autos if a["last_dt"] and a["last_dt"] >= window_start),
        key=lambda a: a["last_dt"],
        reverse=True,
    )
    capped = len(active) > MAX_LOGBOOK
    to_fetch = active[:MAX_LOGBOOK]

    # Track the single most-recent trigger per automation for the feed.
    last_fire: dict[str, dict[str, Any]] = {}
    fetch_err: str | None = None
    for a in to_fetch:
        entries, err = _logbook(core, a["eid"], window_start, now_dt)
        if err:
            fetch_err = err
            continue
        for e in entries:
            if not _is_trigger(e):
                continue
            when = _parse_ts(e.get("when"))
            if when is None:
                continue
            age = max(0.0, (now_dt - when).total_seconds())
            a["c7"] += 1
            if age <= DAY_S:
                a["c24"] += 1
            if age <= HOUR_S:
                a["c1"] += 1
            prev = last_fire.get(a["eid"])
            if prev is None or when.isoformat() > prev["when"]:
                last_fire[a["eid"]] = {
                    "name": a["name"],
                    "eid": a["eid"],
                    "when": when.isoformat(),
                    "ago_s": int(age),
                }

    # Every logbook call failed and nothing came back → surface the error
    # rather than a misleading all-zero board.
    if fetch_err and to_fetch and not last_fire:
        return None, fetch_err

    for a in autos:
        last_dt = a["last_dt"]
        a["last_ago_s"] = int((now_dt - last_dt).total_seconds()) if last_dt else None
        a["last_triggered"] = last_dt.isoformat() if last_dt else None
        a["stale"] = bool(stale_h) and (
            last_dt is None or (now_dt - last_dt).total_seconds() > stale_h * HOUR_S
        )

    fired = [a for a in autos if a["c7"] > 0]
    most = max(fired, key=lambda a: a["c7"]) if fired else None
    least = min(fired, key=lambda a: a["c7"]) if fired else None

    # One row per automation, most-recently-fired first.
    recent = sorted(last_fire.values(), key=lambda r: r["when"], reverse=True)

    autos.sort(key=lambda a: (-a["c7"], a["name"].lower()))
    out_autos = [
        {
            "name": a["name"],
            "eid": a["eid"],
            "on": a["on"],
            "c1": a["c1"],
            "c24": a["c24"],
            "c7": a["c7"],
            "last_triggered": a["last_triggered"],
            "last_ago_s": a["last_ago_s"],
            "stale": a["stale"],
        }
        for a in autos
    ]

    return {
        "title": "",
        "count": len(autos),
        "tracked_all": not wanted,
        "capped": capped,
        "window_days": WINDOW_DAYS,
        "automations": out_autos,
        "recent": recent[:RECENT_LIMIT],
        "total_1h": sum(a["c1"] for a in autos),
        "total_24h": sum(a["c24"] for a in autos),
        "total_7d": sum(a["c7"] for a in autos),
        "most_fired": _rank(most),
        "least_fired": (_rank(least) if least and most and least["eid"] != most["eid"] else None),
        "stale_count": sum(1 for a in autos if a["stale"]),
        "stale_hours": stale_h,
        "fetched_at": now,
    }, None
