"""ha_climate — one or more HA thermostat tiles.

Thin widget over ha_core. A climate entity's state IS its HVAC mode
(heat / cool / off / …); the live temps + what it's actually doing live
in attributes. One ``get_states`` call covers the whole list.
"""

from __future__ import annotations

import re
from typing import Any

from flask import current_app

_MODE_ICONS: dict[str, str] = {
    "heat": "fire",
    "cool": "snowflake",
    "heat_cool": "thermometer-simple",
    "auto": "thermometer-simple",
    "dry": "drop",
    "fan_only": "fan",
    "off": "power",
}

_UNAVAILABLE = {"unavailable", "unknown", "none", ""}


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


def choices(name: str) -> list[dict[str, str]]:
    """Entity multi-select for the editor, filtered to climate entities."""
    core = _core()
    if name == "entity" and core is not None:
        return core.entity_choices(domains=("climate",))
    return []


def _entity_list(raw: Any) -> list[str]:
    """Accept the multiselect's list, or a legacy comma/newline string."""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [tok for tok in re.split(r"[\s,]+", str(raw or "").strip()) if tok]


def _fmt(value: Any) -> str:
    """Trim a temperature to a clean string ("21.0" → "21", "20.5" → "20.5")."""
    if value is None or value == "":
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(f)) if f == int(f) else f"{f:g}"


def _shape(core: Any, st: dict[str, Any]) -> dict[str, Any]:
    attrs = st.get("attributes") or {}
    mode = str(st.get("state") or "")
    return {
        "name": core.friendly_name(st),
        "mode": mode,
        "mode_label": mode.replace("_", " "),
        "action": str(attrs.get("hvac_action") or ""),
        "icon": _MODE_ICONS.get(mode, "thermometer-simple"),
        "current": _fmt(attrs.get("current_temperature")),
        "target": _fmt(attrs.get("temperature")),
        "target_low": _fmt(attrs.get("target_temp_low")),
        "target_high": _fmt(attrs.get("target_temp_high")),
        "unavailable": mode.lower() in _UNAVAILABLE,
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

    try:
        states = core.get_states()
    except Exception as err:
        return {"error": core.coerce_error(err)}

    by_id = {str(s.get("entity_id")): s for s in states}
    items: list[dict[str, Any]] = []
    for eid in wanted:
        st = by_id.get(eid)
        if st is None:
            items.append(
                {
                    "name": eid,
                    "mode": "",
                    "mode_label": "not found",
                    "action": "",
                    "icon": "question",
                    "current": "",
                    "target": "",
                    "target_low": "",
                    "target_high": "",
                    "unavailable": True,
                }
            )
            continue
        items.append(_shape(core, st))

    if not title:
        title = items[0]["name"] if len(items) == 1 else "Climate"
    return {"title": title, "items": items}
