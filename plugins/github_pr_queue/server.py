"""github_pr_queue, your open PRs + PRs awaiting your review."""

from __future__ import annotations

import contextlib
import json
import time
import urllib.parse
from pathlib import Path
from typing import Any

from flask import current_app

CACHE_TTL_S = 300


def _core():
    return current_app.config["PLUGIN_REGISTRY"].get("github_core").server_module


def _slim(item: dict[str, Any]) -> dict[str, Any]:
    repo_url = item.get("repository_url") or ""
    repo = "/".join(repo_url.split("/")[-2:]) if repo_url else ""
    return {
        "title": item.get("title") or "",
        "number": item.get("number"),
        "repo": repo,
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "user": (item.get("user") or {}).get("login") or "",
        "comments": item.get("comments") or 0,
        "draft": bool(item.get("draft")),
    }


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    core = _core()
    if not core.get_token():
        return {
            "error": "Add a GitHub PAT in Plugins → GitHub Core. The PR-search API requires auth.",
            "yours": [],
            "review": [],
        }
    user = (options.get("user") or "").strip() or core.get_username()
    if not user:
        return {
            "error": "Set a GitHub username, here or as github_core default.",
            "yours": [],
            "review": [],
        }

    max_results = max(1, int(options.get("max_results") or 6))
    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"pr_{user}_{max_results}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    def q(query: str) -> list[dict[str, Any]]:
        url = "https://api.github.com/search/issues?" + urllib.parse.urlencode(
            {"q": query, "per_page": str(max_results)}
        )
        try:
            payload = core.request_json(url)
        except Exception:
            return []
        return [_slim(it) for it in (payload.get("items") or [])][:max_results]

    yours = q(f"is:pr is:open author:{user}")
    review = q(f"is:pr is:open review-requested:{user}")
    result = {"user": user, "yours": yours, "review": review}
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result
