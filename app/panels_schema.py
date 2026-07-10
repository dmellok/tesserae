"""Widget data schema for the Panels canvas editor (issue #60).

Widgets expose data via ``server.py`` ``fetch()``, which returns a dict, but
the shape is undeclared: nothing says ``weather_now`` emits
``{temp, feels_like, condition, ...}`` with types. The canvas editor (where
widgets are data sources and users bind individual fields to visual
elements) needs that shape to offer fields for binding.

This module defines the ``data_schema`` block a widget declares in its
``plugin.json`` and assembles the editor's widget catalog. A widget with a
hand-authored block gets its declared fields (with labels/units); a widget
without one falls back to a schema DRAFTED from its dev-gallery sample, so it
is still bindable without an authoring step. :func:`derive_schema` does that
drafting (the hybrid model: introspect to draft, optionally confirm
labels/units/types into the manifest later).

``data_schema`` shape in plugin.json::

    "data_schema": {
      "color": "#256E6B",              // optional accent for the Data panel
      "fields": [
        {"name": "temp", "type": "num", "label": "Temperature", "unit": "deg"},
        {"name": "hourly", "type": "arr"}
      ],
      "sample": {"temp": 72, "hourly": [/* ... */]}   // for editor mock/live
    }

Field ``type`` is one of ``num`` / ``str`` / ``arr``. The catalog entry the
editor consumes is ``{key, name, icon, color, desc, fields, sample}``.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.plugin_loader import Plugin, PluginRegistry

_TYPE_NUM = "num"
_TYPE_STR = "str"
_TYPE_ARR = "arr"
_VALID_TYPES = frozenset({_TYPE_NUM, _TYPE_STR, _TYPE_ARR})

# Keys a fetch() result may carry that are transport/status, not bindable
# data fields. ``error`` is the widget's failure channel; skip it so a
# drafted schema doesn't offer "error" as a field to bind.
_SKIP_KEYS = frozenset({"error"})

# Fallback accent when a widget's data_schema doesn't declare a colour.
_DEFAULT_COLOR = "#256E6B"


def _infer_type(value: Any) -> str:
    """Map a sample value to a field type. Booleans are treated as strings
    (they render as text/state, not numbers)."""
    if isinstance(value, bool):
        return _TYPE_STR
    if isinstance(value, (int, float)):
        return _TYPE_NUM
    if isinstance(value, (list, tuple)):
        return _TYPE_ARR
    return _TYPE_STR


def derive_schema(fetch_result: dict[str, Any]) -> dict[str, Any]:
    """Draft a ``data_schema`` (``{fields, sample}``) from a ``fetch()``
    result. Each key becomes a field with an inferred type; ``sample``
    captures the values verbatim for the editor's mock/live preview. Known
    non-field keys (``error``) are skipped. Meant to bootstrap authoring; a
    human confirms labels/units/types before it lands in ``plugin.json``."""
    fields: list[dict[str, Any]] = []
    sample: dict[str, Any] = {}
    for key, value in fetch_result.items():
        if not isinstance(key, str) or key in _SKIP_KEYS:
            continue
        fields.append({"name": key, "type": _infer_type(value)})
        sample[key] = value
    return {"fields": fields, "sample": sample}


def _normalise_fields(raw_fields: Any) -> list[dict[str, Any]]:
    """Validate + normalise a declared ``fields`` list into the catalog
    shape. Drops malformed entries; keeps optional label/unit when present."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw_fields, list):
        return out
    for field in raw_fields:
        if not isinstance(field, dict):
            continue
        name = field.get("name")
        if not isinstance(name, str) or not name:
            continue
        ftype = field.get("type")
        ftype = ftype if ftype in _VALID_TYPES else _TYPE_STR
        entry: dict[str, Any] = {"name": name, "type": ftype}
        label = field.get("label")
        if isinstance(label, str) and label:
            entry["label"] = label
        unit = field.get("unit")
        if isinstance(unit, str) and unit:
            entry["unit"] = unit
        out.append(entry)
    return out


def _entry(
    plugin: Plugin, fields: list[dict[str, Any]], sample: Any, color: Any = None
) -> dict[str, Any]:
    return {
        "key": plugin.id,
        "name": plugin.name,
        "icon": str(plugin.manifest.get("icon") or "puzzle-piece"),
        "color": color if isinstance(color, str) and color else _DEFAULT_COLOR,
        "desc": str(plugin.manifest.get("description") or ""),
        "fields": fields,
        "sample": sample if isinstance(sample, dict) else {},
    }


def _sample_derived(plugin: Plugin) -> dict[str, Any] | None:
    """Draft a schema from the widget's dev-gallery sample payload, so a widget
    without a hand-authored ``data_schema`` is still bindable. Reuses the
    ``widget_samples`` registry (the same frozen payloads the ``?sample=1``
    gallery renders), keeping one source of truth for a widget's data shape.
    Returns None for widgets with no sample (client-only widgets, or ones whose
    data only exists after a live fetch)."""
    try:
        from app.widget_samples import get_sample
    except Exception:
        return None
    payload = get_sample(plugin.id)
    if not isinstance(payload, dict) or not payload:
        return None
    drafted = derive_schema(payload)
    return drafted if drafted["fields"] else None


def catalog_entry(plugin: Plugin) -> dict[str, Any] | None:
    """Build a catalog entry for a widget usable as a canvas data source.

    Field source, in priority order: a hand-authored ``data_schema`` in the
    manifest (best: carries labels/units), else a schema drafted from the
    widget's dev-gallery sample. Returns None when neither yields fields, i.e.
    the widget isn't bindable (client-only, or live-fetch-only with no sample)."""
    schema = plugin.manifest.get("data_schema")
    if isinstance(schema, dict):
        fields = _normalise_fields(schema.get("fields"))
        if fields:
            return _entry(plugin, fields, schema.get("sample"), schema.get("color"))
    derived = _sample_derived(plugin)
    if derived is not None:
        return _entry(plugin, derived["fields"], derived["sample"])
    return None


def build_catalog(registry: PluginRegistry) -> list[dict[str, Any]]:
    """Assemble the editor's widget catalog from every widget plugin whose data
    shape can be resolved (declared ``data_schema`` or drafted from a sample).
    Widgets with neither are omitted (not yet bindable). Sorted by display name
    for a stable picker order."""
    entries: list[dict[str, Any]] = []
    for plugin in registry.widgets():
        entry = catalog_entry(plugin)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda e: str(e["name"]).lower())
    return entries
