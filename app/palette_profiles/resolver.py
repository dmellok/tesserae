"""Resolve a device's active palette profile into RGB tuples.

Sits between the settings-store (where a device stores which profile
slug it's using) and the renderers (which want a palette tuple to pass
into :func:`app.quantizer.pack_to_panel_bin`). One-hop lookup so
callers don't have to know about the bundled-vs-user split.

The active profile lives at ``settings.devices.<id>.palette_profile_slug``.
Missing / empty / unknown slug means "no override": callers should
fall through to the module-level ``_CALIBRATED_PALETTES`` lookup or,
when the clone's ``calibrated`` toggle is off, the nominal palette.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

from typing import Any

from app.palette_profiles.bundled import bundled_profile
from app.palette_profiles.store import PaletteProfileStore


def resolve_device_palette(
    *,
    device_id: str,
    settings_store: Any,
    profile_store: PaletteProfileStore,
) -> tuple[tuple[int, int, int], ...] | None:
    """Return the palette RGB tuples the device's active profile paints
    with, or ``None`` when there's no profile set / the slug doesn't
    resolve. Bundled slugs are consulted first (fastest, no disk hit),
    then the user store.

    ``settings_store`` is typed ``Any`` because the sibling
    :mod:`app.state.settings_store` module isn't importable from here
    without a cycle; the concrete type has ``get_for_runtime``."""
    if not device_id:
        return None
    slug_field = {"name": "palette_profile_slug", "type": "string", "default": ""}
    raw = settings_store.get_for_runtime("devices", device_id, [slug_field])
    slug = str(raw.get("palette_profile_slug") or "").strip()
    if not slug:
        return None
    bundled = bundled_profile(slug)
    if bundled is not None:
        return bundled.palette.as_tuples()
    user = profile_store.load(slug)
    if user is not None:
        return user.palette.as_tuples()
    return None
