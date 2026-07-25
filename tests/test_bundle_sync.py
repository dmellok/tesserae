"""Protocol v2 state bundles: frame states from the warmed deck cache,
the links table (explicit buttons + default neighbours as gestures),
content-hash versioning, and the REST surface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.bundle_sync import build_bundle, bundle_digest_for
from app.main import REPO_ROOT, create_app
from app.state.deck_model import Deck, DeckLink, DeckPage


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


def _register(app: Flask, client) -> str:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": "e1003", "kind": "esp32_client", "panel_w": 800, "panel_h": 480}
        ),
    )
    assert resp.status_code == 201
    return resp.get_json()["device_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _deck() -> Deck:
    return Deck(
        id="d1",
        name="Deck",
        device_ids=["e1003"],
        entry_page_id="home",
        pages=[
            DeckPage(page_id="home", links=[DeckLink(target_page_id="b", button="right")]),
            DeckPage(page_id="b"),
        ],
    )


def _warm(app: Flask, page_id: str, digest: str) -> None:
    (app.config["RENDERS_DIR"] / f"{digest}.bin").write_bytes(b"\x00" * 64)
    app.config["PUSH_MANAGER"]._deck_renders.setdefault("e1003", {})[page_id] = {
        "digest": digest,
        "ext": "bin",
        "filename": f"{digest}.bin",
        "composition_digest": "c" * 16,
        "timestamp": 1000.0,
    }


def test_bundle_states_links_and_versioning(app: Flask) -> None:
    deck = _deck()
    _warm(app, "home", "a" * 16)
    _warm(app, "b", "b" * 16)
    pm = app.config["PUSH_MANAGER"]
    doc = build_bundle(deck, "e1003", push_mgr=pm, renders_dir=app.config["RENDERS_DIR"])
    assert doc is not None
    by_state = {s["state_id"]: s for s in doc["states"]}
    assert by_state["page:home"]["frame_digest"] == "a" * 16
    assert by_state["page:home"]["bytes"] == 64
    assert by_state["page:home"]["url"].endswith(f"/bundle/frame/{'a' * 16}")
    home_links = doc["links"]["page:home"]
    assert home_links["right"] == "page:b"  # explicit button link
    assert home_links["swipe_left"] == "page:b"  # gesture alias
    assert doc["links"]["page:b"]["left"] == "page:home"  # default neighbour

    # Digest tracks content: same inputs = same digest, re-warm = new one.
    assert bundle_digest_for(deck, "e1003", push_mgr=pm) == doc["bundle_digest"]
    _warm(app, "b", "e" * 16)
    assert bundle_digest_for(deck, "e1003", push_mgr=pm) != doc["bundle_digest"]
    assert bundle_digest_for(None, "e1003", push_mgr=pm) == ""


def test_cold_pages_are_absent_not_blocking(app: Flask) -> None:
    deck = _deck()
    _warm(app, "home", "a" * 16)  # "b" stays cold
    doc = build_bundle(
        deck, "e1003", push_mgr=app.config["PUSH_MANAGER"], renders_dir=app.config["RENDERS_DIR"]
    )
    assert doc is not None
    assert [s["state_id"] for s in doc["states"]] == ["page:home"]


def test_bundle_rest_surface(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    # No deck bound: 204.
    assert client.get("/api/v1/device/e1003/bundle", headers=_auth(token)).status_code == 204

    app.config["DECK_STORE"].upsert(_deck())
    _warm(app, "home", "a" * 16)
    resp = client.get("/api/v1/device/e1003/bundle", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["states"][0]["frame_digest"] == "a" * 16
    assert len(body["bundle_digest"]) == 16

    frame = client.get(f"/api/v1/device/e1003/bundle/frame/{'a' * 16}", headers=_auth(token))
    assert frame.status_code == 200
    assert frame.data == b"\x00" * 64
    assert "immutable" in frame.headers.get("Cache-Control", "")
    frame.close()
    missing = client.get(f"/api/v1/device/e1003/bundle/frame/{'f' * 16}", headers=_auth(token))
    assert missing.status_code == 404


def test_sync_event_carries_bundle_digest(app: Flask) -> None:
    from app.rest_api import _stream_events

    client = app.test_client()
    _register(app, client)
    app.config["DECK_STORE"].upsert(_deck())
    _warm(app, "home", "a" * 16)
    app.config["PUSH_MANAGER"]._latest_renders["e1003"] = {
        "digest": "a" * 16,
        "ext": "bin",
        "filename": f"{'a' * 16}.bin",
        "composition_digest": "c" * 16,
    }
    device = app.config["DEVICE_REGISTRY"].get("e1003")
    chunks = list(_stream_events(app, device, max_ticks=1, scan_s=0))
    sync: dict[str, Any] = {}
    for chunk in chunks:
        if chunk.startswith("event: sync"):
            sync = json.loads(chunk.partition("data: ")[2].strip())
    assert len(sync.get("bundle_digest", "")) == 16
