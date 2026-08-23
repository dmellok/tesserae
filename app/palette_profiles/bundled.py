"""Read-only palette presets shipped in the source tree.

Grouped by panel family: Spectra 6, seven-colour Inky ACeP, and 4-colour
BWRY, each with at least one measured profile plus a nominal
(uncalibrated identity) fallback. Every profile derived from someone
else's work is attributed to its upstream source; the palette hex values
for the paperlesspaper-attributed profiles are the Apache-2.0 palette
data from `paperlesspaper/epdoptimize
<https://github.com/paperlesspaper/epdoptimize/blob/main/src/dither/data/default-palettes.json>`_.
The BWRY values were calibrated against a physical PicPak 4.2" panel by
`varanu5 <https://github.com/varanu5>`_.

Contributors ordered so the picker's default is Tesserae's tuned
profile (currently equivalent to paperlesspaper's ``spectra6``), then
the community-measured alternatives in rough popularity order, then
Nominal at the bottom as the "start over" option. The order the
picker renders in comes from :func:`list_bundled`.

**BWRY deliberately inverts that**: its nominal profile is listed first
and is the family default, so unlocking the Calibration tab for PicPak
panels does not silently restyle frames that already look right. The
measured presets are one click away. If BWRY should follow the same
convention as the other families, reorder here and change
:func:`default_slug_for`.

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
    GrayRamp,
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
    dither: DitherSettings | None = None,
    gray: GrayRamp | None = None,
) -> PaletteProfile:
    """Bundled-profile constructor with the defaults every preset uses:
    tone / dither / edges at their neutral values. Fine-tuning is what
    user profiles are for.

    ``dither`` overrides those neutral defaults. The BWRY presets use it
    to pin ``serpentine=False``, matching what the renderer does when no
    profile is applied -- ``DitherSettings`` defaults it to True, so
    without the pin merely having a profile applied would change output.
    Omitting the argument keeps every pre-existing preset unchanged."""
    return PaletteProfile(
        slug=slug,
        name=name,
        family=family,
        palette=palette,
        gray=gray if gray is not None else GrayRamp(),
        tone=ToneSettings(),
        dither=dither if dither is not None else DitherSettings(),
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
    # --- bwry_4 (PicPak 4.2" black/white/red/yellow) ---------------------
    # Only the first four slots are used: the quantizer slices a profile
    # palette to the gamut's length, and BWRY's canonical order (black,
    # white, yellow, red) is exactly the first four of the shared slot
    # order, so no remapping is needed. ``blue`` / ``green`` keep their
    # dataclass defaults and are sliced off before dithering.
    #
    # The nominal profile is FIRST and is the family default, so a PicPak
    # keeps rendering against the ideal primaries unless the user opts
    # into a measured one. Its dither block pins the renderer's
    # no-profile defaults (serpentine off, RGB matching) rather than
    # ``DitherSettings``' own defaults, which differ -- otherwise merely
    # having a profile applied would change output.
    _profile(
        slug="nominal-bwry",
        name="Nominal BWRY",
        family="bwry_4",
        palette=PaletteColors(
            black="#000000",
            white="#FFFFFF",
            yellow="#FFFF00",
            red="#FF0000",
        ),
        dither=DitherSettings(serpentine=False, color_match="rgb"),
        notes=(
            "Standard colours, what Tesserae has always used for this panel. "
            "Kept as the default so existing screens don't suddenly change "
            "appearance. Try PicPak Calibrated if photos look muddy."
        ),
    ),
    _profile(
        slug="picpak-bwry-calibrated",
        name="PicPak Calibrated",
        family="bwry_4",
        palette=PaletteColors(
            black="#242522",
            white="#ECE9DF",
            yellow="#DEB428",
            red="#BC4248",
        ),
        # Same dither block as the nominal profile: switching to this
        # preset must change the palette and nothing else, so the A/B
        # isolates one variable.
        dither=DitherSettings(serpentine=False, color_match="rgb"),
        # ``based_on`` only, no ``attribution``: the credit line is enough and
        # the card renders it without a link. ``attribution`` exists to point
        # at an upstream project the data was ported from, which does not
        # apply here.
        based_on='varanu5 · PicPak 4.2" BWRY panel calibration',
        notes=(
            "The colours this panel actually prints, measured from a real PicPak "
            '4.2" by varanu5. The default profile aims for perfect ink the screen '
            "can't make (its yellow is closer to mustard than lemon), which can "
            "leave photos looking muddy. Panels vary a little, so copy this "
            "profile and adjust the colours if yours looks off."
        ),
    ),
    # ---- grayscale ramps ------------------------------------------
    #
    # A grey profile carries no ``palette``: the wire format for these
    # panels holds levels, not colours, so only ``gray`` is read. The
    # value you enter is what the panel ACTUALLY PAINTS, and the renderer
    # compensates. That inverts the intuition: a panel rendering too
    # light is fixed by declaring its mid-levels LIGHTER than nominal,
    # because that tells the quantizer those levels overshoot and it
    # should reach for a darker one.
    #
    # Nominal is first and is the family default, so unlocking the tab
    # can't change how an existing panel renders.
    _profile(
        slug="nominal-gray4",
        name="Nominal 4-level grey",
        family="gray_4",
        palette=PaletteColors(),
        gray=GrayRamp(levels=("#000000", "#555555", "#AAAAAA", "#FFFFFF")),
        notes=(
            "Evenly-spaced levels, what Tesserae has always assumed for these panels. "
            "The default, so existing screens don't change. Try the softer ramp if "
            "your panel looks washed out."
        ),
    ),
    _profile(
        slug="soft-mid-gray4",
        name="4-level grey, lifted mid-tones",
        family="gray_4",
        palette=PaletteColors(),
        gray=GrayRamp(levels=("#1E1E1E", "#6E6E6E", "#B4B4B4", "#E8E8E8")),
        notes=(
            "A starting point for panels that render washed out, NOT a measurement of "
            "any particular unit. It says the two middle levels come out lighter than "
            "evenly-spaced, and that the panel's black isn't truly black nor its white "
            "truly white, which is normal for e-paper. The renderer answers by "
            "reaching for darker levels, so the image gets darker overall. Copy this "
            "profile and adjust it against your own panel rather than trusting these "
            "numbers."
        ),
    ),
    _profile(
        slug="nominal-gray16",
        name="Nominal 16-level grey",
        family="gray_16",
        palette=PaletteColors(),
        gray=GrayRamp(levels=("#000000", "#FFFFFF")),
        notes=(
            "Evenly-spaced levels, what Tesserae has always assumed for these panels. "
            "Two anchors interpolated across all 16 steps, which is exactly the linear "
            "ramp. The default, so existing screens don't change."
        ),
    ),
    _profile(
        slug="soft-mid-gray16",
        name="16-level grey, lifted mid-tones",
        family="gray_16",
        palette=PaletteColors(),
        gray=GrayRamp(levels=("#1E1E1E", "#6E6E6E", "#B4B4B4", "#E8E8E8")),
        notes=(
            "The same four anchors as the 4-level ramp, interpolated across 16 steps. "
            "A starting point for a panel that renders washed out, NOT a measurement. "
            "Four patches are far easier to judge by eye than sixteen, which is why "
            "the ramp interpolates rather than asking for every level."
        ),
    ),
    # Known characteristic of the measured palette, recorded here rather
    # than worked around: the real red ink is desaturated enough to sit
    # geometrically next to neutral mid-grey, so grey levels ~80-160 snap
    # to red under every matching mode. Antialiased text edges live in
    # that band, so white-on-black and black-on-white text picks up a
    # small amount of red speckle (~0.4% of pixels on a text-heavy
    # frame). A preset with a de-neutralised red (#D4242A) removed it
    # completely but was dropped as unwanted; Nominal remains the clean
    # choice for text-heavy dashboards.
    #
    # NO LAB / chroma-aware preset here, deliberately. ``color_match="lab"``
    # silently disables dithering. ``_error_diffusion`` builds buf_l / buf_a
    # / buf_b_lab once from the ORIGINAL image and never writes diffused
    # error back into them (only buf_r/g/b get the error), yet the LAB
    # branch makes its nearest-palette decision from those stale buffers.
    # Every pixel is therefore judged against its pristine value: plain
    # nearest-colour quantisation, posterised, no dither texture.
    #
    # Pre-existing and NOT BWRY-specific. ``_error_diffusion`` never sees a
    # gamut, and atkinson / jarvis / stucki all call it (floyd-steinberg
    # detours into it too when LAB is selected), so this hits every
    # gamut x every error-diffusion dither x both lab and chroma-aware --
    # measured as 12/12 combinations collapsing a flat patch to one ink
    # where RGB uses 4 to 7. Ship no preset that steers users onto it.
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
    if family == "bwry_4":
        # Nominal, not measured: unlocking the Calibration tab must not
        # silently change how existing PicPak panels render. Measured is
        # one click away in the picker.
        return "nominal-bwry"
    # Same rule for the grey ramps: the evenly-spaced one is what these
    # panels have always rendered against, so it stays the default and
    # the corrective ramp is a deliberate choice.
    if family == "gray_4":
        return "nominal-gray4"
    if family == "gray_16":
        return "nominal-gray16"
    return "paperlesspaper-spectra6"


def list_bundled(family: str | None = None) -> list[PaletteProfile]:
    """The picker's ordered list. When ``family`` is provided the list
    filters to that panel gamut so a Spectra 6 device doesn't see the
    Inky 7-colour presets and vice versa."""
    if family is None:
        return list(BUNDLED_PROFILES)
    return [p for p in BUNDLED_PROFILES if p.family == family]
