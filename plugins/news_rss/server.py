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
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from flask import current_app

from app import net_guard
from app.net_guard import BlockedURLError
from app.plugin_http import decode_content_encoding

logger = logging.getLogger(__name__)

CACHE_TTL_S = 600
HTTP_TIMEOUT_S = 15
# Full-text and podcast feeds run to several MB; past this it isn't a feed.
FEED_MAX_BYTES = 16 * 1024 * 1024
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
    # Ask for no compression. urllib doesn't transparently decompress, and a
    # server that gzips anyway hands the XML parser a binary blob, which it
    # reports as "not well-formed (invalid token): line 1, column 0" (#212).
    # _fetch_via_urllib decodes defensively for servers that ignore this.
    "Accept-Encoding": "identity",
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


class FeedNotXML(Exception):
    """The response arrived but isn't a feed.

    Worth its own type because the raw ``ParseError`` ("not well-formed
    (invalid token): line 1, column 0") is what the operator sees in the
    cell, and it describes the symptom rather than the cause. A feed that
    intermittently fails this way is almost always answering with a block
    page or an error page, which is actionable; a parser complaint isn't.
    """


_HTML_PAGE_MSG = (
    "The feed returned a web page instead of XML, which usually means the site "
    "served a block or error page for this request."
)


def _parse_feed(body: bytes, content_type: str = "") -> ET.Element:
    looks_html = body[:400].lstrip().lower().startswith((b"<!doctype html", b"<html"))
    try:
        root = ET.fromstring(body)
    except ET.ParseError as err:
        if not body.strip():
            raise FeedNotXML("The feed returned an empty response.") from err
        if looks_html or "text/html" in content_type.lower():
            raise FeedNotXML(_HTML_PAGE_MSG) from err
        raise FeedNotXML(f"The feed isn't valid XML ({err}).") from err
    # A block page can be perfectly well-formed and still not be a feed, so
    # parsing cleanly isn't proof; the root element is.
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag in {"html", "head", "body"}:
        raise FeedNotXML(_HTML_PAGE_MSG)
    return root


def _fetch_via_urllib(url: str) -> ET.Element:
    # A feed URL is chosen by the user (or an agent), so it goes through the
    # SSRF guard like the REST widget's: http(s) only, public hosts only, each
    # redirect hop re-checked, a pinned connection and a size cap. A bare
    # urlopen would fetch file://, the server's own loopback API, a LAN
    # service or cloud metadata.
    body, headers = net_guard.fetch_response(
        url, headers=URLLIB_HEADERS, timeout=HTTP_TIMEOUT_S, max_bytes=FEED_MAX_BYTES
    )
    # Some CDNs compress regardless of what we asked for. Decoding here is
    # what separates "this feed is compressed" from "this feed is broken":
    # the parser can't tell the difference and blames the feed (#212).
    body = decode_content_encoding(body, str(headers.get("Content-Encoding", "")))
    return _parse_feed(body, str(headers.get("Content-Type", "")))


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
            # Chromium follows redirects on its own; public-only makes the
            # pool check each hop the way the urllib path does.
            allow_local=False,
        )
    )
    # Same treatment as the urllib path: Chromium clears more bot protection
    # but can still land on a challenge page, and the raw parser error would
    # be just as opaque.
    return _parse_feed(body.encode("utf-8") if isinstance(body, str) else body)


def _blocked_msg(err: BlockedURLError) -> str:
    return (
        f"This feed address isn't allowed ({err}). Feeds are fetched from public "
        "http(s) addresses only, not this server or the local network."
    )


def _fetch_feed(url: str, *, allow_pool: bool) -> ET.Element | str:
    """urllib first (cheap, most feeds are open), BrowserPool second.

    The pool fetch goes out through Chromium's network stack, whose TLS
    fingerprint is a real browser's - the thing Akamai/Cloudflare-class
    protection actually gates on (#178, lesoir.be). ``allow_pool=False``
    means we're inside a push render: the pool worker is busy with the
    screenshot that's hydrating this very widget, so submitting a fetch
    would deadlock. There urllib is the only option, and on failure the
    composer's last-good fallback serves the prior payload."""
    # Refused before either fetcher runs, so a blocked URL never reaches the
    # pool as "urllib failed, try Chromium".
    try:
        net_guard.assert_public_url(url)
    except BlockedURLError as err:
        return _blocked_msg(err)

    try:
        return _fetch_via_urllib(url)
    except BlockedURLError as err:
        # A redirect to a private host. Chromium would be refused the same
        # hop, so don't spend a pool fetch finding that out.
        return _blocked_msg(err)
    except FeedNotXML as err:
        # Already phrased for the operator; don't bury it behind a type name.
        urllib_error = str(err)
        logger.debug("news_rss: urllib fetch returned a non-feed body (%s)", urllib_error)
    except Exception as err:
        urllib_error = f"{type(err).__name__}: {err}"
        logger.debug("news_rss: urllib fetch failed (%s)", urllib_error)
    if allow_pool:
        try:
            pooled = _fetch_via_pool(url)
            if pooled is not None:
                return pooled
        except BlockedURLError as err:
            return _blocked_msg(err)
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
