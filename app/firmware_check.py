"""Firmware update check for registered devices.

Each device kind's manifest may declare a ``firmware_source`` block, and
Tesserae queries ``api.tesserae.ink/firmware/<kind>/latest`` (v0.70.0)
periodically to learn what version is available. The heartbeat's
``fw_version`` field lets the Devices card show a "vX.Y.Z" chip plus an
amber "update available" indicator when the device is running an older
build.

This module deliberately keeps things lightweight for v0.70.0:

* A single process-lifetime in-memory cache with a 60-minute TTL per
  kind. No on-disk persistence; a Tesserae restart re-fetches.
* Fetches happen lazily on the first request for a given kind and any
  subsequent request after TTL expiry. No background timer.
* Failures are silent, the cache stays empty for that kind and the UI
  simply hides the "update available" chip until the next fetch.

Nothing about this module tracks per-user or per-device identity. The
outbound call sends ``kind`` and ``current`` (fw version) as query
parameters. IP-based geo lookup happens on the api.tesserae.ink side and
is aggregated only (documented on the privacy page).
"""

from __future__ import annotations

import contextlib
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ``api.tesserae.ink`` is the aggregation endpoint shared with the version
# check widget. Override for local development via ``TESSERAE_FIRMWARE_API``.
_DEFAULT_API_BASE = "https://api.tesserae.ink"
_CACHE_TTL_SECONDS = 60 * 60
_HTTP_TIMEOUT_SECONDS = 4.0


@dataclass(frozen=True)
class FirmwareInfo:
    """One kind's latest firmware info as reported by api.tesserae.ink.

    Fields mirror the api.tesserae.ink response, minus the caller-scoped
    fields (``current``/``is_current``/``versions_behind``) which are
    computed per-device by :func:`compare_versions`.
    """

    version: str
    released_at: str
    url: str
    notes_headline: str
    assets: tuple[dict[str, Any], ...]


# Kind -> (fetched_at_epoch, FirmwareInfo | None). A ``None`` entry means we
# tried and couldn't get data (repo has no releases, network error, etc.);
# it still counts against the TTL to avoid hammering the endpoint.
_cache: dict[str, tuple[float, FirmwareInfo | None]] = {}


def latest_for_kind(kind: str, *, api_base: str = _DEFAULT_API_BASE) -> FirmwareInfo | None:
    """Return the latest known firmware info for ``kind`` (or ``None``).

    Consults the in-memory cache first; hits api.tesserae.ink on a miss
    or after TTL expiry. Never raises: any failure returns ``None`` and
    the caller renders a "current" chip without an update indicator.
    """
    now = time.time()
    hit = _cache.get(kind)
    if hit is not None and (now - hit[0]) < _CACHE_TTL_SECONDS:
        return hit[1]
    info = _fetch(kind, api_base=api_base)
    _cache[kind] = (now, info)
    return info


def clear_cache() -> None:
    """Drop the in-memory cache (used by tests + on-demand refresh)."""
    _cache.clear()


def compare_versions(current: str | None, latest: FirmwareInfo | None) -> str:
    """Return ``"current"``, ``"outdated"``, ``"unknown"``, or ``"no_data"``.

    * ``current``: caller is on the latest version.
    * ``outdated``: caller is behind the latest version.
    * ``unknown``: we don't know what the caller is running (device
      heartbeat didn't include ``fw_version``, or the value can't be
      compared to the latest).
    * ``no_data``: we have no ``latest`` info to compare against (the
      firmware source has no releases yet, or the fetch failed).
    """
    if latest is None:
        return "no_data"
    if not current:
        return "unknown"
    # Cheap string equality first; the fast common case.
    if current == latest.version or current.lstrip("v") == latest.version.lstrip("v"):
        return "current"
    # Best-effort SemVer compare. Falls back to "unknown" when either side
    # can't be parsed (e.g. a "1.2.0-dev+abc" build the caller can't compare
    # against a released "1.2.0"). "unknown" is a truthful signal, not a
    # misleading "outdated" claim.
    try:
        from packaging.version import Version

        return "outdated" if Version(current) < Version(latest.version) else "current"
    except Exception:
        return "unknown"


def _fetch(kind: str, *, api_base: str) -> FirmwareInfo | None:
    url = f"{api_base.rstrip('/')}/firmware/{urllib.parse.quote(kind)}/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "tesserae-firmware-check"})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            if resp.status != 200:
                return None
            import json

            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # HTTPError holds an open response body (a NamedTemporaryFile on
        # Python 3.14+). Close it explicitly so it doesn't surface as a
        # ResourceWarning on GC (which pytest promotes to an error).
        with contextlib.suppress(Exception):
            exc.close()
        logger.debug("firmware_check: fetch failed for kind=%s: %s", kind, exc)
        return None
    except Exception as exc:
        logger.debug("firmware_check: fetch failed for kind=%s: %s", kind, exc)
        return None
    latest = payload.get("latest")
    if not isinstance(latest, dict):
        return None
    return FirmwareInfo(
        version=str(latest.get("version") or ""),
        released_at=str(latest.get("released_at") or ""),
        url=str(latest.get("url") or ""),
        notes_headline=str(latest.get("notes_headline") or ""),
        assets=tuple(latest.get("assets") or ()),
    )
