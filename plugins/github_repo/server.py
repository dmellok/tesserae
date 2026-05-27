"""github_repo — single repository at a glance."""

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


def fetch(options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]) -> dict[str, Any]:
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
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    try:
        info = core.request_json(f"https://api.github.com/repos/{repo}")
        try:
            releases = core.request_json(f"https://api.github.com/repos/{repo}/releases/latest")
        except Exception:
            releases = None
    except Exception as err:
        return {"error": core.coerce_error(err)}

    result = {
        "repo":         info.get("full_name") or repo,
        "description":  info.get("description") or "",
        "stars":        info.get("stargazers_count") or 0,
        "forks":        info.get("forks_count") or 0,
        "issues":       info.get("open_issues_count") or 0,
        "watchers":     info.get("subscribers_count") or 0,
        "language":     info.get("language") or "",
        "pushed_at":    info.get("pushed_at"),
        "default_branch": info.get("default_branch") or "main",
        "is_archived":  bool(info.get("archived")),
        "latest_release": (releases or {}).get("tag_name") or "",
        "license":      ((info.get("license") or {}).get("spdx_id")) or "",
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result))
    return result
