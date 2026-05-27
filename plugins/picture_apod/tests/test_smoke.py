"""picture_apod smoke: composer renders cells across every supported
size with mocked NASA APOD data — no network call."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

_FAKE_PAYLOAD = json.dumps(
    {
        "date": "2026-05-27",
        "title": "Galaxy NGC 3660",
        "explanation": "...",
        "media_type": "image",
        "url": "https://apod.nasa.gov/apod/image/2605/galaxy_960.jpg",
        "hdurl": "https://apod.nasa.gov/apod/image/2605/galaxy_1176.jpg",
        "copyright": "Adam Block",
    }
).encode()


class _FakeResp:
    def read(self) -> bytes:
        return _FAKE_PAYLOAD

    def __enter__(self):
        return self

    def __exit__(self, *a) -> bool:
        return False


@pytest.mark.parametrize("size", ["sm", "md", "lg"])
def test_apod_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=picture_apod&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="picture_apod"' in body
    # The hdurl (preferred over plain url) round-trips through data-data.
    assert "galaxy_1176.jpg" in body
    assert "Galaxy NGC 3660" in body
