"""Background, cached "update available" check for the web header.

Reads ``api.tesserae.ink/version/latest`` for the running app off the request
thread: the header context processor calls :func:`status`, which returns the
last cached result instantly and kicks a single-flight background refresh when
stale. Nothing here ever blocks a page render on the network.

Gated by the online-features opt-out, exactly like the heartbeat: an install
that turned online features off makes no call and shows no icon. The install id
is scoped (a one-way derivation) before it's sent, so the header check can't be
correlated with the heartbeat's raw id server-side.

Channel: only ``stable`` is meaningfully populated on the API today (the
release cadence tags every change but cuts full releases, and there are no
prerelease "edge" tags), so the check compares the running version to the
latest stable release. An install already ahead of stable (a source / edge
build) simply reports no update rather than a false nag. Wire another channel
here if edge ever gets its own published tags.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from flask import Flask

from app import install_id as _install_id
from app import online

logger = logging.getLogger(__name__)

_TTL_SECONDS = 6 * 60 * 60
_CHANNEL = "stable"
_SCOPE = "web_update_check"

_lock = threading.Lock()
_state: dict[str, Any] = {"available": False}
_fetched_at = 0.0
_refreshing = False
_thread: threading.Thread | None = None


def status(app: Flask) -> dict[str, Any]:
    """The cached update status for the header. Never blocks: returns the last
    known result and starts a background refresh when the cache is stale.

    Shape: ``{available: bool, channel, latest, url, behind}`` when online
    features are on, or ``{available: False, disabled: True}`` when off.
    """
    global _refreshing, _thread
    settings = app.config.get("SETTINGS_STORE")
    if not online.online_enabled(settings):
        return {"available": False, "disabled": True}
    now = time.time()
    with _lock:
        stale = (now - _fetched_at) > _TTL_SECONDS
        if stale and not _refreshing:
            _refreshing = True
            current = str(app.config.get("APP_VERSION") or "")
            raw_install = str(app.config.get("INSTALL_ID") or "")
            install = _install_id.scoped_id(raw_install, _SCOPE) if raw_install else ""
            _thread = threading.Thread(
                target=_refresh,
                args=(current, install),
                name="tesserae-version-check",
                daemon=True,
            )
            _thread.start()
        return dict(_state)


def _refresh(current: str, install: str) -> None:
    global _state, _fetched_at, _refreshing
    result: dict[str, Any] = {"available": False}
    try:
        data = online.latest_version(_CHANNEL, current, install)
        if data:
            latest = data.get("latest") or {}
            behind = data.get("versions_behind")
            # Available ONLY when we're strictly behind: the API counts releases
            # newer than us. Do NOT fall back to is_current (== latest): that's
            # False whenever our version differs from the latest release,
            # including when we're AHEAD of stable (an edge / source build), and
            # would show a badge pointing at an older release. When the count is
            # None (an unparseable version) we can't tell, so show nothing.
            available = behind is not None and behind > 0
            result = {
                "available": bool(available),
                "channel": _CHANNEL,
                "latest": latest.get("version"),
                "url": latest.get("url"),
                "behind": behind or 0,
            }
    except Exception:
        logger.debug("version check refresh failed", exc_info=True)
    finally:
        with _lock:
            _state = result
            _fetched_at = time.time()
            _refreshing = False


def reset() -> None:
    """Clear the cache so the next status() refetches. Test helper."""
    global _state, _fetched_at, _refreshing
    with _lock:
        _state = {"available": False}
        _fetched_at = 0.0
        _refreshing = False


def join_for_test(timeout: float = 3.0) -> None:
    """Wait for an in-flight background refresh to finish. Test helper."""
    t = _thread
    if t is not None:
        t.join(timeout)
