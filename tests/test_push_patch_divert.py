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
