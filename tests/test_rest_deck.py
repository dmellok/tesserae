"""REST deck-cache sync endpoint tests: the manifest + frame-by-digest
endpoints, the /status deck version envelope + capability ingestion,
and displayed-page report handling on /status and /frame.

Warmed renders are injected straight into the real PushManager's deck
cache (with matching artifact files under RENDERS_DIR) so no Playwright
render runs; the warm-on-demand path itself is unit-tested against a
fake source in tests/test_deck_sync.py."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

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


def _bind_deck(app: Flask, device_id: str) -> Deck:
    deck = Deck(
        id="kitchen",
        name="Kitchen",
        device_ids=[device_id],
        refresh_interval_minutes=15,
        pages=[
            DeckPage(
                page_id="overview", links=[DeckLink(target_page_id="weather", button="right")]
            ),
            DeckPage(page_id="weather", links=[DeckLink(target_page_id="overview", button="left")]),
        ],
    )
    app.config["DECK_STORE"].upsert(deck)
    return deck


def _warm(app: Flask, device_id: str, page_id: str, payload: bytes) -> str:
    """Inject a warmed deck render + its artifact file, no Playwright."""
    digest = hashlib.sha256(payload).hexdigest()[:16]
    filename = f"{digest}.bin"
    (app.config["RENDERS_DIR"] / filename).write_bytes(payload)
    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._deck_renders.setdefault(device_id, {})[page_id] = {
        "digest": digest,
        "ext": "bin",
        "filename": filename,
    }
    return digest


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _post_status(client, device_id: str, token: str, body: dict[str, Any]):
    return client.post(
        f"/api/v1/device/{device_id}/status", headers=_auth(token), data=json.dumps(body)
    )


CAP = {"deck_cache": {"schema": 1, "capacity_bytes": 7_900_000}}


# -- manifest ------------------------------------------------------------


def test_deck_manifest_requires_token(app: Flask) -> None:
    client = app.test_client()
    _register_esp32(app, client, "frame01")
    resp = client.get("/api/v1/device/frame01/deck")
    assert resp.status_code == 401


def test_deck_manifest_204_when_no_deck_bound(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    resp = client.get("/api/v1/device/frame01/deck", headers=_auth(token))
    assert resp.status_code == 204


def test_deck_manifest_returns_pages_digests_and_links(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    _bind_deck(app, "frame01")
    d_overview = _warm(app, "frame01", "overview", b"frame-overview")
    d_weather = _warm(app, "frame01", "weather", b"frame-weather")

    resp = client.get("/api/v1/device/frame01/deck", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["deck_id"] == "kitchen"
    assert body["entry_page_id"] == "overview"
    assert len(body["version"]) == 16
    pages = {p["page_id"]: p for p in body["pages"]}
    assert pages["overview"]["digest"] == d_overview
    assert pages["overview"]["bytes"] == len(b"frame-overview")
    assert pages["overview"]["ttl_s"] == 900
    assert pages["overview"]["links"] == [
        {"button": "right", "zone": None, "target_page_id": "weather"}
    ]
    assert pages["weather"]["digest"] == d_weather


# -- frame by digest -------------------------------------------------------


def test_deck_frame_serves_bytes_by_digest(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    _bind_deck(app, "frame01")
    digest = _warm(app, "frame01", "overview", b"frame-overview")

    resp = client.get(f"/api/v1/device/frame01/deck/frame/{digest}", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.data == b"frame-overview"
    assert resp.headers["ETag"] == f'"{digest}"'
    assert "immutable" in resp.headers.get("Cache-Control", "")
    # send_file keeps the artifact handle open until the response is
    # closed; the test client never closes it for us, and the suite
    # escalates the ResourceWarning to an error.
    resp.close()


def test_deck_frame_404_on_unknown_digest(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    _bind_deck(app, "frame01")
    _warm(app, "frame01", "overview", b"frame-overview")

    resp = client.get(f"/api/v1/device/frame01/deck/frame/{'0' * 16}", headers=_auth(token))
    assert resp.status_code == 404


def test_deck_frame_404_when_no_deck(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    resp = client.get(f"/api/v1/device/frame01/deck/frame/{'0' * 16}", headers=_auth(token))
    assert resp.status_code == 404


# -- /status envelope + capability ------------------------------------------


def test_status_carries_deck_version_for_capable_device(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    _bind_deck(app, "frame01")
    _warm(app, "frame01", "overview", b"frame-overview")
    _warm(app, "frame01", "weather", b"frame-weather")

    resp = _post_status(client, "frame01", token, {**CAP, "battery_mv": 4000})
    assert resp.status_code == 200
    version = resp.get_json()["deck"]["version"]

    manifest = client.get("/api/v1/device/frame01/deck", headers=_auth(token)).get_json()
    assert manifest["version"] == version


def test_status_omits_deck_without_capability_or_deck(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    # Capability but no deck bound.
    assert "deck" not in _post_status(client, "frame01", token, dict(CAP)).get_json()
    # Deck bound but no capability on this beat.
    _bind_deck(app, "frame01")
    assert "deck" not in _post_status(client, "frame01", token, {"battery_mv": 1}).get_json()


def test_capability_is_current_state_not_sticky(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")

    _post_status(client, "frame01", token, dict(CAP))
    entry = app.config["DEVICE_STATUS"]["frame01"]
    assert entry["deck_cache"] == {"schema": 1, "capacity_bytes": 7_900_000}

    # Next beat omits it (card pulled): capability disappears.
    _post_status(client, "frame01", token, {"battery_mv": 4000})
    assert "deck_cache" not in app.config["DEVICE_STATUS"]["frame01"]


# -- displayed-page reports ---------------------------------------------------


def test_status_deck_page_report_updates_nav_position(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    deck = _bind_deck(app, "frame01")

    resp = _post_status(client, "frame01", token, {**CAP, "deck_page_id": "weather"})
    assert resp.status_code == 200
    nav = app.config["DECK_NAV_STORE"].get("frame01")
    assert nav is not None
    assert (nav["deck_id"], nav["page_id"]) == (deck.id, "weather")


def test_deck_page_report_ignores_unknown_page(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    _bind_deck(app, "frame01")

    _post_status(client, "frame01", token, {**CAP, "deck_page_id": "not_in_deck"})
    assert app.config["DECK_NAV_STORE"].get("frame01") is None


def test_frame_query_deck_page_report_updates_nav_position(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "frame01")
    deck = _bind_deck(app, "frame01")

    # No frame pushed yet -> 204, but the report must still land.
    resp = client.get(
        "/api/v1/device/frame01/frame?deck_page_id=weather",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204
    nav = app.config["DECK_NAV_STORE"].get("frame01")
    assert nav is not None
    assert (nav["deck_id"], nav["page_id"]) == (deck.id, "weather")
