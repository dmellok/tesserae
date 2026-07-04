"""Read-only palette presets shipped in the source tree.

Six Spectra 6 profiles + one seven-colour Inky ACeP profile + one
nominal (uncalibrated identity) fallback. Every measured profile is
attributed to its upstream source; the palette hex values for the
paperlesspaper-attributed profiles are the Apache-2.0 palette data
from `paperlesspaper/epdoptimize
<https://github.com/paperlesspaper/epdoptimize/blob/main/src/dither/data/default-palettes.json>`_.

Contributors ordered so the picker's default is Tesserae's tuned
profile (currently equivalent to paperlesspaper's ``spectra6``), then
the community-measured alternatives in rough popularity order, then
Nominal at the bottom as the "start over" option. The order the
picker renders in comes from :func:`list_bundled`.

Attribution surfaces:

* JSON exports carry ``based_on`` + ``attribution`` fields so shared
  profiles keep their provenance.
* The Calibration tab renders an "attribution" chip on each preset
  card linking to the upstream repo.
* :file:`NOTICES.md` at the repo root spells out the license terms.
"""

from __future__ import annotations

from app.palette_profiles.schema import (
    DitherSettings,
    EdgeSettings,
    PaletteColors,
    PaletteProfile,
    ToneSettings,
)

_EPDOPTIMIZE_URL = "https://github.com/paperlesspaper/epdoptimize"


def _profile(
    slug: str,
    name: str,
    family: str,
    palette: PaletteColors,
    *,
    based_on: str | None = None,
    attribution: str | None = None,
    notes: str = "",
) -> PaletteProfile:
    """Bundled-profile constructor with the defaults every preset uses:
    tone / dither / edges at their neutral values. Fine-tuning is what
    user profiles are for."""
    return PaletteProfile(
        slug=slug,
        name=name,
        family=family,
        palette=palette,
        tone=ToneSettings(),
        dither=DitherSettings(),
        edges=EdgeSettings(),
        bundled=True,
        based_on=based_on,
        attribution=attribution,
        notes=notes,
    )


BUNDLED_PROFILES: tuple[PaletteProfile, ...] = (
    _profile(
        slug="paperlesspaper-spectra6",
        name="Paperlesspaper (default)",
        family="spectra6",
        palette=PaletteColors(
            black="#1F2226",
            white="#B9C7C9",
            yellow="#C1BB1E",
            red="#62201E",
            blue="#233F8E",
            green="#35563A",
        ),
        based_on="paperlesspaper/epdoptimize · spectra6",
        attribution=_EPDOPTIMIZE_URL,
        notes=(
            "Paperlesspaper's measured Spectra 6 palette (their production picture-frame "
            "profile). The Tesserae default; matches the calibrated palette shipped in "
            "app.quantizer since v0.61."
        ),
    ),
    _profile(
        slug="paperlesspaper-legacy-spectra6",
        name="Paperlesspaper (legacy)",
        family="spectra6",
        palette=PaletteColors(
            black="#191E21",
            white="#E8E8E8",
            yellow="#EFDE44",
            red="#B21318",
            blue="#2157BA",
            green="#125F20",
        ),
        based_on="paperlesspaper/epdoptimize · spectra6legacy",
        attribution=_EPDOPTIMIZE_URL,
        notes=(
            "Paperlesspaper's earlier Spectra 6 measurements; brighter whites and more "
            "saturated reds. Some panels read closer to this than the current default."
        ),
    ),
    _profile(
        slug="boeber-spectra6",
        name="Boeber",
        family="spectra6",
        palette=PaletteColors(
            black="#1F2226",
            white="#D6D6D6",
            yellow="#DBD529",
            red="#EA4843",
            blue="#416CE1",
            green="#067406",
        ),
        based_on="paperlesspaper/epdoptimize · spectra6-boeber",
        attribution=_EPDOPTIMIZE_URL,
        notes=(
            "Boeber's Spectra 6 measurements, contributed via epdoptimize. Punchier "
            "reds and blues; try this one first if the default looks washed out."
        ),
    ),
    _profile(
        slug="aitjcize-spectra6",
        name="aitjcize",
        family="spectra6",
        palette=PaletteColors(
            black="#020202",
            white="#BEC8C8",
            yellow="#CDCA00",
            red="#871300",
            blue="#05409E",
            green="#27663C",
        ),
        based_on="paperlesspaper/epdoptimize · aitjcize-spectra6",
        attribution=_EPDOPTIMIZE_URL,
        notes=(
            "aitjcize's Spectra 6 profile, originally derived for the epaper-image-"
            "convert toolchain. Deeper blacks and darker reds."
        ),
    ),
    _profile(
        slug="nominal-spectra6",
        name="Nominal (uncalibrated)",
        family="spectra6",
        palette=PaletteColors(
            black="#000000",
            white="#FFFFFF",
            yellow="#FFFF00",
            red="#FF0000",
            blue="#0000FF",
            green="#00FF00",
        ),
        notes=(
            "Ideal-primary sRGB values. Skips tone mapping; use this when you want the "
            "renderer's dither to snap raw source pixels straight to the nominal palette."
        ),
    ),
    _profile(
        slug="paperlesspaper-inky7",
        name="Paperlesspaper Inky 7-colour",
        family="inky_7colour",
        palette=PaletteColors(
            black="#191E21",
            white="#F1F1F1",
            green="#53A428",
            blue="#31318F",
            red="#D20E13",
            yellow="#F3CF11",
            orange="#B85E1C",
        ),
        based_on="paperlesspaper/epdoptimize · acep",
        attribution=_EPDOPTIMIZE_URL,
        notes=(
            "Measured 7-colour Inky Impression / UC8159 (ACeP) palette. Adds a brick "
            "orange to the six Spectra 6 primaries. Tesserae's Inky default since v0.61."
        ),
    ),
    _profile(
        slug="nominal-inky7",
        name="Nominal Inky 7-colour (uncalibrated)",
        family="inky_7colour",
        palette=PaletteColors(
            black="#000000",
            white="#FFFFFF",
            green="#00FF00",
            blue="#0000FF",
            red="#FF0000",
            yellow="#FFFF00",
            orange="#FF8C00",
        ),
        notes="Ideal-primary sRGB values for the 7-colour ACeP palette.",
    ),
)


_BY_SLUG: dict[str, PaletteProfile] = {p.slug: p for p in BUNDLED_PROFILES}


def bundled_profile(slug: str) -> PaletteProfile | None:
    """Look up a bundled profile by slug, or ``None`` when unknown."""
    return _BY_SLUG.get(slug)


def default_slug_for(family: str) -> str:
    """Which bundled profile the Calibration picker preselects for
    each panel gamut. Falls back to the first bundled profile in the
    requested family so an unknown gamut still resolves to something."""
    if family == "inky_7colour":
        return "paperlesspaper-inky7"
    return "paperlesspaper-spectra6"


def list_bundled(family: str | None = None) -> list[PaletteProfile]:
    """The picker's ordered list. When ``family`` is provided the list
    filters to that panel gamut so a Spectra 6 device doesn't see the
    Inky 7-colour presets and vice versa."""
    if family is None:
        return list(BUNDLED_PROFILES)
    return [p for p in BUNDLED_PROFILES if p.family == family]
