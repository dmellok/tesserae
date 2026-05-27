"""picture_unsplash smoke: missing-key error path + happy-path fetch
with mocked API. The composer render test exercises only the empty
path because the test scaffolding doesn't accept cell options or
settings."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient


def _load_server():
    spec = importlib.util.spec_from_file_location(
        "unsplash_server",
        Path(__file__).resolve().parents[1] / "server.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_missing_access_key_returns_friendly_error() -> None:
    server = _load_server()
    out = server.fetch({}, {}, ctx={"data_dir": "/tmp/test_usp_nope"})
    assert out.get("error")
    assert "access key" in out["error"].lower()


_FAKE_PHOTO = json.dumps({
    "urls": {
        "raw":     "https://images.unsplash.com/raw",
        "full":    "https://images.unsplash.com/full",
        "regular": "https://images.unsplash.com/regular",
    },
    "alt_description": "Tree in fog",
    "links": {
        "html":              "https://unsplash.com/photos/abc",
        "download_location": "https://api.unsplash.com/photos/abc/download",
    },
    "user": {"name": "Ansel Adams", "username": "ansel"},
    "color": "#888888",
}).encode()


class _FakeResp:
    def read(self) -> bytes:
        return _FAKE_PHOTO

    def __enter__(self):
        return self

    def __exit__(self, *a) -> bool:
        return False


def test_fetch_picks_regular_url_and_records_credit(tmp_path) -> None:
    server = _load_server()
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        out = server.fetch(
            {"query": "trees"},
            {"access_key": "test"},
            ctx={"data_dir": str(tmp_path)},
        )
    assert "error" not in out
    assert out["url"] == "https://images.unsplash.com/regular"
    assert out["credit_name"] == "Ansel Adams"
    assert out["alt"] == "Tree in fog"


@pytest.mark.parametrize("size", ["sm", "md", "lg"])
def test_unsplash_renders_error_state_when_key_missing(client: FlaskClient, size: str) -> None:
    """No access_key in settings → fetch returns an error payload; the
    composer should still produce a rendered shell."""
    resp = client.get(f"/_test/render?plugin=picture_unsplash&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="picture_unsplash"' in body
    assert "access key" in body.lower() or "error" in body.lower()
