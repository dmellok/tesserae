"""The colours a panel can show that are not one of its inks.

A six-ink panel gets treated as a six-colour palette, and an agent handed
``["#000000", "#FFFFFF", "#FFFF00", "#FF0000", "#0000FF", "#00FF00"]`` designs with six flat
primaries, which is both garish and a fraction of what the hardware does. The renderer dithers:
a fill of any colour becomes a mix of the inks nearest it, and from reading distance a
checkerboard of red and yellow is orange. So the panel's real vocabulary is the inks plus
everything that mixes cleanly from two of them.

Telling an agent to "design in full colour" says this, and has been in the instructions all
along. It does not work, because prose is an abstraction and the palette list is concrete: given
six named hexes and a paragraph, an agent reaches for the hexes. Naming the mixes is what makes
them reachable, so each blend here is one pair of inks at one ratio, with its hex computed from
the panel's own palette rather than written down. A calibrated panel therefore reports what it
will really print.

The catch is scale, and it is the reason each blend carries a ``safe_on``. A dithered colour is
a texture: over a large area it reads as the blend, but a thin rule or a line of small type made
of it breaks into speckle, because there are not enough pixels across the stroke to average out.
Type, hairlines and icons want a pure ink. Panels, headers, chart bands and backgrounds can have
any of these.
"""

from __future__ import annotations

from typing import Any

# Which ink each nominal palette entry is, so a recipe can name its two by colour rather than by
# index (the gamuts order their inks differently).
_INK_OF: dict[tuple[int, int, int], str] = {
    (0, 0, 0): "black",
    (255, 255, 255): "white",
    (255, 255, 0): "yellow",
    (255, 0, 0): "red",
    (0, 0, 255): "blue",
    (0, 255, 0): "green",
    (255, 140, 0): "orange",
}

# The mixes worth naming, in the order a designer would reach for them. A pair the panel does
# not have is skipped, so a BWRY panel simply reports fewer of them.
_RECIPES: tuple[tuple[str, str, str, float], ...] = (
    ("orange", "red", "yellow", 0.5),
    ("amber", "yellow", "red", 0.75),
    ("purple", "red", "blue", 0.5),
    ("magenta", "red", "blue", 0.75),
    ("teal", "blue", "green", 0.5),
    ("lime", "yellow", "green", 0.5),
    ("pink", "red", "white", 0.5),
    ("blush", "red", "white", 0.25),
    ("cream", "yellow", "white", 0.4),
    ("sky", "blue", "white", 0.45),
    ("mint", "green", "white", 0.45),
    ("maroon", "red", "black", 0.55),
    ("olive", "yellow", "black", 0.55),
    ("navy", "blue", "black", 0.55),
    ("forest", "green", "black", 0.55),
    ("charcoal", "black", "white", 0.75),
    ("grey", "black", "white", 0.5),
    ("silver", "black", "white", 0.25),
)

# A mix this close to an ink is that ink; offering it as a separate colour only misleads.
_SAME = 24


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(round(v) for v in rgb))


def blends_for(palette: tuple[tuple[int, int, int], ...], grayscale: bool) -> list[dict[str, Any]]:
    """The blends this panel can show, brightest mixes first.

    Empty for a greyscale panel, whose levels are already its whole vocabulary, and for one-bit
    panels, where every mix is just the grey texture the dither already produces on its own.
    """
    if grayscale or len(palette) <= 2:
        return []
    inks = {_INK_OF[c]: c for c in palette if c in _INK_OF}
    out: list[dict[str, Any]] = []
    for name, a_name, b_name, mix in _RECIPES:
        a, b = inks.get(a_name), inks.get(b_name)
        if a is None or b is None:
            continue
        mixed = tuple(a[i] * mix + b[i] * (1.0 - mix) for i in range(3))
        if any(sum(abs(c[i] - mixed[i]) for i in range(3)) < _SAME for c in inks.values()):
            continue
        out.append(
            {
                "name": name,
                "hex": _hex(mixed),  # type: ignore[arg-type]
                "from": [a_name, b_name],
                "mix": mix,
                "safe_on": "large areas",
            }
        )
    return out
