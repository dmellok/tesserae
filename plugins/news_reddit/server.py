"""news_reddit — top posts from a subreddit via Reddit's RSS/Atom feed.

Reddit firewall-blocks the public ``.json`` endpoint (HTTP 403 with an HTML
challenge page) for server-side reads regardless of User-Agent. The per-
subreddit RSS feed (``/r/<sub>/<sort>.rss``) is still served, so we parse
that instead. The trade-off: RSS carries title / author / permalink / time
but **not** score or comment counts — the client hides those when absent.

Fetch strategy: prefer the warm BrowserPool's ``fetch_text`` (Chromium's
TLS/JA3 fingerprint slips past the bot-shape filter that catches plain
``urllib`` requests), falling back to ``urllib`` when the pool isn't
running or the Playwright fetch raises. Each pool fetch uses a fresh
incognito context — no cookies carry between widgets, so Reddit can't
correlate this widget's fetches with anyone else's traffic going through
the same Chromium.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import time
import urllib.request
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from flask import current_app

logger = logging.getLogger(__name__)

CACHE_TTL_S = 600
HTTP_TIMEOUT_S = 12
# Reddit blocks bot-shaped UAs on its public feeds; a browser UA is served.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)
SUB_RE = re.compile(r"^[A-Za-z0-9_]{1,21}$")
ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _epoch(text: str | None) -> float | None:
    """Atom ISO-8601 (or RSS RFC-822) timestamp → UNIX epoch seconds."""
    if not text:
        return None
    s = text.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def _author(entry: ET.Element) -> str:
    name_el = entry.find(f"{ATOM_NS}author/{ATOM_NS}name")
    name = (name_el.text or "").strip() if name_el is not None else ""
    # Reddit writes "/u/username"; show the bare name.
    return name.removeprefix("/u/").removeprefix("u/")


def _fetch_via_pool(url: str) -> ET.Element | None:
    """Try the warm BrowserPool's ``fetch_text``. Returns the parsed feed
    on success, ``None`` when the pool isn't available (toggle off, tests,
    pool not yet wired). Raises if the pool exists but the fetch failed —
    the caller catches and falls back to urllib."""
    try:
        pool = current_app.config.get("BROWSER_POOL")
    except RuntimeError:
        # No app context (some test code paths). The widget always runs
        # under a request, so this only fires in unusual setups.
        return None
    if pool is None:
        return None
    from app.renderer import FetchRequest as _FetchRequest

    body = pool.fetch_text(
        _FetchRequest(
            url=url,
            timeout_ms=HTTP_TIMEOUT_S * 1000,
            user_agent=USER_AGENT,
            accept="application/atom+xml, application/xml",
        )
    )
    return ET.fromstring(body)


def _fetch_via_urllib(url: str) -> ET.Element:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml, application/xml"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return ET.fromstring(resp.read())


def _fetch_feed(url: str) -> ET.Element | str:
    """Try pool → urllib. Returns the parsed feed on success, or an error
    string with both attempts' details when both fail. We always log
    the pool failure (DEBUG) so a flaky upstream is visible in the logs
    without surfacing two errors to the user."""
    try:
        pooled = _fetch_via_pool(url)
        if pooled is not None:
            return pooled
    except Exception as err:
        logger.debug("news_reddit: pool fetch failed (%s), falling back to urllib", err)
    try:
        return _fetch_via_urllib(url)
    except Exception as err:
        return f"{type(err).__name__}: {err}"


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
            return json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    url = f"https://www.reddit.com/r/{sub}/{sort}.rss?limit={max_items}"
    if sort == "top":
        url += f"&t={window}"

    feed = _fetch_feed(url)
    if isinstance(feed, str):
        # _fetch_feed returns the error string when both paths failed.
        return {"error": feed, "posts": []}

    posts = []
    for entry in feed.findall(f"{ATOM_NS}entry")[:max_items]:
        title_el = entry.find(f"{ATOM_NS}title")
        link_el = entry.find(f"{ATOM_NS}link")
        published_el = entry.find(f"{ATOM_NS}published")
        if published_el is None:
            published_el = entry.find(f"{ATOM_NS}updated")
        href = link_el.attrib.get("href", "") if link_el is not None else ""
        posts.append(
            {
                "title": (title_el.text or "").strip() if title_el is not None else "",
                "url": href,
                "permalink": href,
                "author": _author(entry),
                # RSS carries neither score nor comment count nor self-post flag.
                "score": None,
                "comments": None,
                "time": _epoch(published_el.text if published_el is not None else None),
                "is_self": False,
            }
        )
    result = {"subreddit": sub, "sort": sort, "window": window, "posts": posts}
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result
