"""ha_sensor, one or more HA entities as bold Bauhaus value blocks.

Thin widget over ha_core. One ``get_states`` call covers the whole list,
so the cell costs a single request no matter how many entities it shows.
A single entity renders as a hero number; several lay out as a colour
grid (client-side).
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from flask import current_app

# History calls run in a small pool so a multi-entity cell doesn't pay
# one HA round-trip per entity in sequence. The per-call timeout stays
# inside the composer's 6 s per-widget hydration budget (ha_core's 12 s
# default would guarantee the whole widget times out on a slow HA).
_HISTORY_TIMEOUT_S = 5
_HISTORY_MAX_WORKERS = 4

# Map common HA device_classes to Phosphor icons; everything else falls
# back to a generic gauge. Picked server-side so the client just renders
# ``ph-<icon>``.
_DEVICE_CLASS_ICONS: dict[str, str] = {
    # Environmental
    "temperature": "thermometer",
    "humidity": "drop",
    "moisture": "drop-half",
    "pressure": "gauge",
    "atmospheric_pressure": "gauge",
    "illuminance": "sun-dim",
    "irradiance": "sun",
    "co2": "wind",
    "carbon_dioxide": "wind",
    "carbon_monoxide": "skull",
    "pm1": "circles-three-plus",
    "pm10": "circles-three-plus",
    "pm25": "circles-three-plus",
    "aqi": "wind",
    "vocs": "wind",
    "nitrogen_dioxide": "wind",
    "nitrous_oxide": "wind",
    "ozone": "wind",
    "sulphur_dioxide": "wind",
    "smoke": "cloud",
    "gas": "flame",
    # Power / electricity
    "battery": "battery-medium",
    "power": "lightning",
    "energy": "lightning",
    "current": "wave-sine",
    "voltage": "wave-sawtooth",
    "power_factor": "wave-square",
    "frequency": "wave-triangle",
    "apparent_power": "lightning",
    "reactive_power": "lightning",
    # Counters / quantities
    "distance": "ruler",
    "duration": "clock",
    "speed": "speedometer",
    "wind_speed": "wind",
    "weight": "scales",
    "volume": "tray-arrow-down",
    "volume_flow_rate": "drop",
    "water": "drop",
    "monetary": "currency-circle-dollar",
    "data_size": "database",
    "data_rate": "wifi-high",
    "signal_strength": "wifi-high",
    "timestamp": "clock",
    "date": "calendar",
    "temperature_change": "thermometer-cold",
}

# Domain icons for non-sensor entities (lights, switches, …) shown by id.
_DOMAIN_ICONS: dict[str, str] = {
    "light": "lightbulb",
    "switch": "toggle-right",
    "lock": "lock",
    "fan": "fan",
    "climate": "thermometer-simple",
    "person": "house",
    "binary_sensor": "circle",
}

_UNAVAILABLE = {"unavailable", "unknown", "none", ""}


_NUMBER_FORMAT = re.compile(r"0(?:\.(0+))?$")


def _apply_number_format(value: float, fmt: str) -> str | None:
    """Format ``value`` per a data-element number pattern: ``0`` /
    ``0.0`` / ``0.00`` fix the decimal places (matching the canvas data
    element's vocabulary). Returns ``None`` when ``fmt`` isn't a
    recognised pattern, so the caller keeps its default formatting."""
    m = _NUMBER_FORMAT.fullmatch(fmt)
    if not m:
        return None
    decimals = len(m.group(1)) if m.group(1) else 0
    return f"{value:.{decimals}f}"


def _format_value(raw: str, fmt: str = "") -> str:
    """Format a numeric HA state. With an explicit ``fmt`` (a number
    pattern like ``0.0``) the value is fixed to that many decimals;
    otherwise it's rounded to 2 decimals with trailing zeros trimmed
    (so 21.00 → "21" / 18.40 → "18.4"). Non-numeric states pass through
    unchanged so a state like ``on`` or ``cooling`` keeps its spelling."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return raw
    if fmt:
        formatted = _apply_number_format(value, fmt)
        if formatted is not None:
            return formatted
    return f"{round(value, 2):g}"


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


def _parse_overrides(raw: Any) -> dict[str, dict[str, str]]:
    """Parse the ``overrides`` textarea into
    ``{entity_id: {name?, icon?, format?}}``.

    Format: one entity per line, pipe-separated
    ``entity_id | name | icon | format``. Any field may be empty (leave
    nothing between the pipes) to keep the auto value. Lines starting with
    ``#`` are comments; blank lines are skipped. Tolerant of trailing
    whitespace and a missing third/fourth field.
    """
    out: dict[str, dict[str, str]] = {}
    text = str(raw or "")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if not parts or not parts[0]:
            continue
        entry: dict[str, str] = {}
        if len(parts) > 1 and parts[1]:
            entry["name"] = parts[1]
        if len(parts) > 2 and parts[2]:
            entry["icon"] = parts[2]
        if len(parts) > 3 and parts[3]:
            entry["format"] = parts[3]
        if entry:
            out[parts[0]] = entry
    return out


def _icon_for(entity_id: str, attrs: dict[str, Any]) -> str:
    device_class = str(attrs.get("device_class") or "")
    if device_class in _DEVICE_CLASS_ICONS:
        return _DEVICE_CLASS_ICONS[device_class]
    domain = entity_id.split(".", 1)[0]
    return _DOMAIN_ICONS.get(domain, "gauge")


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _downsample(values: list[float], cap: int = 24) -> list[float]:
    n = len(values)
    if n <= cap:
        return values
    step = (n - 1) / (cap - 1)
    return [values[round(i * step)] for i in range(cap)]


def _attach_history(
    core: Any,
    pending: list[tuple[dict[str, Any], str]],
    *,
    show_trend: bool,
    show_sparkline: bool,
) -> None:
    """Fetch 24 h history for each (item, entity_id) pair in parallel and
    attach trend/sparkline in place. ha_core caches the results, so only
    the first preview cycle in a couple of minutes pays the round-trips."""
    app = current_app._get_current_object()

    def one(eid: str) -> list[dict[str, Any]]:
        # Worker threads don't inherit the request's app context, but
        # ha_core reads its settings through current_app.
        with app.app_context():
            try:
                return core.history(eid, hours=24, timeout=_HISTORY_TIMEOUT_S)
            except Exception:
                return []

    eids = [eid for _item, eid in pending]
    if len(eids) == 1:
        results = [one(eids[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(_HISTORY_MAX_WORKERS, len(eids))) as pool:
            results = list(pool.map(one, eids))
    for (item, _eid), samples in zip(pending, results, strict=True):
        values = [v for v in (_to_float(s.get("state")) for s in samples) if v is not None]
        if len(values) >= 2:
            if show_trend:
                item["trend"] = _trend(values)
            if show_sparkline:
                item["sparkline"] = [round(v, 2) for v in _downsample(values, cap=24)]


def _trend(values: list[float]) -> str:
    """up / down / flat based on the window's first sample → last
    sample, treating moves smaller than 5% of the range as flat so a
    noisy sensor doesn't show a "trend" that's just jitter."""
    if len(values) < 2:
        return "flat"
    delta = values[-1] - values[0]
    span = (max(values) - min(values)) or 1
    if abs(delta) < span * 0.05:
        return "flat"
    return "up" if delta > 0 else "down"


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

    try:
        states = core.get_states()
    except Exception as err:
        return {"error": core.coerce_error(err)}

    by_id = {str(s.get("entity_id")): s for s in states}
    show_unit = options.get("show_unit", True)
    # Trend + sparkline default to on for the single-entity stat mode
    # (where they really pay off) and to opt-in for the multi-entity
    # list (where the sparkline is too cramped to read). The user can
    # flip them either way.
    show_trend = options.get("show_trend") is not False
    show_sparkline = options.get("show_sparkline") is not False
    number_format = str(options.get("number_format") or "").strip()
    overrides = _parse_overrides(options.get("overrides"))
    items: list[dict[str, Any]] = []
    needs_history: list[tuple[dict[str, Any], str]] = []
    for eid in wanted:
        ov = overrides.get(eid) or {}
        st = by_id.get(eid)
        if st is None:
            items.append(
                {
                    "name": ov.get("name") or eid,
                    "value": "-",
                    "unit": "",
                    "icon": ov.get("icon") or "question",
                    "unavailable": True,
                }
            )
            continue
        attrs = st.get("attributes") or {}
        raw = str(st.get("state") or "")
        is_unavailable = raw.lower() in _UNAVAILABLE
        item = {
            "name": ov.get("name") or core.friendly_name(st),
            "value": _format_value(raw, ov.get("format") or number_format),
            "unit": str(attrs.get("unit_of_measurement") or "") if show_unit else "",
            "icon": ov.get("icon") or _icon_for(eid, attrs),
            "unavailable": is_unavailable,
        }
        # Trend + sparkline only for numeric, available sensors. Skip
        # the API call for non-numeric or unavailable entries.
        if (show_trend or show_sparkline) and not is_unavailable and _to_float(raw) is not None:
            needs_history.append((item, eid))
        items.append(item)

    if needs_history:
        _attach_history(core, needs_history, show_trend=show_trend, show_sparkline=show_sparkline)

    # Title precedence: explicit override → the single entity's name → generic.
    if not title:
        title = items[0]["name"] if len(items) == 1 else "Sensors"
    return {
        "title": title,
        "items": items,
        # Visibility toggles ride along so the client doesn't need the raw
        # cell options. Both default on; ``is not False`` keeps cells saved
        # before the option existed rendering unchanged.
        "show_title": options.get("show_title") is not False,
        "show_names": options.get("show_names") is not False,
    }
