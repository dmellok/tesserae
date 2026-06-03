"""ha_sensor — one or more HA entities as bold Bauhaus value blocks.

Thin widget over ha_core. One ``get_states`` call covers the whole list,
so the cell costs a single request no matter how many entities it shows.
A single entity renders as a hero number; several lay out as a colour
grid (client-side).
"""

from __future__ import annotations

import re
from typing import Any

from flask import current_app

# Map common HA device_classes to Phosphor icons; everything else falls
# back to a generic gauge. Picked server-side so the client just renders
# ``ph-<icon>``.
_DEVICE_CLASS_ICONS: dict[str, str] = {
    "temperature": "thermometer-simple",
    "humidity": "drop",
    "moisture": "drop",
    "power": "lightning",
    "energy": "lightning",
    "current": "lightning",
    "voltage": "lightning",
    "battery": "battery-medium",
    "illuminance": "sun",
    "co2": "wind",
    "carbon_dioxide": "wind",
    "pm25": "wind",
    "pressure": "gauge",
    "timestamp": "clock",
    "signal_strength": "wifi-high",
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


def _format_value(raw: str) -> str:
    """Round numeric HA states to 2 decimal places (then trim trailing
    zeros so 21.00 → "21" / 18.40 → "18.4"). Non-numeric states pass
    through unchanged so a state like ``on`` or ``cooling`` keeps its
    spelling."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return raw
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
    """Parse the ``overrides`` textarea into ``{entity_id: {name?, icon?}}``.

    Format: one entity per line, pipe-separated ``entity_id | name | icon``.
    Either field may be empty (just leave nothing between the pipes) to keep
    the auto value. Lines starting with ``#`` are comments; blank lines
    are skipped. Tolerant of trailing whitespace and a missing third field.
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
        if entry:
            out[parts[0]] = entry
    return out


def _icon_for(entity_id: str, attrs: dict[str, Any]) -> str:
    device_class = str(attrs.get("device_class") or "")
    if device_class in _DEVICE_CLASS_ICONS:
        return _DEVICE_CLASS_ICONS[device_class]
    domain = entity_id.split(".", 1)[0]
    return _DOMAIN_ICONS.get(domain, "gauge")


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
    overrides = _parse_overrides(options.get("overrides"))
    items: list[dict[str, Any]] = []
    for eid in wanted:
        ov = overrides.get(eid) or {}
        st = by_id.get(eid)
        if st is None:
            items.append(
                {
                    "name": ov.get("name") or eid,
                    "value": "—",
                    "unit": "",
                    "icon": ov.get("icon") or "question",
                    "unavailable": True,
                }
            )
            continue
        attrs = st.get("attributes") or {}
        raw = str(st.get("state") or "")
        items.append(
            {
                "name": ov.get("name") or core.friendly_name(st),
                "value": _format_value(raw),
                "unit": str(attrs.get("unit_of_measurement") or "") if show_unit else "",
                "icon": ov.get("icon") or _icon_for(eid, attrs),
                "unavailable": raw.lower() in _UNAVAILABLE,
            }
        )

    # Title precedence: explicit override → the single entity's name → generic.
    if not title:
        title = items[0]["name"] if len(items) == 1 else "Sensors"
    return {"title": title, "items": items}
