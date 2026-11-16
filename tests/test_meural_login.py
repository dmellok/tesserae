"""Authenticated Netgear sign-in, token storage and frame selection."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time

import pytest

from app import device_service
from app.main import REPO_ROOT, create_app
from app.meural_auth import AuthResult


@pytest.fixture
def app(tmp_path):
    app = create_app(testing=True, data_root=tmp_path)
    result = device_service.create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DATA_ROOT"],
        instance_id="test_frame",
        kind_id="meural",
        name="Example frame",
    )
    assert result.ok
    return app


def begin(client):
    response = client.get("/settings/devices/test_frame/meural/login")
    assert response.status_code == 200
    match = re.search(
        r'<script[^>]*id="meural-auth-config"[^>]*>(.*?)</script>',
        response.get_data(as_text=True),
        re.S,
    )
    return response, json.loads(match.group(1))


def payload(config):
    return {
        "state": config["state"],
        "trust_id": config["trust_id"],
        "email": "owner@example.com",
        "cognito_access_token": "c" * 120,
    }


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(
        "app.meural_auth.NetgearAuthenticator.exchange_cognito_token",
        lambda self, token: AuthResult(
            "private-access", "private-refresh", time.time() + 3600, self.trust_id
        ),
    )

    def request(method, url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Token private-access"
        if "user/devices" in url:
            return {"data": [{"id": 101, "alias": "Example frame", "localIp": "192.0.2.10"}]}
        return {"data": {"id": 17}}

    monkeypatch.setattr("app.meural_auth.request_json", request)


def test_signin_page_is_private_and_has_no_saved_secrets(app):
    response, config = begin(app.test_client())
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "cognito-idp.eu-west-1.amazonaws.com" in config["cognito_url"]
    assert len(config["state"]) >= 32
    assert "access_token" not in config


@pytest.mark.parametrize("scenario", ["password-mfa", "otp-first", "migration"])
def test_browser_challenges_keep_passwords_and_codes_at_netgear(app, scenario):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed for the browser sign-in boundary test")
    _, config = begin(app.test_client())
    completed = subprocess.run(
        [
            node,
            str(REPO_ROOT / "tests/fixtures/meural_login_flow.cjs"),
            str(REPO_ROOT / "static/meural-login.js"),
            scenario,
        ],
        input=json.dumps(config),
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "signed_in": True,
        "callbacks": 1,
    }


def test_callback_saves_an_encrypted_session_and_selects_the_only_frame(app, provider):
    client = app.test_client()
    _, config = begin(client)
    response = client.post("/settings/devices/test_frame/meural/login", json=payload(config))
    assert response.status_code == 200
    assert response.json["return_url"].endswith("/settings/devices/test_frame#connection")
    cfg = app.config["SETTINGS_STORE"].get_section("devices")["test_frame"]
    assert cfg["cloud_device_id"] == "101"
    assert cfg["meural_refresh_token_secret"] == "private-refresh"
    page = client.get("/settings/devices/test_frame").get_data(as_text=True)
    assert "Example frame" in page and "owner@example.com" in page
    assert "private-access" not in page and "private-refresh" not in page


def test_callback_is_single_use(app, provider):
    client = app.test_client()
    _, config = begin(client)
    assert (
        client.post("/settings/devices/test_frame/meural/login", json=payload(config)).status_code
        == 200
    )
    assert (
        client.post("/settings/devices/test_frame/meural/login", json=payload(config)).status_code
        == 400
    )


@pytest.mark.parametrize("invalid_endpoint", ["user", "user/devices"])
def test_invalid_provider_response_does_not_save_partial_session(
    app, provider, monkeypatch, invalid_endpoint
):
    from app import meural_auth

    original = meural_auth.request_json

    def request(method, url, **kwargs):
        if url.split("/v1/", 1)[1].split("?", 1)[0] == invalid_endpoint:
            return []
        return original(method, url, **kwargs)

    monkeypatch.setattr(meural_auth, "request_json", request)
    client = app.test_client()
    _, config = begin(client)
    assert (
        client.post("/settings/devices/test_frame/meural/login", json=payload(config)).status_code
        == 400
    )
    assert not (app.config["SETTINGS_STORE"].get_section("devices").get("test_frame") or {}).get(
        "meural_refresh_token_secret"
    )


@pytest.mark.parametrize("change", ["state", "trust_id", "cognito_access_token"])
def test_callback_rejects_wrong_state_identity_or_token(app, provider, change):
    client = app.test_client()
    _, config = begin(client)
    data = payload(config)
    data[change] = "wrong"
    assert client.post("/settings/devices/test_frame/meural/login", json=data).status_code == 400


def test_signout_during_token_exchange_cannot_restore_the_session(app, provider, monkeypatch):
    client = app.test_client()
    _, config = begin(client)

    def exchange(self, token):
        assert client.post("/settings/devices/test_frame/meural/logout").status_code == 302
        return AuthResult("private-access", "late-refresh", time.time() + 3600, self.trust_id)

    monkeypatch.setattr("app.meural_auth.NetgearAuthenticator.exchange_cognito_token", exchange)
    response = client.post("/settings/devices/test_frame/meural/login", json=payload(config))
    assert response.status_code == 400
    assert not (app.config["SETTINGS_STORE"].get_section("devices").get("test_frame") or {}).get(
        "meural_refresh_token_secret"
    )


def test_cross_origin_signin_result_is_rejected(app, provider):
    client = app.test_client()
    _, config = begin(client)
    response = client.post(
        "/settings/devices/test_frame/meural/login",
        json=payload(config),
        headers={"Origin": "https://unrelated.example"},
    )
    assert response.status_code == 403


def test_signout_clears_tokens_without_deleting_cloud_artwork(app, provider):
    client = app.test_client()
    _, config = begin(client)
    assert (
        client.post("/settings/devices/test_frame/meural/login", json=payload(config)).status_code
        == 200
    )
    assert client.post("/settings/devices/test_frame/meural/logout").status_code == 302
    cfg = app.config["SETTINGS_STORE"].get_section("devices")["test_frame"]
    assert not cfg.get("meural_refresh_token_secret")
    assert cfg["cloud_device_id"] == "101"


def test_frame_picker_rejects_a_frame_outside_the_linked_account(app, provider):
    client = app.test_client()
    _, config = begin(client)
    client.post("/settings/devices/test_frame/meural/login", json=payload(config))
    response = client.post(
        "/settings/devices/test_frame/meural/frame", data={"cloud_device_id": "999"}
    )
    assert response.status_code == 400
    assert (
        app.config["SETTINGS_STORE"].get_section("devices")["test_frame"]["cloud_device_id"]
        == "101"
    )


def held_frame(app, client):
    from app.meural_cloud import delivery_for_device

    _, config = begin(client)
    assert (
        client.post("/settings/devices/test_frame/meural/login", json=payload(config)).status_code
        == 200
    )
    delivery, target = delivery_for_device(app, app.config["DEVICE_REGISTRY"].devices["test_frame"])
    original = {
        "gallery_id": "27",
        "item_id": "28",
        "settings": {"galleryRotation": True, "schedulerEnabled": False},
    }
    delivery.path.parent.mkdir(parents=True, exist_ok=True)
    delivery.path.write_text(
        json.dumps(
            {
                "scope": f"17:{target}",
                "owned": [],
                "original": original,
                "paused": True,
                "last_key": "current",
            }
        )
    )
    return delivery, original


def test_resume_native_playback_stops_delivery_and_restores_saved_settings(
    app, provider, monkeypatch
):
    client = app.test_client()
    delivery, original = held_frame(app, client)
    restored = []
    monkeypatch.setattr(
        "app.meural_cloud.MeuralCloudClient.restore_playback",
        lambda self, target, state: restored.append((target, state)),
    )
    response = client.post("/settings/devices/test_frame/meural/manage", data={"action": "release"})
    assert response.status_code == 302
    assert restored == [("101", original)]
    assert app.config["SETTINGS_STORE"].get_section("devices")["test_frame"][
        "meural_management_paused"
    ]
    page = client.get("/settings/devices/test_frame").get_data(as_text=True)
    assert "creates no Meural galleries" in page
    assert "Let Tesserae manage" in page
    assert not json.loads(delivery.path.read_text()).get("original")
    assert (
        client.post(
            "/settings/devices/test_frame/meural/manage", data={"action": "manage"}
        ).status_code
        == 302
    )
    assert not app.config["SETTINGS_STORE"].get_section("devices")["test_frame"][
        "meural_management_paused"
    ]


def test_signout_restores_before_removing_credentials(app, provider, monkeypatch):
    client = app.test_client()
    _, original = held_frame(app, client)
    restored = []

    def restore(self, target, state):
        assert self.config()["meural_refresh_token_secret"] == "private-refresh"
        restored.append((target, state))

    monkeypatch.setattr("app.meural_cloud.MeuralCloudClient.restore_playback", restore)
    assert client.post("/settings/devices/test_frame/meural/logout").status_code == 302
    assert restored == [("101", original)]
    assert (
        not app.config["SETTINGS_STORE"]
        .get_section("devices")["test_frame"]
        .get("meural_refresh_token_secret")
    )


def test_failed_restore_keeps_credentials_and_blocks_delivery_for_retry(app, provider, monkeypatch):
    from app.meural_auth import CannotConnect

    client = app.test_client()
    delivery, original = held_frame(app, client)

    def offline(*args):
        raise CannotConnect("offline")

    monkeypatch.setattr("app.meural_cloud.MeuralCloudClient.restore_playback", offline)
    assert client.post("/settings/devices/test_frame/meural/logout").status_code == 409
    cfg = app.config["SETTINGS_STORE"].get_section("devices")["test_frame"]
    assert cfg["meural_refresh_token_secret"] == "private-refresh"
    assert cfg["meural_management_paused"]
    assert json.loads(delivery.path.read_text())["original"] == original


def test_deleting_managed_frame_restores_native_playback_first(app, provider, monkeypatch):
    client = app.test_client()
    _, original = held_frame(app, client)
    restored = []
    monkeypatch.setattr(
        "app.meural_cloud.MeuralCloudClient.restore_playback",
        lambda self, target, state: restored.append((target, state)),
    )
    assert client.post("/settings/devices/test_frame/delete").status_code == 302
    assert restored == [("101", original)]
    assert "test_frame" not in app.config["DEVICE_REGISTRY"].devices


def test_reauthentication_of_same_frame_does_not_restore_playback(app, provider, monkeypatch):
    from app.meural_auth import AuthResult
    from app.meural_cloud import save_tokens

    client = app.test_client()
    held_frame(app, client)
    save_tokens(
        app.config["SETTINGS_STORE"],
        "test_frame",
        AuthResult("expired", "expired-refresh", 1, "trusted"),
    )
    restored = []

    def restore(self, target, original):
        assert self.config()["meural_access_token_secret"] == "private-access"
        restored.append(target)

    monkeypatch.setattr("app.meural_cloud.MeuralCloudClient.restore_playback", restore)
    _, config = begin(client)
    assert (
        client.post("/settings/devices/test_frame/meural/login", json=payload(config)).status_code
        == 200
    )
    assert restored == []


@pytest.mark.parametrize("owned_sleep", [True, False])
def test_release_wakes_only_a_frame_tesserae_put_to_sleep(app, provider, monkeypatch, owned_sleep):
    from pathlib import Path

    client = app.test_client()
    held_frame(app, client)
    device = app.config["DEVICE_REGISTRY"].devices["test_frame"]
    sleep = Path(device.data_dir) / "meural_sleep.json"
    if owned_sleep:
        sleep.write_text(json.dumps({"host": "192.0.2.10"}))
    wake = []

    class Frame:
        def __init__(self, host):
            assert owned_sleep
            assert host == "192.0.2.10"

        def is_sleeping(self):
            return True

        def set_sleep(self, sleeping):
            wake.append(sleeping)

    monkeypatch.setattr("app.meural.MeuralClient", Frame)
    monkeypatch.setattr("app.meural_cloud.MeuralCloudClient.restore_playback", lambda *a: None)
    assert (
        client.post(
            "/settings/devices/test_frame/meural/manage", data={"action": "release"}
        ).status_code
        == 302
    )
    assert wake == ([False] if owned_sleep else [])
    assert not sleep.exists()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Meural tests must replace the HTTP boundary")

    monkeypatch.setattr("app.meural_auth.open_request", blocked)


@pytest.mark.parametrize("width", [1280, 390])
def test_browser_signin_mfa_and_saved_account(app, provider, width):
    import os
    from pathlib import Path

    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        if not Path(executable or playwright.chromium.executable_path).exists():
            pytest.skip("Playwright Chromium not installed")
        browser = playwright.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": width, "height": 900})
        client = app.test_client()
        local_bodies = []
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def respond(route):
            req = route.request
            if req.url == "https://cognito-idp.eu-west-1.amazonaws.com/":
                if req.method == "OPTIONS":
                    route.fulfill(
                        status=204,
                        headers={
                            "Access-Control-Allow-Origin": "*",
                            "Access-Control-Allow-Headers": "*",
                            "Access-Control-Allow-Methods": "POST",
                        },
                    )
                    return
                data = req.post_data_json
                if "InitiateAuth" in req.headers["x-amz-target"]:
                    result = {"ChallengeName": "EMAIL_MFA", "Session": "example-session"}
                else:
                    assert data["ChallengeResponses"]["EMAIL_MFA_CODE"] == "123456"
                    result = {"AuthenticationResult": {"AccessToken": "c" * 120}}
                route.fulfill(json=result, headers={"Access-Control-Allow-Origin": "*"})
                return
            assert req.url.startswith("http://tesserae.test/")
            if req.post_data:
                local_bodies.append(req.post_data)
            response = client.open(
                req.url.removeprefix("http://tesserae.test"),
                method=req.method,
                data=req.post_data,
                headers={"Content-Type": req.headers.get("content-type", "")},
                base_url="http://tesserae.test",
            )
            route.fulfill(
                status=response.status_code, body=response.data, headers=dict(response.headers)
            )

        page.route("**/*", respond)
        page.goto("http://tesserae.test/settings/devices/test_frame/meural/login")
        page.get_by_label("Email", exact=True).fill("owner@example.com")
        page.get_by_label("Password", exact=True).fill("example-password")
        page.get_by_role("button", name="Sign in with Netgear").click()
        page.get_by_label("Verification code").fill("123456")
        page.get_by_role("button", name="Verify", exact=True).click()
        expect(page.get_by_role("heading", name="Signed in", exact=True)).to_be_visible()
        assert page.locator("#password").input_value() == ""
        assert page.locator("#code").input_value() == ""
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert all("example-password" not in body and "123456" not in body for body in local_bodies)
        page.get_by_role("link", name="Return to display settings").click()
        expect(page.get_by_text("Signed in as owner@example.com.", exact=True)).to_be_visible()
        assert (
            page.locator("#meural-account")
            .get_by_role("button", name="Resume Meural gallery")
            .is_visible()
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors
        browser.close()


@pytest.mark.parametrize("endpoint", ["logout", "frame", "login"])
def test_rejections_are_json(app, endpoint):
    response = app.test_client().post(
        f"/settings/devices/test_frame/meural/{endpoint}",
        data={"cloud_device_id": "999"} if endpoint == "frame" else "bad",
        content_type="text/plain",
    )
    if endpoint == "logout":
        return
    assert response.status_code == 400
    assert response.is_json and response.json["error"]


@pytest.mark.parametrize("action", ["logout", "delete", "switch", "frame"])
def test_restore_failure_requires_explicit_remove_anyway(app, provider, monkeypatch, action):
    from app.meural_auth import CannotConnect

    client = app.test_client()
    held_frame(app, client)

    def offline(*args):
        raise CannotConnect("offline")

    monkeypatch.setattr("app.meural_cloud.MeuralCloudClient.restore_playback", offline)
    data = {}
    endpoint = "meural/" + action
    if action == "delete":
        endpoint = "delete"
    elif action == "frame":
        app.config["SETTINGS_STORE"].update_item(
            "devices",
            "test_frame",
            {"meural_devices": [{"id": "102", "name": "Other", "host": "192.0.2.11"}]},
        )
        data = {"cloud_device_id": "102"}
    elif action == "switch":
        endpoint = "meural/login"

        def other(method, url, **kw):
            return {"data": [{"id": 102}]} if "user/devices" in url else {"data": {"id": 18}}

        monkeypatch.setattr("app.meural_auth.request_json", other)
        _, config = begin(client)
        data = payload(config)
    path = "/settings/devices/test_frame/" + endpoint
    kwargs = {"json": data} if action == "switch" else {"data": data}
    response = client.post(path, **kwargs)
    assert response.status_code == 409
    assert response.json["restore_failed"]
    assert "may stay paused" in response.json["error"]
    data["remove_anyway"] = True if action == "switch" else "1"
    response = client.post(path, **kwargs)
    assert response.status_code == (200 if action == "switch" else 302)
    cfg = app.config["SETTINGS_STORE"].get_section("devices").get("test_frame", {})
    if action in ("delete", "logout"):
        assert not cfg.get("meural_refresh_token_secret")
        assert "private-refresh" not in json.dumps(
            app.config["SETTINGS_STORE"].raw_section("devices")
        )
    elif action == "switch":
        assert cfg["meural_account_id"] == "18"
    else:
        assert cfg["cloud_device_id"] == "102"


def test_expired_session_is_visible_in_account_state(app, provider):
    client = app.test_client()
    held_frame(app, client)
    app.config["SETTINGS_STORE"].update_item("devices", "test_frame", {"meural_auth_failed": True})
    state = client.get("/settings/devices/test_frame/meural/account").json["account"]
    assert state["state"] == "session_expired"
    assert state["account_label"] == "owner@example.com"


def test_duplicate_frame_is_rejected(app, provider):
    client = app.test_client()
    held_frame(app, client)
    result = device_service.create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DATA_ROOT"],
        instance_id="other_frame",
        kind_id="meural",
        name="Other display",
    )
    assert result.ok
    app.config["SETTINGS_STORE"].update_item(
        "devices", "other_frame", {"meural_account_id": "17", "cloud_device_id": "101"}
    )
    response = client.post(
        "/settings/devices/test_frame/meural/frame", data={"cloud_device_id": "101"}
    )
    assert response.status_code == 400
    assert "another Tesserae display" in response.json["error"]


def test_failed_signin_exchange_does_not_log_or_return_credentials(app, monkeypatch, caplog):
    from app.meural_auth import HttpError

    private = "example-password-mfa-auth-code-access-refresh"

    def fail(self, token):
        raise HttpError(401, {"message": private})

    monkeypatch.setattr("app.meural_auth.NetgearAuthenticator.exchange_cognito_token", fail)
    client = app.test_client()
    _, config = begin(client)
    response = client.post("/settings/devices/test_frame/meural/login", json=payload(config))
    assert response.status_code == 400
    assert response.is_json
    assert private not in response.text + caplog.text


def test_frame_json_selection_returns_saved_account_state(app, provider):
    client = app.test_client()
    held_frame(app, client)
    response = client.post(
        "/settings/devices/test_frame/meural/frame",
        json={"cloud_device_id": "101"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    assert response.json["account"]["saved"] is True
    assert response.json["account"]["chosen_frame"] == {"id": "101", "name": "Example frame"}
