"""Live Companion API (Phase 1) integration tests.

Drives the real Flask app through its test client and validates the
*actual* responses against the vendored component schemas, so the server
and the iOS client agree on the wire, not just the fixtures with
themselves. Covers the full pair -> read -> revoke round trip, the
unauthenticated boundary, and the separation between companion and
firmware pairing purposes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from flask import Flask

from app import companion_api, companion_gallery, device_upcoming
from app.main import REPO_ROOT, create_app
from app.state.page_store import Page

from ._schema import schema_for

_FMT = jsonschema.FormatChecker()


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


def _issue_companion_code(app: Flask) -> str:
    return app.config["COMPANION_PAIRING_STORE"].issue(note="test").code


_CLIENT = {
    "name": "Test iPhone",
    "platform": "ios",
    "app_version": "0.1.0",
    "installation_id": "A1B2C3D4-E5F6-47A8-9012-3456789ABCDE",
}


def _pair(client: Any, code: str) -> Any:
    return client.post(
        "/api/app/v1/pair",
        data=json.dumps({"code": code, "client": _CLIENT}),
        content_type="application/json",
    )


def _seed_device(app: Flask) -> str:
    """Register a REST device instance so /devices has a real target."""
    client = app.test_client()
    code = app.config["PAIRING_STORE"].issue(note="dev").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "kitchen",
                "kind": "pico_bin_client",
                "panel_w": 1600,
                "panel_h": 1200,
                "fw_version": "1.8.0",
            }
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return "kitchen"


def _seed_page(app: Flask, device_id: str) -> str:
    page = Page(
        id="pantry",
        name="Pantry",
        layout_kind="canvas",
        icon="ph-cooking-pot",
        device_ids=[device_id],
        updated_at="2026-07-28T07:45:00Z",
    )
    app.config["PAGE_STORE"].save(page)
    return page.id


# -- discovery -----------------------------------------------------------


def test_capabilities_probe_is_unauthenticated_and_valid(app: Flask) -> None:
    # The additive `previews` feature is gated on the browser pool and covered
    # separately in test_companion_previews. Canonical History is available
    # whenever Companion 0.4 is served.
    app.config["BROWSER_POOL"] = None
    resp = app.test_client().get("/api/app/v1")
    assert resp.status_code == 200
    body = resp.get_json()
    _validate(body, "Capabilities")
    assert body["product"] == "tesserae"
    assert body["api"] == {"name": "companion", "version": 1}
    # Advertised pairing facts agree with the store the server actually uses.
    assert body["pairing"]["code_length"] == 6
    assert body["pairing"]["ttl_seconds"] == 600
    # All read/write surfaces served without a browser pool. image_url_push is
    # always on (plain fetch); webpage_push + previews are gated on the pool and
    # covered in test_companion_previews.
    assert set(body["features"]) == {
        "devices",
        "dashboards",
        "dashboard_push",
        "image_push",
        "image_url_push",
        "jobs",
        "history",
        "image_framing",
        "personal_data_reminders",
        "lineups",
        "lineup_control",
        "lineup_authoring",
        "session_read",
        # The photo library is a plugin; the test app registers it.
        "gallery",
        # Needs the same plugin plus the album store, so it rides with it here
        # and is advertised separately (#230).
        "offline_albums",
        # Gated on a running scheduler, which the app factory always wires
        # (#232). The projection replays that engine's gates, so an install
        # without one has nothing honest to answer with.
        "device_timeline",
    }
    assert body["personal_data"]["sources"] == ["reminders", "reminders.fridge"]
    assert "webpage_push" not in body["features"]
    assert body["limits"]["image_fit_modes"] == list(companion_api.IMAGE_FIT_MODES)
    # Contract 0.6: the zoom bound is mandatory alongside image_framing.
    assert body["limits"]["image_framing_max_zoom"] == companion_api.IMAGE_FRAMING_MAX_ZOOM
    # Contract 0.11: all three Gallery bounds are mandatory alongside the
    # capability, so a client never hard-codes an upload ceiling.
    assert body["limits"]["gallery_upload_bytes"] == companion_gallery.GALLERY_UPLOAD_BYTES
    assert body["limits"]["gallery_image_content_types"] == list(
        companion_gallery.GALLERY_IMAGE_CONTENT_TYPES
    )
    assert (
        body["limits"]["gallery_upload_batch_size"] == companion_gallery.GALLERY_UPLOAD_BATCH_SIZE
    )
    # Mandatory alongside device_timeline, so the client asks for a window
    # the server will honour instead of finding the ceiling through a 400.
    assert body["limits"]["device_timeline_max_hours"] == device_upcoming.MAX_HOURS
    assert body["limits"]["device_timeline_max_events"] == device_upcoming.MAX_LIMIT
    # No household content leaks from the unauthenticated probe.
    assert "devices" not in body and "dashboards" not in body


# -- pairing -------------------------------------------------------------


def test_pair_exchanges_code_for_scoped_token(app: Flask) -> None:
    client = app.test_client()
    resp = _pair(client, _issue_companion_code(app))
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    _validate(body, "PairingResponse")
    assert body["token"].startswith("tc_live_")
    assert set(body["scopes"]) == {
        "devices:read",
        "dashboards:read",
        "push:write",
        "media:write",
        "personal_data:write",
        # Read + control for Lineups ride along with the pairing role;
        # authoring (lineups:write) deliberately does not, see #207.
        "lineups:read",
        "lineups:control",
        # Browsing and adding to the photo Gallery rides along too; the
        # destructive second slice will not, see #225.
        "gallery:read",
        "gallery:write",
    }
    assert body["instance"]["server_version"] == app.config["APP_VERSION"]


def test_pair_code_is_single_use(app: Flask) -> None:
    client = app.test_client()
    code = _issue_companion_code(app)
    assert _pair(client, code).status_code == 201
    second = _pair(client, code)
    assert second.status_code == 400
    _validate(second.get_json(), "ErrorResponse")
    assert second.get_json()["error"]["code"] == "pairing_expired"


def test_pair_rejects_malformed_body(app: Flask) -> None:
    client = app.test_client()
    # Non-numeric code.
    bad_code = client.post(
        "/api/app/v1/pair",
        data=json.dumps({"code": "abc", "client": _CLIENT}),
        content_type="application/json",
    )
    assert bad_code.status_code == 400
    assert bad_code.get_json()["error"]["code"] == "invalid_request"
    # Missing client.
    no_client = client.post(
        "/api/app/v1/pair",
        data=json.dumps({"code": _issue_companion_code(app)}),
        content_type="application/json",
    )
    assert no_client.status_code == 400
    assert no_client.get_json()["error"]["code"] == "invalid_request"


def test_firmware_pairing_code_cannot_mint_a_companion_token(app: Flask) -> None:
    """Companion and firmware pairing are distinct purposes: a firmware
    code must not exchange for a companion token."""
    client = app.test_client()
    firmware_code = app.config["PAIRING_STORE"].issue(note="fw").code
    resp = _pair(client, firmware_code)
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "pairing_expired"


# -- read models ---------------------------------------------------------


def _token(app: Flask) -> str:
    resp = _pair(app.test_client(), _issue_companion_code(app))
    return str(resp.get_json()["token"])


def test_devices_listing_is_contract_valid(app: Flask) -> None:
    device_id = _seed_device(app)
    token = _token(app)
    resp = app.test_client().get(
        "/api/app/v1/devices", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.get_json()
    _validate(body, "DevicesResponse")
    ids = {d["id"] for d in body["devices"]}
    assert device_id in ids
    device = next(d for d in body["devices"] if d["id"] == device_id)
    assert device["panel"]["orientation"] == "landscape"  # 1600x1200
    # Never seen a heartbeat yet.
    assert device["freshness"] == "unknown"
    assert device["last_seen_at"] is None
    assert device["has_pending_render"] is False
    # #184: the resolved Phosphor slug ships on every device (kind default
    # until the user overrides it per instance; "monitor" as last resort).
    assert isinstance(device["icon"], str) and device["icon"]


def test_devices_listing_reports_a_pending_rest_render_until_frame_is_served(app: Flask) -> None:
    device_id = _seed_device(app)
    manager = app.config["PUSH_MANAGER"]
    latest = {
        "digest": "latest123456789",
        "ext": "bin",
        "filename": "latest123456789.bin",
        "renderer_id": "pico_bin",
        "timestamp": 1.0,
        "composition_digest": "composition12345",
        "preview_digest": "preview123456789",
    }
    manager._latest_renders[device_id] = latest
    token = _token(app)
    headers = {"Authorization": f"Bearer {token}"}

    pending = app.test_client().get("/api/app/v1/devices", headers=headers).get_json()
    device = next(d for d in pending["devices"] if d["id"] == device_id)
    assert device["has_pending_render"] is True
    assert device["pending_render"] == {
        "revision": "latest123456789",
        "rendered_at": "1970-01-01T00:00:01Z",
        "preview_url": (f"/api/app/v1/devices/{device_id}/preview?revision=latest123456789"),
    }

    manager.record_frame_served(device_id, latest)
    caught_up = app.test_client().get("/api/app/v1/devices", headers=headers).get_json()
    device = next(d for d in caught_up["devices"] if d["id"] == device_id)
    assert device["has_pending_render"] is False
    assert "pending_render" not in device


def test_devices_listing_omits_pending_render_when_no_preview_exists(app: Flask) -> None:
    device_id = _seed_device(app)
    manager = app.config["PUSH_MANAGER"]
    manager._latest_renders[device_id] = {
        "digest": "latest123456789",
        "ext": "bin",
        "filename": "latest123456789.bin",
        "renderer_id": "pico_bin",
        "timestamp": 1.0,
        "composition_digest": "composition12345",
        "preview_digest": None,
    }
    token = _token(app)

    body = (
        app.test_client()
        .get(
            "/api/app/v1/devices",
            headers={"Authorization": f"Bearer {token}"},
        )
        .get_json()
    )

    device = next(d for d in body["devices"] if d["id"] == device_id)
    assert device["has_pending_render"] is True
    assert "pending_render" not in device


def test_dashboards_listing_is_contract_valid(app: Flask) -> None:
    device_id = _seed_device(app)
    _seed_page(app, device_id)
    token = _token(app)
    resp = app.test_client().get(
        "/api/app/v1/dashboards", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.get_json()
    _validate(body, "DashboardsResponse")
    pantry = next(d for d in body["dashboards"] if d["id"] == "pantry")
    assert pantry["kind"] == "canvas"
    assert pantry["icon"] == "cooking-pot"
    assert pantry["device_ids"] == [device_id]


# -- auth boundary -------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/app/v1/devices", "/api/app/v1/dashboards"])
def test_read_models_require_a_token(app: Flask, path: str) -> None:
    resp = app.test_client().get(path)
    assert resp.status_code == 401
    _validate(resp.get_json(), "ErrorResponse")
    assert resp.get_json()["error"]["code"] == "unauthorized"


def test_session_reports_the_scopes_the_credential_carries_now(app: Flask) -> None:
    """Not the ones pairing issued. An operator can grant or withdraw an
    optional scope at any time, and the app's persisted copy from pairing
    goes stale the moment they do (#203)."""
    token = _token(app)
    client = app.test_client()
    auth = {"Authorization": f"Bearer {token}"}

    body = client.get("/api/app/v1/session", headers=auth).get_json()
    assert body["token_id"] == app.config["COMPANION_TOKENS"].list_active()[0].token_id
    assert "lineups:read" in body["scopes"]
    assert "lineups:write" not in body["scopes"]
    # Where the operator does the granting, resolved from the routing table.
    assert body["settings_url"] == "/settings/companion"
    with app.test_request_context():
        from flask import url_for

        assert body["settings_url"] == url_for("auth.companion_index")

    store = app.config["COMPANION_TOKENS"]
    store.set_optional_scope(body["token_id"], "lineups:write", granted=True)
    after = client.get("/api/app/v1/session", headers=auth).get_json()
    assert "lineups:write" in after["scopes"]


def test_session_read_needs_a_credential(app: Flask) -> None:
    resp = app.test_client().get("/api/app/v1/session")
    assert resp.status_code == 401
    assert resp.get_json()["error"]["code"] == "unauthorized"


def test_session_revoke_invalidates_the_token(app: Flask) -> None:
    token = _token(app)
    client = app.test_client()
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/app/v1/devices", headers=auth).status_code == 200
    revoke = client.delete("/api/app/v1/session", headers=auth)
    assert revoke.status_code == 204
    after = client.get("/api/app/v1/devices", headers=auth)
    assert after.status_code == 401
    assert after.get_json()["error"]["code"] == "unauthorized"


# -- admin pairing flow --------------------------------------------------


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_admin_can_issue_a_companion_code_and_the_app_pairs_with_it(app: Flask) -> None:
    admin = app.test_client()
    _sign_in(admin)
    # The Companion app settings page renders the pairing controls.
    page = admin.get("/settings/companion")
    assert page.status_code == 200
    assert b"Companion app" in page.get_data()

    issue = admin.post("/settings/companion/pair", data={"note": "phone"})
    assert issue.status_code == 302
    pending = app.config["COMPANION_PAIRING_STORE"].list_pending()
    assert len(pending) == 1
    code = pending[0].code

    # The app exchanges the admin-issued code for a token.
    resp = _pair(app.test_client(), code)
    assert resp.status_code == 201
    token_id = resp.get_json()["token_id"]

    # Admin can disconnect that client; its bearer stops working.
    token = resp.get_json()["token"]
    revoke = admin.post(f"/settings/companion/session/{token_id}/revoke")
    assert revoke.status_code == 302
    after = app.test_client().get(
        "/api/app/v1/devices", headers={"Authorization": f"Bearer {token}"}
    )
    assert after.status_code == 401


def test_revoked_token_is_dropped_from_admin_listing(app: Flask) -> None:
    token = _token(app)
    store = app.config["COMPANION_TOKENS"]
    assert len(store.list_active()) == 1
    app.test_client().delete("/api/app/v1/session", headers={"Authorization": f"Bearer {token}"})
    assert store.list_active() == []
