"""The items a lineup step's dashboard shows one per visit, and how to ask
for a particular one.

A dashboard only has choosable items when exactly one of its widgets (a grid
cell, or a widget element on a canvas) walks a list and that widget can be
pinned to one entry through an option:

* ``picture_gallery`` walks its folder's photos; ``file`` (not a manifest
  option, read by the widget's ``fetch``) pins a photo by name.
* Any other widget can join by declaring ``"lineups": {"pin_option": "<name>"}``
  in its ``plugin.json`` and giving its server module
  ``lineup_items(options) -> list[{"key", "label", "version"?}]``. Choosing an
  item renders the dashboard with that option set to the item's key.

A second mode, "draw next", is for widgets whose items cannot be named but whose
output differs from one render to the next, so the arrows draw the dashboard
again instead of choosing. A widget declares it with ``"lineups": {"draw":
true}``; the Google and iCloud album widgets (separate repositories, which pick
a random photo on every fetch) are known by id, so they work as they are. A
draw sets two hidden options a widget may read: ``_draw`` (a per-drawing token)
and ``_browse`` (how many turns past the current one a widget that keeps its own
turn should show, read-only). A sequential album moves its real cursor on every
fetch, a preview included, so a draw sets ``mode`` to random and never costs the
frame its place.

A draw's key is ``draw:<nonce>:<n>``: the nonce makes each drawing its own
cached file, ``n`` is how many turns past the current one it looks.

A widget that is not installed, or declares nothing, shows one thing and reports
one item, so the Lineups page draws no arrows for it.

``pin_item`` returns the page definition with the chosen item (or the draw's
options) written into that placement's options. The composer applies it for
``/compose/<page>?item=`` (a preview or a push render); nothing is saved to the
page.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flask import current_app, url_for

from app.lineups_view import ItemInfo

#: Items listed for a multi-item step (a gallery folder): enough for the peek
#: grid, without walking a folder of thousands of photos into the page.
MAX_STEP_ITEMS = 60

#: folder -> (stamp, total count, first MAX_STEP_ITEMS names). The stamp is
#: the folder's mtime (adding or removing a photo changes it), or a one-minute
#: bucket when the folder path can't be resolved, so a polling page doesn't
#: list a large folder on every refresh.
_GALLERY_CACHE: dict[str, tuple[float, int, tuple[str, ...]]] = {}


@dataclass(frozen=True)
class StepItems:
    """What a step's dashboard can show: the widget cell, the option that pins
    one item, the full count and the listed items (at most MAX_STEP_ITEMS)."""

    cell_id: str
    option: str
    count: int
    items: tuple[ItemInfo, ...]
    #: "pick": items are named and listed; "draw": no list, the arrows draw again
    mode: str = "pick"


def _plugin_module(plugin_id: str) -> Any:
    reg = current_app.config.get("PLUGIN_REGISTRY")
    plugin = reg.get(plugin_id) if reg is not None else None
    return getattr(plugin, "server_module", None) if plugin is not None else None


def _gallery_files(module: Any, folder: str) -> tuple[int, tuple[str, ...]] | None:
    stamp: float = float(int(time.time() // 60))
    resolve = getattr(module, "_folder_path", None)
    data_dir = getattr(module, "_data_dir", None)
    if callable(resolve) and callable(data_dir):
        try:
            path = resolve(folder, data_dir())
            if path is not None:
                stamp = -float(path.stat().st_mtime_ns)
        except Exception:
            pass
    cached = _GALLERY_CACHE.get(folder)
    if cached is not None and cached[0] == stamp:
        return cached[1], cached[2]
    lister = getattr(module, "list_folder_files", None)
    if not callable(lister):
        return None
    try:
        files = [str(f) for f in lister(folder)]
    except Exception:
        return None
    entry = (stamp, len(files), tuple(files[:MAX_STEP_ITEMS]))
    _GALLERY_CACHE[folder] = entry
    return entry[1], entry[2]


def _gallery_items(cell: Any) -> StepItems | None:
    folder = str(cell.options.get("folder") or "")
    module = _plugin_module("picture_gallery")
    listed = _gallery_files(module, folder) if module is not None else None
    if listed is None or listed[0] <= 1:
        return None
    count, names = listed
    version = getattr(module, "thumb_version", None)
    items: list[ItemInfo] = []
    for i, name in enumerate(names):
        ver = ""
        if callable(version):
            try:
                ver = str(version(folder, name))
            except Exception:
                ver = ""
        try:
            thumb = url_for(
                "picture_gallery_admin.serve_thumbnail",
                folder=folder or "_root",
                filename=name,
                v=ver or None,
            )
        except Exception:
            thumb = ""
        items.append(ItemInfo(i, name, thumb, key=name, version=ver))
    return StepItems(str(cell.id), "file", count, tuple(items))


def _draw_items(cell: Any) -> StepItems | None:
    return StepItems(str(cell.id), "_draw", 0, (), mode="draw")


def _declared_items(plugin_id: str, option: str) -> Callable[[Any], StepItems | None]:
    """A provider for a widget that declared ``lineups.pin_option`` and whose
    server module lists its items with ``lineup_items(options)``."""

    def provide(cell: Any) -> StepItems | None:
        lister = getattr(_plugin_module(plugin_id), "lineup_items", None)
        if not callable(lister) or str(cell.options.get(option) or "").strip():
            return None  # no list to offer, or the cell is already pinned to one entry
        try:
            listed = [dict(x) for x in lister(dict(cell.options))]
        except Exception:
            return None
        if len(listed) <= 1:
            return None
        items = tuple(
            ItemInfo(
                i,
                str(x.get("label") or x["key"]),
                "",
                key=str(x["key"]),
                version=str(x.get("version") or ""),
            )
            for i, x in enumerate(listed[:MAX_STEP_ITEMS])
        )
        return StepItems(str(cell.id), option, len(listed), items)

    return provide


#: Widgets that pick a random item on every fetch but live in their own
#: repositories, so they cannot declare it themselves.
_KNOWN_DRAW_WIDGETS = frozenset({"picture_google_album", "picture_apple_album"})


def _provider(plugin_id: str) -> Callable[[Any], StepItems | None] | None:
    """How ``plugin_id`` offers items, or None. An uninstalled widget offers
    nothing, whatever the page says."""
    reg = current_app.config.get("PLUGIN_REGISTRY")
    plugin = reg.get(plugin_id) if reg is not None else None
    if plugin is None:
        return None
    if plugin_id == "picture_gallery":
        return _gallery_items
    declared = (getattr(plugin, "manifest", None) or {}).get("lineups") or {}
    if not isinstance(declared, dict):
        declared = {}
    pin = declared.get("pin_option")
    if isinstance(pin, str) and pin:
        return _declared_items(plugin_id, pin)
    if declared.get("draw") is True or plugin_id in _KNOWN_DRAW_WIDGETS:
        return _draw_items
    return None


#: A draw's key; the nonce is the page's own random token, ``n`` the turns ahead.
DRAW_KEY = re.compile(r"draw:([0-9a-f]{8,16}):(\d{1,2})")


def _placements(page: Any) -> list[tuple[Callable[[Any], StepItems | None], Any]]:
    """Every list-walking widget on ``page`` as ``(provider, placement)``. Both
    a grid cell and a canvas element carry the ``id`` and ``options`` a provider
    reads; a canvas's other element kinds only borrow a widget's data."""
    named = [
        (str(c.plugin), c) for c in getattr(page, "cells", None) or [] if getattr(c, "plugin", None)
    ]
    canvas = getattr(page, "canvas", None)
    named += [
        (str(e.widget), e)
        for e in getattr(canvas, "els", None) or []
        if getattr(e, "kind", "widget") == "widget" and getattr(e, "widget", None)
    ]
    found = []
    for plugin_id, placement in named:
        provider = _provider(plugin_id)
        if provider is not None:
            found.append((provider, placement))
    return found


def step_choices(page: Any) -> StepItems | None:
    """The choosable items of ``page``, or None: exactly one placement may walk
    a list, otherwise which one a pin would apply to is ambiguous."""
    placements = _placements(page)
    if len(placements) != 1:
        return None
    provider, placement = placements[0]
    return provider(placement)


def step_items(page: Any) -> tuple[int, tuple[ItemInfo, ...]]:
    """``(count, items)`` for the Lineups view: one item unless the dashboard
    walks a list."""
    found = step_choices(page)
    return (found.count, found.items) if found is not None and found.mode == "pick" else (1, ())


def step_draws(page: Any) -> bool:
    """True when the arrows draw the dashboard again rather than choose."""
    found = step_choices(page)
    return found is not None and found.mode == "draw"


def find_item(page: Any, key: str) -> ItemInfo | None:
    """The listed item with this key, or None (unknown, or not choosable)."""
    found = step_choices(page)
    if found is None or not key:
        return None
    if found.mode == "draw":
        m = DRAW_KEY.fullmatch(key)
        return ItemInfo(int(m.group(2)), "", key=key) if m else None
    return next((it for it in found.items if it.key == key), None)


def pin_item(page: Any, page_dict: dict[str, Any], key: str) -> dict[str, Any] | None:
    """``page_dict`` with the widget's pin option set to ``key``, or None when
    ``key`` is not one of the page's items."""
    found = step_choices(page)
    if found is None or find_item(page, key) is None:
        return None
    options: dict[str, Any] = {found.option: key}
    if found.mode == "draw":
        m = DRAW_KEY.fullmatch(key)
        assert m is not None
        options = {"_draw": m.group(1), "_browse": int(m.group(2))}

    def pinned(placements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for one in placements:
            if str(one.get("id")) == found.cell_id:
                merged = {**(one.get("options") or {}), **options}
                # A sequential album moves its real cursor on every fetch, a
                # preview too: draw at random instead of costing the frame a photo.
                if found.mode == "draw" and merged.get("mode") == "sequential":
                    merged["mode"] = "random"
                one = {**one, "options": merged}
            out.append(one)
        return out

    out = {**page_dict, "cells": pinned(page_dict.get("cells") or [])}
    canvas = page_dict.get("canvas")
    if isinstance(canvas, dict):
        out["canvas"] = {**canvas, "els": pinned(canvas.get("els") or [])}
    return out
