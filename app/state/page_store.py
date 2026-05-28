"""File-backed store for saved dashboards.

A page is a Pydantic model — a panel grid plus N positioned cells, each cell
naming a plugin and carrying its options. The store is a JSON file at
``data/core/pages.json`` keyed by page id.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class Panel(BaseModel):
    """Panel dimensions in composition orientation (the panel's mounted form)."""

    w: int = Field(..., gt=0)
    h: int = Field(..., gt=0)


class Cell(BaseModel):
    """One positioned widget. Coordinates are in panel pixels.

    ``plugin`` is optional so the editor can apply a layout template
    (which creates positioned-but-unassigned cells) before the user
    picks which widget renders into each slot. The composer skips
    cells where ``plugin`` is None / empty rather than 500-ing."""

    id: str
    plugin: str | None = None
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    w: int = Field(..., gt=0)
    h: int = Field(..., gt=0)
    theme: str | None = None
    font: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    palette_overrides: dict[str, str] | None = None

    @field_validator("palette_overrides")
    @classmethod
    def _validate_palette_overrides(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        for k, v in value.items():
            if not isinstance(v, str) or not v.startswith("#"):
                raise ValueError(f"palette override {k!r}={v!r} must be a hex string")
        return value


class Page(BaseModel):
    """A saved dashboard.

    ``panel`` is optional: when None, the page renders at the panel
    dims from the app settings (see ``app.panel.resolve_page_panel``),
    OR at the panel dims declared by the device named in ``device_id``
    when that's set (multi-head installs).

    ``device_id`` ties the page to a specific device. When set:

    * the editor sizes the layout against that device's declared panel
    * the push pipeline only fires renderers that target that device
      (so a "kitchen Inky" page never lands on the "hallway ESP32")

    Unset means "no specific home" — the page uses the global settings
    panel and fans out to every loaded renderer, matching the legacy
    single-head behaviour.
    """

    id: str
    name: str
    panel: Panel | None = None
    device_id: str | None = None
    cells: list[Cell] = Field(default_factory=list)
    theme: str | None = None
    font: str | None = None
    gap: int = 0
    corner_radius: int = 0
    bleed_color: str = "#ffffff"
    icon: str | None = None


class PageStore:
    """Thread-safe, file-backed dictionary of pages.

    The file is rewritten whole on every save — pages are small (a handful of
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
        raw = json.loads(self._path.read_text())
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
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
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
