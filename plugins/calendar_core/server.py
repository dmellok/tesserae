"""calendar_core — iCal feed registry + admin page.

Plugins can't import each other's Python modules through normal
package paths, but they can reach into another plugin's server_module
via the live PluginRegistry (current_app.config["PLUGIN_REGISTRY"]).
The three calendar widgets do exactly that — they call
``load_events()`` here so feed-fetching, caching, and recurring-event
expansion live in one place.

Feeds are stored in ``feeds.json`` inside the plugin's data_dir:
  {"feeds": [{"id": "..", "name": "..", "url": "..", "colour": "#..", "enabled": true}]}
The admin page (mounted at /plugins/calendar_core/) provides CRUD for
this list — same shape as the todo plugin.

Per-feed caching:
  feed_<id>.ics      raw .ics bytes, 15 minute TTL
The widgets keep their own narrower caches for the rendered event
slices they need.
"""

from __future__ import annotations

import contextlib
import json
import re
import secrets
import time
import urllib.request
from datetime import UTC, datetime
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

CACHE_TTL_S = 15 * 60
HTTP_TIMEOUT_S = 15
USER_AGENT = "tesserae/0.1 (+calendar_core)"
DEFAULT_COLOUR = "#0d8c7e"


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
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"feeds": []}
    if not isinstance(data, dict) or not isinstance(data.get("feeds"), list):
        return {"feeds": []}
    return data


def _save_feeds(data: dict[str, Any], data_dir: Path | None = None) -> None:
    path = _feeds_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


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


def _fetch_ics(url: str, cache_path: Path) -> bytes | None:
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < CACHE_TTL_S:
        try:
            return cache_path.read_bytes()
        except OSError:
            pass
    # Some providers (notably Google) require https; webcal:// → https://.
    fixed = url.replace("webcal://", "https://", 1) if url.startswith("webcal://") else url
    try:
        req = urllib.request.Request(fixed, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            blob = resp.read()
    except Exception:
        return None
    with contextlib.suppress(OSError):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(blob)
    return blob


def _expand_events(
    blob: bytes, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Parse an .ics blob + return a JSON-safe list of events in
    [start, end), including recurring expansions handled by
    recurring_ical_events."""
    try:
        cal = icalendar.Calendar.from_ical(blob)
    except Exception:
        return []
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
            out.append({
                "summary":  summary or "(untitled)",
                "location": location,
                "start":    s_iso,
                "end":      e_iso,
                "all_day":  all_day,
            })
        except Exception:
            continue
    out.sort(key=lambda e: (not e["all_day"], e["start"]))
    return out


def load_events(
    feed_ids: list[str] | None,
    start: datetime,
    end: datetime,
    *,
    data_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Public API for the calendar_* widgets — returns events from the
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
        url = feed.get("url")
        if not url:
            continue
        blob = _fetch_ics(url, _ics_cache_path(dd, fid))
        if blob is None:
            continue
        for ev in _expand_events(blob, start, end):
            ev["feed_id"]     = fid
            ev["feed_name"]   = feed.get("name") or fid
            ev["feed_colour"] = feed.get("colour") or DEFAULT_COLOUR
            out.append(ev)
    out.sort(key=lambda e: (not e["all_day"], e["start"]))
    return out


# ----- cell-option choices --------------------------------------------

def choices(name: str) -> list[dict[str, str]]:
    """Powers the multi-select feed picker on each calendar widget."""
    if name != "feeds":
        return []
    feeds = _load_feeds().get("feeds") or []
    return [
        {"value": f["id"], "label": f.get("name") or f["id"]}
        for f in feeds if f.get("enabled", True)
    ]


# ----- admin blueprint ------------------------------------------------

def blueprint() -> Blueprint:
    bp = Blueprint("calendar_core_admin", __name__, template_folder="templates")

    @bp.get("/")
    def index() -> str:
        feeds = _load_feeds().get("feeds") or []
        return render_template("calendar_core/index.html", feeds=feeds)

    @bp.post("/feeds")
    def create_feed() -> Response:
        name = (request.form.get("name") or "").strip()
        url = (request.form.get("url") or "").strip()
        colour = (request.form.get("colour") or DEFAULT_COLOUR).strip()
        if not name or not url:
            flash("Name and URL are required.", "warn")
            return redirect(url_for("calendar_core_admin.index"))
        if not re.match(r"^#[0-9a-fA-F]{6}$", colour):
            colour = DEFAULT_COLOUR
        data = _load_feeds()
        fid = _unique_id(data, _slugify(name))
        data["feeds"].append({
            "id":         fid,
            "name":       name,
            "url":        url,
            "colour":     colour,
            "enabled":    True,
            "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        })
        _save_feeds(data)
        flash(f"Added feed '{name}'.", "ok")
        return redirect(url_for("calendar_core_admin.index"))

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
        # Force a refetch by deleting the cached blob — next call to
        # _fetch_ics will re-download.
        with contextlib.suppress(OSError):
            _ics_cache_path(_data_dir(), feed_id).unlink(missing_ok=True)
        # Trigger a fetch immediately so the user sees an updated count.
        data = _load_feeds()
        feed = next((f for f in data.get("feeds", []) if f.get("id") == feed_id), None)
        if feed and feed.get("url"):
            _fetch_ics(feed["url"], _ics_cache_path(_data_dir(), feed_id))
        flash("Feed refreshed.", "ok")
        return redirect(url_for("calendar_core_admin.index"))

    return bp


def _safe_secret() -> str:
    return secrets.token_hex(6)
