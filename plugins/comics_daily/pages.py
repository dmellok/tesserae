"""Scrapers for the three comic sources, as pure functions over page text.

Nothing here touches the network, so each parser is pinned by a saved page
in the tests and a markup change upstream shows up as one failing fixture.
The stdlib ``HTMLParser`` is enough for the handful of tags read here and
copes with the Dilbert viewer's minified markup (no space between quoted
attributes, unquoted ``src``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

FARSIDE_MAX_PANELS = 4

_GOCOMICS_DATE_RE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/?$")
_DILBERT_ALT_RE = re.compile(r"^Comic for (\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True)
class GoComicsPage:
    image_url: str
    date: str | None  # the strip's own date, YYYY-MM-DD
    label: str | None  # "Garfield", from "Garfield by Jim Davis for ..."


@dataclass(frozen=True)
class FarSidePanel:
    image_url: str
    caption: str


@dataclass(frozen=True)
class DilbertStrip:
    image_url: str
    date: str


class _HeadParser(HTMLParser):
    """Collects ``<meta property|name>`` contents and the canonical link."""

    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.canonical = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = {k: v or "" for k, v in attrs}
        if tag == "meta":
            key = found.get("property") or found.get("name")
            if key and key not in self.meta:
                self.meta[key] = found.get("content", "").strip()
        elif tag == "link" and "canonical" in found.get("rel", "").split():
            self.canonical = self.canonical or found.get("href", "").strip()


def _strip_label(title: str) -> str | None:
    """The strip's name from its page title: "Garfield by Jim Davis for ..." -> "Garfield"."""
    head = title.split("|", 1)[0].strip()
    if " by " in head:
        head = head.split(" by ", 1)[0].strip()
    return head or None


def parse_gocomics(page: str, slug: str) -> GoComicsPage | None:
    """The strip on a GoComics day page, or None when the page has none.

    A date that isn't published yet answers 200 with the newest strip and a
    canonical link to that strip's real date, which is why the date comes
    from the canonical link (or ``og:url``) rather than the address asked
    for. An unknown name answers a bare page with no Open Graph tags.
    """
    parser = _HeadParser()
    parser.feed(page)
    image = parser.meta.get("og:image", "")
    page_url = parser.canonical or parser.meta.get("og:url", "")
    if not image.startswith("https://") or f"/{slug}/" not in page_url:
        return None
    match = _GOCOMICS_DATE_RE.search(page_url)
    date = "-".join(match.groups()) if match else None
    return GoComicsPage(
        image_url=image, date=date, label=_strip_label(parser.meta.get("og:title", ""))
    )


class _FarSideParser(HTMLParser):
    """Walks the Daily Dose cards: ``div.tfs-comic`` holding an
    ``div.tfs-comic__image > img[data-src]`` and a ``figcaption``.

    Only ``div`` and ``figcaption`` open and close scopes here; every other
    tag is ignored, so void elements can't unbalance the depth count.
    """

    def __init__(self) -> None:
        super().__init__()
        self.panels: list[FarSidePanel] = []
        self._depth = 0
        self._card_depth: int | None = None
        self._image_depth: int | None = None
        self._image = ""
        self._caption: list[str] | None = None
        self._caption_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = {k: v or "" for k, v in attrs}
        classes = found.get("class", "").split()
        if tag == "div":
            self._depth += 1
            if self._card_depth is None and "tfs-comic" in classes:
                self._card_depth = self._depth
                self._image, self._caption_text = "", ""
            elif self._card_depth is not None and "tfs-comic__image" in classes:
                self._image_depth = self._depth
        elif tag == "img" and self._image_depth is not None and not self._image:
            self._image = found.get("data-src", "").strip()
        elif tag == "figcaption" and self._card_depth is not None:
            self._caption = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "figcaption" and self._caption is not None:
            self._caption_text = " ".join("".join(self._caption).split())
            self._caption = None
        elif tag == "div":
            if self._image_depth == self._depth:
                self._image_depth = None
            if self._card_depth == self._depth:
                if self._image.startswith("https://"):
                    self.panels.append(FarSidePanel(self._image, self._caption_text))
                self._card_depth = None
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._caption is not None:
            self._caption.append(data)


def parse_farside(page: str, limit: int = FARSIDE_MAX_PANELS) -> list[FarSidePanel]:
    """The day's panels from thefarside.com, at most ``limit`` of them.

    The page also lazy-loads promo pictures and the logo through
    ``data-src``; only images inside a comic card count.
    """
    parser = _FarSideParser()
    parser.feed(page)
    return parser.panels[: max(0, limit)]


class _DilbertParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.strip: DilbertStrip | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img" or self.strip is not None:
            return
        found = {k: v or "" for k, v in attrs}
        match = _DILBERT_ALT_RE.match(found.get("alt", "").strip())
        src = found.get("src", "").strip()
        if match and src.startswith("https://"):
            self.strip = DilbertStrip(image_url=src, date=match.group(1))


def parse_dilbert(page: str) -> DilbertStrip | None:
    """The strip on a dilbert-viewer day page (``<img alt="Comic for ...">``)."""
    parser = _DilbertParser()
    parser.feed(page)
    return parser.strip
