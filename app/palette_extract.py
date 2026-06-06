"""Extract a Spectra-shaped theme palette from an uploaded image.

K-means on the image's pixels (downsampled to ~200px on the long
side for speed) finds N dominant colours; :func:`assign_to_tokens`
maps them onto the 20 Spectra tokens by luminance + saturation
heuristics.

Why hand-rolled k-means instead of pulling in scikit-learn? numpy is
already a dep for the quantiser, and the algorithm fits in ~30 lines
once vectorised. Avoiding the scikit dependency keeps the install
small for the rest of the appliance (which doesn't need ML libs).

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import colorsys
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, UnidentifiedImageError

# Cap input size, k-means scales as N×K×iterations; quality plateaus
# past ~200px on a side because dominant clusters are stable well
# before that. 200 also bounds memory: the pairwise distance matrix is
# N×K floats, so 40k pixels × 10 clusters × 8 bytes ≈ 3.2 MB. Tolerable.
_RESIZE_MAX = 200

# How many clusters to extract. 10 gives the assigner spare candidates
# beyond the 6 accent slots and the 3 surface / 3 text / 1 edge
# neutrals, so it can pick the most-distinct hues without forcing the
# first match.
_K_DEFAULT = 10

# Cap upload size as an extra defence in depth, the route layer
# already enforces a content-length limit, but a malformed PNG that
# decodes huge could still walk past the budget.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


class PaletteExtractError(ValueError):
    """Raised when the uploaded bytes can't be opened as an image or
    are empty after decode. Routes catch this to flash a friendly
    message instead of leaking a stack trace."""


@dataclass(frozen=True)
class RGB:
    r: int
    g: int
    b: int

    def hex(self) -> str:
        return f"#{self.r:02X}{self.g:02X}{self.b:02X}"

    @property
    def luminance(self) -> float:
        """WCAG-style relative luminance, 0..1."""
        rs, gs, bs = (c / 255.0 for c in (self.r, self.g, self.b))
        rl = rs / 12.92 if rs <= 0.04045 else ((rs + 0.055) / 1.055) ** 2.4
        gl = gs / 12.92 if gs <= 0.04045 else ((gs + 0.055) / 1.055) ** 2.4
        bl = bs / 12.92 if bs <= 0.04045 else ((bs + 0.055) / 1.055) ** 2.4
        return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl

    @property
    def hsv(self) -> tuple[float, float, float]:
        return colorsys.rgb_to_hsv(self.r / 255, self.g / 255, self.b / 255)


def _kmeans_np(pixels: np.ndarray, k: int, max_iter: int = 12) -> tuple[np.ndarray, np.ndarray]:
    """Lloyd's algorithm, vectorised. ``pixels`` is shape (N, 3) uint8/float.
    Returns (centres (K, 3) float, counts (K,) int).

    Deterministic, k-means++ init uses a fixed-seed RNG so the same
    image extracts the same palette across runs. That matters for the
    builder's "Apply" UX: re-uploading the same photo shouldn't shuffle
    every colour."""
    n = pixels.shape[0]
    if n == 0:
        return np.zeros((0, 3)), np.zeros(0, dtype=np.int64)
    k = min(k, n)
    rng = np.random.default_rng(42)
    pf = pixels.astype(np.float64)

    # k-means++ init: pick first centre at random, subsequent centres
    # weighted by squared distance from the closest existing centre.
    centres = np.empty((k, 3), dtype=np.float64)
    centres[0] = pf[rng.integers(n)]
    closest_d2 = np.sum((pf - centres[0]) ** 2, axis=1)
    for i in range(1, k):
        probs = closest_d2 / closest_d2.sum() if closest_d2.sum() > 0 else None
        idx = rng.choice(n, p=probs) if probs is not None else rng.integers(n)
        centres[i] = pf[idx]
        d2 = np.sum((pf - centres[i]) ** 2, axis=1)
        closest_d2 = np.minimum(closest_d2, d2)

    assignments = np.zeros(n, dtype=np.int64)
    for _ in range(max_iter):
        d2 = np.sum((pf[:, None, :] - centres[None, :, :]) ** 2, axis=2)
        new_assign = np.argmin(d2, axis=1)
        if np.array_equal(new_assign, assignments):
            break
        assignments = new_assign
        for j in range(k):
            mask = assignments == j
            if mask.any():
                centres[j] = pf[mask].mean(axis=0)

    counts = np.bincount(assignments, minlength=k)
    return centres, counts


def extract_dominant(image_bytes: bytes, k: int = _K_DEFAULT) -> list[RGB]:
    """Return up to ``k`` dominant colours from the uploaded image,
    sorted by cluster population (most common first).

    Raises :class:`PaletteExtractError` on a decode failure or an empty
    image so the calling route can convert it into a flash message.
    """
    if not image_bytes:
        raise PaletteExtractError("uploaded file was empty")
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise PaletteExtractError(
            f"image exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB upload cap"
        )
    try:
        img: Image.Image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except (UnidentifiedImageError, OSError) as err:
        raise PaletteExtractError("couldn't decode the upload as an image") from err
    img.thumbnail((_RESIZE_MAX, _RESIZE_MAX))
    pixels = np.asarray(img, dtype=np.uint8).reshape(-1, 3)
    if pixels.size == 0:
        raise PaletteExtractError("image had no pixels after decode")
    centres, counts = _kmeans_np(pixels, k=k)
    order = np.argsort(-counts)
    rounded = np.clip(np.round(centres[order]), 0, 255).astype(int)
    return [RGB(int(r), int(g), int(b)) for r, g, b in rounded]


# -- token assignment --------------------------------------------------


def _hue_distance(a: float, b: float) -> float:
    """Shortest distance between two hues on the [0, 1) hue wheel."""
    d = abs(a - b)
    return min(d, 1.0 - d)


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    """Linear sRGB mix; t=0 returns a, t=1 returns b."""
    return RGB(
        round(a.r * (1 - t) + b.r * t),
        round(a.g * (1 - t) + b.g * t),
        round(a.b * (1 - t) + b.b * t),
    )


def _desaturate_toward(c: RGB, target_l: float) -> RGB:
    """Pull a colour's saturation down and shift its luminance toward
    ``target_l``. Used to synthesise a near-neutral surface or text
    when the image lacks one without going wildly off-brand."""
    h, _, _ = c.hsv
    r, g, b = colorsys.hsv_to_rgb(h, 0.05, target_l)
    return RGB(round(r * 255), round(g * 255), round(b * 255))


def _pick_accent_set(colors: list[RGB], target: int = 6) -> list[RGB]:
    """Choose up to ``target`` accent colours that are saturated AND
    spread on the hue wheel. Falls back to synthesised hue rotations of
    the dominant accent if the image doesn't supply enough distinct
    hues, better than serving six near-duplicates."""
    saturated = sorted(
        (c for c in colors if c.hsv[1] >= 0.15),
        key=lambda c: c.hsv[1],
        reverse=True,
    )
    if not saturated:
        # Synthesize a six-step hue ramp around a slate blue as a
        # last-resort default. Triggered when the image is monochrome.
        anchor = RGB(70, 110, 170)
        return _synthesise_accent_ramp(anchor, target)
    picked = [saturated[0]]
    # Loosen the hue-distance threshold as we accumulate picks: a strict
    # 0.16 (≈58°) works for a 3-accent ask but fails for 6. Start at
    # 0.08 and decrement so later accents tolerate closer hues.
    for c in saturated[1:]:
        if len(picked) >= target:
            break
        min_gap = max(0.04, 0.16 - 0.02 * len(picked))
        h = c.hsv[0]
        if all(_hue_distance(h, p.hsv[0]) > min_gap for p in picked):
            picked.append(c)
    # Synthesise any remainders by rotating the dominant accent by
    # even increments around the hue wheel, keeps the slot filled
    # with a colour that feels related to the image's actual accent.
    if len(picked) < target:
        picked.extend(_synthesise_accent_ramp(picked[0], target - len(picked)))
    return picked[:target]


def _synthesise_accent_ramp(anchor: RGB, count: int) -> list[RGB]:
    """``count`` hue-rotated copies of ``anchor`` to backfill missing
    accent slots. Saturation + value are preserved so the synthesised
    accents read at the same intensity as the anchor."""
    if count <= 0:
        return []
    h, s, v = anchor.hsv
    out: list[RGB] = []
    for i in range(1, count + 1):
        new_h = (h + i / 6.0) % 1.0
        r, g, b = colorsys.hsv_to_rgb(new_h, max(s, 0.55), max(v, 0.45))
        out.append(RGB(round(r * 255), round(g * 255), round(b * 255)))
    return out


def _neutral_pool(colors: list[RGB]) -> list[RGB]:
    """Low-saturation colours, candidates for surfaces / text / edge.
    Falls back to the full list when the image is purely chromatic so
    the assigner always has something to work with."""
    low = [c for c in colors if c.hsv[1] <= 0.25]
    return low or colors


def _detect_mode(colors: list[RGB]) -> str:
    """Guess light vs dark based on the dominant cluster's luminance.
    ``colors`` arrives sorted by population so colors[0] is the modal
    pixel, a fair proxy for "the image's overall mood"."""
    if not colors:
        return "light"
    return "dark" if colors[0].luminance < 0.4 else "light"


def assign_to_tokens(colors: list[RGB], mode: str | None = None) -> dict[str, str]:
    """Map dominant colours onto the 20 Spectra tokens.

    Heuristics:

    * bg / surface / surface-sunken, neutral, light end in light mode,
      dark end in dark mode. Intermediates synthesised off bg toward
      the contrast end so they're always sensible.
    * text-primary / -secondary / -muted, opposite-luminance neutral
      with progressive mix toward bg.
    * edge, text mixed further toward bg (3/4 of the way) so it sits
      between text-muted and surface-sunken in contrast.
    * accent-1..6, six most-saturated hue-spread colours.
    * accent-*-soft, each accent mixed 80% with bg for a tinted pill
      background that pairs cleanly.
    * on-accent, light surface in dark mode (text on accent reads on
      the cool surface) and the dark text colour in light mode.
    """
    if mode is None:
        mode = _detect_mode(colors)
    mode = mode if mode in ("light", "dark") else "light"

    if not colors:
        # No image / extract failed, degrade to the dataclass default
        # palette so the form is still useful.
        return _empty_palette(mode)

    accents = _pick_accent_set(colors, target=6)
    neutrals = sorted(_neutral_pool(colors), key=lambda c: c.luminance)

    if mode == "dark":
        bg = neutrals[0] if neutrals[0].luminance < 0.15 else _desaturate_toward(neutrals[0], 0.06)
        text_primary = (
            neutrals[-1] if neutrals[-1].luminance > 0.7 else _desaturate_toward(neutrals[-1], 0.92)
        )
        surface = _mix(bg, text_primary, 0.10)
        surface_sunken = _mix(bg, text_primary, 0.04)
        text_secondary = _mix(text_primary, bg, 0.25)
        text_muted = _mix(text_primary, bg, 0.50)
        edge = _mix(text_primary, bg, 0.75)
        on_accent = bg
    else:
        bg = (
            neutrals[-1]
            if neutrals[-1].luminance > 0.85
            else _desaturate_toward(neutrals[-1], 0.94)
        )
        text_primary = (
            neutrals[0] if neutrals[0].luminance < 0.2 else _desaturate_toward(neutrals[0], 0.08)
        )
        surface = _mix(bg, text_primary, 0.02)
        surface_sunken = _mix(bg, text_primary, 0.08)
        text_secondary = _mix(text_primary, bg, 0.35)
        text_muted = _mix(text_primary, bg, 0.55)
        edge = _mix(text_primary, bg, 0.75)
        on_accent = surface

    out: dict[str, str] = {
        "bg": bg.hex(),
        "surface": surface.hex(),
        "surface_sunken": surface_sunken.hex(),
        "text_primary": text_primary.hex(),
        "text_secondary": text_secondary.hex(),
        "text_muted": text_muted.hex(),
        "edge": edge.hex(),
        "on_accent": on_accent.hex(),
    }
    for i, accent in enumerate(accents, start=1):
        out[f"accent_{i}"] = accent.hex()
        out[f"accent_{i}_soft"] = _mix(accent, bg, 0.78).hex()
    # Pad missing accent slots with bg-tinted neutrals so every key
    # always lands in the output; never happens after _pick_accent_set
    # synthesises but defensive in case of future refactor.
    for i in range(len(accents) + 1, 7):
        out[f"accent_{i}"] = bg.hex()
        out[f"accent_{i}_soft"] = bg.hex()
    return out


def _empty_palette(mode: str) -> dict[str, str]:
    """Sane defaults when extraction returns nothing, light/dark
    variant of the bundled ``light`` / ``dark`` Spectra defaults."""
    if mode == "dark":
        return {
            "bg": "#141310",
            "surface": "#232019",
            "surface_sunken": "#1B1813",
            "text_primary": "#F1EEE4",
            "text_secondary": "#B9B4A6",
            "text_muted": "#807C70",
            "edge": "#3A362C",
            "on_accent": "#141310",
            "accent_1": "#DC8C63",
            "accent_1_soft": "#3E2E22",
            "accent_2": "#D7B355",
            "accent_2_soft": "#3A3318",
            "accent_3": "#A0BA76",
            "accent_3_soft": "#2C3420",
            "accent_4": "#69B7B0",
            "accent_4_soft": "#1E332F",
            "accent_5": "#88A2CD",
            "accent_5_soft": "#232C3C",
            "accent_6": "#C389AC",
            "accent_6_soft": "#33222E",
        }
    return {
        "bg": "#E7E4DC",
        "surface": "#F7F5F0",
        "surface_sunken": "#E1DDD2",
        "text_primary": "#1B1A16",
        "text_secondary": "#4D4A42",
        "text_muted": "#837F73",
        "edge": "#B6B1A4",
        "on_accent": "#F7F5F0",
        "accent_1": "#A84B2A",
        "accent_1_soft": "#E9D6CC",
        "accent_2": "#9A7414",
        "accent_2_soft": "#EAE0C4",
        "accent_3": "#4F6F36",
        "accent_3_soft": "#D9E2C9",
        "accent_4": "#256E6B",
        "accent_4_soft": "#CCE0DD",
        "accent_5": "#3F5A88",
        "accent_5_soft": "#D2DCEA",
        "accent_6": "#7E4068",
        "accent_6_soft": "#E5D2DF",
    }
