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


def test_frame_response_carries_renderer_payload_fields(app: Flask) -> None:
    """REST /frame must return the renderer-specific fields its MQTT-
    subscribed cousins receive (rotate / scale / bg / saturation for
    pi_png, etc.), not just the REST-shape envelope. A real pi_png
    client logs ``payload missing 'rotate'`` and skips the paint
    otherwise."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="lounge_pi", kind="pi_png_client")
    token = resp.get_json()["device_token"]

    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders["lounge_pi"] = {
        "digest": "ab9dbd3be",
        "ext": "png",
        "filename": "ab9dbd3be.png",
        "renderer_id": "pi_png",
        "timestamp": time.time(),
        "composition_digest": "comp_ab9",
    }

    resp = client.get(
        "/api/v1/device/lounge_pi/frame",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    # REST-shape envelope still present.
    assert body["render_id"] == "ab9dbd3be"
    assert body["format"] == "png"
    # Renderer-specific fields (the v3-frozen pi_png payload) merged in.
    assert "rotate" in body, body
    assert "scale" in body
    assert "bg" in body
    assert "saturation" in body


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
    # Default for pico_bin_client is 60s per device.json schema (every
    # device kind now defaults to a tight cadence so newly-paired
    # devices stay responsive until the user picks a longer one).
    assert body["next_poll_s"] == 60
    # The merged status is now in the cache, in the same shape the MQTT
    # path uses ({"received_at": ts, "parsed": {...}}) so the Devices
    # UI's _status_view reads it correctly.
    cache = app.config["DEVICE_STATUS"]
    record = cache["bedroom_pico"]
    assert record["parsed"]["battery_pct"] == 72
    assert record["received_at"] > 0


def test_rest_status_updates_received_at_so_last_seen_is_fresh(app: Flask) -> None:
    """v0.53 regression: a REST device heartbeat must write
    ``received_at`` to the status cache so the Devices admin page's
    "last seen" / freshness dot updates instead of reading 0 (epoch).

    Pre-v0.53.1 the REST handler wrote a flat dict with ``last_seen``;
    the UI reads ``received_at``, so REST devices appeared stuck at
    "20624 days ago" in the admin UI. This test pins the field
    contract so a future refactor can't reintroduce the drift."""
    import time as _t

    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="freshness_pico")
    token = resp.get_json()["device_token"]

    before = _t.time()
    resp = client.post(
        "/api/v1/device/freshness_pico/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 50, "rssi": -55}),
    )
    after = _t.time()
    assert resp.status_code == 200

    cache = app.config["DEVICE_STATUS"]["freshness_pico"]
    # The Devices UI's _status_view reads cache.get("received_at", 0)
    # then computes ``age = now - received_at``. If this returns 0 the
    # UI shows "20624 days ago".
    assert "received_at" in cache, "received_at must be set on REST heartbeats"
    assert before <= cache["received_at"] <= after + 1
    # Parsed dict lives nested under "parsed", same as the MQTT path,
    # so _status_view's cache.get("parsed", {}) finds the diagnostic
    # fields instead of returning empty.
    assert cache["parsed"]["battery_pct"] == 50
    assert cache["parsed"]["rssi"] == -55


def test_rest_status_records_battery_history(app: Flask) -> None:
    """The device_battery widget plots heartbeats from the
    BATTERY_HISTORY store. Pre-v0.53.1 the REST handler skipped this
    side effect, so REST devices never showed up on the battery
    page. After the fix, a heartbeat with battery_pct lands a sample."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="batt_pico")
    token = resp.get_json()["device_token"]

    history = app.config.get("BATTERY_HISTORY")
    assert history is not None
    # Drive a status post with a battery reading.
    resp = client.post(
        "/api/v1/device/batt_pico/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 80, "battery_mv": 3900, "rssi": -50}),
    )
    assert resp.status_code == 200
    samples = list(history.recent("batt_pico", limit=10))
    assert samples, "REST heartbeat must record into battery history"
    assert samples[0].pct == 80


# -- log ---------------------------------------------------------------


def test_discover_endpoint_adds_to_discovery_cache(app: Flask) -> None:
    """REST discovery announce: a firmware without a pairing code POSTs
    to /discover, the entry shows up in the DiscoveryCache, and the
    Settings -> Devices page picks it up alongside MQTT-discovered
    devices."""
    client = app.test_client()
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "fresh_pico",
                "kind": "pico_bin_client",
                "panel_w": 1600,
                "panel_h": 1200,
                "fw_version": "0.0.1",
                "mac": "aabbccddeeff",
            }
        ),
    )
    assert resp.status_code == 200
    cache = app.config["DISCOVERY_CACHE"]
    discovered = {d.id: d for d in cache.all()}
    assert "fresh_pico" in discovered
    assert discovered["fresh_pico"].parsed.get("kind") == "pico_bin_client"


def test_discover_endpoint_rejects_missing_device_id(app: Flask) -> None:
    client = app.test_client()
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"kind": "pico_bin_client"}),
    )
    assert resp.status_code == 400


def test_discover_endpoint_tags_cache_entry_as_rest(app: Flask) -> None:
    """The cache entry needs a ``transport: "rest"`` hint so the
    admin's Register click on Settings -> Devices creates a REST-
    mode instance (not an MQTT one)."""
    client = app.test_client()
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "fresh_pico_2",
                "kind": "pico_bin_client",
                "mac": "aabbccddeeff",
            }
        ),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["registered"] is False
    cache = app.config["DISCOVERY_CACHE"]
    entry = cache.get("fresh_pico_2")
    assert entry is not None
    assert entry.parsed.get("transport") == "rest"


def test_discover_returns_token_when_mac_matches_registered_instance(app: Flask) -> None:
    """Discover-then-claim flow: admin clicked Register on a previous
    Discovered entry, the resulting instance has the firmware's MAC
    on its manifest. On the firmware's next discover POST the MAC
    matches and the server returns the device's access token. No
    pairing code involved."""
    # Pre-register an instance with the MAC that the firmware will
    # present next. Mirrors what devices_register_discovered would
    # have done after the admin clicked Register.
    from app import device_service

    devices_registry = app.config["DEVICE_REGISTRY"]
    renderers = app.config["RENDERER_REGISTRY"]
    result = device_service.create_instance(
        devices=devices_registry,
        renderers=renderers,
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id="claimed_pico",
        kind_id="pico_bin_client",
        mac="aa:bb:cc:dd:ee:ff",
        transport="rest",
    )
    assert result.device is not None
    expected_token = result.device.manifest["access_token"]

    # Firmware does its discover POST with the matching MAC.
    client = app.test_client()
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "claimed_pico",
                "kind": "pico_bin_client",
                "mac": "AABBCCDDEEFF",  # different format, same MAC
            }
        ),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["registered"] is True
    assert body["device_token"] == expected_token
    assert body["device_id"] == "claimed_pico"
    assert "config" in body


def test_discover_does_not_claim_when_mac_missing(app: Flask) -> None:
    """A discover POST without a MAC can't match-by-MAC; falls through
    to the standard cache-and-tell-firmware-to-retry path even if an
    instance exists with that device id."""
    client = app.test_client()
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"device_id": "no_mac_pico", "kind": "pico_bin_client"}),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["registered"] is False


def test_discover_endpoint_rate_limited(app: Flask) -> None:
    """Discovery shares the register endpoint's rate limiter; an
    attacker spamming discoveries gets 429 after the cap."""
    from app.state.rate_limiter import RateLimiter

    app.config["REGISTER_RATE_LIMITER"] = RateLimiter(max_attempts=2, window_s=60)
    client = app.test_client()
    body = json.dumps({"device_id": "spam_pico", "kind": "pico_bin_client"})
    for _ in range(2):
        resp = client.post(
            "/api/v1/device/discover",
            headers={"Content-Type": "application/json"},
            data=body,
        )
        assert resp.status_code == 200
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=body,
    )
    assert resp.status_code == 429


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


# -- CORS ---------------------------------------------------------------


def test_cors_headers_present_on_normal_response(app: Flask) -> None:
    """Browser-based callers (the device emulator at
    emulator.tesserae.ink, future in-browser test-push UI) need the
    Access-Control-Allow-* headers on every REST API response."""
    client = app.test_client()
    resp = client.get("/api/v1/device/nonexistent/frame")
    # Auth fails before frame logic, but the after_request hook still
    # paints the headers — that's what we're verifying.
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    assert "GET" in resp.headers.get("Access-Control-Allow-Methods", "")
    assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")
    assert "Authorization" in resp.headers.get("Access-Control-Allow-Headers", "")
    assert "ETag" in resp.headers.get("Access-Control-Expose-Headers", "")


def test_cors_preflight_returns_204(app: Flask) -> None:
    """OPTIONS preflight short-circuits to 204 with the CORS headers
    attached, instead of falling through to the route handlers
    (which would 405 on an OPTIONS request)."""
    client = app.test_client()
    resp = client.options(
        "/api/v1/device/bedroom_pico/frame",
        headers={
            "Origin": "https://emulator.tesserae.ink",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert resp.status_code == 204
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    assert "Authorization" in resp.headers.get("Access-Control-Allow-Headers", "")


def test_cors_preflight_for_status_post(app: Flask) -> None:
    """The status endpoint is the POST path the emulator hits most
    often (heartbeats every poll). Confirm OPTIONS works there too."""
    client = app.test_client()
    resp = client.options("/api/v1/device/bedroom_pico/status")
    assert resp.status_code == 204
    assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")


def test_cors_headers_on_auth_failure(app: Flask) -> None:
    """A 401 from a missing/invalid token must still carry the CORS
    headers — otherwise the browser swallows the response and the
    emulator can't even surface the auth error to the user."""
    client = app.test_client()
    resp = client.get(
        "/api/v1/device/bedroom_pico/frame",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code in (401, 403)
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
