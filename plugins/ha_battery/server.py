"""ha_battery, auto-discovered battery levels across HA.

Asks HA for every entity, keeps the ones whose ``device_class`` is
``battery`` (which is HA's own convention for "this state is a 0-100
battery %"), sorts them low-first so the things that need charging
surface to the top, and returns:

    {
      "label": str,
      "time": str,
      "items": [
        {"entity_id": str, "name": str, "level": float,
         "critical": bool, "low": bool},
        ...
      ],
      "summary": {
        "count": int,            # total batteries discovered
        "shown": int,            # after limit
        "low": int, "critical": int,
        "avg": float|None,       # mean level across ALL discovered
        "min": float|None,
        "max": float|None,
        "histogram": [int, ...]  # 10 buckets, 0-10%, 10-20%, …, 90-100%
      }
    }

One ``get_states`` call covers every battery on the install; the rest
is in-process filtering.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from flask import current_app


def choices(name: str) -> list[dict[str, str]]:
    """Entity picker for the editor. Filtered to ``sensor.*`` since the
    auto-discovery looks at ``device_class=battery`` (always on a
    sensor entity)."""
    core = _core()
    if name == "entity" and core is not None:
        return core.entity_choices(domains=("sensor",))
    return []


def _core() -> Any:
    return current_app.config["PLUGIN_REGISTRY"].get("ha_core").server_module


# -- helpers -----------------------------------------------------------


def _f_or_none(value: Any) -> float | None:
    """Coerce a state string to float, returning None for the HA
    "missing data" sentinels so we can drop the entity instead of
    pretending it's at 0%."""
    if value in (None, "", "unavailable", "unknown", "none"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_battery(state: dict[str, Any]) -> bool:
    """A state qualifies as a battery if its ``device_class`` attribute
    is exactly ``"battery"``. HA uses that for the 0-100 % battery level
    sensors that ship with most integrations (phones, vacuums, BLE
    sensors, etc); separate ``battery_charging`` device_class entities
    are booleans and intentionally skipped."""
    attrs = state.get("attributes") or {}
    return str(attrs.get("device_class") or "").lower() == "battery"


def _name(state: dict[str, Any]) -> str:
    attrs = state.get("attributes") or {}
    return str(attrs.get("friendly_name") or state.get("entity_id") or "")


def _histogram(levels: list[float]) -> list[int]:
    """Bin 0-100 % into ten 10-point buckets. 100% lands in the last
    bucket (not a phantom 11th)."""
    buckets = [0] * 10
    for lv in levels:
        idx = int(lv // 10)
        if idx < 0:
            idx = 0
        elif idx > 9:
            idx = 9
        buckets[idx] += 1
    return buckets


# -- entry point -------------------------------------------------------


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    core = _core()
    try:
        states = core.get_states()
    except Exception as err:
        return {"error": core.coerce_error(err)}

    try:
        low_thr = float(options.get("low_threshold", 20))
    except (TypeError, ValueError):
        low_thr = 20.0
    try:
        crit_thr = float(options.get("critical_threshold", 10))
    except (TypeError, ValueError):
        crit_thr = 10.0
    try:
        limit = int(options.get("limit", 12))
    except (TypeError, ValueError):
        limit = 12
    if limit < 1:
        limit = 1

    # If the user picked a specific list, honour it; an empty list means
    # "auto-discover any entity with device_class=battery."
    picked_raw = options.get("entities") or []
    if isinstance(picked_raw, str):
        picked = [tok.strip() for tok in picked_raw.replace(",", "\n").splitlines() if tok.strip()]
    else:
        picked = [str(x).strip() for x in picked_raw if str(x).strip()]
    picked_set = set(picked)

    all_items: list[dict[str, Any]] = []
    for st in states:
        eid = str(st.get("entity_id") or "")
        if picked_set:
            if eid not in picked_set:
                continue
        elif not _is_battery(st):
            continue
        level = _f_or_none(st.get("state"))
        if level is None:
            continue
        # Clamp to 0-100, some integrations briefly report >100 while
        # calibrating and the variants assume the canonical range.
        level = max(0.0, min(100.0, level))
        all_items.append(
            {
                "entity_id": str(st.get("entity_id") or ""),
                "name": _name(st),
                "level": round(level, 1),
                "critical": level < crit_thr,
                "low": crit_thr <= level < low_thr,
            }
        )

    all_items.sort(key=lambda it: (it["level"], it["name"].lower()))

    levels_all = [it["level"] for it in all_items]
    summary = {
        "count": len(all_items),
        "shown": min(limit, len(all_items)),
        "low": sum(1 for it in all_items if it["low"]),
        "critical": sum(1 for it in all_items if it["critical"]),
        "avg": round(sum(levels_all) / len(levels_all), 1) if levels_all else None,
        "min": min(levels_all) if levels_all else None,
        "max": max(levels_all) if levels_all else None,
        "histogram": _histogram(levels_all),
    }

    return {
        "label": options.get("label", "Batteries"),
        "time": datetime.now().strftime("%H:%M"),
        "items": all_items[:limit],
        "summary": summary,
        "low_threshold": low_thr,
        "critical_threshold": crit_thr,
        "_fetched_at": int(time.time()),
    }
