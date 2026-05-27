"""news_hacker_news — top HN stories via the free Firebase API."""

from __future__ import annotations

import contextlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 600
HTTP_TIMEOUT_S = 10
USER_AGENT = "tesserae/0.1 (+news_hacker_news)"
BASE = "https://hacker-news.firebaseio.com/v0"


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    feed = options.get("feed", "top")
    if feed not in ("top", "new", "best", "show", "ask"):
        feed = "top"
    max_items = max(1, int(options.get("max_items") or 10))

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"hn_{feed}_{max_items}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    try:
        ids = _get_json(f"{BASE}/{feed}stories.json")
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "stories": []}

    stories = []
    for sid in (ids or [])[:max_items]:
        try:
            item = _get_json(f"{BASE}/item/{sid}.json")
        except Exception:
            continue
        if not item:
            continue
        stories.append(
            {
                "id": item.get("id"),
                "title": item.get("title") or "",
                "url": item.get("url") or f"https://news.ycombinator.com/item?id={item.get('id')}",
                "by": item.get("by") or "",
                "score": item.get("score") or 0,
                "comments": item.get("descendants") or 0,
                "time": item.get("time"),
            }
        )

    result = {"feed": feed, "stories": stories}
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result))
    return result
