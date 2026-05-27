"""github_releases — latest releases across watched repos."""

from __future__ import annotations

import contextlib
import json
import re
import time
from pathlib import Path
from typing import Any

from flask import current_app

CACHE_TTL_S = 600
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def _core():
    return current_app.config["PLUGIN_REGISTRY"].get("github_core").server_module


def fetch(options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]) -> dict[str, Any]:
    del settings
    raw = options.get("repos") or ""
    repos = [r.strip() for r in raw.replace(",", "\n").splitlines() if REPO_RE.match(r.strip())]
    if not repos:
        return {"error": "Add one or more repos (owner/repo, one per line).", "releases": []}

    max_per = max(1, int(options.get("max_results") or 1))
    core = _core()
    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9]", "_", f"{','.join(repos)}_{max_per}")[:120]
    cache = data_dir / f"rel_{key}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    out = []
    for repo in repos:
        try:
            rels = core.request_json(f"https://api.github.com/repos/{repo}/releases?per_page={max_per}")
        except Exception:
            rels = []
        for r in (rels or [])[:max_per]:
            out.append({
                "repo":         repo,
                "tag":          r.get("tag_name") or "",
                "name":         r.get("name") or r.get("tag_name") or "",
                "published_at": r.get("published_at"),
                "prerelease":   bool(r.get("prerelease")),
                "draft":        bool(r.get("draft")),
            })
    out.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    result = {"releases": out}
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result))
    return result
