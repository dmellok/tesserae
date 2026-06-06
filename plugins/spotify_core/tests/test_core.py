"""spotify_core unit tests: now_playing dispatch + token bookkeeping.

The OAuth HTTP is mocked at the seam (``_valid_access_token`` / ``_api_
get``) so these never touch the network or need real credentials.
"""

from __future__ import annotations

import time
from types import ModuleType
from typing import Any

import pytest
from flask import Flask


@pytest.fixture
def core(app: Flask) -> ModuleType:
    module = app.config["PLUGIN_REGISTRY"].get("spotify_core").server_module
    assert module is not None, "spotify_core failed to load"
    return module


_TRACK_BODY: dict[str, Any] = {
    "is_playing": True,
    "progress_ms": 42000,
    "item": {
        "name": "Such Great Heights",
        "duration_ms": 206000,
        "artists": [{"name": "The Postal Service"}],
        "album": {
            "name": "Give Up",
            "images": [
                {"url": "https://i.scdn.co/image/big", "width": 640},
                {"url": "https://i.scdn.co/image/small", "width": 64},
            ],
        },
    },
}


def test_now_playing_needs_credentials(app: Flask, core: ModuleType, monkeypatch) -> None:
    monkeypatch.setattr(core, "has_credentials", lambda: False)
    with app.app_context():
        out = core.now_playing()
    assert out["connected"] is False
    assert "Client ID" in out["error"]


def test_now_playing_not_connected(app: Flask, core: ModuleType, monkeypatch) -> None:
    monkeypatch.setattr(core, "has_credentials", lambda: True)
    monkeypatch.setattr(core, "connected", lambda: False)
    with app.app_context():
        out = core.now_playing()
    assert out["connected"] is False
    assert "not connected" in out["error"].lower()


def test_now_playing_parses_track(app: Flask, core: ModuleType, monkeypatch) -> None:
    monkeypatch.setattr(core, "has_credentials", lambda: True)
    monkeypatch.setattr(core, "connected", lambda: True)
    monkeypatch.setattr(core, "_valid_access_token", lambda: "tok")
    monkeypatch.setattr(core, "_api_get", lambda url, token: (200, _TRACK_BODY))
    with app.app_context():
        out = core.now_playing()
    assert out["ok"] is True
    assert out["track"] == "Such Great Heights"
    assert out["artist"] == "The Postal Service"
    assert out["album"] == "Give Up"
    # Largest image is kept for full-bleed art.
    assert out["album_art"] == "https://i.scdn.co/image/big"
    assert out["is_playing"] is True
    assert out["duration_ms"] == 206000


def test_now_playing_idle_on_204(app: Flask, core: ModuleType, monkeypatch) -> None:
    monkeypatch.setattr(core, "has_credentials", lambda: True)
    monkeypatch.setattr(core, "connected", lambda: True)
    monkeypatch.setattr(core, "_valid_access_token", lambda: "tok")
    monkeypatch.setattr(core, "_api_get", lambda url, token: (204, None))
    with app.app_context():
        out = core.now_playing()
    assert out["connected"] is True
    assert out["idle"] is True


def test_now_playing_idle_when_item_is_not_a_track(
    app: Flask, core: ModuleType, monkeypatch
) -> None:
    # Ads / podcast gaps return 200 with item == None.
    monkeypatch.setattr(core, "has_credentials", lambda: True)
    monkeypatch.setattr(core, "connected", lambda: True)
    monkeypatch.setattr(core, "_valid_access_token", lambda: "tok")
    monkeypatch.setattr(
        core, "_api_get", lambda url, token: (200, {"is_playing": True, "item": None})
    )
    with app.app_context():
        out = core.now_playing()
    assert out["idle"] is True


def test_store_token_response_keeps_prior_refresh_token(app: Flask, core: ModuleType) -> None:
    # Spotify omits refresh_token on a refresh, the prior one must survive.
    with app.app_context():
        tokens = core._store_token_response(
            {"access_token": "fresh", "expires_in": 3600, "scope": "x"},
            prior={"refresh_token": "keepme"},
        )
    assert tokens["access_token"] == "fresh"
    assert tokens["refresh_token"] == "keepme"
    assert tokens["expires_at"] > time.time()


def test_connected_reflects_token_file(app: Flask, core: ModuleType) -> None:
    with app.app_context():
        assert core.connected() is False
        core._save_tokens({"refresh_token": "r", "access_token": "a", "expires_at": 0})
        assert core.connected() is True
        core._clear_tokens()
        assert core.connected() is False
