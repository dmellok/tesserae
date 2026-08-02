"""REST frame-cache collection endpoint tests (#177): the manifest +
frame-by-digest endpoints, the /status collection envelope + capability
ingestion, and the Gallery "Use as offline album" authoring route.

Warmed renders are injected straight into the real PushManager's album cache
(with matching artifact files under RENDERS_DIR) so no image render runs; the
warm path itself is exercised in tests/test_collection_sync.py against a fake
source and end-to-end below in the authoring test."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from flask import Flask
from PIL import Image

from app import collection_sync
from app.main import REPO_ROOT, create_app
from app.state.album_model import Album


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


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _register_esp32(app: Flask, client, device_id: str) -> str:
    _sign_in(client)
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": device_id, "kind": "esp32_client"}),
    )
    assert resp.status_code == 201
    return resp.get_json()["device_token"]


def _gallery_dir(app: Flask, folder: str) -> Path:
    reg = app.config["PLUGIN_REGISTRY"]
    data_dir = reg.get("picture_gallery").data_dir
    path = Path(data_dir) / folder
    path.mkdir(parents=True, exist_ok=True)
    return path


def _seed_folder(app: Flask, folder: str, names: list[str]) -> None:
    d = _gallery_dir(app, folder)
    for name in names:
        (d / name).write_bytes(f"img-{name}".encode())


def _bind_album(app: Flask, device_id: str, folder: str = "holidays") -> Album:
    album = Album.model_validate(
        {
            "id": folder,
            "name": "Holidays",
            "device_ids": [device_id],
            "source_folder": folder,
            "fit": "fill",
            "playback": {"mode": "sequential", "interval_s": 1800, "repeat": "loop"},
        }
    )
    app.config["ALBUM_STORE"].upsert(album)
    return album


def _warm(app: Flask, device_id: str, filename: str, payload: bytes) -> str:
    """Inject a warmed album render + its artifact file, no real render."""
    digest = hashlib.sha256(payload).hexdigest()[:16]
    fname = f"{digest}.bin"
    (app.config["RENDERS_DIR"] / fname).write_bytes(payload)
    frame_id = collection_sync.frame_id_for(filename)
    app.config["PUSH_MANAGER"]._album_renders.setdefault(device_id, {})[frame_id] = {
        "digest": digest,
        "ext": "bin",
        "filename": fname,
    }
    return digest


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


CAP = {"frame_cache": {"schema": 1, "capacity_bytes": 67_108_864, "max_frames": 32}}


# -- manifest ------------------------------------------------------------


def test_collection_manifest_requires_token(app: Flask) -> None:
    client = app.test_client()
    _register_esp32(app, client, "frame01")
    assert client.get("/api/v1/device/frame01/collection").status_code == 401


def test_collection_manifest_204_when_none_bound(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    resp = client.get("/api/v1/device/frame01/collection", headers=_auth(token))
    assert resp.status_code == 204


def test_collection_manifest_returns_frames(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    _seed_folder(app, "holidays", ["a.jpg", "b.jpg"])
    _bind_album(app, "frame01")
    da = _warm(app, "frame01", "a.jpg", b"frame-a")
    db = _warm(app, "frame01", "b.jpg", b"frame-bbbb")

    resp = client.get("/api/v1/device/frame01/collection", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["collection_id"] == "album:holidays"
    assert body["kind"] == "album"
    assert body["total_frames"] == 2
    assert len(body["version"]) == 16
    frames = body["frames"]
    assert frames[0]["digest"] == da
    assert frames[0]["position"] == 0
    assert frames[0]["bytes"] == len(b"frame-a")
    assert frames[0]["cache"] is True
    assert frames[0]["url"].endswith(f"/collection/frame/{da}")
    assert frames[1]["digest"] == db


# -- frame by digest -------------------------------------------------------


def test_collection_frame_serves_bytes(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    _seed_folder(app, "holidays", ["a.jpg"])
    _bind_album(app, "frame01")
    digest = _warm(app, "frame01", "a.jpg", b"frame-a")

    resp = client.get(f"/api/v1/device/frame01/collection/frame/{digest}", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.data == b"frame-a"
    assert resp.headers["ETag"] == f'"{digest}"'
    assert "immutable" in resp.headers.get("Cache-Control", "")
    resp.close()


def test_collection_frame_404_on_unknown_digest(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    _seed_folder(app, "holidays", ["a.jpg"])
    _bind_album(app, "frame01")
    _warm(app, "frame01", "a.jpg", b"frame-a")

    resp = client.get(f"/api/v1/device/frame01/collection/frame/{'0' * 16}", headers=_auth(token))
    assert resp.status_code == 404


# -- /status envelope + capability ------------------------------------------


def test_status_carries_collection_for_capable_device(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    _seed_folder(app, "holidays", ["a.jpg"])
    _bind_album(app, "frame01")
    _warm(app, "frame01", "a.jpg", b"frame-a")

    resp = client.post(
        "/api/v1/device/frame01/status",
        headers=_auth(token),
        data=json.dumps({**CAP, "battery_mv": 4000}),
    )
    assert resp.status_code == 200
    env = resp.get_json()["collection"]
    assert env["id"] == "album:holidays"
    assert env["kind"] == "album"

    manifest = client.get("/api/v1/device/frame01/collection", headers=_auth(token)).get_json()
    assert manifest["version"] == env["version"]


def test_status_omits_collection_without_capability(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    _seed_folder(app, "holidays", ["a.jpg"])
    _bind_album(app, "frame01")

    resp = client.post(
        "/api/v1/device/frame01/status",
        headers=_auth(token),
        data=json.dumps({"battery_mv": 4000}),
    )
    assert resp.status_code == 200
    assert "collection" not in resp.get_json()


# -- authoring: Gallery "Use as offline album" ------------------------------


def test_use_as_album_creates_and_binds(app: Flask) -> None:
    client = app.test_client()
    _register_esp32(app, client, "frame01")
    _seed_folder(app, "holidays", ["a.jpg", "b.jpg"])

    resp = client.post(
        "/plugins/picture_gallery/folders/holidays/use-as-album",
        data={
            "name": "Beach trip",
            "device_ids": ["frame01"],
            "fit": "fit",
            "mode": "shuffle",
            "interval_min": "45",
            "repeat": "reshuffle",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    album = app.config["ALBUM_STORE"].get("holidays")
    assert album is not None
    assert album.name == "Beach trip"
    assert album.device_ids == ["frame01"]
    assert album.fit == "fit"
    assert album.playback.mode == "shuffle"
    assert album.playback.interval_s == 45 * 60
    assert album.playback.repeat == "reshuffle"


def test_use_as_album_rejects_empty_folder(app: Flask) -> None:
    client = app.test_client()
    _register_esp32(app, client, "frame01")
    _gallery_dir(app, "empty")  # exists but no images

    client.post(
        "/plugins/picture_gallery/folders/empty/use-as-album",
        data={"device_ids": ["frame01"]},
        follow_redirects=False,
    )
    assert app.config["ALBUM_STORE"].get("empty") is None


# -- end-to-end: real render (no injected frames) ---------------------------


def test_collection_end_to_end_renders_real_images(app: Flask) -> None:
    """The manifest endpoint renders real gallery images to panel artifacts on
    demand (warm_missing) and serves them by digest, no frames injected."""
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    folder = _gallery_dir(app, "holidays")
    for i, color in enumerate([(200, 30, 30), (30, 200, 30)]):
        buf = io.BytesIO()
        Image.new("RGB", (240, 160), color).save(buf, "PNG")
        (folder / f"{i}.png").write_bytes(buf.getvalue())
    _bind_album(app, "frame01")

    body = client.get("/api/v1/device/frame01/collection", headers=_auth(token)).get_json()
    assert body["total_frames"] == 2
    for frame in body["frames"]:
        assert len(frame["digest"]) == 16
        assert frame["bytes"] > 0
        resp = client.get(
            f"/api/v1/device/frame01/collection/frame/{frame['digest']}", headers=_auth(token)
        )
        assert resp.status_code == 200
        assert len(resp.data) == frame["bytes"]
        resp.close()
