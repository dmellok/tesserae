"""ha_entities — a status grid of several Home Assistant entities.

Thin widget over ha_core. One ``get_states`` call covers the whole list,
so the cell costs a single request no matter how many entities it shows.
Each entity resolves to a status (on / off / other), a humanised label,
and a domain-appropriate icon.
"""

from __future__ import annotations

import re
from typing import Any

from flask import current_app

# Active / inactive state vocabularies. Anything outside both is treated
# as "other" (a neutral dot + the raw value, e.g. a temperature).
_ON = {"on", "open", "unlocked", "home", "playing", "active", "detected"}
_OFF = {"off", "closed", "locked", "away", "not_home", "idle", "standby", "disarmed"}
_UNAVAILABLE = {"unavailable", "unknown", "none", ""}

_DOMAIN_ICONS: dict[str, str] = {
    "light": "lightbulb",
    "switch": "toggle-right",
    "fan": "fan",
    "climate": "thermometer-simple",
    "person": "house",
    "device_tracker": "house",
    "sensor": "gauge",
    "binary_sensor": "circle",
}

_DOOR_CLASSES = {"door", "window", "garage_door", "opening", "garage"}


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


def _parse_entities(raw: Any) -> list[str]:
    """Accept the multiselect's list, or a legacy comma/newline string."""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [tok for tok in re.split(r"[\s,]+", str(raw or "").strip()) if tok]


def _parse_overrides(raw: Any) -> dict[str, dict[str, str]]:
    """Parse the ``overrides`` textarea into ``{entity_id: {name?, icon?}}``.

    Format: one entity per line, pipe-separated ``entity_id | name | icon``.
    Either field may be empty (just leave nothing between the pipes) to keep
    the auto value. Lines starting with ``#`` are comments; blank lines
    are skipped.
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


def _icon_for(entity_id: str, attrs: dict[str, Any], status: str) -> str:
    domain = entity_id.split(".", 1)[0]
    device_class = str(attrs.get("device_class") or "")
    if domain == "lock":
        return "lock-open" if status == "on" else "lock"
    if domain == "cover" or (domain == "binary_sensor" and device_class in _DOOR_CLASSES):
        return "door-open" if status == "on" else "door"
    return _DOMAIN_ICONS.get(domain, "circle")


def _humanise(raw: str, unit: str) -> str:
    """Format an HA state for display. Numeric states are rounded to
    two decimal places (then trimmed of trailing zeros) so a noisy
    sensor reading like ``18.42857`` reads ``18.43`` instead of taking
    up half a cell. Non-numeric states fall through unchanged with
    underscores → spaces and title-case."""
    try:
        value = float(raw)
    except ValueError:
        return raw.replace("_", " ").capitalize()
    rounded = round(value, 2)
    # Trim trailing zeros so 21.00 → "21" and 18.40 → "18.4".
    rendered = f"{rounded:g}"
    return f"{rendered} {unit}".strip()


def choices(name: str) -> list[dict[str, str]]:
    """Entity multi-select for the editor."""
    core = _core()
    if name == "entity" and core is not None:
        return core.entity_choices()
    return []


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    core = _core()
    if core is None:
        return {"error": "Install the Home Assistant Core plugin to use this widget."}

    wanted = _parse_entities(options.get("entities") or "")
    if not wanted:
        return {"empty": True, "title": options.get("title") or "Entities"}

    try:
        states = core.get_states()
    except Exception as err:
        return {"error": core.coerce_error(err)}

    by_id = {str(s.get("entity_id")): s for s in states}
    overrides = _parse_overrides(options.get("overrides"))
    items: list[dict[str, Any]] = []
    for eid in wanted:
        ov = overrides.get(eid) or {}
        st = by_id.get(eid)
        if st is None:
            items.append(
                {
                    "name": ov.get("name") or eid,
                    "label": "not found",
                    "status": "missing",
                    "icon": ov.get("icon") or "question",
                }
            )
            continue
        attrs = st.get("attributes") or {}
        raw = str(st.get("state") or "")
        low = raw.lower()
        if low in _UNAVAILABLE:
            status = "missing"
        elif low in _ON:
            status = "on"
        elif low in _OFF:
            status = "off"
        else:
            status = "other"
        unit = str(attrs.get("unit_of_measurement") or "")
        items.append(
            {
                "name": ov.get("name") or core.friendly_name(st),
                "label": "unavailable" if status == "missing" else _humanise(raw, unit),
                "status": status,
                "icon": ov.get("icon") or _icon_for(eid, attrs, status),
            }
        )

    return {"title": options.get("title") or "Entities", "items": items}
