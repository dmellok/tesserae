"""news_reddit — top posts from a subreddit via the public .json endpoint."""

from __future__ import annotations

import contextlib
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 600
HTTP_TIMEOUT_S = 12
USER_AGENT = "tesserae/0.1 (+news_reddit)"
SUB_RE = re.compile(r"^[A-Za-z0-9_]{1,21}$")


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    raw_sub = (options.get("subreddit") or "").strip()
    # Tolerate "r/foo", "/r/foo", "/r/foo/" — strip leading paths and
    # trailing slashes before validating the bare name.
    sub = raw_sub.strip("/")
    if sub.startswith("r/"):
        sub = sub[2:]
    if not SUB_RE.match(sub):
        return {"error": "Set a valid subreddit name.", "posts": []}
    sort = options.get("sort", "top")
    if sort not in ("top", "hot", "new"):
        sort = "top"
    window = options.get("window", "day")
    max_items = max(1, int(options.get("max_items") or 8))

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"r_{sub}_{sort}_{window}_{max_items}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit={max_items}"
    if sort == "top":
        url += f"&t={window}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "posts": []}

    children = ((payload.get("data") or {}).get("children")) or []
    posts = []
    for c in children[:max_items]:
        d = c.get("data") or {}
        posts.append(
            {
                "title": d.get("title") or "",
                "url": d.get("url") or f"https://reddit.com{d.get('permalink', '')}",
                "permalink": f"https://reddit.com{d.get('permalink', '')}",
                "author": d.get("author") or "",
                "score": d.get("score") or 0,
                "comments": d.get("num_comments") or 0,
                "time": d.get("created_utc"),
                "is_self": bool(d.get("is_self")),
            }
        )
    result = {"subreddit": sub, "sort": sort, "window": window, "posts": posts}
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result))
    return result
