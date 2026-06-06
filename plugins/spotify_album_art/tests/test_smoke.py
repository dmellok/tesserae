"""spotify_album_art smoke: composer renders with spotify_core.now_playing
mocked, no OAuth, no network."""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

_PLAYING = {
    "connected": True,
    "ok": True,
    "is_playing": True,
    "album_art": "https://i.scdn.co/image/cover640",
    "track": "Such Great Heights",
    "artist": "The Postal Service",
}


def _core(app: Flask):
    return app.config["PLUGIN_REGISTRY"].get("spotify_core").server_module


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_album_art_renders_cover(app: Flask, client: FlaskClient, monkeypatch, size: str) -> None:
    monkeypatch.setattr(_core(app), "now_playing", lambda: _PLAYING)
    resp = client.get(f"/_test/render?plugin=spotify_album_art&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="spotify_album_art"' in body
    # The chosen art URL is embedded in the cell's data-data payload.
    assert "i.scdn.co/image/cover640" in body


def test_album_art_idle(app: Flask, client: FlaskClient, monkeypatch) -> None:
    monkeypatch.setattr(_core(app), "now_playing", lambda: {"connected": True, "idle": True})
    resp = client.get("/_test/render?plugin=spotify_album_art&size=md")
    assert resp.status_code == 200
    assert "i.scdn.co" not in resp.get_data(as_text=True)


def test_album_art_error_surfaces(app: Flask, client: FlaskClient, monkeypatch) -> None:
    monkeypatch.setattr(
        _core(app), "now_playing", lambda: {"connected": False, "error": "Spotify not connected."}
    )
    resp = client.get("/_test/render?plugin=spotify_album_art&size=md")
    assert resp.status_code == 200
    assert "Spotify not connected." in resp.get_data(as_text=True)
