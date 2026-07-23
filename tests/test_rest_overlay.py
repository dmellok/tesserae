"""REST overlay endpoint tests: GET /frame/overlay/<digest> serving
rect-only schema-1 specs, plus sticky overlay capability ingestion on
/status. Renders and region sidecars are injected directly (no
Playwright), mirroring tests/test_rest_deck.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.deck_model import Deck, DeckLink, DeckPage
from app.touch_regions import save_regions


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


def _seed_frame(app: Flask, device_id: str, *, digest: str, comp_digest: str) -> None:
    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders[device_id] = {
        "digest": digest,
        "ext": "bin",
        "filename": f"{digest}.bin",
        "composition_digest": comp_digest,
    }


def _seed_regions(app: Flask, comp_digest: str, regions: list[dict[str, Any]]) -> None:
    save_regions(app.config["RENDERS_DIR"], comp_digest, regions)


REGION = {"x": 120, "y": 640, "w": 300, "h": 90, "on_tap": "refresh"}


def test_overlay_requires_token(app: Flask) -> None:
    client = app.test_client()
    _register_esp32(app, client, "e1003")
    assert client.get(f"/api/v1/device/e1003/frame/overlay/{'a' * 16}").status_code == 401


def test_overlay_404_on_unknown_digest(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "e1003")
    resp = client.get(f"/api/v1/device/e1003/frame/overlay/{'a' * 16}", headers=_auth(token))
    assert resp.status_code == 404


def test_overlay_serves_rect_only_spec_for_live_frame(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "e1003")
    _seed_frame(app, "e1003", digest="a" * 16, comp_digest="c" * 16)
    _seed_regions(app, "c" * 16, [REGION])

    resp = client.get(f"/api/v1/device/e1003/frame/overlay/{'a' * 16}", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["schema"] == 1
    assert body["frame_digest"] == "a" * 16
    assert body["targets"] == [
        {"id": "t1", "x": 120, "y": 640, "w": 300, "h": 90, "echo": "invert"}
    ]


def test_overlay_empty_targets_when_frame_has_no_regions(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "e1003")
    _seed_frame(app, "e1003", digest="a" * 16, comp_digest="c" * 16)

    resp = client.get(f"/api/v1/device/e1003/frame/overlay/{'a' * 16}", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.get_json()["targets"] == []


def test_overlay_resolves_deck_cached_frames(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "e1003")
    app.config["DECK_STORE"].upsert(
        Deck(
            id="hall",
            name="Hall",
            device_ids=["e1003"],
            pages=[
                DeckPage(page_id="p1", links=[DeckLink(target_page_id="p2", button="right")]),
                DeckPage(page_id="p2", links=[]),
            ],
        )
    )
    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._deck_renders.setdefault("e1003", {})["p2"] = {
        "digest": "b" * 16,
        "ext": "bin",
        "filename": f"{'b' * 16}.bin",
        "composition_digest": "d" * 16,
    }
    _seed_regions(app, "d" * 16, [REGION])

    resp = client.get(f"/api/v1/device/e1003/frame/overlay/{'b' * 16}", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.get_json()["targets"][0]["x"] == 120


def test_overlay_capability_is_sticky_on_status(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "e1003")

    def post_status(body: dict[str, Any]):
        return client.post(
            "/api/v1/device/e1003/status", headers=_auth(token), data=json.dumps(body)
        )

    post_status({"overlay": {"schema": 1}, "battery_mv": 4000})
    assert app.config["DEVICE_STATUS"]["e1003"]["overlay"] == {"schema": 1}
    # A beat that omits it keeps the capability (firmware property,
    # unlike the removable-card deck_cache).
    post_status({"battery_mv": 3990})
    assert app.config["DEVICE_STATUS"]["e1003"]["overlay"] == {"schema": 1}
