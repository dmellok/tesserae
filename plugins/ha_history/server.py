"""ha_history — one or more numeric HA sensors over time as sparklines.

Thin widget over ha_core. ``get_states`` gives each entity's current
value + unit + name in one call; ``history`` gives each series. We coerce
to floats, downsample, and add a trend (up/down/flat vs the window start)
so the client can draw an SVG path + a direction arrow.
"""

from __future__ import annotations

import re
from typing import Any

from flask import current_app

_MAX_POINTS = 80
_UNAVAILABLE = {"unavailable", "unknown", "none", ""}


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


def choices(name: str) -> list[dict[str, str]]:
    """Entity multi-select for the editor."""
    core = _core()
    if name == "entity" and core is not None:
        return core.entity_choices()
    return []


def _entity_list(raw: Any) -> list[str]:
    """Accept the multiselect's list, or a legacy comma/newline string."""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [tok for tok in re.split(r"[\s,]+", str(raw or "").strip()) if tok]


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _downsample(values: list[float], cap: int = _MAX_POINTS) -> list[float]:
    n = len(values)
    if n <= cap:
        return values
    step = (n - 1) / (cap - 1)
    return [values[round(i * step)] for i in range(cap)]


def _clamp_hours(raw: Any) -> int:
    try:
        h = int(float(raw))
    except (TypeError, ValueError):
        return 24
    return max(1, min(h, 168))  # 1 hour … 1 week


def _trend(values: list[float]) -> str:
    """up / down / flat from the window's first to last sample."""
    if len(values) < 2:
        return "flat"
    delta = values[-1] - values[0]
    span = (max(values) - min(values)) or 1
    if abs(delta) < span * 0.05:
        return "flat"
    return "up" if delta > 0 else "down"


def _series_for(core: Any, eid: str, by_id: dict[str, Any], hours: int) -> dict[str, Any]:
    st = by_id.get(eid)
    if st is None:
        return {
            "name": eid,
            "unit": "",
            "current": "—",
            "values": [],
            "sparse": True,
            "trend": "flat",
        }
    attrs = st.get("attributes") or {}
    name = core.friendly_name(st)
    unit = str(attrs.get("unit_of_measurement") or "")
    raw_state = str(st.get("state") or "")
    current = "" if raw_state.lower() in _UNAVAILABLE else raw_state

    samples = core.history(eid, hours=hours)
    values = [v for v in (_to_float(s.get("state")) for s in samples) if v is not None]
    if len(values) < 2:
        return {
            "name": name,
            "unit": unit,
            "current": current,
            "values": [],
            "sparse": True,
            "trend": "flat",
        }
    values = _downsample(values)
    lo, hi = min(values), max(values)
    return {
        "name": name,
        "unit": unit,
        "current": current or f"{values[-1]:g}",
        "values": values,
        "min": f"{lo:g}",
        "max": f"{hi:g}",
        "trend": _trend(values),
        "sparse": False,
    }


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    core = _core()
    if core is None:
        return {"error": "Install the Home Assistant Core plugin to use this widget."}

    wanted = _entity_list(options.get("entities"))
    title = (options.get("title") or "").strip()
    if not wanted:
        return {"empty": True, "title": title}

    hours = _clamp_hours(options.get("hours", 24))

    try:
        states = core.get_states()
        by_id = {str(s.get("entity_id")): s for s in states}
        items = [_series_for(core, eid, by_id, hours) for eid in wanted]
    except Exception as err:
        return {"error": core.coerce_error(err)}

    if not title:
        title = items[0]["name"] if len(items) == 1 else "History"
    return {"title": title, "hours": hours, "items": items}
