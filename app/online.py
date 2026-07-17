"""Outbound calls to api.tesserae.ink, behind a single master opt-out.

Everything Tesserae sends to api.tesserae.ink (app + device-firmware update
checks, the marketplace install count, and the daily heartbeat) is gated by one
setting, ``settings.app.online_features``. It is ON by default; turning it off
means the app never contacts api.tesserae.ink.

What is sent is documented on the privacy page: the install's random id (from
``data/core/install_id.json``), the widget id, and the running version. A coarse
country is derived from the caller IP on the server side and the IP is then
discarded. No account, no personal data, no IP or User-Agent is stored.

Every call here is best-effort: it never raises, and a failure (endpoint down,
offline, opted out) degrades to "no data" rather than surfacing an error.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# Override for local development / tests via ``TESSERAE_API_BASE``.
API_BASE = os.environ.get("TESSERAE_API_BASE", "https://api.tesserae.ink")
_TIMEOUT_SECONDS = 4.0
_COUNTS_TTL_SECONDS = 300.0


def _coerce_bool(raw: Any, *, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return default


def _ephemeral_environment() -> bool:
    """True in a build/dev/CI environment that boots the app but isn't a real
    install: our CI, GitHub Codespaces, or Gitpod. We never phone home from
    these so they don't pollute the aggregate stats.

    Only *precise*, provider-injected markers are used. The generic ``CI`` var
    is deliberately NOT checked: a real deployment can carry it (leaked from a
    CI/CD pipeline or a base image), and gating on it would silently drop
    legitimate installs. ``GITHUB_ACTIONS`` / ``CODESPACES`` are set only by
    GitHub's runners / Codespaces, and ``GITPOD_WORKSPACE_ID`` only inside
    Gitpod, so none of them appear on a Docker / HA add-on / pip / LXC / bare
    install."""
    if _coerce_bool(os.environ.get("GITHUB_ACTIONS"), default=False):
        return True
    if _coerce_bool(os.environ.get("CODESPACES"), default=False):
        return True
    return bool(os.environ.get("GITPOD_WORKSPACE_ID"))


def online_enabled(settings_store: Any) -> bool:
    """Master opt-in. ``settings.app.online_features`` defaults to **off**: a
    fresh install never contacts api.tesserae.ink until the user says yes at the
    first-run wizard (or flips it on in Settings). Nobody is counted without an
    explicit choice.

    Always False in an ephemeral CI / Codespaces / dev-container environment
    (see :func:`_ephemeral_environment`). A pre-existing ``check_firmware_updates``
    opt-in still counts as on, so an upgrader who had enabled the old firmware
    lookup keeps it.
    """
    if settings_store is None or _ephemeral_environment():
        return False
    try:
        section = settings_store.get_section("app") or {}
    except Exception:
        return False
    if "online_features" in section:
        return _coerce_bool(section.get("online_features"), default=False)
    if "check_firmware_updates" in section:
        return _coerce_bool(section.get("check_firmware_updates"), default=False)
    return False


def report_widget_install(
    widget_id: str,
    install_id: str | None,
    version: str | None,
    *,
    api_base: str = API_BASE,
) -> bool:
    """POST one widget-install event to ``/widgets/install``. Best-effort.

    Returns True on a 2xx response, False on any failure. The caller is
    responsible for the opt-out check (:func:`online_enabled`) before calling.
    """
    if not widget_id:
        return False
    body = json.dumps(
        {"widget": widget_id, "install": install_id or "", "version": version or ""}
    ).encode("utf-8")
    url = f"{api_base.rstrip('/')}/widgets/install"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "tesserae-install"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            return 200 <= int(resp.status) < 300
    except urllib.error.HTTPError as exc:
        with contextlib.suppress(Exception):
            exc.close()
        logger.debug("online: install report failed for %s: %s", widget_id, exc)
        return False
    except Exception as exc:
        logger.debug("online: install report failed for %s: %s", widget_id, exc)
        return False


def send_heartbeat(fields: dict[str, Any], *, api_base: str = API_BASE) -> bool:
    """POST one daily heartbeat to ``/heartbeat``. Best-effort; returns success.

    The caller checks :func:`online_enabled` first and builds ``fields`` (only
    low-cardinality, aggregate values). This layer just ships them.
    """
    body = json.dumps(fields).encode("utf-8")
    url = f"{api_base.rstrip('/')}/heartbeat"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "tesserae-heartbeat"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            return 200 <= int(resp.status) < 300
    except urllib.error.HTTPError as exc:
        with contextlib.suppress(Exception):
            exc.close()
        logger.debug("online: heartbeat failed: %s", exc)
        return False
    except Exception as exc:
        logger.debug("online: heartbeat failed: %s", exc)
        return False


_counts_cache: tuple[float, dict[str, int]] | None = None


def widget_install_counts(
    *, api_base: str = API_BASE, ttl: float = _COUNTS_TTL_SECONDS
) -> dict[str, int]:
    """``GET /widgets/installs`` -> ``{widget_id: unique_count}``.

    Cached for ``ttl`` seconds; best-effort, returns ``{}`` on any failure so a
    down endpoint just hides the counts. The caller checks :func:`online_enabled`
    first (a fully opted-out install makes no request at all).
    """
    global _counts_cache
    now = time.time()
    if _counts_cache is not None and (now - _counts_cache[0]) < ttl:
        return _counts_cache[1]
    url = f"{api_base.rstrip('/')}/widgets/installs"
    req = urllib.request.Request(url, headers={"User-Agent": "tesserae-install"})
    counts: dict[str, int] = {}
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            if int(resp.status) == 200:
                payload = json.loads(resp.read().decode("utf-8"))
                raw = payload.get("counts") if isinstance(payload, dict) else None
                if isinstance(raw, dict):
                    counts = {
                        str(k): int(v)
                        for k, v in raw.items()
                        if isinstance(v, (int, float)) and not isinstance(v, bool)
                    }
    except urllib.error.HTTPError as exc:
        with contextlib.suppress(Exception):
            exc.close()
        logger.debug("online: install counts fetch failed: %s", exc)
    except Exception as exc:
        logger.debug("online: install counts fetch failed: %s", exc)
    _counts_cache = (now, counts)
    return counts


def clear_counts_cache() -> None:
    """Drop the cached install counts (tests + on-demand refresh)."""
    global _counts_cache
    _counts_cache = None


def latest_version(
    channel: str,
    current: str | None,
    install: str | None = None,
    *,
    api_base: str = API_BASE,
) -> dict[str, Any] | None:
    """``GET /version/latest?channel=&current=&install=`` -> the parsed dict, or
    ``None`` on any failure.

    The response shape (per channel) is
    ``{channel, current, latest: {version, url, released_at, notes_headline},
    is_current, versions_behind}``. Best-effort; the caller checks
    :func:`online_enabled` first (an opted-out install makes no request).
    """
    from urllib.parse import urlencode

    params = {"channel": channel}
    if current:
        params["current"] = current
    if install:
        params["install"] = install
    url = f"{api_base.rstrip('/')}/version/latest?{urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "tesserae-version"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            if int(resp.status) == 200:
                payload = json.loads(resp.read().decode("utf-8"))
                return payload if isinstance(payload, dict) else None
    except urllib.error.HTTPError as exc:
        with contextlib.suppress(Exception):
            exc.close()
        logger.debug("online: version check failed: %s", exc)
    except Exception as exc:
        logger.debug("online: version check failed: %s", exc)
    return None
