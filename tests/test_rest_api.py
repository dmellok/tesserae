"""REST API endpoint tests: frame fetch, status post, register, log.

Covers the wire contract Phase 1 ships:
- Bearer-token auth, including the URL-id mismatch case
- Frame: ETag, 304 on If-None-Match, 204 when no frame rendered yet
- Status: parse + merge, response piggybacks config + next_poll_s
- Register: pairing code flow, idempotent on existing device id,
  refuses bad codes / unknown kinds
- Log: persists into the EventLog with the right shape
"""

from __future__ import annotations

import json
import time
from pathlib import Path

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


def _register_via_api(client, *, code: str, device_id: str, kind: str = "pico_bin_client"):
    """Drive the register endpoint to set up an instance with a token
    we can then reuse in subsequent tests. Returns the response."""
    return client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": device_id,
                "kind": kind,
                "panel_w": 1600,
                "panel_h": 1200,
                "fw_version": "0.1.0",
            }
        ),
    )


def _issue_pairing(app) -> str:
    return app.config["PAIRING_STORE"].issue(note="test").code


# -- register ----------------------------------------------------------


def test_register_with_valid_pairing_code_creates_device_and_returns_token(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)

    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["device_token"]
    assert isinstance(body["device_token"], str)
    assert body["reused_existing"] is False
    # Instance now lives in the registry.
    devices = app.config["DEVICE_REGISTRY"]
    instance = devices.get("bedroom_pico")
    assert instance is not None
    assert instance.kind_of == "pico_bin_client"
    assert instance.manifest.get("access_token") == body["device_token"]


def test_register_without_pairing_code_returns_400(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/api/v1/device/register",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"device_id": "x", "kind": "pico_bin_client"}),
    )
    assert resp.status_code == 400


def test_register_with_invalid_pairing_code_returns_403(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = _register_via_api(client, code="000000", device_id="bedroom_pico")
    assert resp.status_code == 403
    assert "invalid" in resp.get_json()["error"].lower()


def test_register_with_unknown_kind_does_not_burn_pairing_code(app: Flask) -> None:
    """Validation order is load-bearing: pairing code is checked AFTER
    the kind. A typoed kind shouldn't lose the user their code."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="x", kind="nonexistent_kind")
    assert resp.status_code == 400
    # Code still consumable since unknown-kind validation happened first.
    resp2 = _register_via_api(client, code=code, device_id="bedroom_pico")
    assert resp2.status_code == 201


def test_register_is_idempotent_on_existing_device_id(app: Flask) -> None:
    """If the firmware retries register (maybe missed our response),
    second call returns the EXISTING token rather than creating a
    duplicate or failing."""
    client = app.test_client()
    _sign_in(client)
    first_code = _issue_pairing(app)
    second_code = _issue_pairing(app)

    resp1 = _register_via_api(client, code=first_code, device_id="bedroom_pico")
    token1 = resp1.get_json()["device_token"]

    resp2 = _register_via_api(client, code=second_code, device_id="bedroom_pico")
    assert resp2.status_code == 200
    body = resp2.get_json()
    assert body["reused_existing"] is True
    assert body["device_token"] == token1


# -- auth --------------------------------------------------------------


def test_frame_endpoint_without_token_returns_401(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/api/v1/device/bedroom_pico/frame")
    assert resp.status_code == 401


def test_frame_endpoint_with_invalid_token_returns_401(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get(
        "/api/v1/device/bedroom_pico/frame",
        headers={"Authorization": "Bearer wrongtoken"},
    )
    assert resp.status_code == 401


def test_frame_endpoint_with_token_for_other_device_returns_403(app: Flask) -> None:
    """Bearer token is valid (resolves to a device) but the URL claims
    a different device id. Don't leak which device the token belongs to."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    resp = client.get(
        "/api/v1/device/different_device/frame",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_status_endpoint_accepts_x_tesserae_token_header(app: Flask) -> None:
    """Some embedded HTTP libs make custom Authorization headers
    awkward; the X-Tesserae-Token fallback covers that case."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    resp = client.post(
        "/api/v1/device/bedroom_pico/status",
        headers={"X-Tesserae-Token": token, "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 72}),
    )
    assert resp.status_code == 200


# -- frame -------------------------------------------------------------


def test_frame_returns_204_when_nothing_rendered(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    resp = client.get(
        "/api/v1/device/bedroom_pico/frame",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204


def test_frame_returns_url_and_etag_when_rendered(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    # Fake a render. PushManager's latest_renders is the map the
    # endpoint reads from.
    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders["bedroom_pico"] = {
        "digest": "abc123",
        "ext": "bin",
        "filename": "abc123.bin",
        "renderer_id": "pico_bin",
        "timestamp": time.time(),
        "composition_digest": "comp123",
    }

    resp = client.get(
        "/api/v1/device/bedroom_pico/frame",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["render_id"] == "abc123"
    assert body["format"] == "bin"
    assert body["url"].endswith("/renders/abc123.bin")
    assert resp.headers["ETag"] == '"abc123"'


def test_frame_returns_304_when_if_none_match_matches(app: Flask) -> None:
    """The save-the-firmware-a-fetch-and-paint path. A deep-sleep
    client whose last-seen ETag matches the current frame can skip
    everything except the status post."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders["bedroom_pico"] = {
        "digest": "abc123",
        "ext": "bin",
        "filename": "abc123.bin",
        "renderer_id": "pico_bin",
        "timestamp": time.time(),
        "composition_digest": "comp123",
    }

    resp = client.get(
        "/api/v1/device/bedroom_pico/frame",
        headers={
            "Authorization": f"Bearer {token}",
            "If-None-Match": '"abc123"',
        },
    )
    assert resp.status_code == 304
    assert resp.headers["ETag"] == '"abc123"'


# -- status ------------------------------------------------------------


def test_status_response_includes_config_and_next_poll(app: Flask) -> None:
    """One round-trip per wake: status post returns the latest config
    + when to poll again. Firmware doesn't need a separate poll."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    resp = client.post(
        "/api/v1/device/bedroom_pico/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_mv": 3850, "battery_pct": 72, "rssi": -64}),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert "config" in body
    assert "next_poll_s" in body
    # Default for pico_bin_client is 900s (15min) per device.json schema.
    assert body["next_poll_s"] == 900
    # The merged status is now in the cache, tagged with the rest transport.
    cache = app.config["DEVICE_STATUS"]
    assert cache["bedroom_pico"]["battery_pct"] == 72
    assert cache["bedroom_pico"]["transport"] == "rest"


# -- log ---------------------------------------------------------------


def test_admin_pairing_issue_requires_session(app: Flask) -> None:
    """Admin endpoints are session-gated despite living under /api/v1/
    (which is otherwise auth-bypassed). Unauth'd callers get 401."""
    client = app.test_client()
    # No /setup call -> no session.
    resp = client.post("/api/v1/device/admin/pairing/issue")
    assert resp.status_code == 401


def test_admin_pairing_issue_returns_code_when_authed(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/api/v1/device/admin/pairing/issue")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"].isdigit() and len(body["code"]) == 6
    # Code is consumable by the register endpoint.
    code = body["code"]
    reg = _register_via_api(client, code=code, device_id="just_for_test")
    assert reg.status_code == 201


def test_register_marks_instance_as_rest_transport(app: Flask) -> None:
    """Phase 1b: a REST-registered instance carries ``transport: "rest"``
    on its manifest so the push pipeline knows to skip MQTT publish."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    _register_via_api(client, code=code, device_id="bedroom_pico")
    devices = app.config["DEVICE_REGISTRY"]
    instance = devices.get("bedroom_pico")
    assert instance is not None
    assert instance.transport == "rest"
    assert instance.manifest.get("transport") == "rest"


def test_existing_mqtt_instances_default_to_mqtt_transport(app: Flask) -> None:
    """Backward compat: a pre-0.52 instance manifest with no ``transport``
    field reads as MQTT, which keeps the push pipeline behaving as before."""
    devices = app.config["DEVICE_REGISTRY"]
    # Pi BIN client kinds ship without a transport field on the kind
    # manifest (transport is per-instance). The kind itself, being
    # treated as a Device, should default to mqtt.
    pi_kind = devices.get("pi_bin_client")
    assert pi_kind is not None
    assert pi_kind.transport == "mqtt"


def test_push_pipeline_skips_publish_for_rest_devices(app: Flask) -> None:
    """End-to-end: a REST-mode instance's renderer clone is detected as
    http-polled, so ``_renderer_is_http_polled`` short-circuits the MQTT
    publish in ``_publish_artifact``. The transport mock would otherwise
    record a publish call we don't want."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    _register_via_api(client, code=code, device_id="bedroom_pico")
    push_mgr = app.config["PUSH_MANAGER"]
    renderers = app.config["RENDERER_REGISTRY"]
    # The cloned renderer for our REST device.
    clone = renderers.get("pico_bin__bedroom_pico")
    assert clone is not None
    assert push_mgr._renderer_is_http_polled(clone) is True
    # And the base mqtt-bound renderer is NOT marked http-polled, so
    # existing MQTT clients still publish normally.
    base = renderers.get("pi_bin")
    assert base is not None
    assert push_mgr._renderer_is_http_polled(base) is False


def test_admin_pairing_pending_lists_unredeemed_codes(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    a = client.post("/api/v1/device/admin/pairing/issue").get_json()["code"]
    b = client.post("/api/v1/device/admin/pairing/issue").get_json()["code"]
    resp = client.get("/api/v1/device/admin/pairing/pending")
    assert resp.status_code == 200
    codes = [p["code"] for p in resp.get_json()["pending"]]
    assert a in codes and b in codes


def test_log_endpoint_appends_to_event_log(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    resp = client.post(
        "/api/v1/device/bedroom_pico/log",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"level": "warn", "msg": "panel busy timeout"}),
    )
    assert resp.status_code == 200
    events = app.config["EVENT_LOG"]
    rows = events.list(type="device", limit=10)
    matches = [r for r in rows if r.target == "client_log" and r.source == "bedroom_pico"]
    assert matches, "no client_log event recorded"
    assert matches[0].status == "warn"
    assert matches[0].extra.get("msg") == "panel busy timeout"
