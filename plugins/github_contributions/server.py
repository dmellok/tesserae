"""github_contributions — the year's contribution heatmap."""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any

from flask import current_app

CACHE_TTL_S = 1800

QUERY = """
query($user: String!) {
  user(login: $user) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
            contributionLevel
          }
        }
      }
    }
  }
}
""".strip()

LEVEL_TO_INT = {
    "NONE":              0,
    "FIRST_QUARTILE":    1,
    "SECOND_QUARTILE":   2,
    "THIRD_QUARTILE":    3,
    "FOURTH_QUARTILE":   4,
}


def _core():
    return current_app.config["PLUGIN_REGISTRY"].get("github_core").server_module


def fetch(options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]) -> dict[str, Any]:
    del settings
    core = _core()
    if not core.get_token():
        return {"error": "Add a GitHub PAT in Plugins → GitHub Core. GraphQL requires auth.", "weeks": []}
    user = (options.get("user") or "").strip() or core.get_username()
    if not user:
        return {"error": "Set a GitHub username — here or as github_core default.", "weeks": []}

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"contrib_{user}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    try:
        payload = core.request_graphql(QUERY, {"user": user})
    except Exception as err:
        return {"error": core.coerce_error(err), "weeks": []}

    if payload.get("errors"):
        return {"error": payload["errors"][0].get("message", "GraphQL error"), "weeks": []}
    cal = (((payload.get("data") or {}).get("user") or {}).get("contributionsCollection") or {}).get("contributionCalendar")
    if not cal:
        return {"error": f"No contribution data for @{user}.", "weeks": []}

    weeks = []
    for w in cal.get("weeks") or []:
        days = []
        for d in w.get("contributionDays") or []:
            days.append({
                "date":  d.get("date"),
                "count": d.get("contributionCount") or 0,
                "level": LEVEL_TO_INT.get(d.get("contributionLevel"), 0),
            })
        weeks.append(days)

    result = {
        "user":   user,
        "total":  cal.get("totalContributions") or 0,
        "weeks":  weeks,
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result))
    return result
