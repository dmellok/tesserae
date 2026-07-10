"""File-backed store for saved dashboards.

A page is a Pydantic model, a panel grid plus N positioned cells, each cell
naming a plugin and carrying its options. The store is a JSON file at
``data/core/pages.json`` keyed by page id.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class Panel(BaseModel):
    """Panel dimensions in composition orientation (the panel's mounted form).

    ``w``/``h`` are the canvas the composer renders at, already in the
    chosen aspect (portrait = tall). Each renderer maps that canvas onto
    its client's fixed native buffer. ``flip`` adds a 180° turn for an
    upside-down physical mount, the only mount detail the renderer can't
    infer from aspect alone.

    ``gamut`` is the physical panel's colour gamut; the .bin packer keys
    its palette + nibble LUT off it (``waveshare_e6`` default, or
    ``inky_7colour`` for the 7-colour ACeP Inky Impression). Irrelevant to
    the PNG path (the on-device inky library projects its own gamut).

    ``underscan`` insets the rendered frame by N pixels on every edge so
    content clears a physical mat / bezel covering the screen edge. The
    border sits under the mat. Applied per-device in the renderers."""

    w: int = Field(..., gt=0)
    h: int = Field(..., gt=0)
    flip: bool = False
    # v0.69.16: some panels (PicPak 4-colour BWRY, notably) scan their
    # rows bottom-to-top at the hardware level. The firmware streams
    # bytes straight to SPI, so the renderer has to flip rows vertically
    # before packing or the image lands upside down. Distinct from
    # ``flip`` (a full 180° rotation for upside-down mounts): ``vflip``
    # only reverses rows, columns stay put. Set via the device manifest;
    # ``False`` for every panel Tesserae shipped support for pre-v0.69.16.
    vflip: bool = False
    gamut: str = "waveshare_e6"
    underscan: int = Field(default=0, ge=0)
    # Firmware-native row stride. The panel hardware fixes its (w, h)
    # regardless of how the user has the composition oriented, the
    # renderer must pack at these dims (firmware streams straight to
    # SPI / the device's display buffer). Optional: populated from
    # PANEL_PRESETS or a device manifest's ``native_w``/``native_h``
    # block at resolution time. Renderers fall back to (w, h) when
    # absent, matching pre-v0.19.19 behaviour for custom / unknown
    # panels. Not persisted on pages, pages serialize their own
    # ``panel`` optional override but native dims are a hardware fact,
    # not a per-page choice.
    native_w: int | None = Field(default=None, gt=0)
    native_h: int | None = Field(default=None, gt=0)


class Cell(BaseModel):
    """One positioned widget. Coordinates are in panel pixels.

    ``plugin`` is optional so the editor can apply a layout template
    (which creates positioned-but-unassigned cells) before the user
    picks which widget renders into each slot. The composer skips
    cells where ``plugin`` is None / empty rather than 500-ing.

    ``theme`` is an optional per-cell override of the page-level theme.
    When set, the composer emits ``data-theme="<id>"`` on the cell
    element so the Spectra semantic tokens (--bg / --surface / accents)
    rebind for just that subtree. ``None`` (the default) inherits from
    the page.

    ``style`` is the equivalent per-cell override for the typographic
    style axis (``data-style``). Themes and styles are orthogonal: a
    cell may override one without touching the other.
    """

    id: str
    plugin: str | None = None
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    w: int = Field(..., gt=0)
    h: int = Field(..., gt=0)
    theme: str | None = None
    style: str | None = None
    font: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    # Per-cell content zoom. Inverse-sized at render time: the widget
    # paints into a 1/zoom virtual container that's transform-scaled back
    # up to the cell box, so text/icons grow without breaking layout. The
    # slider in the editor exposes 0.7–2.0; the wider 0.5–3.0 envelope is
    # the explicit-JSON safety net.
    zoom: float = Field(default=1.0, ge=0.5, le=3.0)
    # v0.71.x per-cell padding override (r/eink launch feedback). None
    # means "inherit the page-level gap"; an integer means "use this
    # value on all four inner edges", ignoring the gap and the
    # ``render.full_bleed`` manifest flag. Clamped to the same 0..80
    # envelope the page-level corner-radius slider uses so the UI
    # doesn't need a second range.
    padding_override: int | None = Field(default=None, ge=0, le=80)
    # v0.74.x per-cell dither override (issue #86). None means "inherit
    # the widget's ``render.dither`` manifest hint" (the default, so
    # existing pages are unchanged). ``"none"`` forces this cell onto the
    # flat nearest-colour path; ``"auto"`` forces the frame's dither even
    # when the widget's manifest opted out. Consumed by
    # ``app.dither_regions.regions_from_page``.
    dither: str | None = None


class Page(BaseModel):
    """A saved dashboard.

    ``panel`` is optional: when None, the page renders at the panel
    dims from the app settings (see ``app.panel.resolve_page_panel``),
    OR at the panel dims of the device(s) it targets (multi-head).

    ``device_ids`` ties the page to one or more devices. When non-empty:

    * the editor sizes the layout against those devices' panels, one
      preview per distinct aspect ratio
    * the push pipeline renders once per distinct panel and fans out
      each frame only to that panel's devices' renderers

    Empty means "no specific home", the page uses the global settings
    panel and fans out to every loaded renderer (legacy single-head).
    """

    id: str
    name: str
    panel: Panel | None = None
    device_ids: list[str] = Field(default_factory=list)
    cells: list[Cell] = Field(default_factory=list)
    font: str | None = None
    # Spectra theme id, picks one of the self-contained semantic blocks
    # defined in static/style/spectra-tokens.css (light, dark, high-contrast,
    # sepia, nord, cool-gray, bauhaus, destijl, brutalist). Renders as
    # ``data-theme=<id>`` on the composer body; unknown ids fall back to
    # the :root defaults (light).
    theme: str = "light"
    # Spectra style id, the orthogonal typography/density/shape axis defined
    # in static/style/spectra-styles.css (standard, display, editorial, mono,
    # elegant, condensed, bauhaus, destijl, brutalist). Renders as
    # ``data-style=<id>`` on the composer body; unknown ids fall back to
    # ``standard``. The style controls font + scale + edges; the theme controls
    # colour, any pair composes.
    style: str = "standard"
    gap: int = 0
    corner_radius: int = 0
    # Default matting is white. Older pages stored before this default
    # change have an empty string; the composer treats both as the
    # bleed (empty falls back to var(--bg) for theme-following, the
    # explicit default lays down white on new pages so the editor's
    # colour picker shows a sensible starting value rather than the
    # macro fallback of black).
    bleed_color: str = "#ffffff"
    icon: str | None = None
    # v0.71.0: page-level status bar. When enabled, an auto-managed
    # status_bar cell sits at (0, 0, panel.w, STATUS_BAR_HEIGHT_PX) at
    # the top of the layout; other cells are shifted / rescaled to
    # fit into the remaining vertical space. ``status_bar_cell_id``
    # points to the auto-managed cell so we can find + rescale + remove
    # it on toggle-off without confusing it with a user-added
    # tesserae_status widget placed elsewhere.
    status_bar_enabled: bool = False
    status_bar_cell_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_device_id(cls, data: Any) -> Any:
        """Back-compat: a pre-multi-head page stored a single ``device_id``.
        Fold it into ``device_ids`` on load so old pages.json keeps working."""
        if isinstance(data, dict) and "device_ids" not in data:
            legacy = data.get("device_id")
            data = {**data, "device_ids": [legacy] if legacy else []}
            data.pop("device_id", None)
        return data

    @field_validator("icon", mode="before")
    @classmethod
    def _strip_icon_prefix(cls, value: Any) -> Any:
        """Store Phosphor icon names bare (e.g. ``house``). Templates add
        the ``ph ph-`` class prefix themselves, so a stored ``ph-house``
        would render as ``ph-ph-house`` (no icon). Normalise on the way
        in, fixes legacy data on load and any future prefixed input."""
        if isinstance(value, str):
            cleaned = value.strip().removeprefix("ph-")
            return cleaned or None
        return value


class PageStore:
    """Thread-safe, file-backed dictionary of pages.

    The file is rewritten whole on every save, pages are small (a handful of
    KB at most) and human-editable. No journaling; the OS-atomic rename keeps
    the file consistent if the process dies mid-write.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._pages: dict[str, Page] = {}
        # Listeners fire after every save / delete. HA discovery uses
        # this to refresh its per-page button entities. Exceptions are
        # logged and swallowed.
        self._listener_lock = threading.Lock()
        self._listeners: list[Callable[[], None]] = []
        self._load()

    def add_listener(self, callback: Callable[[], None]) -> None:
        with self._listener_lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        with self._listener_lock, contextlib.suppress(ValueError):
            self._listeners.remove(callback)

    def _notify(self) -> None:
        with self._listener_lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb()
            except Exception:
                logger.exception("PageStore listener %r raised", cb)

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{self._path} must contain a JSON object keyed by page id")
        self._pages = {pid: Page.model_validate(p) for pid, p in raw.items()}

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = {
            pid: page.model_dump(mode="json", exclude_none=True)
            for pid, page in self._pages.items()
        }
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, page_id: str) -> Page | None:
        with self._lock:
            return self._pages.get(page_id)

    def list(self) -> list[Page]:
        with self._lock:
            return list(self._pages.values())

    def save(self, page: Page) -> None:
        with self._lock:
            self._pages[page.id] = page
            self._flush()
        self._notify()

    def delete(self, page_id: str) -> bool:
        with self._lock:
            existed = page_id in self._pages
            self._pages.pop(page_id, None)
            if existed:
                self._flush()
        if existed:
            self._notify()
        return existed
