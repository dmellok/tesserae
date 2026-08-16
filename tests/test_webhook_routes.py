"""Webhook push endpoint: auth, body parsing, push wiring, status codes.

The endpoint is the new external surface for the v0.6.x feature
bundle, so the tests deliberately cover every status the response
can take, not just the happy 200 path."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.push import PushResult


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=True,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _set_token(app: Flask, token: str) -> None:
    """Write a webhook token via the SettingsStore so the route reads
    the same value the production flow does."""
    app.config["SETTINGS_STORE"].update_section("app", {"webhook_token_secret": token})


def test_webhook_returns_503_when_no_token_set(app: Flask) -> None:
    """Until the user generates (or pastes) a token, webhooks are
    effectively disabled, a 503 with a clear ``status="disabled"`` so
    automation tools can branch on it."""
    resp = app.test_client().post(
        "/api/v1/push",
        headers={"Authorization": "Bearer anything"},
        json={"page_id": "home"},
    )
    assert resp.status_code == 503
    assert resp.get_json()["status"] == "disabled"


def test_webhook_returns_401_with_wrong_token(app: Flask) -> None:
    _set_token(app, "real-token")
    resp = app.test_client().post(
        "/api/v1/push",
        headers={"Authorization": "Bearer wrong-token"},
        json={"page_id": "home"},
    )
    assert resp.status_code == 401
    assert resp.get_json()["status"] == "unauthorized"


def test_webhook_returns_400_when_page_id_missing(app: Flask) -> None:
    _set_token(app, "t")
    resp = app.test_client().post(
        "/api/v1/push",
        headers={"Authorization": "Bearer t"},
        json={},
    )
    assert resp.status_code == 400
    assert resp.get_json()["status"] == "bad_request"


def test_webhook_with_bearer_token_invokes_push(app: Flask) -> None:
    """Valid bearer token + body → calls PushManager.push with
    respect_quiet_hours=True (the whole point of the webhook honouring
    quiet hours) and surfaces the PushResult on a 200."""
    _set_token(app, "t")
    pm = MagicMock()
    pm.push.return_value = PushResult(status="sent", page_id="home", event_id=42)
    app.config["PUSH_MANAGER"] = pm

    resp = app.test_client().post(
        "/api/v1/push",
        headers={"Authorization": "Bearer t"},
        json={"page_id": "home"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "sent"
    assert body["event_id"] == 42

    pm.push.assert_called_once_with(
        "home", device_ids=None, respect_quiet_hours=True, source="webhook"
    )


def test_webhook_with_x_tesserae_token_header_works(app: Flask) -> None:
    """Some automation tools can't easily customise the Authorization
    header (it conflicts with their own basic auth). X-Tesserae-Token
    is the simpler fallback."""
    _set_token(app, "t")
    pm = MagicMock()
    pm.push.return_value = PushResult(status="sent", page_id="home")
    app.config["PUSH_MANAGER"] = pm

    resp = app.test_client().post(
        "/api/v1/push",
        headers={"X-Tesserae-Token": "t"},
        json={"page_id": "home"},
    )
    assert resp.status_code == 200


def test_webhook_device_ids_restricts_fanout(app: Flask) -> None:
    _set_token(app, "t")
    pm = MagicMock()
    pm.push.return_value = PushResult(status="sent", page_id="home")
    app.config["PUSH_MANAGER"] = pm

    resp = app.test_client().post(
        "/api/v1/push",
        headers={"Authorization": "Bearer t"},
        json={"page_id": "home", "device_ids": ["pi_kitchen", "pi_bedroom"]},
    )
    assert resp.status_code == 200
    pm.push.assert_called_once_with(
        "home",
        device_ids={"pi_kitchen", "pi_bedroom"},
        respect_quiet_hours=True,
        source="webhook",
    )


def test_webhook_quiet_status_returns_202(app: Flask) -> None:
    """``quiet`` is a successful skip, not a failure, the user's "no
    pushes overnight" intent was honoured. 202 Accepted lets callers
    distinguish from a real 200 sent."""
    _set_token(app, "t")
    pm = MagicMock()
    pm.push.return_value = PushResult(
        status="quiet", page_id="home", error="all bound devices in quiet hours"
    )
    app.config["PUSH_MANAGER"] = pm

    resp = app.test_client().post(
        "/api/v1/push",
        headers={"Authorization": "Bearer t"},
        json={"page_id": "home"},
    )
    assert resp.status_code == 202
    assert resp.get_json()["status"] == "quiet"


def test_webhook_busy_returns_409(app: Flask) -> None:
    _set_token(app, "t")
    pm = MagicMock()
    pm.push.return_value = PushResult(status="busy", page_id="home", error="another push in flight")
    app.config["PUSH_MANAGER"] = pm

    resp = app.test_client().post(
        "/api/v1/push",
        headers={"Authorization": "Bearer t"},
        json={"page_id": "home"},
    )
    assert resp.status_code == 409


def test_webhook_form_body_works_alongside_json(app: Flask) -> None:
    """Form-encoded bodies are accepted too, many automation tools
    can't easily switch their Content-Type."""
    _set_token(app, "t")
    pm = MagicMock()
    pm.push.return_value = PushResult(status="sent", page_id="home")
    app.config["PUSH_MANAGER"] = pm

    resp = app.test_client().post(
        "/api/v1/push",
        headers={"Authorization": "Bearer t"},
        data={"page_id": "home"},
    )
    assert resp.status_code == 200


def test_webhook_path_is_session_gate_bypassed(app: Flask) -> None:
    """The /api/v1/ prefix is on auth._OPEN_PATHS, so the session gate
    doesn't redirect unauthenticated webhook callers to /login. (The
    token check inside the route is the real auth.)"""
    resp = app.test_client().post("/api/v1/push", json={})
    # No token configured → 503, not a /login redirect.
    assert resp.status_code in (503, 401, 400)
    # In particular, NOT a 302 redirect to the login page.
    assert resp.status_code != 302


def test_regenerate_token_endpoint_persists_a_new_value(app: Flask) -> None:
    """Settings → System → Regenerate mints + saves a token and surfaces
    it once in a one-shot reveal modal (data-modal-token input on the
    follow-up GET). After that render the value is gone from the
    session; only the masked on-disk secret remains."""
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.post("/settings/system/webhook/regenerate", follow_redirects=True)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Token is 32 hex chars (TOKEN_BYTES=16, .hex() doubles it). It
    # renders inside the modal's <input data-modal-token value="…">.
    import re

    match = re.search(r'data-modal-token[^>]*value="([0-9a-f]{32})"', body)
    if match is None:
        # Attribute order isn't guaranteed; try the reverse pairing.
        match = re.search(r'value="([0-9a-f]{32})"[^>]*data-modal-token', body)
    assert match, "regenerate should reveal the new token in a modal"
    token = match.group(1)
    # The stored value matches the modal-displayed token. The on-disk
    # key is ``webhook_token_secret`` (``_secret`` suffix marks it for
    # masking in the admin UI); ``get_section`` returns raw keys.
    stored = app.config["SETTINGS_STORE"].get_section("app").get("webhook_token_secret")
    assert stored == token
    # Reload the System page, the session-stashed reveal is popped on
    # first GET, so a refresh must NOT re-show the token.
    refresh = client.get("/settings/system").get_data(as_text=True)
    assert token not in refresh


def test_set_endpoint_saves_a_custom_token(app: Flask) -> None:
    """Settings → System → Webhook also accepts a pasted custom token,
    for users who need to match a secret their automation tool already
    has. Posts to /settings/system/webhook/set."""
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.post(
        "/settings/system/webhook/set",
        data={"webhook_token": "my-custom-secret"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    stored = app.config["SETTINGS_STORE"].get_section("app").get("webhook_token_secret")
    assert stored == "my-custom-secret"


def test_set_endpoint_clear_wipes_the_token(app: Flask) -> None:
    """The Clear button on the Webhook card POSTs the same endpoint
    with ``clear=1`` and the on-disk secret is wiped, POST /api/v1/push
    returns 503 again."""
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    # Seed a token, then clear it.
    client.post("/settings/system/webhook/set", data={"webhook_token": "abc123"})
    resp = client.post(
        "/settings/system/webhook/set",
        data={"clear": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    stored = app.config["SETTINGS_STORE"].get_section("app").get("webhook_token_secret")
    assert not stored


# -- single-image push (discussion #231) ---------------------------------
#
# The sibling of /push for the case with no saved dashboard behind the
# frame. Same token, same quiet-hours policy; device_ids is required
# because a loose image has no bindings to fall back on.


def _png(size: tuple[int, int] = (400, 300)) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (30, 90, 160)).save(buf, format="PNG")
    return buf.getvalue()


def _register(app: Flask, device_id: str) -> None:
    import json

    client = app.test_client()
    code = app.config["PAIRING_STORE"].issue(note="t").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": device_id, "kind": "esp32_client"}),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)


def _post_image(app: Flask, **fields: object) -> object:
    import io

    data: dict[str, object] = {"image": (io.BytesIO(_png()), "photo.png", "image/png")}
    data.update(fields)
    return app.test_client().post(
        "/api/v1/push/image",
        headers={"Authorization": "Bearer real-token"},
        data=data,
        content_type="multipart/form-data",
    )


def test_image_push_sends_to_named_displays(app: Flask) -> None:
    _set_token(app, "real-token")
    _register(app, "kitchen")
    _register(app, "hallway")
    calls: list[dict[str, object]] = []

    def fake_push_image(image_bytes: bytes, **kw: object) -> PushResult:
        calls.append({"bytes": len(image_bytes), **kw})
        return PushResult(status="sent", page_id="", error=None)

    app.config["PUSH_MANAGER"].push_image = fake_push_image  # type: ignore[method-assign]

    resp = _post_image(app, device_ids="kitchen,hallway", fit="fill", label="Doorbell")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "sent"
    assert body["sent"] == ["kitchen", "hallway"]
    assert body["quiet"] == [] and body["failed"] == []
    # Each display gets its own push so the frame is fitted to its panel.
    assert [c["device_id"] for c in calls] == ["kitchen", "hallway"]
    assert {c["fit"] for c in calls} == {"fill"}
    assert {c["source_label"] for c in calls} == {"Doorbell"}


def test_image_push_accepts_repeated_device_fields(app: Flask) -> None:
    """Comma-separated or repeated: form encoders disagree, the endpoint
    shouldn't care."""
    _set_token(app, "real-token")
    _register(app, "kitchen")
    _register(app, "hallway")
    app.config["PUSH_MANAGER"].push_image = lambda b, **kw: PushResult(  # type: ignore[method-assign]
        status="sent", page_id="", error=None
    )
    resp = _post_image(app, device_ids=["kitchen", "hallway"])
    assert resp.status_code == 200
    assert resp.get_json()["sent"] == ["kitchen", "hallway"]


def test_image_push_requires_a_target(app: Flask) -> None:
    """A dashboard knows its displays; a loose image does not. Silence
    here would mean fanning a photo out to every panel in the house."""
    _set_token(app, "real-token")
    _register(app, "kitchen")
    resp = _post_image(app)
    assert resp.status_code == 400
    assert "device_ids" in resp.get_json()["error"]


def test_image_push_rejects_an_unknown_display(app: Flask) -> None:
    _set_token(app, "real-token")
    _register(app, "kitchen")
    resp = _post_image(app, device_ids="kitchen,ghost")
    assert resp.status_code == 404
    assert "ghost" in resp.get_json()["error"]


def test_image_push_needs_the_token(app: Flask) -> None:
    _set_token(app, "real-token")
    _register(app, "kitchen")
    import io

    resp = app.test_client().post(
        "/api/v1/push/image",
        headers={"Authorization": "Bearer wrong"},
        data={"image": (io.BytesIO(_png()), "p.png", "image/png"), "device_ids": "kitchen"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 401


def test_image_push_rejects_a_file_that_is_not_an_image(app: Flask) -> None:
    """Declaring an accepted media type doesn't make the bytes decodable."""
    _set_token(app, "real-token")
    _register(app, "kitchen")
    import io

    resp = app.test_client().post(
        "/api/v1/push/image",
        headers={"Authorization": "Bearer real-token"},
        data={
            "image": (io.BytesIO(b"definitely not a png"), "p.png", "image/png"),
            "device_ids": "kitchen",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 415

    wrong_type = app.test_client().post(
        "/api/v1/push/image",
        headers={"Authorization": "Bearer real-token"},
        data={"image": (io.BytesIO(b"hello"), "p.txt", "text/plain"), "device_ids": "kitchen"},
        content_type="multipart/form-data",
    )
    assert wrong_type.status_code == 415


def test_image_push_validates_the_fit_mode(app: Flask) -> None:
    _set_token(app, "real-token")
    _register(app, "kitchen")
    resp = _post_image(app, device_ids="kitchen", fit="squish")
    assert resp.status_code == 400
    assert "fit" in resp.get_json()["error"]


def test_image_push_passes_the_requested_turn_through(app: Flask) -> None:
    """``rotate`` reaches the push, for when the image and the panel
    disagree about which way is up (a portrait photo on a landscape
    panel). Unset means "keep the orientation it was shot in"."""
    _set_token(app, "real-token")
    _register(app, "kitchen")
    calls: list[dict[str, object]] = []
    app.config["PUSH_MANAGER"].push_image = lambda b, **kw: (  # type: ignore[method-assign]
        calls.append(kw),
        PushResult(status="sent", page_id="", error=None),
    )[1]

    assert _post_image(app, device_ids="kitchen", rotate="auto").status_code == 200
    assert _post_image(app, device_ids="kitchen").status_code == 200
    assert [c["rotate"] for c in calls] == ["auto", None]


def test_image_push_validates_the_rotate_mode(app: Flask) -> None:
    _set_token(app, "real-token")
    _register(app, "kitchen")
    resp = _post_image(app, device_ids="kitchen", rotate="45")
    assert resp.status_code == 400
    assert "rotate" in resp.get_json()["error"]


def test_image_push_holds_a_quiet_display_and_says_so(app: Flask) -> None:
    """202 rather than 200, and the display is named under `quiet`, so a
    script can tell a skip from a failure without counting."""
    _set_token(app, "real-token")
    _register(app, "kitchen")
    # patch, not update: update_section replaces the whole app section and
    # would drop the webhook token set above.
    app.config["SETTINGS_STORE"].patch_section(
        "app",
        {"quiet_hours_enabled": True, "quiet_hours_start": "00:00", "quiet_hours_end": "23:59"},
    )
    pushed: list[str] = []
    app.config["PUSH_MANAGER"].push_image = lambda b, **kw: (  # type: ignore[method-assign]
        pushed.append(str(kw["device_id"])),
        PushResult(status="sent", page_id="", error=None),
    )[1]

    resp = _post_image(app, device_ids="kitchen")
    assert resp.status_code == 202
    body = resp.get_json()
    assert body["status"] == "quiet" and body["quiet"] == ["kitchen"]
    assert pushed == []


def test_image_push_reports_a_partial_failure(app: Flask) -> None:
    _set_token(app, "real-token")
    _register(app, "kitchen")
    _register(app, "hallway")

    def flaky(image_bytes: bytes, **kw: object) -> PushResult:
        if kw["device_id"] == "hallway":
            return PushResult(status="failed", page_id="", error="renderer offline")
        return PushResult(status="sent", page_id="", error=None)

    app.config["PUSH_MANAGER"].push_image = flaky  # type: ignore[method-assign]
    resp = _post_image(app, device_ids="kitchen,hallway")
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["sent"] == ["kitchen"]
    assert body["failed"] == [{"device_id": "hallway", "error": "renderer offline"}]


def test_image_push_is_disabled_without_a_token(app: Flask) -> None:
    resp = _post_image(app, device_ids="kitchen")
    assert resp.status_code == 503
    assert resp.get_json()["status"] == "disabled"


def test_image_push_can_override_quiet_hours(app: Flask) -> None:
    """Not every image is ambient. A doorbell snapshot is worth a refresh
    at 3am, and the alternative was turning quiet hours off for the
    display and remembering to turn them back on."""
    _set_token(app, "real-token")
    _register(app, "kitchen")
    app.config["SETTINGS_STORE"].patch_section(
        "app",
        {"quiet_hours_enabled": True, "quiet_hours_start": "00:00", "quiet_hours_end": "23:59"},
    )
    pushed: list[str] = []
    app.config["PUSH_MANAGER"].push_image = lambda b, **kw: (  # type: ignore[method-assign]
        pushed.append(str(kw["device_id"])),
        PushResult(status="sent", page_id="", error=None),
    )[1]

    resp = _post_image(app, device_ids="kitchen", override_quiet_hours="true")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "sent" and body["sent"] == ["kitchen"]
    assert body["quiet"] == []
    assert pushed == ["kitchen"]


def test_quiet_hours_override_reads_the_spellings_form_encoders_send(app: Flask) -> None:
    """Forms have no boolean type, so the flag arrives as whatever the
    caller's tooling produced. Anything unrecognised stays off, because
    the safe default for "wake the bedroom panel" is no."""
    _set_token(app, "real-token")
    _register(app, "kitchen")
    app.config["SETTINGS_STORE"].patch_section(
        "app",
        {"quiet_hours_enabled": True, "quiet_hours_start": "00:00", "quiet_hours_end": "23:59"},
    )
    app.config["PUSH_MANAGER"].push_image = lambda b, **kw: PushResult(  # type: ignore[method-assign]
        status="sent", page_id="", error=None
    )

    for raw in ("1", "true", "TRUE", "yes", "on"):
        assert (
            _post_image(app, device_ids="kitchen", override_quiet_hours=raw).status_code == 200
        ), raw
    for raw in ("0", "false", "no", "", "maybe"):
        assert (
            _post_image(app, device_ids="kitchen", override_quiet_hours=raw).status_code == 202
        ), raw
