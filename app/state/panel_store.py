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


class Element(BaseModel):
    """One placed element on the canvas: a widget instance rendered as one of
    its declared fragments, at an absolute box. ``z`` is implicit in list order
    (paint order, first painted first).

    Each element is its own widget instance with its own ``options``, so two
    elements can be the same widget configured differently (weather for two
    cities), and ``fragment`` selects which part of the widget paints.
    """

    id: str
    # Plugin id whose render() paints this element. Empty = an unassigned box
    # (placed but not yet pointed at a widget).
    widget: str = ""
    # Which declared fragment to paint; "full" is the whole widget. Passed to
    # the widget's render() as ``ctx.fragment``.
    fragment: str = "full"
    # Per-instance widget config, matching the widget's ``cell_options`` shape;
    # resolved via ``_resolved_options`` and handed to ``fetch()``.
    options: dict[str, Any] = Field(default_factory=dict)
    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)
    w: int = Field(default=1, gt=0)
    h: int = Field(default=1, gt=0)
    # Per-element dither opt-out (issue #86): flat packs nearest-colour.
    dither: bool = True
    visible: bool = True
    locked: bool = False
    group: str | None = None


class CanvasPage(BaseModel):
    """A canvas document: a fixed artboard plus its freely-placed elements."""

    id: str
    name: str = "Untitled Panel"
    w: int = Field(default=600, gt=0)
    h: int = Field(default=400, gt=0)
    # Device instances this canvas is sent to.
    device_ids: list[str] = Field(default_factory=list)
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
