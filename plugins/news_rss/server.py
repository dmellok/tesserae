"""news_rss, minimal RSS 2.0 + Atom 1.0 feed reader.

Pure stdlib XML parsing. Handles both formats by checking the root
element. Doesn't pull feedparser since the requirements are small
(title, link, published date, source name) and we want to stay
dep-light.

Fetching mirrors the news_reddit widget (#178): plain ``urllib`` first
(cheap, works for most feeds), then the warm BrowserPool's Chromium
network stack when the feed sits behind Akamai/Cloudflare-class bot
protection. Those services gate on the TLS fingerprint, which a Python
request can't fake regardless of User-Agent; Chromium's is genuine.
"""

from __future__ import annotations

import contextlib
import html
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
HTTP_TIMEOUT_S = 15
# Browser-shaped UA: news sites behind bot protection 403 a transparently
# bot-shaped UA outright, before TLS fingerprinting even enters into it.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)
ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"
# Browser-like headers for the urllib path. TLS/JA3 is fingerprinted
# first (unfakeable from Python), but a fully browser-shaped request
# still clears the milder tiers of protection.
URLLIB_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": ACCEPT,
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

ATOM_NS = "{http://www.w3.org/2005/Atom}"

# Cap the excerpt server-side. A full-text feed puts whole articles in
# ``<description>``, and the panel only ever shows a couple of lines, so
# shipping the rest just bloats every render's payload. Generous enough that
# the client's line clamp, not this, decides where the text visually ends.
EXCERPT_MAX_CHARS = 400
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Feeds whose description is a link dump rather than prose (Hacker News ships
# "Article URL: … Comments URL: …"). Stripped of markup these read as two bare
# URLs, which is worse on a panel than showing nothing.
_URL_RE = re.compile(r"https?://\S+")
_SPACED_PUNCT_RE = re.compile(r"\s+([,.;:!?…»])")


def _clean_excerpt(raw: str) -> str:
    """Plain-text preview from a feed's description/summary markup.

    Feeds put anything in there: plain text, escaped HTML, CDATA-wrapped
    markup, tracking pixels. Strip tags, decode entities, collapse
    whitespace, and drop the result if what's left is mostly URL, so a
    link-dump feed shows no excerpt rather than a wall of href."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    # A second pass: entity-decoding can reveal markup that was escaped
    # rather than CDATA-wrapped (&lt;p&gt;...).
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    # Tags become spaces, so an inline link before a full stop leaves
    # "at Ars ." Pull punctuation back onto the word.
    text = _SPACED_PUNCT_RE.sub(r"\1", text)
    if not text:
        return ""
    without_urls = _URL_RE.sub("", text).strip()
    if len(without_urls) < len(text) / 2:
        return ""
    if len(text) > EXCERPT_MAX_CHARS:
        text = text[:EXCERPT_MAX_CHARS].rstrip() + "…"
    return text


def _parse_when(s: str) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    # Atom: ISO 8601
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        pass
    # RSS: RFC 822
    try:
        dt = parsedate_to_datetime(s)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


def _slim_atom(feed: ET.Element, max_items: int) -> tuple[str, list[dict[str, Any]]]:
    title_el = feed.find(f"{ATOM_NS}title")
    feed_title = (title_el.text or "").strip() if title_el is not None else ""
    items: list[dict[str, Any]] = []
    for entry in feed.findall(f"{ATOM_NS}entry")[:max_items]:
        t = entry.find(f"{ATOM_NS}title")
        link_el = entry.find(f"{ATOM_NS}link")
        published_el = entry.find(f"{ATOM_NS}published")
        if published_el is None:
            published_el = entry.find(f"{ATOM_NS}updated")
        href = ""
        if link_el is not None:
            href = link_el.attrib.get("href") or ""
        when = _parse_when(published_el.text if published_el is not None else "")
        summary_el = entry.find(f"{ATOM_NS}summary")
        if summary_el is None:
            summary_el = entry.find(f"{ATOM_NS}content")
        items.append(
            {
                "title": (t.text or "").strip() if t is not None else "",
                "url": href,
                "published": when.isoformat() if when else "",
                "excerpt": _clean_excerpt(
                    "".join(summary_el.itertext()) if summary_el is not None else ""
                ),
            }
        )
    return feed_title, items


def _slim_rss(rss: ET.Element, max_items: int) -> tuple[str, list[dict[str, Any]]]:
    channel = rss.find("channel")
    if channel is None:
        return "", []
    title_el = channel.find("title")
    feed_title = (title_el.text or "").strip() if title_el is not None else ""
    items: list[dict[str, Any]] = []
    for item in channel.findall("item")[:max_items]:
        title_el = item.find("title")
        link_el = item.find("link")
        date_el = item.find("pubDate")
        when = _parse_when(date_el.text if date_el is not None else "")
        desc_el = item.find("description")
        items.append(
            {
                "title": (title_el.text or "").strip() if title_el is not None else "",
                "url": (link_el.text or "").strip() if link_el is not None else "",
                "published": when.isoformat() if when else "",
                "excerpt": _clean_excerpt(
                    "".join(desc_el.itertext()) if desc_el is not None else ""
                ),
            }
        )
    return feed_title, items


def _fetch_via_urllib(url: str) -> ET.Element:
    req = urllib.request.Request(url, headers=URLLIB_HEADERS)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return ET.fromstring(resp.read())


def _fetch_via_pool(url: str) -> ET.Element | None:
    """Try the warm BrowserPool's ``fetch_text``. Returns the parsed feed
    on success, ``None`` when the pool isn't available (toggle off, tests,
    pool not yet wired). Raises if the pool exists but the fetch failed -
    the caller catches and reports the urllib error instead."""
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
            accept=ACCEPT,
        )
    )
    return ET.fromstring(body)


def _fetch_feed(url: str, *, allow_pool: bool) -> ET.Element | str:
    """urllib first (cheap, most feeds are open), BrowserPool second.

    The pool fetch goes out through Chromium's network stack, whose TLS
    fingerprint is a real browser's - the thing Akamai/Cloudflare-class
    protection actually gates on (#178, lesoir.be). ``allow_pool=False``
    means we're inside a push render: the pool worker is busy with the
    screenshot that's hydrating this very widget, so submitting a fetch
    would deadlock. There urllib is the only option, and on failure the
    composer's last-good fallback serves the prior payload."""
    try:
        return _fetch_via_urllib(url)
    except Exception as err:
        urllib_error = f"{type(err).__name__}: {err}"
        logger.debug("news_rss: urllib fetch failed (%s)", urllib_error)
    if allow_pool:
        try:
            pooled = _fetch_via_pool(url)
            if pooled is not None:
                return pooled
        except Exception as err:
            logger.debug("news_rss: pool fetch failed (%s)", err)
    return urllib_error


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    url = (options.get("url") or "").strip()
    if not url:
        return {"error": "Set a feed URL.", "items": []}
    max_items = max(1, int(options.get("max_items") or 8))

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9]", "_", url)[:60]
    # ``max_items`` is in the cache key so changing it in the editor
    # refetches at the new size instead of serving a stale slice from
    # the prior fetch's payload until the cache TTL expires.
    cache = data_dir / f"rss_{safe}_{max_items}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # ``ctx["preview"]`` is True for the editor / dev gallery and False
    # during a push render (where the pool worker is busy hydrating us).
    root = _fetch_feed(url, allow_pool=bool(ctx.get("preview")))
    if isinstance(root, str):
        return {"error": root, "items": []}

    if root.tag == f"{ATOM_NS}feed":
        feed_title, items = _slim_atom(root, max_items)
    elif root.tag == "rss":
        feed_title, items = _slim_rss(root, max_items)
    else:
        return {"error": f"Unknown feed root: {root.tag}", "items": []}

    result = {"feed_title": feed_title, "items": items, "url": url}
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result
