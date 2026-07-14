"""Generic REST/JSON service (kind: service).

A non-placeable data source that GETs an arbitrary public JSON endpoint the
agent supplies and returns the parsed response to a code element. It's the
universal fallback for any API without a bespoke service plugin.

Safety: only http(s) is allowed, and loopback / private / link-local hosts are
blocked so the agent can't turn this into an SSRF against the machine Tesserae
runs on (it would otherwise be a way to reach Tesserae's own loopback API or a
LAN service). This is a best-effort hostname/IP guard, not a full SSRF sandbox.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urlparse

from app.plugin_http import fetch_json


def _discovery() -> dict[str, Any]:
    return {
        "service": "rest",
        "usage": "Set options.url to a public https JSON endpoint; optionally set "
        "options.headers to a JSON object of extra request headers (e.g. "
        '{"Authorization": "Bearer …"}). Returns the parsed JSON under "data", or '
        'an "error". Loopback and private hosts are blocked.',
    }


def _host_is_blocked(host: str) -> bool:
    """True when the host is loopback / private / link-local (block SSRF)."""
    if not host or host.lower() in ("localhost", "localhost.localdomain"):
        return True
    candidates: list[str] = []
    try:
        infos = socket.getaddrinfo(host, None)
        candidates = [info[4][0] for info in infos]
    except OSError:
        candidates = [host]  # couldn't resolve; check the literal
    for addr in candidates:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
    return False


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    url = str(options.get("url") or "").strip()
    if not url:
        return _discovery()

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"error": "url must be http(s)"}
    if _host_is_blocked(parsed.hostname or ""):
        return {"error": "host is loopback/private and not allowed"}

    headers: dict[str, str] = {}
    raw_headers = str(options.get("headers") or "").strip()
    if raw_headers:
        try:
            parsed_headers = json.loads(raw_headers)
            if isinstance(parsed_headers, dict):
                headers = {str(k): str(v) for k, v in parsed_headers.items()}
        except (json.JSONDecodeError, ValueError):
            return {"error": "headers must be a JSON object"}

    try:
        data = fetch_json(url, headers=headers or None, timeout=8.0, retries=1)
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}
    return {"data": data, "url": url}
