"""Companion Offline Album authoring (#230).

Drives the real app so every response is validated against the same
component schemas the iOS client decodes with, and covers the parts the
contract makes the server responsible for: opaque identity that never
leaks a folder name, a validator that refuses a write prepared against a
record someone else has since changed, a conflict that names the album
holding a display rather than an id nobody can resolve, an order that is
normalized rather than echoed, and a plan that says only what it can
actually compute.

Warmed renders are injected straight into the PushManager's album cache
with matching artifact files, the same shortcut ``tests/
test_rest_collection.py`` uses, so no image render runs here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from flask import Flask

from app import collection_sync, companion_albums, companion_gallery
from app.main import REPO_ROOT, create_app
from app.state.album_model import Album

from ._schema import schema_for

_FMT = jsonschema.FormatChecker()

CAP = {"frame_cache": {"schema": 1, "capacity_bytes": 67_108_864, "max_frames": 32}}

_CLIENT = {
    "name": "Test iPhone",
    "platform": "ios",
    "app_version": "0.1.0",
    "installation_id": "A1B2C3D4-E5F6-47A8-9012-3456789ABCDE",
}


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


def _validate(instance: Any, component: str) -> None:
    jsonschema.validate(instance, schema_for(component), format_checker=_FMT)


def _pair(app: Flask) -> tuple[str, str]:
    """``(token, token_id)`` for a freshly paired client."""
    code = app.config["COMPANION_PAIRING_STORE"].issue(note="test").code
    resp = app.test_client().post(
        "/api/app/v1/pair",
        data=json.dumps({"code": code, "client": _CLIENT}),
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    token = resp.get_json()["token"]
    return token, app.config["COMPANION_TOKENS"].lookup(token).token_id


@pytest.fixture
def auth(app: Flask) -> dict[str, str]:
    """A paired client that has also been granted the optional write scope,
    which is what an operator does in Settings before authoring."""
    token, token_id = _pair(app)
    app.config["COMPANION_TOKENS"].set_optional_scope(
        token_id, "offline_albums:write", granted=True
    )
    return {"Authorization": f"Bearer {token}"}


def _register(
    app: Flask, device_id: str, *, cap: dict[str, Any] | None = CAP, beat: bool = True
) -> None:
    """Register an ESP32 client and let it report (or not report) storage.

    ``cap=None`` posts an empty beat, which is a display whose current
    report explicitly lacks the frame cache. ``beat=False`` skips the beat
    entirely, which leaves it ``unknown``: a different answer, and one
    that stays bindable."""
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": device_id, "kind": "esp32_client"}),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    if not beat:
        return
    token = resp.get_json()["device_token"]
    client.post(
        f"/api/v1/device/{device_id}/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps(cap if cap is not None else {}),
    )


def _seed_folder(app: Flask, folder: str, names: list[str]) -> None:
    data_dir = Path(app.config["PLUGIN_REGISTRY"].get("picture_gallery").data_dir)
    target = data_dir / folder
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        (target / name).write_bytes(f"img-{name}".encode())


def _warm(app: Flask, device_id: str, filename: str, payload: bytes) -> str:
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


def _draft(**over: Any) -> dict[str, Any]:
    draft = {
        "name": "Holidays",
        "enabled": True,
        "device_ids": ["frame01"],
        "order": [],
        "fit": "fill",
        "playback": {"mode": "sequential", "interval_s": 1800, "repeat": "loop"},
    }
    draft.update(over)
    return draft


def _url(folder: str = "holidays") -> str:
    return f"/api/app/v1/gallery/folders/{companion_gallery.folder_id(folder)}/offline-album"


def _put(app: Flask, auth: dict[str, str], body: dict[str, Any], **headers: str) -> Any:
    return app.test_client().put(
        _url(),
        headers={**auth, **headers},
        data=json.dumps(body),
        content_type="application/json",
    )


def _create(app: Flask, auth: dict[str, str], **over: Any) -> Any:
    resp = _put(app, auth, {"album": _draft(**over)})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp


# -- capability and scope ------------------------------------------------


def test_the_capability_is_advertised_and_the_write_scope_is_not_granted_at_pairing(
    app: Flask,
) -> None:
    token, token_id = _pair(app)
    resp = app.test_client().get("/api/app/v1", headers={"Authorization": f"Bearer {token}"})
    body = resp.get_json()
    _validate(body, "Capabilities")
    assert "offline_albums" in body["features"]

    record = app.config["COMPANION_TOKENS"].lookup(token)
    assert "offline_albums:write" not in record.scopes
    assert "gallery:read" in record.scopes

    assert app.config["COMPANION_TOKENS"].set_optional_scope(
        token_id, "offline_albums:write", granted=True
    )
    assert "offline_albums:write" in app.config["COMPANION_TOKENS"].lookup(token).scopes


def test_authoring_without_the_granted_scope_is_refused(app: Flask) -> None:
    token, _ = _pair(app)
    _seed_folder(app, "holidays", ["a.jpg"])
    _register(app, "frame01")
    resp = _put(app, {"Authorization": f"Bearer {token}"}, {"album": _draft()})
    assert resp.status_code == 403
    _validate(resp.get_json(), "ErrorResponse")


# -- read ----------------------------------------------------------------


def test_a_folder_without_an_album_is_a_404(app: Flask, auth: dict[str, str]) -> None:
    _seed_folder(app, "holidays", ["a.jpg"])
    resp = app.test_client().get(_url(), headers=auth)
    assert resp.status_code == 404
    _validate(resp.get_json(), "ErrorResponse")


def test_the_album_id_is_opaque_and_is_not_the_folder_name(
    app: Flask, auth: dict[str, str]
) -> None:
    """The stored id is still the folder, because every device report is
    keyed on it, but nothing on the wire says so."""
    _seed_folder(app, "holidays", ["a.jpg"])
    _register(app, "frame01")
    body = _create(app, auth).get_json()
    _validate(body, "OfflineAlbumResponse")

    assert body["album"]["id"] != "holidays"
    assert body["album"]["id"] != body["album"]["folder_id"]
    assert "holidays" not in body["album"]["id"]
    assert app.config["ALBUM_STORE"].get("holidays") is not None


# -- write, validators, conflicts ----------------------------------------


def test_create_then_replace_with_the_validator(app: Flask, auth: dict[str, str]) -> None:
    _seed_folder(app, "holidays", ["a.jpg"])
    _register(app, "frame01")

    created = _create(app, auth)
    etag = created.headers["ETag"]
    assert etag

    stale = _put(app, auth, {"album": _draft(name="Renamed")})
    assert stale.status_code == 412, "no If-Match on an existing album is creation-only"
    _validate(stale.get_json(), "ErrorResponse")
    assert stale.get_json()["error"]["code"] == "precondition_failed"

    replaced = _put(app, auth, {"album": _draft(name="Renamed")}, **{"If-Match": etag})
    assert replaced.status_code == 200
    _validate(replaced.get_json(), "OfflineAlbumResponse")
    assert replaced.get_json()["album"]["name"] == "Renamed"
    assert replaced.headers["ETag"] != etag

    again = _put(app, auth, {"album": _draft(name="Third")}, **{"If-Match": etag})
    assert again.status_code == 412, "the validator moved when the album was replaced"


def test_a_validator_for_a_deleted_album_is_refused(app: Flask, auth: dict[str, str]) -> None:
    _seed_folder(app, "holidays", ["a.jpg"])
    _register(app, "frame01")
    etag = _create(app, auth).headers["ETag"]
    assert app.test_client().delete(_url(), headers=auth).status_code == 204

    resp = _put(app, auth, {"album": _draft()}, **{"If-Match": etag})
    assert resp.status_code == 412


def test_an_unsupported_target_is_named_rather_than_dropped(
    app: Flask, auth: dict[str, str]
) -> None:
    """The Web form drops these and says so; over the API a form that
    passed preflight and then went stale gets the ids back."""
    _seed_folder(app, "holidays", ["a.jpg"])
    _register(app, "frame01")
    _register(app, "frame02", cap=None)

    resp = _put(app, auth, {"album": _draft(device_ids=["frame01", "frame02"])})
    assert resp.status_code == 400
    body = resp.get_json()
    _validate(body, "ErrorResponse")
    assert body["error"]["code"] == "offline_album_unsupported_targets"
    assert body["error"]["device_ids"] == ["frame02"]
    assert app.config["ALBUM_STORE"].get("holidays") is None


def test_an_unknown_target_stays_bindable(app: Flask, auth: dict[str, str]) -> None:
    """A display asleep during setup is not evidence of anything, and
    refusing it would make it impossible to bind at all."""
    _seed_folder(app, "holidays", ["a.jpg"])
    _register(app, "frame01")
    _register(app, "frame03", beat=False)

    resp = _put(app, auth, {"album": _draft(device_ids=["frame01", "frame03"])})
    assert resp.status_code == 201
    body = resp.get_json()
    _validate(body, "OfflineAlbumResponse")
    states = {t["device_id"]: t["support"]["state"] for t in body["targets"]}
    assert states == {"frame01": "supported", "frame03": "unknown"}


def test_an_unregistered_target_is_an_invalid_target(app: Flask, auth: dict[str, str]) -> None:
    _seed_folder(app, "holidays", ["a.jpg"])
    resp = _put(app, auth, {"album": _draft(device_ids=["nope"])})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_target"


def test_a_conflict_names_the_album_holding_the_display(app: Flask, auth: dict[str, str]) -> None:
    _seed_folder(app, "holidays", ["a.jpg"])
    _seed_folder(app, "family", ["b.jpg"])
    _register(app, "frame01")
    _register(app, "frame02")
    app.config["ALBUM_STORE"].upsert(
        Album.model_validate(
            {
                "id": "family",
                "name": "Family",
                "device_ids": ["frame01", "frame02"],
                "source_folder": "family",
            }
        )
    )

    resp = _put(app, auth, {"album": _draft(device_ids=["frame01"])})
    assert resp.status_code == 409
    body = resp.get_json()
    _validate(body, "ErrorResponse")
    assert body["error"]["code"] == "offline_album_conflict"
    claim = body["error"]["claims"]["frame01"]
    assert claim["name"] == "Family", "an id the app can't resolve is not an explanation"
    assert claim["album_id"] == companion_albums.album_id("family")
    assert app.config["ALBUM_STORE"].get("holidays") is None


def test_an_explicit_takeover_leaves_the_displaced_albums_other_bindings(
    app: Flask, auth: dict[str, str]
) -> None:
    _seed_folder(app, "holidays", ["a.jpg"])
    _seed_folder(app, "family", ["b.jpg"])
    _register(app, "frame01")
    _register(app, "frame02")
    store = app.config["ALBUM_STORE"]
    store.upsert(
        Album.model_validate(
            {
                "id": "family",
                "name": "Family",
                "device_ids": ["frame01", "frame02"],
                "source_folder": "family",
            }
        )
    )

    resp = _put(app, auth, {"album": _draft(device_ids=["frame01"]), "replace_conflicts": True})
    assert resp.status_code == 201
    assert store.get("holidays").device_ids == ["frame01"]
    displaced = store.get("family")
    assert displaced is not None, "a takeover unbinds a display, it doesn't delete an album"
    assert displaced.device_ids == ["frame02"]


def test_a_disabled_album_claims_nothing(app: Flask, auth: dict[str, str]) -> None:
    _seed_folder(app, "holidays", ["a.jpg"])
    _seed_folder(app, "family", ["b.jpg"])
    _register(app, "frame01")
    app.config["ALBUM_STORE"].upsert(
        Album.model_validate(
            {
                "id": "family",
                "name": "Family",
                "enabled": False,
                "device_ids": ["frame01"],
                "source_folder": "family",
            }
        )
    )
    assert _put(app, auth, {"album": _draft(device_ids=["frame01"])}).status_code == 201


# -- order ---------------------------------------------------------------


def test_order_is_normalized_not_echoed(app: Flask, auth: dict[str, str]) -> None:
    """A cross-folder id is refused outright; an id for a photo deleted
    while the form was open is dropped, so the saved order is shorter than
    the submitted one."""
    _seed_folder(app, "holidays", ["a.jpg", "b.jpg"])
    _seed_folder(app, "family", ["c.jpg"])
    _register(app, "frame01")

    foreign = companion_gallery.image_id("family", "c.jpg")
    resp = _put(app, auth, {"album": _draft(order=[foreign])})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_request"

    gone = companion_gallery.image_id("holidays", "deleted.jpg")
    order = [
        companion_gallery.image_id("holidays", "b.jpg"),
        gone,
        companion_gallery.image_id("holidays", "a.jpg"),
    ]
    body = _create(app, auth, order=order).get_json()
    _validate(body, "OfflineAlbumResponse")
    assert body["album"]["order"] == [
        companion_gallery.image_id("holidays", "b.jpg"),
        companion_gallery.image_id("holidays", "a.jpg"),
    ]
    assert app.config["ALBUM_STORE"].get("holidays").order == ["b.jpg", "a.jpg"]


def test_an_empty_order_stays_empty(app: Flask, auth: dict[str, str]) -> None:
    """Expanding it to the full playback list would report "these frames
    were pinned" for an album saved as "use natural order"."""
    _seed_folder(app, "holidays", ["a.jpg", "b.jpg"])
    _register(app, "frame01")
    body = _create(app, auth).get_json()
    assert body["album"]["order"] == []


# -- plan and observation ------------------------------------------------


def test_a_cold_album_plans_by_frame_count_and_omits_storage(
    app: Flask, auth: dict[str, str]
) -> None:
    """Nothing is warmed, so no bytes exist to budget against. Quoting a
    size here would be inventing one."""
    _seed_folder(app, "holidays", ["a.jpg", "b.jpg"])
    _register(app, "frame01")
    body = _create(app, auth).get_json()
    _validate(body, "OfflineAlbumResponse")

    plan = body["targets"][0]["plan"]
    assert plan["total_frames"] == 2
    assert plan["cacheable_frames"] == 2
    assert plan["accuracy"] == "exact"
    assert plan["fully_offline"] is True
    assert "storage" not in plan
    assert "desired_version" not in body["targets"][0]


def test_a_cold_album_is_capped_by_the_reported_frame_limit(
    app: Flask, auth: dict[str, str]
) -> None:
    _seed_folder(app, "holidays", ["a.jpg", "b.jpg", "c.jpg"])
    _register(
        app,
        "frame01",
        cap={"frame_cache": {"schema": 1, "capacity_bytes": 67_108_864, "max_frames": 2}},
    )
    plan = _create(app, auth).get_json()["targets"][0]["plan"]
    assert plan["cacheable_frames"] == 2
    assert plan["fully_offline"] is False


def test_a_warmed_album_reports_exact_storage_and_a_version(
    app: Flask, auth: dict[str, str]
) -> None:
    _seed_folder(app, "holidays", ["a.jpg", "b.jpg"])
    _register(app, "frame01")
    _create(app, auth)
    _warm(app, "frame01", "a.jpg", b"frame-a")
    _warm(app, "frame01", "b.jpg", b"frame-bbbb")

    body = app.test_client().get(_url(), headers=auth).get_json()
    _validate(body, "OfflineAlbumResponse")
    target = body["targets"][0]
    assert target["plan"]["storage"] == {
        "bytes": len(b"frame-a") + len(b"frame-bbbb"),
        "accuracy": "exact",
    }
    assert target["plan"]["accuracy"] == "exact"
    assert len(target["desired_version"]) == 16


def test_a_partly_warmed_album_still_withholds_storage_and_the_version(
    app: Flask, auth: dict[str, str]
) -> None:
    _seed_folder(app, "holidays", ["a.jpg", "b.jpg"])
    _register(app, "frame01")
    _create(app, auth)
    _warm(app, "frame01", "a.jpg", b"frame-a")

    target = app.test_client().get(_url(), headers=auth).get_json()["targets"][0]
    assert "storage" not in target["plan"]
    assert "desired_version" not in target, "a version that moves while warming is not drift"


def test_a_legacy_observation_carries_only_what_the_device_sent(
    app: Flask, auth: dict[str, str]
) -> None:
    """Older firmware reports a state and nothing else. Filling in zeros
    for the counts would render as real progress."""
    _seed_folder(app, "holidays", ["a.jpg"])
    _register(app, "frame01")
    _create(app, auth)
    app.config["DEVICE_STATUS"]["frame01"]["collection_report"] = {
        "id": "album:holidays",
        "state": "playing",
        "received_at": 1_760_000_000,
    }

    body = app.test_client().get(_url(), headers=auth).get_json()
    _validate(body, "OfflineAlbumResponse")
    observed = body["targets"][0]["observed"]
    assert observed["state"] == "playing"
    assert observed["observed_at"] == "2025-10-09T08:53:20Z"
    assert "cached" not in observed
    assert "total" not in observed
    assert "version" not in observed


def test_a_report_about_another_album_is_not_an_observation_of_this_one(
    app: Flask, auth: dict[str, str]
) -> None:
    _seed_folder(app, "holidays", ["a.jpg"])
    _register(app, "frame01")
    _create(app, auth)
    app.config["DEVICE_STATUS"]["frame01"]["collection_report"] = {
        "id": "album:family",
        "state": "playing",
        "cached": 9,
        "total": 9,
        "received_at": 1_760_000_000,
    }

    body = app.test_client().get(_url(), headers=auth).get_json()
    assert "observed" not in body["targets"][0]


# -- preflight -----------------------------------------------------------


def test_preflight_plans_without_saving_and_reports_conflicts_as_information(
    app: Flask, auth: dict[str, str]
) -> None:
    _seed_folder(app, "holidays", ["a.jpg", "b.jpg"])
    _seed_folder(app, "family", ["c.jpg"])
    _register(app, "frame01")
    app.config["ALBUM_STORE"].upsert(
        Album.model_validate(
            {
                "id": "family",
                "name": "Family",
                "device_ids": ["frame01"],
                "source_folder": "family",
            }
        )
    )

    resp = app.test_client().post(
        f"{_url()}/preflight",
        headers=auth,
        data=json.dumps(_draft()),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    _validate(body, "OfflineAlbumPreflightResponse")
    assert body["folder_id"] == companion_gallery.folder_id("holidays")
    target = body["targets"][0]
    assert target["conflict"]["name"] == "Family", "a conflict here is information, not a refusal"
    assert target["plan"]["total_frames"] == 2
    assert "observed" not in target
    assert "desired_version" not in target
    assert app.config["ALBUM_STORE"].get("holidays") is None, "preflight must not write"


# -- delete --------------------------------------------------------------


def test_delete_is_idempotent_and_keeps_the_photos(app: Flask, auth: dict[str, str]) -> None:
    _seed_folder(app, "holidays", ["a.jpg"])
    _register(app, "frame01")
    _create(app, auth)
    client = app.test_client()

    assert client.delete(_url(), headers=auth).status_code == 204
    assert app.config["ALBUM_STORE"].get("holidays") is None
    assert client.delete(_url(), headers=auth).status_code == 204, (
        "a folder with no album is 204, not a missing folder"
    )

    data_dir = Path(app.config["PLUGIN_REGISTRY"].get("picture_gallery").data_dir)
    assert (data_dir / "holidays" / "a.jpg").exists(), (
        "unbinding a display must not delete source photos"
    )
    folder = client.get(
        f"/api/app/v1/gallery/folders/{companion_gallery.folder_id('holidays')}", headers=auth
    )
    assert folder.status_code == 200


def test_a_missing_folder_is_a_404(app: Flask, auth: dict[str, str]) -> None:
    missing = companion_gallery.folder_id("nope")
    resp = app.test_client().delete(
        f"/api/app/v1/gallery/folders/{missing}/offline-album", headers=auth
    )
    assert resp.status_code == 404
    _validate(resp.get_json(), "ErrorResponse")
