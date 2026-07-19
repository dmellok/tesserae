"""Map a Home Assistant device's telemetry into Tesserae's heartbeat shape.

An ``opendisplay_ha`` tag never talks to Tesserae directly (Home Assistant
owns the Bluetooth link), so its battery / signal / firmware never arrive
as a heartbeat the way an MQTT or REST client's do. This reads those values
off the tag's HA entities (via ``ha_core``'s ``/api/template``) and reshapes
them into the same ``{battery_pct, rssi, fw_version, …}`` dict a real
heartbeat carries, so it can flow through ``record_status_heartbeat`` and
light up the device card's tiles, battery history, and event log with no
new rendering code.

Entities are matched by ``device_class`` (``battery`` / ``signal_strength``
/ ``temperature``) plus the device's ``sw_version``, so it's integration
agnostic: any HA-mapped device kind can reuse it. The template uses the
``device_entities`` / ``state_attr`` / ``device_attr`` *functions* (not
their filter forms, which some HA builds don't register).
"""

from __future__ import annotations

import json
from typing import Any


def build_template(ha_device_id: str) -> str:
    """Jinja that renders a JSON object of the device's telemetry. Values
    come back as strings (via ``tojson``); numeric coercion happens in
    :func:`parse_telemetry` so a non-numeric state can't break the JSON."""
    # ha_device_id is HA's internal hex id; embed it as a quoted literal.
    d = json.dumps(ha_device_id)
    return (
        f"{{%- set d = {d} -%}}"
        "{%- set ns = namespace(battery_pct=none, rssi=none, temperature=none) -%}"
        "{%- for e in device_entities(d) -%}"
        "{%- set dc = state_attr(e, 'device_class') -%}"
        "{%- set st = states(e) -%}"
        "{%- if st not in ['unknown', 'unavailable', 'none', none] -%}"
        "{%- if dc == 'battery' and ns.battery_pct is none -%}{%- set ns.battery_pct = st -%}{%- endif -%}"
        "{%- if dc == 'signal_strength' and ns.rssi is none -%}{%- set ns.rssi = st -%}{%- endif -%}"
        "{%- if dc == 'temperature' and ns.temperature is none -%}{%- set ns.temperature = st -%}{%- endif -%}"
        "{%- endif -%}"
        "{%- endfor -%}"
        "{"
        '"fw_version": {{ (device_attr(d, "sw_version") or "") | tojson }},'
        ' "battery_pct": {{ (ns.battery_pct or "") | tojson }},'
        ' "rssi": {{ (ns.rssi or "") | tojson }},'
        ' "temperature": {{ (ns.temperature or "") | tojson }}'
        "}"
    )


def _num(value: Any) -> int | float | None:
    """Coerce a HA state string to int/float; None for blank / non-numeric."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def parse_telemetry(rendered: str) -> dict[str, Any]:
    """Turn the rendered template JSON into a heartbeat-shaped dict, keeping
    only fields that are actually present (so absent tiles stay blank rather
    than showing zero). Battery percentage and RSSI become ints, temperature
    stays fractional. ``rssi`` is only carried when it's a real dBm reading
    (a ``signal_strength`` sensor), so the card's bars stay honest."""
    try:
        raw = json.loads(rendered)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    fw = raw.get("fw_version")
    if isinstance(fw, str) and fw.strip():
        out["fw_version"] = fw.strip()
    battery = _num(raw.get("battery_pct"))
    if battery is not None:
        out["battery_pct"] = int(battery)
    rssi = _num(raw.get("rssi"))
    if rssi is not None:
        out["rssi"] = int(rssi)
    temperature = _num(raw.get("temperature"))
    if temperature is not None:
        out["temperature"] = temperature
    return out
