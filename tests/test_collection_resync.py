"""Forced collection resync (#247).

The collection version is a content digest, so there is normally no way to say
"send it again" when server and device disagree for a reason the content can't
express. A per-device resync token moves the version without touching the
manifest body.

The load-bearing property is agreement: /status and the manifest endpoint must
fold in the SAME token, or the device sees a mismatch on every beat and
re-syncs forever. That is asserted end-to-end here, not just at unit level."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from app import collection_sync
from app.main import REPO_ROOT, create_app
from app.state.collection_resync_store import CollectionResyncStore

from .test_rest_collection import CAP, _auth, _bind_album, _register_esp32, _seed_folder, _warm


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


# -- store ---------------------------------------------------------------


def test_store_starts_empty(tmp_path: Path) -> None:
    store = CollectionResyncStore(tmp_path / "resync.json")
    assert store.token("frame01") is None
    assert store.get("frame01") is None


def test_store_bump_persists_and_is_readable(tmp_path: Path) -> None:
    path = tmp_path / "resync.json"
    token = CollectionResyncStore(path).bump("frame01", album_id="holidays")
    # A second instance reads the same file: a restart must not silently
    # revert the version and undo the resync.
    assert CollectionResyncStore(path).token("frame01") == token
    assert CollectionResyncStore(path).get("frame01")["album_id"] == "holidays"


def test_store_bumps_differ_within_the_same_second(tmp_path: Path) -> None:
    store = CollectionResyncStore(tmp_path / "resync.json")
    first = store.bump("frame01", album_id="holidays")
    second = store.bump("frame01", album_id="holidays")
    assert first != second


def test_store_is_per_device(tmp_path: Path) -> None:
    store = CollectionResyncStore(tmp_path / "resync.json")
    store.bump("frame01", album_id="holidays")
    assert store.token("frame02") is None


def test_store_clear(tmp_path: Path) -> None:
    store = CollectionResyncStore(tmp_path / "resync.json")
    store.bump("frame01", album_id="holidays")
    assert store.clear("frame01") is True
    assert store.token("frame01") is None
    assert store.clear("frame01") is False


def test_store_survives_a_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "resync.json"
    path.write_text("{not json", encoding="utf-8")
    store = CollectionResyncStore(path)
    assert store.token("frame01") is None
    assert store.bump("frame01", album_id="holidays")


# -- version folding -----------------------------------------------------


def test_no_token_leaves_the_digest_alone() -> None:
    assert collection_sync.resynced_version("abc123", None) == "abc123"
    assert collection_sync.resynced_version("abc123", "") == "abc123"


def test_token_changes_the_version_deterministically() -> None:
    base = "abc123"
    once = collection_sync.resynced_version(base, "t1")
    assert once != base
    assert len(once) == 16
    assert collection_sync.resynced_version(base, "t1") == once
    assert collection_sync.resynced_version(base, "t2") != once


# -- end to end ----------------------------------------------------------


def _status_version(client, token: str) -> str:
    resp = client.post(
        "/api/v1/device/frame01/status",
        headers=_auth(token),
        data=json.dumps({**CAP, "battery_mv": 4000}),
    )
    assert resp.status_code == 200
    return str(resp.get_json()["collection"]["version"])


def _setup(app: Flask, client) -> str:
    token = _register_esp32(app, client, "frame01")
    _seed_folder(app, "holidays", ["a.jpg", "b.jpg"])
    _bind_album(app, "frame01")
    _warm(app, "frame01", "a.jpg", b"frame-a")
    _warm(app, "frame01", "b.jpg", b"frame-bbbb")
    return token


def test_resync_changes_the_version_the_device_sees(app: Flask) -> None:
    client = app.test_client()
    token = _setup(app, client)

    before = _status_version(client, token)
    app.config["COLLECTION_RESYNC_STORE"].bump("frame01", album_id="holidays")
    after = _status_version(client, token)
    assert after != before


def test_status_and_manifest_agree_after_a_resync(app: Flask) -> None:
    """The one that matters: disagreement here means a device that re-syncs
    on every single wake instead of once."""
    client = app.test_client()
    token = _setup(app, client)
    app.config["COLLECTION_RESYNC_STORE"].bump("frame01", album_id="holidays")

    status_version = _status_version(client, token)
    manifest = client.get("/api/v1/device/frame01/collection", headers=_auth(token)).get_json()
    assert manifest["version"] == status_version


def test_resync_leaves_the_manifest_body_untouched(app: Flask) -> None:
    """Only the opaque version moves. Firmware sees an ordinary content
    change, which is the code path it already handles."""
    client = app.test_client()
    token = _setup(app, client)

    before = client.get("/api/v1/device/frame01/collection", headers=_auth(token)).get_json()
    app.config["COLLECTION_RESYNC_STORE"].bump("frame01", album_id="holidays")
    after = client.get("/api/v1/device/frame01/collection", headers=_auth(token)).get_json()

    assert after["version"] != before["version"]
    assert {k: v for k, v in after.items() if k != "version"} == {
        k: v for k, v in before.items() if k != "version"
    }


def test_version_is_stable_across_beats_after_one_resync(app: Flask) -> None:
    """A token is a one-shot version change, not a moving target: once bumped
    it must hold still, or the device never converges."""
    client = app.test_client()
    token = _setup(app, client)
    app.config["COLLECTION_RESYNC_STORE"].bump("frame01", album_id="holidays")

    assert _status_version(client, token) == _status_version(client, token)


# -- settings route ------------------------------------------------------


def test_resync_route_bumps_the_token(app: Flask) -> None:
    # _register_esp32 signs the client in as part of pairing, so this client
    # is already an authenticated admin session.
    client = app.test_client()
    _setup(app, client)

    assert app.config["COLLECTION_RESYNC_STORE"].token("frame01") is None
    resp = client.post("/settings/devices/frame01/album/resync")
    assert resp.status_code == 302
    assert app.config["COLLECTION_RESYNC_STORE"].token("frame01") is not None


def test_resync_route_rejects_a_device_with_no_album(app: Flask) -> None:
    client = app.test_client()
    _register_esp32(app, client, "frame01")  # signs in; no album bound

    resp = client.post("/settings/devices/frame01/album/resync")
    assert resp.status_code == 302
    assert app.config["COLLECTION_RESYNC_STORE"].token("frame01") is None


def test_devices_page_offers_resync_for_a_bound_album(app: Flask) -> None:
    """The control has to be reachable, which means the card renders it and
    the endpoint name in the template actually resolves."""
    client = app.test_client()
    _setup(app, client)

    body = client.get("/settings/devices").get_data(as_text=True)
    assert "/settings/devices/frame01/album/resync" in body
    assert "Resync" in body


def test_devices_page_shows_the_album_before_any_report(app: Flask) -> None:
    """A binding that has never produced a report is exactly when the resync
    control is needed, so the line must not wait for one (#247)."""
    client = app.test_client()
    _register_esp32(app, client, "frame01")
    _seed_folder(app, "holidays", ["a.jpg"])
    _bind_album(app, "frame01")

    body = client.get("/settings/devices").get_data(as_text=True)
    assert "Offline album" in body
    assert "no report yet" in body
    assert "/settings/devices/frame01/album/resync" in body


def test_resync_route_requires_sign_in(app: Flask) -> None:
    _setup(app, app.test_client())
    # A SEPARATE client, carrying no admin session cookie.
    resp = app.test_client().post("/settings/devices/frame01/album/resync")
    assert resp.status_code in (302, 401, 403)
    assert app.config["COLLECTION_RESYNC_STORE"].token("frame01") is None
