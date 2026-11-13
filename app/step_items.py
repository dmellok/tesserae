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

A draw widget may also report how many items it draws from, so the lineup
editor can say "212 photos" instead of "draw": give its server module
``lineup_item_count(options) -> int | None`` (optional; None or an error means
unknown). A widget may name its items for the editor with ``"lineups": {"noun":
"papers"}`` (plural; "items" when absent, "photos" for the gallery).

A widget that is not installed, or declares nothing, shows one thing and reports
one item, so the Lineups page draws no arrows for it.

``list_items`` pages through every item (the view-model stops at
MAX_STEP_ITEMS) with an optional text filter, for the lineup editor's grid;
``item_summary`` is the count and pin the editor's row shows; ``pin_page``
pins a declared widget to one item, or unpins it, on the saved page.

``pin_item`` returns the page definition with the chosen item (or the draw's
options) written into that placement's options. The composer applies it for
``/compose/<page>?item=`` (a preview or a push render); nothing is saved to the
page.
"""

from __future__ import annotations

import inspect
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from flask import current_app, has_request_context, request, url_for

from app.lineups_view import ItemInfo

#: Items listed for a multi-item step (a gallery folder): enough for the peek
#: grid, without walking a folder of thousands of photos into the page.
MAX_STEP_ITEMS = 60

#: Most items one page of the lineup editor's item grid holds.
MAX_PAGE_ITEMS = 120

#: folder -> (stamp, total count, every name). The stamp is
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
    #: "pick": items are named and listed; "draw": no list, the arrows draw again;
    #: "empty": a widget that lists its items listed none
    mode: str = "pick"


def _plugin_module(plugin_id: str) -> Any:
    reg = current_app.config.get("PLUGIN_REGISTRY")
    plugin = reg.get(plugin_id) if reg is not None else None
    return getattr(plugin, "server_module", None) if plugin is not None else None


def _accepts_ctx(fn: Callable[..., Any]) -> bool:
    """True when ``fn`` takes a ``ctx`` keyword (or ``**kwargs``)."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        (p.name == "ctx" and p.kind is not inspect.Parameter.POSITIONAL_ONLY)
        or p.kind is inspect.Parameter.VAR_KEYWORD
        for p in params
    )


#: Most entries taken from one ``lineup_items`` listing; a widget that lists
#: more is cut here, so a runaway hook cannot fill the process.
MAX_HOOK_ITEMS = 5000

#: (widget, hook, options, ctx) -> (when, result). The editor and the Lineups
#: page ask the same question several times while drawing one page, and a
#: hook may be a network call, so an answer is kept for a few seconds.
_HOOK_CACHE: dict[str, tuple[float, Any]] = {}
_HOOK_CACHE_TTL_S = 20.0
_HOOK_CACHE_MAX = 128


def _call_hook(
    plugin_id: str, fn: Callable[..., Any], options: dict[str, Any], ctx: dict[str, Any] | None
) -> Any:
    """Call a widget's ``lineup_items`` / ``lineup_item_count`` hook; the
    render ``ctx`` goes only to a hook that declares it, so a widget written
    with one argument keeps working.

    The call runs inside the widget's capability scope, as its data fetch
    does, so a hook reaches only the hosts the widget declared. A listing is
    read at most ``MAX_HOOK_ITEMS`` entries deep, and an answer is reused for
    a few seconds."""
    import itertools
    import json

    from app.capabilities import capability_scope

    try:
        key: str | None = json.dumps(
            [plugin_id, getattr(fn, "__name__", ""), id(fn), options, ctx],
            sort_keys=True,
            default=str,
        )
    except (TypeError, ValueError):
        key = None
    now = time.monotonic()
    if key is not None:
        hit = _HOOK_CACHE.get(key)
        if hit is not None and now - hit[0] < _HOOK_CACHE_TTL_S:
            return hit[1]
    reg = current_app.config.get("PLUGIN_REGISTRY")
    plugin = reg.get(plugin_id) if reg is not None else None
    with capability_scope(getattr(plugin, "capabilities", None)):
        if ctx is not None and _accepts_ctx(fn):
            result = fn(dict(options), ctx=ctx)
        else:
            result = fn(dict(options))
        if result is not None and not isinstance(result, (int, float, str, bytes, dict)):
            # Read a listing here, inside the scope: it may be a generator
            # that fetches as it goes.
            result = list(itertools.islice(result, MAX_HOOK_ITEMS))
    if key is not None:
        if len(_HOOK_CACHE) >= _HOOK_CACHE_MAX:
            for stale in [
                k for k, (at, _r) in _HOOK_CACHE.items() if now - at >= _HOOK_CACHE_TTL_S
            ]:
                _HOOK_CACHE.pop(stale, None)
            while len(_HOOK_CACHE) >= _HOOK_CACHE_MAX:
                _HOOK_CACHE.pop(next(iter(_HOOK_CACHE)), None)
        _HOOK_CACHE[key] = (now, result)
    return result


def _item_ctx(page: Any, placement: Any, plugin_id: str, device_id: str | None) -> dict[str, Any]:
    """The ``ctx`` an item hook gets, built as the renderer builds a widget's:
    the widget's data dir, the cell's size on the target display, the
    placement's id and the display's panel. ``device_id`` None means the
    dashboard's first bound display."""
    from app.panel import device_panel, fit_cells_to_panel, resolve_panel_for_page

    reg = current_app.config.get("PLUGIN_REGISTRY")
    plugin = reg.get(plugin_id) if reg is not None else None
    devices = current_app.config.get("DEVICE_REGISTRY")
    settings = current_app.config.get("SETTINGS_STORE")
    target = device_id or next(iter(getattr(page, "device_ids", None) or []), None)
    panel = None
    try:
        device = devices.get(target) if devices is not None and target else None
        panel = device_panel(device) if device is not None else None
        if panel is None:
            panel = resolve_panel_for_page(page, devices, settings)  # type: ignore[arg-type]
    except Exception:
        panel = None
    panel_w = int(getattr(panel, "w", 0) or 0)
    panel_h = int(getattr(panel, "h", 0) or 0)
    cell_w = cell_h = 0
    cells = list(getattr(page, "cells", None) or [])
    try:
        idx = next(i for i, c in enumerate(cells) if c is placement)
        if panel_w and panel_h:
            coords = [(int(c.x), int(c.y), int(c.w), int(c.h)) for c in cells]
            strip = getattr(page, "status_bar_cell_id", None)
            top = next((i for i, c in enumerate(cells) if strip and c.id == strip), None)
            fitted = fit_cells_to_panel(coords, panel_w, panel_h, top_strip_index=top)
            cell_w, cell_h = int(fitted[idx][2]), int(fitted[idx][3])
        else:
            cell_w, cell_h = int(cells[idx].w), int(cells[idx].h)
    except Exception:
        # A canvas element (or anything unfittable) reports its own size.
        cell_w = int(getattr(placement, "w", 0) or 0)
        cell_h = int(getattr(placement, "h", 0) or 0)
    return {
        "data_dir": str(getattr(plugin, "data_dir", "") or ""),
        "cell_w": cell_w,
        "cell_h": cell_h,
        "placement_id": str(getattr(placement, "id", "") or ""),
        "device_id": target or None,
        "panel_w": panel_w,
        "panel_h": panel_h,
    }


def _with_script_root(path: str) -> str:
    """An app-relative path as the browser must ask for it: under Home Assistant
    ingress the app lives beneath a prefix (``request.script_root``), so a bare
    ``/plugins/...`` would resolve against the host's root and 404. Already
    prefixed paths are left alone."""
    if not has_request_context():
        return path
    root = request.script_root.rstrip("/")
    if not root or path == root or path.startswith(root + "/"):
        return path
    return root + path


def _safe_thumb(raw: Any) -> str:
    """A widget-supplied preview image URL, kept only when it is http(s) or an
    app-relative path (never ``//host`` or a script scheme)."""
    if not isinstance(raw, str):
        return ""
    url = raw.strip()
    if not url or len(url) > 2000 or any(ord(ch) < 32 or ch in '\\"<>' for ch in url):
        return ""
    if url.startswith("/") and not url.startswith("//"):
        return _with_script_root(url)
    parts = urlsplit(url)
    return url if parts.scheme in ("http", "https") and parts.netloc else ""


def _declared_infos(listed: list[dict[str, Any]], limit: int | None = None) -> list[ItemInfo]:
    return [
        ItemInfo(
            i,
            str(x.get("label") or x["key"]),
            _safe_thumb(x.get("thumb")),
            key=str(x["key"]),
            version=str(x.get("version") or ""),
        )
        for i, x in enumerate(listed[:limit])
        if x.get("key") not in (None, "")
    ]


def _gallery_files(module: Any, folder: str) -> tuple[int, tuple[str, ...]] | None:
    """``(count, the first MAX_STEP_ITEMS names)`` of a gallery folder."""
    listed = _gallery_all(module, folder)
    return None if listed is None else (listed[0], listed[1][:MAX_STEP_ITEMS])


def _gallery_all(module: Any, folder: str) -> tuple[int, tuple[str, ...]] | None:
    """``(count, every name)`` of a gallery folder, cached until it changes."""
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
    entry = (stamp, len(files), tuple(files))
    _GALLERY_CACHE[folder] = entry
    return entry[1], entry[2]


def _gallery_items(cell: Any, ctx: dict[str, Any] | None = None) -> StepItems | None:
    folder = str(cell.options.get("folder") or "")
    module = _plugin_module("picture_gallery")
    listed = _gallery_files(module, folder) if module is not None else None
    if listed is None or listed[0] <= 1:
        return None
    count, names = listed
    items = _gallery_item_infos(module, folder, list(enumerate(names)))
    return StepItems(str(cell.id), "file", count, tuple(items))


def _gallery_item_infos(module: Any, folder: str, named: list[tuple[int, str]]) -> list[ItemInfo]:
    version = getattr(module, "thumb_version", None)
    items: list[ItemInfo] = []
    for i, name in named:
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
    return items


def _draw_items(cell: Any, ctx: dict[str, Any] | None = None) -> StepItems | None:
    return StepItems(str(cell.id), "_draw", 0, (), mode="draw")


def _declared_items(
    plugin_id: str, option: str
) -> Callable[[Any, dict[str, Any] | None], StepItems | None]:
    """A provider for a widget that declared ``lineups.pin_option`` and whose
    server module lists its items with ``lineup_items(options)``."""

    def provide(cell: Any, ctx: dict[str, Any] | None = None) -> StepItems | None:
        lister = getattr(_plugin_module(plugin_id), "lineup_items", None)
        if not callable(lister) or str(cell.options.get(option) or "").strip():
            return None  # no list to offer, or the cell is already pinned to one entry
        try:
            listed = [dict(x) for x in _call_hook(plugin_id, lister, dict(cell.options), ctx)]
        except Exception:
            current_app.logger.warning("%s: lineup_items failed", plugin_id, exc_info=True)
            return None
        if not listed:
            # Nothing to list (a cold cache or filters that match nothing; a hook
            # cannot say which): not choosable, but not a plain single picture.
            return StepItems(str(cell.id), option, 0, (), mode="empty")
        if len(listed) == 1:
            return None
        items = tuple(_declared_infos(listed, MAX_STEP_ITEMS))
        return StepItems(str(cell.id), option, len(listed), items)

    return provide


#: Widgets that pick a random item on every fetch but live in their own
#: repositories, so they cannot declare it themselves.
_NOUN_RE = re.compile(r"[A-Za-z][A-Za-z '-]{0,31}")
_KNOWN_DRAW_WIDGETS = frozenset({"picture_google_album", "picture_apple_album"})


def _provider(plugin_id: str) -> Callable[[Any, dict[str, Any] | None], StepItems | None] | None:
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
        listed = _declared_items(plugin_id, pin)
        if declared.get("draw") is not True:
            return listed

        def list_or_draw(cell: Any, ctx: dict[str, Any] | None = None) -> StepItems | None:
            # Declares a list and a draw: with nothing to list, it draws at random.
            found = listed(cell, ctx)
            return _draw_items(cell, ctx) if found is not None and found.mode == "empty" else found

        return list_or_draw
    if declared.get("draw") is True or plugin_id in _KNOWN_DRAW_WIDGETS:
        return _draw_items
    return None


#: A draw's key; the nonce is the page's own random token, ``n`` the turns ahead.
DRAW_KEY = re.compile(r"draw:([0-9a-f]{8,16}):(\d{1,2})")


def named_placements(page: Any) -> list[tuple[str, Any]]:
    """Every widget on ``page`` as ``(plugin id, placement)``. Both a grid cell
    and a canvas element carry the ``id`` and ``options`` a provider reads; a
    canvas's other element kinds only borrow a widget's data."""
    named = [
        (str(c.plugin), c) for c in getattr(page, "cells", None) or [] if getattr(c, "plugin", None)
    ]
    canvas = getattr(page, "canvas", None)
    named += [
        (str(e.widget), e)
        for e in getattr(canvas, "els", None) or []
        if getattr(e, "kind", "widget") == "widget" and getattr(e, "widget", None)
    ]
    return named


def _placements(
    page: Any,
) -> list[tuple[str, Callable[[Any, dict[str, Any] | None], StepItems | None], Any]]:
    """Every list-walking widget on ``page`` as ``(provider, placement)``."""
    found = []
    for plugin_id, placement in named_placements(page):
        provider = _provider(plugin_id)
        if provider is not None:
            found.append((plugin_id, provider, placement))
    return found


def step_choices(page: Any, device_id: str | None = None) -> StepItems | None:
    """The choosable items of ``page``, or None: exactly one placement may walk
    a list, otherwise which one a pin would apply to is ambiguous."""
    placements = _placements(page)
    if len(placements) != 1:
        return None
    plugin_id, provider, placement = placements[0]
    return provider(placement, _item_ctx(page, placement, plugin_id, device_id))


def step_items(page: Any, device_id: str | None = None) -> tuple[int, tuple[ItemInfo, ...]]:
    """``(count, items)`` for the Lineups view: one item unless the dashboard
    walks a list."""
    found = step_choices(page, device_id)
    return (found.count, found.items) if found is not None and found.mode == "pick" else (1, ())


def step_draws(page: Any, device_id: str | None = None) -> bool:
    """True when the arrows draw the dashboard again rather than choose."""
    found = step_choices(page, device_id)
    return found is not None and found.mode == "draw"


_DRAW_COUNTS: dict[str, tuple[float, int]] = {}
_DRAW_COUNT_TTL_S = 300.0


def step_draw_count(page: Any, device_id: str | None = None) -> int:
    """How many items a draw widget says it draws from (its optional
    ``lineup_item_count``), or 0 when it does not say. Remembered for a few
    minutes: the Lineups page asks on every view and a count may be a network call."""
    import time

    key = str(getattr(page, "id", ""))
    now = time.monotonic()
    hit = _DRAW_COUNTS.get(key)
    if hit is not None and now - hit[0] < _DRAW_COUNT_TTL_S:
        return hit[1]
    found = list_items(page, limit=1, device_id=device_id)
    n = int(found.total) if found.mode == "draw" else 0
    _DRAW_COUNTS[key] = (now, n)
    return n


def step_empty(page: Any, device_id: str | None = None) -> bool:
    """True when the dashboard's widget lists its items and listed none."""
    found = step_choices(page, device_id)
    return found is not None and found.mode == "empty"


def step_fit(page: Any) -> bool:
    """True when the dashboard's pictures are letterboxed on the frame (a
    gallery set to fit), so item previews show them whole; otherwise a
    preview fills its box the way the frame shows it."""
    for plugin_id, placement in named_placements(page):
        if plugin_id == "picture_gallery":
            options = getattr(placement, "options", None) or {}
            return str(options.get("scale") or "fit") != "fill"
    return False


def find_item(page: Any, key: str, device_id: str | None = None) -> ItemInfo | None:
    """The listed item with this key, or None (unknown, or not choosable)."""
    found = step_choices(page, device_id)
    if found is None or not key:
        return None
    if found.mode == "draw":
        m = DRAW_KEY.fullmatch(key)
        return ItemInfo(int(m.group(2)), "", key=key) if m else None
    return next((it for it in found.items if it.key == key), None)


def pin_item(
    page: Any, page_dict: dict[str, Any], key: str, device_id: str | None = None
) -> dict[str, Any] | None:
    """``page_dict`` with the widget's pin option set to ``key``, or None when
    ``key`` is not one of the page's items."""
    found = step_choices(page, device_id)
    if found is None or find_item(page, key, device_id) is None:
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


# -- the lineup editor: every item, a page at a time --------------------------


@dataclass(frozen=True)
class ItemPage:
    """One page of a dashboard's items for the lineup editor.

    ``mode`` is "pick" (named items), "draw" (a fresh one each render) or
    "none"; ``total`` counts every item (0 when a draw widget can't say),
    ``matched`` those the query kept, and ``items`` the slice from ``offset``.
    ``pinnable`` marks a widget declaring ``lineups.pin_option``; ``pinned`` is
    the key the saved dashboard is pinned to ("" while it walks the list)."""

    mode: str
    total: int
    matched: int
    offset: int
    items: tuple[ItemInfo, ...]
    noun: str
    plugin: str = ""
    pinnable: bool = False
    pinned: str = ""
    pinned_label: str = ""
    fit: bool = False
    #: the widget lists its items (and is not pinned to one) but listed none
    empty: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "total": self.total,
            "matched": self.matched,
            "offset": self.offset,
            "noun": self.noun,
            "plugin": self.plugin,
            "pinnable": self.pinnable,
            "pinned": self.pinned,
            "pinned_label": self.pinned_label,
            "fit": self.fit,
            "empty": self.empty,
            "items": [
                {"index": it.index, "label": it.label, "key": it.key, "thumb_url": it.thumb_url}
                for it in self.items
            ],
        }


def _declared(plugin_id: str) -> dict[str, Any]:
    reg = current_app.config.get("PLUGIN_REGISTRY")
    plugin = reg.get(plugin_id) if reg is not None else None
    declared = (getattr(plugin, "manifest", None) or {}).get("lineups") if plugin else None
    return declared if isinstance(declared, dict) else {}


def _noun(plugin_id: str) -> str:
    if plugin_id == "picture_gallery":
        return "photos"
    # A manifest string that ends up in the editor's markup: letters only,
    # short, or the stock word (the schema says the same).
    noun = _declared(plugin_id).get("noun")
    noun = noun.strip() if isinstance(noun, str) else ""
    return noun if _NOUN_RE.fullmatch(noun) else "items"


def _catalog(page: Any) -> tuple[str, Any] | None:
    """The one list-walking widget on ``page`` as ``(plugin id, placement)``,
    pinned or not, or None (none, or several so a pin would be ambiguous)."""
    found = [(pid, pl) for pid, pl in named_placements(page) if _provider(pid) is not None]
    return found[0] if len(found) == 1 else None


def _draw_count(plugin_id: str, options: dict[str, Any], ctx: dict[str, Any] | None = None) -> int:
    """What a draw widget's optional ``lineup_item_count(options)`` says, or 0."""
    counter = getattr(_plugin_module(plugin_id), "lineup_item_count", None)
    if not callable(counter):
        return 0
    try:
        n = _call_hook(plugin_id, counter, options, ctx)
    except Exception:
        current_app.logger.warning("%s: lineup_item_count failed", plugin_id, exc_info=True)
        return 0
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0


def _declared_listing(
    plugin_id: str, option: str, options: dict[str, Any], ctx: dict[str, Any] | None = None
) -> list[ItemInfo]:
    lister = getattr(_plugin_module(plugin_id), "lineup_items", None)
    if not callable(lister):
        return []
    rest = {k: v for k, v in options.items() if k != option}
    try:
        listed = [dict(x) for x in _call_hook(plugin_id, lister, rest, ctx)]
    except Exception:
        current_app.logger.warning("%s: lineup_items failed", plugin_id, exc_info=True)
        return []
    return _declared_infos(listed)


def list_items(
    page: Any,
    *,
    offset: int = 0,
    limit: int = MAX_PAGE_ITEMS,
    query: str = "",
    device_id: str | None = None,
) -> ItemPage:
    """A page of ``page``'s items, filtered by ``query`` (case-insensitive,
    on the label or key). Unlike ``step_choices`` this reads a pinned
    widget's list too, so the editor can show what it is pinned to."""
    offset = max(0, int(offset))
    limit = max(1, min(MAX_PAGE_ITEMS, int(limit)))
    q = query.strip().casefold()
    found = _catalog(page)
    if found is None:
        return ItemPage("none", 1, 1, 0, (), "items")
    plugin_id, placement = found
    options = dict(getattr(placement, "options", None) or {})
    ctx = _item_ctx(page, placement, plugin_id, device_id)
    noun = _noun(plugin_id)
    if plugin_id == "picture_gallery":
        folder = str(options.get("folder") or "")
        module = _plugin_module(plugin_id)
        listed = _gallery_all(module, folder) if module is not None else None
        names = listed[1] if listed is not None else ()
        named = [(i, n) for i, n in enumerate(names) if not q or q in n.casefold()]
        items = _gallery_item_infos(module, folder, named[offset : offset + limit])
        mode = "pick" if len(names) > 1 else "none"
        return ItemPage(
            mode, len(names), len(named), offset, tuple(items), noun, plugin_id, fit=step_fit(page)
        )
    declared = _declared(plugin_id)
    option = declared.get("pin_option")
    every: list[ItemInfo] = []
    pinned = ""
    if isinstance(option, str) and option:
        every = _declared_listing(plugin_id, option, options, ctx)
        pinned = str(options.get(option) or "").strip()
    # A widget that declares a list and a draw, with nothing to list, draws.
    if isinstance(option, str) and option and (every or pinned or declared.get("draw") is not True):
        label = next((it.label for it in every if it.key == pinned), pinned)
        kept = [it for it in every if not q or q in it.label.casefold() or q in it.key.casefold()]
        return ItemPage(
            "pick" if len(every) > 1 else "none",
            len(every),
            len(kept),
            offset,
            tuple(kept[offset : offset + limit]),
            noun,
            plugin_id,
            pinnable=True,
            pinned=pinned,
            pinned_label=label if pinned else "",
            empty=not every and not pinned,
        )
    count = _draw_count(plugin_id, options, ctx)
    return ItemPage("draw", count, count, 0, (), noun, plugin_id)


def item_summary(page: Any, device_id: str | None = None) -> dict[str, Any]:
    """What the editor's row says about ``page``'s items: ``{mode, count,
    noun, plugin, pinnable, pinned, pinned_label}``."""
    found = list_items(page, limit=1, device_id=device_id)
    return {
        "mode": found.mode,
        "count": found.total,
        "noun": found.noun,
        "plugin": found.plugin,
        "pinnable": found.pinnable,
        "pinned": found.pinned,
        "pinned_label": found.pinned_label,
        "fit": found.fit,
        "empty": found.empty,
    }


def pin_page(page: Any, key: str | None, device_id: str | None = None) -> dict[str, Any] | None:
    """``page``'s definition (a dict) with its declared widget pinned to
    ``key``, or unpinned for None/"". None when the page has no pinnable
    widget or ``key`` is not one of its items."""
    found = _catalog(page)
    if found is None:
        return None
    plugin_id, placement = found
    option = _declared(plugin_id).get("pin_option")
    if not isinstance(option, str) or not option:
        return None
    options = dict(getattr(placement, "options", None) or {})
    ctx = _item_ctx(page, placement, plugin_id, device_id)
    key = (key or "").strip()
    if key and key not in {it.key for it in _declared_listing(plugin_id, option, options, ctx)}:
        return None
    cell_id = str(placement.id)

    def pinned(placements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for one in placements:
            if str(one.get("id")) == cell_id:
                one = {**one, "options": {**(one.get("options") or {}), option: key}}
            out.append(one)
        return out

    data: dict[str, Any] = page.model_dump(mode="json")
    out = {**data, "cells": pinned(data.get("cells") or [])}
    canvas = data.get("canvas")
    if isinstance(canvas, dict):
        out["canvas"] = {**canvas, "els": pinned(canvas.get("els") or [])}
    return out
