"""github_repo, single repository at a glance."""

from __future__ import annotations

import contextlib
import json
import re
import time
from pathlib import Path
from typing import Any

from flask import current_app

CACHE_TTL_S = 600
SAFE_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def _core():
    return current_app.config["PLUGIN_REGISTRY"].get("github_core").server_module


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    repo = (options.get("repo") or "").strip()
    if not SAFE_RE.match(repo):
        return {"error": "Set repo as 'owner/repo' (e.g. torvalds/linux)."}
    core = _core()
    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    safe = repo.replace("/", "__")
    cache = data_dir / f"repo_{safe}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            # Self-heal a previously cached "stats computing" result so
            # the user doesn't have to wait out the full 10-minute TTL
            # once GitHub's done. An active repo with zero commit_weeks
            # is unrealistic, so refetch in that case.
            if cached.get("commit_weeks"):
                return cached
        except (json.JSONDecodeError, OSError):
            pass
    # Tracks whether commit_activity came back from GitHub's async stats
    # endpoint as a "still computing" 202. If so, we skip writing the
    # cache so the next render picks up the real data instead of
    # serving "No commit activity" for 10 minutes.
    activity_pending = False
    try:
        info = core.request_json(f"https://api.github.com/repos/{repo}")
        try:
            releases = core.request_json(f"https://api.github.com/repos/{repo}/releases/latest")
        except Exception:
            releases = None
        try:
            langs = core.request_json(f"https://api.github.com/repos/{repo}/languages")
        except Exception:
            langs = {}
        try:
            # 52-week commit activity, list of {"week", "total", "days"}.
            # First request often returns 202 (computing); the next hit
            # has the data.
            activity = core.request_json(
                f"https://api.github.com/repos/{repo}/stats/commit_activity"
            )
        except core.GithubAcceptedError:
            activity = []
            activity_pending = True
        except Exception:
            activity = []
        try:
            # Top contributors, login + avatar + contribution count.
            contributors = core.request_json(
                f"https://api.github.com/repos/{repo}/contributors?per_page=6"
            )
        except Exception:
            contributors = []
    except Exception as err:
        return {"error": core.coerce_error(err)}

    # Language breakdown, top-5 by byte count + an "Other" rollup so
    # the bar sums to 100% without the long-tail dominating.
    lang_items: list[dict[str, Any]] = []
    if isinstance(langs, dict) and langs:
        total = sum(int(v) for v in langs.values()) or 1
        sorted_langs = sorted(langs.items(), key=lambda kv: kv[1], reverse=True)
        for name, b in sorted_langs[:5]:
            lang_items.append({"name": name, "pct": round(int(b) / total * 100, 1)})
        tail = sum(v for _, v in sorted_langs[5:])
        if tail:
            lang_items.append({"name": "Other", "pct": round(tail / total * 100, 1)})

    commit_weeks: list[int] = []
    if isinstance(activity, list):
        commit_weeks = [int(w.get("total") or 0) for w in activity if isinstance(w, dict)]

    contrib_items: list[dict[str, Any]] = []
    if isinstance(contributors, list):
        for c in contributors[:6]:
            if not isinstance(c, dict):
                continue
            contrib_items.append(
                {
                    "login": c.get("login") or "",
                    "avatar_url": c.get("avatar_url") or "",
                    "contributions": int(c.get("contributions") or 0),
                }
            )

    result = {
        "repo": info.get("full_name") or repo,
        "description": info.get("description") or "",
        "stars": info.get("stargazers_count") or 0,
        "forks": info.get("forks_count") or 0,
        "issues": info.get("open_issues_count") or 0,
        "watchers": info.get("subscribers_count") or 0,
        "language": info.get("language") or "",
        "pushed_at": info.get("pushed_at"),
        "default_branch": info.get("default_branch") or "main",
        "is_archived": bool(info.get("archived")),
        "latest_release": (releases or {}).get("tag_name") or "",
        "license": ((info.get("license") or {}).get("spdx_id")) or "",
        "languages": lang_items,
        "commit_weeks": commit_weeks,
        "commits_year": sum(commit_weeks),
        "busiest_week": max(commit_weeks) if commit_weeks else 0,
        "contributors": contrib_items,
    }
    # Don't cache while the commit_activity stats are still being
    # computed by GitHub, that'd lock in an empty bars chart for
    # 10 minutes when the answer is on its way.
    if not activity_pending:
        with contextlib.suppress(OSError):
            cache.write_text(json.dumps(result), encoding="utf-8")
    return result
