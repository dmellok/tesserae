"""spotify_now_playing smoke: composer renders with spotify_core.now_playing
mocked, no OAuth, no network."""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

_PLAYING = {
    "connected": True,
    "ok": True,
    "is_playing": True,
    "album_art": "https://i.scdn.co/image/cover",
    "track": "Such Great Heights",
    "artist": "The Postal Service",
    "album": "Give Up",
    "progress_ms": 42000,
    "duration_ms": 206000,
}


def _core(app: Flask):
    return app.config["PLUGIN_REGISTRY"].get("spotify_core").server_module


@pytest.mark.parametrize("size", ["sm", "md", "lg"])
def test_now_playing_renders_metadata(
    app: Flask, client: FlaskClient, monkeypatch, size: str
) -> None:
    monkeypatch.setattr(_core(app), "now_playing", lambda: _PLAYING)
    resp = client.get(f"/_test/render?plugin=spotify_now_playing&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="spotify_now_playing"' in body
    assert "Such Great Heights" in body
    assert "The Postal Service" in body
    assert "Give Up" in body


def test_now_playing_idle(app: Flask, client: FlaskClient, monkeypatch) -> None:
    monkeypatch.setattr(_core(app), "now_playing", lambda: {"connected": True, "idle": True})
    resp = client.get("/_test/render?plugin=spotify_now_playing&size=md")
    assert resp.status_code == 200
    assert "Such Great Heights" not in resp.get_data(as_text=True)
