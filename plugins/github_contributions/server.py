"""github_contributions, the year's contribution heatmap."""

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
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}


def _core():
    return current_app.config["PLUGIN_REGISTRY"].get("github_core").server_module


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    core = _core()
    if not core.get_token():
        return {
            "error": "Add a GitHub PAT in Plugins → GitHub Core. GraphQL requires auth.",
            "weeks": [],
        }
    user = (options.get("user") or "").strip() or core.get_username()
    if not user:
        return {"error": "Set a GitHub username, here or as github_core default.", "weeks": []}

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    # v2 cache key, bump when the payload shape changes so a stale
    # cached v1 file (without the streak/busiest fields) doesn't keep
    # serving until the TTL expires.
    cache = data_dir / f"contrib_v2_{user}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    # Drop any v1 file lying around from earlier versions.
    legacy = data_dir / f"contrib_{user}.json"
    if legacy.exists():
        with contextlib.suppress(OSError):
            legacy.unlink()

    try:
        payload = core.request_graphql(QUERY, {"user": user})
    except Exception as err:
        return {"error": core.coerce_error(err), "weeks": []}

    if payload.get("errors"):
        return {"error": payload["errors"][0].get("message", "GraphQL error"), "weeks": []}
    cal = (
        ((payload.get("data") or {}).get("user") or {}).get("contributionsCollection") or {}
    ).get("contributionCalendar")
    if not cal:
        return {"error": f"No contribution data for @{user}.", "weeks": []}

    weeks = []
    for w in cal.get("weeks") or []:
        days = []
        for d in w.get("contributionDays") or []:
            days.append(
                {
                    "date": d.get("date"),
                    "count": d.get("contributionCount") or 0,
                    "level": LEVEL_TO_INT.get(d.get("contributionLevel"), 0),
                }
            )
        weeks.append(days)

    # Derived stats, current streak (consecutive days with count > 0,
    # ending today), longest streak in the year, busiest day, this week
    # / month totals. All from the flat sorted day list.
    flat = sorted(
        (d for w in weeks for d in w),
        key=lambda d: d.get("date") or "",
    )
    longest = 0
    run = 0
    busiest = {"date": "", "count": 0}
    for d in flat:
        c = d.get("count") or 0
        if c > 0:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
        if c > busiest["count"]:
            busiest = {"date": d.get("date") or "", "count": c}

    # Current streak, walk backward from today over days with > 0,
    # tolerate today being zero (still-early-in-day) by skipping it
    # when counting; the streak only breaks on a confirmed past-day zero.
    current_streak = 0
    for d in reversed(flat):
        if (d.get("count") or 0) > 0:
            current_streak += 1
        else:
            # Allow today itself to be zero without breaking the streak;
            # any earlier zero breaks it.
            if d is flat[-1]:
                continue
            break

    this_week = sum((d.get("count") or 0) for d in flat[-7:])
    this_month = sum((d.get("count") or 0) for d in flat[-30:])

    result = {
        "user": user,
        "total": cal.get("totalContributions") or 0,
        "weeks": weeks,
        "current_streak": current_streak,
        "longest_streak": longest,
        "busiest_date": busiest["date"],
        "busiest_count": busiest["count"],
        "this_week": this_week,
        "this_month": this_month,
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result
