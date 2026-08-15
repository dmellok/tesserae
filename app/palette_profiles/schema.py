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
        tone=_tone_from_dict(raw.get("tone") or {}),
        dither=_dither_from_dict(raw.get("dither") or {}),
        edges=_edges_from_dict(raw.get("edges") or {}),
        bundled=bundled or bool(raw.get("bundled")),
        based_on=(str(raw["based_on"]) if raw.get("based_on") else None),
        attribution=(str(raw["attribution"]) if raw.get("attribution") else None),
        notes=str(raw.get("notes") or ""),
        saved_at=str(raw.get("saved_at") or ""),
    )
