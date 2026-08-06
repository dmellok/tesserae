"""news_rss: parsing smoke test plus the #178 fetch-fallback ladder.

The widget renders from a patched urllib response (no network), and the
fallback unit tests prove a bot-blocked feed reaches the BrowserPool's
Chromium-fingerprint fetch, is skipped during push renders (deadlock
guard), and reports the original urllib error when no pool exists.
"""

from __future__ import annotations

import importlib.util
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree as ET

import pytest
from flask.testing import FlaskClient

_SPEC = importlib.util.spec_from_file_location(
    "news_rss_server", Path(__file__).resolve().parent.parent / "server.py"
)
assert _SPEC is not None and _SPEC.loader is not None
srv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(srv)

_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Le Soir</title>
    <item>
      <title>Premier article</title>
      <link>https://example.com/a</link>
      <pubDate>Sat, 01 Aug 2026 09:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Deuxieme article</title>
      <link>https://example.com/b</link>
      <pubDate>Sat, 01 Aug 2026 08:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""


class _FakeResp:
    def read(self) -> bytes:
        return _RSS

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *a: object) -> bool:
        return False


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_widget_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=news_rss&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="news_rss"' in body
    assert "Premier article" in body


def _http_403(url: str) -> ET.Element:
    # Not a real HTTPError: constructing one without a live fp trips a
    # ResourceWarning at GC, which the suite promotes to an error. The
    # fallback ladder only cares that urllib raised, not the exact type.
    raise urllib.error.URLError("HTTP Error 403: Forbidden")


def test_urllib_success_never_touches_the_pool() -> None:
    pool = MagicMock()
    with (
        patch.object(srv, "_fetch_via_urllib", return_value=ET.fromstring(_RSS)),
        patch.object(srv, "_fetch_via_pool", pool),
    ):
        root = srv._fetch_feed("https://example.com/rss", allow_pool=True)
    assert isinstance(root, ET.Element)
    pool.assert_not_called()


def test_bot_blocked_feed_falls_back_to_the_browser_pool() -> None:
    with (
        patch.object(srv, "_fetch_via_urllib", side_effect=_http_403),
        patch.object(srv, "_fetch_via_pool", return_value=ET.fromstring(_RSS)),
    ):
        root = srv._fetch_feed("https://www.lesoir.be/rss2/2/cible_principale", allow_pool=True)
    assert isinstance(root, ET.Element)
    assert root.find("channel/title").text == "Le Soir"  # type: ignore[union-attr]


def test_push_render_skips_the_pool_to_avoid_deadlock() -> None:
    pool = MagicMock()
    with (
        patch.object(srv, "_fetch_via_urllib", side_effect=_http_403),
        patch.object(srv, "_fetch_via_pool", pool),
    ):
        result = srv._fetch_feed("https://example.com/rss", allow_pool=False)
    assert isinstance(result, str) and "403" in result
    pool.assert_not_called()


def test_missing_pool_reports_the_urllib_error() -> None:
    # No app context here, so the real _fetch_via_pool returns None and
    # the caller surfaces the original urllib failure.
    with patch.object(srv, "_fetch_via_urllib", side_effect=_http_403):
        result = srv._fetch_feed("https://example.com/rss", allow_pool=True)
    assert isinstance(result, str) and "403" in result


def test_urllib_request_is_browser_shaped() -> None:
    seen: dict[str, str] = {}

    class _Resp(_FakeResp):
        pass

    def _capture(req: object, timeout: int = 0) -> _Resp:
        seen.update(dict(getattr(req, "headers", {})))
        return _Resp()

    with patch.object(srv.urllib.request, "urlopen", _capture):
        srv._fetch_via_urllib("https://example.com/rss")
    ua = seen.get("User-agent", "")
    assert ua.startswith("Mozilla/5.0")
    assert "tesserae" not in ua.lower()


# -- article preview excerpts (discussion #194) ---------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Plain text passes straight through.
        ("An outage caused delays.", "An outage caused delays."),
        # CDATA-wrapped markup: tags out, entities decoded.
        (
            "<p>Le gouvernement <b>f&eacute;d&eacute;ral</b> pr&eacute;cise.</p>",
            "Le gouvernement fédéral précise.",
        ),
        # Markup that arrived escaped rather than CDATA-wrapped.
        ("&lt;p&gt;Escaped markup&lt;/p&gt;", "Escaped markup"),
        # Stripping an inline tag must not strand punctuation.
        ('Read it at <a href="https://x.test">Ars</a>.', "Read it at Ars."),
        # Nothing to show.
        ("", ""),
        ("   \n  ", ""),
        # A bare tracking pixel leaves no prose.
        ('<img src="https://t.test/p.gif" width="1" height="1">', ""),
    ],
)
def test_clean_excerpt_shapes(raw: str, expected: str) -> None:
    assert srv._clean_excerpt(raw) == expected


def test_clean_excerpt_drops_link_dump_descriptions() -> None:
    """Some feeds put navigation in the description rather than prose (Hacker
    News ships "Article URL: ... Comments URL: ..."). Stripped of markup those
    read as two bare URLs, which is worse on a panel than no excerpt at all."""
    hn = (
        '<p>Article URL: <a href="https://example.com/x">https://example.com/x</a></p>'
        '<p>Comments URL: <a href="https://news.ycombinator.com/item?id=1">'
        "https://news.ycombinator.com/item?id=1</a></p>"
    )
    assert srv._clean_excerpt(hn) == ""


def test_clean_excerpt_caps_full_text_feeds() -> None:
    """A full-text feed puts whole articles in the description. The panel shows
    a couple of lines, so the rest is payload we never render."""
    out = srv._clean_excerpt("mot " * 500)
    assert len(out) <= srv.EXCERPT_MAX_CHARS + 1  # +1 for the ellipsis
    assert out.endswith("…")


def test_rss_and_atom_items_carry_excerpts() -> None:
    """Both feed formats expose a preview: RSS from <description>, Atom from
    <summary> falling back to <content>."""
    rss = ET.fromstring(
        b"""<rss version="2.0"><channel><title>F</title>
        <item><title>T</title><link>https://e.test/a</link>
        <description>&lt;p&gt;Une phrase de r\xc3\xa9sum\xc3\xa9.&lt;/p&gt;</description></item>
        </channel></rss>"""
    )
    _, items = srv._slim_rss(rss, 5)
    assert items[0]["excerpt"] == "Une phrase de résumé."

    atom = ET.fromstring(
        b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>F</title>
        <entry><title>T</title><link href="https://e.test/a"/>
        <content>Contenu de repli.</content></entry></feed>"""
    )
    _, items = srv._slim_atom(atom, 5)
    assert items[0]["excerpt"] == "Contenu de repli."


def test_items_without_a_description_still_parse() -> None:
    """The pre-existing fixture has no description elements at all."""
    root = ET.fromstring(_RSS)
    _, items = srv._slim_rss(root, 5)
    assert [i["excerpt"] for i in items] == ["", ""]
    assert items[0]["title"] == "Premier article"
