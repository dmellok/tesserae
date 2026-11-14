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


def _static_choice_values(spec: Mapping[str, Any]) -> set[str] | None:
    """The values a select / multiselect lists in its manifest, or None when
    they come from elsewhere (``choices_from``) or aren't declared."""
    choices = spec.get("choices")
    if spec.get("type") not in ("select", "multiselect") or spec.get("choices_from"):
        return None
    if not isinstance(choices, list) or not choices:
        return None
    out: set[str] = set()
    for c in choices:
        if isinstance(c, Mapping):
            out.add(_as_text(c.get("value", "")))
        elif isinstance(c, list | tuple) and c:
            out.add(_as_text(c[0]))
        else:
            out.add(_as_text(c))
    return out


def carried_value(spec: Mapping[str, Any], value: Any) -> tuple[bool, Any]:
    """Whether ``value``, saved under another widget's option of the same
    name, still means something to this ``spec``, and as what.

    Replacing a widget keeps the options both widgets declare. Same name is
    not same choices: the photo widgets all have ``orientation``, but only
    Google's lists ``splitview``. A choice this widget doesn't list drops
    (the cell gets the default); a multiselect keeps the picks it lists."""
    allowed = _static_choice_values(spec)
    if allowed is None:
        return True, value
    if spec.get("type") == "multiselect":
        if not isinstance(value, list):
            return False, None
        kept = [v for v in value if _as_text(v) in allowed]
        return (bool(kept) or not value), kept
    value = canonical_value(spec, value)
    return _as_text(value) in allowed, value


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


# ----- compact layout (dashboard editor v2) --------------------------------
# A short control is one that reads fine at half the settings column
# (about 145px on a 1440 screen, 160px on a phone): a number, a colour, a
# time, a select whose longest choice is a short word or two, a text field
# whose example is a short phrase. Everything else spans the row.
_SHORT_TYPES = frozenset({"number", "color", "time"})
_SWITCH_TYPES = frozenset({"boolean", "switch"})
SHORT_CHOICE_CHARS = 14
SHORT_TEXT_CHARS = 16
SHORT_LABEL_CHARS = 22
# Two switches share a row in the switch list when both labels fit half of
# it on one line, next to the switch (and the "i" button, when there is
# help): about 60px of label with an "i", 84px without.
PAIR_SWITCH_CHARS = 8
PAIR_SWITCH_CHARS_NO_HELP = 11
# A character count can't tell "Departure time" (fits a half-width select)
# from "Most comments" (doesn't): m and w are three times as wide as i and
# l. So a short field's text must also fit by width, estimated with the
# editor's own font (Archivo, 14px; labels are 13px semibold, 4% narrower).
# The room is the 1440 layout's, the narrower of the two: 99px inside a
# half-width select, 119px inside a text box, 145px of label, 24px of it
# the "i" button when there is help.
_CHAR_PX = {
    ch: px
    for px, chars in (
        (3.0, " 'ijl`"),
        (3.5, "I|"),
        (4.0, "!,./:;[\\]ft"),
        (4.5, "-r"),
        (5.0, '"(){}'),
        (5.5, "*"),
        (7.0, "$_ksvxyz"),
        (7.5, "17Lace"),
        (8.0, "02345689#?Jbdghnopqu"),
        (8.5, "FT"),
        (9.0, "+<=>VYZ^~"),
        (9.5, "&AEKPSX"),
        (10.0, "BCRUw"),
        (10.5, "DHN"),
        (11.0, "GOQ"),
        (12.0, "Mm"),
        (13.0, "W"),
        (13.5, "%"),
        (14.0, "@"),
    )
    for ch in chars
}
SHORT_CHOICE_PX = 98
SHORT_TEXT_PX = 118
SHORT_LABEL_PX = 144
_INFO_BUTTON_PX = 24
_LABEL_SCALE = 0.96


def text_px(text: str) -> float:
    """``text``'s width in the editor's 14px field font, near enough."""
    return sum(_CHAR_PX.get(ch, 8.0) for ch in text)


def _label_fits(spec: Mapping[str, Any]) -> bool:
    label = str(spec.get("label") or spec.get("name") or "")
    room = SHORT_LABEL_PX - (_INFO_BUTTON_PX if spec.get("help") else 0)
    return len(label) <= SHORT_LABEL_CHARS and text_px(label) * _LABEL_SCALE <= room


# How many full-width items a short one may be lifted over to pair with the
# short one before it. More than this and it is too far from where the
# manifest put it.
_MAX_LIFT = 2


def _choice_label(choice: Any) -> str:
    if isinstance(choice, Mapping):
        return str(choice.get("label", choice.get("value", "")))
    if isinstance(choice, list | tuple) and len(choice) > 1:
        return str(choice[1])
    return str(choice)


def option_size(spec: Mapping[str, Any]) -> str:
    """``switch``, ``short`` (half a row) or ``long`` (the whole row)."""
    kind = spec.get("type", "string")
    if kind in _SWITCH_TYPES:
        return "switch"
    if not _label_fits(spec):
        return "long"
    if kind in _SHORT_TYPES:
        presets = spec.get("presets") if kind == "number" else None
        if presets and not all(_choice_fits(p) for p in presets):
            return "long"
        return "short"
    if kind == "string" and not spec.get("mask", spec.get("secret")):
        # A short free-text answer: its example is a word or a phrase
        # ("Home", "pt-BR", "Google Dark Theme"), not a URL or a list of ids.
        example = str(spec.get("placeholder") or spec.get("default") or "")
        if (
            example
            and len(example) <= SHORT_TEXT_CHARS
            and text_px(example) <= SHORT_TEXT_PX
            and "://" not in example
        ):
            return "short"
        return "long"
    if kind == "select" and "choices_note" not in spec:
        choices = spec.get("choices") or []
        if not choices or any(isinstance(c, Mapping) and c.get("image") for c in choices):
            return "long"
        if all(_choice_fits(c) for c in choices):
            return "short"
    return "long"


def _choice_fits(choice: Any) -> bool:
    label = _choice_label(choice)
    return len(label) <= SHORT_CHOICE_CHARS and text_px(label) <= SHORT_CHOICE_PX


def switch_pairs(spec: Mapping[str, Any]) -> bool:
    """Whether a switch's label is short enough to share a row."""
    label = str(spec.get("label") or spec.get("name") or "")
    limit = PAIR_SWITCH_CHARS if spec.get("help") else PAIR_SWITCH_CHARS_NO_HELP
    return len(label) <= limit


def option_layout(
    section: Iterable[Mapping[str, Any]],
    values: Mapping[str, Any] | None,
    specs: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One section's options as the editor lays them out.

    Every switch in the section goes into one list, where the first switch
    was; two switches with short labels share a row of it. The other fields
    keep manifest order on a two-column grid, short ones side by side. A
    short item left alone before a full-width one is joined by the next
    short one, lifted over at most two full-width items and never over one
    it depends on. Returns blocks: ``{"kind": "switches", "rows": [[spec],
    [spec, spec], ...]}`` and ``{"kind": "field", "size": "short" | "long",
    "opt": spec}``. Visibility is the first render's; hidden fields keep
    their place and the grid closes up around them."""
    all_specs = [s for s in specs if isinstance(s, Mapping)]
    opts = [s for s in section if isinstance(s, Mapping)]

    def visible(spec: Mapping[str, Any]) -> bool:
        return option_visible(spec, values, all_specs)

    switches = [s for s in opts if option_size(s) == "switch"]
    items: list[dict[str, Any]] = []
    for spec in opts:
        size = option_size(spec)
        if size == "switch":
            if spec is switches[0]:
                items.append({"kind": "switches", "opts": switches, "size": "long"})
            continue
        items.append({"kind": "field", "size": size, "opt": spec})

    def shown(item: dict[str, Any]) -> bool:
        if item["kind"] == "switches":
            return any(visible(s) for s in item["opts"])
        return visible(item["opt"])

    out = _arrange(items, shown)
    for block in out:
        if block["kind"] == "switches":
            block["rows"] = _switch_rows(block["opts"], visible)
    return out


def _item_specs(item: dict[str, Any]) -> list[Mapping[str, Any]]:
    return list(item["opts"]) if item["kind"] == "switches" else [item["opt"]]


def _arrange(items: list[dict[str, Any]], shown: Any) -> list[dict[str, Any]]:
    """``items`` in order, a lone short one joined by the next short one."""
    out: list[dict[str, Any]] = []
    half = False  # a short item sits alone in the current row
    pending = list(items)
    while pending:
        item = pending.pop(0)
        if not shown(item):
            out.append(item)
            continue
        if item["size"] == "short":
            out.append(item)
            half = not half
            continue
        if half:
            lifted = _lift_short(item, pending, shown)
            if lifted is not None:
                out.append(lifted)
                half = False
                pending.insert(0, item)
                continue
        out.append(item)
        half = False
    return out


def _lift_short(
    first: dict[str, Any], pending: list[dict[str, Any]], shown: Any
) -> dict[str, Any] | None:
    """Take the next shown short item out of ``pending`` if it may move up
    over ``first`` and the full-width items after it."""
    skipped = [first]
    for i, item in enumerate(pending):
        if not shown(item):
            skipped.append(item)
            continue
        if item["size"] != "short":
            if sum(1 for s in skipped if shown(s)) >= _MAX_LIFT:
                return None
            skipped.append(item)
            continue
        cond = item["opt"].get("show_if")
        needs = set(cond) if isinstance(cond, Mapping) else set()
        names = {str(spec.get("name")) for s in skipped for spec in _item_specs(s)}
        if needs & names:
            return None
        return pending.pop(i)
    return None


def _switch_rows(switches: list[Mapping[str, Any]], visible: Any) -> list[list[Mapping[str, Any]]]:
    """The switch list's rows: one switch each, or two short-labelled ones."""
    items = [
        {"kind": "field", "size": "short" if switch_pairs(s) else "long", "opt": s}
        for s in switches
    ]
    rows: list[list[Mapping[str, Any]]] = []
    open_pair: list[Mapping[str, Any]] | None = None
    for item in _arrange(items, lambda it: visible(it["opt"])):
        spec = item["opt"]
        if not visible(spec):
            rows.append([spec])
            continue
        if item["size"] == "short" and open_pair is not None:
            open_pair.append(spec)
            open_pair = None
            continue
        rows.append([spec])
        open_pair = rows[-1] if item["size"] == "short" else None
    return rows
