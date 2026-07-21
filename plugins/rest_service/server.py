"""Generic REST/JSON service (kind: service).

A non-placeable data source that GETs an arbitrary public JSON endpoint the
agent supplies and returns the parsed response to a code element. It's the
universal fallback for any API without a bespoke service plugin.

Safety: fetching is delegated to :mod:`app.net_guard`, which allows only
http(s), refuses loopback / private / link-local hosts, re-validates every
redirect hop, and caps the response size, so the agent can't turn this into an
SSRF against the machine Tesserae runs on. Best-effort, not a full sandbox.
"""

from __future__ import annotations

import json
from typing import Any

from app.net_guard import BlockedURLError, fetch_json


def _discovery() -> dict[str, Any]:
    return {
        "service": "rest",
        "usage": "Set options.url to a public https JSON endpoint; optionally set "
        "options.headers to a JSON object of extra request headers (e.g. "
        '{"Authorization": "Bearer …"}). Returns the parsed JSON under "data", or '
        'an "error". Loopback and private hosts are blocked.',
    }


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    url = str(options.get("url") or "").strip()
    if not url:
        return _discovery()

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
        data = fetch_json(url, headers=headers or None)
    except BlockedURLError as err:
        return {"error": str(err)}
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"}
    return {"data": data, "url": url}
