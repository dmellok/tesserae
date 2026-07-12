"""MCP API (agentic canvas dashboards): the token-authed /api/mcp surface.

Covers the experiment gate, token-or-loopback auth, the canvas create/get/set
roundtrip + 422 validation, the grid-page guardrail, and the preview/push paths
(with the renderer + push manager mocked so no Chromium is spun up).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.push import PushResult
from app.state.page_store import Page

_FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-bytes"
_REMOTE = {"REMOTE_ADDR": "203.0.113.9"}  # a non-loopback client


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


def _enable(app: Flask) -> None:
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})


def _set_token(app: Flask, token: str) -> None:
    app.config["SETTINGS_STORE"].patch_section("app", {"mcp_token_secret": token})


def _create_page(client: Any, name: str = "Agent board") -> str:
    resp = client.post("/api/mcp/pages", json={"name": name, "w": 800, "h": 480})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return str(resp.get_json()["id"])


# -- gate + auth --------------------------------------------------------


def test_404_when_experiment_off(app: Flask) -> None:
    # Default: mcp experiment off → the whole surface 404s.
    assert app.test_client().get("/api/mcp/catalog").status_code == 404


def test_loopback_allowed_without_token(app: Flask) -> None:
    _enable(app)
    resp = app.test_client().get("/api/mcp/catalog")
    assert resp.status_code == 200
    assert "widgets" in resp.get_json()


def test_remote_without_token_401(app: Flask) -> None:
    _enable(app)
    resp = app.test_client().get("/api/mcp/catalog", environ_overrides=_REMOTE)
    assert resp.status_code == 401


def test_remote_with_token_ok(app: Flask) -> None:
    _enable(app)
    _set_token(app, "s3cret-token")
    resp = app.test_client().get(
        "/api/mcp/catalog",
        headers={"Authorization": "Bearer s3cret-token"},
        environ_overrides=_REMOTE,
    )
    assert resp.status_code == 200


def test_remote_with_bad_token_401(app: Flask) -> None:
    _enable(app)
    _set_token(app, "s3cret-token")
    resp = app.test_client().get(
        "/api/mcp/catalog",
        headers={"Authorization": "Bearer wrong"},
        environ_overrides=_REMOTE,
    )
    assert resp.status_code == 401


# -- discovery ----------------------------------------------------------


def test_catalog_and_widget_options(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    cat = client.get("/api/mcp/catalog").get_json()
    assert isinstance(cat["widgets"], list) and cat["widgets"]
    assert "appearance" in cat
    key = str(cat["widgets"][0]["key"])
    opts = client.get(f"/api/mcp/widgets/{key}/options")
    assert opts.status_code == 200
    assert opts.get_json()["key"] == key
    assert client.get("/api/mcp/widgets/not_a_widget/options").status_code == 404


def test_devices_shape(app: Flask) -> None:
    _enable(app)
    body = app.test_client().get("/api/mcp/devices").get_json()
    assert "devices" in body and isinstance(body["devices"], list)


# -- canvas CRUD --------------------------------------------------------


def test_create_get_set_roundtrip(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)

    doc = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()
    assert doc["w"] == 800 and doc["h"] == 480 and doc["els"] == []

    new_doc = {
        "w": 800,
        "h": 480,
        "els": [{"id": "e1", "kind": "text", "text": "Hi", "x": 10, "y": 10, "w": 200, "h": 60}],
    }
    saved = client.put(f"/api/mcp/pages/{pid}/canvas", json=new_doc)
    assert saved.status_code == 200
    assert saved.get_json()["elements"] == 1  # compact ack

    again = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()
    assert again["els"][0]["text"] == "Hi"

    # Marked as agent-made and listed.
    pages = client.get("/api/mcp/pages").get_json()["pages"]
    entry = next(p for p in pages if p["id"] == pid)
    assert entry["created_by"] == "mcp" and entry["elements"] == 1


def test_set_canvas_compact_and_return_doc(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    body = {
        "w": 800,
        "h": 480,
        "els": [{"id": "e1", "kind": "rect", "x": 0, "y": 0, "w": 20, "h": 20}],
    }
    # Default: compact ack, no document echoed.
    ack = client.put(f"/api/mcp/pages/{pid}/canvas", json=body).get_json()
    assert ack["ok"] is True and ack["elements"] == 1 and ack["rev"] and "els" not in ack
    # Opt-in: full document.
    doc = client.put(f"/api/mcp/pages/{pid}/canvas?return=doc", json=body).get_json()
    assert "els" in doc and len(doc["els"]) == 1


def test_append_element_saves_incrementally(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    r1 = client.post(
        f"/api/mcp/pages/{pid}/elements",
        json={"kind": "text", "text": "hi", "x": 5, "y": 5, "w": 80, "h": 30},
    )
    assert r1.status_code == 200
    j1 = r1.get_json()
    assert j1["ok"] and j1["elements"] == 1 and j1["element_id"]
    r2 = client.post(
        f"/api/mcp/pages/{pid}/elements", json={"kind": "rect", "x": 0, "y": 0, "w": 40, "h": 40}
    )
    assert r2.get_json()["elements"] == 2  # accumulates
    # Bad element → 422.
    assert (
        client.post(f"/api/mcp/pages/{pid}/elements", json={"kind": "rect", "w": 0}).status_code
        == 422
    )


def test_probe_widget_data_returns_payload(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    resp = client.post("/api/mcp/widgets/weather_now/data", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["key"] == "weather_now" and "data" in body
    assert client.post("/api/mcp/widgets/not_a_widget/data", json={}).status_code == 404


def test_catalog_omits_samples(app: Flask) -> None:
    _enable(app)
    cat = app.test_client().get("/api/mcp/catalog").get_json()
    assert cat["widgets"] and all("sample" not in w for w in cat["widgets"])


def test_set_invalid_returns_422(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    # w must be > 0 → a validation error with field-level details.
    resp = client.put(f"/api/mcp/pages/{pid}/canvas", json={"w": 0})
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"] == "invalid canvas document"
    assert isinstance(body["details"], list) and body["details"]


def test_grid_page_write_rejected(app: Flask) -> None:
    _enable(app)
    # A grid dashboard is not a canvas → MCP writes/reads 404 (guardrail).
    app.config["PAGE_STORE"].save(Page(id="grid1", name="Grid", layout_kind="grid"))
    client = app.test_client()
    assert client.get("/api/mcp/pages/grid1/canvas").status_code == 404
    assert client.put("/api/mcp/pages/grid1/canvas", json={"w": 800}).status_code == 404


# -- preview + push (renderer / push manager mocked) --------------------


def test_preview_returns_png(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    monkeypatch.setattr("app.renderer.render_to_png", lambda req, pool=None: _FAKE_PNG)
    client = app.test_client()
    pid = _create_page(client)
    resp = client.get(f"/api/mcp/pages/{pid}/preview.png")
    assert resp.status_code == 200 and resp.mimetype == "image/png"
    assert resp.get_data() == _FAKE_PNG


def test_push_requires_device_ids(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    assert client.post(f"/api/mcp/pages/{pid}/push", json={}).status_code == 400


def test_push_ok(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    monkeypatch.setattr("app.renderer.render_to_png", lambda req, pool=None: _FAKE_PNG)
    pm = MagicMock()
    pm.push_image.return_value = PushResult(status="sent", page_id="c", composition_digest="d")
    app.config["PUSH_MANAGER"] = pm
    client = app.test_client()
    pid = _create_page(client)
    resp = client.post(f"/api/mcp/pages/{pid}/push", json={"device_ids": ["dev1"]})
    assert resp.status_code == 200
    assert resp.get_json()["sent"] == ["dev1"]
    (payload,) = pm.push_image.call_args.args
    assert payload == _FAKE_PNG
