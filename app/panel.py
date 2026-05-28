"""Panel-dimension resolution.

Pages no longer carry their own panel dims. The panel comes from the
app-wide ``app`` settings section so that swapping panels (or rotating
a single panel) updates every saved page without per-page edits.

Resolution order:

1. ``app.panel_preset`` (e.g. ``inky_13_3``) — if it's a known preset,
   take its native landscape dims.
2. Otherwise (``custom`` or unknown): fall back to ``app.panel_w`` /
   ``app.panel_h``.
3. If ``app.panel_orientation`` is ``portrait``, swap width and height.

A Page can still set its own ``panel`` (Pydantic field stays optional)
to override — useful if you ever want one dashboard at a different
size — but the UI doesn't expose that knob for v1.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

from math import gcd
from typing import TYPE_CHECKING, Any

from app.state.page_store import Panel
from app.state.settings_store import SettingsStore

if TYPE_CHECKING:
    from app.device_loader import Device, DeviceRegistry
    from app.state.page_store import Page

# (native_landscape_w, native_landscape_h) per panel. Orientation is
# applied separately so users can mount any panel landscape or portrait
# without us shipping double the entries.
PANEL_PRESETS: dict[str, tuple[int, int]] = {
    "inky_13_3": (1600, 1200),  # Pimoroni Inky Impression 13.3" (Spectra 6)
    "inky_7_3": (800, 480),  # Pimoroni Inky Impression 7.3" (Spectra 6)
    "inky_5_7": (600, 448),  # Pimoroni Inky Impression 5.7" (7-colour legacy)
    "inky_4": (640, 400),  # Pimoroni Inky Impression 4" (Spectra 6)
    "waveshare_e6_7_5": (800, 480),  # Waveshare E6 7.5"
}

PANEL_PRESET_CHOICES: list[dict[str, str]] = [
    {"value": "inky_13_3", "label": 'Inky Impression 13.3" — 1600x1200'},
    {"value": "inky_7_3", "label": 'Inky Impression 7.3" — 800x480'},
    {"value": "inky_5_7", "label": 'Inky Impression 5.7" — 600x448'},
    {"value": "inky_4", "label": 'Inky Impression 4" — 640x400'},
    {"value": "waveshare_e6_7_5", "label": 'Waveshare E6 7.5" — 800x480'},
    {"value": "custom", "label": "Custom (set width + height below)"},
]

DEFAULT_PRESET: str = "inky_13_3"


def resolve_settings_panel(settings: SettingsStore) -> Panel:
    """Compute the panel dims from the app settings section."""
    app = settings.get_section("app")
    preset = str(app.get("panel_preset") or DEFAULT_PRESET)
    if preset in PANEL_PRESETS:
        w, h = PANEL_PRESETS[preset]
    else:
        # 'custom' or any value the user typed in by hand falls back to
        # the explicit w/h fields. Defaults match Inky 13.3" so a fresh
        # install with no settings still works.
        w = _int_or(app.get("panel_w"), 1600)
        h = _int_or(app.get("panel_h"), 1200)
    if _is_portrait(app.get("panel_orientation")):
        w, h = h, w
    return Panel(w=max(1, w), h=max(1, h))


def resolve_page_panel(page_panel: Panel | None, settings: SettingsStore) -> Panel:
    """Resolve the panel from raw page-panel dims + settings.

    Multi-head dispatch lives in ``resolve_panel_for_page`` — use that
    when you have the full Page (it can look up the device's declared
    panel). This entry point is kept for callers that only have the
    optional Panel field handy."""
    if page_panel is not None:
        return page_panel
    return resolve_settings_panel(settings)


def _device_panel(device: Device) -> Panel | None:
    """Panel for a single device, or None if it declares no panel block."""
    block = device.panel
    if block is None:
        return None
    return Panel(
        w=int(block["w"]),
        h=int(block["h"]),
        flip=is_flipped_orientation(block.get("orientation")),
        gamut=str(block.get("gamut") or "waveshare_e6"),
        underscan=max(0, int(block.get("underscan") or 0)),
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
        panel = _device_panel(device)
        if panel is not None:
            out.append((device, panel))
    return out


def resolve_panel_for_page(
    page: Page,
    devices: DeviceRegistry | None,
    settings: SettingsStore,
) -> Panel:
    """The page's primary panel — for single-panel contexts (the editor's
    layout grid, the default compose render). Uses the first targeted
    device's panel, else ``page.panel``, else the global settings panel."""
    panels = _selected_device_panels(page, devices)
    if panels:
        return panels[0][1]
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
    — render at the virtual panel and fan out to every renderer."""
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
    devices — the editor renders a preview card for each. Same aspect =
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

    Pure function — no Cell objects, no I/O — so this is cheap to call
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
