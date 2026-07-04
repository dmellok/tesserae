"""Resolve settings.app.timezone (an IANA name, the literal 'system', or empty) into a real IANA zone name."""

from __future__ import annotations

import os
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def app_timezone() -> tzinfo:
    """Return the tzinfo the app should render user-facing timestamps in.

    Reads ``settings.app.timezone`` via ``current_app.config["SETTINGS_STORE"]``
    (a Flask app context is expected, callers are all inside a request or
    a rendered template). Falls back to system-local when unset, "system",
    or unresolvable; ultimate fallback is UTC so callers always get a
    tz-aware datetime.

    Used by history-row rendering (issue #52 item 2) and any other
    server-side clock display that used to hit a naive
    ``datetime.fromtimestamp`` and inherit the container TZ (UTC on
    Docker / MicroCloud defaults).
    """
    from flask import current_app

    raw = "system"
    try:
        store = current_app.config.get("SETTINGS_STORE")
        if store is not None:
            raw = str((store.get_section("app") or {}).get("timezone") or "system").strip()
    except Exception:
        # Called outside app context (or a corrupt settings store):
        # fall through to system-local resolution.
        raw = "system"

    if raw and raw.lower() != "system":
        try:
            return ZoneInfo(raw)
        except ZoneInfoNotFoundError:
            pass

    resolved = _resolve_iana_timezone("system")
    if resolved:
        try:
            return ZoneInfo(resolved)
        except ZoneInfoNotFoundError:
            pass

    return datetime.now().astimezone().tzinfo or UTC


def _resolve_iana_timezone(stored: str) -> str:
    """Turn the user's ``settings.app.timezone`` value into an IANA
    name. The setting is one of: a real IANA name (``Australia/
    Melbourne``), the literal ``system`` (auto-detect from host), or
    empty (legacy installs that pre-date the setting).

    Returns the empty string when no real IANA name can be derived —
    the caller drops the ``timezone`` property off the event in that
    case rather than ship junk like ``C`` or ``POSIX``.
    """
    import zoneinfo

    available = zoneinfo.available_timezones()

    # Explicit user choice wins, but validate against the IANA db so a
    # stale settings file with a renamed zone doesn't ship garbage.
    if stored and stored.lower() != "system" and stored in available:
        return stored

    # System auto-detect path. ``TZ`` env var is the most explicit
    # signal (Docker images that pin TZ set this), so check it first.
    tz_env = os.environ.get("TZ", "").strip()
    if tz_env and tz_env in available:
        return tz_env

    # macOS / most Linux: /etc/localtime is a symlink into the IANA
    # tzdata tree, e.g. /usr/share/zoneinfo/Australia/Melbourne. We
    # parse the IANA name out of the link target. This is the same
    # technique tzlocal uses; doing it inline keeps us off a new dep.
    try:
        link = os.readlink("/etc/localtime")
    except OSError:
        link = ""
    if link:
        marker = "zoneinfo/"
        idx = link.find(marker)
        if idx >= 0:
            candidate = link[idx + len(marker) :].strip("/")
            if candidate in available:
                return candidate
    return ""
