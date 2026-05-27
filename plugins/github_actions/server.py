"""github_actions — recent CI runs across watched repos."""

from __future__ import annotations

import contextlib
import json
import re
import time
from pathlib import Path
from typing import Any

from flask import current_app

CACHE_TTL_S = 120
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def _core():
    return current_app.config["PLUGIN_REGISTRY"].get("github_core").server_module


def fetch(options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]) -> dict[str, Any]:
    del settings
    raw = options.get("repos") or ""
    repos = [r.strip() for r in raw.replace(",", "\n").splitlines() if REPO_RE.match(r.strip())]
    if not repos:
        return {"error": "Add one or more repos (owner/repo, one per line).", "runs": []}

    max_per = max(1, int(options.get("max_results") or 3))
    core = _core()
    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9]", "_", f"{','.join(repos)}_{max_per}")[:120]
    cache = data_dir / f"runs_{key}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    runs: list[dict[str, Any]] = []
    for repo in repos:
        try:
            payload = core.request_json(
                f"https://api.github.com/repos/{repo}/actions/runs?per_page={max_per}"
            )
        except Exception:
            continue
        for r in (payload.get("workflow_runs") or [])[:max_per]:
            runs.append({
                "repo":        repo,
                "name":        r.get("name") or "",
                "branch":      r.get("head_branch") or "",
                "event":       r.get("event") or "",
                "status":      r.get("status") or "",
                "conclusion":  r.get("conclusion") or "",
                "run_number":  r.get("run_number"),
                "updated_at":  r.get("updated_at"),
            })
    runs.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    result = {"runs": runs}
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result))
    return result
