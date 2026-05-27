"""news_rss — minimal RSS 2.0 + Atom 1.0 feed reader.

Pure stdlib XML parsing. Handles both formats by checking the root
element. Doesn't pull feedparser since the requirements are small
(title, link, published date, source name) and we want to stay
dep-light.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import urllib.request
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

CACHE_TTL_S = 600
HTTP_TIMEOUT_S = 15
USER_AGENT = "tesserae/0.1 (+news_rss)"

ATOM_NS = "{http://www.w3.org/2005/Atom}"


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
        items.append(
            {
                "title": (t.text or "").strip() if t is not None else "",
                "url": href,
                "published": when.isoformat() if when else "",
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
        items.append(
            {
                "title": (title_el.text or "").strip() if title_el is not None else "",
                "url": (link_el.text or "").strip() if link_el is not None else "",
                "published": when.isoformat() if when else "",
            }
        )
    return feed_title, items


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
    cache = data_dir / f"rss_{safe}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            blob = resp.read()
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "items": []}

    try:
        root = ET.fromstring(blob)
    except ET.ParseError as err:
        return {"error": f"Bad XML: {err}", "items": []}

    if root.tag == f"{ATOM_NS}feed":
        feed_title, items = _slim_atom(root, max_items)
    elif root.tag == "rss":
        feed_title, items = _slim_rss(root, max_items)
    else:
        return {"error": f"Unknown feed root: {root.tag}", "items": []}

    result = {"feed_title": feed_title, "items": items, "url": url}
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result))
    return result
