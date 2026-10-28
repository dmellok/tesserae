"""Text normalisation, feed parsing (broken and hostile feeds included) and the
article fetch with its paywall fallback."""

from __future__ import annotations

import pytest

from plugins.news_illustrated import feeds, net, text
from plugins.news_illustrated.tests.fakes import FakeNet, article_page, ok, rss

LONG = "The council voted on Tuesday to approve the new tram line. " * 60


def test_normalise_maps_odd_hyphens_and_spaces() -> None:
    raw = "co‑op deal − 5 %​ ― done⁠  now﻿ – —"
    assert text.normalise(raw) == "co-op deal - 5 % — done now – —"


def test_truncate_bytes_never_splits_a_character() -> None:
    assert text.truncate_bytes("abécd", 3) == "ab"
    assert text.truncate_bytes("abécd", 4) == "abé"
    assert text.truncate_bytes("short", 100) == "short"


def test_clean_description_drops_markup_and_hnrss_link_dump() -> None:
    raw = (
        "<p>Article URL: https://example.com/a</p><p>Comments URL: "
        "https://news.ycombinator.com/item?id=1</p><p>Points: 120</p><p># Comments: 33</p>"
        "<p>Real&amp;text</p><p>here</p>"
    )
    assert text.clean_description(raw) == "Real&text here"


def test_rss_first_item() -> None:
    item = feeds.parse_first_item(
        rss("Bank &amp;amp; rates ‑ up", "https://example.com/a", "<b>Rates</b> rose.").encode()
    )
    assert item == feeds.Item("Bank & rates - up", "https://example.com/a", "Rates rose.")


def test_rss_title_keeps_angle_brackets() -> None:
    item = feeds.parse_first_item(
        rss("A &lt;canvas&gt; renderer", "https://example.com/c").encode()
    )
    assert item is not None and item.title == "A <canvas> renderer"


def test_atom_first_entry_uses_the_alternate_link() -> None:
    atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Atom story</title>
        <link rel="self" href="https://example.com/self"/>
        <link href="https://example.com/story"/>
        <summary type="html">&lt;p&gt;Summary text&lt;/p&gt;</summary></entry>
      <entry><title>Older</title></entry></feed>"""
    assert feeds.parse_first_item(atom) == feeds.Item(
        "Atom story", "https://example.com/story", "Summary text"
    )


def test_not_a_feed_or_hostile_xml_gives_nothing() -> None:
    assert feeds.parse_first_item(b"<html><body>blocked</body></html>") is None
    assert feeds.parse_first_item(b"not xml at all") is None
    assert feeds.parse_first_item(b"<rss><channel></channel></rss>") is None
    bomb = b"""<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]>
      <rss><channel><item><title>&b;</title></item></channel></rss>"""
    assert feeds.parse_first_item(bomb) is None


def test_feed_is_retried_once(fake_net: FakeNet) -> None:
    feed = "https://hnrss.org/frontpage"
    fake_net.on(feed, net.Reply(503), ok(rss("Story", "https://e.com/s")))
    pauses: list[float] = []
    log: list[str] = []
    item = feeds.newest_item(feed, log.append, sleep=pauses.append)
    assert item is not None and item.title == "Story"
    assert len(fake_net.to(feed)) == 2
    assert pauses == [feeds.FEED_RETRY_PAUSE_S]
    assert "Feed attempt 1 failed: HTTP 503" in log


def test_feed_failing_twice_gives_nothing(fake_net: FakeNet) -> None:
    feed = "https://feeds.example.com/rss"
    fake_net.on(feed, ok("<html>blocked</html>"))
    log: list[str] = []
    assert feeds.newest_item(feed, log.append, sleep=lambda s: None) is None
    assert len(fake_net.to(feed)) == 2
    assert any("the address is a web page, not a feed" in line for line in log)


def _item(description: str = "The short feed description of the story.") -> feeds.Item:
    return feeds.Item("Tram line approved", "https://news.example.com/a", description)


def test_article_text_strips_scripts_and_navigation(fake_net: FakeNet) -> None:
    fake_net.on("https://news.example.com/a", ok(article_page(LONG)))
    got = feeds.article(_item(), [].append)
    assert got.origin == "article page"
    assert "tracking" not in got.text and "Home News" not in got.text
    assert "Copyright" not in got.text and "A story" not in got.text
    assert got.text.startswith("The council voted")
    assert len(got.text.encode("utf-8")) <= feeds.ARTICLE_TEXT_BYTES
    call = fake_net.calls[0]
    assert call.timeout == feeds.ARTICLE_TIMEOUT_S
    assert "Chrome" in call.headers["User-Agent"]


def test_article_cap_is_utf8_safe(fake_net: FakeNet) -> None:
    fake_net.on("https://news.example.com/a", ok(article_page("é" * 3000)))
    got = feeds.article(_item(), [].append)
    assert got.text == "é" * (feeds.ARTICLE_TEXT_BYTES // 2)


def test_paywall_title_falls_back_to_description(fake_net: FakeNet) -> None:
    fake_net.on("https://news.example.com/a", ok(article_page(LONG, title="Just a moment...")))
    log: list[str] = []
    got = feeds.article(_item(), log.append)
    assert got == feeds.Article("The short feed description of the story.", "feed description")
    assert any("paywall or bot check" in line for line in log)


def test_short_enable_javascript_page_falls_back(fake_net: FakeNet) -> None:
    fake_net.on(
        "https://news.example.com/a",
        ok(article_page("Please enable JavaScript and cookies to continue.")),
    )
    assert feeds.article(_item(""), [].append).origin == "feed description"


def test_long_article_mentioning_javascript_is_kept(fake_net: FakeNet) -> None:
    body = LONG + " Enable JavaScript to watch the video."
    fake_net.on("https://news.example.com/a", ok(article_page(body)))
    assert feeds.article(_item(), [].append).origin == "article page"


def test_failed_or_odd_article_fetch_falls_back(fake_net: FakeNet) -> None:
    fallback = feeds.Article("The short feed description of the story.", "feed description")
    log: list[str] = []
    assert feeds.article(_item(), log.append) == fallback  # unrouted: connection failure
    assert any("Article fetch failed" in line for line in log)
    fake_net.on("https://news.example.com/a", ok(b"%PDF-1.4", "application/pdf"))
    assert feeds.article(_item(), [].append) == fallback
    assert feeds.article(feeds.Item("T", "javascript:alert(1)", "d"), [].append).text == "d"


def test_article_shorter_than_the_description_is_ignored(fake_net: FakeNet) -> None:
    fake_net.on("https://news.example.com/a", ok(article_page("Tiny.")))
    assert feeds.article(_item(), [].append).origin == "feed description"


def test_unknown_xml_encoding_is_a_parse_failure() -> None:
    body = b'<?xml version="1.0" encoding="x-made-up"?><rss><channel><item><title>T</title></item></channel></rss>'
    assert feeds.parse_first_item(body) is None


def test_relative_links_resolve_against_xml_base_and_the_feed_url() -> None:
    atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
        xml:base="https://blog.example.com/posts/">
      <entry><title>Relative</title><link href="article"/></entry></feed>"""
    item = feeds.parse_first_item(atom, base_url="https://blog.example.com/feed.xml")
    assert item is not None and item.link == "https://blog.example.com/posts/article"
    entry_base = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry xml:base="/2026/"><title>T</title><link href="story"/></entry></feed>"""
    item = feeds.parse_first_item(entry_base, base_url="https://site.example.com/feed")
    assert item is not None and item.link == "https://site.example.com/2026/story"
    plain = rss("T", "/news/1").encode()
    item = feeds.parse_first_item(plain, base_url="https://site.example.com/rss")
    assert item is not None and item.link == "https://site.example.com/news/1"


def test_redact_scrubs_keys_url_keys_and_bearer_tokens() -> None:
    line = "failed: Bearer abc123 for https://x.example/i?w=1&key=pol-secret&h=2 with gsk_SECRET_1"
    assert text.redact(line, ("gsk_SECRET_1",)) == (
        "failed: Bearer *** for https://x.example/i?w=1&key=***&h=2 with ***"
    )


def test_keys_with_whitespace_or_control_characters_are_not_clean() -> None:
    assert text.is_clean_key("gsk_abc-123")
    assert not text.is_clean_key("gsk_abc\nheader: x")
    assert not text.is_clean_key("two words")
    assert not text.is_clean_key("bell\x07")


# ----------------------------------------------------------------------
# Feeds that are broken, hostile or unreachable never raise


def test_rss_1_rdf_feeds_are_read() -> None:
    body = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/">
  <channel><title>Feed</title></channel>
  <item><title>RDF story</title><link>https://example.com/rdf</link>
    <description>&lt;p&gt;Said so.&lt;/p&gt;</description></item>
</rdf:RDF>"""
    assert feeds.parse_first_item(body) == feeds.Item(
        "RDF story", "https://example.com/rdf", "Said so."
    )


BAD_FEEDS = {
    "truncated": (b"<?xml version='1.0'?><rss><channel><item><title>Half", "not valid XML"),
    "garbage": (b"\x00\xff\xfe not xml at all", "not valid XML"),
    "html page": (b"<!DOCTYPE html><html><body><p>Hi<br></body></html>", "a web page"),
    "other xml": (
        b"<?xml version='1.0'?><opml><body/></opml>",
        "not an RSS or Atom feed (it starts with <opml>)",
    ),
    "empty feed": (b"<rss><channel><title>Nothing</title></channel></rss>", "no stories"),
    "blank": (b"   ", "answered with nothing"),
    "entities": (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>'
        b"<rss><channel><item><title>&lol2;</title></item></channel></rss>",
        "refused for safety",
    ),
    "external entity": (
        b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b"<rss><channel><item><title>&x;</title></item></channel></rss>",
        "refused for safety",
    ),
    "unknown encoding": (b'<?xml version="1.0" encoding="x-klingon"?><rss/>', "not valid XML"),
}


@pytest.mark.parametrize("name", list(BAD_FEEDS))
def test_a_bad_feed_is_no_story_with_the_reason(fake_net: FakeNet, name: str) -> None:
    body, reason = BAD_FEEDS[name]
    assert feeds.parse_first_item(body) is None
    assert reason in feeds.why_no_item(body)
    url = "https://feeds.example.com/bad.xml"
    fake_net.on(url, ok(body, "application/xml"))
    log: list[str] = []
    assert feeds.newest_item(url, log.append, sleep=lambda s: None) is None
    assert sum(reason in line for line in log) == 2  # both attempts say why


def test_an_unreachable_feed_is_no_story(fake_net: FakeNet) -> None:
    log: list[str] = []
    # No route: the fake answers like a dead host (status 0).
    assert (
        feeds.newest_item("https://gone.example.com/rss", log.append, sleep=lambda s: None) is None
    )
    assert len(log) == 2 and all("failed" in line for line in log)


def test_a_parser_crash_is_caught(fake_net: FakeNet, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(body: bytes, base_url: str = "") -> None:
        raise RecursionError("deep")

    monkeypatch.setattr(feeds, "parse_first_item", boom)
    url = "https://feeds.example.com/deep.xml"
    fake_net.on(url, ok(rss("x", "https://example.com/x"), "text/xml"))
    log: list[str] = []
    assert feeds.newest_item(url, log.append, sleep=lambda s: None) is None
    assert any("could not read it (RecursionError)" in line for line in log)
