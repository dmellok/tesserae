"""Artwork: pictures a widget makes and keeps.

A widget that paints with fal.ai (or makes any slow, paid picture) declares a
:class:`Collection` once, at module level, with the function that makes one
picture, and asks for pictures by key from ``fetch()``::

    from app import artwork

    ART = artwork.Collection("specimen_sheet", "paintings", make=paint,
                             label="Specimen Sheet", title="Painting specimens",
                             facet="style")

    def paint(job: artwork.Job) -> tuple[bytes, dict]:
        prompt = ...                                  # from job.payload
        raw = job.fal.generate("nano_banana", prompt, refs=[photo])
        return raw, {"credit": "...", "style": job.payload["style"]}

    def fetch(options, settings, *, ctx):
        art = ART.open(ctx, settings, subject=subject_id(options))
        got = art.request(key, payload={...}, title="Australian Magpie, Gould")
        # got.state: ready | painting | held | waiting | failed | no_key | capped
        src = art.url(key, px=400, levels="colour") if got.ready else None
        ...
        return {..., **art.live()}

Everything else is here, once: the store (``<data_dir>/<name>/<key>.jpg``,
its ``.json`` credit and ``.err``, ``archive/`` of earlier versions, resized
and converted copies), the day's budget in US cents at the models' listed
prices, the queue and its worker threads, the hold while a dashboard is being
edited (``ctx["settled"]`` from the host, a settle timer for older hosts),
what each dashboard asked for (so "Render now" paints exactly that), the
job tray cards, and the admin page every collection gets at
``/plugins/<plugin_id>/artwork/<name>/``.

A key is the widget's own readable slug (``australian_magpie--gould--p2``);
the collection checks it and owns the files. ``make`` runs on a worker
thread with a :class:`Job`: the payload the widget gave (written to the
queue file, so a custom prompt survives a restart), a ``fal`` client that
charges the collection's budget, and a stop event.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flask import has_request_context, request

from app import fal

logger = logging.getLogger(__name__)

UNLIMITED = 10**9
KEY_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,118}")
STOPPED = "stopped"
# A failed picture is not tried again for this long; a stopped one likewise.
RETRY_AFTER_S = 6 * 3600
# A preview that keeps the same settings this long is taken as kept, and the
# paintings it would start are started (hosts without ``ctx["settled"]``).
SETTLE_S = 20
SEEN_S = 3600
# Held paintings kept, dashboards remembered, and how recent a dashboard's
# render must be for Render now to count it.
HELD_MAX = 200
WANTED_S = 30 * 86400
ON_FRAME_S = 2 * 86400
LIVE_S = 6
LEVELS = ("colour", "gray16", "gray8", "gray4", "mono")


class ArtworkError(Exception):
    """Raised by a widget's ``make`` with the sentence to keep in ``.err``."""


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def dollars(cents: int) -> str:
    return "no limit" if cents >= UNLIMITED else f"US${cents / 100:.2f}"


# The host's ledger and ceiling over every collection (Settings › Generative
# AI), set once by the app factory; nothing here needs Flask.
_HOST: dict[str, Any] = {"ledger": None, "ceilings": None}


def host_configure(
    ledger: Any | None = None, ceilings: Callable[[], tuple[int, int]] | None = None
) -> None:
    """``ledger.append(plugin_id, name, model, cents)`` is told every charge;
    ``ceilings()`` gives (daily, monthly) cents, UNLIMITED for none."""
    _HOST["ledger"] = ledger
    _HOST["ceilings"] = ceilings


def ceiling_spent() -> str:
    """Why nothing more may be painted server-wide, or empty."""
    ledger, ceilings = _HOST.get("ledger"), _HOST.get("ceilings")
    if ledger is None or ceilings is None:
        return ""
    try:
        daily, monthly = ceilings()
        if daily < UNLIMITED and ledger.today_cents() >= daily:
            return "the server's daily ceiling for paintings is reached"
        if monthly < UNLIMITED and ledger.month_cents() >= monthly:
            return "the server's monthly ceiling for paintings is reached"
    except Exception:
        logger.exception("artwork: the ceiling could not be read")
    return ""


def cell_id_of(plugin_id: str, options: dict[str, Any]) -> str:
    """A stable id for one placement's settings: the plugin and its options,
    secrets left out by the caller. The host computes this into
    ``ctx["cell_id"]``; a widget on an older host may call it itself."""
    try:
        blob = json.dumps(options, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = repr(sorted(options.items(), key=lambda kv: kv[0]))
    return hashlib.sha1(
        (plugin_id + "\n" + blob).encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:16]


# ----------------------------------------------------------------------
# Jobs and results
# ----------------------------------------------------------------------


@dataclass
class Job:
    """One picture to make, handed to the collection's ``make``."""

    key: str
    payload: dict[str, Any]
    title: str
    data_dir: Path
    fal: fal.FalClient
    stop: threading.Event
    origin: str = "panel"
    subject: str = ""
    cell: str = ""

    @property
    def stopped(self) -> bool:
        return self.stop.is_set()

    def file(self, name: str) -> Path:
        """A path inside the collection's folder, for a make that keeps
        something beside the picture (a reference it fetched, say)."""
        return self.data_dir / name


@dataclass
class Got:
    """What ``Art.request`` found for a key."""

    key: str
    state: str
    path: Path | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    why: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    @property
    def credit(self) -> str:
        return str(self.meta.get("credit") or "")


# ----------------------------------------------------------------------
# The job tray
# ----------------------------------------------------------------------


class _Batch:
    """One tray card for everything queued since the queue was last empty."""

    def __init__(self, source: str, key: str, title: str, label: str, href: str) -> None:
        self.source, self.key, self.title, self.label, self.href = source, key, title, label, href
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._total = 0
        self._stopped = 0
        self._stop: Callable[[], object] | None = None

    def add(self, item: str, stop: Callable[[], object]) -> None:
        with self._lock:
            if not self._pending:
                self._total = 0
                self._stopped = 0
            if item in self._pending:
                return
            self._pending.add(item)
            self._total += 1
            self._stop = stop
            self._push()

    def done(self, item: str, stopped: bool = False) -> None:
        with self._lock:
            if item not in self._pending:
                return
            self._pending.discard(item)
            if stopped:
                self._stopped += 1
            if self._pending:
                self._push()
            else:
                self._close()

    def _push(self) -> None:
        try:
            from app import jobs

            finished = self._total - len(self._pending)
            jobs.report(
                self.source,
                self.key,
                self.title,
                progress=finished / self._total if self._total else None,
                detail=f"{finished} of {self._total}",
                label=self.label,
                href=self.href,
                stop=self._stop,
            )
        except Exception:
            pass

    def _close(self) -> None:
        try:
            from app import jobs

            made = self._total - self._stopped
            if self._stopped:
                jobs.cancel(self.source, self.key, f"{made} of {self._total} finished")
            else:
                jobs.finish(self.source, self.key, f"{made} finished")
        except Exception:
            pass


def _tray_waiting(source: str, label: str, href: str, n: int, start: Callable[[], object]) -> None:
    try:
        from app import jobs

        if n <= 0:
            jobs.clear(source, "waiting")
            return
        jobs.report(
            source,
            "waiting",
            f"{n} painting{'' if n == 1 else 's'} waiting",
            detail="Held while the dashboard is edited; Paint now starts them, within the day's budget",
            label=label,
            href=href,
            action=("Paint now", start),
        )
    except Exception:
        pass


# ----------------------------------------------------------------------
# The collection
# ----------------------------------------------------------------------

_COLLECTIONS: dict[tuple[str, str], Collection] = {}
_REGISTRY_LOCK = threading.Lock()


class _Queue:
    """The live state of one collection's queue. Shared by reference when a
    collection is declared again (a widget reloaded in place), so a worker
    started by the old declaration and the new one count the same jobs."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.queue: list[tuple[Job, int]] = []  # (job, cap cents)
        self.queued: set[str] = set()
        self.running: dict[str, tuple[float, str]] = {}
        self.stops: dict[str, threading.Event] = {}
        self.finished: list[tuple[str, float]] = []
        self.workers = 0
        self.resumed: set[Path] = set()


def collections() -> list[Collection]:
    with _REGISTRY_LOCK:
        return list(_COLLECTIONS.values())


def collection(plugin_id: str, name: str) -> Collection | None:
    with _REGISTRY_LOCK:
        return _COLLECTIONS.get((plugin_id, name))


class Collection:
    """The pictures of one kind a widget keeps. Declare one per module; a
    second declaration with the same plugin and name replaces the first
    (a widget reloaded in place) and inherits its queue."""

    def __init__(
        self,
        plugin_id: str,
        name: str,
        *,
        make: Callable[[Job], tuple[bytes, dict[str, Any]]],
        label: str,
        title: str = "Painting",
        href: str | None = None,
        facet: str | None = None,
        facet_label: Callable[[str], str] | None = None,
        budget_setting: str = "gen_daily_usd",
        count_setting: str = "gen_daily_cap",
        parallel_setting: str = "gen_parallel",
        per_preview_setting: str = "gen_per_refresh",
        default_cents: int = 400,
        typical_cents: int = 4,
        parallel: int = 3,
        per_preview: int = 3,
        retry_after_s: int = RETRY_AFTER_S,
        parent: Collection | None = None,
        keep_versions: bool = True,
        extra_template: str | None = None,
        extra_context: Callable[[Path], dict[str, Any]] | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.name = name
        self.make = make
        self.label = label
        self.title = title
        # Root-relative; ``href`` adds the request's URL prefix for links and
        # redirects, the tray card keeps this and the browser adds it there.
        self.page_path = href or f"/plugins/{plugin_id}/artwork/{name}/"
        self.facet = facet
        self.facet_label = facet_label or (lambda v: v)
        self.budget_setting = budget_setting
        self.count_setting = count_setting
        self.parallel_setting = parallel_setting
        self.per_preview_setting = per_preview_setting
        self.default_cents = default_cents
        self.typical_cents = typical_cents
        self.parallel_default = parallel
        self.per_preview_default = per_preview
        self.retry_after_s = retry_after_s
        self.parent = parent
        self.keep_versions = keep_versions
        # A Jinja template of the widget's own (its blueprint's folder) that the
        # host page includes under the To-day card: a form to paint a species by
        # name, say. It sees ``c``, ``plugin`` and ``g``.
        self.extra_template = extra_template
        # What that template needs beyond ``c``, ``plugin`` and ``g``: a
        # callable of the data dir, called when the page is rendered.
        self.extra_context = extra_context
        self.children: list[Collection] = []
        self.actions: list[tuple[str, str, Callable[[str, Path], str]]] = []
        self._parallel = parallel
        self._per_preview = per_preview
        self._tray = _Batch(plugin_id, f"artwork:{name}", title, label, self.page_path)
        if parent is not None:
            parent.children.append(self)
        with _REGISTRY_LOCK:
            old = _COLLECTIONS.get((plugin_id, name))
            self._q: _Queue = old._q if old is not None else _Queue()
            _COLLECTIONS[(plugin_id, name)] = self

    @property
    def href(self) -> str:
        """The collection's page as a link or redirect target, under the URL
        prefix the request came through (Home Assistant's ingress)."""
        if has_request_context():
            return request.script_root.rstrip("/") + self.page_path
        return self.page_path

    @href.setter
    def href(self, value: str) -> None:
        self.page_path = value

    # -- declarations ----------------------------------------------------

    def action(self, slug: str, label: str, fn: Callable[[str, Path], str]) -> None:
        """A per-picture action for the admin page: ``fn(key, data_dir)``
        returns the sentence to flash."""
        self.actions.append((slug, label, fn))

    # -- paths and settings ----------------------------------------------

    def folder(self, data_dir: Path) -> Path:
        return Path(data_dir) / self.name

    def configure(self, settings: dict[str, Any] | None) -> None:
        s = settings or {}
        self._parallel = _whole(s.get(self.parallel_setting), self.parallel_default, 1, 8)
        self._per_preview = _whole(s.get(self.per_preview_setting), self.per_preview_default, 1, 50)

    def cap(self, settings: dict[str, Any] | None) -> int:
        """The day's budget in US cents; UNLIMITED for 0. An older count of
        pictures still counts at the typical price; anything unreadable is
        the default."""
        s = settings or {}
        usd = s.get(self.budget_setting)
        if str(usd if usd is not None else "").strip() != "":
            with contextlib.suppress(TypeError, ValueError):
                return round(float(str(usd)) * 100) or UNLIMITED
        raw = s.get(self.count_setting)
        if raw is None or str(raw).strip() == "":
            return self.default_cents
        try:
            n = int(float(str(raw)))
        except (TypeError, ValueError):
            return self.default_cents
        return n * self.typical_cents if n > 0 else UNLIMITED

    def per_preview(self) -> int:
        return self._per_preview

    # -- budget ------------------------------------------------------------

    def budget_used(self, data_dir: Path) -> int:
        got = _read_json(self.folder(data_dir) / "budget.json", {})
        return int(got.get(_today(), 0)) if isinstance(got, dict) else 0

    def spend(self, data_dir: Path, cents: int, model: str = "") -> None:
        with self._q.lock:
            _write(
                self.folder(data_dir) / "budget.json",
                json.dumps({_today(): self.budget_used(data_dir) + int(cents)}).encode(),
            )
        ledger = _HOST.get("ledger")
        if ledger is not None:
            with contextlib.suppress(Exception):
                ledger.append(self.plugin_id, self.name, model, int(cents))

    def budget_reset(self, data_dir: Path) -> None:
        _write(self.folder(data_dir) / "budget.json", b"{}")

    def room(self, data_dir: Path, cap: int) -> int:
        """How many pictures at the typical price the day's budget still allows."""
        if ceiling_spent():
            return 0
        if cap >= UNLIMITED:
            return 10**6
        return max(0, cap - self.budget_used(data_dir)) // max(1, self.typical_cents)

    # -- the store ---------------------------------------------------------

    def path(self, key: str, data_dir: Path) -> Path | None:
        f = self.folder(data_dir)
        for ext in (".jpg", ".png"):
            p = f / f"{key}{ext}"
            if p.exists():
                return p
        return None

    def meta(self, key: str, data_dir: Path) -> dict[str, Any]:
        got = _read_json(self.folder(data_dir) / f"{key}.json", {})
        return got if isinstance(got, dict) else {}

    def state(self, key: str, data_dir: Path) -> str:
        """'ready', 'running' (queued or under way), 'failed' (recently) or 'none'."""
        with self._q.lock:
            if key in self._q.queued or key in self._q.running:
                return "running"
        if self.path(key, data_dir) is not None:
            return "ready"
        err = _read_json(self.folder(data_dir) / f"{key}.err", None)
        if isinstance(err, dict) and time.time() - float(err.get("at") or 0) < self.retry_after_s:
            return "failed"
        return "none"

    def why_failed(self, key: str, data_dir: Path) -> str:
        err = _read_json(self.folder(data_dir) / f"{key}.err", None)
        return str(err.get("why") or "") if isinstance(err, dict) else ""

    def note_error(self, key: str, data_dir: Path, why: str, title: str = "") -> None:
        """Keep why a picture could not be used (a cut-out that failed, say):
        the key counts as failed for ``retry_after_s``."""
        _write(
            self.folder(data_dir) / f"{key}.err",
            json.dumps({"why": why, "at": int(time.time()), "title": title or key}).encode(),
        )

    def clear_error(self, key: str, data_dir: Path) -> None:
        with contextlib.suppress(OSError):
            (self.folder(data_dir) / f"{key}.err").unlink()

    def picture(self, key: str, data_dir: Path) -> tuple[bytes, dict[str, Any]] | None:
        p = self.path(key, data_dir)
        if p is None:
            return None
        try:
            return p.read_bytes(), self.meta(key, data_dir)
        except OSError:
            return None

    def keep(self, key: str, data_dir: Path, raw: bytes, meta: dict[str, Any]) -> Path:
        """Write a finished picture: an earlier version goes to ``archive/``,
        derived copies of it are dropped, the error (if any) is cleared."""
        f = self.folder(data_dir)
        if self.path(key, data_dir) is not None:
            if self.keep_versions:
                self.archive(key, data_dir)
            else:
                self.delete(key, data_dir, versions=False)
        ext = ".png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
        _write(
            f / f"{key}.json",
            json.dumps({**meta, "made": int(time.time())}, ensure_ascii=False).encode(),
        )
        _write(f / f"{key}{ext}", raw)
        self.drop_derived(key, data_dir)
        self.clear_error(key, data_dir)
        return f / f"{key}{ext}"

    def archive(self, key: str, data_dir: Path) -> None:
        f = self.folder(data_dir)
        arch = f / "archive"
        arch.mkdir(parents=True, exist_ok=True)
        # the stamp is to the second: a second archive within it (paint again, then use the
        # earlier version) takes the next free name rather than writing over the first
        base = time.strftime("%Y%m%d-%H%M%S")
        stamp, n = base, 1
        while any((arch / f"{key}__{stamp}{ext}").exists() for ext in (".jpg", ".png", ".json")):
            stamp = f"{base}-{n}"
            n += 1
        for ext in (".jpg", ".png", ".json"):
            with contextlib.suppress(OSError):
                os.replace(f / f"{key}{ext}", arch / f"{key}__{stamp}{ext}")
        self.drop_derived(key, data_dir)

    def versions(self, key: str, data_dir: Path) -> list[dict[str, Any]]:
        arch = self.folder(data_dir) / "archive"
        out = []
        with contextlib.suppress(OSError):
            for p in sorted(arch.glob(f"{key}__*")):
                if p.suffix in (".jpg", ".png"):
                    stamp = p.stem.rsplit("__", 1)[-1]
                    out.append({"stem": p.stem, "file": p.name, "made": stamp})
        return out

    def use_version(self, stem: str, data_dir: Path) -> str | None:
        """Make an archived version the current picture; the current one is
        archived in its turn. Returns the key, or None when there is no such
        version."""
        arch = self.folder(data_dir) / "archive"
        key = stem.rsplit("__", 1)[0]
        src = next(
            (arch / f"{stem}{ext}" for ext in (".jpg", ".png") if (arch / f"{stem}{ext}").exists()),
            None,
        )
        if src is None:
            return None
        raw = src.read_bytes()
        meta = _read_json(arch / f"{stem}.json", {})
        for ext in (".jpg", ".png", ".json"):
            with contextlib.suppress(OSError):
                (arch / f"{stem}{ext}").unlink()
        self.keep(key, data_dir, raw, meta if isinstance(meta, dict) else {})
        return key

    def delete_version(self, stem: str, data_dir: Path) -> bool:
        arch = self.folder(data_dir) / "archive"
        gone = False
        for ext in (".jpg", ".png", ".json"):
            with contextlib.suppress(OSError):
                (arch / f"{stem}{ext}").unlink()
                gone = True
        return gone

    def drop_derived(self, key: str, data_dir: Path) -> None:
        """Remove the resized copies and any variant the widget made beside
        the picture (``<key>__<variant>.png``, a cut-out say)."""
        f = self.folder(data_dir)
        with contextlib.suppress(OSError):
            for p in list((f / "derived").glob(f"{key}__*")) + list(f.glob(f"{key}__*")):
                with contextlib.suppress(OSError):
                    p.unlink()
        for child in self.children:
            child.delete(key, data_dir, versions=True)

    def delete(self, key: str, data_dir: Path, *, versions: bool = True) -> int:
        """Remove a picture, its credit, its error, its derived copies and,
        with ``versions``, its earlier versions. How many files went."""
        self.stop(key, data_dir)
        f = self.folder(data_dir)
        n = 0
        for ext in (".jpg", ".png", ".json", ".err"):
            with contextlib.suppress(OSError):
                (f / f"{key}{ext}").unlink()
                n += 1
        self.drop_derived(key, data_dir)
        if versions:
            with contextlib.suppress(OSError):
                for p in (f / "archive").glob(f"{key}__*"):
                    p.unlink()
                    n += 1
        return n

    def purge(self, data_dir: Path) -> dict[str, int]:
        """Every picture, version and held job gone; the day's spend stays."""
        self.stop_all(data_dir)
        f = self.folder(data_dir)
        out = {"pictures": 0, "versions": 0}
        with contextlib.suppress(OSError):
            for p in f.iterdir():
                if p.is_file() and p.name not in (
                    "budget.json",
                    "preview_seen.json",
                    "dashboards.json",
                ):
                    if p.suffix in (".jpg", ".png"):
                        out["pictures"] += 1
                    p.unlink()
            for sub in ("archive", "derived"):
                d = f / sub
                if d.is_dir():
                    for p in d.iterdir():
                        if sub == "archive" and p.suffix in (".jpg", ".png"):
                            out["versions"] += 1
                        p.unlink()
        self.unhold(data_dir)
        for child in self.children:
            child.purge(data_dir)
        return out

    def listing(self, data_dir: Path) -> list[dict[str, Any]]:
        """Every kept picture with its credit, state and versions, newest first."""
        f = self.folder(data_dir)
        out: list[dict[str, Any]] = []
        with contextlib.suppress(OSError):
            for p in f.iterdir():
                if p.suffix in (".jpg", ".png") and p.is_file() and "__" not in p.stem:
                    key = p.stem
                    meta = self.meta(key, data_dir)
                    out.append(
                        {
                            "key": key,
                            "file": p.name,
                            "made": int(meta.get("made") or int(p.stat().st_mtime)),
                            "meta": meta,
                            "title": str(
                                meta.get("title") or meta.get("name") or meta.get("reg") or key
                            ),
                            "facet": str(meta.get(self.facet) or "") if self.facet else "",
                            "facet_label": self.facet_label(str(meta.get(self.facet) or ""))
                            if self.facet
                            else "",
                            "versions": self.versions(key, data_dir),
                            "error": self.why_failed(key, data_dir),
                        }
                    )
        # a picture deleted but kept as an earlier version has no current file: it stays on the
        # page as a card with its versions, so one of them can be made current again or dropped
        have = {e["key"] for e in out}
        arch = f / "archive"
        with contextlib.suppress(OSError):
            for p in sorted(arch.iterdir()):
                if p.suffix not in (".jpg", ".png") or "__" not in p.stem:
                    continue
                key = p.stem.rsplit("__", 1)[0]
                if key in have:
                    continue
                have.add(key)
                meta = _read_json(arch / f"{p.stem}.json", {})
                meta = meta if isinstance(meta, dict) else {}
                versions = self.versions(key, data_dir)
                try:
                    made = int(time.mktime(time.strptime(versions[-1]["made"], "%Y%m%d-%H%M%S")))
                except (ValueError, IndexError, OverflowError):
                    made = int(p.stat().st_mtime)
                out.append(
                    {
                        "key": key,
                        "file": "",
                        "made": made,
                        "meta": meta,
                        "title": str(
                            meta.get("title") or meta.get("name") or meta.get("reg") or key
                        ),
                        "facet": str(meta.get(self.facet) or "") if self.facet else "",
                        "facet_label": self.facet_label(str(meta.get(self.facet) or ""))
                        if self.facet
                        else "",
                        "versions": versions,
                        "error": self.why_failed(key, data_dir),
                    }
                )
        out.sort(key=lambda e: -e["made"])
        return out

    def errors(self, data_dir: Path) -> list[dict[str, Any]]:
        """Keys with a recent failure and no picture."""
        f = self.folder(data_dir)
        out: list[dict[str, Any]] = []
        with contextlib.suppress(OSError):
            for p in f.glob("*.err"):
                key = p.stem
                if self.path(key, data_dir) is None:
                    err = _read_json(p, {})
                    if isinstance(err, dict):
                        out.append(
                            {
                                "key": key,
                                "why": str(err.get("why") or ""),
                                "at": int(err.get("at") or 0),
                                "title": str(err.get("title") or key),
                            }
                        )
        out.sort(key=lambda e: -int(e["at"]))
        return out

    # -- derived copies ----------------------------------------------------

    def url(
        self,
        key: str,
        data_dir: Path,
        *,
        px: int = 600,
        levels: str = "colour",
        cut: bool = False,
        variant: str = "",
    ) -> str:
        """The picture as a data URI, resized to ``px`` on its longest side
        and converted for a panel without colour; cached per size and panel.
        ``cut`` keeps transparency (a PNG), for a cut-out. ``variant`` names
        another file beside the picture (``<key>__<variant>.png``, made by the
        widget) to derive from instead."""
        f = self.folder(data_dir)
        src = f / f"{key}__{variant}.png" if variant else self.path(key, data_dir)
        if src is None or not src.exists():
            return ""
        levels = levels if levels in LEVELS else "colour"
        cache = (
            f / "derived" / f"{key}__{variant or 'full'}_{px}_{levels}.{'png' if cut else 'jpg'}"
        )
        if cache.exists():
            with contextlib.suppress(OSError):
                return fal.data_uri(cache.read_bytes())
        try:
            raw = src.read_bytes()
        except OSError:
            return ""
        data = _derive(raw, px, levels, cut)
        if not data:
            return ""
        _write(cache, data)
        return fal.data_uri(data)

    # -- the queue -----------------------------------------------------------

    def _fal_for(self, data_dir: Path, key: str, stop: threading.Event, cap: int) -> fal.FalClient:
        def charge(cents: int, model: str = "") -> None:
            if cap < UNLIMITED and self.budget_used(data_dir) >= cap:
                raise ArtworkError("the day's budget is spent")
            why = ceiling_spent()
            if why:
                raise ArtworkError(why)
            self.spend(data_dir, cents, model)

        return fal.FalClient(key, charge=charge, stop=stop)

    def start(self, job: Job, cap: int) -> bool:
        """Queue a job; False when its key is already queued or under way."""
        key = job.key
        with self._q.lock:
            if key in self._q.queued or key in self._q.running:
                return False
            self._q.queued.add(key)
            self._q.queue.append((job, cap))
            self._tray.add(key, lambda: self.stop_all(job.data_dir))
            self._persist_locked(job.data_dir)
            spawn = self._q.workers < self._parallel
            if spawn:
                self._q.workers += 1
        if spawn:
            threading.Thread(target=self._work, name=f"artwork-{self.name}", daemon=True).start()
        return True

    def start_batch(self, jobs: list[Job], data_dir: Path, cap: int) -> int:
        n = 0
        for job in jobs:
            if cap < UNLIMITED and self.budget_used(data_dir) >= cap:
                break
            if self.start(job, cap):
                n += 1
        return n

    def _work(self) -> None:
        while True:
            with self._q.lock:
                if not self._q.queue:
                    self._q.workers -= 1
                    return
                job, cap = self._q.queue.pop(0)
                self._q.queued.discard(job.key)
                self._q.stops[job.key] = job.stop
                self._q.running[job.key] = (time.time(), job.title)
                self._persist_locked(job.data_dir)
            try:
                if cap < UNLIMITED and self.budget_used(job.data_dir) >= cap:
                    self._note_error(job, "the day's budget is spent")
                    continue
                why = ceiling_spent()
                if why:
                    self._note_error(job, why)
                    continue
                self._run_one(job, cap)
            finally:
                with self._q.lock:
                    self._q.running.pop(job.key, None)
                    self._q.stops.pop(job.key, None)
                    self._q.finished.append((job.key, time.time()))
                    del self._q.finished[:-20]
                self._tray.done(job.key)

    def _run_one(self, job: Job, cap: int) -> None:
        """Make one picture, giving the slot up at once if it is stopped: a
        call to fal.ai cannot be recalled, so it finishes on its own and the
        result is dropped."""
        job.fal = self._fal_for(job.data_dir, job.fal.key, job.stop, cap)
        done = threading.Event()
        result: dict[str, Any] = {}

        def go() -> None:
            try:
                raw, meta = self.make(job)
                if not job.stop.is_set():
                    result["ok"] = (raw, meta)
            except (ArtworkError, fal.FalError) as err:
                result["why"] = str(err) or "failed"
            except Exception as exc:
                logger.exception("artwork %s/%s: %s failed", self.plugin_id, self.name, job.key)
                result["why"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            finally:
                done.set()

        t = threading.Thread(target=go, name=f"artwork-{self.name}-{job.key[:16]}", daemon=True)
        t.start()
        while not done.is_set() and not job.stop.is_set():
            done.wait(0.25)
        if job.stop.is_set():
            self._note_error(job, STOPPED)
            return
        if "ok" in result:
            raw, meta = result["ok"]
            full = {"title": job.title, "payload": job.payload, **meta}
            self.keep(job.key, job.data_dir, raw, full)
        else:
            self._note_error(job, result.get("why") or "failed")

    def _note_error(self, job: Job, why: str) -> None:
        _write(
            self.folder(job.data_dir) / f"{job.key}.err",
            json.dumps({"why": why, "at": int(time.time()), "title": job.title}).encode(),
        )

    def stop(self, key: str, data_dir: Path) -> bool:
        with self._q.lock:
            for i, (job, _cap) in enumerate(self._q.queue):
                if job.key == key:
                    gone = self._q.queue.pop(i)[0]
                    self._q.queued.discard(key)
                    self._persist_locked(data_dir)
                    break
            else:
                gone = None
                ev = self._q.stops.get(key)
                if ev is None:
                    return False
                ev.set()
        if gone is not None:
            self._note_error(gone, STOPPED)
        self._tray.done(key, stopped=True)
        return True

    def stop_all(self, data_dir: Path) -> int:
        with self._q.lock:
            keys = [j.key for j, _c in self._q.queue] + list(self._q.stops)
        return sum(1 for k in keys if self.stop(k, data_dir))

    def cull(self, subject: str, keep: set[str]) -> list[str]:
        """Drop the waiting jobs a cell's previews queued for a look it has
        moved on from: those of ``subject`` from a preview whose key is not
        in ``keep``. One under way is left alone. The keys dropped."""
        gone: list[str] = []
        with self._q.lock:
            for i in range(len(self._q.queue) - 1, -1, -1):
                job = self._q.queue[i][0]
                if job.origin == "preview" and job.subject == subject and job.key not in keep:
                    self._q.queue.pop(i)
                    self._q.queued.discard(job.key)
                    gone.append(job.key)
            if gone:
                self._persist_locked(job.data_dir)
        for key in gone:
            self._tray.done(key, stopped=True)
        return gone

    def progress(self, data_dir: Path, settings: dict[str, Any] | None) -> dict[str, Any]:
        now = time.time()
        with self._q.lock:
            running = [
                {"key": k, "title": t, "seconds": int(now - since)}
                for k, (since, t) in self._q.running.items()
            ]
            queued = [{"key": j.key, "title": j.title} for j, _c in self._q.queue]
            finished = [{"key": k, "at": int(at)} for k, at in self._q.finished]
        cap = self.cap(settings)
        used = self.budget_used(data_dir)
        return {
            "running": running,
            "queued": queued,
            "finished": finished,
            "used": used,
            "cap": cap,
            "used_text": dollars(used),
            "cap_text": dollars(cap),
            "held": len(self.held(data_dir)),
        }

    # -- the queue on disk, for a restart ------------------------------------

    def _persist_locked(self, data_dir: Path) -> None:
        rows = [
            {
                "key": j.key,
                "payload": j.payload,
                "title": j.title,
                "origin": j.origin,
                "subject": j.subject,
                "cell": j.cell,
                "cap": cap,
            }
            for j, cap in self._q.queue
            if j.data_dir == data_dir
        ] + [
            {
                "key": k,
                "payload": {},
                "title": t,
                "origin": "panel",
                "subject": "",
                "cell": "",
                "cap": UNLIMITED,
                "running": True,
            }
            for k, (_s, t) in self._q.running.items()
        ]
        with contextlib.suppress(OSError):
            _write(
                self.folder(data_dir) / "queue.json", json.dumps(rows, ensure_ascii=False).encode()
            )

    def resume(self, data_dir: Path, fal_key: str) -> int:
        """Queue what was waiting when the server last stopped (once per
        data dir). A job that was under way is queued again from its payload
        when the payload is known, else dropped. How many were queued."""
        with self._q.lock:
            if data_dir in self._q.resumed:
                return 0
            self._q.resumed.add(data_dir)
        rows = _read_json(self.folder(data_dir) / "queue.json", [])
        if not isinstance(rows, list) or not fal_key:
            return 0
        n = 0
        for r in rows:
            if (
                not isinstance(r, dict)
                or not r.get("key")
                or not isinstance(r.get("payload"), dict)
                or not r["payload"]
            ):
                continue
            if self.path(str(r["key"]), data_dir) is not None:
                continue
            job = self.job(
                str(r["key"]),
                r["payload"],
                str(r.get("title") or r["key"]),
                data_dir,
                fal_key,
                origin=str(r.get("origin") or "panel"),
                subject=str(r.get("subject") or ""),
                cell=str(r.get("cell") or ""),
            )
            if self.start(job, int(r.get("cap") or UNLIMITED)):
                n += 1
        return n

    def job(
        self,
        key: str,
        payload: dict[str, Any],
        title: str,
        data_dir: Path,
        fal_key: str,
        *,
        origin: str = "panel",
        subject: str = "",
        cell: str = "",
    ) -> Job:
        stop = threading.Event()
        return Job(
            key=key,
            payload=payload,
            title=title,
            data_dir=Path(data_dir),
            fal=fal.FalClient(fal_key, stop=stop),
            stop=stop,
            origin=origin,
            subject=subject,
            cell=cell,
        )

    # -- held pictures, settling previews ----------------------------------

    def held(self, data_dir: Path) -> dict[str, dict[str, Any]]:
        got = _read_json(self.folder(data_dir) / "held.json", {})
        return got if isinstance(got, dict) else {}

    def hold(self, data_dir: Path, job: Job) -> None:
        with self._q.lock:
            got = self.held(data_dir)
            if job.key in got:
                return
            got[job.key] = {
                "key": job.key,
                "payload": job.payload,
                "title": job.title,
                "subject": job.subject,
                "cell": job.cell,
                "at": time.time(),
            }
            for old in list(got)[: max(0, len(got) - HELD_MAX)]:
                del got[old]
            _write(
                self.folder(data_dir) / "held.json", json.dumps(got, ensure_ascii=False).encode()
            )

    def unhold(self, data_dir: Path, keys: list[str] | None = None) -> None:
        with self._q.lock:
            got = self.held(data_dir)
            for k in keys if keys is not None else list(got):
                got.pop(k, None)
            _write(
                self.folder(data_dir) / "held.json", json.dumps(got, ensure_ascii=False).encode()
            )

    def preview_settled(self, data_dir: Path, cell: str, now: float | None = None) -> bool:
        """For a host that does not say whether the dashboard is being
        edited: a cell whose settings have stood for SETTLE_S of previews is
        taken as kept."""
        now = now if now is not None else time.time()
        path = self.folder(data_dir) / "preview_seen.json"
        with self._q.lock:
            got = _read_json(path, {})
            got = (
                {k: float(v) for k, v in got.items() if now - float(v) < SEEN_S}
                if isinstance(got, dict)
                else {}
            )
            first = got.get(cell)
            if first is None:
                got[cell] = now
            _write(path, json.dumps(got).encode())
        return first is not None and now - first >= SETTLE_S

    def start_held(
        self, data_dir: Path, fal_key: str, settings: dict[str, Any] | None, subject: str = ""
    ) -> int:
        """Start what previews held back, for one cell's subject when given,
        within the day's budget. How many started."""
        cap = self.cap(settings)
        rows = [
            j for j in self.held(data_dir).values() if not subject or j.get("subject") == subject
        ]
        self.unhold(data_dir, [str(j["key"]) for j in rows])
        ready = [j for j in rows if self.state(str(j["key"]), data_dir) == "none"]
        begin = ready[: self.room(data_dir, cap)]
        jobs = [
            self.job(
                str(j["key"]),
                dict(j.get("payload") or {}),
                str(j.get("title") or j["key"]),
                data_dir,
                fal_key,
                origin="panel",
                subject=str(j.get("subject") or ""),
                cell=str(j.get("cell") or ""),
            )
            for j in begin
        ]
        n = self.start_batch(jobs, data_dir, cap)
        _tray_waiting(
            self.plugin_id,
            self.label,
            self.page_path,
            len(self.held(data_dir)),
            lambda: self.start_held(data_dir, fal_key, settings),
        )
        return n

    # -- what the dashboards asked for -------------------------------------

    def note_dashboard(
        self,
        data_dir: Path,
        ctx: dict[str, Any],
        subject: str,
        facet: str,
        jobs: dict[str, dict[str, Any]],
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Which dashboard a panel render came from and the pictures it wants,
        so the admin page groups them and Render now paints exactly those. A
        dashboard is its name, facet (the style) and subject; an edit makes a
        new subject and the old one goes once it has not rendered for an hour."""
        path = self.folder(data_dir) / "dashboards.json"
        now = time.time()
        got = _read_json(path, {})
        got = {
            k: v
            for k, v in (got.items() if isinstance(got, dict) else [])
            if isinstance(v, dict) and now - float(v.get("at") or 0) < WANTED_S
        }
        name = " ".join(str(ctx.get("page_name") or "").split())[:80]
        rid = f"{name}|{facet}|{subject}"
        got = {
            k: v
            for k, v in got.items()
            if not (
                v.get("name") == name
                and v.get("facet") == facet
                and k != rid
                and now - float(v.get("at") or 0) > 3600
            )
        }
        got[rid] = {
            "name": name,
            "icon": str(ctx.get("page_icon") or "")[:40],
            "facet": facet,
            "facet_label": self.facet_label(facet) if facet else "",
            "subject": subject,
            "keys": list(jobs),
            "jobs": {
                k: {"payload": j.get("payload") or {}, "title": str(j.get("title") or k)}
                for k, j in jobs.items()
            },
            "at": now,
            **(extra or {}),
        }
        _write(path, json.dumps(got, ensure_ascii=False).encode())

    def dashboards(self, data_dir: Path) -> list[dict[str, Any]]:
        got = _read_json(self.folder(data_dir) / "dashboards.json", {})
        if not isinstance(got, dict):
            return []
        now = time.time()
        out = [
            {**v, "id": k}
            for k, v in got.items()
            if isinstance(v, dict) and now - float(v.get("at") or 0) < WANTED_S
        ]
        return sorted(out, key=lambda v: -float(v.get("at") or 0))

    def render_now(
        self, data_dir: Path, fal_key: str, settings: dict[str, Any] | None, who: str = ""
    ) -> tuple[int, int]:
        """Paint what the panels are missing: the pictures the dashboards
        rendered to a panel lately asked for and do not have; one dashboard's
        when ``who`` names it. (started, found)."""
        cap = self.cap(settings)
        now = time.time()
        wanted: dict[str, dict[str, Any]] = {}
        for d in self.dashboards(data_dir):
            if who and d["id"] != who:
                continue
            if not who and now - float(d.get("at") or 0) > ON_FRAME_S:
                continue
            for key, j in (d.get("jobs") or {}).items():
                if isinstance(j, dict) and j.get("payload"):
                    wanted.setdefault(key, {**j, "subject": str(d.get("subject") or "")})
        ready = [(k, j) for k, j in wanted.items() if self.path(k, data_dir) is None]
        begin = [(k, j) for k, j in ready if self.state(k, data_dir) == "none"][
            : self.room(data_dir, cap)
        ]
        jobs = [
            self.job(
                k,
                dict(j["payload"]),
                str(j.get("title") or k),
                data_dir,
                fal_key,
                origin="panel",
                subject=str(j.get("subject") or ""),
            )
            for k, j in begin
        ]
        return self.start_batch(jobs, data_dir, cap), len(ready)

    def repaint(
        self, key: str, data_dir: Path, fal_key: str, settings: dict[str, Any] | None
    ) -> bool:
        """Paint a kept picture again from the payload kept with it; the old
        one becomes an earlier version when the new one lands."""
        meta = self.meta(key, data_dir)
        payload = meta.get("payload")
        if not isinstance(payload, dict) or not payload:
            held = self.held(data_dir).get(key)
            payload = held.get("payload") if isinstance(held, dict) else None
        if not isinstance(payload, dict) or not payload:
            return False
        self.clear_error(key, data_dir)
        job = self.job(key, dict(payload), str(meta.get("title") or key), data_dir, fal_key)
        return self.start(job, self.cap(settings))

    # -- an older install's pictures -----------------------------------------

    def adopt(
        self,
        data_dir: Path,
        old_name: str,
        *,
        drop: str = "",
        budget_counts: bool = False,
        job_to_payload: Callable[[dict[str, Any]], tuple[dict[str, Any], str]] | None = None,
    ) -> bool:
        """Take over the folder a widget kept its pictures in before it used a
        collection: ``<data_dir>/<old_name>/`` becomes the collection's folder
        (its ``archive/`` as it is), files matching ``drop`` (resized copies)
        go, a day's budget kept as a count of pictures is turned into cents,
        and the widget's own ``held_paints.json`` and ``dashboards.json`` at
        the data dir's root are read into the collection's through
        ``job_to_payload(job) -> (payload, title)``. Once, and only while the
        collection's folder does not exist yet. True when something moved."""
        data_dir = Path(data_dir)
        old = data_dir / old_name
        new = self.folder(data_dir)
        if new.exists() or not old.is_dir():
            return False
        pattern = re.compile(drop) if drop else None
        with contextlib.suppress(OSError):
            for f in old.iterdir():
                if f.is_file() and pattern is not None and pattern.fullmatch(f.name):
                    f.unlink()
        os.replace(old, new)
        if budget_counts:
            got = _read_json(new / "budget.json", {})
            if isinstance(got, dict):
                _write(
                    new / "budget.json",
                    json.dumps(
                        {
                            k: int(v) * self.typical_cents
                            for k, v in got.items()
                            if isinstance(v, (int, float))
                        }
                    ).encode(),
                )
        if job_to_payload is not None:
            held = _read_json(data_dir / "held_paints.json", {})
            if isinstance(held, dict) and held:
                kept = {}
                for key, job in held.items():
                    if isinstance(job, dict):
                        payload, title = job_to_payload(job)
                        kept[key] = {
                            "key": key,
                            "payload": payload,
                            "title": title,
                            "subject": str(job.get("subject") or ""),
                            "cell": str(job.get("cell") or ""),
                            "at": time.time(),
                        }
                _write(new / "held.json", json.dumps(kept, ensure_ascii=False).encode())
            dash = _read_json(data_dir / "dashboards.json", {})
            if isinstance(dash, dict) and dash:
                out = {}
                for rid, d in dash.items():
                    if not isinstance(d, dict):
                        continue
                    jobs = {}
                    for key, job in (d.get("jobs") or {}).items():
                        if isinstance(job, dict):
                            payload, title = job_to_payload(job)
                            jobs[key] = {"payload": payload, "title": title}
                    facet = str(d.get("facet") or d.get("style") or "")
                    out[rid] = {
                        "name": str(d.get("name") or ""),
                        "icon": str(d.get("icon") or ""),
                        "facet": facet,
                        "facet_label": self.facet_label(facet) if facet else "",
                        "subject": str(
                            d.get("subject") or (rid.rsplit("|", 1)[-1] if "|" in rid else "")
                        ),
                        "keys": [str(k) for k in d.get("keys") or []],
                        "jobs": jobs,
                        "at": float(d.get("at") or 0),
                    }
                _write(new / "dashboards.json", json.dumps(out, ensure_ascii=False).encode())
            for name in ("held_paints.json", "dashboards.json", "preview_seen.json"):
                with contextlib.suppress(OSError):
                    (data_dir / name).unlink()
        logger.info(
            "artwork %s/%s: adopted %s/ from an older install", self.plugin_id, self.name, old_name
        )
        return True

    # -- the bound view a fetch uses -----------------------------------------

    def open(
        self,
        ctx: dict[str, Any],
        settings: dict[str, Any] | None,
        *,
        subject: str = "",
        cell: str = "",
    ) -> Art:
        """Bind the collection to one fetch: its data dir, whether this is a
        preview and whether it has settled, the key and the day's budget."""
        settings = settings or {}
        self.configure(settings)
        data_dir = Path(ctx.get("data_dir") or ".")
        key = fal.resolve_key(settings)
        if key:
            self.resume(data_dir, key)
        preview = bool(ctx.get("preview"))
        cell = cell or str(ctx.get("cell_id") or "")
        if "settled" in ctx:
            settled = bool(ctx["settled"])
        elif preview and cell:
            settled = self.preview_settled(data_dir, cell)
        else:
            settled = not preview
        return Art(
            self,
            data_dir,
            settings,
            key,
            preview=preview,
            settled=settled,
            subject=subject,
            cell=cell,
        )


class Art:
    """One fetch's view of a collection: ask for pictures, start what is
    missing within the rules, and say what is pending."""

    def __init__(
        self,
        c: Collection,
        data_dir: Path,
        settings: dict[str, Any],
        key: str,
        *,
        preview: bool,
        settled: bool,
        subject: str,
        cell: str,
    ) -> None:
        self.c = c
        self.data_dir = data_dir
        self.settings = settings
        self.key = key
        self.preview = preview
        self.settled = settled
        self.subject = subject
        self.cell = cell
        self.cap = c.cap(settings)
        self.started = 0
        self.states: dict[str, int] = {}
        self.asked: dict[str, dict[str, Any]] = {}

    def _count(self, state: str) -> None:
        self.states[state] = self.states.get(state, 0) + 1

    def request(
        self, key: str, payload: dict[str, Any], *, title: str = "", want: bool = True
    ) -> Got:
        """The picture under ``key``, or what is being done about it. With
        ``want`` false the picture is only looked up, never started."""
        if not KEY_RE.fullmatch(key):
            return Got(
                key,
                "failed",
                why="a key is lowercase letters, digits, dots, dashes and underscores",
            )
        title = title or key
        c = self.c
        p = c.path(key, self.data_dir)
        if p is not None:
            self._count("ready")
            if want:
                self.asked[key] = {"payload": payload, "title": title}
            return Got(key, "ready", path=p, meta=c.meta(key, self.data_dir))
        if not want:
            return Got(key, "none")
        self.asked[key] = {"payload": payload, "title": title}
        st = c.state(key, self.data_dir)
        if st == "running":
            self._count("painting")
            return Got(key, "painting")
        if st == "failed":
            self._count("failed")
            return Got(key, "failed", why=c.why_failed(key, self.data_dir))
        if not self.key:
            self._count("no_key")
            return Got(key, "no_key")
        if (self.cap < UNLIMITED and c.budget_used(self.data_dir) >= self.cap) or ceiling_spent():
            self._count("capped")
            return Got(key, "capped")
        job = c.job(
            key,
            payload,
            title,
            self.data_dir,
            self.key,
            origin="preview" if self.preview else "panel",
            subject=self.subject,
            cell=self.cell,
        )
        if self.preview and not self.settled:
            c.hold(self.data_dir, job)
            self._count("held")
            return Got(key, "held")
        if self.preview and self.started >= c.per_preview():
            self._count("waiting")
            return Got(key, "waiting")
        if c.start(job, self.cap):
            self.started += 1
            c.unhold(self.data_dir, [key])
            self._count("painting")
            return Got(key, "painting")
        self._count("painting")
        return Got(key, "painting")

    def url(
        self,
        key: str,
        *,
        px: int = 600,
        levels: str = "colour",
        cut: bool = False,
        variant: str = "",
    ) -> str:
        return self.c.url(key, self.data_dir, px=px, levels=levels, cut=cut, variant=variant)

    def picture(self, key: str) -> tuple[bytes, dict[str, Any]] | None:
        return self.c.picture(key, self.data_dir)

    @property
    def pending(self) -> int:
        return self.states.get("painting", 0) + self.states.get("waiting", 0)

    @property
    def busy(self) -> bool:
        return any(self.states.get(k) for k in ("painting", "waiting", "held"))

    def live(self) -> dict[str, int]:
        """The fields a fetch returns so editors and a warm-ahead know work is
        on the way: ``live_refresh_s`` and ``live_pending``."""
        return {"live_refresh_s": LIVE_S if self.busy else 0, "live_pending": self.pending}

    def notes(self) -> list[str]:
        """Standard sentences for the page foot: no key, budget spent, failures, held."""
        out: list[str] = []
        s = self.states
        if s.get("no_key"):
            out.append(
                "AI pictures need a fal.ai key: this widget's own, or the app's under Settings."
            )
        if s.get("capped"):
            out.append(
                f"AI pictures: to-day's budget of {dollars(self.cap)} is spent; more to-morrow."
            )
        if s.get("failed"):
            n = s["failed"]
            out.append(f"{n} picture{'' if n == 1 else 's'} failed; tried again later")
        if s.get("held"):
            n = len(
                [j for j in self.c.held(self.data_dir).values() if j.get("subject") == self.subject]
            )
            out.append(f"{n} picture{'' if n == 1 else 's'} waiting for Paint now in the job tray")
        return out

    def status(self) -> list[str]:
        s = self.states
        out: list[str] = []
        if s.get("painting") or s.get("waiting"):
            total = sum(s.get(k, 0) for k in ("ready", "painting", "waiting", "held", "failed"))
            out.append(f"painting {min(s.get('ready', 0) + 1, total)} of {total}")
        return out

    def finish(
        self, ctx: dict[str, Any], facet: str = "", extra: dict[str, Any] | None = None
    ) -> None:
        """Close the fetch: a preview culls what it no longer wants and puts
        the waiting card in the tray; a panel render records what its
        dashboard asked for."""
        c = self.c
        if self.preview:
            c.cull(self.subject, set(self.asked))
            stale = [
                k
                for k, j in c.held(self.data_dir).items()
                if j.get("subject") == self.subject and k not in self.asked
            ]
            if stale:
                c.unhold(self.data_dir, stale)
            if self.states.get("held"):
                n = len(
                    [j for j in c.held(self.data_dir).values() if j.get("subject") == self.subject]
                )
                _tray_waiting(
                    c.plugin_id,
                    c.label,
                    c.page_path,
                    n,
                    lambda: c.start_held(self.data_dir, self.key, self.settings, self.subject),
                )
        elif self.asked:
            c.note_dashboard(self.data_dir, ctx, self.subject, facet, self.asked, extra)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _whole(value: Any, default: int, low: int = 0, high: int = 100000) -> int:
    with contextlib.suppress(TypeError, ValueError):
        if str(value).strip() != "":
            return min(high, max(low, int(float(value))))
    return default


def _derive(raw: bytes, px: int, levels: str, cut: bool) -> bytes:
    """Resize and convert for the panel: a JPEG, or a PNG with its alpha kept."""
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - Pillow ships with Tesserae
        return raw
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    except Exception:
        return b""
    if im is None:
        return b""
    im = im.convert("RGBA" if cut else "RGB")
    im.thumbnail((px, px), Image.Resampling.LANCZOS)
    if levels != "colour":
        cutoff = {"gray16": 0.5, "gray8": 0.5}.get(levels, 1.0)
        alpha = im.getchannel("A") if cut else None
        grey = ImageOps.autocontrast(im.convert("RGB").convert("L"), cutoff=cutoff)
        if levels == "mono":
            # the panel dithers every mid-tone to a texture, so lift them a little
            grey = grey.point([round(255 * (i / 255) ** 0.8) for i in range(256)])
        im = Image.merge("LA", (grey, alpha)) if alpha is not None else grey
    out = io.BytesIO()
    if cut:
        im.save(out, "PNG", optimize=True)
    else:
        im.save(out, "JPEG", quality=82, optimize=True, progressive=True)
    return out.getvalue()


def paper_of(raw: bytes) -> str:
    """The page colour round a picture, as #rrggbb: the median of a thin band
    along its edges. The default cream when it cannot be told."""
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(raw)).convert("RGB")
        w, h = im.size
        b = max(2, min(w, h) // 40)
        px: list[tuple[int, int, int]] = []
        for box in ((0, 0, w, b), (0, h - b, w, h), (0, 0, b, h), (w - b, 0, w, h)):
            px.extend(im.crop(box).getdata())
        if not px:
            return "#f3ead6"
        r = sorted(p[0] for p in px)[len(px) // 2]
        g = sorted(p[1] for p in px)[len(px) // 2]
        bl = sorted(p[2] for p in px)[len(px) // 2]
        return f"#{r:02x}{g:02x}{bl:02x}"
    except Exception:
        return "#f3ead6"


def image_data_uri(raw: bytes) -> str:
    return fal.data_uri(raw)


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()
