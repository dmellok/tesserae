"""Appearance overrides declared by cells and widget manifests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.option_conditions import canonical_value, option_visible
from app.state.page_store import Page
from app.widget_picker import split_name


def _value(spec: Mapping[str, Any], value: Any) -> Any:
    kind = spec.get("type")
    if kind == "boolean":
        return str(value).lower() in {"true", "1", "on"}
    if kind in {"number", "slider"}:
        try:
            return float(value)
        except (TypeError, ValueError):
            return ""
    if kind == "multiselect":
        return sorted(str(v) for v in value) if isinstance(value, list) else []
    value = canonical_value(spec, value)
    return "" if value is None else str(value)


def editor_look_context(page: Page, widgets: Iterable[Any]) -> dict[str, Any]:
    """First-paint warnings and the same manifest rules for live form updates."""
    plugins = {p.id: p for p in widgets}
    rules: dict[str, Any] = {}
    for plugin in plugins.values():
        manifest = plugin.manifest
        always = manifest.get("overrides_look")
        options = [
            {
                "name": spec["name"],
                "type": spec["type"],
                "default": _value(spec, spec.get("default")),
                "message": spec["overrides_look"],
            }
            for spec in manifest.get("cell_options", [])
            if isinstance(spec.get("overrides_look"), str) and spec["overrides_look"].strip()
        ]
        rules[plugin.id] = {
            "always": always if isinstance(always, str) else "",
            "options": options,
        }

    rows = []
    for cell in page.cells:
        if cell.id == page.status_bar_cell_id or cell.plugin is None or cell.plugin not in plugins:
            continue
        plugin = plugins[cell.plugin]
        reasons = [
            {"label": f"own {field}", "field": field, "resettable": True}
            for field in ("theme", "style", "font")
            if getattr(cell, field)
        ]
        if cell.padding_override is not None:
            reasons.append(
                {"label": "own padding", "field": "padding_override", "resettable": True}
            )
        declared = rules[cell.plugin]
        if declared["always"]:
            reasons.append({"label": declared["always"], "field": "", "resettable": False})
        specs = plugin.manifest.get("cell_options", [])
        for spec in specs:
            message = spec.get("overrides_look")
            if not isinstance(message, str) or not message.strip():
                continue
            value = cell.options.get(spec["name"], spec.get("default"))
            if _value(spec, value) != _value(spec, spec.get("default")) and option_visible(
                spec, cell.options, specs
            ):
                reasons.append(
                    {"label": message, "field": f"opt_{spec['name']}", "resettable": False}
                )
        if reasons:
            rows.append(
                {
                    "cell_id": cell.id,
                    "name": split_name(plugin.name, plugin.id)[1],
                    "reasons": reasons,
                    "resettable": any(r["resettable"] for r in reasons),
                    "widget_owned": any(not r["resettable"] for r in reasons),
                }
            )
    return {"look_overrides": rows, "widget_look_rules": rules}
