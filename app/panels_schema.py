"""Widget catalog for the Panels canvas editor (issue #60).

Every renderable widget can be dropped onto the freeform canvas. A widget may
declare ``fragments`` in its ``plugin.json`` to expose individually-placeable
parts (e.g. just the temperature of a weather card); the catalog surfaces those
plus an implicit ``full`` fragment for whole-widget placement, so the editor
palette can list a widget and expand it to its parts.

The catalog entry the editor consumes is
``{key, name, icon, desc, fragments:[...], updates_on_change,
updates_on_schedule, strings}``.

mypy --strict does not apply here; see pyproject.toml.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.plugin_loader import Plugin, PluginRegistry

# Default box a fragment is dropped at when it declares no size.
_DEFAULT_W = 240
_DEFAULT_H = 160


def fragments_of(plugin: Plugin) -> list[dict[str, Any]]:
    """The widget's declared fragments (validated), always including a ``full``
    entry so the whole widget is placeable even when no fragments are declared.
    Malformed fragment entries are dropped."""
    out: list[dict[str, Any]] = []
    raw = plugin.manifest.get("fragments")
    if isinstance(raw, list):
        for frag in raw:
            if not isinstance(frag, dict):
                continue
            fid = frag.get("id")
            if not isinstance(fid, str) or not fid:
                continue
            entry: dict[str, Any] = {
                "id": fid,
                "label": str(frag.get("label") or fid),
                "w": frag["w"] if isinstance(frag.get("w"), int) else _DEFAULT_W,
                "h": frag["h"] if isinstance(frag.get("h"), int) else _DEFAULT_H,
            }
            icon = frag.get("icon")
            if isinstance(icon, str) and icon:
                entry["icon"] = icon
            out.append(entry)
    if not any(f["id"] == "full" for f in out):
        out.insert(0, {"id": "full", "label": "Full widget", "w": _DEFAULT_W, "h": _DEFAULT_H})
    return out


def _preview_sample(plugin: Plugin) -> dict[str, Any] | None:
    """The widget's dev-gallery sample payload, used by the editor to mount a
    live WYSIWYG preview without a real fetch. None when the widget ships no
    sample (it previews with its own empty/error state instead)."""
    try:
        from app.widget_samples import get_sample
    except Exception:
        return None
    payload = get_sample(plugin.id)
    return payload if isinstance(payload, dict) else None


def catalog_entry(plugin: Plugin, locale: str = "en") -> dict[str, Any]:
    """Catalog entry for a placeable widget: identity, fragments, a preview
    sample for the editor's live mount, and this widget's resolved strings
    for ``locale`` ({} for a widget that hasn't declared any locales --
    see Plugin.strings_for). The editor's live preview isn't rendering
    for any particular device, so it always uses the app-wide default
    locale (composer.py's ``_resolve_locale_for_device_id("")``), same
    as an unbound grid/canvas preview."""
    return {
        "key": plugin.id,
        "name": plugin.name,
        "icon": str(plugin.manifest.get("icon") or "ph-puzzle-piece"),
        "desc": str(plugin.manifest.get("description") or ""),
        "fragments": fragments_of(plugin),
        "sample": _preview_sample(plugin),
        # Deliberately read the manifest instead of Plugin.on_change_updates:
        # catalog builders accept duck-typed plugin objects in tests and tools.
        "updates_on_change": bool(
            isinstance(plugin.manifest.get("updates"), dict)
            and plugin.manifest["updates"].get("on_change")
        ),
        "updates_on_schedule": [dict(spec) for spec in plugin.on_schedule_updates],
        "strings": plugin.strings_for(locale),
    }


def build_catalog(registry: PluginRegistry, locale: str = "en") -> list[dict[str, Any]]:
    """Assemble the editor's widget palette: every renderable widget plugin
    (kind ``widget``), each with its fragments, sorted by display name."""
    entries = [catalog_entry(p, locale) for p in registry.widgets()]
    entries.sort(key=lambda e: str(e["name"]).lower())
    return entries
