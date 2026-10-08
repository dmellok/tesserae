"""Server name and colour (#350): marks which server a page belongs to.

Someone running more than one Tesserae (dev, test, prod) sets a short name
and a colour under Settings › Server › This server. The colour paints a 4px
stripe across the top of every page and becomes the accent; the name shows
as a chip under the wordmark and leads the browser tab title. The server
mark's top-right square, in the nav and the tab icon, takes the colour too. Both are install-wide settings in the ``app`` section,
so every browser and phone sees the same thing.

Nothing changes until one is set: with no name and no colour the admin looks
exactly as it always has.

The presets are the Spectra 6 inks, muted to sit beside Paper's red, with a
lighter step for dark mode. A custom colour is any ``#rrggbb`` and is used
as given in both modes.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

# id -> (label, light, dark). Light values are Paper's panel pigments.
PRESETS: dict[str, tuple[str, str, str]] = {
    "red": ("Red", "#B8352A", "#D0493C"),
    "yellow": ("Yellow", "#C9971C", "#D9B45A"),
    "green": ("Green", "#3D7A3A", "#8DBF7F"),
    "blue": ("Blue", "#2B4E9B", "#8FA8E0"),
    "black": ("Black", "#1C1B19", "#EDE9DF"),
}

NAME_MAX = 24
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def normalise_hex(raw: str) -> str | None:
    """``#abc`` / ``abc`` / ``#AABBCC`` -> ``#aabbcc``; anything else None."""
    m = _HEX_RE.match(raw.strip())
    if not m:
        return None
    digits = m.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return "#" + digits.lower()


def normalise_colour(raw: Any) -> str:
    """The stored form of a colour choice: a preset id, a ``#rrggbb``, or
    ``""`` for none. Unrecognised input clears the choice."""
    if not isinstance(raw, str):
        return ""
    value = raw.strip().lower()
    if value in PRESETS:
        return value
    return normalise_hex(value) or ""


def normalise_name(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return " ".join(raw.split())[:NAME_MAX]


def _luminance(hex_colour: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def text_on(hex_colour: str) -> str:
    """Paper ink or Paper white, whichever reads better on ``hex_colour``."""
    lum = _luminance(hex_colour)
    # Contrast against white (1.0) vs against ink #1C1B19 (~0.011).
    on_white = 1.05 / (lum + 0.05)
    on_ink = (lum + 0.05) / 0.061
    return "#FFFDF8" if on_white >= on_ink else "#1C1B19"


def favicon_data_uri(hex_colour: str) -> str:
    """The server mark as an SVG data URI with ``hex_colour`` in its top-right
    square.

    The tile stays ink and the bottom-left square paper, so a tinted tab
    still reads as a self-hosted server; only the red square takes the
    server colour. As in static/brand/favicon.svg, the tile never follows
    the system theme and a dark tab strip only adds the 1 px light
    hairline. Pass the colour's on-ink (dark) step."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        "<style>.hd{display:none}@media (prefers-color-scheme: dark){.hd{display:inline}}</style>"
        '<defs><clipPath id="t"><rect width="256" height="256" rx="72"/></clipPath></defs>'
        '<rect width="256" height="256" rx="72" fill="#1C1B19"/>'
        '<rect class="hd" width="256" height="256" rx="72" fill="none" stroke="#EDE9DF"'
        ' stroke-opacity="0.3" stroke-width="2" vector-effect="non-scaling-stroke"'
        ' clip-path="url(#t)"/>'
        f'<path d="M128 55H174A27 27 0 0 1 201 82V128H128Z" fill="{hex_colour}"/>'
        '<path d="M55 128H128V201H82A27 27 0 0 1 55 174Z" fill="#FFFDF8"/></svg>'
    )
    return "data:image/svg+xml," + quote(svg, safe="")


def resolve(app_settings: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the templates need, or None when neither name nor colour is set.

    ``light`` / ``dark`` are the colour per theme with the text colour that
    sits on it; ``colour`` is None when only a name is set (the chip then
    falls back to the normal accent and there is no stripe)."""
    section = app_settings or {}
    name = normalise_name(section.get("instance_name"))
    choice = normalise_colour(section.get("server_colour"))
    if not name and not choice:
        return None
    colour: dict[str, Any] | None = None
    if choice:
        if choice in PRESETS:
            _, light, dark = PRESETS[choice]
        else:
            light = dark = choice
        colour = {
            "id": choice,
            "light": light,
            "light_fg": text_on(light),
            "dark": dark,
            "dark_fg": text_on(dark),
            "favicon": favicon_data_uri(dark),
        }
    return {"name": name, "colour": colour}


def presets_for_picker() -> list[dict[str, str]]:
    """The presets in picker order, for the settings swatches."""
    return [
        {"id": pid, "label": label, "light": light, "dark": dark}
        for pid, (label, light, dark) in PRESETS.items()
    ]
