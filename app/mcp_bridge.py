"""Which tesserae-mcp bridge is talking to us, and whether it is out of date.

The bridge is a separately installed package (``pipx install tesserae-mcp``)
that wraps this server's ``/api/mcp`` surface. Most of what an agent reads is
served live from here: ``/api/mcp/instructions`` hands over the handshake
instructions and the canvas doc-shape, so a copy or capability change reaches a
connected agent with no bridge release at all.

What a running bridge cannot pick up is anything baked into the installed
wheel: the tool list, each tool's own docstring, and its client-side result
trimming. Those move only with a release, and an operator had no way to know a
release had happened, or which version their agent was even running.

Every bridge call already identifies itself as ``User-Agent:
tesserae-mcp/<version>``, so the answer is on the wire.
:func:`record_request` stashes what called and when, :func:`status` compares it
against :data:`EXPECTED_VERSION` (the bridge that ships in this repo), and
Settings → System → MCP reveals a card once something has actually connected.
Nothing is shown before then, so an install that never uses MCP never sees it.
"""

from __future__ import annotations

import contextlib
import re
import time
from typing import Any

from app.semver import is_strictly_newer
from app.state.settings_store import SettingsStore

# The bridge version this Tesserae ships alongside. Both live in this repo
# (``packages/tesserae-mcp``), so the server knows what "current" means without
# a network call; ``test_mcp_bridge_version.py`` fails the build if they drift.
EXPECTED_VERSION = "0.14.0"

UPGRADE_COMMAND = "pipx upgrade tesserae-mcp"

# Where the last-seen record lives in settings. No ``_secret`` suffix: a client
# version string is not a credential.
_SEEN_KEY = "mcp_bridge_seen"

# Don't rewrite settings on every single tool call. A busy compose session fires
# dozens of requests a minute and the timestamp only drives a "last seen" line,
# so refreshing it every few minutes is plenty. A *changed* client is always
# written through immediately, which is the case that matters.
_REFRESH_SECONDS = 300.0

# ``tesserae-mcp/0.14.0``. Anything else (curl, a hand-rolled client) is still
# recorded as activity, just without a version to compare.
_UA_RE = re.compile(r"^tesserae-mcp/(\d[A-Za-z0-9.+-]*)$")

_CLIENT_MAX = 80


def _seen(settings: SettingsStore) -> dict[str, Any]:
    raw = settings.get_section("app").get(_SEEN_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def record_request(settings: SettingsStore, user_agent: str) -> None:
    """Note that an MCP client just called, and which one.

    Called for authorised requests only, so an unauthenticated prod can't write
    to settings. Silently does nothing on a store error: this is bookkeeping
    for a status card, never a reason to fail the agent's actual call.
    """
    ua = (user_agent or "").strip()[:_CLIENT_MAX]
    match = _UA_RE.match(ua)
    version = match.group(1) if match else ""
    now = time.time()

    previous = _seen(settings)
    unchanged = previous.get("version") == version and previous.get("client") == ua
    if unchanged and (now - _as_float(previous.get("at"))) < _REFRESH_SECONDS:
        return

    # A status card is never worth failing the agent's actual call over.
    with contextlib.suppress(Exception):
        settings.patch_section("app", {_SEEN_KEY: {"version": version, "client": ua, "at": now}})


def status(settings: SettingsStore) -> dict[str, Any]:
    """What to show in Settings → System → MCP.

    ``seen`` is False until a client has actually connected, which is what keeps
    the card hidden on an install that has enabled MCP but never used it.
    ``update_available`` is True only when the connected bridge parses as
    strictly older than what this repo ships: an unparseable or *newer* version
    (someone running from a clone) reports no update rather than a false nag.
    """
    seen = _seen(settings)
    version = str(seen.get("version") or "")
    client = str(seen.get("client") or "")
    at = _as_float(seen.get("at"))
    newer = is_strictly_newer(EXPECTED_VERSION, version) if version else None
    return {
        "seen": bool(client or at),
        "version": version,
        "client": client,
        "at": at,
        "expected": EXPECTED_VERSION,
        "update_available": newer is True,
        # Activity from something that isn't the bridge (curl, a custom client).
        # Worth showing as a connection, but there's no version to nag about.
        "unknown_client": bool(client) and not version,
        "upgrade_command": UPGRADE_COMMAND,
    }


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
