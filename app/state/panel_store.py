"""Persistence for Panels canvas documents (issue #60).

A canvas ``Panel`` (distinct from a device's physical panel) is a fixed-size
artboard holding absolutely-positioned visual ``Element`` s, each optionally
bound to a widget data field. This is the storage layer for the experimental
canvas editor; it mirrors :class:`app.state.page_store.PageStore` (whole-file
JSON, atomic rename, thread-locked) since these documents are small and
human-inspectable.

Kept deliberately separate from the grid ``Page`` model: canvas mode is
additive and opt-in, and its document shape (elements, not cells) is
different enough that folding it into ``Page`` would muddy both.

Field names are snake_case and shared verbatim with the editor's client-side
element objects, so there's no alias/mapping layer between the JSON on disk
and the JS in the browser.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# Element types the palette offers. The renderer (client + future server
# compose) switches on this. Unknown types are rejected at validation so a
# malformed doc can't smuggle an unhandled type into the render path.
ELEMENT_TYPES = (
    "big",
    "small",
    "text",
    "icon",
    "spark",
    "bar",
    "chip",
    "progress",
    "list",
    "image",
    "shape",
)

# The fixed Spectra-6 ink palette elements may use (the editor offers no
# arbitrary RGB). Default is ink (near-black). Not enforced as an enum so a
# future gamut can widen it without a migration, the renderer dithers
# whatever lands here onto the device palette.
DEFAULT_INK = "#1B1A16"


class Element(BaseModel):
    """One placed visual element. Coordinates are artboard pixels; ``z`` is
    implicit in list order (paint order, first painted first)."""

    id: str
    type: str = Field(..., pattern=r"^[a-z]+$")
    name: str = ""
    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)
    w: int = Field(default=1, gt=0)
    h: int = Field(default=1, gt=0)
    # Data binding as a ``<widget_key>.<field>`` path, or None for static /
    # decorative elements (text with literal content, shapes).
    binding: str | None = None
    text: str = ""
    prefix: str = ""
    suffix: str = ""
    upper: bool = False
    weight: int = 700
    color: str = DEFAULT_INK
    align: str = "left"
    font_size: int = 24
    icon: str = ""
    # Dithering defaults ON (issue #86): flat is a deliberate opt-out, since
    # dithering renders anti-aliased text + shading as tone on low-palette
    # panels. When False the element's region packs nearest-colour.
    dither: bool = True
    visible: bool = True
    locked: bool = False
    group: str | None = None
    # Shape-only knobs; ignored by other types.
    shape_kind: str = "rect"
    mode: str = "fill"
    stroke: int = 2
    radius: int = 0

    def type_is_known(self) -> bool:
        return self.type in ELEMENT_TYPES


class CanvasPage(BaseModel):
    """A canvas document: a fixed artboard plus its elements and the widget
    data sources currently in play."""

    id: str
    name: str = "Untitled Panel"
    w: int = Field(default=600, gt=0)
    h: int = Field(default=400, gt=0)
    # Active widget data sources (catalog keys) whose fields are bindable.
    sources: list[str] = Field(default_factory=list)
    els: list[Element] = Field(default_factory=list)


class CanvasStore:
    """Thread-safe, file-backed dict of canvas documents. Whole-file JSON
    with an atomic rename, mirroring :class:`PageStore`."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._docs: dict[str, CanvasPage] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{self._path} must contain a JSON object keyed by canvas id")
        self._docs = {cid: CanvasPage.model_validate(doc) for cid, doc in raw.items()}

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload: dict[str, Any] = {
            cid: doc.model_dump(mode="json") for cid, doc in self._docs.items()
        }
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, canvas_id: str) -> CanvasPage | None:
        with self._lock:
            return self._docs.get(canvas_id)

    def list(self) -> list[CanvasPage]:
        with self._lock:
            return list(self._docs.values())

    def save(self, doc: CanvasPage) -> None:
        with self._lock:
            self._docs[doc.id] = doc
            self._flush()

    def delete(self, canvas_id: str) -> bool:
        with self._lock:
            existed = self._docs.pop(canvas_id, None) is not None
            if existed:
                self._flush()
        return existed

    def __len__(self) -> int:
        with self._lock:
            return len(self._docs)
