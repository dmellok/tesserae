"""picture_natgeo smoke: composer renders cells across every supported
size with mocked feed / article / image responses, no network call; plus
the feed parsing, the article-page lookup, and the local photo store."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient
from PIL import Image

from plugins.picture_natgeo import server

_THUMB = "https://assets-natgeotv.fnghub.com/POD/18231.ThumbL.jpg"
_FULL = "https://assets-natgeotv.techops.disn.io/POD/18230.jpg"

# Shaped like the live feed: newest first, the description is escaped
# HTML carrying the thumbnail and a caption ending "Image from <show>."
_FEED = f"""<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>
<title>Photo of the day</title>
<item><title>OCTOBER 24, 2026</title>
<link>http://natgeotv.com/ca/photo-of-the-day/2026/october/24</link>
<description>&lt;a style=float:left; href=&quot;http://natgeotv.com/ca/photo-of-the-day/2026/october/24&quot;&gt;&lt;img src=&quot;{_THUMB}&quot; /&gt;&lt;/a&gt;Two of Kio&amp;#039;s brothers sit upright. Image from Lion.</description></item>
<item><title>OCTOBER 23, 2026</title>
<link>http://natgeotv.com/ca/photo-of-the-day/2026/october/23</link>
<description>&lt;img src=&quot;https://assets-natgeotv.fnghub.com/POD/18229.ThumbL.jpg&quot; /&gt;Aldabra Atoll. Image from Africa.</description></item>
</channel></rss>""".encode()

# The article page lists the whole month; the day's frame sits in
# <a id="POD<day>">.
_PAGE = f"""<ul><li class="PODItem"><a class="DisplayBlock" id="POD24"><img style="margin: 0 auto;" width="940" src="{_FULL}" alt="..." /></a></li>
<li class="PODItem"><a class="DisplayBlock" id="POD2"><img src="https://assets-natgeotv.techops.disn.io/POD/18184.jpg" /></a></li></ul>""".encode()


def _jpeg(w: int = 2400, h: int = 1350) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 120, 40)).save(buf, "JPEG")
    return buf.getvalue()


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a) -> bool:
        return False


def _router(responses: dict[str, bytes | Exception]):
    def urlopen(req: Any, timeout: float | None = None) -> _FakeResp:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for prefix, body in responses.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return _FakeResp(body)
        raise AssertionError(f"unexpected request: {url}")

    return urlopen


_LIVE = {
    server.FEED_URL: _FEED,
    "https://www.natgeotv.com/": _PAGE,
    _FULL: _jpeg(),
}


def _fetch(tmp_path: Path, responses: dict[str, bytes | Exception]) -> dict[str, Any]:
    with patch("urllib.request.urlopen", side_effect=_router(responses)):
        return server.fetch({}, {}, ctx={"data_dir": str(tmp_path)})


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_natgeo_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", side_effect=_router(_LIVE)):
        resp = client.get(f"/_test/render?plugin=picture_natgeo&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="picture_natgeo"' in body
    # Values from the fake feed round-trip into the cell's data payload.
    assert "/plugins/picture_natgeo/photos/18230.jpg" in body
    assert "Kio" in body
    assert "2026-10-24" in body
    assert "Lion" in body


def test_parses_newest_item(tmp_path: Path) -> None:
    data = _fetch(tmp_path, _LIVE)
    assert data["date"] == "2026-10-24"
    assert data["caption"] == "Two of Kio's brothers sit upright."
    assert data["source"] == "Lion"
    assert data["link"] == "https://www.natgeotv.com/ca/photo-of-the-day/2026/october/24"


def test_full_frame_is_downscaled_and_served_locally(tmp_path: Path) -> None:
    data = _fetch(tmp_path, _LIVE)
    assert data["url"] == "/plugins/picture_natgeo/photos/18230.jpg"
    stored = tmp_path / "photos" / "18230.jpg"
    with Image.open(stored) as img:
        assert max(img.size) == server.MAX_EDGE_PX


def test_photo_route_serves_the_stored_file(client: FlaskClient) -> None:
    plugin = client.application.config["PLUGIN_REGISTRY"].get("picture_natgeo")
    photos = Path(plugin.data_dir) / "photos"
    photos.mkdir(parents=True, exist_ok=True)
    (photos / "99999.jpg").write_bytes(_jpeg(10, 10))
    resp = client.get("/plugins/picture_natgeo/photos/99999.jpg")
    try:
        assert resp.status_code == 200
        assert resp.mimetype == "image/jpeg"
    finally:
        resp.close()
    assert client.get("/plugins/picture_natgeo/photos/..%2Fpod.json").status_code == 404


def test_falls_back_to_thumbnail_when_article_unreadable(tmp_path: Path) -> None:
    responses = {
        server.FEED_URL: _FEED,
        "https://www.natgeotv.com/": OSError("down"),
        "https://assets-natgeotv.fnghub.com/": _jpeg(300, 169),
    }
    data = _fetch(tmp_path, responses)
    assert data["url"] == "/plugins/picture_natgeo/photos/18231-ThumbL.jpg"


def test_falls_back_to_remote_url_when_image_download_fails(tmp_path: Path) -> None:
    responses = {**_LIVE, _FULL: OSError("refused")}
    data = _fetch(tmp_path, responses)
    assert data["url"] == _FULL


def test_unreachable_feed_is_a_friendly_error(tmp_path: Path) -> None:
    data = _fetch(tmp_path, {server.FEED_URL: OSError("no route")})
    assert data["error"] == "Couldn't reach National Geographic right now."


def test_feed_is_cached(tmp_path: Path) -> None:
    first = _fetch(tmp_path, _LIVE)
    # Any request now would raise "unexpected request".
    assert _fetch(tmp_path, {}) == first
