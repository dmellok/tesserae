"""ha_history, one or more numeric HA sensors over time as sparklines.

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


_NUMBER_FORMAT = re.compile(r"0(?:\.(0+))?$")


def _fmt_num(value: float, fmt: str = "") -> str:
    """Format ``value`` per a data-element number pattern (``0`` / ``0.0``
    / ``0.00``, matching the canvas data element's vocabulary). Blank or
    unrecognised patterns round to 2 decimals with trailing zeros trimmed
    (18.42857 → "18.43", 21.00 → "21")."""
    if fmt:
        m = _NUMBER_FORMAT.fullmatch(fmt)
        if m:
            decimals = len(m.group(1)) if m.group(1) else 0
            return f"{value:.{decimals}f}"
    return f"{round(value, 2):g}"


def _keep_indices(n: int, cap: int = _MAX_POINTS) -> list[int]:
    """Which sample indexes survive downsampling to ``cap`` points, evenly
    spread. Returned as indexes (not values) so the timestamps ride along
    with the values they belong to: the x-axis labels have to describe the
    points the chart actually plots."""
    if n <= cap:
        return list(range(n))
    step = (n - 1) / (cap - 1)
    return [round(i * step) for i in range(cap)]


def _clamp_hours(raw: Any) -> int:
    """Coerce a window option to a sane hour count.

    Accepts either the new ``window`` select (preset string-of-int from
    the plugin.json choices) or the legacy ``hours`` number field that
    earlier cells were saved with. Clamps 1 hour ≤ window ≤ 3 months -
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


def _parse_dt(raw: Any) -> Any:
    """A HA history timestamp as an aware datetime, or None. HA sends UTC
    ISO strings; a naive one is read as UTC rather than guessed at."""
    from datetime import UTC, datetime

    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _axis_labels(stamps: list[Any], hours: int) -> list[str]:
    """One x-axis label per plotted sample, in the app's timezone, at a
    resolution the window can carry: clock time within a day, weekday +
    clock time up to three days, calendar date beyond that. The chart used
    to label the axis with sample ordinals (1, 10, 19, …), which read as
    data and told the viewer nothing about when anything happened.

    All or nothing: one unparseable stamp returns [] and the client keeps
    its ordinal fallback, rather than shipping an axis that's right for
    part of the series and wrong for the rest.
    """
    from app.tz_resolve import app_timezone

    zone = app_timezone()
    out: list[str] = []
    for raw in stamps:
        dt = _parse_dt(raw)
        if dt is None:
            return []
        local = dt.astimezone(zone)
        if hours <= 24:
            out.append(f"{local:%H:%M}")
        elif hours <= 72:
            out.append(f"{local:%a %H:%M}")
        else:
            # Day number formatted separately: "%-d" is not portable to
            # Windows, and "%d" would zero-pad.
            out.append(f"{local.day} {local:%b}")
    return out


def _hourly_profile(samples: list[dict[str, Any]]) -> list[float | None]:
    """Average value by hour-of-day across the entire window. Returns 24
    floats (or None for hours with no samples). Used by the client to
    overlay a "what does an average day at this hour look like?" ghost
    line on top of the live series. Worth computing only when the
    window is long enough to span multiple days (caller decides)."""
    buckets: list[list[float]] = [[] for _ in range(24)]
    for s in samples:
        value = _to_float(s.get("state"))
        if value is None:
            continue
        dt = _parse_dt(s.get("last_changed") or s.get("last_updated"))
        if dt is None:
            continue
        buckets[dt.hour].append(value)
    out: list[float | None] = []
    for hour_samples in buckets:
        if hour_samples:
            out.append(round(sum(hour_samples) / len(hour_samples), 2))
        else:
            out.append(None)
    return out


def _series_for(
    core: Any, eid: str, by_id: dict[str, Any], hours: int, fmt: str = ""
) -> dict[str, Any]:
    st = by_id.get(eid)
    if st is None:
        return {
            "name": eid,
            "unit": "",
            "current": "-",
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
        current = _fmt_num(current_f, fmt) if current_f is not None else raw_state

    samples = core.history(eid, hours=hours)
    # Carry each sample's timestamp alongside its value so the chart can
    # label its x-axis with real times.
    pairs: list[tuple[Any, float]] = []
    for s in samples:
        value = _to_float(s.get("state"))
        if value is None:
            continue
        pairs.append((s.get("last_changed") or s.get("last_updated"), value))
    values = [v for _, v in pairs]
    if len(values) < 2:
        return {
            "name": name,
            "unit": unit,
            "current": current,
            "values": [],
            "sparse": True,
            "trend": "flat",
        }
    # Compute hourly profile BEFORE downsampling, it needs the raw
    # timestamps. Only emit a profile when the window is at least
    # three days, otherwise the per-hour averages are too noisy to
    # carry meaning.
    profile = _hourly_profile(samples) if hours >= 72 else []
    keep = _keep_indices(len(pairs))
    pairs = [pairs[i] for i in keep]
    # Round each sample to 2 decimals so the chart's tooltip / axis
    # labels stay clean (the chart itself just plots floats so the
    # rounding is purely cosmetic at the client layer).
    values = [round(v, 2) for _, v in pairs]
    times = _axis_labels([ts for ts, _ in pairs], hours)
    # Find the min/max INDEX so the client can mark them as dots on
    # the line. Use the downsampled series so indexes line up with
    # what the chart plots.
    min_idx = values.index(min(values))
    max_idx = values.index(max(values))
    lo, hi = min(values), max(values)
    return {
        "name": name,
        "unit": unit,
        "current": current or _fmt_num(values[-1], fmt),
        "values": values,
        # One label per plotted point; empty when the history carried no
        # usable timestamps, which the client reads as "no time axis".
        "times": times,
        "min": _fmt_num(lo, fmt),
        "max": _fmt_num(hi, fmt),
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
    number_format = str(options.get("number_format") or "").strip()

    try:
        states = core.get_states()
        by_id = {str(s.get("entity_id")): s for s in states}
        items = [_series_for(core, eid, by_id, hours, number_format) for eid in wanted]
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
