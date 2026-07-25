"""REST delivery of post-action frame patches (overlay schema 2): the
``patches`` block on ``GET /frame/data``, the ``overlay_patches``
sibling on ``/status``, the content-addressed blob endpoint, and the
PushManager patch store's digest anchoring + recency guard."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app


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


def _register(app: Flask, client, device_id: str) -> str:
    _sign_in(client)
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": device_id,
                "kind": "esp32_client",
                "panel_w": 1872,
                "panel_h": 1404,
            }
        ),
    )
    assert resp.status_code == 201
    return resp.get_json()["device_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _seed_frame(app: Flask, device_id: str, *, digest: str) -> None:
    app.config["PUSH_MANAGER"]._latest_renders[device_id] = {
        "digest": digest,
        "ext": "bin",
        "filename": f"{digest}.bin",
        "composition_digest": "c" * 16,
    }


def _seed_patch(app: Flask, device_id: str, *, anchor: str, blob: bytes = b"\xab" * 64) -> str:
    """Install a patch document + blob the way ``reconcile_via_patches``
    would, returning the blob digest."""
    pm = app.config["PUSH_MANAGER"]
    blob_digest = hashlib.sha256(blob).hexdigest()[:16]
    (app.config["RENDERS_DIR"] / f"overlay-patch-{blob_digest}.bin").write_bytes(blob)
    with pm._lock:
        pm._patch_seq += 1
        pm._patch_docs[device_id] = {
            "schema": 2,
            "frame_digest": anchor,
            "seq": pm._patch_seq,
            "format": "fb-rect",
            "url": f"/api/v1/device/{device_id}/frame/patch/{blob_digest}",
            "bytes": len(blob),
            "rects": [{"x": 0, "y": 0, "w": 32, "h": 4, "offset": 0, "len": len(blob)}],
            "blob_digest": blob_digest,
        }
    return blob_digest


# -- /frame/data ----------------------------------------------------------


def test_frame_data_serves_patch_doc_for_anchored_digest(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003")
    _seed_frame(app, "e1003", digest="a" * 16)
    _seed_patch(app, "e1003", anchor="a" * 16)

    resp = client.get(f"/api/v1/device/e1003/frame/data?digest={'a' * 16}", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["values"] == {}  # page has no slots; patches ride alone
    patches = body["patches"]
    assert patches["schema"] == 2
    assert patches["frame_digest"] == "a" * 16
    assert patches["format"] == "fb-rect"
    assert patches["rects"][0]["len"] == 64
    assert "blob_digest" not in patches  # internal bookkeeping stays internal


def test_frame_data_never_hands_out_patches_for_other_frames(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003")
    _seed_frame(app, "e1003", digest="a" * 16)
    _seed_patch(app, "e1003", anchor="a" * 16)

    resp = client.get(f"/api/v1/device/e1003/frame/data?digest={'b' * 16}", headers=_auth(token))
    assert resp.status_code == 404


def test_frame_data_empty_200_when_nothing_pending(app: Flask) -> None:
    """A known digest with no slots and no staged patches answers an
    empty document, not 404: a device that latched data-off from a 404
    mid-linger would miss the patch a tap stages a second later."""
    client = app.test_client()
    token = _register(app, client, "e1003")
    _seed_frame(app, "e1003", digest="a" * 16)
    resp = client.get(f"/api/v1/device/e1003/frame/data?digest={'a' * 16}", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["values"] == {} and "patches" not in body
    assert body["seq"] > 1_700_000_000_000


# -- blob endpoint --------------------------------------------------------


def test_patch_blob_is_content_addressed_and_immutable(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003")
    _seed_frame(app, "e1003", digest="a" * 16)
    blob_digest = _seed_patch(app, "e1003", anchor="a" * 16, blob=b"\x01\x02\x03\x04")

    resp = client.get(f"/api/v1/device/e1003/frame/patch/{blob_digest}", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.data == b"\x01\x02\x03\x04"
    assert "immutable" in resp.headers.get("Cache-Control", "")
    resp.close()

    missing = client.get(f"/api/v1/device/e1003/frame/patch/{'f' * 16}", headers=_auth(token))
    assert missing.status_code == 404


def test_patch_blob_requires_token(app: Flask) -> None:
    client = app.test_client()
    _register(app, client, "e1003")
    assert client.get(f"/api/v1/device/e1003/frame/patch/{'a' * 16}").status_code == 401


# -- /status piggyback ----------------------------------------------------


def _post_status(client, token: str, body: dict[str, Any]):
    return client.post("/api/v1/device/e1003/status", headers=_auth(token), data=json.dumps(body))


def test_status_piggybacks_patches_for_schema_2(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003")
    _seed_frame(app, "e1003", digest="a" * 16)
    _seed_patch(app, "e1003", anchor="a" * 16)

    resp = _post_status(client, token, {"overlay": {"schema": 2, "max_targets": 32}})
    assert resp.status_code == 200
    patches = resp.get_json().get("overlay_patches")
    assert patches is not None and patches["frame_digest"] == "a" * 16


def test_status_omits_patches_for_schema_1(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003")
    _seed_frame(app, "e1003", digest="a" * 16)
    _seed_patch(app, "e1003", anchor="a" * 16)

    resp = _post_status(client, token, {"overlay": {"schema": 1}})
    assert resp.status_code == 200
    assert "overlay_patches" not in resp.get_json()


# -- store anchoring + recency guard --------------------------------------


def test_promote_shadow_drops_patches_and_respects_recency(app: Flask) -> None:
    pm = app.config["PUSH_MANAGER"]
    _seed_frame(app, "e1003", digest="a" * 16)
    blob_digest = _seed_patch(app, "e1003", anchor="a" * 16)
    blob_path = app.config["RENDERS_DIR"] / f"overlay-patch-{blob_digest}.bin"

    shadow = {"digest": "b" * 16, "ext": "bin", "filename": f"{'b' * 16}.bin"}
    assert pm._promote_shadow("e1003", "a" * 16, shadow) == "promoted"
    assert pm.latest_render_for("e1003")["digest"] == "b" * 16
    # The patch anchored to the old frame died with it, blob included.
    assert pm.frame_patches_for("e1003", "a" * 16) is None
    assert not blob_path.exists()

    # A shadow diffed against a frame the device has already moved past
    # must never revert the newer frame.
    stale = {"digest": "d" * 16, "ext": "bin", "filename": f"{'d' * 16}.bin"}
    assert pm._promote_shadow("e1003", "a" * 16, stale) == "superseded"
    assert pm.latest_render_for("e1003")["digest"] == "b" * 16


def test_new_live_frame_invalidates_pending_patches(app: Flask) -> None:
    pm = app.config["PUSH_MANAGER"]
    _seed_frame(app, "e1003", digest="a" * 16)
    _seed_patch(app, "e1003", anchor="a" * 16)
    with pm._lock:
        pm._latest_renders["e1003"] = {"digest": "e" * 16, "ext": "bin"}
        pm._drop_patches_locked("e1003", keep_digest="e" * 16)
    assert pm.frame_patches_for("e1003", "a" * 16) is None
    assert pm.frame_patches_for("e1003", "e" * 16) is None
