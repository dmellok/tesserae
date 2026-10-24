"""Shared, bounded-shape adapter for widget choice providers.

Provider exceptions and credentials never become API messages. An empty list
is a successful result; known legacy HA error sentinels are unavailable.
"""

from __future__ import annotations

from typing import Any


def choice_rows(plugin: Any, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Load the provider's original rows, preserving web-editor metadata/errors."""
    source = spec.get("choices_from")
    if source:
        resolver = getattr(plugin.server_module, "choices", None) if plugin else None
        if not callable(resolver):
            raise LookupError("The widget choice provider is unavailable.")
        return list(resolver(source))
    return list(spec.get("choices", []))


def resolve_choices(plugin: Any, spec: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    try:
        raw = choice_rows(plugin, spec)
        rows = [
            {"value": item["value"], "label": str(item.get("label", item["value"]))}
            for item in raw
            if isinstance(item, dict) and "value" in item
        ]
        if (
            len(rows) == 1
            and rows[0]["value"] == ""
            and (
                rows[0]["label"].startswith("Set HA URL + token")
                or rows[0]["label"].startswith("Home Assistant unreachable")
            )
        ):
            return [], False
        return rows, True
    except Exception:
        return [], False
