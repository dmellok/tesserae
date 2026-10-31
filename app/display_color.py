"""Color metadata for UI previews; delivery keeps using the real renderer.

Device.panel is the source of truth: the loader has already applied catalog,
instance and selected-renderer gamut overrides. Browser filters approximate
that gamut, never the renderer's spatial dithering or calibrated palette.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from flask import Flask, g

from app.quantizer import canonicalise_gamut

if TYPE_CHECKING:
    from app.device_loader import Device
    from app.renderer_loader import RendererRegistry
    from app.state.settings_store import SettingsStore


@dataclass(frozen=True)
class DisplayColor:
    gamut: str = "color"
    levels: int = 0  # Zero means a color palette, not a grey ramp.
    dither: str | None = None


def display_color_mode(
    device: Device | None,
    renderers: RendererRegistry | None = None,
    settings: SettingsStore | None = None,
) -> DisplayColor:
    """Resolve the effective grey level count and selected renderer's dither.

    Dedicated renderer gamuts also cover old devices with no declared gamut.
    Dither 'none' still quantises: it changes spatial placement, not the number of levels.
    Mono is 'mono' (hard threshold) only for dither 'none'; any other or
    unknown dither is 'mono-dither', which previews as smooth grey.
    Unknown panels stay in color rather than silently discarding content.
    """
    if device is None:
        return DisplayColor()
    panel = device.panel or {}
    gamut = str(panel.get("gamut") or "")
    renderer = renderers.get(device.renderer_ids[0]) if renderers and device.renderer_ids else None
    dither = None
    if renderer is not None:
        gamuts = renderer.manifest.get("gamuts") or []
        if not gamut and len(gamuts) == 1:
            gamut = str(gamuts[0])
        values = (
            settings.get_for_runtime(
                "renderers", renderer.id, renderer.manifest.get("settings", [])
            )
            if settings is not None
            else renderer.settings_defaults()
        )
        values = {**values, **(renderer.manifest.get("render_overrides") or {})}
        if isinstance(values.get("dither"), str):
            dither = str(values["dither"])
    gamut = canonicalise_gamut(gamut)
    if gamut == "mono":
        # Only an explicit no-dither mode paints hard black/white. Every error
        # diffusion or ordered mode averages out to grey at thumbnail size.
        return DisplayColor("mono" if dither == "none" else "mono-dither", 2, dither)
    if gamut.startswith("gray_") and gamut[5:].isdigit():
        levels = int(gamut[5:])
        if levels > 1:
            return DisplayColor(f"grey{levels}", levels, dither)
    return DisplayColor(dither=dither)


def register_preview_color(app: Flask) -> None:
    """One request-cached resolver for every Jinja thumbnail surface."""

    def preview_color(
        device_ids: str | list[str] | tuple[str, ...] | None = None,
    ) -> DisplayColor:
        ids = [device_ids] if isinstance(device_ids, str) else list(device_ids or [])
        devices = app.config.get("DEVICE_REGISTRY")
        if not ids or devices is None:
            return DisplayColor()
        cache: dict[str, DisplayColor] = g.setdefault("_preview_colors", {})
        modes = []
        for did in ids:
            if did not in cache:
                cache[did] = display_color_mode(
                    devices.get(did),
                    app.config.get("RENDERER_REGISTRY"),
                    app.config.get("SETTINGS_STORE"),
                )
            modes.append(cache[did])
        # Unbound, missing or mixed-gamut targets keep color. A shared dashboard
        # must not suggest that all its displays paint like the first one.
        if any((m.gamut, m.levels) != (modes[0].gamut, modes[0].levels) for m in modes):
            return DisplayColor()
        return modes[0]

    app.jinja_env.globals["preview_color"] = preview_color
