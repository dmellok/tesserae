"""calendar_core, iCal feed registry + admin page.

Plugins can't import each other's Python modules through normal
package paths, but they can reach into another plugin's server_module
via the live PluginRegistry (current_app.config["PLUGIN_REGISTRY"]).
The three calendar widgets do exactly that, they call
``load_events()`` here so feed-fetching, caching, and recurring-event
expansion live in one place.

Feeds are stored in ``feeds.json`` inside the plugin's data_dir:
  {"feeds": [{"id": "..", "name": "..", "url": "..", "colour": "#..", "enabled": true}]}
A feed can instead point at a Home Assistant calendar entity:
  {"id": "..", "name": "..", "source": "ha", "entity_id": "calendar...", ...}
The admin page (mounted at /plugins/calendar_core/) provides CRUD for
this list, same shape as the todo plugin.

Per-feed caching:
  feed_<id>.ics      raw .ics bytes, 15 minute TTL
The widgets keep their own narrower caches for the rendered event
slices they need.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import icalendar
import recurring_ical_events
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.wrappers import Response

from app.plugin_http import decode_content_encoding

_log = logging.getLogger(__name__)

CACHE_TTL_S = 15 * 60
HTTP_TIMEOUT_S = 15
USER_AGENT = "tesserae/0.1 (+calendar_core)"
DEFAULT_COLOUR = "#0d8c7e"

# Per-feed HTTP auth modes. "none" is the default (public Google / iCloud
# share URLs); "basic" and "digest" cover a self-hosted CalDAV server
# (Baikal / Radicale / Nextcloud) that gates its .ics export behind
# credentials. Both run server-side, so a LAN-only server the panel can't
# reach directly still works as long as Tesserae can reach it.
AUTH_MODES = ("none", "basic", "digest")


# ----- storage --------------------------------------------------------


def _data_dir() -> Path:
    registry = current_app.config["PLUGIN_REGISTRY"]
    plugin = registry.get("calendar_core")
    if plugin is None:
        raise RuntimeError("calendar_core plugin not registered")
    path: Path = plugin.data_dir
    return path


def _feeds_path(data_dir: Path | None = None) -> Path:
    return (data_dir or _data_dir()) / "feeds.json"


def _load_feeds(data_dir: Path | None = None) -> dict[str, Any]:
    path = _feeds_path(data_dir)
    if not path.exists():
        return {"feeds": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"feeds": []}
    if not isinstance(data, dict) or not isinstance(data.get("feeds"), list):
        return {"feeds": []}
    return data


def _save_feeds(data: dict[str, Any], data_dir: Path | None = None) -> None:
    path = _feeds_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _slugify(s: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return base or "feed"


def _unique_id(data: dict[str, Any], base: str) -> str:
    taken = {f.get("id") for f in data.get("feeds", [])}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


# ----- fetch + cache --------------------------------------------------


def _ics_cache_path(data_dir: Path, feed_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", feed_id)[:40]
    return data_dir / f"feed_{safe}.ics"


def _feed_auth(feed: dict[str, Any]) -> dict[str, str]:
    """The auth block for a feed: ``{mode, username, password}``. Absent /
    malformed values collapse to no auth."""
    mode = str(feed.get("auth_mode") or "none").strip().lower()
    if mode not in AUTH_MODES:
        mode = "none"
    return {
        "mode": mode,
        "username": str(feed.get("username") or ""),
        "password": str(feed.get("password") or ""),
    }


def _build_opener(url: str, auth: dict[str, str] | None) -> urllib.request.OpenerDirector:
    """An opener that answers a basic/digest challenge for ``url`` with the
    feed's credentials. urllib's auth handlers are reactive (they respond
    to the server's 401 ``WWW-Authenticate``), which is exactly right for a
    CalDAV export URL behind Baikal/Radicale digest auth. ``none`` (or no
    creds) returns a plain opener."""
    if not auth or auth.get("mode") == "none" or not auth.get("username"):
        return urllib.request.build_opener()
    pwd_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    pwd_mgr.add_password(None, url, auth["username"], auth.get("password", ""))  # type: ignore[arg-type]
    if auth["mode"] == "digest":
        handler: urllib.request.BaseHandler = urllib.request.HTTPDigestAuthHandler(pwd_mgr)
    else:
        handler = urllib.request.HTTPBasicAuthHandler(pwd_mgr)
    return urllib.request.build_opener(handler)


# ----- feed health ----------------------------------------------------
#
# Whether a feed is actually working is something only the fetch path
# knows, and it used to throw that away: a 401 looked exactly like a
# calendar with no events. Recorded here so surfaces that list feeds
# (Settings -> Rooms) can say "401 since yesterday" instead of showing a
# room as free because nothing could be read.
#
# Written only when a fetch is genuinely attempted, which the cache TTL
# bounds to once per feed per CACHE_TTL_S, so this is not hot-path I/O.


def _health_path(data_dir: Path) -> Path:
    return data_dir / "feed_health.json"


def load_health(data_dir: Path | None = None) -> dict[str, Any]:
    """Per-feed fetch health, keyed by feed id."""
    path = _health_path(data_dir or _data_dir())
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def record_fetch(data_dir: Path, feed_id: str, *, ok: bool, error: str | None = None) -> None:
    """Note the outcome of one fetch.

    ``failing_since`` is preserved across consecutive failures so a
    surface can say how long a feed has been broken, and cleared the
    moment one succeeds.
    """
    try:
        health = load_health(data_dir)
        prior = health.get(feed_id) or {}
        now = time.time()
        entry: dict[str, Any] = {"checked_at": now}
        if ok:
            entry["last_ok"] = now
            entry["error"] = None
            entry["failing_since"] = None
        else:
            entry["last_ok"] = prior.get("last_ok")
            entry["error"] = error or "unknown"
            entry["failing_since"] = prior.get("failing_since") or now
        health[feed_id] = entry
        path = _health_path(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(health, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        # Health is diagnostic. Failing to record it must never break the
        # fetch that was actually asked for.
        _log.debug("calendar_core: could not record feed health", exc_info=True)


def _describe_fetch_error(err: Exception) -> str:
    """A short reason fit to show an operator.

    "401" tells someone their credentials are wrong; "HTTPError" tells
    them nothing, and a traceback tells them less.
    """
    if isinstance(err, urllib.error.HTTPError):
        return f"HTTP {err.code}"
    if isinstance(err, urllib.error.URLError):
        return f"unreachable ({err.reason})"
    return type(err).__name__


def _http_get(
    url: str, auth: dict[str, str] | None, *, error_out: list[str] | None = None
) -> bytes | None:
    """GET ``url`` (server-side), honouring the feed's auth. Returns the
    body bytes, or None on any failure (unreachable, 401, timeout).

    ``error_out`` collects the reason when there is one, so callers that
    care (feed health) can report it without this having to raise and
    every existing caller having to catch."""
    fixed = url.replace("webcal://", "https://", 1) if url.startswith("webcal://") else url
    try:
        opener = _build_opener(fixed, auth)
        # Ask for an uncompressed body (urllib doesn't decompress); decode
        # defensively if a proxy compresses the feed anyway (#168).
        req = urllib.request.Request(
            fixed, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        )
        with opener.open(req, timeout=HTTP_TIMEOUT_S) as resp:
            blob: bytes = resp.read()
            blob = decode_content_encoding(blob, str(resp.headers.get("Content-Encoding", "")))
        return blob
    except Exception as err:
        if error_out is not None:
            error_out.append(_describe_fetch_error(err))
        return None


def _fetch_ics(
    url: str,
    cache_path: Path,
    auth: dict[str, str] | None = None,
    *,
    feed_id: str = "",
    data_dir: Path | None = None,
) -> bytes | None:
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < CACHE_TTL_S:
        try:
            return cache_path.read_bytes()
        except OSError:
            pass
    # Some providers (notably Google) require https; webcal:// → https://.
    errors: list[str] = []
    blob = _http_get(url, auth, error_out=errors)
    # Only record when a fetch was actually attempted: a served-from-cache
    # read above is not evidence the feed is reachable.
    if feed_id and data_dir is not None:
        record_fetch(data_dir, feed_id, ok=blob is not None, error=errors[0] if errors else None)
    if blob is None:
        return None
    with contextlib.suppress(OSError):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(blob)
    return blob


# v0.71.x (r/eink launch feedback): expanded-events cache. On a busy
# calendar with lots of recurring rules (daily standup, weekly 1:1)
# ``recurring_ical_events.of(cal).between(start, end)`` on a 90+ day
# window is measured in seconds, and every schedule/day/week/month
# widget on the dashboard was paying that cost on every render. We
# now expand once into a wide "warm" window per (feed_id, ics_mtime)
# and let per-widget calls slice from the cached list. Cache lives on
# the module for the process lifetime; ``ics_mtime`` changing (fresh
# feed pull) invalidates the entry automatically. Threadsafe by
# accident: dict get/set are atomic in CPython, and the worst-case
# race is two threads expanding the same feed in parallel.
_EXPANSION_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
# Warm window: 30 days back (all-day multi-day events still visible)
# to 180 days forward (covers 3 months + a buffer). ``fill`` mode
# ceils at 90 days so this is comfortably wider than any widget will
# ask for; requests outside the warm window fall back to a per-call
# expansion.
_WARM_WINDOW_BACK_DAYS = 30
_WARM_WINDOW_FORWARD_DAYS = 180


def _disambiguate_duplicate_uids(cal: Any) -> None:
    """Give plain VEVENTs that share a UID their own one.

    ``recurring_ical_events`` folds every component carrying the same
    UID into a single event (per spec a UID names exactly one event),
    so a feed that reuses a UID across separate VEVENTs — Outlook
    keeps the UID when an appointment is copied to another day —
    silently drops all but one of them. Suffix such UIDs with the
    component's DTSTART so each VEVENT expands on its own. Components
    with an RRULE or a RECURRENCE-ID keep the shared UID untouched:
    that is a genuine series plus its overrides, and splitting them
    would detach the overrides from the rule. Two components with the
    same UID *and* the same DTSTART still fold into one, which is the
    right call for a feed that repeats an identical VEVENT verbatim.
    """
    plain_uid_counts: dict[str, int] = {}
    for comp in cal.walk("VEVENT"):
        if comp.get("RRULE") is None and comp.get("RECURRENCE-ID") is None:
            uid = str(comp.get("UID") or "")
            plain_uid_counts[uid] = plain_uid_counts.get(uid, 0) + 1
    for comp in cal.walk("VEVENT"):
        if comp.get("RRULE") is not None or comp.get("RECURRENCE-ID") is not None:
            continue
        uid = str(comp.get("UID") or "")
        if plain_uid_counts.get(uid, 0) < 2:
            continue
        dtstart = comp.get("DTSTART")
        suffix = dtstart.dt.isoformat() if dtstart is not None else "no-dtstart"
        comp["UID"] = f"{uid}#tesserae-dup-{suffix}"


def _expand_events_full(blob: bytes, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Uncached expansion (the previous ``_expand_events`` body). Used
    for the initial warm-cache fill and for windows outside it."""
    try:
        cal = icalendar.Calendar.from_ical(blob)
    except Exception:
        return []
    with contextlib.suppress(Exception):
        _disambiguate_duplicate_uids(cal)
    out: list[dict[str, Any]] = []
    try:
        events = recurring_ical_events.of(cal).between(start, end)
    except Exception:
        events = []
    for ev in events:
        try:
            summary = str(ev.get("SUMMARY") or "").strip()
            location = str(ev.get("LOCATION") or "").strip()
            dtstart = ev.get("DTSTART")
            dtend = ev.get("DTEND") or ev.get("DTSTART")
            sdt = dtstart.dt if dtstart else None
            edt = dtend.dt if dtend else sdt
            if sdt is None:
                continue
            # Date-only (all-day) vs datetime
            all_day = not hasattr(sdt, "hour")
            if all_day:
                s_iso = sdt.isoformat()
                e_iso = edt.isoformat() if edt else s_iso
            else:
                # Normalise to UTC ISO so the client can localize.
                if sdt.tzinfo is None:
                    sdt = sdt.replace(tzinfo=UTC)
                if edt and getattr(edt, "tzinfo", None) is None:
                    edt = edt.replace(tzinfo=UTC)
                s_iso = sdt.astimezone(UTC).isoformat()
                e_iso = edt.astimezone(UTC).isoformat() if edt else s_iso
            out.append(
                {
                    "summary": summary or "(untitled)",
                    "location": location,
                    "start": s_iso,
                    "end": e_iso,
                    "all_day": all_day,
                }
            )
        except Exception:
            continue
    out.sort(key=lambda e: (not e["all_day"], e["start"]))
    return out


def _expand_events_cached(
    feed_id: str,
    ics_mtime: float,
    blob: bytes,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Expand a feed's events through a warm cache when the requested
    window fits inside it, otherwise fall back to a fresh expansion
    of just the requested window."""
    now = datetime.now(UTC)
    warm_start = now - timedelta(days=_WARM_WINDOW_BACK_DAYS)
    warm_end = now + timedelta(days=_WARM_WINDOW_FORWARD_DAYS)
    within_warm = start >= warm_start and end <= warm_end
    if not within_warm:
        return _expand_events_full(blob, start, end)
    cached = _EXPANSION_CACHE.get(feed_id)
    if cached is not None and cached[0] == ics_mtime:
        events = cached[1]
    else:
        events = _expand_events_full(blob, warm_start, warm_end)
        _EXPANSION_CACHE[feed_id] = (ics_mtime, events)
    # Slice the warm-cached list to the widget's specific window.
    start_iso = start.astimezone(UTC).isoformat()
    end_iso = end.astimezone(UTC).isoformat()
    # All-day events store bare "YYYY-MM-DD" dates (no timezone), which
    # can't compare correctly against the full UTC datetime bounds above:
    # a bare date string sorts as "less than" any same-day timestamp
    # string, so once the UTC calendar day rolls past local midnight
    # (e.g. any evening in a negative-UTC-offset timezone), today's
    # all-day event silently drops out. Widen by a day on each side for
    # all-day events instead — every widget already re-filters all-day
    # events against the real local date downstream, so over-inclusion
    # here is harmless.
    all_day_start = (start - timedelta(days=1)).date().isoformat()
    all_day_end = (end + timedelta(days=1)).date().isoformat()

    def _in_window(e: dict[str, Any]) -> bool:
        if e.get("all_day"):
            return (e.get("end") or e.get("start", "")) >= all_day_start and (
                e.get("start") or ""
            ) < all_day_end
        return (e.get("end") or e.get("start", "")) >= start_iso and (
            e.get("start") or ""
        ) < end_iso

    return [e for e in events if _in_window(e)]


# Public API for the calendar_* widgets uses the cached path. Direct
# callers (tests / one-off diagnostics) can still hit ``_expand_events_full``.
_expand_events = _expand_events_full


# ----- Home Assistant calendars ---------------------------------------
#
# A feed row can point at a Home Assistant calendar entity instead of an
# ICS URL: ``{"source": "ha", "entity_id": "calendar.family", ...}``
# (rows without a ``source`` are the original ICS/CalDAV kind). Events
# come from HA's REST API through the ha_core plugin, which owns the
# base URL + token, via the same registry reach-in the calendar widgets
# use to call into this module. HA expands recurrences server-side, so
# these feeds skip the ICS expansion cache entirely; each requested
# window is cached whole for the same TTL as an ICS fetch. Widget
# windows are local-midnight aligned, so the cache keys stay stable
# between renders.

_HA_CACHE_MAX = 64
_ha_events_cache: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]] = {}


def feed_source(feed: dict[str, Any]) -> str:
    """``"ha"`` for a Home Assistant calendar feed, else ``"ics"``."""
    return "ha" if str(feed.get("source") or "").strip().lower() == "ha" else "ics"


def _ha_core() -> Any:
    """ha_core's server module via the live registry, or None when the
    plugin isn't installed (or there is no app context, e.g. standalone
    module use in tests)."""
    try:
        plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    except Exception:
        return None
    return plugin.server_module if plugin is not None else None


def _parse_ha_when(value: Any) -> tuple[str, bool] | None:
    """One HA event boundary (``{"dateTime": ...}`` / ``{"date": ...}``,
    or a bare ISO string) → ``(iso, all_day)``, or None when unusable.
    Timed values are normalised to UTC ISO like the ICS path, so the
    widgets' local-day bucketing sees a single shape."""
    raw: Any = value
    if isinstance(value, dict):
        if value.get("date"):
            return str(value["date"]), True
        raw = value.get("dateTime")
    if not raw or not isinstance(raw, str):
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):  # bare date, all-day
        return raw, True
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(), False


def _normalise_ha_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One HA API event → the event dict shape ``load_events`` emits, or
    None when it has no usable start."""
    start = _parse_ha_when(raw.get("start"))
    if start is None:
        return None
    s_iso, all_day = start
    end = _parse_ha_when(raw.get("end"))
    e_iso = end[0] if end is not None else s_iso
    return {
        "summary": str(raw.get("summary") or "").strip() or "(untitled)",
        "location": str(raw.get("location") or "").strip(),
        "start": s_iso,
        "end": e_iso,
        "all_day": all_day,
    }


def _fetch_ha_events(
    feed: dict[str, Any],
    start: datetime,
    end: datetime,
    data_dir: Path,
) -> list[dict[str, Any]]:
    """Events for one HA-source feed inside [start, end). Failures record
    feed health (the same surface the ICS path writes) and return []."""
    fid = str(feed.get("id") or "")
    entity_id = str(feed.get("entity_id") or "").strip()
    if not entity_id:
        return []
    start_iso = start.astimezone(UTC).isoformat()
    end_iso = end.astimezone(UTC).isoformat()
    key = (fid, start_iso, end_iso)
    hit = _ha_events_cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    core = _ha_core()
    if core is None:
        record_fetch(data_dir, fid, ok=False, error="ha_core plugin not installed")
        return []
    path = (
        f"/api/calendars/{urllib.parse.quote(entity_id)}"
        f"?start={urllib.parse.quote(start_iso)}&end={urllib.parse.quote(end_iso)}"
    )
    try:
        data = core.request_json(path, timeout=HTTP_TIMEOUT_S)
    except Exception as err:
        # A RuntimeError is ha_core's own "not configured / token
        # unreadable" guidance; keep its wording, it says what to fix.
        msg = str(err) if isinstance(err, RuntimeError) else _describe_fetch_error(err)
        record_fetch(data_dir, fid, ok=False, error=msg)
        return []
    events: list[dict[str, Any]] = []
    for raw in data if isinstance(data, list) else []:
        if isinstance(raw, dict):
            ev = _normalise_ha_event(raw)
            if ev is not None:
                events.append(ev)
    events.sort(key=lambda e: (not e["all_day"], e["start"]))
    record_fetch(data_dir, fid, ok=True)
    if len(_ha_events_cache) >= _HA_CACHE_MAX:
        _ha_events_cache.clear()
    _ha_events_cache[key] = (time.monotonic(), events)
    return events


def _drop_ha_cache(feed_id: str) -> None:
    """Forget every cached window for one HA feed (Refresh button)."""
    for key in [k for k in _ha_events_cache if k[0] == feed_id]:
        _ha_events_cache.pop(key, None)


def load_events(
    feed_ids: list[str] | None,
    start: datetime,
    end: datetime,
    *,
    data_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Public API for the calendar_* widgets, returns events from the
    requested feeds inside [start, end), tagged with the feed's
    name + colour. Pass ``None`` for ``feed_ids`` to include every
    enabled feed."""
    dd = data_dir if data_dir is not None else _data_dir()
    feeds = _load_feeds(dd).get("feeds") or []
    wanted = set(feed_ids) if feed_ids else None
    out: list[dict[str, Any]] = []
    for feed in feeds:
        if not feed.get("enabled", True):
            continue
        fid = feed.get("id")
        if wanted is not None and fid not in wanted:
            continue
        if feed_source(feed) == "ha":
            for ev in _fetch_ha_events(feed, start, end, dd):
                ev["feed_id"] = fid
                ev["feed_name"] = feed.get("name") or fid
                ev["feed_colour"] = feed.get("colour") or DEFAULT_COLOUR
                out.append(ev)
            continue
        url = feed.get("url")
        if not url:
            continue
        cache_path = _ics_cache_path(dd, fid)
        blob = _fetch_ics(url, cache_path, _feed_auth(feed))
        if blob is None:
            continue
        try:
            ics_mtime = cache_path.stat().st_mtime
        except OSError:
            ics_mtime = 0.0
        for ev in _expand_events_cached(fid, ics_mtime, blob, start, end):
            ev["feed_id"] = fid
            ev["feed_name"] = feed.get("name") or fid
            ev["feed_colour"] = feed.get("colour") or DEFAULT_COLOUR
            out.append(ev)
    out.sort(key=lambda e: (not e["all_day"], e["start"]))
    return out


# ----- todos (VTODO) --------------------------------------------------

# iCal VTODO STATUS values → the shape the todo widget (and ha_todo)
# expect, so a caldav todo list and an HA todo list render through the
# same client.
_VTODO_STATUS = {
    "NEEDS-ACTION": "needs_action",
    "COMPLETED": "completed",
    "IN-PROCESS": "in_process",
    "CANCELLED": "cancelled",
}


def _normalise_vtodo(comp: Any) -> dict[str, Any] | None:
    """One icalendar VTODO → a normalised todo dict, or None when it has
    no summary (a placeholder the widget shouldn't show)."""
    summary = str(comp.get("SUMMARY") or "").strip()
    if not summary:
        return None
    raw_status = str(comp.get("STATUS") or "NEEDS-ACTION").strip().upper()
    status = _VTODO_STATUS.get(raw_status, "needs_action")

    due_iso: str | None = None
    due = comp.get("DUE")
    if due is not None:
        ddt = getattr(due, "dt", None)
        if ddt is not None:
            if hasattr(ddt, "hour"):
                if ddt.tzinfo is None:
                    ddt = ddt.replace(tzinfo=UTC)
                due_iso = ddt.astimezone(UTC).isoformat()
            else:
                due_iso = ddt.isoformat()

    priority = comp.get("PRIORITY")
    try:
        priority_n: int | None = int(priority) if priority not in (None, "") else None
    except (TypeError, ValueError):
        priority_n = None

    percent = comp.get("PERCENT-COMPLETE")
    try:
        percent_n: int | None = int(percent) if percent not in (None, "") else None
    except (TypeError, ValueError):
        percent_n = None

    return {
        "uid": str(comp.get("UID") or ""),
        "summary": summary,
        "status": status,
        "due": due_iso,
        "description": str(comp.get("DESCRIPTION") or "").strip(),
        "priority": priority_n,
        "percent_complete": percent_n,
    }


def _parse_todos(blob: bytes) -> list[dict[str, Any]]:
    try:
        cal = icalendar.Calendar.from_ical(blob)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for comp in cal.walk("VTODO"):
        try:
            item = _normalise_vtodo(comp)
        except Exception:
            item = None
        if item is not None:
            out.append(item)
    return out


def load_todos(
    feed_ids: list[str] | None,
    *,
    data_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Public API for todo widgets: VTODO items from the requested feeds,
    tagged with the feed's name + colour. Pass ``None`` for ``feed_ids``
    to include every enabled feed. VTODOs aren't recurrence-expanded (a
    recurring todo is rare and the semantics are murky); each VTODO
    component maps to one item."""
    dd = data_dir if data_dir is not None else _data_dir()
    feeds = _load_feeds(dd).get("feeds") or []
    wanted = set(feed_ids) if feed_ids else None
    out: list[dict[str, Any]] = []
    for feed in feeds:
        if not feed.get("enabled", True):
            continue
        fid = feed.get("id")
        if wanted is not None and fid not in wanted:
            continue
        url = feed.get("url")
        if not url:
            continue
        blob = _fetch_ics(url, _ics_cache_path(dd, fid), _feed_auth(feed), feed_id=fid, data_dir=dd)
        if blob is None:
            continue
        for item in _parse_todos(blob):
            item["feed_id"] = fid
            item["feed_name"] = feed.get("name") or fid
            item["feed_colour"] = feed.get("colour") or DEFAULT_COLOUR
            out.append(item)
    return out


# ----- CalDAV discovery -----------------------------------------------

# One PROPFIND over the calendar-home collection enumerates its child
# calendar / todo collections in a single round-trip. Depth: 1 lists the
# immediate children (the collections), which is what a self-hosted
# server's "calendar home" (``.../calendars/<user>/``) holds.
_NS = {
    "d": "DAV:",
    "c": "urn:ietf:params:xml:ns:caldav",
    "cs": "http://apple.com/ns/ical/",
}
_PROPFIND_BODY = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
    b'xmlns:cs="http://apple.com/ns/ical/"><d:prop>'
    b"<d:resourcetype/><d:displayname/><cs:calendar-color/>"
    b"<c:supported-calendar-component-set/>"
    # current-user-principal + calendar-home-set let discovery resolve a
    # principal or service-root URL to the collection that holds the
    # calendars, instead of requiring the exact calendar-home URL.
    b"<d:current-user-principal/><c:calendar-home-set/>"
    b"</d:prop></d:propfind>"
)


def _export_url(collection_url: str) -> str:
    """The GET-able .ics export URL for a collection. sabre/dav (Baikal,
    Nextcloud) needs ``?export``; Radicale serves the collection as ICS on
    a plain GET and ignores the extra query, so appending it is safe
    either way."""
    sep = "&" if "?" in collection_url else "?"
    return f"{collection_url}{sep}export"


def _normalise_colour(raw: str) -> str:
    """A CalDAV ``calendar-color`` (often ``#RRGGBBAA``) → the ``#RRGGBB``
    the feed store keeps. Falls back to the default when unparseable."""
    val = (raw or "").strip()
    if re.match(r"^#[0-9a-fA-F]{6}", val):
        return val[:7]
    return DEFAULT_COLOUR


def _prop_find(props: list[Any], tag: str) -> Any:
    """First matching child ``tag`` across a response's ``propstat/prop``
    blocks. A ``<response>`` can carry several ``<propstat>`` elements split
    by status: sabre/dav (Baikal, Nextcloud) returns the props it has in a
    200 block and the ones it lacks in a 404 block, in no guaranteed order.
    Looking only at the first block drops a calendar whose ``resourcetype``
    happens to sit after a 404 block (e.g. a calendar with no colour set)."""
    for p in props:
        el = p.find(tag, _NS)
        if el is not None:
            return el
    return None


def _propfind(
    url: str, auth: dict[str, str] | None, depth: str
) -> tuple[Any, dict[str, Any] | None]:
    """One PROPFIND round-trip. Returns ``(root_element, None)`` on success,
    or ``(None, error_dict)`` where ``error_dict`` is a discovery result
    shape (``{"collections": [], "error": <msg>}``). Never raises."""
    import defusedxml.ElementTree as DET

    content_encoding = ""
    content_type = ""
    try:
        opener = _build_opener(url, auth)
        req = urllib.request.Request(
            url,
            data=_PROPFIND_BODY,
            method="PROPFIND",
            headers={
                "User-Agent": USER_AGENT,
                "Depth": depth,
                "Content-Type": 'application/xml; charset="utf-8"',
                # Ask for an uncompressed body: urllib won't decompress a
                # gzip/brotli response, and an absent Accept-Encoding lets the
                # server compress at will (#168).
                "Accept-Encoding": "identity",
            },
        )
        with opener.open(req, timeout=HTTP_TIMEOUT_S) as resp:
            body = resp.read()
            content_encoding = str(resp.headers.get("Content-Encoding", ""))
            content_type = str(resp.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as err:
        code = err.code
        err.close()  # HTTPError is a response object; don't leak the handle.
        if code in (401, 403):
            return None, {
                "collections": [],
                "error": "Authentication failed (check username / password).",
            }
        return None, {"collections": [], "error": f"Server returned HTTP {code}."}
    except Exception as err:
        return None, {
            "collections": [],
            "error": f"Couldn't reach the server: {type(err).__name__}.",
        }
    # Decode a compressed body the server sent despite our identity request
    # (a proxy / CDN in front of Nextcloud is the usual cause), otherwise the
    # parser just sees a binary blob (#168).
    body = decode_content_encoding(body, content_encoding)
    # Some servers (misconfigured PHP / Nextcloud output buffering, an app
    # that emits a stray newline before the response) prepend a BOM or
    # whitespace ahead of the ``<?xml`` declaration. A browser or curl
    # tolerates it, but a strict XML parser rejects "text before the
    # declaration" and the whole discovery fails on an otherwise-valid 207.
    # Trim any junk ahead of the first ``<`` so a real multistatus still
    # parses; a body with no ``<`` at all is left to fail below.
    lt = body.find(b"<")
    if lt > 0:
        body = body[lt:]
    try:
        return DET.fromstring(body), None
    except Exception:
        _log.warning(
            "CalDAV PROPFIND response not parseable as XML "
            "(content-type=%r content-encoding=%r); first bytes: %r",
            content_type,
            content_encoding,
            body[:160],
        )
        return None, {"collections": [], "error": "The server's response wasn't valid CalDAV XML."}


def _calendars_from_multistatus(root: Any, base_url: str) -> list[dict[str, Any]]:
    """Every calendar collection in a PROPFIND multistatus, each tagged with
    name / colour / components and a GET-able ``?export`` URL. ``base_url``
    resolves relative hrefs."""
    collections: list[dict[str, Any]] = []
    for resp_el in root.findall("d:response", _NS):
        href_el = resp_el.find("d:href", _NS)
        if href_el is None or not (href_el.text or "").strip():
            continue
        props = resp_el.findall("d:propstat/d:prop", _NS)
        if not props:
            continue
        rtype = _prop_find(props, "d:resourcetype")
        if rtype is None or rtype.find("c:calendar", _NS) is None:
            continue  # not a calendar collection (principal, addressbook, …)
        comps = [
            c.get("name", "").upper()
            for prop in props
            for c in prop.findall("c:supported-calendar-component-set/c:comp", _NS)
            if c.get("name")
        ]
        # No declared component set means "everything" per RFC 4791; treat
        # it as both so the collection still shows up under either widget.
        wanted = [x for x in ("VEVENT", "VTODO") if x in comps] or ["VEVENT", "VTODO"]
        name_el = _prop_find(props, "d:displayname")
        colour_el = _prop_find(props, "cs:calendar-color")
        href = (href_el.text or "").strip()
        collection_url = urllib.parse.urljoin(base_url, href)
        collections.append(
            {
                "name": (name_el.text or "").strip() if name_el is not None else href,
                "url": collection_url,
                "export_url": _export_url(collection_url),
                "colour": _normalise_colour(colour_el.text or "")
                if colour_el is not None
                else DEFAULT_COLOUR,
                "components": wanted,
            }
        )
    return collections


def _href_in_prop(root: Any, base_url: str, prop_tag: str) -> str | None:
    """Absolute ``<d:href>`` inside the first ``prop_tag`` property found in a
    multistatus (e.g. ``c:calendar-home-set`` or ``d:current-user-principal``),
    resolved against ``base_url``."""
    for resp_el in root.findall("d:response", _NS):
        props = resp_el.findall("d:propstat/d:prop", _NS)
        el = _prop_find(props, prop_tag)
        if el is None:
            continue
        href_el = el.find("d:href", _NS)
        if href_el is not None and (href_el.text or "").strip():
            resolved: str = urllib.parse.urljoin(base_url, (href_el.text or "").strip())
            return resolved
    return None


def discover_collections(base_url: str, auth: dict[str, str] | None) -> dict[str, Any]:
    """Discover the calendar / todo collections reachable from ``base_url``.

    Returns ``{"collections": [{name, url, export_url, colour,
    components[]}], "error": <msg|None>}``. ``components`` is the subset of
    ``VEVENT`` / ``VTODO`` the collection holds, so the UI can label a todo
    list vs a calendar.

    ``base_url`` can be the calendar home (``.../calendars/<user>/``,
    calendars come back on the first PROPFIND), a principal
    (``.../principals/<user>/``), or the CalDAV service root: when the first
    PROPFIND finds no calendars, follow ``calendar-home-set`` (directly, or
    via ``current-user-principal``) and enumerate there. Best-effort: a bad
    URL / auth / non-CalDAV response comes back as an ``error`` string,
    never an exception."""
    fixed = (
        base_url.replace("webcal://", "https://", 1)
        if base_url.startswith("webcal://")
        else base_url
    )
    root, err = _propfind(fixed, auth, "1")
    if err is not None:
        return err
    collections = _calendars_from_multistatus(root, fixed)
    if collections:
        return {"collections": collections, "error": None}

    # No calendars at the given URL. It may be a principal or the service
    # root, so follow calendar-home-set to the collection that holds them,
    # resolving current-user-principal first when the home isn't advertised
    # on this resource directly.
    home = _href_in_prop(root, fixed, "c:calendar-home-set")
    if home is None:
        principal = _href_in_prop(root, fixed, "d:current-user-principal")
        if principal and principal != fixed:
            proot, _perr = _propfind(principal, auth, "0")
            if proot is not None:
                home = _href_in_prop(proot, principal, "c:calendar-home-set")
    if home and home != fixed:
        hroot, herr = _propfind(home, auth, "1")
        if herr is not None:
            return herr
        collections = _calendars_from_multistatus(hroot, home)
        if collections:
            return {"collections": collections, "error": None}

    return {
        "collections": [],
        "error": "No calendars found at that URL. Point it at your calendar home "
        "(the folder that holds your calendars, e.g. .../calendars/<user>/), "
        "your principal URL, or the CalDAV root.",
    }


# ----- cell-option choices --------------------------------------------


def choices(name: str) -> list[dict[str, str]]:
    """Powers the multi-select feed picker on each calendar widget."""
    if name != "feeds":
        return []
    feeds = _load_feeds().get("feeds") or []
    return [
        {"value": f["id"], "label": f.get("name") or f["id"]}
        for f in feeds
        if f.get("enabled", True)
    ]


# ----- admin blueprint ------------------------------------------------


def blueprint() -> Blueprint:
    bp = Blueprint("calendar_core_admin", __name__, template_folder="templates")

    def _ha_calendar_list() -> tuple[list[dict[str, Any]], str | None]:
        """Calendar entities from Home Assistant for the add-from-HA
        section: ``([{entity_id, name}], error)``. The error string is
        guidance fit to flash (plugin missing / not configured /
        unreachable)."""
        core = _ha_core()
        if core is None:
            return [], "Install the Home Assistant Core plugin to list HA calendars."
        if not core.is_configured():
            return [], (
                "Home Assistant is not configured; set URL + token in "
                "Settings → Widgets → Home Assistant Core."
            )
        try:
            states = core.get_states()
        except Exception as err:
            return [], str(core.coerce_error(err))
        cals: list[dict[str, Any]] = []
        for st in states:
            eid = str(st.get("entity_id") or "")
            if not eid.startswith("calendar."):
                continue
            cals.append({"entity_id": eid, "name": str(core.friendly_name(st)) or eid})
        cals.sort(key=lambda c: c["name"].lower())
        return cals, None

    def _render_index(
        discovered: list[dict[str, Any]] | None = None,
        discover_auth: dict[str, str] | None = None,
        ha_calendars: list[dict[str, Any]] | None = None,
    ) -> str:
        feeds = _load_feeds().get("feeds") or []
        # Cache-status per feed so the admin page can chip "fresh"
        # vs "stale (N min old)" next to each row. Users on HA / Docker
        # can't SSH to check the cache dir, so surfacing this saves the
        # "is my feed working" back-and-forth.
        dd = _data_dir()
        now_ts = time.time()
        cache_info: dict[str, dict[str, Any]] = {}
        for f in feeds:
            fid = f.get("id")
            if not isinstance(fid, str):
                continue
            path = _ics_cache_path(dd, fid)
            if not path.exists():
                cache_info[fid] = {"has_cache": False}
                continue
            try:
                mtime = path.stat().st_mtime
                size = path.stat().st_size
            except OSError:
                cache_info[fid] = {"has_cache": False}
                continue
            age_s = max(0, int(now_ts - mtime))
            cache_info[fid] = {
                "has_cache": True,
                "age_s": age_s,
                "size": size,
                "is_stale": age_s >= CACHE_TTL_S,
                "ttl_s": CACHE_TTL_S,
            }
        # Mark which feeds a discovered collection would duplicate (by
        # export URL / entity id) so the UI can show "already added".
        existing_urls = {f.get("url") for f in feeds}
        for col in discovered or []:
            col["already_added"] = col.get("export_url") in existing_urls
        existing_entities = {f.get("entity_id") for f in feeds if feed_source(f) == "ha"}
        for cal in ha_calendars or []:
            cal["already_added"] = cal.get("entity_id") in existing_entities
        return render_template(
            "calendar_core/index.html",
            feeds=feeds,
            cache_info=cache_info,
            auth_modes=AUTH_MODES,
            discovered=discovered or [],
            discover_auth=discover_auth or {},
            ha_calendars=ha_calendars or [],
        )

    @bp.get("/")
    def index() -> str:
        return _render_index()

    def _read_auth_form() -> dict[str, str]:
        mode = (request.form.get("auth_mode") or "none").strip().lower()
        if mode not in AUTH_MODES:
            mode = "none"
        return {
            "auth_mode": mode,
            "username": (request.form.get("username") or "").strip(),
            "password": request.form.get("password") or "",
        }

    @bp.post("/feeds")
    def create_feed() -> Response | str:
        name = (request.form.get("name") or "").strip()
        url = (request.form.get("url") or "").strip()
        colour = (request.form.get("colour") or DEFAULT_COLOUR).strip()
        # Set when the add came from the discovery list (the per-row Add form
        # echoes the discovery URL). We re-render the discovered list afterwards
        # so adding one calendar doesn't wipe the others off the page.
        discover_base_url = (request.form.get("discover_base_url") or "").strip()
        if not re.match(r"^#[0-9a-fA-F]{6}$", colour):
            colour = DEFAULT_COLOUR
        if (request.form.get("source") or "").strip().lower() == "ha":
            entity_id = (request.form.get("entity_id") or "").strip()
            if not name or not entity_id:
                flash("Name and calendar entity are required.", "warn")
                return redirect(url_for("calendar_core_admin.index"))
            if not entity_id.startswith("calendar."):
                flash(f"'{entity_id}' isn't a calendar entity.", "warn")
                return redirect(url_for("calendar_core_admin.index"))
            data = _load_feeds()
            fid = _unique_id(data, _slugify(name))
            data["feeds"].append(
                {
                    "id": fid,
                    "name": name,
                    "source": "ha",
                    "entity_id": entity_id,
                    "colour": colour,
                    "enabled": True,
                    "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
                }
            )
            _save_feeds(data)
            flash(f"Added Home Assistant calendar '{name}'.", "ok")
            # Keep the HA list on screen (same as the CalDAV discovery flow)
            # so adding one calendar doesn't wipe the rest off the page.
            calendars, _err = _ha_calendar_list()
            return _render_index(ha_calendars=calendars)
        if not name or not url:
            flash("Name and URL are required.", "warn")
            return redirect(url_for("calendar_core_admin.index"))
        data = _load_feeds()
        fid = _unique_id(data, _slugify(name))
        auth = _read_auth_form()
        entry: dict[str, Any] = {
            "id": fid,
            "name": name,
            "url": url,
            "colour": colour,
            "enabled": True,
            "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        }
        if auth["auth_mode"] != "none":
            entry.update(auth)
        data["feeds"].append(entry)
        _save_feeds(data)
        flash(f"Added feed '{name}'.", "ok")
        # Came from the discovery list: keep it on screen (re-run discovery so
        # the just-added collection now shows "already added" and the rest stay
        # addable) instead of redirecting to a bare index that drops the list.
        if discover_base_url:
            result = discover_collections(discover_base_url, _feed_auth(auth))
            return _render_index(
                discovered=result.get("collections") or [],
                discover_auth={**auth, "base_url": discover_base_url},
            )
        return redirect(url_for("calendar_core_admin.index"))

    @bp.post("/feeds/<feed_id>/auth")
    def update_auth(feed_id: str) -> Response:
        """Set / change / clear a feed's credentials without recreating it.
        A blank password leaves the stored one untouched (so the form
        never has to echo the secret back); switching to ``none`` drops
        the credentials entirely."""
        data = _load_feeds()
        feed = next((f for f in data.get("feeds", []) if f.get("id") == feed_id), None)
        if not feed:
            abort(404)
        if feed_source(feed) == "ha":
            flash(
                "Home Assistant feeds use the shared connection from "
                "Settings → Widgets → Home Assistant Core; there are no "
                "per-feed credentials.",
                "warn",
            )
            return redirect(url_for("calendar_core_admin.index"))
        auth = _read_auth_form()
        if auth["auth_mode"] == "none":
            for key in ("auth_mode", "username", "password"):
                feed.pop(key, None)
        else:
            feed["auth_mode"] = auth["auth_mode"]
            feed["username"] = auth["username"]
            # Blank password = keep the existing one (form doesn't echo it).
            if auth["password"]:
                feed["password"] = auth["password"]
        _save_feeds(data)
        # Force a re-fetch so the new creds take effect immediately.
        with contextlib.suppress(OSError):
            _ics_cache_path(_data_dir(), feed_id).unlink(missing_ok=True)
        flash(f"Updated credentials for '{feed.get('name') or feed_id}'.", "ok")
        return redirect(url_for("calendar_core_admin.index"))

    @bp.post("/discover")
    def discover() -> str:
        """Enumerate a self-hosted CalDAV server's calendars / todo lists
        from one PROPFIND, so the user picks + adds them instead of hand-
        constructing each ``?export`` URL."""
        base_url = (request.form.get("base_url") or "").strip()
        auth = _read_auth_form()
        if not base_url:
            flash("Enter your CalDAV calendar-home URL.", "warn")
            return _render_index()
        result = discover_collections(base_url, _feed_auth(auth))
        if result.get("error"):
            flash(result["error"], "warn")
        elif not result.get("collections"):
            flash("No calendars found at that URL.", "warn")
        # Re-render the index with the discovered list + the creds echoed
        # into the per-row Add forms so a one-click add carries them. Echo
        # base_url back too so a failed discovery doesn't wipe the field the
        # user just typed (the template reads discover_auth.base_url).
        return _render_index(
            discovered=result.get("collections") or [],
            discover_auth={**auth, "base_url": base_url},
        )

    @bp.post("/ha/list")
    def ha_list() -> str:
        """List Home Assistant calendar entities so the user can add each
        as a feed in one click, mirroring the CalDAV discovery flow. A
        POST-triggered listing (not part of the index render) so an
        unreachable HA can't slow the page down."""
        calendars, error = _ha_calendar_list()
        if error:
            flash(error, "warn")
        elif not calendars:
            flash("No calendar entities found in Home Assistant.", "warn")
        return _render_index(ha_calendars=calendars)

    @bp.post("/feeds/<feed_id>/toggle")
    def toggle_feed(feed_id: str) -> Response:
        data = _load_feeds()
        for f in data.get("feeds", []):
            if f.get("id") == feed_id:
                f["enabled"] = not f.get("enabled", True)
                break
        else:
            abort(404)
        _save_feeds(data)
        return redirect(url_for("calendar_core_admin.index"))

    @bp.post("/feeds/<feed_id>/delete")
    def delete_feed(feed_id: str) -> Response:
        data = _load_feeds()
        before = len(data.get("feeds", []))
        data["feeds"] = [f for f in data.get("feeds", []) if f.get("id") != feed_id]
        if len(data["feeds"]) == before:
            abort(404)
        _save_feeds(data)
        # Drop cached .ics file too.
        with contextlib.suppress(OSError):
            _ics_cache_path(_data_dir(), feed_id).unlink(missing_ok=True)
        flash(f"Deleted feed '{feed_id}'.", "ok")
        return redirect(url_for("calendar_core_admin.index"))

    @bp.post("/feeds/<feed_id>/refresh")
    def refresh_feed(feed_id: str) -> Response:
        # Force a refetch by deleting the cached blob, next call to
        # _fetch_ics will re-download. Flash an informative message
        # afterwards so HA / Docker users (who can't SSH to check the
        # cache dir) can tell whether the refresh actually pulled new
        # events.
        with contextlib.suppress(OSError):
            _ics_cache_path(_data_dir(), feed_id).unlink(missing_ok=True)
        data = _load_feeds()
        feed = next((f for f in data.get("feeds", []) if f.get("id") == feed_id), None)
        if not feed:
            abort(404)
        name = feed.get("name") or feed_id
        if feed_source(feed) == "ha":
            _drop_ha_cache(feed_id)
            window_start = datetime.now(UTC) - timedelta(days=30)
            window_end = datetime.now(UTC) + timedelta(days=90)
            # The cache was just dropped, so this is a real fetch and the
            # health record reflects its outcome.
            events = _fetch_ha_events(feed, window_start, window_end, _data_dir())
            health = load_health().get(feed_id) or {}
            if health.get("error"):
                flash(f"Refresh failed for '{name}': {health['error']}", "warn")
            else:
                now_iso = datetime.now(UTC).isoformat()
                future = [e for e in events if str(e.get("start") or "") >= now_iso]
                flash(
                    f"Refreshed '{name}': {len(events)} events in the past 30 / "
                    f"next 90 days ({len(future)} upcoming).",
                    "ok",
                )
            return redirect(url_for("calendar_core_admin.index"))
        url = feed.get("url") or ""
        if not url:
            flash(f"Feed '{name}' has no URL to refresh from.", "warn")
            return redirect(url_for("calendar_core_admin.index"))
        blob = _fetch_ics(url, _ics_cache_path(_data_dir(), feed_id), _feed_auth(feed))
        if blob is None:
            flash(
                f"Refresh failed for '{name}': couldn't reach the feed URL. "
                f"Check the URL is reachable from the server.",
                "warn",
            )
            return redirect(url_for("calendar_core_admin.index"))
        # Peek the fetched blob so the user sees an actionable count.
        # A wide window (past 30 days .. future 90 days) captures the
        # common "feed loaded but nothing in view" symptom.
        try:
            window_start = datetime.now(UTC) - timedelta(days=30)
            window_end = datetime.now(UTC) + timedelta(days=90)
            events = _expand_events(blob, window_start, window_end)
            future = [
                e
                for e in events
                if e.get("start") and str(e["start"]) >= datetime.now(UTC).isoformat()
            ]
            flash(
                f"Refreshed '{name}': {len(events)} events in the past 30 / "
                f"next 90 days ({len(future)} upcoming). "
                f"Cache is {len(blob)} bytes.",
                "ok",
            )
        except Exception:
            # Fallback for a broken .ics blob: still report the bytes so
            # the user knows the fetch succeeded but parsing didn't.
            flash(
                f"Refreshed '{name}' ({len(blob)} bytes on disk), but "
                f"couldn't parse events. The .ics file may be malformed.",
                "warn",
            )
        return redirect(url_for("calendar_core_admin.index"))

    return bp


def _safe_secret() -> str:
    return secrets.token_hex(6)
