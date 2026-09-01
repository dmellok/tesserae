"""Composition-space patch payloads and the push-to-patch divert that
holds a device's digest stable across small-diff renders (the
header-clock case): payload building from real comp PNGs + artifacts,
the settings-changed fallback, the divert gate conditions, and the
overlay-capability restart seed."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from flask import Flask
from PIL import Image

from app.main import REPO_ROOT, create_app
from app.state.page_store import Panel

W, H = 1872, 1404
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


def _register(app: Flask, client, device_id: str = "e1003") -> str:
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


def _write_artifact(app: Flask, arr: np.ndarray) -> str:
    blob = arr.tobytes()
    digest = hashlib.sha256(blob).hexdigest()[:16]
    (app.config["RENDERS_DIR"] / f"{digest}.bin").write_bytes(blob)
    return digest


def _write_comp(app: Flask, arr: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    png = buf.getvalue()
    digest = hashlib.sha256(png).hexdigest()[:16]
    (app.config["RENDERS_DIR"] / f"{digest}.png").write_bytes(png)
    return digest


def _entries(app: Flask, *, dithered_globally: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """An anchor entry + a new render whose composition differs in one
    small band. ``dithered_globally`` makes the two ARTIFACTS differ in
    noise everywhere (what floyd-steinberg does), which is exactly the
    case the composition diff must see through."""
    rng_a = np.zeros((H, STRIDE), dtype=np.uint8)
    # dithered_globally = every byte differs, mimicking error diffusion.
    rng_b = (rng_a + 1) % 251 if dithered_globally else rng_a.copy()
    rng_b[640:730, 64:214] = 0xEE  # the "real" change band

    comp_a = np.full((H, W, 3), 255, dtype=np.uint8)
    comp_b = comp_a.copy()
    comp_b[640:730, 128:428] = 0  # comp-space band matching the artifact change

    anchor = {
        "digest": _write_artifact(app, rng_a),
        "ext": "bin",
        "filename": "",
        "composition_digest": _write_comp(app, comp_a),
        "page_id": "lights",
        "timestamp": 1000.0,
    }
    anchor["filename"] = f"{anchor['digest']}.bin"
    info = {
        "digest": _write_artifact(app, rng_b),
        "ext": "bin",
        "filename": "",
        "composition_digest": _write_comp(app, comp_b),
        "page_id": "lights",
        "timestamp": 2000.0,
    }
    info["filename"] = f"{info['digest']}.bin"
    return anchor, info


PANEL = {"w": W, "h": H}


def test_payload_from_composition_diff_survives_global_dither_noise(app: Flask) -> None:
    anchor, info = _entries(app, dithered_globally=True)
    pm = app.config["PUSH_MANAGER"]
    payload = pm._build_patch_payload(anchor, info, PANEL)
    assert not isinstance(payload, str), payload
    blob, entries = payload
    assert len(entries) == 1
    r = entries[0]
    # Comp band x=128..427 y=640..729, padded by 2 and byte-aligned.
    assert (r["x"], r["y"], r["w"], r["h"]) == (126, 638, 304, 94)
    assert r["len"] == 304 * 94 // 2 == len(blob)


def test_payload_settings_change_falls_back_to_full_frame(app: Flask) -> None:
    """Same composition, different artifact = renderer settings changed;
    the whole frame legitimately repaints."""
    anchor, info = _entries(app, dithered_globally=True)
    info = {**info, "composition_digest": anchor["composition_digest"]}
    pm = app.config["PUSH_MANAGER"]
    assert pm._build_patch_payload(anchor, info, PANEL) == "render_settings_changed"


def test_payload_over_budget_reports_fallback(app: Flask) -> None:
    """A composition change covering most of the frame ships as a full
    frame, not a patch."""
    comp_a = np.full((H, W, 3), 255, dtype=np.uint8)
    comp_b = np.zeros((H, W, 3), dtype=np.uint8)
    art_a = np.zeros((H, STRIDE), dtype=np.uint8)
    art_b = np.full((H, STRIDE), 0xEE, dtype=np.uint8)
    anchor = {
        "digest": _write_artifact(app, art_a),
        "filename": "",
        "composition_digest": _write_comp(app, comp_a),
    }
    anchor["filename"] = f"{anchor['digest']}.bin"
    info = {
        "digest": _write_artifact(app, art_b),
        "filename": "",
        "composition_digest": _write_comp(app, comp_b),
    }
    info["filename"] = f"{info['digest']}.bin"
    pm = app.config["PUSH_MANAGER"]
    outcome = pm._build_patch_payload(anchor, info, PANEL)
    assert isinstance(outcome, str) and outcome.startswith("over_budget")


# -- the push divert (digest stability) -----------------------------------


def _renderer_for(app: Flask, device_id: str):
    return next(r for r in app.config["RENDERER_REGISTRY"].all() if r.device == device_id)


def _advertise(client, token: str, schema: int) -> None:
    resp = client.post(
        "/api/v1/device/e1003/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"overlay": {"schema": schema, "max_targets": 32}}),
    )
    assert resp.status_code == 200


def test_divert_holds_digest_and_stages_patches(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    _advertise(client, token, 2)
    anchor, info = _entries(app, dithered_globally=True)
    pm = app.config["PUSH_MANAGER"]
    pm._latest_renders["e1003"] = dict(anchor)
    renderer = _renderer_for(app, "e1003")

    with pm._lock:
        assert pm._divert_to_patches_locked(renderer, info, Panel(w=W, h=H)) is True
    assert pm.latest_render_for("e1003")["digest"] == anchor["digest"]  # digest held
    doc = pm.frame_patches_for("e1003", anchor["digest"])
    assert doc is not None and doc["rects"]


def test_divert_refuses_cross_page_and_schema_1(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    anchor, info = _entries(app, dithered_globally=False)
    pm = app.config["PUSH_MANAGER"]
    renderer = _renderer_for(app, "e1003")

    # Schema 1: never diverted.
    _advertise(client, token, 1)
    pm._latest_renders["e1003"] = dict(anchor)
    with pm._lock:
        assert pm._divert_to_patches_locked(renderer, info, Panel(w=W, h=H)) is False

    # Schema 2 but a different page: the on-glass touch regions would
    # not match the patched pixels.
    _advertise(client, token, 2)
    with pm._lock:
        assert (
            pm._divert_to_patches_locked(renderer, {**info, "page_id": "other"}, Panel(w=W, h=H))
            is False
        )


def test_overlay_capability_survives_restart(tmp_path: Path) -> None:
    """A patch-capable panel must not be demoted to full-repaint
    reconciles by a server restart: the capability persists in the facts
    store and re-seeds the status cache at startup."""
    first = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    first.config["TESTING"] = True
    client = first.test_client()
    token = _register(first, client)
    _advertise(client, token, 2)
    assert first.config["DEVICE_FACTS"].get("e1003")["overlay"]["schema"] == 2

    second = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    status = second.config["DEVICE_STATUS"].get("e1003")
    assert status is not None
    assert status["overlay"] == {"schema": 2, "max_targets": 32}


# -- promote-on-poll fallback (#271) ---------------------------------------


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _diverted(app: Flask, client, token: str):
    """Stage a divert: anchor live, patch doc staged, full render parked."""
    _advertise(client, token, 2)
    anchor, info = _entries(app, dithered_globally=True)
    pm = app.config["PUSH_MANAGER"]
    pm._latest_renders["e1003"] = dict(anchor)
    renderer = _renderer_for(app, "e1003")
    with pm._lock:
        assert pm._divert_to_patches_locked(renderer, info, Panel(w=W, h=H)) is True
    return pm, anchor, info


def test_divert_parks_the_full_render(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    pm, _anchor, info = _diverted(app, client, token)
    held = pm.held_render_for("e1003")
    assert held is not None and held["digest"] == info["digest"]


def test_unfetched_patch_promotes_on_the_next_frame_poll(app: Flask) -> None:
    """A deep-sleep device can't composite patches on a timer wake; its
    /frame poll must serve the diverted content as a full frame instead
    of 304ing on the held anchor forever (#271)."""
    client = app.test_client()
    token = _register(app, client)
    pm, anchor, info = _diverted(app, client, token)

    resp = client.get(
        "/api/v1/device/e1003/frame",
        headers={**_auth(token), "If-None-Match": f'"{anchor["digest"]}"'},
    )
    assert resp.status_code == 200
    assert resp.get_json()["render_id"] == info["digest"]
    assert pm.latest_render_for("e1003")["digest"] == info["digest"]
    # The doc anchored to the retired frame went with it.
    assert pm.frame_patches_for("e1003", anchor["digest"]) is None
    assert pm.held_render_for("e1003") is None
    # Next poll of the promoted frame settles back to 304.
    resp = client.get(
        "/api/v1/device/e1003/frame",
        headers={**_auth(token), "If-None-Match": f'"{info["digest"]}"'},
    )
    assert resp.status_code == 304


def test_fetched_patch_blob_suppresses_the_promote(app: Flask) -> None:
    """A lingering / SSE device that downloaded the patch blob converged
    via patches; its next poll must keep 304ing on the anchor rather
    than paying a redundant full download + repaint."""
    client = app.test_client()
    token = _register(app, client)
    pm, anchor, _info = _diverted(app, client, token)

    blob_digest = pm._patch_docs["e1003"]["blob_digest"]
    resp = client.get(f"/api/v1/device/e1003/frame/patch/{blob_digest}", headers=_auth(token))
    assert resp.status_code == 200
    assert len(resp.data) > 0
    resp.close()
    assert pm.held_render_for("e1003") is None

    resp = client.get(
        "/api/v1/device/e1003/frame",
        headers={**_auth(token), "If-None-Match": f'"{anchor["digest"]}"'},
    )
    assert resp.status_code == 304
    assert pm.latest_render_for("e1003")["digest"] == anchor["digest"]
    # The doc survives for the linger loop's 1 s re-delivery.
    assert pm.frame_patches_for("e1003", anchor["digest"]) is not None


def test_preview_serves_the_parked_composition(app: Flask) -> None:
    """/preview (and the /mirror page embedding it) must show the newest
    accepted content while the live slot is deliberately held at the
    patch anchor, matching what the History tab shows (#271)."""
    client = app.test_client()
    token = _register(app, client)
    _pm, _anchor, info = _diverted(app, client, token)

    resp = client.get("/preview/e1003.png")
    assert resp.status_code == 200
    expected = (app.config["RENDERS_DIR"] / f"{info['composition_digest']}.png").read_bytes()
    body = resp.data
    resp.close()
    assert body == expected


def test_revert_to_anchor_drops_the_unfetched_patch_and_parked_render(app: Flask) -> None:
    """Content that changes and then reverts within one sleep cycle: the
    device never fetched the patch, so both the doc and the parked
    render are stale and promoting either would paint departed state."""
    client = app.test_client()
    token = _register(app, client)
    pm, anchor, _info = _diverted(app, client, token)

    # A third render whose composition is back at (within tolerance of)
    # the anchor: every pixel within COMP_DIFF_TOLERANCE per channel.
    comp_r = np.full((H, W, 3), 251, dtype=np.uint8)
    art_r = np.full((H, STRIDE), 7, dtype=np.uint8)
    revert = {
        "digest": _write_artifact(app, art_r),
        "ext": "bin",
        "filename": "",
        "composition_digest": _write_comp(app, comp_r),
        "page_id": "lights",
        "timestamp": 3000.0,
    }
    revert["filename"] = f"{revert['digest']}.bin"
    renderer = _renderer_for(app, "e1003")
    with pm._lock:
        assert pm._divert_to_patches_locked(renderer, revert, Panel(w=W, h=H)) is True
    assert pm.frame_patches_for("e1003", anchor["digest"]) is None
    assert pm.held_render_for("e1003") is None
    # And the poll 304s: nothing to promote, nothing changed visually.
    resp = client.get(
        "/api/v1/device/e1003/frame",
        headers={**_auth(token), "If-None-Match": f'"{anchor["digest"]}"'},
    )
    assert resp.status_code == 304


def test_refresh_mode_full_disables_the_divert(app: Flask) -> None:
    """The per-device "Always full refresh" setting (#271): every visible
    change stamps normally, no patch delivery for this device."""
    client = app.test_client()
    token = _register(app, client)
    _advertise(client, token, 2)
    store = app.config["SETTINGS_STORE"]
    store.patch_section("devices", {"e1003": {"refresh_mode": "full"}})

    anchor, info = _entries(app, dithered_globally=True)
    pm = app.config["PUSH_MANAGER"]
    pm._latest_renders["e1003"] = dict(anchor)
    renderer = _renderer_for(app, "e1003")
    with pm._lock:
        assert pm._divert_to_patches_locked(renderer, info, Panel(w=W, h=H)) is False


def _comp_png(band: bool) -> bytes:
    arr = np.full((H, W, 3), 255, dtype=np.uint8)
    if band:
        arr[640:730, 128:428] = 0
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def test_fan_out_divert_writes_through_the_deck_cache(app: Flask) -> None:
    """A diverted render must still replace this page's warmed deck
    frame, or a later promote_deck_page reverts the panel to a frame
    even older than the patch anchor (#271, same class as #266). Runs
    the real _fan_out through the real esp32 renderer twice: the first
    stamps the live slot, the second small-diff render diverts."""
    client = app.test_client()
    token = _register(app, client)
    _advertise(client, token, 2)
    pm = app.config["PUSH_MANAGER"]

    first = pm._fan_out(
        _comp_png(band=False),
        {"w": W, "h": H},
        source="page",
        target="lights",
        started=0.0,
        device_filters={"e1003"},
        page_id="lights",
    )
    assert first.status == "sent"
    live = pm.latest_render_for("e1003")
    assert live is not None
    pm._deck_renders["e1003"] = {"lights": dict(live)}

    second = pm._fan_out(
        _comp_png(band=True),
        {"w": W, "h": H},
        source="page",
        target="lights",
        started=0.0,
        device_filters={"e1003"},
        page_id="lights",
    )
    assert second.status == "sent"
    # The live slot held at the anchor (the divert fired)...
    assert pm.latest_render_for("e1003")["digest"] == live["digest"]
    held = pm.held_render_for("e1003")
    assert held is not None and held["digest"] != live["digest"]
    # ...but the warmed deck frame carries the fresh render.
    assert pm._deck_renders["e1003"]["lights"]["digest"] == held["digest"]
