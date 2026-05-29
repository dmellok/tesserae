"""Smoke test — news_reddit parses Reddit's Atom feed, no network."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>programming</title>
  <entry>
    <title>First post about Rust</title>
    <link href="https://www.reddit.com/r/programming/comments/aaa/first/"/>
    <author><name>/u/alice</name></author>
    <published>2026-05-30T01:00:00+00:00</published>
  </entry>
  <entry>
    <title>Second post about Go</title>
    <link href="https://www.reddit.com/r/programming/comments/bbb/second/"/>
    <author><name>/u/bob</name></author>
    <updated>2026-05-30T02:00:00+00:00</updated>
  </entry>
</feed>"""


class _FakeResp:
    def read(self) -> bytes:
        return _ATOM

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *a: object) -> bool:
        return False


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_widget_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=news_reddit&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="news_reddit"' in body
    # Title + author from the fake feed land in the cell's embedded data.
    assert "First post about Rust" in body
    assert "alice" in body
