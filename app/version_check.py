"""Background, cached "update available" check for the web header.

Reads ``api.tesserae.ink/version/latest`` for the running app off the request
thread: the header context processor calls :func:`status`, which returns the
last cached result instantly and kicks a single-flight background refresh when
stale. Nothing here ever blocks a page render on the network.

Gated by the online-features opt-out, exactly like the heartbeat: an install
that turned online features off makes no call and shows no icon. The install id
is scoped (a one-way derivation) before it's sent, so the header check can't be
correlated with the heartbeat's raw id server-side.

Channel follows the build (:func:`app.heartbeat.channel`):

* ``stable`` compares the running version to the latest stable release. An
  install ahead of stable reports no update rather than a false nag.
* ``edge`` (a Docker image built from a push to main) and ``main`` (a source
  checkout off a tag) have no release tags to compare, so they ask the API's
  ``main`` channel how many commits the build is behind the head of main. The
  build commit is ``TESSERAE_BUILD_SHA``, which docker.yml stamps into the
  image, or ``git rev-parse HEAD`` for a source checkout. An edge image built
  before that stamp existed has no commit to send, so it shows no badge.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from typing import Any

from flask import Flask

from app import heartbeat as _heartbeat
from app import install_id as _install_id
from app import online

logger = logging.getLogger(__name__)

_TTL_SECONDS = 6 * 60 * 60
_SCOPE = "web_update_check"

_lock = threading.Lock()
_state: dict[str, Any] = {"available": False}
_fetched_at = 0.0
_refreshing = False
_thread: threading.Thread | None = None


def status(app: Flask) -> dict[str, Any]:
    """The cached update status for the header. Never blocks: returns the last
    known result and starts a background refresh when the cache is stale.

    Shape: ``{available: bool, channel, latest, url, behind, label}`` when
    online features are on (``latest`` is a version on stable and a short
    commit on edge / main; ``label`` is what the badge prints), or ``{available: False, disabled: True}`` when off.
    """
    global _refreshing, _thread
    settings = app.config.get("SETTINGS_STORE")
    if not online.online_enabled(settings):
        return {"available": False, "disabled": True}
    # Same gate, same moment: an install whose UI is open and that has a
    # heartbeat due sends it now. Closes the gap where short-lived installs
    # showed up in the update check but never in the heartbeat.
    _heartbeat.kick(app)
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
                args=(_channel(), current, install),
                name="tesserae-version-check",
                daemon=True,
            )
            _thread.start()
        return dict(_state)


def _channel() -> str:
    """The build's release channel. A seam so tests can pin it."""
    return _heartbeat.channel()


def _build_sha() -> str:
    """The commit this build came from: the image stamp, else the checkout's
    HEAD. ``""`` when neither is known. Runs on the refresh thread, never on a
    request (it may shell out to git)."""
    stamped = (os.environ.get("TESSERAE_BUILD_SHA") or "").strip()
    if stamped:
        return stamped
    try:
        from app.main import REPO_ROOT

        if not (REPO_ROOT / ".git").exists():
            return ""
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _refresh(channel: str, current: str, install: str) -> None:
    global _state, _fetched_at, _refreshing
    result: dict[str, Any] = {"available": False}
    try:
        if channel in ("edge", "main"):
            result = _check_main(channel, install)
        else:
            result = _check_stable(current, install)
    except Exception:
        logger.debug("version check refresh failed", exc_info=True)
    finally:
        with _lock:
            _state = result
            _fetched_at = time.time()
            _refreshing = False


def _check_stable(current: str, install: str) -> dict[str, Any]:
    data = online.latest_version("stable", current, install)
    if not data:
        return {"available": False}
    latest = data.get("latest") or {}
    behind = data.get("versions_behind")
    # Available ONLY when we're strictly behind: the API counts releases
    # newer than us. Do NOT fall back to is_current (== latest): that's
    # False whenever our version differs from the latest release,
    # including when we're AHEAD of stable (an edge / source build), and
    # would show a badge pointing at an older release. When the count is
    # None (an unparseable version) we can't tell, so show nothing.
    available = behind is not None and behind > 0
    version = latest.get("version")
    return {
        "available": bool(available),
        "channel": "stable",
        "latest": version,
        "url": latest.get("url"),
        "behind": behind or 0,
        "label": f"v{version}" if version else "",
    }


def _check_main(channel: str, install: str) -> dict[str, Any]:
    sha = _build_sha()
    if not sha:
        return {"available": False}
    data = online.latest_version("main", sha, install)
    if not data:
        return {"available": False}
    latest = data.get("latest") or {}
    behind = data.get("commits_behind")
    # None means the API doesn't know our commit (a local or unpushed
    # build, or a fork): no badge rather than a guess.
    available = behind is not None and behind > 0
    n = behind or 0
    return {
        "available": bool(available),
        "channel": channel,
        "latest": latest.get("sha"),
        "url": latest.get("url"),
        "behind": n,
        "label": f"{n} new commit{'s' if n != 1 else ''}" if n else "",
    }


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
