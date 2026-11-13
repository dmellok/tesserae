"""What the lineup editor shows about a dashboard and a display.

The editor (``templates/deck_editor.html``, ``static/deck_editor.js``) draws
every row from these: a dashboard's one-line subtitle ("Gallery · 412
photos"), what keeps it fresh, and which display options apply to a display
(taps, return home, send-before-wake), so the page only offers settings that
do something on that display.

Pure reads of the page, plugin and device registries; nothing here writes.
"""

from __future__ import annotations

import re
from typing import Any

from flask import current_app

from app.step_items import item_summary, named_placements

#: Widget option names that hold a list of Home Assistant entities.
_ENTITY_OPTIONS = ("entities", "entity_ids")


#: Widget families whose variant is what they are ("Picture, Gallery" is a
#: gallery); for the others the family is ("Home Assistant, Entities").
_VARIANT_FAMILIES = frozenset({"Picture", "News"})
_FAMILY_NAMES = frozenset({"Home Assistant", "Calendar", "Clock", "Weather"})


def _plugin_name(plugin_id: str) -> str:
    """A widget's short name: "Picture, Gallery" reads "Gallery", "Home
    Assistant, Entities" reads "Home Assistant"."""
    reg = current_app.config.get("PLUGIN_REGISTRY")
    plugin = reg.get(plugin_id) if reg is not None else None
    manifest = getattr(plugin, "manifest", None) or {}
    name = str(manifest.get("name") or plugin_id.replace("_", " ").capitalize())
    if "," not in name:
        return name
    family, variant = (x.strip() for x in name.split(",", 1))
    if family in _VARIANT_FAMILIES:
        return variant
    if family in _FAMILY_NAMES:
        return family
    return f"{family} {variant.lower()}"


def _plugin_icon(plugin_id: str) -> str:
    """A widget's Phosphor icon class ("ph-images"), from its manifest."""
    reg = current_app.config.get("PLUGIN_REGISTRY")
    plugin = reg.get(plugin_id) if reg is not None else None
    icon = str((getattr(plugin, "manifest", None) or {}).get("icon") or "")
    return icon if re.fullmatch(r"ph-[a-z0-9-]+", icon) else ""


def _every(minutes: int) -> str:
    if minutes % 1440 == 0:
        n = minutes // 1440
        return "day" if n == 1 else f"{n} days"
    if minutes % 60 == 0:
        n = minutes // 60
        return "hour" if n == 1 else f"{n} h"
    return f"{minutes} min"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun[:-1] if n == 1 and noun.endswith('s') else noun}"


def dashboard_summary(page: Any, device_id: str | None = None) -> dict[str, Any]:
    """``{kind, icon, facts, subtitle, items, fresh}`` for one dashboard.

    ``kind`` names what it is (the widget, or the first two of several) and
    ``icon`` is that widget's Phosphor icon ("" for several or none);
    ``facts`` the key numbers ("412 photos, in order", "12 entities");
    ``items`` is ``app.step_items.item_summary``; ``fresh`` the one line on
    what keeps it current, from its own Updates (``Page.refresh_minutes``)."""
    widgets = named_placements(page)
    items: dict[str, Any] = (
        item_summary(page, device_id) if widgets else {"mode": "none", "count": 1, "noun": "items"}
    )
    lister = items.get("plugin") or ""
    icon = ""
    if lister:
        kind = _plugin_name(lister)
        icon = _plugin_icon(lister)
    elif len(widgets) == 1:
        kind = _plugin_name(widgets[0][0])
        icon = _plugin_icon(widgets[0][0])
    elif widgets:
        names = list(dict.fromkeys(_plugin_name(pid) for pid, _ in widgets))
        kind = " & ".join(names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else "")
    else:
        kind = "Canvas" if getattr(page, "layout_kind", "") == "canvas" else "Empty"
    facts: list[str] = []
    if items["mode"] == "pick":
        fact = _count(int(items["count"]), str(items["noun"]))
        if items.get("pinned"):
            fact += f", chosen: {items['pinned_label'] or items['pinned']}"
        elif lister == "picture_gallery":
            mode = next(
                (str(pl.options.get("mode") or "random") for pid, pl in widgets if pid == lister),
                "random",
            )
            fact += ", in order" if mode == "sequential" else ", random each time"
            # The row says how one is picked per visit.
            items = {**items, "order": "in turn" if mode == "sequential" else "random"}
        facts.append(fact)
    elif items["mode"] == "draw":
        count = int(items["count"])
        facts.append(
            f"{_count(count, str(items['noun']))}, random each time"
            if count
            else "a fresh draw each time"
        )
    else:
        entities = 0
        for _pid, placement in widgets:
            options = getattr(placement, "options", None) or {}
            for name in _ENTITY_OPTIONS:
                value = options.get(name)
                if isinstance(value, list):
                    entities += len(value)
        if entities:
            facts.append(_count(entities, "entities"))
    refresh = int(getattr(page, "refresh_minutes", 0) or 0)
    noun = "photo" if items.get("noun") == "photos" else "picture"
    if refresh:
        fresh = f"New {noun} every {_every(refresh)} while shown."
    elif items["mode"] in ("pick", "draw"):
        fresh = f"A new {noun} each time it is sent."
    else:
        fresh = "Redrawn each time it is sent."
    return {
        "kind": kind,
        "icon": icon,
        "facts": ", ".join(facts),
        "subtitle": " · ".join(x for x in (kind, ", ".join(facts)) if x),
        "items": items,
        "fresh": fresh,
        "refresh_minutes": refresh,
    }


def display_caps(device: Any) -> dict[str, Any]:
    """Which lineup options do something on ``device``.

    ``buttons`` / ``touch``: it sends presses or taps (taps also flip, return
    home and the home dashboard need one). ``sleeps``: it sleeps and checks
    in, so a send can wait for its wake ("send just before it wakes");
    ``wake_learned`` when its wake times are trusted. A display with neither
    (a TV that takes pushes, an always-on panel) has no display options."""
    from app.device_loader import has_buttons, has_touch
    from app.next_update import MODE_SLEEPS, MODE_UNKNOWN, reach_for_device

    buttons = has_buttons(device)
    touch = has_touch(device)
    try:
        reach = reach_for_device(device)
        mode, learned = reach.mode, bool(reach.cadence_stable)
    except Exception:
        mode, learned = MODE_UNKNOWN, False
    schema = (getattr(device, "manifest", None) or {}).get("config_schema") or {}
    checks_in = isinstance(schema, dict) and "sleep_interval_s" in schema
    sleeps = mode == MODE_SLEEPS or (mode == MODE_UNKNOWN and checks_in)
    return {
        "buttons": buttons,
        "touch": touch,
        "inputs": buttons or touch,
        "sleeps": sleeps,
        "wake_learned": sleeps and learned,
    }


def display_model(device: Any, view: dict[str, Any] | None) -> str:
    """ "Visionect frame · 1440×2560, 16 greys": the kind and the panel."""
    reg = current_app.config.get("DEVICE_REGISTRY")
    kind_id = getattr(device, "kind_of", None)
    kind = reg.get(kind_id) if reg is not None and kind_id else None
    kind_name = str(getattr(kind, "name", "") or "")
    size = str((view or {}).get("size_label") or "")
    levels = int((view or {}).get("grey_levels") or 0)
    gamut = str((view or {}).get("gamut") or "")
    look = f"{levels} greys" if levels else ("colour" if gamut.startswith("colo") else "")
    panel = ", ".join(x for x in (size, look) if x)
    return " · ".join(x for x in (kind_name, panel) if x)
