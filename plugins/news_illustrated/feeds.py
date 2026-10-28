"""The story behind each illustration: a feed's newest item and its article text.

Feeds are RSS 2.0, RSS 1.0 (RDF) or Atom 1.0, parsed with defusedxml since
the XML comes from anywhere. A feed that is not XML, or XML of another kind,
never raises: it is no story, with the reason in the run log. The article page is read best-effort with a short timeout: a
paywall or a bot check answers with a challenge page instead of the story,
so those (and every failure) fall back to the feed's own description.
"""

from __future__ import annotations

import html
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from . import net, text

FEED_TIMEOUT_S = 15
# hnrss in particular fails intermittently: one retry after a short pause.
FEED_RETRY_PAUSE_S = 5.0
FEED_MAX_BYTES = 5 * 1024 * 1024
# Sites that do not answer quickly are usually paywalled or blocking bots.
ARTICLE_TIMEOUT_S = 8
ARTICLE_MAX_BYTES = 4 * 1024 * 1024
# Enough text for a grounded summary; more only costs tokens.
ARTICLE_TEXT_BYTES = 2000
# A challenge page usually announces itself in its title. "Enable JavaScript"
# only counts on a short page: a real article may say it about an embedded video.
_BLOCKED_TITLE = re.compile(
    r"security verification|just a moment|access denied|attention required|"
    r"are you a robot|captcha",
    re.IGNORECASE,
)
_BLOCKED_BODY = re.compile(
    r"enable javascript|enable cookies|verify you are (a )?human", re.IGNORECASE
)
BLOCKED_BODY_MAX_CHARS = 1500
_SKIPPED_TAGS = frozenset(
    {"script", "style", "noscript", "nav", "header", "footer", "aside", "iframe", "form", "svg"}
)
_ATOM = "{http://www.w3.org/2005/Atom}"
_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF"
_RSS1 = "{http://purl.org/rss/1.0/}"
_XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
_FEED_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"


@dataclass(frozen=True)
class Item:
    """A feed's newest story."""

    title: str
    link: str
    description: str


@dataclass(frozen=True)
class Article:
    """The text to summarise and where it came from (for the run log)."""

    text: str
    origin: str


def newest_item(
    url: str, log: Callable[[str], None], sleep: Callable[[float], None] = time.sleep
) -> Item | None:
    """The first item of the feed at ``url``, trying twice. None when the feed
    cannot be read or holds no item."""
    for attempt in (1, 2):
        reply = net.request(
            url,
            timeout=FEED_TIMEOUT_S,
            headers={"User-Agent": net.BROWSER_USER_AGENT, "Accept": _FEED_ACCEPT},
            max_bytes=FEED_MAX_BYTES,
        )
        if not reply.ok:
            log(f"Feed attempt {attempt} failed: {reply.reason()}")
        else:
            try:
                item = parse_first_item(reply.body, base_url=url)
            except Exception as err:  # a feed must never cost the run
                item = None
                log(f"Feed attempt {attempt}: could not read it ({type(err).__name__})")
            else:
                if item is None:
                    log(f"Feed attempt {attempt}: {why_no_item(reply.body)}")
            if item is not None:
                return item
        if attempt == 1:
            sleep(FEED_RETRY_PAUSE_S)
    return None


def parse_first_item(body: bytes, base_url: str = "") -> Item | None:
    """The first ``<item>`` of an RSS 2.0 feed or ``<entry>`` of an Atom feed.
    A relative link is resolved against ``xml:base`` and the feed's own URL."""
    try:
        root = fromstring(body)
    except (ParseError, DefusedXmlException, ValueError, LookupError):
        # LookupError: the XML declares an encoding Python does not know.
        return None
    base = urljoin(base_url, root.attrib.get(_XML_BASE, ""))
    if root.tag == "rss":
        node = root.find("channel/item")
        if node is None:
            return None
        title = _child_text(node, "title")
        link = urljoin(base, _child_text(node, "link").strip())
        description = _child_text(node, "description")
    elif root.tag == _RDF:
        node = root.find(f"{_RSS1}item")
        if node is None:
            return None
        title = _child_text(node, f"{_RSS1}title")
        link = urljoin(base, _child_text(node, f"{_RSS1}link").strip())
        description = _child_text(node, f"{_RSS1}description")
    elif root.tag == f"{_ATOM}feed":
        node = root.find(f"{_ATOM}entry")
        if node is None:
            return None
        title = _child_text(node, f"{_ATOM}title")
        link = _atom_link(node, urljoin(base, node.attrib.get(_XML_BASE, "")))
        summary = node.find(f"{_ATOM}summary")
        if summary is None:
            summary = node.find(f"{_ATOM}content")
        description = "".join(summary.itertext()) if summary is not None else ""
    else:
        return None
    # Titles are plain text; only undo a feed that escaped them twice. Stripping
    # tags here would eat titles like "A <canvas> renderer".
    title = text.normalise(text.squash(html.unescape(title)))
    if not title:
        return None
    return Item(title=title, link=link.strip(), description=text.clean_description(description))


def why_no_item(body: bytes) -> str:
    """Why ``body`` gave no story, in words for the run log."""
    if not body.strip():
        return "the feed address answered with nothing"
    try:
        root = fromstring(body)
    except DefusedXmlException:
        return "the feed uses XML features that are refused for safety"
    except (ParseError, ValueError, LookupError) as err:
        where = getattr(err, "position", None)
        at = f" (line {where[0]}, column {where[1]})" if isinstance(where, tuple) else ""
        head = body[:200].lstrip().lower()
        if head.startswith((b"<!doctype html", b"<html")):
            return "the address is a web page, not a feed"
        return f"the feed is not valid XML{at}"
    tag = root.tag.rpartition("}")[2] if isinstance(root.tag, str) else "?"
    if root.tag in ("rss", _RDF, f"{_ATOM}feed"):
        return "the feed has no stories in it"
    if tag.lower() == "html":
        return "the address is a web page, not a feed"
    return f"not an RSS or Atom feed (it starts with <{tag[:30]}>)"


def article(item: Item, log: Callable[[str], None]) -> Article:
    """The article's own text when the page gives it up, else the description."""
    fallback = Article(item.description, "feed description")
    if not item.link.startswith(("http://", "https://")):
        return fallback
    reply = net.request(
        item.link,
        timeout=ARTICLE_TIMEOUT_S,
        headers={
            "User-Agent": net.BROWSER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            "Accept-Language": "en-US,en;q=0.9",
        },
        max_bytes=ARTICLE_MAX_BYTES,
    )
    if not reply.ok:
        log(f"Article fetch failed ({reply.reason()}), using the feed description")
        return fallback
    if "html" not in reply.content_type.lower():
        return fallback
    title, body = page_text(reply.text())
    if is_blocked(title, body):
        log("Article page is a paywall or bot check, using the feed description")
        return fallback
    if len(body) > len(item.description):
        return Article(text.truncate_bytes(body, ARTICLE_TEXT_BYTES), "article page")
    return fallback


def is_blocked(title: str, body: str) -> bool:
    """True for a challenge page served in place of the article."""
    if _BLOCKED_TITLE.search(title):
        return True
    return len(body) < BLOCKED_BODY_MAX_CHARS and bool(_BLOCKED_BODY.search(body))


def page_text(markup: str) -> tuple[str, str]:
    """(page title, readable body text) of an HTML page, without scripts,
    navigation, headers, footers and forms."""
    reader = _PageReader()
    try:
        reader.feed(markup)
        reader.close()
    except (AssertionError, ValueError):
        pass  # what was read before the parser gave up is still good
    return text.squash(" ".join(reader.title)), text.squash(" ".join(reader.body))


class _PageReader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.body: list[str] = []
        self._skip = 0
        self._in_title = False
        self._in_head = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "head":
            self._in_head = True
        elif tag == "body":
            self._in_head = False

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag == "head":
            self._in_head = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title.append(data)
        elif not self._skip and not self._in_head:
            self.body.append(data)


def _child_text(node: Element, tag: str) -> str:
    child = node.find(tag)
    return "".join(child.itertext()) if child is not None else ""


def _atom_link(entry: Element, base: str) -> str:
    """The entry's alternate link (Atom allows several, ``rel`` defaults to it),
    resolved against the ``xml:base`` in force."""
    for link in entry.findall(f"{_ATOM}link"):
        if link.attrib.get("rel", "alternate") == "alternate" and link.attrib.get("href"):
            here = urljoin(base, link.attrib.get(_XML_BASE, ""))
            return urljoin(here, str(link.attrib["href"]).strip())
    return ""
