"""ha_lights, overview of a set of Home Assistant ``light.*`` entities.

Takes a newline-separated list of entity IDs from the cell options,
looks each one up in a single ``get_states`` call, and returns a clean
per-light shape plus a top-level summary the four variants render:

    {
      "place": str, "time": str,
      "on_count": int, "total": int,
      "lights": [
        {
          "entity_id": "light.kitchen",
          "name": "Kitchen",
          "on": True,
          "brightness_pct": 84,         # 0..100 when on, else None
          "domain_icon": "lightbulb"    # Phosphor glyph hint
        },
        ...
      ]
    }

The widget is defensive on purpose, an offline entity, a missing one,
or HA being unreachable all keep the cell drawing. Anything we can't
resolve gets ``on=False`` and ``brightness_pct=None`` so the variants
have something coherent to paint.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

from flask import current_app


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


def choices(name: str) -> list[dict[str, str]]:
    """Entity picker for the editor, restricted to ``light.*``."""
    core = _core()
    if name == "entity" and core is not None:
        return core.entity_choices(domains=("light",))
    return []


# -- helpers -----------------------------------------------------------


def _parse_entities(raw: Any) -> list[str]:
    """Accept a list, a newline-separated string, or a comma list."""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [tok for tok in re.split(r"[\s,]+", str(raw or "").strip()) if tok]


def _state(states: list[dict[str, Any]], entity_id: str) -> dict[str, Any] | None:
    """Linear lookup over the get_states() result. The full state dump
    for a typical install is a few hundred entries; one pass is fine."""
    for st in states:
        if st.get("entity_id") == entity_id:
            return st
    return None


def _brightness_pct(value: Any) -> int | None:
    """HA reports ``attributes.brightness`` on the 0..255 scale when a
    light is on; coerce to a clamped 0..100 percentage, or ``None`` when
    HA didn't include it (some dimmable lights only expose state)."""
    if value in (None, "", "unavailable", "unknown"):
        return None
    try:
        b = float(value)
    except (TypeError, ValueError):
        return None
    pct = round((b / 255.0) * 100)
    if pct < 0:
        return 0
    if pct > 100:
        return 100
    return int(pct)


def _icon_hint(attrs: dict[str, Any]) -> str:
    """Prefer the icon HA already provides for the entity (handy when the
    user has set a custom MDI/HA icon), but strip the ``mdi:`` prefix and
    fall back to a plain ``lightbulb`` so we always end up with something
    the Phosphor map can render."""
    raw = str(attrs.get("icon") or "").strip().lower()
    if not raw:
        return "lightbulb"
    # HA icons look like "mdi:ceiling-light", keep the suffix only.
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    # Phosphor doesn't carry every MDI glyph; the variants only use the
    # bulb anyway. Pass the hint through for renderers that want to map.
    return raw or "lightbulb"


# -- entry point -------------------------------------------------------


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    core = _core()
    if core is None:
        return {"error": "Install the Home Assistant Core plugin to use this widget."}

    wanted = _parse_entities(options.get("entities") or "")
    place = options.get("label") or "Home"
    now = datetime.now().strftime("%H:%M")

    try:
        states = core.get_states()
    except Exception as err:
        return {"error": core.coerce_error(err)}

    # Empty picker = "show every light HA knows about". Lets the user
    # drop a `ha_lights` cell with no config and immediately see
    # something useful, then narrow down later.
    if not wanted:
        wanted = [
            str(st.get("entity_id") or "")
            for st in states
            if str(st.get("entity_id") or "").startswith("light.")
        ]
        if not wanted:
            return {
                "place": place,
                "time": now,
                "lights": [],
                "on_count": 0,
                "total": 0,
                "empty": True,
            }

    lights: list[dict[str, Any]] = []
    on_count = 0
    for eid in wanted:
        st = _state(states, eid)
        if st is None:
            # Missing or never-seen entity. Keep it in the list so the
            # user notices the typo, but mark it off + dimmed.
            lights.append(
                {
                    "entity_id": eid,
                    "name": eid.split(".", 1)[-1].replace("_", " ").title(),
                    "on": False,
                    "brightness_pct": None,
                    "domain_icon": "lightbulb",
                    "missing": True,
                }
            )
            continue
        attrs = st.get("attributes") or {}
        raw_state = str(st.get("state") or "").lower()
        on = raw_state == "on"
        if on:
            on_count += 1
        brightness = _brightness_pct(attrs.get("brightness")) if on else None
        # Colour information, HA reports either color_temp_kelvin
        # (direct), color_temp (mireds, legacy), or hs_color (when
        # the bulb is in colour mode). We forward whatever's there;
        # the client picks one to render as a tiny swatch dot.
        color_temp_kelvin = None
        if on:
            kelvin_raw = attrs.get("color_temp_kelvin")
            mireds_raw = attrs.get("color_temp")
            try:
                if kelvin_raw not in (None, "", "unavailable"):
                    color_temp_kelvin = int(float(kelvin_raw))
                elif mireds_raw not in (None, "", "unavailable"):
                    mireds = float(mireds_raw)
                    if mireds > 0:
                        color_temp_kelvin = int(1_000_000 / mireds)
            except (TypeError, ValueError):
                color_temp_kelvin = None
        hs_color = attrs.get("hs_color") if on else None
        if isinstance(hs_color, (list, tuple)) and len(hs_color) >= 2:
            try:
                hs_color = [float(hs_color[0]), float(hs_color[1])]
            except (TypeError, ValueError):
                hs_color = None
        else:
            hs_color = None
        lights.append(
            {
                "entity_id": eid,
                "name": core.friendly_name(st),
                "on": on,
                "brightness_pct": brightness,
                "color_temp_kelvin": color_temp_kelvin,
                "hs_color": hs_color,
                "domain_icon": _icon_hint(attrs),
                "missing": False,
            }
        )

    return {
        "place": place,
        "time": now,
        "lights": lights,
        "on_count": on_count,
        "total": len(lights),
        "_fetched_at": int(time.time()),
    }
