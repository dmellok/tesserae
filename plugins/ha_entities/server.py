"""ha_entities, a status grid of several Home Assistant entities.

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
    "person": "user",
    "device_tracker": "map-pin",
    "sensor": "gauge",
    "binary_sensor": "circle",
    "media_player": "speaker-high",
    "camera": "video-camera",
    "vacuum": "broom",
    "automation": "gear",
    "script": "code",
    "scene": "palette",
    "input_boolean": "toggle-right",
    "input_number": "sliders",
    "input_select": "list",
    "input_text": "text-aa",
    "weather": "cloud-sun",
    "sun": "sun",
    "timer": "timer",
    "calendar": "calendar",
    "alarm_control_panel": "shield",
    "update": "arrow-circle-up",
    "todo": "check-square",
    "button": "circle-dashed",
    "select": "list",
    "number": "hash",
    "text": "text-aa",
    "zone": "map-pin-area",
}

# Device-class → Phosphor icon. Wins over the domain default because
# device_class is the most specific signal HA exposes. A
# `sensor.living_room_co2` has domain=sensor (default → gauge) but
# device_class=carbon_dioxide → ph-wind, far more useful.
_DEVICE_CLASS_ICONS: dict[str, str] = {
    # Environmental sensors
    "temperature": "thermometer",
    "humidity": "drop",
    "pressure": "gauge",
    "illuminance": "sun-dim",
    "co2": "wind",
    "carbon_dioxide": "wind",
    "carbon_monoxide": "skull",
    "pm1": "circles-three-plus",
    "pm10": "circles-three-plus",
    "pm25": "circles-three-plus",
    "aqi": "wind",
    "vocs": "wind",
    "nitrogen_dioxide": "wind",
    "ozone": "wind",
    "sulphur_dioxide": "wind",
    "moisture": "drop-half",
    "smoke": "cloud",
    "gas": "flame",
    # Power / electricity
    "battery": "battery-medium",
    "power": "lightning",
    "energy": "lightning",
    "voltage": "wave-sawtooth",
    "current": "wave-sine",
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
    "atmospheric_pressure": "gauge",
    "irradiance": "sun",
    # Binary-sensor specifics
    "motion": "person-simple-walk",
    "occupancy": "user",
    "presence": "user",
    "vibration": "wave-sine",
    "sound": "speaker-high",
    "tamper": "warning",
    "safety": "shield-check",
    "problem": "warning-circle",
    "running": "play",
    "plug": "plug",
    "power_failure": "lightning-slash",
    "moving": "arrow-fat-line-right",
    "light": "sun",
    "cold": "snowflake",
    "heat": "thermometer-hot",
    "connectivity": "wifi-high",
    "battery_charging": "battery-charging",
    "update": "arrow-circle-up",
    # Door / window / opening, used as a fallback for the door-aware
    # icon switch below.
    "door": "door",
    "window": "frame-corners",
    "garage": "garage",
    "garage_door": "garage",
    "opening": "door",
    "lock": "lock",
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
    """Parse the ``overrides`` textarea into
    ``{entity_id: {name?, icon?, format?}}``.

    Format: one entity per line, pipe-separated
    ``entity_id | name | icon | format``. Any field may be empty (leave
    nothing between the pipes) to keep the auto value. Lines starting with
    ``#`` are comments; blank lines are skipped.
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


def _icon_for(entity_id: str, attrs: dict[str, Any], status: str) -> str:
    domain = entity_id.split(".", 1)[0]
    device_class = str(attrs.get("device_class") or "")
    # Domain-specific stateful glyph swaps win over the device-class
    # lookup because the same device_class can mean different things
    # in different domains (e.g. cover with device_class=garage_door
    # should swap open ↔ closed glyph).
    if domain == "lock":
        return "lock-open" if status == "on" else "lock"
    if domain == "cover" or (domain == "binary_sensor" and device_class in _DOOR_CLASSES):
        return "door-open" if status == "on" else "door"
    # Device-class wins over domain default for sensors / binary_sensors.
    if device_class and device_class in _DEVICE_CLASS_ICONS:
        return _DEVICE_CLASS_ICONS[device_class]
    return _DOMAIN_ICONS.get(domain, "circle")


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


def _humanise(raw: str, unit: str, fmt: str = "") -> str:
    """Format an HA state for display. With an explicit ``fmt`` (a number
    pattern like ``0.0``) the value is fixed to that many decimals;
    otherwise numeric states are rounded to two decimal places (then
    trimmed of trailing zeros) so a noisy reading like ``18.42857`` reads
    ``18.43`` instead of taking up half a cell. Non-numeric states fall
    through unchanged with underscores → spaces and title-case."""
    try:
        value = float(raw)
    except ValueError:
        return raw.replace("_", " ").capitalize()
    rendered = None
    if fmt:
        rendered = _apply_number_format(value, fmt)
    if rendered is None:
        # Trim trailing zeros so 21.00 → "21" and 18.40 → "18.4".
        rendered = f"{round(value, 2):g}"
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
    number_format = str(options.get("number_format") or "").strip()
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
                "label": (
                    "unavailable"
                    if status == "missing"
                    else _humanise(raw, unit, ov.get("format") or number_format)
                ),
                "status": status,
                "icon": ov.get("icon") or _icon_for(eid, attrs, status),
                # Surface last_changed so the client can flag rows
                # that just transitioned; format is HA's standard
                # ISO 8601 with timezone.
                "last_changed": str(st.get("last_changed") or ""),
                "domain": eid.split(".", 1)[0],
                "device_class": str(attrs.get("device_class") or ""),
            }
        )

    return {"title": options.get("title") or "Entities", "items": items}
