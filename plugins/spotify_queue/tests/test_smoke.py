"""spotify_queue smoke: composer renders with spotify_core.queue mocked -
no OAuth, no network."""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

_QUEUE = {
    "connected": True,
    "ok": True,
    "currently_playing": {
        "track": "Such Great Heights",
        "artist": "The Postal Service",
        "album": "Give Up",
        "album_art": "https://i.scdn.co/image/cover-large",
        "album_art_thumb": "https://i.scdn.co/image/cover-thumb",
        "duration_ms": 266000,
    },
    "queue": [
        {
            "track": "Sleeping In",
            "artist": "The Postal Service",
            "album": "Give Up",
            "album_art": None,
            "album_art_thumb": None,
            "duration_ms": 232000,
        },
        {
            "track": "We Will Become Silhouettes",
            "artist": "The Postal Service",
            "album": "Give Up",
            "album_art": None,
            "album_art_thumb": None,
            "duration_ms": 240000,
        },
    ],
}


def _core(app: Flask):
    return app.config["PLUGIN_REGISTRY"].get("spotify_core").server_module


@pytest.mark.parametrize("size", ["sm", "md", "lg"])
def test_queue_renders_lede_and_list(
    app: Flask, client: FlaskClient, monkeypatch, size: str
) -> None:
    monkeypatch.setattr(_core(app), "queue", lambda: _QUEUE)
    resp = client.get(f"/_test/render?plugin=spotify_queue&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="spotify_queue"' in body
    # Lede now-playing track and at least one queued track land in the HTML.
    assert "Such Great Heights" in body
    assert "Sleeping In" in body
    assert "We Will Become Silhouettes" in body


def test_queue_idle(app: Flask, client: FlaskClient, monkeypatch) -> None:
    """An idle queue returns ``{"idle": True}``, the client renders the
    empty-state shell from JS, so nothing from the queue payload (track
    titles etc.) lands in the server-side HTML."""
    monkeypatch.setattr(_core(app), "queue", lambda: {"connected": True, "idle": True})
    resp = client.get("/_test/render?plugin=spotify_queue&size=md")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Sleeping In" not in body
    assert "Such Great Heights" not in body


def test_queue_error_surfaces(app: Flask, client: FlaskClient, monkeypatch) -> None:
    monkeypatch.setattr(
        _core(app),
        "queue",
        lambda: {"connected": True, "error": "Spotify Premium is required to read the queue."},
    )
    resp = client.get("/_test/render?plugin=spotify_queue&size=md")
    assert resp.status_code == 200
    assert "Premium" in resp.get_data(as_text=True)


def test_queue_max_items_truncation(monkeypatch) -> None:
    """``max_items`` is enforced server-side so the client just iterates
    whatever it's given. Test the server.py wrapper in isolation."""
    from plugins.spotify_queue import server as widget_server

    class _FakeCore:
        @staticmethod
        def queue():
            return _QUEUE

    monkeypatch.setattr(widget_server, "_core", lambda: _FakeCore)
    result = widget_server.fetch({"max_items": 1, "show_now_playing": True}, settings={}, ctx={})
    assert result["currently_playing"]["track"] == "Such Great Heights"
    assert len(result["queue"]) == 1
    assert result["queue"][0]["track"] == "Sleeping In"


def test_queue_hide_now_playing(monkeypatch) -> None:
    """``show_now_playing=False`` drops the lede; the queue list stands
    alone."""
    from plugins.spotify_queue import server as widget_server

    class _FakeCore:
        @staticmethod
        def queue():
            return _QUEUE

    monkeypatch.setattr(widget_server, "_core", lambda: _FakeCore)
    result = widget_server.fetch({"max_items": 6, "show_now_playing": False}, settings={}, ctx={})
    assert result["currently_playing"] is None
    assert len(result["queue"]) == 2
