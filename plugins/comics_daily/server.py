"""comics_daily, today's comics on one page: Far Side panels over full-width strips.

The sources:

* **GoComics** strips (Garfield, Calvin and Hobbes, Pearls Before Swine, any
  ``gocomics.com/<name>``), from the ``og:image`` of the day's page. The site
  sits behind Bunny Shield, so the client here keeps a cookie session in
  ``data_dir`` and solves the proof of work when the site asks (see
  ``bunny_shield.py``).
* **Dilbert**, a random archived strip a day from dilbert-viewer, a Heroku
  app that may need waking up first.
* **The Far Side** Daily Dose panels and their captions from thefarside.com.
* Any strip can instead come from a direct image address, the manual way
  round a source that is blocking downloads.

Downloads are slow (a cold dyno, a proof of work, a dozen pictures), far past
the few seconds a render gives ``fetch()``. So ``fetch()`` never downloads:
it answers from the last good pictures on disk and, when a source is due,
queues it for one background worker, reporting the work in the job tray and
as ``live_refresh_s`` so editors repaint and a push is followed up once the
new pictures land. ``render.warm_ahead_s`` starts that work ahead of a
scheduled send, and ``updates.on_schedule`` lets a placement refresh daily.

A source is due once per local day. A GoComics strip whose newest edition is
still yesterday's (the server's day can start before the strip is published)
is retried a few times later that day. A failure keeps the last good picture,
backs off, and is reported in the data so the page can say what is stale.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import importlib.util
import json
import logging
import os
import random
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from email.message import Message
from http.cookiejar import CookieJar, LoadError, MozillaCookieJar
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    has_app_context,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.wrappers import Response

try:
    from app import jobs
except ImportError:  # an older Tesserae without the job tray
    jobs = None  # type: ignore[assignment]

try:
    from app.capabilities import CapabilityDenied
except ImportError:  # pragma: no cover - every supported host has it

    class CapabilityDenied(RuntimeError):  # type: ignore[no-redef]
        pass


_HERE = Path(__file__).resolve().parent
PLUGIN_ID = _HERE.name


def _load_sibling(name: str) -> ModuleType:
    """Load a helper module beside this file (docs/widgets.md, "Helper modules
    beside server.py"). Registered in ``sys.modules`` under a name unique to
    this widget so dataclasses inside it resolve their own module."""
    module_name = f"_tesserae_plugins.{PLUGIN_ID}.{name}"
    spec = importlib.util.spec_from_file_location(module_name, _HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


bunny_shield = _load_sibling("bunny_shield")
pages = _load_sibling("pages")
lifecycle = _load_sibling("media_lifecycle")
images = _load_sibling("images")
cookies = _load_sibling("cookies")
screen = _load_sibling("screen")
config_file = _load_sibling("config_file")

logger = logging.getLogger(__name__)

# -- sources -------------------------------------------------------------------

GOCOMICS = "https://www.gocomics.com"
FARSIDE_URL = "https://www.thefarside.com/"
DILBERT = "https://dilbert-viewer.herokuapp.com"
# The archive the viewer serves: the first and the last Dilbert strip.
DILBERT_FIRST = date(1989, 4, 16)
DILBERT_LAST = date(2023, 3, 12)
DILBERT_TRIES = 3
# GoComics: today's page, then the two days before when a day is missing.
GOCOMICS_DAYS_BACK = 3
# The warm-up page, far enough back that no strip fetch asks for it: Bunny
# Shield challenges a session that revisits an address.
WARM_DAYS_BACK = 7
FARSIDE_MAX_PANELS = 4
MAX_STRIPS = config_file.MAX_STRIPS

DEFAULT_LABELS = {
    "garfield": "Garfield",
    "calvinandhobbes": "Calvin and Hobbes",
    "pearlsbeforeswine": "Pearls Before Swine",
    "dilbert": "Dilbert",
    "farside": "The Far Side",
    "peanuts": "Peanuts",
    "nonsequitur": "Non Sequitur",
    "bc": "B.C.",
    "doonesbury": "Doonesbury",
}

# Every request looks like the desktop browser the sites were scraped with.
# BunnyCDN answers its challenge page to anything without these two Accept
# headers, cookie or no cookie, so they go on every request, images too.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

PAGE_TIMEOUT_S = 20
IMAGE_TIMEOUT_S = 30
# A sleeping Heroku dyno takes a while to answer its first request.
WAKE_TIMEOUT_S = 45
PAGE_MAX_BYTES = 4 * 1024 * 1024
IMAGE_MAX_BYTES = 20 * 1024 * 1024

# -- refresh timing --------------------------------------------------------------

# After a failed try a source is asked again on media_lifecycle.py's
# backoff (30 minutes doubling, at most 6 hours apart, TRIES_PER_DAY a day;
# the record's ``retry``). A record from before that kept only
# ``last_attempt``: it reads as one failed try. These two are the old waits,
# kept for reference by older tests.
RETRY_MISSING_S = 15 * 60
RETRY_STALE_S = 60 * 60
# A GoComics strip still on yesterday's edition: look again this often, this
# many times a day. The newest strip shows meanwhile, so this is not urgent.
EDITION_RETRY_S = 3 * 3600
EDITION_ATTEMPTS = 5
# GoComics publishes each day's strips at midnight US Eastern. Once this much
# later, a strip still on the day before is reported as not updated.
GOCOMICS_TZ = "America/New_York"
PUBLISH_GRACE = timedelta(hours=3)
# How long the very first render waits for pictures before painting the
# "fetching" state; the composer gives a widget six seconds in all.
FIRST_RENDER_WAIT_S = 4.0
# Editors poll, and a push is followed up, this often while work is queued.
LIVE_REFRESH_S = 8

COOKIE_FILE = "gocomics-cookies.txt"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_GOCOMICS_PAGE_RE = re.compile(r"^https?://(?:www\.)?gocomics\.com/([a-z0-9-]+)", re.IGNORECASE)


class SourceError(Exception):
    """A source could not be fetched. The message is shown to the user."""


class NotFound(SourceError):
    """GoComics has no strip by that name."""


class NoRecentStrip(SourceError):
    """GoComics has the strip but nothing from the last few days (a weekly
    strip, say), or no strip by that name: its pages all answer 404."""


def _allowed_hosts() -> frozenset[str] | None:
    """Hosts the manifest's ``requires:`` lets this widget reach; None for any.

    Read from plugin.json so the manual image address check and the host's
    socket-level enforcement can never disagree.
    """
    try:
        manifest = json.loads((_HERE / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    hosts = set()
    for cap in manifest.get("requires") or []:
        if isinstance(cap, str) and cap.startswith("network:"):
            hosts.add(cap.split(":", 1)[1].lower())
    return None if "*" in hosts else frozenset(hosts)


ALLOWED_HOSTS = _allowed_hosts()


def _host(url: str) -> str:
    """The URL's host, "" when it has none or doesn't parse (``https://[``)."""
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


# -- HTTP -----------------------------------------------------------------------


@dataclass
class Reply:
    status: int
    headers: Message
    body: bytes
    url: str

    def text(self) -> str:
        charset = self.headers.get_content_charset() or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


class Web:
    """A small browser: one opener, optionally with a cookie jar.

    Error statuses come back as replies rather than exceptions, because the
    Bunny Shield challenge arrives as a 403 whose body matters. Only a
    transport failure raises, as a :class:`SourceError` naming the host.
    """

    def __init__(self, jar: CookieJar | None = None) -> None:
        handlers = [urllib.request.HTTPCookieProcessor(jar)] if jar is not None else []
        self._opener = urllib.request.build_opener(*handlers)

    def get(
        self,
        url: str,
        *,
        timeout: float = PAGE_TIMEOUT_S,
        limit: int = PAGE_MAX_BYTES,
        headers: dict[str, str] | None = None,
    ) -> Reply:
        request = urllib.request.Request(url, headers={**BROWSER_HEADERS, **(headers or {})})
        return self._send(request, timeout, limit)

    def post(
        self, url: str, data: bytes, *, headers: dict[str, str], timeout: float = PAGE_TIMEOUT_S
    ) -> Reply:
        request = urllib.request.Request(
            url, data=data, method="POST", headers={**BROWSER_HEADERS, **headers}
        )
        return self._send(request, timeout, PAGE_MAX_BYTES)

    def _send(self, request: urllib.request.Request, timeout: float, limit: int) -> Reply:
        host = _host(request.full_url)
        try:
            response: Any = self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as err:
            response = err
        except CapabilityDenied as err:
            raise SourceError(f"{host} is not one of the sites this widget may reach.") from err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise SourceError(f"Couldn't reach {host}.") from err
        try:
            with contextlib.closing(response):
                has_body = getattr(response, "fp", None) is not None
                body = response.read(limit + 1) if has_body else b""
        except (TimeoutError, OSError) as err:
            raise SourceError(f"{host} stopped answering.") from err
        if len(body) > limit:
            raise SourceError(f"{host} sent more than this widget will download.")
        headers = getattr(response, "headers", None) or Message()
        body = decode_body(body, str(headers.get("Content-Encoding") or ""), limit, host)
        status = int(getattr(response, "status", None) or getattr(response, "code", 0) or 0)
        return Reply(status, headers, body, getattr(response, "url", "") or request.full_url)


def decode_body(body: bytes, encoding: str, limit: int, host: str) -> bytes:
    """Undo a gzip or deflate ``Content-Encoding``, never past ``limit`` bytes.

    urllib sends no Accept-Encoding, but a CDN may compress anyway. The cap
    applies to the decoded size, so a small compressed answer can't expand
    into a huge one; other codings pass through unchanged.
    """
    coding = encoding.lower().strip()
    if coding in ("gzip", "x-gzip"):
        attempts = [zlib.MAX_WBITS | 16]
    elif coding == "deflate":
        # Zlib-wrapped in theory, raw in some servers' practice.
        attempts = [zlib.MAX_WBITS, -zlib.MAX_WBITS]
    else:
        return body
    for wbits in attempts:
        try:
            return _inflate(body, wbits, limit, multi=coding != "deflate")
        except zlib.error:
            continue
        except OverflowError as err:
            raise SourceError(f"{host} sent more than this widget will download.") from err
    raise SourceError(f"{host} sent a damaged compressed answer.")


def _inflate(body: bytes, wbits: int, limit: int, *, multi: bool) -> bytes:
    """Decompress every member of ``body`` (gzip allows several back to back)
    under one cumulative ``limit``. A stream that ends before its trailer, or
    leaves bytes after it that aren't another member, is a ``zlib.error``;
    going past ``limit`` is an OverflowError."""
    out = bytearray()
    data = body
    while True:
        decoder = zlib.decompressobj(wbits)
        out += decoder.decompress(data, limit + 1 - len(out))
        if len(out) > limit or decoder.unconsumed_tail:
            raise OverflowError("decoded body over the limit")
        if not decoder.eof:
            raise zlib.error("truncated compressed stream")
        data = decoder.unused_data
        if not data:
            return bytes(out)
        if not multi:
            raise zlib.error("data after the end of the stream")


def _download(web: Web, url: str, what: str) -> bytes:
    """A picture's bytes, or SourceError with ``what`` named."""
    reply = web.get(url, timeout=IMAGE_TIMEOUT_S, limit=IMAGE_MAX_BYTES)
    if reply.status >= 400:
        raise SourceError(f"The picture for {what} answered HTTP {reply.status}.")
    return reply.body


# -- what a cell asks for --------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """One thing to show. ``key`` names its record in state.json and is
    shared by every cell that shows the same strip. A ``manual`` source is
    the picture uploaded or imported for a strip on the admin page: it is
    never fetched."""

    key: str
    kind: str  # "farside" | "gocomics" | "dilbert" | "url" | "manual"
    name: str  # the strip's name ("garfield")
    url: str = ""
    title: str = ""  # the label set for the strip, if any

    @property
    def label(self) -> str:
        return self.title or default_label(self.name)

    @property
    def fetched(self) -> bool:
        return self.kind != "manual"


def default_label(name: str) -> str:
    return DEFAULT_LABELS.get(name) or name.replace("-", " ").title()


FARSIDE = Source(key="farside", kind="farside", name="farside")


@dataclass(frozen=True)
class Problem:
    """A line of the Strips option that can't be fetched, and why."""

    name: str
    message: str


def _source_for(name: str, url: str) -> Source | Problem:
    if url:
        host = _host(url)
        if not _URL_RE.match(url) or not host or any(c.isspace() for c in url):
            return Problem(name, "That image address isn't a web address.")
        if ALLOWED_HOSTS is not None and host not in ALLOWED_HOSTS:
            return Problem(
                name,
                f"{host or 'That address'} is not one of the comic sites this widget may "
                "download from.",
            )
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        return Source(key=f"url:{digest}", kind="url", name=name, url=url)
    if name == "dilbert":
        return Source(key="dilbert", kind="dilbert", name=name)
    return Source(key=f"gocomics:{name}", kind="gocomics", name=name)


def parse_strips(text: str) -> tuple[list[Source], list[Problem]]:
    """Read the Strips option: names top to bottom, one per line (commas work
    too), each optionally followed by a direct image address that replaces the
    automatic download. A GoComics page address stands for its strip's name,
    and a line starting with ``#`` is skipped, which turns a strip off without
    losing its place."""
    entries: list[tuple[str, str]] = []
    problems: list[Problem] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        named_on_line = False
        for token in re.split(r"[\s,]+", line):
            if not token:
                continue
            page = _GOCOMICS_PAGE_RE.match(token)
            if page:
                entries.append((page.group(1).lower(), ""))
                named_on_line = True
            elif _URL_RE.match(token):
                if named_on_line and not entries[-1][1]:
                    entries[-1] = (entries[-1][0], token)
                else:
                    problems.append(Problem(token[:40], "Put a strip name before the address."))
            else:
                name = token.lower()
                if _NAME_RE.match(name):
                    entries.append((name, ""))
                    named_on_line = True
                else:
                    problems.append(Problem(token[:40], "That isn't a strip name."))
    sources: list[Source] = []
    seen: set[str] = set()
    for name, url in entries:
        if name in seen:
            continue
        seen.add(name)
        found = _source_for(name, url)
        if isinstance(found, Problem):
            problems.append(found)
        elif len(sources) < MAX_STRIPS:
            sources.append(found)
    return sources, problems


# -- the settings ------------------------------------------------------------------
#
# The comics are chosen once, on the widget's admin page (Widgets > Admin
# pages > Comics), and every cell shows them. They live in the widget's own
# data_dir as config.json (config_file.py), not as manifest settings.
#
# config.json is started the first time the widget needs it (a render, the
# admin page or Fetch now). An install that kept these values in
# settings.json (plugins > comics_daily, before config.json) has them copied
# over, and they stay there untouched, so going back to that version still
# works. Before that, cells carried their own options (far_side,
# far_side_count, captions, a ``strips`` text box, status_note, gap,
# gap_min, gap_max): with nothing in settings.json the file is started from
# the first comics cell whose options were changed from the old defaults,
# else from the defaults, so a dashboard looks the same after the upgrade. A
# render with no app to ask (a direct call, the tests) reads config.json
# when there is one and otherwise the cell's own options.
MODES = config_file.MODES
# What a cell's options defaulted to before the settings existed.
LEGACY_DEFAULTS: dict[str, Any] = {
    "far_side": True,
    "far_side_count": 4,
    "captions": True,
    "strips": "garfield\ncalvinandhobbes\npearlsbeforeswine\ndilbert",
    "status_note": True,
    "gap": 32,
    "gap_min": 6,
    "gap_max": 48,
}


@dataclass(frozen=True)
class StripRow:
    """One row of the ``strips`` setting."""

    slug: str
    enabled: bool = True
    label: str = ""
    mode: str = "auto"
    image_url: str = ""


@dataclass(frozen=True)
class Config:
    """What every comics cell shows, from the settings (or a cell's own
    options where there are no settings to read)."""

    strips: tuple[StripRow, ...]
    far_side: bool = True
    far_side_count: int = FARSIDE_MAX_PANELS
    captions: bool = True
    status_note: bool = True
    gap: int = 32
    gap_min: int = 6
    gap_max: int = 48
    small_screens: str = "fit"  # "fit" (as many as fit) | "one" (one at a time)

    def layout(self) -> dict[str, Any]:
        """The cell options client.js reads, as the settings say."""
        return {
            "far_side": self.far_side,
            "far_side_count": self.far_side_count,
            "captions": self.captions,
            "status_note": self.status_note,
            "gap": self.gap,
            "gap_min": self.gap_min,
            "gap_max": self.gap_max,
        }


def _common(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "far_side": _bool(values.get("far_side"), True),
        "far_side_count": _count(values.get("far_side_count"), 4, 1, FARSIDE_MAX_PANELS),
        "captions": _bool(values.get("captions"), True),
        "status_note": _bool(values.get("status_note"), True),
        "gap": _count(values.get("gap"), 32, 0, 200),
        "gap_min": _count(values.get("gap_min"), 6, 0, 200),
        "gap_max": _count(values.get("gap_max"), 48, 0, 400),
        "small_screens": "one" if values.get("small_screens") == "one" else "fit",
    }


def strip_rows(value: Any) -> list[StripRow]:
    """The ``strips`` setting's rows, leniently: a row without a usable
    strip name is skipped, an unknown source falls back to Automatic."""
    rows: list[StripRow] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        slug = str(raw.get("slug") or "").strip().lower()
        if not _NAME_RE.match(slug):
            continue
        mode = raw.get("mode") if raw.get("mode") in MODES else "auto"
        rows.append(
            StripRow(
                slug=slug,
                enabled=_bool(raw.get("enabled"), True),
                label=str(raw.get("label") or "").strip(),
                mode=str(mode),
                image_url=str(raw.get("image_url") or "").strip(),
            )
        )
    return rows


def config_from_settings(settings: dict[str, Any]) -> Config:
    return Config(strips=tuple(strip_rows(settings.get("strips"))), **_common(settings))


def legacy_strip_rows(text: str) -> list[dict[str, Any]]:
    """A cell's old Strips text as rows of the setting: one row per strip
    name in order, a ``#`` line as a row switched off, an image address
    after a name as that row's Direct image URL."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        enabled = not line.startswith("#")
        sources, _ = parse_strips(line.lstrip("#"))
        for source in sources:
            if source.name in seen:
                continue
            seen.add(source.name)
            rows.append(
                {
                    "enabled": enabled,
                    "label": default_label(source.name),
                    "slug": source.name,
                    "mode": "url" if source.url else "auto",
                    "image_url": source.url,
                }
            )
    if "dilbert" not in seen:
        # Dilbert is built into the list (it can only be switched off).
        rows = rows[: MAX_STRIPS - 1]
        rows.append(
            {
                "enabled": False,
                "label": "Dilbert",
                "slug": "dilbert",
                "mode": "auto",
                "image_url": "",
            }
        )
    return rows[:MAX_STRIPS]


def config_from_options(options: dict[str, Any]) -> Config:
    """A cell's own options, as the widget read them before it had settings."""
    rows = strip_rows(legacy_strip_rows(str(options.get("strips") or "")))
    return Config(strips=tuple(rows), **_common(options))


def customised(options: dict[str, Any]) -> bool:
    """Whether a cell's options were changed from the old defaults: only then
    are they worth seeding the settings from."""
    for key, default in LEGACY_DEFAULTS.items():
        if key not in options:
            continue
        value = options[key]
        if key == "strips":
            if str(value or "").split() != str(default).split():
                return True
        elif isinstance(default, bool):
            if _bool(value, default) != default:
                return True
        elif _count(value, default, -1000, 1000) != default:
            return True
    return False


def seed_values(options: dict[str, Any] | None, defaults: dict[str, Any]) -> dict[str, Any]:
    """The settings to start from: a customised cell's options turned into
    settings, else the manifest's defaults (``defaults``)."""
    if not options or not customised(options):
        return dict(defaults)
    seeded = dict(defaults)
    seeded.update(_common(options))
    if "strips" in options:
        seeded["strips"] = legacy_strip_rows(str(options.get("strips") or ""))
    return seeded


def sources_for(config: Config) -> tuple[list[Source], list[Problem]]:
    """What the settings show, top to bottom: the Far Side, then each strip
    switched on, from where its Source says. A row that can't be shown is a
    Problem the page notes."""
    wanted: list[Source] = [FARSIDE] if config.far_side else []
    problems: list[Problem] = []
    for row in config.strips:
        if not row.enabled:
            continue
        if len(wanted) - (1 if config.far_side else 0) >= MAX_STRIPS:
            break
        found = row_source(row)
        if isinstance(found, Problem):
            problems.append(found)
        else:
            wanted.append(found)
    return wanted, problems


def wanted_keys(config: Config) -> set[str]:
    """Every record the settings still name, shown or not: the Far Side,
    and for each strip in the list (on or off) its download, its Direct
    image URL and its manual picture, so switching a strip off or between
    sources never loses its last good picture."""
    keys = {FARSIDE.key}
    for row in config.strips:
        keys.add(f"manual:{row.slug}")
        keys.add("dilbert" if row.slug == "dilbert" else f"gocomics:{row.slug}")
        if row.image_url:
            found = _source_for(row.slug, row.image_url)
            if isinstance(found, Source):
                keys.add(found.key)
    return keys


def row_source(row: StripRow) -> Source | Problem:
    """Where one strip row's picture comes from, as its Source says."""
    if row.mode == "upload":
        return Source(key=f"manual:{row.slug}", kind="manual", name=row.slug, title=row.label)
    if row.mode == "url" and not row.image_url:
        return Problem(row.label or row.slug, "Give it an image URL first.")
    found = _source_for(row.slug, row.image_url if row.mode == "url" else "")
    if isinstance(found, Problem):
        return Problem(row.label or found.name, found.message)
    return Source(key=found.key, kind=found.kind, name=found.name, url=found.url, title=row.label)


def _settings_store() -> Any:
    """The app's settings store, when there is an app to ask (a render or an
    admin request); None for a direct call."""
    try:
        return current_app.config.get("SETTINGS_STORE")
    except RuntimeError:
        return None


def _first_cell_options() -> dict[str, Any] | None:
    """The options of the first comics cell (grid or canvas) whose options
    were changed from the old defaults, in the page store's order."""
    try:
        page_store = current_app.config.get("PAGE_STORE")
        pages_list = page_store.list() if page_store is not None else []
    except Exception:
        return None
    for page in pages_list:
        placements = [c.options for c in getattr(page, "cells", []) if c.plugin == PLUGIN_ID]
        canvas = getattr(page, "canvas", None)
        for element in getattr(canvas, "els", None) or []:
            if element.kind == "widget" and element.widget == PLUGIN_ID:
                placements.append(element.options)
        for options in placements:
            if isinstance(options, dict) and customised(options):
                return dict(options)
    return None


def legacy_settings(store: Any) -> dict[str, Any]:
    """The values this widget kept in settings.json before config.json
    (section ``plugins``, key ``comics_daily``): only those stored, as
    stored. Empty when none were."""
    if store is None:
        return {}
    saved = (store.get_section("plugins") or {}).get(PLUGIN_ID)
    if not isinstance(saved, dict):
        return {}
    stored = [key for key in config_file.LEGACY_KEYS if key in saved]
    if not stored:
        return {}
    values = store.get_for_runtime("plugins", PLUGIN_ID, config_file.LEGACY_FIELDS)
    return {key: values[key] for key in stored if key in values}


def first_values(hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """What config.json starts from: the values kept in settings.json, when
    there are any; strips from there, else from the first customised comics
    cell, else ``hint`` (the cell being rendered) when it is customised,
    else the defaults."""
    legacy = legacy_settings(_settings_store())
    cell = None
    if "strips" not in legacy:
        cell = _first_cell_options() or (hint if hint and customised(hint) else None)
    values = seed_values(cell, config_file.defaults())
    values.update(legacy)
    used = [
        name for name, found in (("settings.json", legacy), ("a cell's options", cell)) if found
    ]
    logger.info("comics_daily: config.json started from %s", " and ".join(used) or "the defaults")
    return values


def config_store(data_dir: Path | str) -> Any:
    """The widget's config.json in ``data_dir``."""
    return config_file.ConfigFile(data_dir, legacy_rows=legacy_strip_rows)


def _plugin_data_dir() -> Path:
    plugin = current_app.config["PLUGIN_REGISTRY"].get(PLUGIN_ID)
    if plugin is None:
        abort(404)
    return Path(plugin.data_dir)


def ensure_settings(
    data_dir: Path | str | None = None, hint: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """The values in config.json (``data_dir``, by default the plugin's),
    starting the file first if there is none (see the notes above). With no
    app to ask, config.json when there is one, else None."""
    if not has_app_context():
        return config_store(data_dir).read() if data_dir is not None else None
    values: dict[str, Any] = config_store(data_dir or _plugin_data_dir()).load(
        lambda: first_values(hint)
    )
    return values


# The GoComics cookies pasted on the Comics page: kept in the app's settings
# store as an encrypted value under a field of its own, which nothing draws
# (the widget declares no settings), so it is never sent back to a browser.
COOKIE_FIELD: dict[str, Any] = {"name": "gocomics_cookies", "type": "string", "secret": True}


def saved_cookie_header() -> str:
    """The pasted cookies as a Cookie header, "" when none or no app."""
    store = _settings_store()
    if store is None:
        return ""
    try:
        value = store.get_for_runtime("plugins", PLUGIN_ID, [COOKIE_FIELD]).get(
            COOKIE_FIELD["name"]
        )
    except Exception:  # a key that no longer decrypts reads as none
        return ""
    return value if isinstance(value, str) else ""


def save_cookie_header(header: str) -> None:
    store = _settings_store()
    if store is None:
        raise RuntimeError("no settings store")
    store.update_for_namespace("plugins", PLUGIN_ID, {COOKIE_FIELD["name"]: header}, [COOKIE_FIELD])


def resolve_config(
    options: dict[str, Any], settings: dict[str, Any], data_dir: Path | str | None = None
) -> Config:
    """What a render shows: config.json (started on first use), or with no
    app to ask and no config.json, ``settings`` when they carry strips,
    else the cell's own options."""
    values = ensure_settings(data_dir, options)
    if values is not None:
        return config_from_settings(values)
    if isinstance(settings.get("strips"), list):
        return config_from_settings(settings)
    return config_from_options(options)


# -- fetching each source --------------------------------------------------------


@dataclass
class Fetched:
    """What a source produced: raw pictures with captions, and its date.
    ``expected`` is how many pictures a complete set has, when some of them
    may have failed to download (the Far Side's panels)."""

    pictures: list[tuple[bytes, str]]
    edition: str | None = None
    label: str | None = None
    expected: int | None = None


class GoComics:
    """A gocomics.com session that gets past Bunny Shield.

    The cookie jar persists in ``data_dir`` so most days reuse yesterday's
    session; a fresh one is set up only when no shield cookie is saved or a
    page answers with the challenge. Setting one up visits a page the strip
    fetches never ask for (the first strip a week back), solves the proof of
    work there, and confirms the page then loads. At most one proof of work
    per refresh: if the site still challenges after that, it is blocking us.
    """

    def __init__(
        self, cookie_file: Path, today: date, warm_name: str = "", pasted: str = ""
    ) -> None:
        self._cookie_file = cookie_file
        self._jar = MozillaCookieJar()
        self._today = today
        # A session was set up in this refresh / setting one up was tried.
        self._ready = False
        self._warmed = False
        # The strip whose week-old page sets a session up: the first strip
        # asked for, unless the caller names a known one (checking a name
        # that may not exist must not warm up on it).
        self._warm_name = warm_name
        with contextlib.suppress(OSError, LoadError):
            if cookie_file.is_file():
                self._jar.load(str(cookie_file), ignore_discard=True)
        # Cookies pasted on the Comics page (cookies.py) go first, over any
        # saved session of the same name; they are never written to the
        # session file. If GoComics still challenges, the session falls back
        # to the built-in browser check. ``pasted_result`` says how they did
        # in this session: "worked", "turned away" or None (not used).
        found = cookies.from_header(pasted) if pasted else None
        self._pasted = cookies.jar_cookies(found) if found else []
        self.pasted_result: str | None = None
        names = {c.name for c in self._pasted}
        for old in [c for c in self._jar if c.name in names]:
            self._jar.clear(old.domain, old.path, old.name)
        for cookie in self._pasted:
            self._jar.set_cookie(cookie)
        self._web = Web(self._jar)

    def _using_pasted(self) -> bool:
        return any(c.name == p.name and c.value == p.value for c in self._jar for p in self._pasted)

    def strip(self, name: str, label: str) -> Fetched:
        self._warm_name = self._warm_name or name
        if not self._ready and not bunny_shield.has_session(self._jar):
            if self._warmed:
                raise SourceError("GoComics is blocking automatic downloads right now.")
            self._warm()
        for back in range(GOCOMICS_DAYS_BACK):
            day = self._today - timedelta(days=back)
            reply = self._page(f"{GOCOMICS}/{name}/{day:%Y/%m/%d}")
            if reply.status == 404:
                continue
            if reply.status >= 400:
                raise SourceError(f"GoComics answered HTTP {reply.status} for {label}.")
            page = pages.parse_gocomics(reply.text(), name)
            if page is None:
                raise NotFound(f"GoComics has no strip called “{name}”.")
            raw = _download(self._web, page.image_url, label)
            self._save()
            return Fetched([(raw, "")], edition=page.date or day.isoformat(), label=page.label)
        raise NoRecentStrip(f"GoComics has no recent {label} strip.")

    def exists(self, name: str) -> bool:
        """Whether gocomics.com has a strip called ``name`` at all: its page
        answers. For telling a weekly strip from a name that isn't one."""
        self._warm_name = self._warm_name or name
        if not self._ready and not bunny_shield.has_session(self._jar):
            self._warm()
        reply = self._page(f"{GOCOMICS}/{name}")
        if reply.status == 404:
            return False
        if reply.status >= 400:
            raise SourceError(f"GoComics answered HTTP {reply.status}.")
        return True

    def _page(self, url: str) -> Reply:
        pasted = self._using_pasted()
        reply = self._web.get(url)
        if not self._challenged(reply):
            if pasted and self.pasted_result is None:
                self.pasted_result = "worked"
            return reply
        if pasted:
            self.pasted_result = "turned away"
            logger.info("comics_daily: GoComics turned the pasted cookies away")
        if self._warmed:
            raise SourceError("GoComics is blocking automatic downloads right now.")
        # The saved session has gone stale. Start over on a page this refresh
        # won't ask for, then ask for this one in the new session.
        logger.info("comics_daily: GoComics session expired, setting up a new one")
        self._jar.clear()
        self._warm()
        reply = self._web.get(url)
        if self._challenged(reply):
            raise SourceError("GoComics is blocking automatic downloads right now.")
        return reply

    @staticmethod
    def _challenged(reply: Reply) -> bool:
        return bunny_shield.is_challenge(reply.headers, reply.text())

    def _warm(self) -> None:
        self._warmed = True
        day = self._today - timedelta(days=WARM_DAYS_BACK)
        url = f"{GOCOMICS}/{self._warm_name}/{day:%Y/%m/%d}"
        reply = self._web.get(url)
        if not self._challenged(reply):
            if reply.status >= 400:
                raise SourceError(f"GoComics answered HTTP {reply.status}.")
            self._ready = True
            self._save()
            return
        data_pow = bunny_shield.find_data_pow(reply.text())
        if not data_pow:
            raise SourceError("GoComics changed its browser check; it can't be passed.")
        try:
            answer = bunny_shield.solve(data_pow)
        except ValueError as err:
            raise SourceError("GoComics changed its browser check; it can't be passed.") from err
        except bunny_shield.SolverUnavailable as err:
            raise SourceError(
                "GoComics wants a browser check this server can't solve: it needs "
                "cryptography 44 or newer (update Tesserae), or give the strip an "
                "image address."
            ) from err
        if answer is None:
            raise SourceError("GoComics' browser check took too long to solve.")
        verify = self._web.post(
            GOCOMICS + bunny_shield.VERIFY_PATH,
            b"{}",
            headers={
                "Content-Type": "application/json",
                bunny_shield.RESPONSE_HEADER: bunny_shield.challenge_response(data_pow, answer),
                "Origin": GOCOMICS,
                "Referer": url,
            },
        )
        if verify.status >= 400:
            raise SourceError("GoComics turned down its browser check answer.")
        if self._challenged(self._web.get(url)):
            raise SourceError("GoComics is blocking automatic downloads right now.")
        logger.info("comics_daily: passed the GoComics browser check")
        self._ready = True
        self._save()

    def _save(self) -> None:
        """Persist the session, owner-only, replacing the file atomically.
        Session cookies are kept too: the shield cookie is one."""
        self._cookie_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".cookies.", suffix=".tmp", dir=self._cookie_file.parent)
        os.close(fd)
        session = MozillaCookieJar()
        for cookie in self._jar:
            if not any(cookie.name == p.name and cookie.value == p.value for p in self._pasted):
                session.set_cookie(cookie)
        try:
            session.save(tmp, ignore_discard=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._cookie_file)
        except OSError:
            logger.warning("comics_daily: could not save the GoComics session")
            with contextlib.suppress(OSError):
                os.unlink(tmp)


def fetch_farside(web: Web) -> Fetched:
    reply = web.get(FARSIDE_URL)
    if bunny_shield.is_challenge(reply.headers, reply.text()):
        raise SourceError("The Far Side is blocking automatic downloads right now.")
    if reply.status >= 400:
        raise SourceError(f"thefarside.com answered HTTP {reply.status}.")
    panels = pages.parse_farside(reply.text(), FARSIDE_MAX_PANELS)
    if not panels:
        raise SourceError("No Far Side panels on thefarside.com today.")
    pictures: list[tuple[bytes, str]] = []
    failure: SourceError | None = None
    for panel in panels:
        try:
            pictures.append((_download(web, panel.image_url, "The Far Side"), panel.caption))
        except SourceError as err:
            failure = err
    if not pictures:
        raise failure or SourceError("No Far Side panels could be downloaded.")
    return Fetched(pictures, expected=len(panels))


def fetch_dilbert(web: Web, today: date) -> Fetched:
    """A strip from the archive, the same one all day: the day seeds the pick."""
    with contextlib.suppress(SourceError):
        # Wake the dyno; a slow first answer is normal and the next is quick.
        web.get(f"{DILBERT}/", timeout=WAKE_TIMEOUT_S)
    picker = random.Random(today.toordinal())
    span = (DILBERT_LAST - DILBERT_FIRST).days
    failure = SourceError("The Dilbert archive didn't return a strip.")
    for _ in range(DILBERT_TRIES):
        day = DILBERT_FIRST + timedelta(days=picker.randrange(span + 1))
        try:
            reply = web.get(f"{DILBERT}/{day.isoformat()}")
        except SourceError as err:
            failure = err
            continue
        strip = pages.parse_dilbert(reply.text()) if reply.status < 400 else None
        if strip is None:
            continue
        try:
            raw = _download(web, strip.image_url, "Dilbert")
        except SourceError as err:
            failure = err
            continue
        return Fetched([(raw, "")], edition=strip.date)
    raise failure


def fetch_image_url(web: Web, source: Source) -> Fetched:
    return Fetched([(_download(web, source.url, source.label), "")])


# -- the refresh worker ----------------------------------------------------------


def _today() -> date:
    """The local day, in the timezone set under Settings when there is one."""
    try:
        from app.tz_resolve import app_timezone

        return datetime.now(app_timezone()).date()
    except Exception:
        return datetime.now().astimezone().date()


def is_due(source: Source, record: dict[str, Any] | None, today: str, now: float) -> bool:
    """Whether ``source`` should be fetched now, given its record."""
    if not record:
        return True
    last_attempt = float(record.get("last_attempt") or 0)
    if record.get("fetched_on") == today:
        edition = record.get("edition")
        return bool(
            source.kind == "gocomics"
            and edition
            and edition < today
            and record.get("attempted_on") == today
            and int(record.get("attempts") or 0) < EDITION_ATTEMPTS
            and now - last_attempt >= EDITION_RETRY_S
        )
    retry = record.get("retry")
    if not isinstance(retry, dict) and last_attempt:
        day = str(record.get("attempted_on") or today)
        retry = {"day": day, "tries": 1, "after": last_attempt + lifecycle.RETRY_FIRST_S}
    return lifecycle.may_try(retry, today, now)


@dataclass
class _Run:
    """One background refresh: a queue a render can add to while it runs.

    Queue entries carry a ``force`` flag (``?fresh=1``), which skips the
    due checks. ``completed`` remembers what this run already fetched, so a
    render working from a snapshot taken before that can't queue it again.
    """

    data_dir: Path
    today: date
    queue: list[tuple[Source, bool]] = field(default_factory=list)
    active: Source | None = None
    completed: set[str] = field(default_factory=set)
    done: bool = False
    finished: int = 0
    failed: int = 0
    thread: threading.Thread | None = None
    # The GoComics cookies pasted on the Comics page, as the latest render
    # or admin request read them (the worker has no settings to read).
    pasted: str = ""
    # Every source the settings name, for the cleanup at the run's end;
    # None when the caller didn't say (a direct call).
    wanted: set[str] | None = None

    def keys(self) -> set[str]:
        keys = {s.key for s, _ in self.queue}
        if self.active is not None:
            keys.add(self.active.key)
        return keys

    def alive(self) -> bool:
        return not self.done and self.thread is not None and self.thread.is_alive()


# Kept outside this module (see images.shared) so a reload of the plugin
# finds the worker already running instead of starting a second one.
_RUNS: dict[str, _Run] = images.shared("runs", dict)
_RUNS_LOCK: threading.Lock = images.shared("runs_lock", threading.Lock)


def _enqueue(
    data_dir: Path,
    sources: list[Source],
    today: date,
    *,
    force: bool = False,
    pasted: str | None = None,
    wanted: set[str] | None = None,
) -> _Run:
    """Queue ``sources`` on the running refresh, starting one if none runs.

    A run whose thread has died without finishing is replaced, not reused.
    The worker runs in a copy of the caller's context, so the widget's
    capability scope (its ``requires:`` hosts) still applies off-thread.
    """
    with _RUNS_LOCK:
        run = _RUNS.get(str(data_dir))
        start = run is None or not run.alive()
        if start:
            run = _Run(data_dir=data_dir, today=today)
            _RUNS[str(data_dir)] = run
        assert run is not None
        if pasted is not None:
            run.pasted = pasted
        if wanted is not None:
            run.wanted = wanted
        known = run.keys() | (set() if force else run.completed)
        run.queue.extend((s, force) for s in sources if s.key not in known)
        if start:
            context = contextvars.copy_context()
            run.thread = threading.Thread(
                target=context.run, args=(_work, run), name="comics-daily-refresh", daemon=True
            )
            run.thread.start()
    return run


def _running_keys(data_dir: Path) -> set[str]:
    with _RUNS_LOCK:
        run = _RUNS.get(str(data_dir))
        return run.keys() if run is not None and run.alive() else set()


def _report(run: _Run, source: Source) -> None:
    if jobs is None:
        return
    total = run.finished + len(run.queue) + 1
    with contextlib.suppress(Exception):
        jobs.report(
            PLUGIN_ID,
            "refresh",
            "Fetching today's comics",
            progress=run.finished / total,
            detail=source.label,
            label="Comics",
        )


def _finish_job(run: _Run, crashed: bool) -> None:
    if jobs is None:
        return
    with contextlib.suppress(Exception):
        summary = f"{run.finished - run.failed} of {run.finished} updated"
        if crashed or (run.failed and run.failed == run.finished):
            jobs.fail(PLUGIN_ID, "refresh", summary)
        else:
            jobs.finish(PLUGIN_ID, "refresh", summary)


def _work(run: _Run) -> None:
    """Drain the run's queue, one source at a time.

    Whatever happens, the run ends marked done with nothing active, so the
    next render starts a fresh worker rather than waiting on a dead one.
    """
    store = images.Store(run.data_dir)
    gocomics: GoComics | None = None
    web = Web()
    crashed = False
    try:
        while True:
            with _RUNS_LOCK:
                if not run.queue:
                    run.active = None
                    run.done = True
                    break
                source, force = run.queue.pop(0)
                run.active = source
            # The render that queued this read the state before earlier work
            # in this run landed; ask the state again, now.
            record = store.sources().get(source.key)
            if not force and not is_due(source, record, run.today.isoformat(), time.time()):
                continue
            _report(run, source)
            if source.kind == "gocomics" and gocomics is None:
                gocomics = GoComics(run.data_dir / COOKIE_FILE, run.today, pasted=run.pasted)
            if not refresh_source(store, source, run.today, web=web, gocomics=gocomics):
                run.failed += 1
            run.finished += 1
            run.completed.add(source.key)
    except Exception:
        crashed = True
        logger.exception("comics_daily: the refresh worker stopped")
    finally:
        with _RUNS_LOCK:
            run.active = None
            run.done = True
        if gocomics is not None:
            note_pasted_result(store, gocomics)
        with contextlib.suppress(Exception):
            gone = store.prune(wanted=run.wanted)
            if gone:
                logger.info("comics_daily: removed %d unused pictures", len(gone))
        _finish_job(run, crashed)


def note_pasted_result(store: Any, gocomics: GoComics) -> None:
    """Keep how the pasted cookies did in this session, for the Comics page."""
    result = gocomics.pasted_result
    if result is None:
        return
    with contextlib.suppress(Exception):
        store.update_cookie_status(lambda status: status.update(result=result, used_at=time.time()))
        log_entry(store, "GoComics cookies", "ok" if result == "worked" else "blocked", result)


def _fetch_source(source: Source, today: date, web: Web, gocomics: GoComics | None) -> Fetched:
    if source.kind == "farside":
        return fetch_farside(web)
    if source.kind == "dilbert":
        return fetch_dilbert(web, today)
    if source.kind == "url":
        return fetch_image_url(web, source)
    if gocomics is None:
        raise SourceError("GoComics session missing.")
    return gocomics.strip(source.name, source.label)


def refresh_source(
    store: Any, source: Source, today: date, *, web: Web, gocomics: GoComics | None
) -> bool:
    """Fetch one source and record the outcome; True when it fully updated.

    The previous pictures stay unless a complete new set was stored, so a
    failure never blanks the page and a half-downloaded Far Side row never
    replaces a whole one. Storage failures (a full disk) are logged and
    reported as a failed source, never raised into the worker.
    """
    today_iso = today.isoformat()
    started = time.time()

    def attempt(entry: dict[str, Any]) -> None:
        if entry.get("attempted_on") != today_iso:
            entry["attempted_on"] = today_iso
            entry["attempts"] = 0
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        entry["last_attempt"] = started

    try:
        store.update(source.key, attempt)
    except Exception:
        logger.exception("comics_daily: could not record an attempt at %s", source.key)
        return False
    try:
        fetched: Fetched | SourceError = _fetch_source(source, today, web, gocomics)
    except SourceError as err:
        fetched = err
    except Exception:
        logger.exception("comics_daily: fetching %s failed", source.key)
        fetched = SourceError(f"Couldn't update {source.label}.")
    return record_fetch(store, source, fetched, today, started)


def record_fetch(
    store: Any, source: Source, fetched: Fetched | SourceError, today: date, started: float
) -> bool:
    """Keep what a fetch of ``source`` brought (or the error it ended in),
    note it in the log, and say whether the source fully updated."""
    today_iso = today.isoformat()
    stored: list[dict[str, Any]] = []
    expected = 0
    if isinstance(fetched, Fetched):
        try:
            stored = _store_pictures(store, source, fetched)
        except SourceError as err:
            fetched = err
        except Exception:
            logger.exception("comics_daily: storing %s failed", source.key)
            fetched = SourceError(f"Couldn't keep the pictures for {source.label}.")
    if isinstance(fetched, SourceError):
        outcome: Callable[[dict[str, Any]], None] = _failed(str(fetched), today_iso)
        complete = False
    else:
        expected = max(fetched.expected or 0, len(fetched.pictures))
        complete = len(stored) >= expected
        outcome = _succeeded(source, fetched, stored, expected, today_iso)
    try:
        entry = store.update(source.key, outcome)
    except Exception:
        logger.exception("comics_daily: could not record the outcome for %s", source.key)
        return False
    _log_fetch(store, source, entry, fetched, stored, expected, today_iso, time.time() - started)
    if complete and isinstance(fetched, Fetched):
        logger.info("comics_daily: updated %s (%s)", source.key, fetched.edition or today_iso)
    else:
        logger.warning("comics_daily: %s not updated: %s", source.key, entry.get("error"))
    return complete


def _size(stored: list[dict[str, Any]]) -> str:
    total = sum(int(p.get("bytes") or 0) for p in stored)
    return f"{total / 1048576:.1f} MB" if total >= 1048576 else f"{max(1, round(total / 1024))} KB"


def log_entry(store: Any, label: str, outcome: str, text: str) -> None:
    """One line of the admin page's log. Never raises: the log is a courtesy."""
    with contextlib.suppress(Exception):
        store.append_log({"at": time.time(), "source": label, "outcome": outcome, "text": text})


def _log_fetch(
    store: Any,
    source: Source,
    entry: dict[str, Any],
    fetched: Fetched | SourceError,
    stored: list[dict[str, Any]],
    expected: int,
    today: str,
    took: float,
) -> None:
    """Log one fetch: OK, stale (GoComics still on an older edition),
    partial, blocked (a site refusing automatic downloads) or error, with
    the edition, size and time taken."""
    label = str(entry.get("label") or source.label)
    if source.title:
        label = source.title
    seconds = f"{took:.1f} s"
    if isinstance(fetched, SourceError):
        message = str(fetched)
        outcome = "blocked" if "blocking" in message else "error"
        retry = lifecycle.describe(entry.get("retry"), today, time.time())
        kept = "; the last good picture stays up" if entry.get("pictures") else ""
        log_entry(
            store, label, outcome, f"{message} ({seconds}){kept}{'; ' + retry if retry else ''}"
        )
        return
    edition = fetched.edition or today
    count = len(stored)
    what = f"{count} picture{'' if count == 1 else 's'}, {_size(stored)} in {seconds}"
    if count < expected:
        log_entry(store, label, "partial", f"{count} of {expected} pictures, {_size(stored)}")
        return
    published = published_edition(datetime.now(UTC), today)
    if source.kind == "gocomics" and edition < published:
        log_entry(store, label, "stale", f"still the {edition} strip, {what}")
        return
    log_entry(store, label, "ok", f"{edition}, {what}")


def _failed(message: str, today: str) -> Callable[[dict[str, Any]], None]:
    """A failed try: the error, and the backoff before the next one. What
    was on disk stays up meanwhile."""

    def record(entry: dict[str, Any]) -> None:
        entry["error"] = message
        entry["retry"] = lifecycle.failed(entry.get("retry"), today, time.time())

    return record


def _succeeded(
    source: Source, fetched: Fetched, stored: list[dict[str, Any]], expected: int, today: str
) -> Callable[[dict[str, Any]], None]:
    def record(entry: dict[str, Any]) -> None:
        if len(stored) < expected:
            # Never let a partial set replace a complete one. A partial set
            # stands in when there is nothing else, and gives way to a fuller
            # one. Not marked fetched today, so it retries.
            cached = entry.get("pictures") or []
            if not cached or (entry.get("partial") and len(stored) > len(cached)):
                entry.update(pictures=stored, edition=fetched.edition, partial=True)
            entry["error"] = f"Only {len(stored)} of {expected} pictures came through."
            # An incomplete set is a failed try: the same backoff and daily
            # limit as any other.
            entry["retry"] = lifecycle.failed(entry.get("retry"), today, time.time())
            return
        entry.pop("retry", None)
        entry.update(
            pictures=stored,
            edition=fetched.edition,
            fetched_on=today,
            last_success=time.time(),
            error=None,
            partial=False,
        )
        if fetched.label:
            entry["label"] = fetched.label

    return record


def _store_pictures(store: Any, source: Source, fetched: Fetched) -> list[dict[str, Any]]:
    """Convert and keep each fetched picture; skip any that isn't one.
    Raises SourceError when none survive, which leaves the old pictures up."""
    stored = []
    for raw, caption in fetched.pictures:
        try:
            picture = images.normalise(raw)
        except ValueError:
            continue
        name = store.add(source.key, picture)
        stored.append(
            {
                "file": name,
                "w": picture.width,
                "h": picture.height,
                "caption": caption,
                "bytes": len(picture.data),
            }
        )
    if not stored:
        raise SourceError(f"The download for {source.label} wasn't a picture.")
    return stored


# -- widget entry point ----------------------------------------------------------


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return default if value is None else bool(value)


def _count(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(float(value))))
    except (TypeError, ValueError):
        return default


def _image_url(name: str) -> str:
    return f"/plugins/{PLUGIN_ID}/img/{name}"


def _pictures(record: dict[str, Any], store: Any) -> list[dict[str, Any]]:
    """The record's pictures whose files are still on disk."""
    out = []
    for pic in record.get("pictures") or []:
        if not isinstance(pic, dict):
            continue
        name = str(pic.get("file") or "")
        w, h = int(pic.get("w") or 0), int(pic.get("h") or 0)
        if w > 0 and h > 0 and store.image_path(name) is not None:
            out.append({"src": _image_url(name), "w": w, "h": h, "caption": pic.get("caption", "")})
    return out


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    store = images.Store(Path(ctx["data_dir"]))
    config = resolve_config(options, settings, ctx["data_dir"])
    wanted, problems = sources_for(config)
    if not wanted:
        with contextlib.suppress(Exception):
            store.prune_if_due(wanted_keys(config))
        if problems:
            return {"error": f"{problems[0].name}: {problems[0].message}"}
        return {"error": "Turn on a strip or the Far Side under Widgets → Admin pages → Comics."}

    today = _today()
    today_iso = today.isoformat()
    now = time.time()
    records = store.sources()
    force = bool(ctx.get("fresh"))
    due = [
        s for s in wanted if s.fetched and (force or is_due(s, records.get(s.key), today_iso, now))
    ]
    pasted = saved_cookie_header() if any(s.kind == "gocomics" for s in due) else None
    run = (
        _enqueue(store.root, due, today, force=force, pasted=pasted, wanted=wanted_keys(config))
        if due
        else None
    )
    if run is None:
        # No worker to clean up after itself (all manual, all up to date):
        # clean up here, at most hourly.
        with contextlib.suppress(Exception):
            store.prune_if_due(wanted_keys(config), now)
    if run is not None and not any(_pictures(records.get(s.key) or {}, store) for s in wanted):
        # Nothing to show yet: give a quick source the chance to land in this
        # render rather than painting "fetching" and waiting for the repaint.
        if run.thread is not None:
            run.thread.join(FIRST_RENDER_WAIT_S)
        records = store.sources()
    busy = _running_keys(store.root)
    published = published_edition(datetime.now(UTC), today_iso)
    payload = _payload(wanted, problems, records, busy, config, store, today_iso, published)
    payload["layout"] = config.layout()
    return fit_screen(payload, config, store, ctx, now)


def fit_screen(
    payload: dict[str, Any], config: Config, store: Any, ctx: dict[str, Any], now: float
) -> dict[str, Any]:
    """Keep only what this cell shows (screen.py): everything when it fits,
    else the comics whose turn it is. A settled render (a push, a warm-ahead)
    moves the placement's turn on, at most once per screen.HOLD_S; a preview
    shows the turn without moving it."""
    farside = payload.get("farside") or []
    strips = payload.get("strips") or []
    items = []
    if farside:
        items.append(
            screen.Item(
                "farside",
                tuple((int(p["w"]), int(p["h"])) for p in farside),
                tuple(str(p.get("caption") or "") for p in farside),
            )
        )
    items += [screen.Item("strip", ((int(s["w"]), int(s["h"])),)) for s in strips]
    if len(items) < 2:
        return payload
    settled = bool(ctx.get("settled", not ctx.get("preview")))
    # The placement's own turn: the host's placement id (page, cell,
    # device) when it gives one; else the options' id with the cell's size,
    # so identical cells on a big panel and a small one never share a turn.
    placement = str(ctx.get("placement_id") or "") or (
        f"{ctx.get('cell_id') or 'default'}:{ctx.get('cell_w') or 0}x{ctx.get('cell_h') or 0}"
    )

    def decide(start: int) -> Any:
        return screen.plan(
            ctx.get("cell_w"),
            ctx.get("cell_h"),
            items,
            far_side_count=config.far_side_count,
            captions=config.captions,
            note=config.status_note and bool(payload.get("notes")),
            gap_min=config.gap_min,
            one_at_a_time=config.small_screens == "one",
            start=start,
        )

    chosen = store.take_turn(placement, len(items), decide, advance=settled, now=now)
    if not chosen.rotating:
        return payload
    shown = set(chosen.show)
    offset = 1 if farside else 0
    payload["farside"] = farside if 0 in shown and farside else []
    payload["strips"] = [strip for i, strip in enumerate(strips) if (i + offset) in shown]
    # The order they take their turn in, the first shown on top.
    order = {index: n for n, index in enumerate(chosen.show)}
    payload["strips"].sort(key=lambda s: order.get(strips.index(s) + offset, 0))
    payload["turns"] = {"showing": len(chosen.show), "of": chosen.total}
    return payload


def published_edition(now: datetime, today: str) -> str:
    """The newest GoComics edition that should be out at ``now`` (aware).

    GoComics publishes at midnight US Eastern; after a few hours' grace the
    Eastern date is due. Never later than the local ``today``, the date the
    strip pages are asked for, so a server west of New York isn't told its
    strip is late before its own day has begun.
    """
    try:
        zone: Any = ZoneInfo(GOCOMICS_TZ)
    except ZoneInfoNotFoundError:
        zone = timezone(timedelta(hours=-5))
    eastern = (now.astimezone(zone) - PUBLISH_GRACE).date().isoformat()
    return min(eastern, today)


def freshness(
    source: Source,
    record: dict[str, Any],
    has_pictures: bool,
    busy: bool,
    today: str,
    published: str | None = None,
) -> tuple[str, str | None]:
    """How current a source's pictures are, and the date a note should show.

    ``ok``, ``stale`` (old pictures up), ``missing`` (none, and the last try
    failed) or ``pending``. A GoComics strip is judged by its own edition
    against the one that should be out by now (``published``), not by
    whether a request went through or how many were made: a page that keeps
    answering with an old strip is stale, and so is yesterday's once today's
    is due. Archived sources (Dilbert) and image addresses carry no such
    date and go by the last success.
    """
    error = record.get("error")
    if not has_pictures:
        return ("missing" if error and not busy else "pending"), None
    edition = record.get("edition")
    if source.kind == "gocomics" and isinstance(edition, str) and edition < (published or today):
        return "stale", edition
    if error and record.get("fetched_on") != today:
        return "stale", record.get("fetched_on")
    return "ok", None


def _payload(
    wanted: list[Source],
    problems: list[Problem],
    records: dict[str, dict[str, Any]],
    busy: set[str],
    config: Config,
    store: Any,
    today: str,
    published: str | None = None,
) -> dict[str, Any]:
    farside: list[dict[str, Any]] = []
    strips: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    for source in wanted:
        record = records.get(source.key) or {}
        pictures = _pictures(record, store)
        label = source.title or str(record.get("label") or source.label)
        error = record.get("error")
        busy_now = source.key in busy
        state, since = freshness(source, record, bool(pictures), busy_now, today, published)
        if source.kind == "manual" and not pictures:
            state, error = "missing", "No picture uploaded yet."
        sources.append(
            {
                "id": source.name,
                "label": label,
                "state": state,
                "updating": source.key in busy,
                "edition": record.get("edition"),
                "updated_on": record.get("fetched_on"),
                "message": error or "",
            }
        )
        if state in ("stale", "missing"):
            notes.append(
                {
                    "label": label,
                    "state": state,
                    "since": since,
                    "message": error or "",
                }
            )
        if source.kind == "farside":
            farside = pictures[: config.far_side_count]
        elif pictures:
            first = pictures[0]
            strips.append(
                {
                    "id": source.name,
                    "label": label,
                    "src": first["src"],
                    "w": first["w"],
                    "h": first["h"],
                    "date": record.get("edition"),
                }
            )
    for problem in problems:
        notes.append({"label": problem.name, "state": "invalid", "message": problem.message})

    pending = [s for s in sources if s["updating"]]
    payload: dict[str, Any] = {
        "farside": farside,
        "strips": strips,
        "sources": sources,
        "notes": notes,
        "updating": bool(pending),
        "today": today,
    }
    if pending:
        payload["live_refresh_s"] = LIVE_REFRESH_S
        payload["live_pending"] = len(pending)
    elif not farside and not strips:
        failed = [s["message"] for s in sources if s["state"] == "missing" and s["message"]]
        if failed:
            payload["error"] = "Couldn't fetch any comics yet. " + failed[0]
    return payload


# -- serving the pictures --------------------------------------------------------

# Read-only, so the loopback renderer may load the pictures with no session
# (docs/widgets.md, "Serving your own media").
RENDER_SAFE_ENDPOINTS: tuple[str, ...] = (f"{PLUGIN_ID}_media.image",)


def _store() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get(PLUGIN_ID)
    if plugin is None:
        abort(404)
    return images.Store(Path(plugin.data_dir))


# -- the admin page ---------------------------------------------------------------
#
# /plugins/comics_daily/: today's comics with a Fetch now button and a
# preview per screen; a card per source (the Far Side; GoComics: the strips
# in order, each with its status, edit, upload and import, adding a strip,
# the browser check and pasted cookies) and Layout, each saving through a
# plain form of its own; then the log of every fetch, upload and import.
# Every change is a POST that saves config.json and redirects back to the
# card it came from, so the page works without JavaScript (static/admin.js
# only adds niceties).
# Room for the multipart framing around an uploaded picture.
UPLOAD_OVERHEAD_BYTES = 64 * 1024
# The strip a session is set up on while checking a new name: one that
# surely exists, so a name that doesn't can be told apart.
WARM_STRIP = "garfield"
_ADD_RE = re.compile(r"^(?:https?://)?(?:www\.)?gocomics\.com/([A-Za-z0-9-]+)", re.IGNORECASE)


def parse_strip_input(text: str) -> str | None:
    """A GoComics strip name from what was typed: its address
    (``https://www.gocomics.com/garfield``, with or without a date after it)
    or its bare name. None when it is neither."""
    value = (text or "").strip()
    page = _ADD_RE.match(value)
    name = (page.group(1) if page else value).lower()
    return name if _NAME_RE.match(name) else None


def _admin_plugin() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get(PLUGIN_ID)
    if plugin is None:
        abort(404)
    return plugin


def _scope(plugin: Any) -> Any:
    """The widget's own network scope (its ``requires:`` hosts), for work an
    admin request starts, as a render's would run in."""
    try:
        from app.capabilities import capability_scope
    except ImportError:  # pragma: no cover - every supported host has it
        return contextlib.nullcontext()
    return capability_scope(plugin.capabilities)


def _admin_tz() -> Any:
    try:
        from app.tz_resolve import app_timezone

        return app_timezone()
    except Exception:
        return None


def _when(ts: Any, tz: Any, today: str) -> str:
    try:
        moment = datetime.fromtimestamp(float(ts), tz)
    except (TypeError, ValueError, OverflowError, OSError):
        return "at an unknown time"
    if moment.date().isoformat() == today:
        return f"today at {moment:%H:%M}"
    return f"{moment:%b} {moment.day} at {moment:%H:%M}"


def _day(iso: Any) -> str:
    try:
        day = date.fromisoformat(str(iso))
    except ValueError:
        return str(iso)
    return f"{day:%b} {day.day}"


def source_status(
    source: Source,
    record: dict[str, Any],
    store: Any,
    busy: set[str],
    today: str,
    published: str,
    tz: Any,
) -> dict[str, Any]:
    """How one source stands, for the admin page: a short chip (and its
    tone), a status line, a warning when it couldn't refresh automatically,
    and its pictures."""
    pictures = _pictures(record, store)
    error = str(record.get("error") or "")
    edition = record.get("edition")
    state, since = freshness(source, record, bool(pictures), source.key in busy, today, published)
    fetched = record.get("last_success")
    warning = ""
    if source.key in busy:
        chip, tone, line = "Fetching", "slate", "Fetching now…"
    elif source.kind == "manual":
        if not pictures:
            chip, tone = "No picture", "bad"
            line = "No picture yet: upload one, or import one from the Image URL."
        else:
            chip, tone = "Manual", "slate"
            origin = str(record.get("origin") or "uploaded")
            line = f"Manual picture, {origin} {_when(fetched, tz, today)}."
            if record.get("fetched_on") != today:
                warning = (
                    f"This picture is from {_day(record.get('fetched_on'))}. Switch the source "
                    "back to Automatic when the site works again."
                )
    elif state == "ok":
        chip, tone = "OK", "ok"
        if source.kind == "farside":
            what = "Today's panels"
        elif edition:
            what = f"The {_day(edition)} strip"
        else:
            what = "Today's picture"
        line = f"{what}, fetched {_when(fetched, tz, today)}."
    elif state == "pending":
        chip, tone, line = "Not fetched", "", "Not fetched yet: Fetch now gets it."
    else:
        blocked = "blocking" in error
        if blocked:
            chip, tone = "Blocked", "bad"
        elif state == "stale":
            chip, tone = "Stale", "warn"
        else:
            chip, tone = "Missing", "bad"
        if state == "stale":
            last = f"the {_day(edition)} strip" if edition else "the last good picture"
            line = f"Stale since {_day(since) if since else 'earlier'}: showing {last}"
            line += f", fetched {_when(fetched, tz, today)}." if fetched else "."
        else:
            line = "Nothing to show yet."
        if error:
            warning = f"Couldn't refresh automatically: {error}"
        elif edition:
            warning = f"The site still has the {_day(edition)} strip; it's asked again later today."
    return {
        "state": state,
        "chip": chip,
        "tone": tone,
        "line": line,
        "warning": warning,
        "pictures": pictures,
        # A GoComics strip the site is blocking: pasted cookies may help.
        "cookies_help": chip == "Blocked" and source.kind == "gocomics",
    }


def session_view(store: Any, tz: Any, today: str) -> dict[str, Any]:
    """The GoComics session the built-in browser check keeps (COOKIE_FILE)
    for the Comics page: whether a shield cookie is saved, and when the
    session was last updated (written after each GoComics fetch). Never the
    cookies."""
    path = store.root / COOKIE_FILE
    jar = MozillaCookieJar()
    try:
        jar.load(str(path), ignore_discard=True, ignore_expires=True)
        updated = path.stat().st_mtime
    except (OSError, LoadError):
        jar, updated = MozillaCookieJar(), None
    shields = [c for c in jar if c.name.startswith(bunny_shield.COOKIE_PREFIX)]
    if not shields:
        return {
            "set": False,
            "chip": "None yet",
            "tone": "",
            "line": "The next GoComics fetch passes the browser check and saves a session.",
        }
    line = f"Session cookie saved, last updated {_when(updated, tz, today)}"
    expires = max(bunny_shield.cookie_expiry(c) for c in shields)
    expired = not bunny_shield.has_session(shields)
    if expired:
        line += f", expired {_when(expires, tz, today)}. The next fetch sets up a new one."
    elif expires:
        line += f", expires {_when(expires, tz, today)}."
    else:
        line += ". GoComics keeps it until it asks for the check again."
    chip, tone = ("Expired", "warn") if expired else ("Saved", "ok")
    return {"set": True, "chip": chip, "tone": tone, "line": line}


def cookie_view(store: Any, tz: Any, today: str) -> dict[str, Any]:
    """The pasted GoComics cookies' state for the Comics page: whether any
    are set, a chip and a status line. Never the cookies."""
    if not saved_cookie_header():
        return {
            "set": False,
            "chip": "Not set",
            "tone": "",
            "line": "None pasted. Only needed when the browser check is blocked.",
        }
    status = store.cookie_status()
    count = int(status.get("count") or 0)
    expires = int(status.get("expires_at") or 0)
    parts = [f"Pasted {_when(status.get('saved_at'), tz, today)}"]
    if count:
        parts.append(f"{count} cookie{'' if count == 1 else 's'}")
    expired = 0 < expires < time.time()
    if expires:
        parts.append(f"{'expired' if expired else 'expire'} {_when(expires, tz, today)}")
    line = ", ".join(parts) + "."
    result = status.get("result")
    if result:
        used = _when(status.get("used_at"), tz, today)
        if result == "worked":
            line += f" Last used {used}: GoComics let them through."
        else:
            line += (
                f" Last used {used}: GoComics turned them away, so the built-in browser "
                "check was tried instead. Paste fresh ones."
            )
    if expired:
        chip, tone = "Expired", "warn"
    elif result == "turned away":
        chip, tone = "Turned away", "bad"
    elif result == "worked":
        chip, tone = "Working", "ok"
    else:
        chip, tone = "Set", "slate"
    return {"set": True, "chip": chip, "tone": tone, "line": line}


MINE = "Your screens"
REFERENCE = "Reference screens"
MAX_OWN_SCREENS = 8


def _dims(value: Any) -> tuple[int, int] | None:
    """An object's (w, h) when both are positive ints, else None."""
    try:
        width, height = int(value.w), int(value.h)
    except (AttributeError, TypeError, ValueError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def _comics_cells(page: Any) -> tuple[tuple[int, int] | None, list[tuple[int, int]]]:
    """A page's layout size (None when it takes its device's) and the size
    of each comics cell on it, grid or canvas."""
    if getattr(page, "layout_kind", "grid") == "canvas":
        canvas = getattr(page, "canvas", None)
        cells = [
            el
            for el in getattr(canvas, "els", None) or []
            if el.kind == "widget" and el.widget == PLUGIN_ID
        ]
        base = _dims(canvas)
    else:
        cells = [c for c in getattr(page, "cells", None) or [] if c.plugin == PLUGIN_ID]
        base = _dims(getattr(page, "panel", None))
    return base, [size for size in map(_dims, cells) if size]


def preview_screens(pages: list[Any], devices: list[Any]) -> tuple[list[dict[str, Any]], int]:
    """The screens the admin page previews and which one it opens on.

    First the user's own: each device a page with a comics cell is sent
    to, at the cell's real size (the panel's when the cell fills it),
    then their other devices at full panel size. Then the reference
    screens (screen.SCREENS). It opens on the first device that shows
    comics, else the first reference screen."""
    panels = {}
    for device in devices:
        panel = device.panel if getattr(device, "kind_of", None) else None
        if panel:
            panels[device.id] = (device.display_name, int(panel["w"]), int(panel["h"]))
    showing: list[dict[str, Any]] = []
    for page in pages:
        base, cells = _comics_cells(page)
        for device_id in getattr(page, "device_ids", None) or []:
            if device_id not in panels:
                continue
            name, pw, ph = panels[device_id]
            bw, bh = base or (pw, ph)
            for cw, ch in cells:
                width, height = round(cw * pw / bw), round(ch * ph / bh)
                if width >= 0.95 * pw and height >= 0.95 * ph:
                    width, height = pw, ph
                label = name if (width, height) == (pw, ph) else f"{name}, comics cell"
                showing.append({"name": label, "w": width, "h": height, "device": device_id})
        if not getattr(page, "device_ids", None):
            for cw, ch in cells:
                showing.append({"name": f"Comics cell on {page.name}", "w": cw, "h": ch})
    seen: set[tuple[str, int, int]] = set()
    own: list[dict[str, Any]] = []
    shown_on = {s.get("device") for s in showing}
    others = [
        {"name": name, "w": w, "h": h}
        for device_id, (name, w, h) in panels.items()
        if device_id not in shown_on
    ]
    for entry in showing + others:
        key = (entry["name"], entry["w"], entry["h"])
        if key not in seen and len(own) < MAX_OWN_SCREENS:
            seen.add(key)
            own.append({"name": entry["name"], "w": entry["w"], "h": entry["h"], "group": MINE})
    reference = [{"name": n, "w": w, "h": h, "group": REFERENCE} for n, w, h in screen.SCREENS]
    return own + reference, 0 if showing else len(own)


def _your_screens() -> tuple[list[dict[str, Any]], int]:
    """preview_screens() from the app's pages and devices (just the
    reference screens without an app to ask)."""
    try:
        page_store = current_app.config.get("PAGE_STORE")
        registry = current_app.config.get("DEVICE_REGISTRY")
        pages = page_store.list_active() if page_store is not None else []
        devices = registry.all() if registry is not None else []
    except Exception:
        pages, devices = [], []
    try:
        return preview_screens(pages, devices)
    except Exception:
        logger.warning("comics_daily: couldn't list the user's screens", exc_info=True)
        return preview_screens([], [])


def screens_view(
    config: Config,
    farside: dict[str, Any] | None,
    preview: list[dict[str, Any]],
    screens: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """What each screen shows on its first turn, and in words how the rest
    take turns, for the admin page's preview. ``screens`` defaults to the
    reference screens (screen.SCREENS)."""
    if screens is None:
        screens = [{"name": n, "w": w, "h": h, "group": REFERENCE} for n, w, h in screen.SCREENS]
    pictures: list[tuple[str, list[dict[str, Any]]]] = []
    if farside and farside.get("pictures"):
        pictures.append(("The Far Side", farside["pictures"]))
    pictures += [(item["label"], item["pictures"][:1]) for item in preview if item.get("pictures")]
    items = [
        screen.Item(
            "farside" if label == "The Far Side" and n == 0 and farside else "strip",
            tuple((int(p["w"]), int(p["h"])) for p in pics),
            tuple(str(p.get("caption") or "") for p in pics),
        )
        for n, (label, pics) in enumerate(pictures)
    ]
    out = []
    for n, sc in enumerate(screens):
        name, width, height = sc["name"], sc["w"], sc["h"]
        turns = screen.turns(
            width,
            height,
            items,
            far_side_count=config.far_side_count,
            captions=config.captions,
            note=False,
            gap_min=config.gap_min,
            one_at_a_time=config.small_screens == "one",
        )
        chosen = turns[0] if turns else screen.Plan((), False, 0)
        shown = [pictures[i] for i in chosen.show]
        names = ", ".join(label for label, _ in shown)
        if not items:
            text = "Nothing to show yet."
        elif not chosen.rotating:
            text = f"Everything at once: {names}."
        else:
            steps = [" and ".join(pictures[i][0] for i in turn.show) for turn in turns]
            text = f"Takes turns, one refresh each: {'; then '.join(steps)}."
        out.append(
            {
                "id": n,
                "name": name,
                "group": sc.get("group", REFERENCE),
                "w": width,
                "h": height,
                "text": text,
                "shown": [
                    {"label": label, "pictures": pics, "farside": label == "The Far Side"}
                    for label, pics in shown
                ],
            }
        )
    return out


# How the admin page's log marks an outcome: a problem, or one to watch.
LOG_TONES = {"blocked": "bad", "error": "bad", "stale": "warn", "partial": "warn"}


def admin_view(store: Any, config: Config, tz: Any) -> dict[str, Any]:
    """Everything the admin page shows besides the forms."""
    today = _today().isoformat()
    published = published_edition(datetime.now(UTC), today)
    records = store.sources()
    busy = _running_keys(store.root)

    def status(source: Source) -> dict[str, Any]:
        return source_status(
            source, records.get(source.key) or {}, store, busy, today, published, tz
        )

    strips: dict[str, dict[str, Any]] = {}
    for row in config.strips:
        found = row_source(row)
        if isinstance(found, Problem):
            entry = {"chip": "Can't show", "tone": "bad", "line": found.message}
            entry.update(warning=found.message, pictures=[], state="invalid")
        else:
            entry = status(found)
            entry["label"] = found.label
        if not row.enabled:
            entry.update(chip="Off", tone="", warning="")
        manual = records.get(f"manual:{row.slug}") or {}
        entry["manual"] = bool(_pictures(manual, store))
        entry["label"] = row.label or default_label(row.slug)
        entry["kind"] = "Dilbert archive" if row.slug == "dilbert" else "GoComics"
        strips[row.slug] = entry

    wanted, problems = sources_for(config)
    farside = status(FARSIDE) if config.far_side else None
    if farside is not None:
        farside["pictures"] = farside["pictures"][: config.far_side_count]
    shown = [s for s in wanted if s.kind != "farside"]
    preview = [{"label": s.label, **strips.get(s.name, status(s))} for s in shown]
    every = ([{"label": FARSIDE.label, **farside}] if farside else []) + preview
    attention = [
        f"{item['label']} ({item['chip'].lower()})"
        for item in every
        if item["chip"] in ("Blocked", "Stale", "Missing", "No picture", "Can't show")
    ]
    attention += [f"{p.name} ({p.message.rstrip('.').lower()})" for p in problems]
    lede: list[str] = []
    fetching = [item["label"] for item in every if item["chip"] == "Fetching"]
    if fetching:
        lede.append(f"Fetching now: {', '.join(fetching)}.")
    elif not every:
        lede.append("Nothing to show: switch on a strip or the Far Side below.")
    elif attention:
        verb = "needs" if len(attention) == 1 else "need"
        lede.append(f"{len(attention)} of {len(every)} {verb} a look: {', '.join(attention)}.")
    else:
        lede.append(f"All {len(every)} are up to date.")
    attempts = [float(r.get("last_attempt") or 0) for r in records.values()]
    if attempts and max(attempts) > 0:
        lede.append(f"Last fetch {_when(max(attempts), tz, today)}.")

    screens, default_screen = _your_screens()

    # The log, oldest first, as columns: when, source, outcome, what.
    log = []
    for entry in store.log():
        when = "?"
        with contextlib.suppress(TypeError, ValueError, OverflowError, OSError):
            when = datetime.fromtimestamp(float(entry["at"]), tz).strftime("%b %d %H:%M:%S")
        outcome = str(entry.get("outcome") or "").lower()
        log.append(
            {
                "when": when,
                "source": str(entry.get("source") or "-")[:40],
                "outcome": outcome,
                "tone": LOG_TONES.get(outcome, ""),
                "text": str(entry["text"]),
            }
        )
    return {
        "lede": " ".join(lede),
        "screens": screens_view(config, farside, preview, screens),
        "screen_default": default_screen,
        "cookies": cookie_view(store, tz, today),
        "session": session_view(store, tz, today),
        "running": bool(busy),
        "farside": farside,
        "preview": preview,
        "strips": strips,
        "problems": problems,
        "log": log,
    }


# The choices the admin page's selects offer, as (value, label).
MODE_CHOICES = (
    ("auto", "Automatic"),
    ("upload", "Manual upload"),
    ("url", "Direct image URL"),
)
SMALL_SCREEN_CHOICES = (
    ("fit", "As many as fit, taking turns with the rest"),
    ("one", "One at a time, taking turns"),
)


def _stored_rows(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in values.get("strips") or [] if isinstance(row, dict)]


def _update(
    data_dir: Path, change: Callable[[dict[str, Any]], dict[str, Any] | None]
) -> dict[str, Any] | None:
    """Change config.json in one step under its lock (starting it first if
    there is none): ``change`` gets the values as stored now, not as they
    were when the request began (a slow download or another tab may have
    changed them), and returns the new values, or None to leave them. They
    are checked as every save is (config_file.check, ConfigError when they
    don't fit). Returns what was written, or None."""
    ensure_settings(data_dir)
    written: dict[str, Any] | None = config_store(data_dir).update(change)
    return written


def _patch_rows(
    data_dir: Path, change: Callable[[list[dict[str, Any]]], list[dict[str, Any]] | None]
) -> bool:
    """_update for the strip rows alone: ``change`` gets the rows as stored
    now and returns the new rows, or None. True when they were written."""

    def patch(values: dict[str, Any]) -> dict[str, Any] | None:
        rows = change(_stored_rows(values))
        return None if rows is None else {**values, "strips": rows}

    return _update(data_dir, patch) is not None


def _set_row(
    slug: str, **values: Any
) -> Callable[[list[dict[str, Any]]], list[dict[str, Any]] | None]:
    """A _patch_rows change setting ``values`` on the row for ``slug``, if
    that row is still there."""

    def change(rows: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        row = next((r for r in rows if r.get("slug") == slug), None)
        if row is None:
            return None
        row.update(values)
        return rows

    return change


def _back(anchor: str = "gocomics") -> Response:
    return redirect(url_for(f"{PLUGIN_ID}_media.index", _anchor=anchor))


def _row_label(row: dict[str, Any]) -> str:
    return str(row.get("label") or default_label(str(row.get("slug") or "")))


def _keep_manual(
    data_dir: Path,
    row: dict[str, Any],
    picture: Any,
    origin: str,
    url: str = "",
) -> None:
    """Keep ``picture`` as the strip's manual picture and switch the strip
    to it (Manual upload) until it is switched back to Automatic."""
    store = images.Store(data_dir)
    slug = str(row["slug"])
    key = f"manual:{slug}"
    name = store.add(key, picture)
    now = time.time()
    today = _today().isoformat()
    stored = {"file": name, "w": picture.width, "h": picture.height, "caption": ""}
    stored["bytes"] = len(picture.data)

    def record(entry: dict[str, Any]) -> None:
        entry.update(
            pictures=[stored],
            fetched_on=today,
            last_success=now,
            last_attempt=now,
            error=None,
            partial=False,
            origin=origin,
            origin_url=url,
        )

    store.update(key, record)
    keep_url = url and config_file.is_web_address(url) and len(url) <= config_file.URL_MAX
    switch = {"mode": "upload", **({"image_url": url} if keep_url else {})}
    if not _patch_rows(data_dir, _set_row(slug, **switch)):
        logger.info("comics_daily: %s left the list while its picture was kept", slug)
    detail = f"{picture.width}×{picture.height} px, {_size([stored])}"
    log_entry(store, _row_label(row), origin.split()[0], f"{origin}: {detail}")


def _checked(name: str) -> bool:
    """A checkbox of the posted form: present means on."""
    return request.form.get(name) not in (None, "", "off", "false", "0")


def blueprint() -> Blueprint:
    bp = Blueprint(f"{PLUGIN_ID}_media", __name__, template_folder="templates")

    @bp.get("/")
    def index() -> str:
        data_dir = _plugin_data_dir()
        values = ensure_settings(data_dir) or config_file.defaults()
        config = config_from_settings(values)
        store = images.Store(data_dir)
        with contextlib.suppress(Exception):
            store.prune_if_due(wanted_keys(config))
        view = admin_view(store, config, _admin_tz())
        # ?screen=N: the preview picked without JavaScript (the picker's
        # Show button); with it, the picker swaps the preview in place.
        picked = request.args.get("screen", type=int)
        if picked is not None and 0 <= picked < len(view["screens"]):
            view["screen_default"] = picked
        return render_template(
            "comics_daily/index.html",
            values=values,
            rows=_stored_rows(values),
            view=view,
            fixed_slug=config_file.FIXED_SLUG,
            numbers=config_file.NUMBERS,
            mode_choices=MODE_CHOICES,
            small_screen_choices=SMALL_SCREEN_CHOICES,
            max_strips=MAX_STRIPS,
            upload_mb=images.MAX_UPLOAD_BYTES // (1024 * 1024),
        )

    def _save_card(change: dict[str, Any], anchor: str, done: str) -> Response:
        try:
            _update(_plugin_data_dir(), lambda values: {**values, **change})
        except config_file.ConfigError as err:
            flash(f"Not saved: {err}", "error")
        else:
            flash(done, "ok")
        return _back(anchor)

    def _posted(*names: str) -> dict[str, Any]:
        """The posted values of these fields; one not posted keeps its value."""
        return {name: request.form[name] for name in names if name in request.form}

    @bp.post("/far-side")
    def save_far_side() -> Response:
        change = {
            "far_side": _checked("far_side"),
            "captions": _checked("captions"),
            **_posted("far_side_count"),
        }
        return _save_card(change, "farside", "Saved the Far Side settings.")

    @bp.post("/layout")
    def save_layout() -> Response:
        change = {
            "status_note": _checked("status_note"),
            **_posted("small_screens", "gap_min", "gap", "gap_max"),
        }
        return _save_card(change, "layout", "Saved the layout.")

    @bp.post("/fetch")
    def fetch_now() -> Response:
        plugin = _admin_plugin()
        config = config_from_settings(ensure_settings(Path(plugin.data_dir)) or {})
        wanted, _ = sources_for(config)
        sources = [s for s in wanted if s.fetched]
        store = images.Store(Path(plugin.data_dir))
        if not sources:
            flash("Nothing to fetch: every strip shown is a manual picture, or none is on.", "warn")
        else:
            with _scope(plugin):
                _enqueue(
                    store.root,
                    sources,
                    _today(),
                    force=True,
                    pasted=saved_cookie_header(),
                    wanted=wanted_keys(config),
                )
            names = ", ".join(s.label for s in sources)
            log_entry(store, "Fetch now", "started", f"asked for {names}")
            flash("Fetching today's comics now; the job tray shows how far it got.", "ok")
        return redirect(url_for(f"{PLUGIN_ID}_media.index", _anchor="today"))

    def _to_cookies() -> Response:
        return redirect(url_for(f"{PLUGIN_ID}_media.index", _anchor="cookies"))

    @bp.post("/cookies")
    def save_cookies() -> Response:
        plugin = _admin_plugin()
        try:
            pasted = cookies.parse_paste(request.form.get("cookies", ""))
        except cookies.PasteError as err:
            flash(str(err), "error")
            return _to_cookies()
        save_cookie_header(pasted.header())
        store = images.Store(Path(plugin.data_dir))
        store.update_cookie_status(None)
        store.update_cookie_status(
            lambda status: status.update(
                saved_at=time.time(), expires_at=pasted.expires_at, count=len(pasted.pairs)
            )
        )
        count = len(pasted.pairs)
        log_entry(store, "GoComics cookies", "saved", f"{count} pasted")
        flash(
            f"Saved {count} GoComics cookie{'' if count == 1 else 's'}. The next GoComics "
            "fetch uses them; Fetch now tries them at once.",
            "ok",
        )
        return _to_cookies()

    @bp.post("/cookies/clear")
    def clear_cookies() -> Response:
        plugin = _admin_plugin()
        save_cookie_header("")
        store = images.Store(Path(plugin.data_dir))
        store.update_cookie_status(None)
        log_entry(store, "GoComics cookies", "cleared", "the built-in browser check only")
        flash("Cleared the GoComics cookies; the built-in browser check carries on alone.", "ok")
        return _to_cookies()

    @bp.post("/strips/add")
    def add_strip() -> Response:
        plugin = _admin_plugin()
        data_dir = Path(plugin.data_dir)
        rows = _stored_rows(ensure_settings(data_dir) or {})
        slug = parse_strip_input(request.form.get("strip", ""))
        if slug is None:
            flash(
                "That isn't a GoComics address or strip name: paste the strip's address "
                "(https://www.gocomics.com/garfield) or type its name (garfield).",
                "error",
            )
            return _back()
        existing = next((row for row in rows if row.get("slug") == slug), None)
        if existing is not None:
            if not existing.get("enabled"):
                _patch_rows(data_dir, _set_row(slug, enabled=True))
                flash(f"{existing.get('label') or default_label(slug)} is switched back on.", "ok")
            else:
                flash(
                    f"{existing.get('label') or default_label(slug)} is already in the list.",
                    "warn",
                )
            return _back()
        if len(rows) >= MAX_STRIPS:
            flash(f"The list holds at most {MAX_STRIPS} strips; remove one first.", "error")
            return _back()
        store = images.Store(Path(plugin.data_dir))
        label, warning = default_label(slug), ""
        if slug != "dilbert":
            source = Source(key=f"gocomics:{slug}", kind="gocomics", name=slug)
            started = time.time()
            today = _today()
            outcome: Fetched | SourceError
            with _scope(plugin):
                warm = WARM_STRIP if slug != WARM_STRIP else ""
                gocomics = GoComics(
                    store.root / COOKIE_FILE, today, warm_name=warm, pasted=saved_cookie_header()
                )
                try:
                    outcome = gocomics.strip(slug, label)
                except NotFound as err:
                    flash(str(err), "error")
                    return _back()
                except NoRecentStrip as err:
                    try:
                        found = gocomics.exists(slug)
                    except SourceError:
                        found = True  # can't tell: let it in, its status will say
                    if not found:
                        flash(f"GoComics has no strip called “{slug}”.", "error")
                        return _back()
                    outcome = err
                except SourceError as err:
                    outcome = err
            record_fetch(store, source, outcome, today, started)
            note_pasted_result(store, gocomics)
            if isinstance(outcome, Fetched):
                label = outcome.label or label
            else:
                warning = str(outcome)
        new_row = {"enabled": True, "label": label, "slug": slug, "mode": "auto", "image_url": ""}

        def append(current: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
            if len(current) >= MAX_STRIPS or any(r.get("slug") == slug for r in current):
                return None
            return [*current, new_row]

        if not _patch_rows(data_dir, append):
            flash(f"{label} wasn't added: the list changed meanwhile. Try again.", "warn")
            return _back()
        log_entry(store, label, "added", f"added to the list as {slug}")
        if warning:
            flash(f"Added {label}, but it couldn't be fetched yet: {warning}", "warn")
        else:
            flash(f"Added {label}.", "ok")
        return _back(f"strip-{slug}")

    def _row_for(slug: str) -> tuple[Path, dict[str, Any]]:
        """The plugin's data_dir and the row for ``slug``; 404 when the
        list has no such strip."""
        data_dir = _plugin_data_dir()
        rows = _stored_rows(ensure_settings(data_dir) or {})
        row = next((r for r in rows if r.get("slug") == slug), None)
        if row is None:
            abort(404)
        return data_dir, row

    def _change_row(
        slug: str,
        op: Callable[[list[dict[str, Any]], int], tuple[str, str] | None],
    ) -> Response:
        """Apply ``op(rows, index)`` to the row for ``slug`` in one step
        under config.json's lock. ``op`` changes ``rows`` in place and
        returns the message to flash and the anchor to go back to, or None
        when there is nothing to change. A change that doesn't fit is
        refused with the check's message, and nothing is saved."""
        data_dir, _ = _row_for(slug)
        said: list[tuple[str, str]] = []
        gone: list[bool] = []

        def change(rows: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
            index = next((i for i, r in enumerate(rows) if r.get("slug") == slug), None)
            if index is None:
                gone.append(True)
                return None
            outcome = op(rows, index)
            if outcome is None:
                return None
            said.append(outcome)
            return rows

        try:
            _patch_rows(data_dir, change)
        except config_file.ConfigError as err:
            flash(f"Not saved: {err}", "error")
            return _back(f"strip-{slug}")
        if gone:
            flash("That strip isn't in the list any more.", "warn")
            return _back()
        if not said:
            return _back(f"strip-{slug}")
        message, anchor = said[0]
        flash(message, "ok")
        return _back(anchor)

    @bp.post("/strips/<slug>/edit")
    def edit_strip(slug: str) -> Response:
        form = request.form
        typed = form.get("slug", slug).strip()
        new_slug = parse_strip_input(typed) or typed.lower()
        fields = {
            "label": form.get("label", ""),
            "slug": new_slug,
            "mode": form.get("mode", "auto"),
            "image_url": form.get("image_url", ""),
        }

        def op(rows: list[dict[str, Any]], index: int) -> tuple[str, str]:
            rows[index].update(fields)
            return f"Saved {_row_label(rows[index])}.", f"strip-{new_slug}"

        return _change_row(slug, op)

    @bp.post("/strips/<slug>/move")
    def move_strip(slug: str) -> Response:
        step = -1 if request.form.get("direction") == "up" else 1

        def op(rows: list[dict[str, Any]], index: int) -> tuple[str, str] | None:
            other = index + step
            if not 0 <= other < len(rows):
                return None
            rows[index], rows[other] = rows[other], rows[index]
            where = "up" if step < 0 else "down"
            return f"Moved {_row_label(rows[other])} {where}.", f"strip-{slug}"

        return _change_row(slug, op)

    @bp.post("/strips/<slug>/switch")
    def switch_strip(slug: str) -> Response:
        enabled = request.form.get("enabled") == "on"

        def op(rows: list[dict[str, Any]], index: int) -> tuple[str, str]:
            rows[index]["enabled"] = enabled
            state = "on" if enabled else "off"
            return f"{_row_label(rows[index])} is switched {state}.", f"strip-{slug}"

        return _change_row(slug, op)

    @bp.post("/strips/<slug>/remove")
    def remove_strip(slug: str) -> Response:
        def op(rows: list[dict[str, Any]], index: int) -> tuple[str, str]:
            # Dilbert can't go: the save's check refuses it (switch it off).
            row = rows.pop(index)
            return f"Removed {_row_label(row)}.", "gocomics"

        return _change_row(slug, op)

    @bp.post("/strips/<slug>/upload")
    def upload_strip(slug: str) -> Response:
        # Refuse an oversized body before the form is parsed: parsing spools
        # every byte of it to disk first. The header decides when it is
        # sent; the parser's own limit catches a body without one.
        limit = images.MAX_UPLOAD_BYTES + UPLOAD_OVERHEAD_BYTES
        if request.content_length is not None and request.content_length > limit:
            flash(f"That picture is over {images.MAX_UPLOAD_BYTES // 1048576} MB.", "error")
            return _back(f"strip-{slug}")
        request.max_content_length = limit
        data_dir, row = _row_for(slug)
        try:
            upload = request.files.get("file")
        except RequestEntityTooLarge:
            flash(f"That picture is over {images.MAX_UPLOAD_BYTES // 1048576} MB.", "error")
            return _back(f"strip-{slug}")
        if upload is None or not upload.filename:
            flash("Choose a picture to upload first.", "error")
            return _back(f"strip-{slug}")
        try:
            raw = upload.stream.read(images.MAX_UPLOAD_BYTES + 1)
        finally:
            upload.close()  # a large upload is spooled to a temp file
        try:
            picture = images.validate_upload(raw)
        except ValueError as err:
            flash(str(err), "error")
            return _back(f"strip-{slug}")
        _keep_manual(data_dir, row, picture, "uploaded")
        flash(
            f"{_row_label(row)} shows the picture you uploaded until you switch it back to "
            "Automatic.",
            "ok",
        )
        return _back(f"strip-{slug}")

    @bp.post("/strips/<slug>/import")
    def import_strip(slug: str) -> Response:
        from app import net_guard

        data_dir, row = _row_for(slug)
        # The address as typed: the import form's own box, or the strip's
        # Image URL box when Import URL is pressed in its edit form.
        typed = request.form.get("url") or request.form.get("image_url")
        url = (typed or str(row.get("image_url") or "")).strip()
        if not url:
            flash("Put the picture's address in Image URL first.", "error")
            return _back(f"strip-{slug}")
        if not _URL_RE.match(url):
            flash("The Image URL has to start with https:// or http://.", "error")
            return _back(f"strip-{slug}")
        try:
            raw, _ = net_guard.fetch_bytes(
                url,
                headers={k: v for k, v in BROWSER_HEADERS.items() if k != "Accept"},
                timeout=IMAGE_TIMEOUT_S,
                max_bytes=images.MAX_UPLOAD_BYTES,
                allow_local=False,
            )
        except net_guard.BlockedURLError:
            flash("That address isn't on the public web, so it can't be imported.", "error")
            return _back(f"strip-{slug}")
        except ValueError:
            flash(f"That picture is over {images.MAX_UPLOAD_BYTES // 1048576} MB.", "error")
            return _back(f"strip-{slug}")
        except Exception as err:  # URLError, HTTPError, a timeout, a cut-off body
            reason = getattr(err, "reason", None) or getattr(err, "code", None) or err
            flash(f"Couldn't download that picture ({str(reason)[:120]}).", "error")
            return _back(f"strip-{slug}")
        try:
            picture = images.validate_upload(raw)
        except ValueError as err:
            flash(str(err), "error")
            return _back(f"strip-{slug}")
        host = _host(url) or "the web"
        _keep_manual(data_dir, row, picture, f"imported from {host}", url=url)
        flash(
            f"{_row_label(row)} shows the imported picture until you switch it back to Automatic.",
            "ok",
        )
        return _back(f"strip-{slug}")

    @bp.get("/img/<name>")
    def image(name: str) -> Response:
        path = _store().image_path(name)
        if path is None:
            abort(404)
        response = send_file(path, conditional=True, max_age=365 * 86400)
        # Names are content hashes: a new strip is a new address.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    return bp
