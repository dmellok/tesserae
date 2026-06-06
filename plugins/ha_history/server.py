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
    """Coerce a window option to a sane hour count.

    Accepts either the new ``window`` select (preset string-of-int from
    the plugin.json choices) or the legacy ``hours`` number field that
    earlier cells were saved with. Clamps 1 hour ≤ window ≤ 3 months —
    HA's history API can technically span longer but the widget would
    spend most of its time downsampling for a chart that won't read
    meaningfully on an e-ink panel anyway."""
    try:
        h = int(float(raw))
    except (TypeError, ValueError):
        return 24
    return max(1, min(h, 2160))  # 1 hour … 90 days


def _trend(values: list[float]) -> str:
    """up / down / flat from the window's first to last sample."""
    if len(values) < 2:
        return "flat"
    delta = values[-1] - values[0]
    span = (max(values) - min(values)) or 1
    if abs(delta) < span * 0.05:
        return "flat"
    return "up" if delta > 0 else "down"


def _hourly_profile(samples: list[dict[str, Any]]) -> list[float | None]:
    """Average value by hour-of-day across the entire window. Returns 24
    floats (or None for hours with no samples). Used by the client to
    overlay a "what does an average day at this hour look like?" ghost
    line on top of the live series. Worth computing only when the
    window is long enough to span multiple days (caller decides)."""
    from datetime import datetime

    buckets: list[list[float]] = [[] for _ in range(24)]
    for s in samples:
        value = _to_float(s.get("state"))
        if value is None:
            continue
        ts = s.get("last_changed") or s.get("last_updated")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        buckets[dt.hour].append(value)
    out: list[float | None] = []
    for hour_samples in buckets:
        if hour_samples:
            out.append(round(sum(hour_samples) / len(hour_samples), 2))
        else:
            out.append(None)
    return out


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
    # Round numeric current values to 2 decimal places (trimmed) so the
    # widget doesn't render ``18.42857143`` in the hero numeral.
    current = ""
    if raw_state.lower() not in _UNAVAILABLE:
        current_f = _to_float(raw_state)
        current = f"{round(current_f, 2):g}" if current_f is not None else raw_state

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
    # Compute hourly profile BEFORE downsampling — it needs the raw
    # timestamps. Only emit a profile when the window is at least
    # three days, otherwise the per-hour averages are too noisy to
    # carry meaning.
    profile = _hourly_profile(samples) if hours >= 72 else []
    values = _downsample(values)
    # Round each sample to 2 decimals so the chart's tooltip / axis
    # labels stay clean (the chart itself just plots floats so the
    # rounding is purely cosmetic at the client layer).
    values = [round(v, 2) for v in values]
    # Find the min/max INDEX so the client can mark them as dots on
    # the line. Use the downsampled series so indexes line up with
    # what the chart plots.
    min_idx = values.index(min(values))
    max_idx = values.index(max(values))
    lo, hi = min(values), max(values)
    return {
        "name": name,
        "unit": unit,
        "current": current or f"{values[-1]:g}",
        "values": values,
        "min": f"{lo:g}",
        "max": f"{hi:g}",
        "min_idx": min_idx,
        "max_idx": max_idx,
        "hourly_profile": profile,
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

    # Prefer the new ``window`` select; fall back to ``hours`` for cells
    # saved before the picker existed.
    hours = _clamp_hours(options.get("window") or options.get("hours") or 24)

    try:
        states = core.get_states()
        by_id = {str(s.get("entity_id")): s for s in states}
        items = [_series_for(core, eid, by_id, hours) for eid in wanted]
    except Exception as err:
        return {"error": core.coerce_error(err)}

    if not title:
        title = items[0]["name"] if len(items) == 1 else "History"
    # Pass the threshold options through verbatim so the client can
    # draw a horizontal threshold line + tint min-max ranges. Both
    # values are optional; the client falls back gracefully when they
    # aren't present.
    threshold = _to_float(options.get("threshold"))
    return {
        "title": title,
        "hours": hours,
        "items": items,
        "threshold": threshold,
        "show_profile": options.get("show_profile") is not False,
        "show_min_max": options.get("show_min_max") is not False,
    }
