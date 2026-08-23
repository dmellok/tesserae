"""Palette profile dataclasses + JSON (de)serialisation.

The profile schema is a superset of what epdoptimize expects, so
importing an epdoptimize palette JSON works after a straight structural
map. Unknown fields on load are dropped rather than rejected so
older / newer profiles remain forward-compatible; missing fields fall
back to sensible defaults.

Tone knob ranges:

* ``exposure`` : -100..+100 (0 = no change; scales sRGB linear midpoint)
* ``contrast`` : 0..3       (1.0 = no change; matches the existing
                             per-clone renderer field so migration is
                             storage-preserving)
* ``saturation`` : 0..3     (as contrast)
* ``s_curve`` : -100..+100  (mid-tone punch; positive = more contrast
                             at the middle of the range)
* ``lab_compress_min`` : 0..100  (LAB lightness floor as a percent)
* ``lab_compress_max`` : 0..100  (LAB lightness ceiling as a percent)

Dither / edge knobs land in phase 2 (they're stored but not yet wired
to the quantizer). Ship-early lets users see + save their preferred
setup, and the quantizer catches up in a subsequent release.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PaletteColors:
    """Six-colour Spectra 6 / Waveshare E6 slot; ``orange`` filled only
    on the seven-colour Inky ACeP profile. Values are ``#rrggbb`` sRGB
    strings so the JSON round-trips through browser colour pickers
    without a numeric coercion step."""

    black: str = "#000000"
    white: str = "#ffffff"
    yellow: str = "#ffff00"
    red: str = "#ff0000"
    blue: str = "#0000ff"
    green: str = "#00ff00"
    orange: str | None = None

    def as_tuples(self) -> tuple[tuple[int, int, int], ...]:
        """Return the palette as ``((r, g, b), ...)`` tuples in the
        canonical order the quantizer wants (black, white, yellow, red,
        blue, green, optional orange). Bad hex values fall through to
        (0, 0, 0) so a broken profile can't crash the render."""
        out: list[tuple[int, int, int]] = []
        for hex_value in (self.black, self.white, self.yellow, self.red, self.blue, self.green):
            out.append(_hex_to_rgb(hex_value))
        if self.orange:
            out.append(_hex_to_rgb(self.orange))
        return tuple(out)


@dataclass(frozen=True)
class GrayRamp:
    """What a grayscale panel's levels actually paint, darkest first.

    The grey packers assumed a perfectly linear ramp (``i * 85`` for
    4-level, ``i * 17`` for 16-level). Real e-paper grey waveforms are
    not linear in reflectance, so a panel whose mid-levels come out
    lighter than that renders washed out, and error diffusion makes it
    worse by diffusing against values the panel never produces.

    ``levels`` holds ``#rrggbb`` anchors in **ascending** order, darkest
    first, because the wire format fixes index 0 as black and the top
    index as white. Order is meaning here, not presentation: entry *i*
    is what level *i* paints.

    Any anchor count of two or more is allowed and
    :meth:`as_tuples` interpolates to whatever a given gamut needs, so
    four measured patches can drive a 16-level panel. Empty (the
    default) means no override and the caller keeps its linear ramp.
    """

    levels: tuple[str, ...] = ()

    def as_tuples(self, count: int) -> tuple[tuple[int, int, int], ...] | None:
        """Resolve to exactly ``count`` RGB triplets, or ``None`` when
        there is nothing usable to override with.

        Fewer than two anchors can't describe a ramp, so that returns
        ``None`` rather than a guess. An exact-length ramp is used as
        given; otherwise anchors are spread evenly across the output and
        linearly interpolated between."""
        if count <= 0:
            return None
        anchors = [_hex_to_rgb(value) for value in self.levels]
        if len(anchors) < 2:
            return None
        if len(anchors) == count:
            return tuple(anchors)
        if count == 1:
            return (anchors[0],)
        last = len(anchors) - 1
        out: list[tuple[int, int, int]] = []
        for i in range(count):
            pos = i * last / (count - 1)
            lo = min(int(pos), last)
            hi = min(lo + 1, last)
            frac = pos - lo
            a, b = anchors[lo], anchors[hi]
            out.append(
                (
                    round(a[0] + (b[0] - a[0]) * frac),
                    round(a[1] + (b[1] - a[1]) * frac),
                    round(a[2] + (b[2] - a[2]) * frac),
                )
            )
        return tuple(out)


@dataclass(frozen=True)
class ToneSettings:
    """Tone knobs applied before quantisation. ``contrast`` and
    ``saturation`` mirror the existing per-clone renderer fields so the
    same value means the same thing on both sides of the UI move."""

    exposure: int = 0
    contrast: float = 1.0
    saturation: float = 1.0
    s_curve: int = 0
    lab_compress_min: int = 0
    lab_compress_max: int = 100


@dataclass(frozen=True)
class DitherSettings:
    """Dither algorithm + variants. ``algorithm`` mirrors the existing
    per-clone renderer field so the same value means the same thing.
    The remaining fields are new and take effect in phase 2."""

    algorithm: str = "floyd-steinberg"
    serpentine: bool = True
    color_match: str = "rgb"
    diffusion_strength: int = 100


# Upper bound for ``protect_native_colours``, as Euclidean distance in
# sRGB. Past this a "close enough to white" test starts catching the pale
# end of photographs, which is a flattened sky rather than a clean
# background, so the form can't offer a value that reads as a bug.
MAX_NATIVE_TOLERANCE = 48


@dataclass(frozen=True)
class EdgeSettings:
    """Edge-handling knobs (experimental, phase 3). Stored on every
    profile so the schema stays stable but the renderer ignores them
    until the phase 3 wiring lands."""

    preserve_line_art: bool = False
    smoothing_radius: int = 0
    # Tolerance for the native-colour guard (discussion #227). 0 is off.
    # Pixels already within this distance of one of the panel's own
    # colours keep that colour instead of collecting their neighbours'
    # diffused error, which is what speckles flat backgrounds.
    protect_native_colours: int = 0


@dataclass(frozen=True)
class PaletteProfile:
    """A complete palette + tone + dither + edges bundle.

    ``slug`` is the on-disk filename (without ``.json``) and the URL
    fragment; ``name`` is the human-facing label. ``family`` is one
    of ``spectra6`` / ``inky_7colour`` / ``mono`` / ``custom`` and
    scopes which panel gamuts a profile is offered against. ``bundled``
    is True on the read-only presets shipped in the source tree;
    ``based_on`` and ``attribution`` describe the upstream source when
    the profile was cloned from a bundled preset."""

    slug: str
    name: str
    family: str
    palette: PaletteColors = field(default_factory=PaletteColors)
    # Grayscale panels ignore ``palette`` entirely: their wire format
    # carries levels, not colours. Empty on every colour profile.
    gray: GrayRamp = field(default_factory=GrayRamp)
    tone: ToneSettings = field(default_factory=ToneSettings)
    dither: DitherSettings = field(default_factory=DitherSettings)
    edges: EdgeSettings = field(default_factory=EdgeSettings)
    bundled: bool = False
    based_on: str | None = None
    attribution: str | None = None
    notes: str = ""
    saved_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Round-tripable JSON dict. Drops ``orange`` when unset so the
        Spectra 6 profiles serialise cleanly and Inky ACeP profiles
        carry the extra colour naturally."""
        payload = asdict(self)
        if payload["palette"].get("orange") is None:
            payload["palette"].pop("orange", None)
        # Colour profiles carry no ramp; an empty key on every one of
        # them is noise in the file and in a diff.
        if not payload.get("gray", {}).get("levels"):
            payload.pop("gray", None)
        return payload


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Parse ``#rrggbb`` / ``#rgb`` / ``rrggbb`` into an ``(r, g, b)``
    tuple. Bad input returns black so a corrupt profile degrades to a
    dark render instead of crashing."""
    if not isinstance(value, str):
        return (0, 0, 0)
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return (0, 0, 0)
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return (0, 0, 0)


def _colors_from_dict(raw: dict[str, Any]) -> PaletteColors:
    return PaletteColors(
        black=str(raw.get("black", "#000000")),
        white=str(raw.get("white", "#ffffff")),
        yellow=str(raw.get("yellow", "#ffff00")),
        red=str(raw.get("red", "#ff0000")),
        blue=str(raw.get("blue", "#0000ff")),
        green=str(raw.get("green", "#00ff00")),
        orange=(str(raw["orange"]) if raw.get("orange") else None),
    )


def _gray_from_dict(raw: dict[str, Any]) -> GrayRamp:
    """Hydrate a grey ramp. A non-list, or anything under two usable
    anchors, degrades to empty (no override) rather than a partial ramp
    the packer would have to second-guess."""
    levels = raw.get("levels")
    if not isinstance(levels, (list, tuple)):
        return GrayRamp()
    parsed = tuple(str(value) for value in levels if isinstance(value, str) and value.strip())
    if len(parsed) < 2:
        return GrayRamp()
    return GrayRamp(levels=parsed)


def _tone_from_dict(raw: dict[str, Any]) -> ToneSettings:
    def _as_int(key: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(raw.get(key, default))))
        except (TypeError, ValueError):
            return default

    def _as_float(key: str, default: float, lo: float, hi: float) -> float:
        try:
            return max(lo, min(hi, float(raw.get(key, default))))
        except (TypeError, ValueError):
            return default

    return ToneSettings(
        exposure=_as_int("exposure", 0, -100, 100),
        contrast=_as_float("contrast", 1.0, 0.0, 3.0),
        saturation=_as_float("saturation", 1.0, 0.0, 3.0),
        s_curve=_as_int("s_curve", 0, -100, 100),
        lab_compress_min=_as_int("lab_compress_min", 0, 0, 100),
        lab_compress_max=_as_int("lab_compress_max", 100, 0, 100),
    )


def _dither_from_dict(raw: dict[str, Any]) -> DitherSettings:
    return DitherSettings(
        algorithm=str(raw.get("algorithm", "floyd-steinberg")),
        serpentine=bool(raw.get("serpentine", True)),
        color_match=str(raw.get("color_match", "rgb")),
        diffusion_strength=max(0, min(100, int(raw.get("diffusion_strength", 100) or 0))),
    )


def _edges_from_dict(raw: dict[str, Any]) -> EdgeSettings:
    return EdgeSettings(
        preserve_line_art=bool(raw.get("preserve_line_art", False)),
        smoothing_radius=max(0, min(3, int(raw.get("smoothing_radius", 0) or 0))),
        protect_native_colours=max(
            0, min(MAX_NATIVE_TOLERANCE, int(raw.get("protect_native_colours", 0) or 0))
        ),
    )


def profile_from_dict(raw: dict[str, Any], *, bundled: bool = False) -> PaletteProfile:
    """Hydrate a profile from an on-disk (or imported) JSON dict.

    ``bundled`` forces the flag on the returned profile; the store uses
    False for user profiles and the bundled table sets True. Unknown
    fields are ignored so importing an epdoptimize palette JSON works
    without a schema translation step."""
    slug = str(raw.get("slug") or raw.get("id") or "").strip()
    name = str(raw.get("name") or slug).strip()
    family = str(raw.get("family") or "spectra6").strip()
    return PaletteProfile(
        slug=slug,
        name=name,
        family=family,
        palette=_colors_from_dict(raw.get("palette") or {}),
        gray=_gray_from_dict(raw.get("gray") or {}),
        tone=_tone_from_dict(raw.get("tone") or {}),
        dither=_dither_from_dict(raw.get("dither") or {}),
        edges=_edges_from_dict(raw.get("edges") or {}),
        bundled=bundled or bool(raw.get("bundled")),
        based_on=(str(raw["based_on"]) if raw.get("based_on") else None),
        attribution=(str(raw["attribution"]) if raw.get("attribution") else None),
        notes=str(raw.get("notes") or ""),
        saved_at=str(raw.get("saved_at") or ""),
    )
