"""``show_if`` on widget cell options: show a field only when it applies.

A cell option can declare ``show_if``, an object keyed by other option
names. Every key must match for the field to show::

    "show_if": {"layout": "cluster"}                     equals
    "show_if": {"pictures": ["gen", "plate_gen"]}        any of
    "show_if": {"url": {"not": ""}}                      none of (here: set)

Values compare as strings, so ``true`` matches a ticked switch and ``"8"``
matches 8. A multiselect matches when any of its picks matches. A ``select``
choice may list ``aliases``, the values an earlier manifest used for it; a
saved alias reads as that choice (``canonical_value``), here, in the
editor's select and in the value the next save writes. A field
whose controlling option is itself hidden is hidden too, so a chain like
"cluster style only for the cluster layout, refresh only for the blend
style" needs no repetition.

Hiding is presentation only. A hidden field stays in the form and is
submitted with its value, so switching back restores what was there and
the widget's saved options never lose a key. This module answers the
first render on the server (so nothing flashes); ``static/components.js``
(``attachShowIf``) keeps the form in step as the operator edits.

An option can also carry ``"advanced": true``: fine-tuning a typical user
never touches. Forms render those last, in manifest order, inside a
collapsed "More options" disclosure (``option_sections``). ``show_if``
still applies inside it, and the disclosure hides when none of its options
would show.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

# Deeper than any real manifest; stops a cycle (a shows-if b shows-if a)
# from recursing forever. A cycle evaluates as hidden.
_MAX_DEPTH = 8


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Mapping):
        return "[object Object]"
    if type(value) in (int, float):
        try:
            number = float(value)
        except OverflowError:
            return str(value)
        if math.isfinite(number):
            if number == 0:
                return "0"
            shortest = repr(number)
            if 1e-6 <= abs(number) < 1e21:
                fixed = format(Decimal(shortest), "f")
                return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed
            coefficient, exponent = shortest.lower().split("e")
            if "." in coefficient:
                coefficient = coefficient.rstrip("0").rstrip(".")
            return f"{coefficient}e{int(exponent):+d}"
    return str(value)


def _matches(actual: Any, expected: Any) -> bool:
    """One ``show_if`` entry against the controlling option's value."""
    if isinstance(expected, Mapping) and "not" in expected:
        return not _matches(actual, expected["not"])
    wanted = (
        {_as_text(v) for v in expected}
        if isinstance(expected, list | tuple)
        else {_as_text(expected)}
    )
    if isinstance(actual, list | tuple):
        return any(_as_text(v) in wanted for v in actual)
    return _as_text(actual) in wanted


def canonical_value(spec: Mapping[str, Any], value: Any) -> Any:
    """``value`` as the option's current choice value.

    A ``select`` choice may list ``aliases``: values an earlier manifest used
    for it. A saved alias stands for the choice it names, so a widget can
    rename a value without stranding every cell that still holds the old
    one. An exact match outranks another choice's alias; anything else comes
    back as it was."""
    choices = spec.get("choices")
    if spec.get("type") != "select" or not isinstance(choices, list):
        return value
    text = _as_text(value)
    if text == "":
        return value
    found: Any = None
    matched = False
    for choice in choices:
        if not isinstance(choice, Mapping) or "value" not in choice:
            continue
        if _as_text(choice["value"]) == text:
            return value
        aliases = choice.get("aliases")
        if not matched and isinstance(aliases, list) and text in {_as_text(a) for a in aliases}:
            found, matched = choice["value"], True
    return found if matched else value


def option_visible(
    spec: Mapping[str, Any],
    values: Mapping[str, Any] | None,
    specs: Iterable[Mapping[str, Any]],
    _depth: int = 0,
) -> bool:
    """Whether ``spec`` shows given the cell's current ``values``.

    ``specs`` is the widget's full option list, used for defaults of
    options the stored values don't carry yet and for the controlling
    options' own conditions. A ``show_if`` naming an option the widget
    doesn't have is ignored rather than hiding the field for good."""
    cond = spec.get("show_if")
    if not isinstance(cond, Mapping) or not cond:
        return True
    if _depth >= _MAX_DEPTH:
        return False
    by_name = {s.get("name"): s for s in specs if isinstance(s, Mapping)}
    current = values or {}
    for name, expected in cond.items():
        controller = by_name.get(name)
        if controller is None:
            continue
        if not option_visible(controller, current, by_name.values(), _depth + 1):
            return False
        actual = current[name] if name in current else controller.get("default")
        if not _matches(canonical_value(controller, actual), expected):
            return False
    return True


def is_advanced(spec: Mapping[str, Any]) -> bool:
    """Whether ``spec`` is fine-tuning (``"advanced": true``), shown in the
    collapsed "More options" section at the end of the form."""
    return spec.get("advanced") is True


def option_sections(
    specs: Iterable[Mapping[str, Any]],
    values: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Split a widget's options into the form's two sections.

    ``basic`` keeps manifest order and renders first; ``advanced`` keeps
    manifest order and renders inside the "More options" disclosure.
    ``more_visible`` is False when every advanced option's ``show_if`` is
    false, so the disclosure itself starts hidden (its fields are still in
    the form and still submitted). ``attachShowIf`` keeps it in step."""
    all_specs = [s for s in specs if isinstance(s, Mapping)]
    basic = [s for s in all_specs if not is_advanced(s)]
    advanced = [s for s in all_specs if is_advanced(s)]
    more_visible = any(option_visible(s, values, all_specs) for s in advanced)
    return {"basic": basic, "advanced": advanced, "more_visible": more_visible}
