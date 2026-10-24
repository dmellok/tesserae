"""Scoped, partial editing of dashboard metadata and declared Canvas inputs.

No layout, code or source targets cross this API boundary. The shared input
applier writes the same slots as Configure on the web. A whole-page revision
is checked inside PageStore's write lock, including changes made through MCP.
"""

from __future__ import annotations

import copy
import json
import math
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from pydantic import ValidationError

from app.state.page_store import Page, page_revision

_SETTING_NAMES = frozenset(
    ("name", "icon", "device_ids", "refresh_minutes", "sleep_interval_s", "theme", "style", "font")
)
_TEXT_TYPES = frozenset(("string", "text", "textarea", "url", "password", "color"))
_SUPPORTED_TYPES = _TEXT_TYPES | {"number", "integer", "boolean", "switch", "select", "multiselect"}


class InvalidSettings(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.fields = {field: message}


def appearance_options() -> dict[str, list[dict[str, Any]]]:
    from app.panels_routes import appearance_options as panel_appearance_options

    raw = panel_appearance_options()
    return {
        group: [
            {
                "value": item.get("value", item.get("id")),
                "label": item.get("label", item.get("name", item.get("id", ""))),
            }
            for item in raw[group]
        ]
        for group in ("themes", "styles", "fonts")
    }


def _target_container(
    canvas: dict[str, Any], target: dict[str, Any]
) -> tuple[dict[str, Any], str] | None:
    el = next((el for el in canvas.get("els", []) if el.get("id") == target.get("el")), None)
    if el is None:
        return None
    slot, key, index = target.get("slot"), target.get("key"), target.get("index")
    if slot == "options" and key:
        return el.setdefault("options", {}), key
    collection = el.get("bind" if slot == "bind_options" else "sources", [])
    if type(index) is not int or index < 0 or index >= len(collection):
        return None
    entry = collection[index]
    if slot in ("source_options", "bind_options") and key:
        return entry.setdefault("options", {}), key
    if slot == "source_url":
        return entry, "url"
    if slot == "source_header":
        return (entry.setdefault("headers", {}), key) if key else (entry, "headers")
    return None


def _target_secret(canvas: dict[str, Any], target: dict[str, Any], registry: Any) -> bool:
    if target.get("slot") == "source_header":
        return True
    el: dict[str, Any] = next(
        (el for el in canvas.get("els", []) if el.get("id") == target.get("el")), {}
    )
    slot, index = target.get("slot"), target.get("index")
    plugin_id = el.get("widget") or el.get("source")
    if slot in ("source_options", "bind_options"):
        entries = el.get("bind" if slot == "bind_options" else "sources", [])
        if type(index) is int and 0 <= index < len(entries):
            plugin_id = entries[index].get("source" if slot == "bind_options" else "key")
    plugin = registry.get(plugin_id) if registry and plugin_id else None
    return bool(
        plugin
        and any(
            opt.get("name") == target.get("key")
            and (opt.get("secret") or opt.get("mask") or opt.get("type") == "password")
            for opt in plugin.manifest.get("cell_options", [])
        )
    )


def _input_specs(page: Page, registry: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if page.layout_kind != "canvas" or page.canvas is None:
        return {}, []
    canvas = page.canvas.model_dump(mode="json")
    specs = copy.deepcopy(canvas["inputs"])
    name_counts = Counter(spec["name"] for spec in specs)
    # A secret target stays secret through overlapping declarations too. Never
    # let a second, non-secret alias turn a masked credential into readable data.
    secret_slots: set[tuple[int, str]] = set()
    for spec in specs:
        spec["secret"] = bool(
            spec["secret"]
            or spec.get("mask")
            or spec["type"] == "password"
            or any(_target_secret(canvas, target, registry) for target in spec["targets"])
        )
        if spec["secret"]:
            for target in spec["targets"]:
                resolved = _target_container(canvas, target)
                if resolved:
                    container, key = resolved
                    secret_slots.add((id(container), key))
                    if target["slot"] == "source_header" and not target.get("key"):
                        secret_slots.update(
                            (id(container[key]), header) for header in container[key]
                        )
    # Propagate through inputs that fan out to several targets, until every
    # alias of a credential is masked regardless of declaration order.
    secret_names = {spec["name"] for spec in specs if spec["secret"]}
    changed = True
    while changed:
        changed = False
        for spec in specs:
            slots = {
                (id(resolved[0]), resolved[1])
                for target in spec["targets"]
                if (resolved := _target_container(canvas, target))
            }
            if spec["secret"] or spec["name"] in secret_names or slots & secret_slots:
                spec["secret"] = True
                if spec["name"] not in secret_names:
                    secret_names.add(spec["name"])
                    changed = True
                if slots - secret_slots:
                    secret_slots.update(slots)
                    changed = True
    for spec in specs:
        spec["_duplicate"] = name_counts[spec["name"]] > 1
        spec["editable"] = (
            not spec["_duplicate"]
            and spec["type"] in _SUPPORTED_TYPES
            and (not spec["secret"] or spec["type"] in _TEXT_TYPES)
            and bool(spec["targets"])
            and all(_target_container(canvas, target) is not None for target in spec["targets"])
        )
    return canvas, specs


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def response(
    page: Page, *, can_edit: bool, options: dict[str, Any], registry: Any
) -> dict[str, Any]:
    settings = {name: getattr(page, name) for name in _SETTING_NAMES}
    if page.layout_kind == "canvas" and page.canvas is not None:
        settings.update(
            theme=page.canvas.theme, style=page.canvas.style, font=page.canvas.font or None
        )
    from app.companion_component_settings import Schema

    return {
        "dashboard_id": page.id,
        "kind": page.layout_kind,
        "revision": page_revision(page),
        "can_edit": can_edit,
        "settings": settings,
        "options": options,
        **Schema(page, registry).response(),
        "web_url": f"/pages/canvas/c/{quote(page.id, safe='')}/configure"
        if page.layout_kind == "canvas"
        else f"/pages/{quote(page.id, safe='')}",
    }


def _validate_settings(
    settings: dict[str, Any], options: dict[str, Any], device_ids: set[str]
) -> None:
    for key, value in settings.items():
        field = f"settings.{key}"
        if key not in _SETTING_NAMES:
            raise InvalidSettings(field, "This setting is not editable here.")
        if key == "name":
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise InvalidSettings(field, "Enter a name between 1 and 200 characters.")
        elif key == "icon":
            if value is not None and (not isinstance(value, str) or len(value) > 100):
                raise InvalidSettings(field, "Use an icon name of at most 100 characters or null.")
        elif key == "device_ids":
            if not isinstance(value, list) or any(
                not isinstance(v, str) or v not in device_ids for v in value
            ):
                raise InvalidSettings(field, "Choose existing displays.")
            if len(value) != len(set(value)):
                raise InvalidSettings(field, "Choose each display only once.")
        elif key in ("refresh_minutes", "sleep_interval_s"):
            if key == "sleep_interval_s" and value is None:
                continue
            low, high = (0, 1440) if key == "refresh_minutes" else (1, 604800)
            if type(value) is not int or not low <= value <= high:
                raise InvalidSettings(field, f"Enter a whole number between {low} and {high}.")
        else:
            if key == "font" and value is None:
                continue
            if not isinstance(value, str) or value not in {
                opt["value"] for opt in options[f"{key}s"]
            }:
                raise InvalidSettings(field, "Choose an available appearance option.")


def _validate_input(spec: dict[str, Any], value: Any) -> None:
    field, kind = f"inputs.{spec['name']}", spec["type"]
    if not spec["editable"]:
        raise InvalidSettings(field, "Edit this input in the web editor.")
    if _empty(value):
        if spec["required"]:
            raise InvalidSettings(field, "This field is required.")
        if value is None:
            return
    if kind in _TEXT_TYPES:
        valid = isinstance(value, str) and len(value) <= 16384
    elif kind == "integer":
        valid = type(value) is int
    elif kind == "number":
        valid = type(value) is int or (type(value) is float and math.isfinite(value))
    elif kind in ("boolean", "switch"):
        valid = type(value) is bool
    elif kind == "select":
        valid = any(
            type(value) is type(c.get("value")) and value == c.get("value") for c in spec["choices"]
        )
    elif kind == "multiselect":
        valid = isinstance(value, list) and all(
            any(type(v) is type(c.get("value")) and v == c.get("value") for c in spec["choices"])
            for v in value
        )
    else:
        valid = False
    if not valid:
        raise InvalidSettings(field, "Enter a value matching the field type and available choices.")
    for target in spec["targets"]:
        if target["slot"] in ("source_url", "source_header") and not isinstance(value, str):
            raise InvalidSettings(field, "This source field requires text.")
        if target["slot"] == "source_header" and not target.get("key"):
            try:
                headers = json.loads(value) if value else {}
            except (ValueError, TypeError):
                headers = None
            if not isinstance(headers, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in headers.items()
            ):
                raise InvalidSettings(field, "Enter a JSON object of header names and text values.")


def apply_patch(
    page: Page,
    body: dict[str, Any],
    *,
    options: dict[str, Any],
    device_ids: set[str],
    registry: Any,
) -> Page:
    from app.companion_component_settings import Schema

    settings = body.get("settings", {})
    if not isinstance(settings, dict):
        raise InvalidSettings("settings", "Settings must be a JSON object.")
    _validate_settings(settings, options, device_ids)
    try:
        updated = Schema(page, registry).apply(body)
    except ValidationError:
        raise InvalidSettings(
            "inputs", "These values cannot be applied to this dashboard."
        ) from None
    updates = dict(settings)
    if "name" in updates:
        updates["name"] = updates["name"].strip()
    if updated.canvas:
        appearance = {
            key: settings[key] or "" for key in ("theme", "style", "font") if key in settings
        }
        updates["canvas"] = updated.canvas.model_copy(update=appearance, deep=True)
    updates.update(updated_at=datetime.now(UTC).isoformat(), updated_by="companion")
    return updated.model_copy(update=updates, deep=True)
