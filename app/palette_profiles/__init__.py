"""Palette profiles: pre-measured e-ink calibration data + user-saved edits.

E-paper panels don't reproduce sRGB primaries; picking a palette is
a per-panel choice that depends on the panel's chemistry, viewing
light, and the user's taste. A profile bundles the palette (six or
seven RGB values), tone-mapping controls (exposure, contrast,
saturation, S-curve, LAB range compression), dither options
(algorithm, serpentine scan, colour match, diffusion strength), and
edge-handling toggles into one JSON blob.

Two sources:

* **Bundled** (:mod:`app.palette_profiles.bundled`), read-only presets
  derived from :mod:`epdoptimize`'s ``default-palettes.json``. Six
  Spectra 6 variants (paperlesspaper measured, Boeber, aitjcize,
  paperlesspaper legacy, Tesserae's tuned default, and the nominal
  identity), plus the seven-colour Inky ACeP profile. All carry
  attribution back to the source project.

* **User** (:mod:`app.palette_profiles.store`), JSON files under
  ``data/palette_profiles/<slug>.json`` written by the "Save as new"
  action on the Calibration tab and by profile imports.

Palette data reused from `paperlesspaper/epdoptimize
<https://github.com/paperlesspaper/epdoptimize>`_ is under Apache 2.0;
see :file:`NOTICES.md` at the repo root.
"""

from __future__ import annotations

from app.palette_profiles.bundled import BUNDLED_PROFILES, bundled_profile, list_bundled
from app.palette_profiles.resolver import resolve_device_palette
from app.palette_profiles.schema import (
    MAX_NATIVE_TOLERANCE,
    DitherSettings,
    EdgeSettings,
    PaletteColors,
    PaletteProfile,
    ToneSettings,
    profile_from_dict,
)
from app.palette_profiles.store import PaletteProfileStore, slugify

__all__ = [
    "BUNDLED_PROFILES",
    "MAX_NATIVE_TOLERANCE",
    "DitherSettings",
    "EdgeSettings",
    "PaletteColors",
    "PaletteProfile",
    "PaletteProfileStore",
    "ToneSettings",
    "bundled_profile",
    "list_bundled",
    "profile_from_dict",
    "resolve_device_palette",
    "slugify",
]
