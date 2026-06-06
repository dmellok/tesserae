"""github_core, shared GitHub credentials + HTTP helpers.

No widget cell of its own; sibling github_* widgets reach in via the
registry and call ``request_json`` / ``request_graphql`` so all of
them share the same User-Agent, auth scheme, and rate-limit
behaviour.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from flask import current_app

USER_AGENT = "tesserae/0.1 (+github_core)"
ACCEPT = "application/vnd.github+json"
API_VERSION = "2022-11-28"


def _settings() -> dict[str, Any]:
    store = current_app.config["SETTINGS_STORE"]
    section = store.get_section("plugins") or {}
    return section.get("github_core") or {}


def get_token() -> str:
    """Returns the configured PAT or "" if unset. Keyed as
    ``token_secret`` on disk per the settings_store secret convention."""
    s = _settings()
    return (s.get("token_secret") or s.get("token") or "").strip()


def get_username() -> str:
    return (_settings().get("username") or "").strip()


def headers(*, accept: str = ACCEPT) -> dict[str, str]:
    h: dict[str, str] = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "X-GitHub-Api-Version": API_VERSION,
    }
    token = get_token()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


class GithubAcceptedError(Exception):
    """GitHub returned 202 Accepted, endpoint is async and the stats
    are still being computed (typical for ``/stats/commit_activity``,
    ``/stats/contributors``, etc.). Widgets should treat this as a
    "no data yet, try again" signal and avoid caching the empty
    result so the next render picks up the computed data."""


def request_json(url: str, *, timeout: int = 12) -> Any:
    """GET a JSON endpoint, return the parsed body. Raises on HTTP
    errors so each widget can decide how to surface them.

    Special case: GitHub's stats endpoints return 202 with an empty
    body the first time you hit them (the answer is being computed
    async). We raise ``GithubAcceptedError`` for those so the caller
    can keep its widget state empty WITHOUT caching the empty result -
    the next render will get the real data."""
    req = urllib.request.Request(url, headers=headers(), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status == 202:
            raise GithubAcceptedError(f"GitHub still computing stats for {url}")
        body = resp.read().decode("utf-8")
        if not body.strip():
            # Some stats endpoints return 200 with an empty body when
            # there's genuinely nothing to report, treat like a normal
            # empty list rather than blowing up on JSONDecodeError.
            return []
        return json.loads(body)


def request_graphql(query: str, variables: dict[str, Any], *, timeout: int = 12) -> Any:
    """POST a GraphQL query to api.github.com/graphql. Always requires
    the PAT, GitHub's GraphQL API doesn't accept unauthenticated
    requests."""
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    h = headers()
    h["Content-Type"] = "application/json"
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers=h,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def coerce_error(err: Exception) -> str:
    """Friendly one-line error for widgets to surface."""
    if isinstance(err, urllib.error.HTTPError):
        try:
            payload = json.loads(err.read().decode("utf-8", errors="replace"))
            msg = payload.get("message") or err.reason
        except Exception:
            msg = err.reason
        return f"GitHub HTTP {err.code}: {msg}"
    return f"{type(err).__name__}: {err}"
