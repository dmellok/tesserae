"""picture_apod smoke: composer renders cells across every
supported size with mocked science.nasa.gov apod-basic data, no network
call; plus the payload quirks the server module has to undo."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

from plugins.picture_apod import server

_ASSETS = "https://assets.science.nasa.gov"

# Shaped like the live route: ``url`` is the article permalink, the image
# is ``hdurl``, credits are HTML, newest first.
_VIDEO = {
    "date": "2026-09-14",
    "title": "Comet Flyby",
    "permalink": "https://science.nasa.gov/image-article/apod-2026-september-14-comet/",
    "media_type": "video",
    "credit": "NASA",
    "copyright": "NASA",
    "url": "https://science.nasa.gov/image-article/apod-2026-september-14-comet/",
    "hdurl": f"{_ASSETS}/dynamicimage/assets/science/cds/apod/apod/2026/september/Comet_snapshot.png?w=1920&h=1080&fit=clip",
}
_IMAGE = {
    "date": "2026-09-13",
    "title": "Galaxy NGC 3660 &amp; Friends",
    "permalink": "https://science.nasa.gov/image-article/apod-2026-september-13-galaxy/",
    "media_type": "image",
    "credit": '<strong>Image Credit:</strong> <a href="https://example.org">Adam Block</a>',
    "copyright": '<strong>Image Credit:</strong> <a href="https://example.org">Adam Block</a>',
    "alt": "A spiral galaxy.",
    "url": "https://science.nasa.gov/image-article/apod-2026-september-13-galaxy/",
    "hdurl": f"{_ASSETS}/dynamicimage/assets/science/cds/apod/apod/2026/september/galaxy_4000.jpg?w=4000&h=3000&fit=clip&crop=faces%2Cfocalpoint",
}


class _FakeResp:
    def __init__(self, payload: Any) -> None:
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a) -> bool:
        return False


@pytest.mark.parametrize("size", ["sm", "md", "lg"])
def test_apod_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", return_value=_FakeResp([_VIDEO, _IMAGE])):
        resp = client.get(f"/_test/render?plugin=picture_apod&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="picture_apod"' in body
    # The image entry wins over the newer video, at the capped size.
    assert "galaxy_4000.jpg?w=1200" in body
    assert "Comet_snapshot" not in body
    assert "Adam Block" in body


def _fetch(tmp_path: Path, payload: Any) -> dict[str, Any]:
    with patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
        return server.fetch({}, {}, ctx={"data_dir": str(tmp_path)})


def test_flattens_html_and_caps_image(tmp_path: Path) -> None:
    data = _fetch(tmp_path, [_VIDEO, _IMAGE])
    assert data["date"] == "2026-09-13"
    assert data["title"] == "Galaxy NGC 3660 & Friends"
    assert data["copyright"] == "Adam Block"
    assert data["alt"] == "A spiral galaxy."
    assert data["url"] == (
        f"{_ASSETS}/dynamicimage/assets/science/cds/apod/apod/2026/september/"
        "galaxy_4000.jpg?w=1200&h=1200&fit=clip"
    )
    assert data["permalink"].startswith("https://science.nasa.gov/image-article/")


def test_content_dam_rewritten_to_resizer(tmp_path: Path) -> None:
    entry = dict(
        _IMAGE, hdurl=f"{_ASSETS}/content/dam/science/cds/apod/apod/2017/july/AS11.jpg?w=2349"
    )
    data = _fetch(tmp_path, [entry])
    assert data["url"] == (
        f"{_ASSETS}/dynamicimage/assets/science/cds/apod/apod/2017/july/AS11.jpg?w=1200&h=1200&fit=clip"
    )


def test_falls_back_to_video_still(tmp_path: Path) -> None:
    data = _fetch(tmp_path, [_VIDEO])
    assert "Comet_snapshot.png" in data["url"]


def test_img_src_from_basic_html_when_no_hdurl(tmp_path: Path) -> None:
    entry = dict(
        _IMAGE, hdurl="", basic_html=f'<IMG SRC="{_ASSETS}/dynamicimage/assets/x/y.jpg" alt="">'
    )
    data = _fetch(tmp_path, [entry])
    assert data["url"] == f"{_ASSETS}/dynamicimage/assets/x/y.jpg?w=1200&h=1200&fit=clip"


def test_empty_listing_is_an_error(tmp_path: Path) -> None:
    data = _fetch(tmp_path, [])
    assert data["url"] is None
    assert "No APOD image" in data["error"]
