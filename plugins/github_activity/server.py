"""github_activity, recent public events for a user."""

from __future__ import annotations

import contextlib
import json
import time
from datetime import UTC
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
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# GitHub event types → (icon, human label).
EVENT_KINDS = {
    "PushEvent": ("git-commit", "pushed"),
    "PullRequestEvent": ("git-pull-request", "PR"),
    "IssuesEvent": ("warning-circle", "issue"),
    "IssueCommentEvent": ("chat-circle", "commented"),
    "PullRequestReviewEvent": ("eye", "reviewed"),
    "PullRequestReviewCommentEvent": ("chats", "review"),
    "CreateEvent": ("plus-circle", "created"),
    "DeleteEvent": ("minus-circle", "deleted"),
    "ForkEvent": ("git-fork", "forked"),
    "WatchEvent": ("star", "starred"),
    "ReleaseEvent": ("tag", "released"),
    "PublicEvent": ("globe", "made public"),
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
        "icon": kind,
        "label": label,
        "repo": repo,
        "detail": detail,
        "at": ev.get("created_at"),
    }


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    core = _core()
    user = (options.get("user") or "").strip() or core.get_username()
    if not user:
        return {
            "error": "Set a GitHub username, either here or as the default in Plugins → GitHub Core.",
            "events": [],
        }

    max_events = int(options.get("max_events") or 10)
    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / f"events_{user}.json"
    cached = _cached(cache_path)
    if cached is not None:
        cached["events"] = cached.get("events", [])[:max_events]
        return cached

    try:
        # Fetch the max GitHub serves on the public events endpoint
        # (100) so the derived stats span a useful window even though
        # the visible list is shorter.
        raw = core.request_json(f"https://api.github.com/users/{user}/events/public?per_page=100")
    except Exception as err:
        return {"error": core.coerce_error(err), "events": []}

    all_events = [_slim_event(ev) for ev in (raw or [])]

    # Derived stats from the full window:
    #  * 7-day daily activity bars (for a mini histogram)
    #  * per-type counts (commits / PRs / issues / other)
    #  * unique repos touched
    from datetime import datetime

    now = datetime.now(UTC)
    seven_days = [0] * 7
    # Per-type daily breakdown for the stacked histogram. Each entry
    # is {commits, prs, issues, releases, other} indexed by the same
    # 0-6 oldest-first ordering as seven_days.
    daily_typed: list[dict[str, int]] = [
        {"commits": 0, "prs": 0, "issues": 0, "releases": 0, "other": 0} for _ in range(7)
    ]
    type_counts = {
        "PushEvent": 0,
        "PullRequestEvent": 0,
        "IssuesEvent": 0,
        "ReleaseEvent": 0,
        "other": 0,
    }
    TYPE_BUCKET = {
        "PushEvent": "commits",
        "PullRequestEvent": "prs",
        "IssuesEvent": "issues",
        "ReleaseEvent": "releases",
    }
    repos_set: set[str] = set()
    # Track which dates the user was active on, for a streak count.
    active_dates: set[Any] = set()
    for ev_raw, ev in zip((raw or []), all_events, strict=False):
        repos_set.add(ev.get("repo") or "")
        kind = ev_raw.get("type") or ""
        if kind in type_counts:
            type_counts[kind] += 1
        else:
            type_counts["other"] += 1
        # Daily bucket, UTC days back from today.
        at = ev.get("at")
        if not at:
            continue
        try:
            dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError:
            continue
        active_dates.add(dt.date())
        delta_days = (now.date() - dt.date()).days
        if 0 <= delta_days < 7:
            seven_days[6 - delta_days] += 1  # oldest first → newest last
            bucket = TYPE_BUCKET.get(kind, "other")
            daily_typed[6 - delta_days][bucket] += 1

    # Current streak, count consecutive days back from today (or
    # yesterday, if today is empty) where the user was active. Public
    # events go back at most 30 days, so 30 is the streak ceiling.
    streak = 0
    cursor = now.date()
    if cursor not in active_dates:
        # Allow today to be empty, only break if yesterday is also empty.
        from datetime import timedelta

        cursor = cursor - timedelta(days=1)
    from datetime import timedelta as _td

    while cursor in active_dates:
        streak += 1
        cursor = cursor - _td(days=1)

    events = all_events[:max_events]
    result = {
        "user": user,
        "events": events,
        "count": len(events),
        "total_30": len(all_events),
        "daily": seven_days,
        "daily_typed": daily_typed,
        "streak": streak,
        "repos_count": len([r for r in repos_set if r]),
        "type_commits": type_counts.get("PushEvent", 0),
        "type_prs": type_counts.get("PullRequestEvent", 0),
        "type_issues": type_counts.get("IssuesEvent", 0),
        "type_releases": type_counts.get("ReleaseEvent", 0),
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result
