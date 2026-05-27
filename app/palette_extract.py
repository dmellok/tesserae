"""Extract a theme palette from an image.

K-means on the image's pixels (downsampled for speed) finds N dominant
colours; ``assign_to_tokens`` then maps them to the theme tokens by
luminance + saturation heuristics.

Numpy-vectorised — pure-Python k-means works correctly but is ~100x
slower on a 200x200 image; numpy was already a dep for the quantiser.
"""

from __future__ import annotations

import colorsys
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image

# Cap input size to keep k-means fast; quality plateaus past ~200px on a side.
_RESIZE_MAX = 200
# How many clusters to extract. More than the 14 tokens so we have spares
# to pick the most-saturated / most-distinct hues from.
_K_DEFAULT = 8


@dataclass(frozen=True)
class RGB:
    r: int
    g: int
    b: int

    def hex(self) -> str:
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

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
    Returns (centres (K,3) float, counts (K,) int)."""
    n = pixels.shape[0]
    if n == 0:
        return np.zeros((0, 3)), np.zeros(0, dtype=np.int64)
    k = min(k, n)
    rng = np.random.default_rng(42)  # deterministic for the same image
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
        # (N, K) distance matrix in one shot, then argmin per row.
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
    """Return up to ``k`` dominant colours, sorted by cluster size desc."""
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")
    img.thumbnail((_RESIZE_MAX, _RESIZE_MAX))
    pixels = np.asarray(img, dtype=np.uint8).reshape(-1, 3)
    centres, counts = _kmeans_np(pixels, k=k)
    # Popularity sort + clip to 0-255 (mean might give 255.4 etc.).
    order = np.argsort(-counts)
    rounded = np.clip(np.round(centres[order]), 0, 255).astype(int)
    return [RGB(int(r), int(g), int(b)) for r, g, b in rounded]


def _hue_distance(a: float, b: float) -> float:
    """Shortest distance between two hues on the [0,1) hue wheel."""
    d = abs(a - b)
    return min(d, 1.0 - d)


def _pick_accent_trio(colors: list[RGB]) -> list[RGB]:
    """Choose 3 colours with the most saturation that are also spread on
    the hue wheel — so the trio reads as distinct decorative tokens.
    If the image yields fewer than 3 well-saturated hues, the missing
    slots are synthesised by rotating the dominant accent's hue."""
    # Only consider colours with non-trivial saturation; the rest are
    # neutrals that don't belong in the accent slots.
    saturated = [c for c in colors if c.hsv[1] >= 0.15]
    candidates = sorted(saturated, key=lambda c: c.hsv[1], reverse=True)
    if not candidates:
        return []
    picked = [candidates[0]]
    for c in candidates[1:]:
        if len(picked) >= 3:
            break
        h = c.hsv[0]
        if all(_hue_distance(h, p.hsv[0]) > 0.08 for p in picked):
            picked.append(c)
    # Synthesise missing accents by rotating the dominant accent's hue
    # 120° / 240° and keeping its saturation + value. Beats grabbing an
    # unrelated near-duplicate or a neutral as accent3.
    if len(picked) < 3:
        base = picked[0]
        h, s, v = base.hsv
        for rot in (1 / 3, 2 / 3):
            if len(picked) >= 3:
                break
            new_h = (h + rot) % 1.0
            r, g, b = colorsys.hsv_to_rgb(new_h, s, v)
            picked.append(RGB(round(r * 255), round(g * 255), round(b * 255)))
    return picked[:3]


def _desaturate_toward(c: RGB, target_l: float) -> RGB:
    """Pull a colour's saturation down and shift its luminance toward
    ``target_l`` — used when the image lacks a true neutral and we need
    to synthesise one without going off-brand."""
    h, _, _ = c.hsv
    r, g, b = colorsys.hsv_to_rgb(h, 0.05, target_l)
    return RGB(round(r * 255), round(g * 255), round(b * 255))


def _neutral_pool(colors: list[RGB]) -> list[RGB]:
    """Colours with low saturation — candidates for surface / text /
    divider slots. Falls back to all colours if nothing qualifies."""
    low = [c for c in colors if c.hsv[1] <= 0.25]
    return low or colors


def assign_to_tokens(colors: list[RGB], mode: str = "light") -> dict[str, str]:
    """Map extracted dominant colours onto the 14 theme tokens.

    Heuristics:
    * bg / surface / surface2 → low-saturation, light end (dark in dark mode)
    * fg / fgSoft / muted     → low-saturation, dark end (light in dark mode)
    * accent + accent2 + accent3 → three most-saturated, hue-spread
    * accentSoft → lightened accent
    * divider → fgSoft / 50 mix toward bg
    * ok / warn / danger → fixed semantic values (NOT derived from image)
    """
    if not colors:
        # Empty extraction — return a sane default so the form still has
        # 14 keys.
        zero = "#000000"
        one = "#ffffff"
        return {
            "bg": one,
            "surface": one,
            "surface2": "#e2e8f0",
            "fg": zero,
            "fgSoft": "#475569",
            "muted": "#94a3b8",
            "accent": "#1d4ed8",
            "accent2": "#eab308",
            "accent3": "#dc2626",
            "accentSoft": "#dbeafe",
            "divider": "#cbd5e1",
            "ok": "#16a34a",
            "warn": "#facc15",
            "danger": "#dc2626",
        }

    accents = _pick_accent_trio(colors)
    neutrals = sorted(_neutral_pool(colors), key=lambda c: c.luminance)

    # Anchor bg and fg from the appropriate ends of the neutral pool,
    # synthesising if the image doesn't reach the needed luminance.
    # Intermediate tokens (surface, surface2, fg_soft, muted, divider)
    # are SYNTHESISED via mixes off bg/fg — that way they're guaranteed
    # to be sensible neutrals even when the image has few qualifying
    # colours.
    if mode == "dark":
        bg = neutrals[0] if neutrals[0].luminance < 0.15 else _desaturate_toward(neutrals[0], 0.06)
        fg = (
            neutrals[-1] if neutrals[-1].luminance > 0.7 else _desaturate_toward(neutrals[-1], 0.92)
        )
        # Surfaces brighten off bg; text/muted/divider darken off fg.
        surface = _mix(bg, fg, 0.10)
        surface2 = _mix(bg, fg, 0.20)
        fg_soft = _mix(fg, bg, 0.30)
        muted = _mix(fg, bg, 0.50)
        divider = _mix(fg, bg, 0.65)
    else:
        bg = (
            neutrals[-1]
            if neutrals[-1].luminance > 0.85
            else _desaturate_toward(neutrals[-1], 0.94)
        )
        fg = neutrals[0] if neutrals[0].luminance < 0.2 else _desaturate_toward(neutrals[0], 0.08)
        # Surfaces darken slightly off bg; text/muted/divider lighten off fg.
        surface = _mix(bg, fg, 0.02)
        surface2 = _mix(bg, fg, 0.10)
        fg_soft = _mix(fg, bg, 0.35)
        muted = _mix(fg, bg, 0.55)
        divider = _mix(fg, bg, 0.75)

    accent = accents[0] if accents else fg
    accent2 = accents[1] if len(accents) > 1 else accent
    accent3 = accents[2] if len(accents) > 2 else accent

    accent_soft = _mix(accent, bg, 0.75)  # 75% bg, 25% accent

    return {
        "bg": bg.hex(),
        "surface": surface.hex(),
        "surface2": surface2.hex(),
        "fg": fg.hex(),
        "fgSoft": fg_soft.hex(),
        "muted": muted.hex(),
        "accent": accent.hex(),
        "accent2": accent2.hex(),
        "accent3": accent3.hex(),
        "accentSoft": accent_soft.hex(),
        "divider": divider.hex(),
        # Semantic tokens stay reserved for ok/warn/error and never
        # derive from the image — a "moody photo" theme would otherwise
        # have an unreadable "danger" colour.
        "ok": "#16a34a",
        "warn": "#facc15",
        "danger": "#dc2626",
    }


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    """Linear RGB mix; t=0 returns a, t=1 returns b."""
    return RGB(
        round(a.r * (1 - t) + b.r * t),
        round(a.g * (1 - t) + b.g * t),
        round(a.b * (1 - t) + b.b * t),
    )
