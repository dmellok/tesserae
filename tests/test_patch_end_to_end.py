"""End-to-end wiring of the post-action patch path against a real app:
capability ingestion via /status, a real touch dispatch through the
button service, the reconcile worker, the real diff over real .bin
artifacts, and delivery on /frame/data. Only the Playwright render is
stubbed (``shadow_render_page`` returns a pre-written artifact), so a
break anywhere else in the chain fails here the way the bench does."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.touch_regions import save_regions

W, H = 1872, 1404  # E1003 wire dims, 4bpp gray -> 1_314_144 bytes
STRIDE = W // 2


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
            {"device_id": device_id, "kind": "esp32_client", "panel_w": W, "panel_h": H}
        ),
    )
    assert resp.status_code == 201
    return resp.get_json()["device_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _advertise_schema_2(client, token: str) -> None:
    resp = client.post(
        "/api/v1/device/e1003/status",
        headers=_auth(token),
        data=json.dumps({"overlay": {"schema": 2, "max_targets": 32}}),
    )
    assert resp.status_code == 200


def _write_frames(app: Flask) -> tuple[str, str]:
    """Write an on-glass artifact and a post-action artifact differing in
    one 300x90-px band, returning their digests."""
    import hashlib

    base = np.full((H, STRIDE), 0xFF, dtype=np.uint8)
    changed = base.copy()
    changed[640:730, 64:214] = 0x00  # px x=128..427, y=640..729
    renders = app.config["RENDERS_DIR"]
    digests = []
    for arr in (base, changed):
        blob = arr.tobytes()
        digest = hashlib.sha256(blob).hexdigest()[:16]
        (renders / f"{digest}.bin").write_bytes(blob)
        digests.append(digest)
    return digests[0], digests[1]


def _seed_live(app: Flask, digest: str, *, comp: str, page_id: str) -> None:
    app.config["PUSH_MANAGER"]._latest_renders["e1003"] = {
        "digest": digest,
        "ext": "bin",
        "filename": f"{digest}.bin",
        "composition_digest": comp,
        "page_id": page_id,
        "timestamp": 1000.0,
    }


HA_REGION = {
    "x": 100,
    "y": 100,
    "w": 400,
    "h": 300,
    "depth": 1,
    "order": 0,
    "tap": {"action": "ha", "domain": "light", "service": "toggle", "data": {}},
    "swipe": None,
    "slide": None,
    "origin": "config",
    "dangling": [],
    "invalid": [],
}


def test_tap_to_patch_document_end_to_end(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003")
    _advertise_schema_2(client, token)

    old_digest, new_digest = _write_frames(app)
    comp = "c" * 16
    _seed_live(app, old_digest, comp=comp, page_id="lights")
    save_regions(app.config["RENDERS_DIR"], comp, [HA_REGION])

    # Stub the two externals only: the HA call and the Playwright render.
    # The shadow render reports a NEW composition digest with no PNG on
    # disk, so the payload builder exercises its wire-diff fallback
    # (composition-diff coverage lives in test_push_patch_divert.py).
    svc = app.config["BUTTON_SERVICE"]
    ha_calls: list[tuple[str, str]] = []
    svc._call_ha = lambda domain, service, data: ha_calls.append((domain, service))
    pm = app.config["PUSH_MANAGER"]
    pm.shadow_render_page = lambda page_id, device_id: {
        "digest": new_digest,
        "ext": "bin",
        "filename": f"{new_digest}.bin",
        "composition_digest": "d" * 16,
        "page_id": page_id,
        "timestamp": 2000.0,
    }
    # Run the worker synchronously with a zero debounce.
    app.config["SETTINGS_STORE"].update_section("app", {"touch_patch_debounce_s": 0})
    spawned: list[str] = []
    svc._spawn_reconcile = spawned.append

    # The tap arrives exactly the way firmware sends it: on the /frame wake.
    resp = client.get(
        f"/api/v1/device/e1003/frame?touch_x0=150&touch_y0=150&touch_x1=150&touch_y1=150"
        f"&touch_digest={old_digest}&touch_event_id=917354862",
        headers={**_auth(token), "If-None-Match": f'"{old_digest}"'},
    )
    assert resp.status_code == 304  # digest unchanged on the wake itself
    assert ha_calls == [("light", "toggle")]
    assert spawned == ["e1003"]
    svc._reconcile_worker("e1003")

    # The live digest must NOT have moved; the patch is the repaint.
    assert pm.latest_render_for("e1003")["digest"] == old_digest

    data = client.get(f"/api/v1/device/e1003/frame/data?digest={old_digest}", headers=_auth(token))
    assert data.status_code == 200
    patches = data.get_json()["patches"]
    assert patches["schema"] == 2
    assert patches["frame_digest"] == old_digest
    assert patches["seq"] > 1_700_000_000_000  # milliseconds, not seconds
    rects = patches["rects"]
    assert len(rects) == 1
    r = rects[0]
    assert (r["x"], r["y"], r["w"], r["h"]) == (128, 640, 300, 90)
    assert r["len"] == 300 * 90 // 2
    assert patches["bytes"] == r["len"]

    blob_digest = patches["url"].rsplit("/", 1)[-1]
    blob = client.get(f"/api/v1/device/e1003/frame/patch/{blob_digest}", headers=_auth(token))
    assert blob.status_code == 200
    assert blob.data == b"\x00" * (300 * 90 // 2)
    blob.close()


def test_second_tap_with_same_digest_still_dispatches(app: Flask) -> None:
    """After a patch is staged the digest stays put, so a follow-up tap
    carrying the same digest must dispatch (the bench's rapid-tap loss
    came from digest churn)."""
    client = app.test_client()
    token = _register(app, client, "e1003")
    _advertise_schema_2(client, token)
    old_digest, new_digest = _write_frames(app)
    comp = "c" * 16
    _seed_live(app, old_digest, comp=comp, page_id="lights")
    save_regions(app.config["RENDERS_DIR"], comp, [HA_REGION])
    svc = app.config["BUTTON_SERVICE"]
    ha_calls: list[Any] = []
    svc._call_ha = lambda domain, service, data: ha_calls.append(domain)
    pm = app.config["PUSH_MANAGER"]
    pm.shadow_render_page = lambda page_id, device_id: {
        "digest": new_digest,
        "ext": "bin",
        "filename": f"{new_digest}.bin",
        "composition_digest": "d" * 16,
        "page_id": page_id,
        "timestamp": 2000.0,
    }
    app.config["SETTINGS_STORE"].update_section("app", {"touch_patch_debounce_s": 0})
    svc._spawn_reconcile = lambda d: None

    for event_id in (5, 6):
        resp = client.get(
            f"/api/v1/device/e1003/frame?touch_x0=150&touch_y0=150&touch_x1=150&touch_y1=150"
            f"&touch_digest={old_digest}&touch_event_id={event_id}",
            headers={**_auth(token), "If-None-Match": f'"{old_digest}"'},
        )
        assert resp.status_code == 304
        svc._reconcile_worker("e1003")
    assert ha_calls == ["light", "light"]
