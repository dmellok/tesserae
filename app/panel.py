"""Panel-dimension resolution.

Pages no longer carry their own panel dims. The panel comes from the
app-wide ``app`` settings section so that swapping panels (or rotating
a single panel) updates every saved page without per-page edits.

Resolution order:

1. ``app.panel_preset`` (e.g. ``inky_13_3``), if it's a known preset,
   take its native landscape dims.
2. Otherwise (``custom`` or unknown): fall back to ``app.panel_w`` /
   ``app.panel_h``.
3. If ``app.panel_orientation`` is ``portrait``, swap width and height.

A Page can still set its own ``panel`` (Pydantic field stays optional)
to override, useful if you ever want one dashboard at a different
size, but the UI doesn't expose that knob for v1.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from math import gcd
from typing import TYPE_CHECKING, Any

from app.state.page_store import Panel
from app.state.settings_store import SettingsStore

if TYPE_CHECKING:
    from app.device_loader import Device, DeviceRegistry
    from app.state.page_store import Page


@dataclass(frozen=True)
class PanelPreset:
    """A panel preset's intrinsic facts.

    ``w`` / ``h`` are the firmware-native row stride, what the panel's
    on-device firmware expects to feed SPI / its own composite buffer.
    The user can mount the panel in any orientation; the composer
    handles the rotation upstream, but the renderer must always pack at
    the native stride or the firmware will read the wrong row width.

    ``native_landscape`` is True when the firmware-native stride is
    wider than tall (most consumer panels). It's False for the
    portrait-native Waveshare 13.3" Spectra 6 used with ESP32, whose
    firmware reads 1200 wide × 1600 tall regardless of how the user
    mounts it.

    ``label`` is the user-facing string shown in the panel picker.
    """

    w: int
    h: int
    label: str
    native_landscape: bool = True


PANEL_PRESETS: dict[str, PanelPreset] = {
    "inky_13_3": PanelPreset(
        1600, 1200, label='Inky Impression 13.3", 1600x1200'
    ),  # Pimoroni Inky Impression 13.3" (Spectra 6), landscape native
    "inky_7_3": PanelPreset(
        800, 480, label='Inky Impression 7.3", 800x480'
    ),  # Pimoroni Inky Impression 7.3" (Spectra 6), landscape native
    "inky_5_7": PanelPreset(
        600, 448, label='Inky Impression 5.7", 600x448'
    ),  # Pimoroni Inky Impression 5.7" (7-colour legacy), landscape native
    "inky_4": PanelPreset(
        640, 400, label='Inky Impression 4", 640x400'
    ),  # Pimoroni Inky Impression 4" (Spectra 6), landscape native
    "waveshare_e6_7_5": PanelPreset(
        800, 480, label='Waveshare E6 7.5", 800x480'
    ),  # Waveshare E6 7.5", landscape native
    "waveshare_photopainter_7_3": PanelPreset(
        800, 480, label='Waveshare 7.3" PhotoPainter (ESP32-S3), 800x480'
    ),  # Waveshare 7.3" PhotoPainter (ESP32-S3 Spectra 6), landscape native
    "waveshare_e6_13_3": PanelPreset(
        1200,
        1600,
        label='Waveshare 13.3" Spectra 6 (ESP32), 1200x1600',
        native_landscape=False,
    ),  # ESP32 Waveshare 13.3", portrait-native (firmware reads 1200×1600 portrait)
    "waveshare_42_bw": PanelPreset(
        400, 300, label='Waveshare 4.2" B/W (ESP32, 1-bpp), 400x300'
    ),  # ESP32 Waveshare 4.2" mono e-paper, 400×300 landscape-native, 1-bpp wire
}

PANEL_PRESET_CHOICES: list[dict[str, str]] = [
    {"value": pid, "label": preset.label} for pid, preset in PANEL_PRESETS.items()
] + [
    {"value": "custom", "label": "Custom (set width + height below)"},
]

DEFAULT_PRESET: str = "inky_13_3"


def _preset_composition_landscape(preset: PanelPreset) -> tuple[int, int]:
    """Return the (w, h) the panel takes when mounted **landscape**.

    For a landscape-native preset that's already ``(preset.w, preset.h)``;
    for a portrait-native one the user gets a landscape view by mounting
    the panel sideways, which swaps the visible dims to
    ``(preset.h, preset.w)``."""
    if preset.native_landscape:
        return (preset.w, preset.h)
    return (preset.h, preset.w)


def panel_overrides_from_form(form: Any) -> dict[str, Any]:
    """Resolve an Add-device form's panel size into a ``{"w","h"}``
    override dict (and ``native_w / native_h`` for known presets). A
    named preset wins; otherwise the custom width / height inputs are
    used (ignored if non-numeric). Shared between Settings → Devices
    and the onboarding wizard's manual-add form.

    ``w``/``h`` are the panel's **landscape composition dims**, the
    legacy convention every downstream caller (onboarding, device
    routes, page hydration) already expects. The orientation pick is
    applied separately and rounds out the composition orientation.

    ``native_w``/``native_h`` are the **firmware-native row stride** -
    populated only when a preset is chosen, because custom panels
    can't tell us their hardware orientation without an extra UI
    knob. Downstream callers pass them through to the renderer."""
    preset_id = (form.get("panel_preset") or "").strip()
    if preset_id in PANEL_PRESETS:
        preset = PANEL_PRESETS[preset_id]
        landscape_w, landscape_h = _preset_composition_landscape(preset)
        return {
            "w": landscape_w,
            "h": landscape_h,
            "native_w": preset.w,
            "native_h": preset.h,
        }
    overrides: dict[str, Any] = {}
    for field_name, key in (("panel_w", "w"), ("panel_h", "h")):
        raw = form.get(field_name)
        if raw:
            with contextlib.suppress(ValueError):
                overrides[key] = int(raw)
    return overrides


def resolve_settings_panel(settings: SettingsStore) -> Panel:
    """Compute the panel dims from the app settings section."""
    app = settings.get_section("app")
    preset_id = str(app.get("panel_preset") or DEFAULT_PRESET)
    native_w: int | None = None
    native_h: int | None = None
    if preset_id in PANEL_PRESETS:
        preset = PANEL_PRESETS[preset_id]
        # Start from the panel's landscape composition view (legacy
        # convention everything else expects).
        w, h = _preset_composition_landscape(preset)
        # Firmware-native row stride is fixed by the hardware regardless
        # of how the user mounts it.
        native_w, native_h = preset.w, preset.h
    else:
        # 'custom' or any value the user typed in by hand falls back to
        # the explicit w/h fields. Defaults match Inky 13.3" so a fresh
        # install with no settings still works. We can't infer the
        # firmware-native orientation for a hand-typed panel; the
        # renderer treats native_w / native_h == None as "pack at panel
        # (w, h) directly", which matches pre-v0.19.19 behaviour.
        w = _int_or(app.get("panel_w"), 1600)
        h = _int_or(app.get("panel_h"), 1200)
    if _is_portrait(app.get("panel_orientation")):
        w, h = h, w
    return Panel(w=max(1, w), h=max(1, h), native_w=native_w, native_h=native_h)


def resolve_page_panel(page_panel: Panel | None, settings: SettingsStore) -> Panel:
    """Resolve the panel from raw page-panel dims + settings.

    Multi-head dispatch lives in ``resolve_panel_for_page``, use that
    when you have the full Page (it can look up the device's declared
    panel). This entry point is kept for callers that only have the
    optional Panel field handy."""
    if page_panel is not None:
        return page_panel
    return resolve_settings_panel(settings)


def device_panel(device: Device) -> Panel | None:
    """Panel for a single device, or None if it declares no panel block.

    Picks up firmware-native dims from the device manifest when
    available (``panel.native_w`` / ``panel.native_h``). Without those,
    falls back to the matching preset's native dims if the device's
    declared (w, h) matches a known preset's landscape composition.
    Custom panels with no preset hit and no manifest hint leave
    ``native_w / native_h`` as None, the renderer packs at (w, h)
    directly in that case."""
    block = device.panel
    if block is None:
        return None
    w = int(block["w"])
    h = int(block["h"])
    native_w: int | None = None
    native_h: int | None = None
    # Explicit manifest declaration wins.
    if "native_w" in block and "native_h" in block:
        with contextlib.suppress(TypeError, ValueError):
            native_w = int(block["native_w"])
            native_h = int(block["native_h"])
    else:
        # Try to match the device's (w, h) against a known preset's
        # landscape composition. If a preset matches, lift its native
        # dims. This means a device.json that already says (800, 480)
        # implicitly picks up the PhotoPainter's landscape-native
        # stride, no manifest change required.
        for preset in PANEL_PRESETS.values():
            comp_w, comp_h = _preset_composition_landscape(preset)
            if (w, h) == (comp_w, comp_h) or (w, h) == (comp_h, comp_w):
                native_w, native_h = preset.w, preset.h
                break
    return Panel(
        w=w,
        h=h,
        flip=is_flipped_orientation(block.get("orientation")),
        gamut=str(block.get("gamut") or "waveshare_e6"),
        underscan=max(0, int(block.get("underscan") or 0)),
        native_w=native_w,
        native_h=native_h,
    )


def _selected_device_panels(
    page: Page, devices: DeviceRegistry | None
) -> list[tuple[Device, Panel]]:
    """The page's targeted devices that declare a panel, paired with it.
    Devices that are unknown or panel-less are skipped."""
    if not page.device_ids or devices is None:
        return []
    out: list[tuple[Device, Panel]] = []
    for did in page.device_ids:
        device = devices.devices.get(did)
        if device is None:
            continue
        panel = device_panel(device)
        if panel is not None:
            out.append((device, panel))
    return out


def resolve_panel_for_page(
    page: Page,
    devices: DeviceRegistry | None,
    settings: SettingsStore,
) -> Panel:
    """The page's primary panel, for single-panel contexts (the editor's
    layout grid, the default compose render).

    Selection rule:

    * If devices are bound, return the **largest** of their panels by
      area. Largest is deterministic regardless of the user's bind
      order, so binding a second device with a different aspect ratio
      doesn't silently swap the design canvas under the existing
      layout. It also gives the editor the most pixels to work with.
      Ties (two panels with identical area) break by panel id /
      ``(w, h)`` tuple for stability.
    * Otherwise fall back to ``page.panel``, else the global settings
      panel.

    Previously this returned ``panels[0]`` (first targeted device),
    which meant a layout designed against device A would get
    silently re-anchored to device B as soon as B was added — and
    the load handler's ``_ensure_cells_fit_panel`` would then
    non-uniformly rescale every cell, with the round-trip eating
    edge alignments and "garbling" the layout. See
    ``_ensure_cells_fit_panel`` in app/page_routes.py for the
    matching change.
    """
    panels = _selected_device_panels(page, devices)
    if panels:
        return max(panels, key=lambda dp: (dp[1].w * dp[1].h, dp[1].w, dp[1].h, dp[0].id))[1]
    return resolve_page_panel(page.panel, settings)


def panel_groups_for_push(
    page: Page,
    devices: DeviceRegistry | None,
    settings: SettingsStore,
) -> list[tuple[Panel, list[str]]]:
    """Distinct panels the page must be rendered at, each paired with the
     device ids that share it. Devices are grouped by exact dims + flip, so
     a 4:3 and a portrait panel render separately while two identical
     panels render once. An empty device-id list means "no targeted device"
    , render at the virtual panel and fan out to every renderer."""
    panels = _selected_device_panels(page, devices)
    if not panels:
        return [(resolve_page_panel(page.panel, settings), [])]
    # Key includes gamut + underscan: same-size panels that pack to a
    # different palette (E6 vs 7-colour Inky) or inset by a different mat
    # margin must render as separate groups, so each group's Panel carries
    # the exact attributes its renderers transform against.
    groups: dict[tuple[int, int, bool, str, int], tuple[Panel, list[str]]] = {}
    for device, panel in panels:
        key = (panel.w, panel.h, panel.flip, panel.gamut, panel.underscan)
        if key not in groups:
            groups[key] = (panel, [])
        groups[key][1].append(device.id)
    return list(groups.values())


def preview_groups_for_page(
    page: Page,
    devices: DeviceRegistry | None,
    settings: SettingsStore,
) -> list[dict[str, Any]]:
    """One entry per distinct aspect ratio among the page's targeted
    devices, the editor renders a preview card for each. Same aspect =
    same layout, so devices that differ only in resolution share a card.
    No targeted device → a single virtual-panel card."""
    panels = _selected_device_panels(page, devices)
    if not panels:
        p = resolve_page_panel(page.panel, settings)
        return [{"w": p.w, "h": p.h, "label": "Virtual panel", "devices": []}]
    groups: dict[tuple[int, int], dict[str, Any]] = {}
    for device, panel in panels:
        g = gcd(panel.w, panel.h) or 1
        key = (panel.w // g, panel.h // g)
        grp = groups.setdefault(key, {"w": panel.w, "h": panel.h, "devices": []})
        grp["devices"].append(device.display_name)
    out: list[dict[str, Any]] = []
    for (aw, ah), grp in groups.items():
        shape = (
            "Portrait"
            if grp["h"] > grp["w"]
            else ("Square" if grp["h"] == grp["w"] else "Landscape")
        )
        grp["label"] = f"{shape} {aw}:{ah}"
        out.append(grp)
    return out


def is_flipped_orientation(orientation: object) -> bool:
    """True for the upside-down orientation variants. The renderer adds a
    180° turn so the dashboard reads upright on a flipped physical mount."""
    return orientation in ("landscape_flipped", "portrait_flipped")


def fit_cells_to_panel(
    cells: list[tuple[int, int, int, int]],
    target_w: int,
    target_h: int,
) -> list[tuple[int, int, int, int]]:
    """Project cells onto the target panel, auto-rotating 90° if the
    cells were laid out for the opposite orientation.

    Each cell is ``(x, y, w, h)`` in panel pixels. The function picks
    the "design panel" from the cells' bounding box (max x+w, max y+h)
    and:

    * Rotates the layout 90° if design orientation != target
      orientation, so a landscape dashboard auto-fits a portrait panel
      and vice-versa.
    * Scales each cell by ``target_w / design_w`` and
      ``target_h / design_h`` so the layout proportions are preserved
      regardless of exact panel size differences (e.g. 1600x1200 →
      800x600).

    Pure function, no Cell objects, no I/O, so this is cheap to call
    at every render/preview tick."""
    if not cells:
        return []
    design_w = max(x + w for x, y, w, h in cells)
    design_h = max(y + h for x, y, w, h in cells)
    if design_w <= 0 or design_h <= 0:
        return list(cells)

    design_landscape = design_w >= design_h
    target_landscape = target_w >= target_h
    if design_landscape != target_landscape:
        # Rotate 90° clockwise: (x, y) -> (design_h - y - h, x);
        # the cell's (w, h) swap so it lines up after the rotation.
        rotated: list[tuple[int, int, int, int]] = []
        for x, y, w, h in cells:
            rotated.append((design_h - y - h, x, h, w))
        cells = rotated
        design_w, design_h = design_h, design_w

    sx = target_w / design_w
    sy = target_h / design_h
    out: list[tuple[int, int, int, int]] = []
    for x, y, w, h in cells:
        nx = max(0, min(target_w - 1, round(x * sx)))
        ny = max(0, min(target_h - 1, round(y * sy)))
        nw = max(1, min(target_w - nx, round(w * sx)))
        nh = max(1, min(target_h - ny, round(h * sy)))
        out.append((nx, ny, nw, nh))
    return out


def _is_portrait(value: object) -> bool:
    """Tolerant truthy-check for the orientation field.

    The switch input stores a real ``bool``, but older configs (and
    raw form POSTs that bypassed the coercer) may have left a string
    like ``"portrait"`` / ``"on"`` / ``"true"`` on disk."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("portrait", "on", "true", "1", "yes")
    return False


def _int_or(value: object, default: int) -> int:
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default
