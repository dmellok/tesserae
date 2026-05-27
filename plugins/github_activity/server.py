"""github_activity — recent public events for a user."""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

from flask import current_app

CACHE_TTL_S = 300


def _core():
    return current_app.config["PLUGIN_REGISTRY"].get("github_core").server_module


def _cached(path: Path) -> Any | None:
    if not path.exists() or time.time() - path.stat().st_mtime >= CACHE_TTL_S:
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# GitHub event types → (icon, human label).
EVENT_KINDS = {
    "PushEvent":            ("git-commit",       "pushed"),
    "PullRequestEvent":     ("git-pull-request", "PR"),
    "IssuesEvent":          ("warning-circle",   "issue"),
    "IssueCommentEvent":    ("chat-circle",      "commented"),
    "PullRequestReviewEvent": ("eye",            "reviewed"),
    "PullRequestReviewCommentEvent": ("chats", "review"),
    "CreateEvent":          ("plus-circle",      "created"),
    "DeleteEvent":          ("minus-circle",     "deleted"),
    "ForkEvent":            ("git-fork",         "forked"),
    "WatchEvent":           ("star",             "starred"),
    "ReleaseEvent":         ("tag",              "released"),
    "PublicEvent":          ("globe",            "made public"),
}


def _slim_event(ev: dict[str, Any]) -> dict[str, Any]:
    kind, label = EVENT_KINDS.get(ev.get("type", ""), ("activity", ev.get("type", "")))
    payload = ev.get("payload") or {}
    repo = (ev.get("repo") or {}).get("name") or ""
    detail = ""
    if ev["type"] == "PushEvent":
        commits = payload.get("commits") or []
        n = payload.get("size") or len(commits)
        detail = f"{n} commit{'' if n == 1 else 's'}"
    elif ev["type"] == "PullRequestEvent":
        pr = payload.get("pull_request") or {}
        action = payload.get("action") or ""
        detail = f"{action} #{pr.get('number', '?')}"
    elif ev["type"] == "IssuesEvent":
        issue = payload.get("issue") or {}
        action = payload.get("action") or ""
        detail = f"{action} #{issue.get('number', '?')}"
    elif ev["type"] == "ReleaseEvent":
        rel = payload.get("release") or {}
        detail = rel.get("tag_name") or ""
    elif ev["type"] == "CreateEvent":
        detail = payload.get("ref_type") or ""
    return {
        "icon":    kind,
        "label":   label,
        "repo":    repo,
        "detail":  detail,
        "at":      ev.get("created_at"),
    }


def fetch(options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]) -> dict[str, Any]:
    del settings
    core = _core()
    user = (options.get("user") or "").strip() or core.get_username()
    if not user:
        return {"error": "Set a GitHub username — either here or as the default in Plugins → GitHub Core.", "events": []}

    max_events = int(options.get("max_events") or 10)
    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / f"events_{user}.json"
    cached = _cached(cache_path)
    if cached is not None:
        cached["events"] = cached.get("events", [])[:max_events]
        return cached

    try:
        raw = core.request_json(f"https://api.github.com/users/{user}/events/public?per_page=50")
    except Exception as err:
        return {"error": core.coerce_error(err), "events": []}

    events = [_slim_event(ev) for ev in (raw or [])][:max_events]
    result = {"user": user, "events": events, "count": len(events)}
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result))
    return result
