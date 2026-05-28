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

from typing import TYPE_CHECKING

from app.state.page_store import Panel
from app.state.settings_store import SettingsStore

if TYPE_CHECKING:
    from app.device_loader import DeviceRegistry
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


def resolve_panel_for_page(
    page: Page,
    devices: DeviceRegistry | None,
    settings: SettingsStore,
) -> Panel:
    """Pick the panel for a page using the multi-head resolution chain:

    1. If ``page.device_id`` names a loaded device that declares a panel
       block, use those dims (multi-head install — each page lives on
       its assigned device).
    2. Else fall back to ``page.panel`` if explicitly set.
    3. Else fall back to the global settings panel (legacy / single-head
       behaviour, unchanged from v0.1)."""
    if page.device_id and devices is not None:
        device = devices.devices.get(page.device_id)
        if device is not None:
            block = device.panel
            if block is not None:
                return Panel(
                    w=int(block["w"]),
                    h=int(block["h"]),
                    flip=is_flipped_orientation(block.get("orientation")),
                )
    return resolve_page_panel(page.panel, settings)


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
