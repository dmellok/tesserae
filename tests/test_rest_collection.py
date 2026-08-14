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


def test_collection_manifest_pages_with_cursor(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collection_sync, "PAGE_MAX_FRAMES", 2)
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    _seed_folder(app, "holidays", ["a.jpg", "b.jpg", "c.jpg"])
    _bind_album(app, "frame01")
    for name in ["a.jpg", "b.jpg", "c.jpg"]:
        _warm(app, "frame01", name, f"frame-{name}".encode())

    p1 = client.get("/api/v1/device/frame01/collection", headers=_auth(token)).get_json()
    assert [f["position"] for f in p1["frames"]] == [0, 1]
    assert p1["cursor"] is None
    assert p1["total_frames"] == 3
    assert p1["next_cursor"]

    p2 = client.get(
        f"/api/v1/device/frame01/collection?cursor={p1['next_cursor']}",
        headers=_auth(token),
    ).get_json()
    assert [f["position"] for f in p2["frames"]] == [2]
    assert p2["cursor"] == p1["next_cursor"]
    assert p2["next_cursor"] is None
    assert p2["version"] == p1["version"]

    # A stale cursor (collection changed mid-walk) restarts at page one.
    stale = client.get(
        "/api/v1/device/frame01/collection?cursor=deadbeef00000000.2",
        headers=_auth(token),
    ).get_json()
    assert stale["cursor"] is None
    assert [f["position"] for f in stale["frames"]] == [0, 1]


def test_large_album_first_page_holds_every_cacheable_frame(app: Flask) -> None:
    """A folder well past max_frames pages instead of shipping one huge
    manifest (the firmware REST receive ceiling is 32 KiB); all cache:true
    frames land on page one for slice-1 single-page firmware."""
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    names = [f"{i:03d}.jpg" for i in range(70)]
    _seed_folder(app, "holidays", names)
    _bind_album(app, "frame01")
    for name in names:
        _warm(app, "frame01", name, f"frame-{name}".encode())
    # Advertise the slice-1 cap so cache eligibility is bounded at 32.
    client.post("/api/v1/device/frame01/status", headers=_auth(token), data=json.dumps(CAP))

    p1 = client.get("/api/v1/device/frame01/collection", headers=_auth(token)).get_json()
    assert p1["total_frames"] == 70
    assert len(p1["frames"]) == collection_sync.PAGE_MAX_FRAMES
    assert p1["next_cursor"]
    cacheable = [f for f in p1["frames"] if f["cache"]]
    assert len(cacheable) == 32
    assert len(json.dumps(p1).encode()) < 32 * 1024

    p2 = client.get(
        f"/api/v1/device/frame01/collection?cursor={p1['next_cursor']}",
        headers=_auth(token),
    ).get_json()
    assert [f["position"] for f in p2["frames"]] == list(range(64, 70))
    assert all(not f["cache"] for f in p2["frames"])
    assert p2["next_cursor"] is None


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


# -- /status playback-report ingest -----------------------------------------


REPORT = {
    "id": "album:holidays",
    "version": "b" * 16,
    "cached": 1,
    "total": 1,
    "state": "playing",
}


def test_status_ingests_playback_report_and_carries_forward(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    _seed_folder(app, "holidays", ["a.jpg"])
    _bind_album(app, "frame01")
    _warm(app, "frame01", "a.jpg", b"frame-a")

    resp = client.post(
        "/api/v1/device/frame01/status",
        headers=_auth(token),
        data=json.dumps({**CAP, "collection": REPORT}),
    )
    assert resp.status_code == 200
    stored = app.config["DEVICE_STATUS"]["frame01"]["collection_report"]
    assert stored["id"] == "album:holidays"
    assert stored["state"] == "playing"
    assert stored["received_at"] > 0

    # A beat that omits the report keeps the last observation (with its
    # original received_at) rather than forgetting playback state.
    client.post("/api/v1/device/frame01/status", headers=_auth(token), data=json.dumps(CAP))
    kept = app.config["DEVICE_STATUS"]["frame01"]["collection_report"]
    assert kept["state"] == "playing"
    assert kept["received_at"] == stored["received_at"]


def test_devices_card_shows_report_only_for_bound_album(app: Flask) -> None:
    from app.settings.index_routes import _collection_view

    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    _seed_folder(app, "holidays", ["a.jpg"])
    _bind_album(app, "frame01")
    _warm(app, "frame01", "a.jpg", b"frame-a")
    client.post(
        "/api/v1/device/frame01/status",
        headers=_auth(token),
        data=json.dumps({**CAP, "collection": REPORT}),
    )

    device = next(d for d in app.config["DEVICE_REGISTRY"].all() if d.id == "frame01")
    cache = app.config["DEVICE_STATUS"]["frame01"]
    with app.test_request_context("/settings/devices"):
        view = _collection_view(device, cache)
        assert view is not None
        assert view["album_name"] == "Holidays"
        assert view["state"] == "playing"
        assert view["pill_class"] == "is-ok"
        assert view["counts"] == "1 of 1 frames cached"

        # Unbinding the album hides the (now-irrelevant) report.
        app.config["ALBUM_STORE"].delete("holidays")
        assert _collection_view(device, cache) is None


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


def test_album_targets_are_offered_by_advertised_capability(app: Flask) -> None:
    """The form lists every registered display but marks what each one
    actually reported, so a panel with no frame cache can't be picked and
    one that simply hasn't checked in isn't presented as confirmed (#225).
    """
    client = app.test_client()
    with_cache = _register_esp32(app, client, "frame01")
    no_cache = _register_esp32(app, client, "frame02")
    _register_esp32(app, client, "frame03")  # never reports

    client.post("/api/v1/device/frame01/status", headers=_auth(with_cache), data=json.dumps(CAP))
    client.post("/api/v1/device/frame02/status", headers=_auth(no_cache), data=json.dumps({}))

    module = app.config["PLUGIN_REGISTRY"].get("picture_gallery").server_module
    with app.test_request_context("/plugins/picture_gallery"):
        by_id = {d["id"]: d for d in module._bindable_devices()}

    assert by_id["frame01"]["state"] == "supported"
    assert by_id["frame01"]["selectable"] is True
    assert by_id["frame01"]["note"] == ""

    assert by_id["frame02"]["state"] == "unsupported"
    assert by_id["frame02"]["selectable"] is False
    assert by_id["frame02"]["note"]

    # Never heard from: still bindable, because refusing it would make a
    # sleeping display impossible to set up, but not shown as confirmed.
    assert by_id["frame03"]["state"] == "unknown"
    assert by_id["frame03"]["selectable"] is True
    assert by_id["frame03"]["note"]


def test_use_as_album_drops_a_target_that_reported_no_frame_cache(app: Flask) -> None:
    """Saving from a stale tab (or a hand-made POST) must not bind a
    display that can never receive the collection: it looks like it
    worked and then silently never plays."""
    client = app.test_client()
    with_cache = _register_esp32(app, client, "frame01")
    no_cache = _register_esp32(app, client, "frame02")
    client.post("/api/v1/device/frame01/status", headers=_auth(with_cache), data=json.dumps(CAP))
    client.post("/api/v1/device/frame02/status", headers=_auth(no_cache), data=json.dumps({}))
    _seed_folder(app, "holidays", ["a.jpg"])

    client.post(
        "/plugins/picture_gallery/folders/holidays/use-as-album",
        data={"name": "Beach trip", "device_ids": ["frame01", "frame02"]},
        follow_redirects=False,
    )
    album = app.config["ALBUM_STORE"].get("holidays")
    assert album is not None
    assert album.device_ids == ["frame01"]


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
