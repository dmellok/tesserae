"""Export dashboards and lineups to a file, and import them again (#541).

A bundle is one JSON document, plain enough to keep in git::

    {
      "tesserae_bundle": 1,
      "exported_from": "0.514.0",
      "exported_at": "2026-11-17T09:00:00+00:00",
      "requires": ["weather_now", "ha_entities"],
      "dashboards": [{"page": {...}, "assets": {"<name>": "<base64>"}}],
      "lineups": [{...}]
    }

It is for moving work between installs (build on a dev server, import into
production), not a backup: :mod:`app.backup` copies the whole data folder,
secrets included. A bundle leaves out everything that belongs to one install:

* which displays a dashboard or lineup is bound to;
* widget options a manifest marks ``secret`` and the request headers of a
  code element's sources, both of which sit in ``pages.json`` in the clear;
* plugin settings (API keys), which are encrypted with a per-install key.

Home Assistant entity ids, calendar addresses and the like are kept as they
are. The two ends of a dev-to-production move usually share them, and the
import report says which widgets are missing so nothing fails silently.

Import keeps an object's id when the target has nothing under it, so a second
import of the same file can *replace* what the first one made. Replacing keeps
the target's own display bindings. An id that is taken and not being replaced
gets a new one, and every reference to it inside the bundle follows.
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app import page_assets
from app.state.deck_model import Deck
from app.state.page_store import Page

BUNDLE_VERSION = 1
# The whole uploaded file. Page assets are capped one by one as uploads are.
MAX_BUNDLE_BYTES = 64 * 1024 * 1024

# Fields that describe one install's use of a dashboard, not the dashboard.
_PAGE_LOCAL_FIELDS = ("device_ids", "archived", "created_by", "updated_at", "updated_by")
_DECK_LOCAL_FIELDS = ("device_ids",)

_ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,120}$")
_ASSET_EXTS = frozenset(page_assets._EXT_BY_TYPE.values())


class BundleError(ValueError):
    """The file is not a bundle this version can read."""


# -- export ------------------------------------------------------------------


def _secret_names(registry: Any, plugin_id: str | None) -> set[str]:
    plugin = registry.get(plugin_id) if registry is not None and plugin_id else None
    manifest = getattr(plugin, "manifest", None) or {}
    return {
        str(opt.get("name"))
        for opt in manifest.get("cell_options") or []
        if isinstance(opt, dict) and opt.get("secret") and opt.get("name")
    }


def _blank_secrets(
    registry: Any, plugin_id: str | None, options: Any, where: str, notes: list[str]
) -> None:
    if not isinstance(options, dict):
        return
    for name in sorted(_secret_names(registry, plugin_id)):
        if options.get(name) not in (None, "", [], {}):
            options[name] = ""
            notes.append(f"{where}: left out the {name!r} option of {plugin_id}")


def _note_plugin(requires: set[str], plugin_id: Any) -> None:
    if isinstance(plugin_id, str) and plugin_id:
        requires.add(plugin_id)


def _redact_page(doc: dict[str, Any], registry: Any, requires: set[str], notes: list[str]) -> None:
    name = str(doc.get("name") or doc.get("id"))
    for cell in doc.get("cells") or []:
        _note_plugin(requires, cell.get("plugin"))
        _blank_secrets(registry, cell.get("plugin"), cell.get("options"), name, notes)
    canvas = doc.get("canvas") or {}
    for el in canvas.get("els") or []:
        for key in ("widget", "source"):
            _note_plugin(requires, el.get(key))
            _blank_secrets(registry, el.get(key), el.get("options"), name, notes)
        for source in el.get("sources") or []:
            _note_plugin(requires, source.get("key"))
            _blank_secrets(registry, source.get("key"), source.get("options"), name, notes)
            if source.get("headers"):
                source["headers"] = {}
                notes.append(f"{name}: left out the request headers of a code source")
        for bind in el.get("bind") or []:
            _note_plugin(requires, bind.get("source"))
            _blank_secrets(registry, bind.get("source"), bind.get("options"), name, notes)


def _asset_names(doc: dict[str, Any], page_id: str) -> list[str]:
    found = re.findall(
        r"/page-assets/" + re.escape(page_id) + r"/([A-Za-z0-9._-]+)", json.dumps(doc)
    )
    return sorted(set(found))


def export_page(
    page: Page, *, registry: Any, data_root: Path, requires: set[str], notes: list[str]
) -> dict[str, Any]:
    doc = page.model_dump(mode="json", exclude_none=True)
    for key in _PAGE_LOCAL_FIELDS:
        doc.pop(key, None)
    _redact_page(doc, registry, requires, notes)
    assets: dict[str, str] = {}
    directory = page_assets.assets_dir(data_root, page.id)
    for name in _asset_names(doc, page.id):
        path = directory / name
        try:
            assets[name] = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            notes.append(f"{page.name}: the picture {name} is missing here and was not exported")
    entry: dict[str, Any] = {"page": doc}
    if assets:
        entry["assets"] = assets
    return entry


def deck_page_ids(deck: Deck | dict[str, Any]) -> list[str]:
    """Every dashboard a lineup refers to, members first."""
    doc = deck.model_dump(mode="json") if isinstance(deck, Deck) else deck
    ids: list[str] = []
    for member in doc.get("pages") or []:
        ids.append(str(member.get("page_id") or ""))
        ids.extend(str(link.get("target_page_id") or "") for link in member.get("links") or [])
    ids.extend(
        str(doc.get(key) or "")
        for key in ("entry_page_id", "home_page_id", "advance_fallback_page_id")
    )
    return list(dict.fromkeys(i for i in ids if i))


def build_bundle(
    *,
    page_ids: list[str],
    deck_ids: list[str],
    page_store: Any,
    deck_store: Any,
    registry: Any,
    data_root: Path,
    version: str = "",
) -> tuple[dict[str, Any], list[str]]:
    """The bundle for those dashboards and lineups, and notes on what was left
    out. A lineup brings every dashboard it refers to."""
    notes: list[str] = []
    requires: set[str] = set()
    decks = [d for d in (deck_store.get(i) for i in dict.fromkeys(deck_ids)) if d is not None]
    wanted = list(dict.fromkeys([*page_ids, *(pid for d in decks for pid in deck_page_ids(d))]))
    dashboards = []
    for page_id in wanted:
        page = page_store.get(page_id)
        if page is None:
            notes.append(f"dashboard {page_id} no longer exists and was not exported")
            continue
        dashboards.append(
            export_page(
                page, registry=registry, data_root=data_root, requires=requires, notes=notes
            )
        )
    lineups = []
    for deck in decks:
        doc = deck.model_dump(mode="json", exclude_none=True)
        for key in _DECK_LOCAL_FIELDS:
            doc.pop(key, None)
        lineups.append(doc)
    bundle = {
        "tesserae_bundle": BUNDLE_VERSION,
        "exported_from": version,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "requires": sorted(requires),
        "dashboards": dashboards,
        "lineups": lineups,
    }
    return bundle, notes


# -- import ------------------------------------------------------------------


def read_bundle(raw: bytes) -> dict[str, Any]:
    """Parse an uploaded file. Raises :class:`BundleError` with a sentence an
    operator can act on; the contents are checked object by object later."""
    if len(raw) > MAX_BUNDLE_BYTES:
        raise BundleError("the file is larger than a bundle can be")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as err:
        raise BundleError("the file is not JSON") from err
    if not isinstance(doc, dict) or "tesserae_bundle" not in doc:
        raise BundleError("the file is not a Tesserae export")
    version = doc.get("tesserae_bundle")
    if type(version) is not int or version < 1:
        raise BundleError("the file has no usable format version")
    if version > BUNDLE_VERSION:
        raise BundleError(
            "the file was exported by a newer Tesserae; update this server before importing it"
        )
    dashboards = doc.get("dashboards", [])
    lineups = doc.get("lineups", [])
    if not isinstance(dashboards, list) or not isinstance(lineups, list):
        raise BundleError("the file is not a Tesserae export")
    for entry in dashboards:
        page = entry.get("page") if isinstance(entry, dict) else None
        if not isinstance(page, dict) or not isinstance(page.get("id"), str) or not page["id"]:
            raise BundleError("a dashboard in the file has no id")
        if not isinstance(entry.get("assets", {}), dict):
            raise BundleError("a dashboard in the file has unreadable pictures")
    for deck in lineups:
        if not isinstance(deck, dict) or not isinstance(deck.get("id"), str) or not deck["id"]:
            raise BundleError("a lineup in the file has no id")
    if not dashboards and not lineups:
        raise BundleError("the file has no dashboards or lineups in it")
    return doc


def _page_plugins(doc: dict[str, Any]) -> set[str]:
    requires: set[str] = set()
    _redact_page(copy.deepcopy(doc), None, requires, [])
    return requires


def describe(
    bundle: dict[str, Any], *, page_store: Any, deck_store: Any, registry: Any
) -> dict[str, Any]:
    """What importing would do, for the confirmation page. Writes nothing."""
    dashboards = []
    for entry in bundle.get("dashboards") or []:
        doc = entry["page"]
        existing = page_store.get(doc["id"])
        missing = sorted(p for p in _page_plugins(doc) if registry.get(p) is None)
        dashboards.append(
            {
                "id": doc["id"],
                "name": str(doc.get("name") or doc["id"]),
                "kind": "canvas" if doc.get("layout_kind") == "canvas" else "grid",
                "exists": existing is not None,
                "existing_name": existing.name if existing is not None else "",
                "missing": missing,
                "assets": len(entry.get("assets") or {}),
            }
        )
    lineups = []
    for doc in bundle.get("lineups") or []:
        existing = deck_store.get(doc["id"])
        lineups.append(
            {
                "id": doc["id"],
                "name": str(doc.get("name") or doc["id"]),
                "exists": existing is not None,
                "existing_name": existing.name if existing is not None else "",
                "pages": len(doc.get("pages") or []),
            }
        )
    return {
        "dashboards": dashboards,
        "lineups": lineups,
        "exported_from": str(bundle.get("exported_from") or ""),
        "missing": sorted({m for d in dashboards for m in d["missing"]}),
    }


@dataclass
class ImportReport:
    created_pages: list[str] = field(default_factory=list)
    replaced_pages: list[str] = field(default_factory=list)
    created_lineups: list[str] = field(default_factory=list)
    replaced_lineups: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    # bundle id -> the id it has on this install
    page_ids: dict[str, str] = field(default_factory=dict)
    lineup_ids: dict[str, str] = field(default_factory=dict)

    @property
    def changed(self) -> int:
        return (
            len(self.created_pages)
            + len(self.replaced_pages)
            + len(self.created_lineups)
            + len(self.replaced_lineups)
        )


def _rewrite(value: Any, page_map: dict[str, str]) -> Any:
    """Follow renamed dashboards through a document: ``page:<id>`` actions and
    ``/page-assets/<id>/`` paths, wherever they sit."""
    if isinstance(value, dict):
        return {k: _rewrite(v, page_map) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite(v, page_map) for v in value]
    if not isinstance(value, str):
        return value
    if value.startswith("page:") and value[5:] in page_map:
        return "page:" + page_map[value[5:]]
    if "/page-assets/" in value:
        for old, new in page_map.items():
            value = value.replace(f"/page-assets/{old}/", f"/page-assets/{new}/")
    return value


def _mapped(page_map: dict[str, str], page_id: Any) -> Any:
    return page_map.get(page_id, page_id) if isinstance(page_id, str) else page_id


def _free_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    candidate, n = f"{name} (imported)", 2
    while candidate in taken:
        candidate, n = f"{name} (imported {n})", n + 1
    return candidate


def _new_deck_id(base: str, taken: set[str]) -> str:
    n = 2
    candidate = f"{base}_imported"
    while candidate in taken:
        candidate, n = f"{base}_imported_{n}", n + 1
    return candidate


def _decode_assets(entry: dict[str, Any], name: str) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for asset, encoded in (entry.get("assets") or {}).items():
        ext = asset.rsplit(".", 1)[-1].lower() if "." in asset else ""
        if not _ASSET_NAME_RE.match(asset) or ext not in _ASSET_EXTS:
            raise BundleError(f"{name}: {asset!r} is not a picture name this server stores")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError, TypeError) as err:
            raise BundleError(f"{name}: the picture {asset} is damaged") from err
        if len(data) > page_assets._MAX_IMAGE_BYTES:
            raise BundleError(f"{name}: the picture {asset} is too large")
        out[asset] = data
    return out


def apply_bundle(
    bundle: dict[str, Any],
    *,
    replace_pages: set[str],
    replace_lineups: set[str],
    page_store: Any,
    deck_store: Any,
    registry: Any,
    data_root: Path,
) -> ImportReport:
    """Import ``bundle``. Everything is checked before anything is written, so
    a file with one bad dashboard changes nothing. Raises :class:`BundleError`.

    ``replace_pages`` / ``replace_lineups`` name the bundle ids whose existing
    object on this install should be overwritten; any other id that is taken
    here is imported beside it under a new id."""
    report = ImportReport()
    existing_pages = {p.id: p for p in page_store.list()}
    existing_decks = {d.id: d for d in deck_store.all()}
    taken_page_ids = set(existing_pages)
    taken_names = {p.name for p in existing_pages.values()}

    # 1. Decide each dashboard's id on this install.
    for entry in bundle.get("dashboards") or []:
        old = entry["page"]["id"]
        if old in report.page_ids:
            raise BundleError(f"the file lists dashboard {old} twice")
        if old in existing_pages and old not in replace_pages:
            new = uuid.uuid4().hex[:12]
            while new in taken_page_ids:
                new = uuid.uuid4().hex[:12]
        else:
            new = old
        if not page_assets._PAGE_ID_RE.match(new):
            raise BundleError(f"dashboard id {old!r} is not one this server can store")
        taken_page_ids.add(new)
        report.page_ids[old] = new

    # 2. Build and validate every dashboard.
    lineup_members = {
        report.page_ids.get(pid, pid)
        for doc in bundle.get("lineups") or []
        for pid in deck_page_ids(doc)
    }
    pages: list[tuple[Page, dict[str, bytes], bool]] = []
    missing: set[str] = set()
    for entry in bundle.get("dashboards") or []:
        doc = copy.deepcopy(entry["page"])
        old = doc["id"]
        new = report.page_ids[old]
        name = str(doc.get("name") or old)
        missing |= {p for p in _page_plugins(doc) if registry.get(p) is None}
        for key in _PAGE_LOCAL_FIELDS:
            doc.pop(key, None)
        target = existing_pages.get(new) if new == old else None
        replacing = target is not None
        if new != old:
            # A copy beside the original: its cells must not share ids with it.
            cell_ids = {
                c["id"]: uuid.uuid4().hex[:8] for c in doc.get("cells") or [] if c.get("id")
            }
            for cell in doc.get("cells") or []:
                cell["id"] = cell_ids.get(cell.get("id"), cell.get("id"))
            if doc.get("status_bar_cell_id") in cell_ids:
                doc["status_bar_cell_id"] = cell_ids[doc["status_bar_cell_id"]]
            doc["name"] = _free_name(name, taken_names)
        doc = _rewrite(doc, report.page_ids)
        doc["id"] = new
        if target is not None:
            doc["device_ids"] = list(target.device_ids)
            # A lineup never holds an archived dashboard.
            doc["archived"] = target.archived and new not in lineup_members
        doc["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        doc["updated_by"] = "import"
        try:
            page = Page.model_validate(doc)
        except ValidationError as err:
            first = err.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ()))
            raise BundleError(f"{name}: {where or 'dashboard'}: {first.get('msg')}") from err
        taken_names.add(page.name)
        pages.append((page, _decode_assets(entry, name), replacing))

    # 3. Build and validate every lineup.
    known_pages = taken_page_ids
    taken_deck_ids = set(existing_decks)
    taken_deck_names = {d.name for d in existing_decks.values()}
    decks: list[tuple[Deck, bool]] = []
    for source in bundle.get("lineups") or []:
        doc = copy.deepcopy(source)
        old = doc["id"]
        if old in report.lineup_ids:
            raise BundleError(f"the file lists lineup {old} twice")
        name = str(doc.get("name") or old)
        for key in _DECK_LOCAL_FIELDS:
            doc.pop(key, None)
        target = existing_decks.get(old)
        replacing = target is not None and old in replace_lineups
        if target is not None and not replacing:
            doc["id"] = _new_deck_id(old, taken_deck_ids)
            doc["name"] = _free_name(name, taken_deck_names)
        for member in doc.get("pages") or []:
            if isinstance(member, dict):
                member["page_id"] = _mapped(report.page_ids, member.get("page_id"))
                for link in member.get("links") or []:
                    link["target_page_id"] = _mapped(report.page_ids, link.get("target_page_id"))
        for key in ("entry_page_id", "home_page_id", "advance_fallback_page_id"):
            if doc.get(key):
                doc[key] = _mapped(report.page_ids, doc[key])
        unknown = [pid for pid in deck_page_ids(doc) if pid not in known_pages]
        if unknown:
            report.skipped.append(
                f"lineup {name}: it refers to a dashboard that is not in the file or on this server"
            )
            continue
        if replacing and target is not None:
            doc["device_ids"] = list(target.device_ids)
        try:
            deck = Deck.model_validate(doc)
        except ValidationError as err:
            first = err.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ()))
            raise BundleError(f"lineup {name}: {where or 'lineup'}: {first.get('msg')}") from err
        taken_deck_ids.add(deck.id)
        taken_deck_names.add(deck.name)
        report.lineup_ids[old] = deck.id
        decks.append((deck, replacing))

    # 4. Write. Pictures first, so a saved dashboard never points at nothing.
    for page, assets, replacing in pages:
        directory = page_assets.assets_dir(data_root, page.id)
        if assets:
            directory.mkdir(parents=True, exist_ok=True)
        for asset, data in assets.items():
            (directory / asset).write_bytes(data)
        page_store.save(page)
        (report.replaced_pages if replacing else report.created_pages).append(page.name)
    for deck, replacing in decks:
        deck_store.upsert(deck)
        (report.replaced_lineups if replacing else report.created_lineups).append(deck.name)
    report.missing = sorted(missing)
    return report
