"""App-wide cookie request provenance, including real proxy middleware."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from flask import Flask

from app import auth
from app.main import create_app


@pytest.fixture
def app(tmp_path: Path, monkeypatch) -> Flask:
    monkeypatch.setenv("TESSERAE_SECRET_KEY", "00" * 32)
    app = create_app(testing=True, data_root=tmp_path)
    app.config["CSRF_TEST_WRITES"] = []

    @app.route("/_csrf_probe/<item>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def mutate(item: str):
        app.config["CSRF_TEST_WRITES"].append(item)
        return "", 204

    return app


@pytest.fixture
def gated_app(app) -> Flask:
    auth.install_gate(app, app.config["SETTINGS_STORE"])
    return app


@pytest.mark.parametrize("action", ["setup", "login", "forget"])
@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "http://localhost:9999"},
        {"Referer": "http://localhost:9999/attack"},
    ],
)
def test_cookie_free_admin_forms_reject_cross_origin(gated_app, action, headers) -> None:
    store = gated_app.config["SETTINGS_STORE"]
    if action != "setup":
        auth.set_password(store, "test-password")
    if action == "forget":
        auth.set_password_disabled(store, True)
    before = store.get_section("auth")
    client = gated_app.test_client()
    path = "/settings/system/auth/forget" if action == "forget" else f"/{action}"
    response = client.post(
        path,
        headers=headers,
        data={"password": "test-password", "password_confirm": "test-password", "confirmed": "1"},
    )
    assert response.status_code == 403
    assert store.get_section("auth") == before
    assert client.get_cookie("session") is None


@pytest.mark.parametrize("action", ["setup", "login", "forget"])
def test_cookie_free_same_origin_admin_forms_work(gated_app, action) -> None:
    store = gated_app.config["SETTINGS_STORE"]
    if action != "setup":
        auth.set_password(store, "test-password")
    if action == "forget":
        auth.set_password_disabled(store, True)
    client = gated_app.test_client()
    path = "/settings/system/auth/forget" if action == "forget" else f"/{action}"
    response = client.post(
        path,
        headers={"Origin": "http://localhost"},
        data={"password": "test-password", "password_confirm": "test-password", "confirmed": "1"},
    )
    assert response.status_code == 302
    assert auth.password_is_set(store) == (action != "forget")
    if action != "forget":
        with client.session_transaction() as session:
            assert session["authed"]


@pytest.mark.parametrize("cookie", [False, True])
def test_session_only_pairing_api_is_not_exempt(gated_app, cookie) -> None:
    client = gated_app.test_client()
    if cookie:
        with client.session_transaction() as session:
            session["authed"] = True
    assert (
        client.post(
            "/api/v1/device/admin/pairing/issue", headers={"Origin": "http://elsewhere.test"}
        ).status_code
        == 403
    )


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "unknown"},
        {"Sec-Fetch-Site": ""},
        {"Origin": "http://localhost:9999"},
        {"Referer": "http://localhost:9999/form"},
        {"Origin": "https://localhost"},
        {"Origin": "http://elsewhere.test"},
        {"Origin": "null"},
        {"Origin": "broken"},
        {"Origin": "http://localhost:bad"},
        {"Origin": "http://localhost.attacker.test"},
        {"Origin": "http://localhost", "Sec-Fetch-Site": "same-site"},
        {"Origin": "null", "Referer": "http://localhost/form"},
    ],
)
def test_cross_origin_cookie_mutations_are_rejected(app, method, headers) -> None:
    client = app.test_client()
    # Presence matters, even when Flask cannot decode the cookie.
    client.set_cookie("session", "invalid-session")
    response = client.open("/_csrf_probe/one", method=method, headers=headers)
    assert response.status_code == 403
    assert "cross-origin" in response.text
    assert app.config["CSRF_TEST_WRITES"] == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "none"},
        {"Origin": "http://localhost"},
        {"Origin": "http://LOCALHOST:80"},
        {"Referer": "http://localhost/settings?tab=server"},
        {},  # Legacy browsers with every provenance header absent are allowed.
    ],
)
def test_same_origin_and_legacy_cookie_posts_are_accepted(app, headers) -> None:
    client = app.test_client()
    with client.session_transaction() as session:
        session["authed"] = True
    assert client.post("/_csrf_probe/one", headers=headers).status_code == 204
    assert app.config["CSRF_TEST_WRITES"] == ["one"]


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_are_not_blocked(app, method) -> None:
    client = app.test_client()
    client.set_cookie("session", "test")
    assert (
        client.open(
            "/_csrf_probe/one", method=method, headers={"Sec-Fetch-Site": "cross-site"}
        ).status_code
        != 403
    )


def test_cookie_name_is_configurable(app) -> None:
    app.config["SESSION_COOKIE_NAME"] = "tesserae-session"
    client = app.test_client()
    client.set_cookie("tesserae-session", "test")
    assert (
        client.post("/_csrf_probe/one", headers={"Origin": "http://localhost:9999"}).status_code
        == 403
    )


def test_the_403_says_how_to_fix_a_proxy_that_does_not_forward_the_host(app) -> None:
    app.config["SESSION_COOKIE_NAME"] = "session"
    client = app.test_client()
    client.set_cookie("session", "test")
    response = client.post("/_csrf_probe/one", headers={"Origin": "http://localhost:9999"})
    assert response.status_code == 403
    body = response.get_data(as_text=True)
    assert "Settings → Server → Public URL" in body
    assert "X-Forwarded-Proto" in body and "X-Forwarded-Host" in body


@pytest.mark.parametrize(
    "headers", [{"Sec-Fetch-Site": "same-site"}, {"Origin": "http://localhost:9999"}]
)
def test_logged_in_cross_port_form_cannot_disable_password(app, headers) -> None:
    store = app.config["SETTINGS_STORE"]
    auth.set_password(store, "csrf-test-password")
    auth.install_gate(app, store)
    client = app.test_client()
    with client.session_transaction() as session:
        session["authed"] = True
    path = "/settings/system/auth/disable"
    assert client.post(path, data={"confirmed": "1"}, headers=headers).status_code == 403
    assert auth.password_required(store)
    assert (
        client.post(
            path, data={"confirmed": "1"}, headers={"Origin": "http://localhost"}
        ).status_code
        == 302
    )
    assert not auth.password_required(store)


@pytest.mark.parametrize("source_header", ["Origin", "Referer"])
def test_ingress_forwarded_origin_is_accepted_but_another_port_is_rejected(
    gated_app, source_header
) -> None:
    app = gated_app
    app.config["HA_INGRESS_MODE"] = True
    client = app.test_client()
    client.set_cookie("session", "test")
    headers = {
        "X-Forwarded-Host": "homeassistant.local",
        "X-Forwarded-Port": "8123",
        "X-Forwarded-Proto": "https",
        "X-Ingress-Path": "/api/hassio_ingress/test-prefix",
        source_header: "https://homeassistant.local:8123",
    }
    path = "/api/hassio_ingress/test-prefix/_csrf_probe/one"
    assert client.post(path, headers=headers).status_code == 204
    headers[source_header] = "https://homeassistant.local:9999"
    assert client.post(path, headers=headers).status_code == 403


def test_public_url_override_is_the_effective_origin(app) -> None:
    app.config["SETTINGS_STORE"].patch_section("app", {"public_url": "https://paper.test:8443"})
    client = app.test_client()
    client.set_cookie("session", "test")
    assert (
        client.post("/_csrf_probe/one", headers={"Origin": "https://paper.test:8443"}).status_code
        == 204
    )
    assert (
        client.post("/_csrf_probe/one", headers={"Origin": "http://elsewhere.test"}).status_code
        == 403
    )


@pytest.mark.parametrize(
    "request_origin,origin",
    [
        ("http://192.168.1.23:8765", "http://192.168.1.23:8765"),
        ("https://public.test", "https://public.test"),
        ("http://192.168.1.23:8765", "https://public.test"),
    ],
)
@pytest.mark.parametrize("source_header", ["Origin", "Referer"])
def test_public_url_also_allows_direct_lan_admin_forms(
    gated_app, request_origin, origin, source_header
) -> None:
    store = gated_app.config["SETTINGS_STORE"]
    store.patch_section("app", {"public_url": "https://public.test/paper"})
    auth.set_password_disabled(store, True)
    client = gated_app.test_client()
    response = client.post(
        "/settings/system/auth/enable",
        base_url=request_origin,
        environ_overrides={"REMOTE_ADDR": "192.168.1.45"},
        headers={source_header: origin},
    )
    assert response.status_code == 302
    assert auth.password_required(store)


def test_public_url_does_not_allow_another_lan_port(gated_app) -> None:
    store = gated_app.config["SETTINGS_STORE"]
    store.patch_section("app", {"public_url": "https://public.test/paper"})
    auth.set_password_disabled(store, True)
    response = gated_app.test_client().post(
        "/settings/system/auth/enable",
        base_url="http://192.168.1.23:8765",
        environ_overrides={"REMOTE_ADDR": "192.168.1.45"},
        headers={"Origin": "http://192.168.1.23:9999"},
    )
    assert response.status_code == 403
    assert not auth.password_required(store)


@pytest.mark.parametrize("peer", ["203.0.113.9", "2001:db8::9"])
@pytest.mark.parametrize("source_header", ["Origin", "Referer"])
def test_untrusted_peer_cannot_spoof_the_setup_origin(gated_app, peer, source_header) -> None:
    response = gated_app.test_client().post(
        "/setup",
        environ_overrides={"REMOTE_ADDR": peer},
        headers={
            "X-Forwarded-Host": "attacker.test",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Port": "8443",
            source_header: "https://attacker.test:8443",
        },
        data={"password": "test-password", "password_confirm": "test-password"},
    )
    assert response.status_code == 403
    assert not auth.password_is_set(gated_app.config["SETTINGS_STORE"])


def test_untrusted_ingress_header_does_not_bypass_auth(gated_app) -> None:
    gated_app.config["HA_INGRESS_MODE"] = True
    auth.set_password(gated_app.config["SETTINGS_STORE"], "test-password")
    response = gated_app.test_client().get(
        "/settings/system",
        environ_overrides={"REMOTE_ADDR": "203.0.113.9"},
        headers={"X-Ingress-Path": "/api/hassio_ingress/forged"},
    )
    assert response.status_code == 302
    assert "/login" in response.location


@pytest.mark.parametrize("path", ["/pages/new", "/pages/canvas/"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_dashboard_creation_reads_never_mutate(gated_app, path, method) -> None:
    client = gated_app.test_client()
    auth.set_password(gated_app.config["SETTINGS_STORE"], "test-password")
    with client.session_transaction() as session:
        session["authed"] = True
    before = gated_app.config["PAGE_STORE"].list()
    response = client.open(path, method=method, headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 302
    assert gated_app.config["PAGE_STORE"].list() == before
    assert response.location.startswith("/pages?create=1")
    if path == "/pages/canvas/" and method == "GET":
        form = client.get(response.location).text
        assert 'method="post" action="/pages/new"' in form
        assert '<option value="canvas" selected>' in form


@pytest.mark.parametrize("path", ["/pages/new", "/pages/canvas/new"])
@pytest.mark.parametrize("cookie", [False, True])
def test_dashboard_creation_requires_same_origin_post(gated_app, path, cookie) -> None:
    client = gated_app.test_client()
    store = gated_app.config["SETTINGS_STORE"]
    if cookie:
        auth.set_password(store, "test-password")
        with client.session_transaction() as session:
            session["authed"] = True
    else:
        auth.set_password_disabled(store, True)
    pages = gated_app.config["PAGE_STORE"]
    before = len(pages.list())
    assert client.post(path, headers={"Origin": "http://elsewhere.test"}).status_code == 403
    assert len(pages.list()) == before
    assert client.post(path, headers={"Origin": "http://localhost"}).status_code == 302
    assert len(pages.list()) == before + 1


def test_rejections_are_logged_once_per_route_without_request_secrets(app, caplog) -> None:
    client = app.test_client()
    client.set_cookie("session", "test")
    with caplog.at_level(logging.WARNING, logger="app.csrf"):
        for item in ("one", "two"):
            assert (
                client.post(
                    f"/_csrf_probe/{item}",
                    headers={"Referer": "https://elsewhere.test/?token=private"},
                ).status_code
                == 403
            )
    records = [record for record in caplog.records if record.name == "app.csrf"]
    assert len(records) == 1
    assert "/_csrf_probe/<item>" in records[0].message
    assert "private" not in records[0].message


@pytest.mark.parametrize(
    "path,body,headers,status",
    [
        ("/api/mcp/pages", {}, {}, 403),  # Token-free loopback still checks browser provenance.
        (
            "/api/mcp/pages",
            {},
            {"Authorization": "Bearer mcp-test", "X-Forwarded-For": "10.0.0.2"},
            200,
        ),
        ("/api/v1/push", {}, {"Authorization": "Bearer webhook-test"}, 400),
        ("/api/v1/device/probe/status", {}, {"Authorization": "Bearer bad-token"}, 401),
        ("/api/v1/device/register", {}, {}, 400),
    ],
)
def test_cookie_free_api_clients_reach_their_existing_auth_and_validation(
    app, path, body, headers, status
) -> None:
    auth.install_gate(app, app.config["SETTINGS_STORE"])
    app.config["SETTINGS_STORE"].patch_section(
        "app", {"mcp_token_secret": "mcp-test", "webhook_token_secret": "webhook-test"}
    )
    headers = {**headers, "Sec-Fetch-Site": "cross-site", "Origin": "http://elsewhere.test"}
    response = app.test_client().post(path, json=body, headers=headers)
    assert response.status_code == status


def test_device_registration_heartbeat_frame_and_ota_report_without_cookie(app) -> None:
    auth.install_gate(app, app.config["SETTINGS_STORE"])
    client = app.test_client()
    headers = {"Origin": "http://elsewhere.test", "Sec-Fetch-Site": "cross-site"}
    code = app.config["PAIRING_STORE"].issue(note="csrf-test").code
    response = client.post(
        "/api/v1/device/register",
        headers={**headers, "X-Pairing-Code": code},
        json={"device_id": "csrf-panel", "kind": "pico_bin_client"},
    )
    assert response.status_code == 201
    token = response.json["device_token"]
    headers["Authorization"] = f"Bearer {token}"
    response = client.post(
        "/api/v1/device/csrf-panel/status",
        headers=headers,
        json={"battery_pct": 80, "ota": {"schema": 1, "phase": "confirmed", "target_fw": "1.0"}},
    )
    assert response.status_code == 200
    assert client.get("/api/v1/device/csrf-panel/frame", headers=headers).status_code == 204


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://localhost:9999"},
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Referer": "http://localhost:9999/attack"},
    ],
)
@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded"])
def test_token_free_loopback_mcp_rejects_browser_csrf(gated_app, headers, content_type) -> None:
    auth.set_password(gated_app.config["SETTINGS_STORE"], "test-password")
    before = gated_app.config["PAGE_STORE"].list()
    response = gated_app.test_client().post(
        "/api/mcp/pages", headers=headers, content_type=content_type, data="name=attacker"
    )
    assert response.status_code == 403
    assert gated_app.config["PAGE_STORE"].list() == before


@pytest.mark.parametrize("token_header", ["Authorization", "X-Tesserae-Token"])
@pytest.mark.parametrize("peer", ["127.0.0.1", "203.0.113.9"])
def test_explicit_mcp_token_allows_cross_origin_clients(gated_app, token_header, peer) -> None:
    gated_app.config["SETTINGS_STORE"].patch_section("app", {"mcp_token_secret": "mcp-test"})
    credential = "Bearer mcp-test" if token_header == "Authorization" else "mcp-test"
    before = len(gated_app.config["PAGE_STORE"].list())
    response = gated_app.test_client().post(
        "/api/mcp/pages",
        headers={
            token_header: credential,
            "Origin": "http://elsewhere.test",
            "Sec-Fetch-Site": "cross-site",
        },
        environ_overrides={"REMOTE_ADDR": peer},
        json={"name": "Token client"},
    )
    assert response.status_code == 200
    assert len(gated_app.config["PAGE_STORE"].list()) == before + 1


@pytest.mark.parametrize(
    "token_headers",
    [
        {"Authorization": "Bearer wrong"},
        {"X-Tesserae-Token": "wrong"},
        {"Authorization": "Bearer café"},
        {"X-Tesserae-Token": "café"},
    ],
)
def test_invalid_token_cannot_exempt_loopback_mcp(gated_app, token_headers) -> None:
    gated_app.config["SETTINGS_STORE"].patch_section("app", {"mcp_token_secret": "mcp-test"})
    before = gated_app.config["PAGE_STORE"].list()
    response = gated_app.test_client().post(
        "/api/mcp/pages",
        headers={**token_headers, "Origin": "http://localhost:9999"},
        json={},
    )
    assert response.status_code == 403
    assert gated_app.config["PAGE_STORE"].list() == before


@pytest.mark.parametrize("user_agent", ["Claude MCP bridge", "Codex MCP bridge"])
def test_native_token_free_mcp_bridges_without_origin_still_work(gated_app, user_agent) -> None:
    auth.set_password(gated_app.config["SETTINGS_STORE"], "test-password")
    response = gated_app.test_client().post(
        "/api/mcp/pages", headers={"User-Agent": user_agent}, json={"name": "Native client"}
    )
    assert response.status_code == 200
    assert gated_app.config["PAGE_STORE"].get(response.json["id"]).name == "Native client"


def _blocked_body(app, headers) -> str:
    client = app.test_client()
    client.set_cookie("session", "test")
    response = client.post("/_csrf_probe/one", headers=headers)
    assert response.status_code == 403
    return response.get_data(as_text=True)


def test_the_403_shows_the_origin_the_browser_sent_and_the_one_expected(app) -> None:
    body = _blocked_body(app, {"Origin": "http://192.168.88.5:8123"})
    assert "Browser sent: Origin: http://192.168.88.5:8123" in body
    assert "Expected:     http://localhost" in body
    assert "Public URL" in body


@pytest.mark.parametrize("origin", ["null", "file://"])
def test_a_stripped_origin_points_at_software_on_the_computer(app, origin) -> None:
    # Privacy extensions, antivirus web shields and no-referrer settings make
    # a same-site POST carry ``Origin: null``. A sandboxed attacker frame sends
    # the same thing, so it stays refused; the message has to say why.
    body = _blocked_body(app, {"Origin": origin})
    assert "Browser sent: Origin: null (no usable origin)" in body
    assert "extension" in body and "antivirus" in body
    assert "X-Forwarded-Host" not in body


def test_admin_pages_ask_for_a_same_origin_referrer_policy(gated_app) -> None:
    # Home Assistant Ingress forces ``Referrer-Policy: no-referrer``, which
    # makes a plain form POST carry ``Origin: null`` and, over HTTP (no
    # Sec-Fetch-Site), get refused above. The page's own meta tag overrides
    # the header so same-origin forms send their real Origin. (#497)
    page = gated_app.test_client().get("/setup").get_data(as_text=True)
    assert '<meta name="referrer" content="same-origin">' in page


def test_a_stripped_origin_also_names_a_proxy_referrer_policy(app) -> None:
    body = _blocked_body(app, {"Origin": "null"})
    assert "Referrer-Policy: no-referrer" in body


def test_a_cross_site_fetch_metadata_rejection_says_so(app) -> None:
    body = _blocked_body(app, {"Sec-Fetch-Site": "cross-site"})
    assert "Browser sent: Sec-Fetch-Site: cross-site" in body
    assert "another site" in body


def test_the_log_carries_bare_origins_only(app, caplog) -> None:
    client = app.test_client()
    client.set_cookie("session", "test")
    with caplog.at_level(logging.WARNING, logger="app.csrf"):
        client.post(
            "/_csrf_probe/x",
            headers={"Referer": "http://[fd00::5]:8123/api/hassio_ingress/secret/?t=private"},
        )
        client.post("/_csrf_probe/y", headers={"Origin": "null"})
    messages = [r.message for r in caplog.records if r.name == "app.csrf"]
    assert len(messages) == 2  # a different kind of failure on one route logs again
    assert "Referer: http://[fd00::5]:8123" in messages[0]
    assert "expected http://localhost" in messages[0]
    assert not any("secret" in m or "private" in m for m in messages)
