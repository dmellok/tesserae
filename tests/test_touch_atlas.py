"""Touch-v3 glyph atlas builder + /frame/spec attachment + /atlas serve.

Uses a fake rasterizer (a plain grayscale strip + per-glyph boxes) so no headless
browser is needed; the byte content is irrelevant to the descriptor shape.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from jsonschema import Draft202012Validator
from PIL import Image

import app.rest_api as rest_api
from app.main import REPO_ROOT, create_app
from app.state.page_store import Page
from app.state.panel_store import CanvasLayout, Element
from app.touch_atlas import build_touch_atlas

_SCHEMA_DIR = REPO_ROOT / "schema"


def _fake_rasterize(px: int, weight: int, charset: str) -> tuple[bytes, list[dict[str, Any]]]:
    cell = max(4, px // 2)
    img = Image.new("L", (cell * len(charset) + 2, px + 4), 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    boxes = [{"ch": ch, "x": i * cell, "y": 2, "w": cell, "h": px} for i, ch in enumerate(charset)]
    return buf.getvalue(), boxes


# --- unit: the builder ---


def test_build_touch_atlas_descriptor(tmp_path: Path) -> None:
    desc = build_touch_atlas("l20", renders_dir=tmp_path, rasterize=_fake_rasterize)
    assert desc is not None
    assert desc["id"] == "l20" and desc["px"] == 20 and desc["weight"] == 400
    assert desc["format"] == "gray4"
    assert desc["ascent"] + desc["descent"] == desc["strip_h"]
    for ch in "AZaz09.%":
        g = desc["glyphs"][ch]
        assert g["adv"] == g["w"]  # packed cell is advance-wide
    assert (tmp_path / f"touch-atlas-{desc['digest']}.bin").is_file()


def test_unknown_role_is_none(tmp_path: Path) -> None:
    assert build_touch_atlas("zzz", renders_dir=tmp_path, rasterize=_fake_rasterize) is None


# --- endpoint: attach on /frame/spec + serve on /atlas ---


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
    a.config["OVERLAY_ATLAS_RASTERIZER"] = _fake_rasterize
    Path(a.config["RENDERS_DIR"]).mkdir(parents=True, exist_ok=True)
    return a


def _register(app: Flask, client) -> str:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": "e1003", "kind": "esp32_client", "panel_w": 1872, "panel_h": 1404}
        ),
    )
    assert resp.status_code == 201
    return resp.get_json()["device_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    app.config["PAGE_STORE"].save(
        Page(
            id="p1",
            name="Touch",
            layout_kind="canvas",
            canvas=CanvasLayout(
                els=[
                    Element(
                        id="sw",
                        kind="switch",
                        x=10,
                        y=10,
                        w=160,
                        h=80,
                        label="Desk",
                        value_key="ha:light.desk",
                    ),
                    Element(
                        id="sl",
                        kind="slider",
                        x=10,
                        y=120,
                        w=300,
                        h=70,
                        axis="x",
                        value_min=0,
                        value_max=100,
                    ),
                ]
            ),
        )
    )
    monkeypatch.setattr(
        app.config["PUSH_MANAGER"], "latest_render_for", lambda _d: {"digest": "a" * 16}
    )
    monkeypatch.setattr(
        rest_api,
        "_frame_info_for_digest",
        lambda _dev, _dig: {"page_id": "p1", "composition_digest": "c"},
    )


def _atlas_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads((_SCHEMA_DIR / "atlas.schema.json").read_text()))


def test_frame_spec_attaches_valid_atlases(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed(app, monkeypatch)
    doc = client.get("/api/v1/device/e1003/frame/spec", headers=_auth(token)).get_json()
    atlases = doc["atlases"]
    # switch label -> l20, slider value_text -> v28.
    assert sorted(a["id"] for a in atlases) == ["l20", "v28"]
    v = _atlas_validator()
    for a in atlases:
        assert not list(v.iter_errors(a))
        assert a["url"].endswith(f"/atlas/{a['digest']}")


def test_atlas_binary_is_served(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed(app, monkeypatch)
    doc = client.get("/api/v1/device/e1003/frame/spec", headers=_auth(token)).get_json()
    digest = doc["atlases"][0]["digest"]
    resp = client.get(f"/api/v1/device/e1003/atlas/{digest}", headers=_auth(token))
    assert resp.status_code == 200
    assert len(resp.data) > 0
    assert "immutable" in resp.headers.get("Cache-Control", "")


def test_atlas_bad_digest_404(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    assert client.get("/api/v1/device/e1003/atlas/..%2fx", headers=_auth(token)).status_code == 404
    assert client.get("/api/v1/device/e1003/atlas/zzzz", headers=_auth(token)).status_code == 404
