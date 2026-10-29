"""Background, cached count of catalog widgets with an update waiting.

The Widgets entry in the sidebar carries a small count, the way a phone's
app-store icon does, when installed catalog widgets have a newer release in
the index. The header context processor calls :func:`status`, which answers
from the marketplace's cached index instantly and kicks a single-flight
background refresh of that index when it is stale. Nothing here blocks a
page render on the network.

Three gates, all of which must be open for a count to show:

* the master online-features switch (``settings.app.online_features``),
  since the catalog index lives on GitHub;
* the badge's own switch (``settings.app.widget_update_badge``, on by
  default), for an operator who would rather check the Browse page
  themselves;
* a marketplace with an index URL.

An update that needs a newer Tesserae than the one running is not counted:
the Browse page cannot apply it, so the badge would nag about something the
operator cannot act on.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from app import online

if TYPE_CHECKING:
    from app.marketplace import CatalogEntry, InstalledRecord, Marketplace

logger = logging.getLogger(__name__)

# How long a fetched index serves the badge before a background refresh.
# Longer than the marketplace's own in-memory TTL (which protects the Browse
# page's refresh button), since a badge that is an hour behind is fine.
_TTL_SECONDS = 60 * 60
# After a failed fetch (offline, GitHub down) the next try waits this long
# rather than firing on every page render.
_RETRY_SECONDS = 5 * 60
_SETTING = "widget_update_badge"

_lock = threading.Lock()
_fetched_at = 0.0
_refreshing = False
_thread: threading.Thread | None = None
# Bumped by reset(): a refresh started before it must not write its result
# into the state that came after (tests reset between cases while a thread
# from the previous case may still be finishing).
_generation = 0


def badge_enabled(settings: Any) -> bool:
    """Whether the operator wants the sidebar count (on by default)."""
    if settings is None:
        return True
    try:
        section = settings.get_section("app") or {}
    except Exception:
        return True
    raw = section.get(_SETTING, True)
    if isinstance(raw, str):
        return raw.strip().lower() not in ("0", "false", "no", "off")
    return bool(raw)


def applicable_updates(
    entries: list[CatalogEntry],
    installed: dict[str, InstalledRecord],
    host_version: str | None = None,
) -> list[CatalogEntry]:
    """The catalog entries whose installed copy is behind the index AND whose
    new release this server can run. The same rule the Browse page uses for
    its per-row "Update to vX" button, minus the rows that button disables.

    ``host_version`` is passed explicitly so the background thread can
    answer without an application context."""
    from app import plugin_loader

    out: list[CatalogEntry] = []
    for entry in entries:
        record = installed.get(entry.id)
        if record is None or record.version == entry.release_version:
            continue
        if plugin_loader.too_new_for_host(entry.tesserae_min, host_version):
            continue
        out.append(entry)
    return out


def status(app: Any) -> dict[str, Any] | None:
    """The cached update count for the sidebar, or None when no badge should
    render (a gate is closed, or nothing is known yet). Never blocks.

    Shape: ``{count: int, ids: [catalog ids]}``; ``count`` is never 0 in a
    returned dict, so templates can test the dict itself.
    """
    global _refreshing, _thread
    settings = app.config.get("SETTINGS_STORE")
    if not online.online_enabled(settings) or not badge_enabled(settings):
        return None
    mkt = app.config.get("MARKETPLACE")
    if mkt is None:
        return None
    try:
        if not mkt.index_url():
            return None
        entries = mkt.cached_index()
    except Exception:
        return None
    now = time.time()
    with _lock:
        wait = _TTL_SECONDS if entries is not None else _RETRY_SECONDS
        stale = (now - _fetched_at) > wait
        if stale and not _refreshing:
            _refreshing = True
            _thread = threading.Thread(
                target=_refresh,
                args=(mkt, _generation),
                name="tesserae-widget-updates",
                daemon=True,
            )
            _thread.start()
    if not entries:
        return None
    try:
        installed = mkt.installed()
    except Exception:
        return None
    host = str(app.config.get("APP_VERSION") or "") or None
    behind = applicable_updates(entries, installed, host)
    if not behind:
        return None
    return {"count": len(behind), "ids": [e.id for e in behind]}


def _refresh(mkt: Marketplace, generation: int = 0) -> None:
    """Pull the index into the marketplace's cache off the request thread.
    A failure is logged at debug and leaves whatever was cached in place;
    the next stale render tries again."""
    global _refreshing, _fetched_at
    try:
        mkt.fetch_index()
    except Exception:
        # Back off below: a failed fetch still counts as an attempt, so an
        # offline install isn't retried on every page render.
        logger.debug("widget update check failed", exc_info=True)
    finally:
        with _lock:
            if generation == _generation:
                _fetched_at = time.time()
                _refreshing = False


def reset() -> None:
    """Forget the cache (tests)."""
    global _fetched_at, _refreshing, _thread, _generation
    with _lock:
        _generation += 1
        _fetched_at = 0.0
        _refreshing = False
        _thread = None


def join_for_test(timeout: float = 3.0) -> None:
    """Wait for an in-flight refresh (tests)."""
    t = _thread
    if t is not None:
        t.join(timeout)
