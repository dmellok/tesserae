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

from app.state.page_store import Panel
from app.state.settings_store import SettingsStore

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
    """Resolve the panel for a page: explicit page-level panel wins
    (legacy / advanced), otherwise pull from settings."""
    if page_panel is not None:
        return page_panel
    return resolve_settings_panel(settings)


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
