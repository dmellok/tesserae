"""Shared, bounded-shape adapter for widget choice providers.

Provider exceptions and credentials never become API messages. An empty list
is a successful result; known legacy HA error sentinels are unavailable.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any


def accepts_options(fn: Callable[..., Any]) -> bool:
    """True when a ``choices`` provider takes an ``options`` keyword (or
    ``**kwargs``): it wants the cell's current option values."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        (p.name == "options" and p.kind is not inspect.Parameter.POSITIONAL_ONLY)
        or p.kind is inspect.Parameter.VAR_KEYWORD
        for p in params
    )


def wants_options(plugin: Any) -> bool:
    """Whether this widget's ``choices`` provider reads the cell's options, so
    its rows can differ from one cell to the next."""
    resolver = getattr(getattr(plugin, "server_module", None), "choices", None)
    return callable(resolver) and accepts_options(resolver)


def choice_rows(
    plugin: Any, spec: dict[str, Any], options: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Load the provider's original rows, preserving web-editor metadata/errors.

    A provider whose ``choices`` takes an ``options`` keyword gets the cell's
    current option values (``{}`` when there is no cell to ask about)."""
    source = spec.get("choices_from")
    if source:
        resolver = getattr(plugin.server_module, "choices", None) if plugin else None
        if not callable(resolver):
            raise LookupError("The widget choice provider is unavailable.")
        from app.capabilities import capability_scope

        with capability_scope(getattr(plugin, "capabilities", None)):
            if accepts_options(resolver):
                return list(resolver(source, options=dict(options or {})))
            return list(resolver(source))
    return list(spec.get("choices", []))


# Labels of the single blank row a provider returns when it can't list
# anything at all (as opposed to a real empty value like "(no folders yet)").
# Any widget can say the same with ``"note": true`` on that row.
GUIDANCE_PREFIXES = ("Set HA URL + token", "Home Assistant unreachable", "Stored secret for")


def guidance_note(rows: Any) -> str | None:
    """The provider's "why there is nothing to pick" label, or None."""
    if (
        isinstance(rows, list)
        and len(rows) == 1
        and isinstance(rows[0], dict)
        and rows[0].get("value") == ""
        and (
            rows[0].get("note") is True
            or str(rows[0].get("label") or "").startswith(GUIDANCE_PREFIXES)
        )
        and str(rows[0].get("label") or "").strip()
    ):
        return str(rows[0]["label"])
    return None


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
