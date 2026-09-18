"""The sign-in and first-run setup pages render a bare shell.

Without a session the base template used to show the full admin
chrome: top navigation, the device battery popover naming every
registered device with its charge, the update badge, and the running
version in the footer. Anyone who could reach the port could read them
(reported on Discord). ``auth.shell_locked`` now withholds that data at
the context-processor level and the template skips the chrome.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app import app_factory
from app.main import REPO_ROOT, create_app

PASSWORD = "abcdefgh"
FAKE_BATTERIES = [
    {"id": "dev-1", "name": "Kitchen Secret Panel", "pct": 42, "tone": "low"},
    {"id": "dev-2", "name": "Hallway Secret Panel", "pct": 91, "tone": "ok"},
]


@pytest.fixture
def app_with_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Flask:
    """Gate installed (testing=False), tmp data root, two fake devices
    reporting a battery so the topbar popover has something to leak."""
    monkeypatch.setattr(app_factory, "_collect_battery_status", lambda _app: FAKE_BATTERIES)
    app = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    app.config["TESTING"] = True
    return app


def _set_password(app: Flask) -> None:
    # A throwaway client so the session cookie doesn't carry over.
    app.test_client().post("/setup", data={"password": PASSWORD, "password_confirm": PASSWORD})


def _assert_bare_shell(body: str) -> None:
    assert 'id="primary-nav"' not in body, "top navigation rendered before sign-in"
    assert 'href="/send"' not in body
    assert 'href="/settings"' not in body
    assert "topbar-batteries" not in body, "battery popover rendered before sign-in"
    for b in FAKE_BATTERIES:
        assert b["name"] not in body, f"device name {b['name']!r} leaked before sign-in"
    assert "topbar-update" not in body
    assert "topbar-optin" not in body
    assert "Tesserae v" not in body, "version number rendered before sign-in"
    # The community links stay; they carry nothing about this install.
    assert "discord.gg" in body


def test_login_page_renders_bare_shell(app_with_gate: Flask) -> None:
    _set_password(app_with_gate)
    resp = app_with_gate.test_client().get("/login")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'name="password"' in body
    _assert_bare_shell(body)


def test_login_redirect_target_never_carries_the_shell(app_with_gate: Flask) -> None:
    """The page a stranger actually lands on: an admin URL bounced to /login."""
    _set_password(app_with_gate)
    resp = app_with_gate.test_client().get("/send", follow_redirects=True)
    assert resp.status_code == 200
    assert resp.request.path == "/login"
    _assert_bare_shell(resp.get_data(as_text=True))


def test_first_run_setup_page_renders_bare_shell(app_with_gate: Flask) -> None:
    resp = app_with_gate.test_client().get("/setup")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'name="password_confirm"' in body
    _assert_bare_shell(body)


def test_signed_in_pages_keep_the_full_shell(app_with_gate: Flask) -> None:
    """Regression guard: the lock must not bleed into authenticated pages."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": PASSWORD, "password_confirm": PASSWORD})
    body = client.get("/send").get_data(as_text=True)
    assert 'id="primary-nav"' in body
    assert "topbar-batteries" in body
    for b in FAKE_BATTERIES:
        assert b["name"] in body
    assert "Tesserae v" in body


def test_signed_in_admin_never_sees_the_login_page(app_with_gate: Flask) -> None:
    """A bookmarked /login visited with a live session bounces to the app,
    so the lock only ever applies to strangers."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": PASSWORD, "password_confirm": PASSWORD})
    resp = client.get("/login?next=/settings", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.location.endswith("/settings")


def test_password_disabled_lan_client_keeps_the_shell(app_with_gate: Flask) -> None:
    """Auth switched off in Settings: a LAN client is the operator and the
    gate lets it anywhere, so hiding the nav on /login would be theatre."""
    from app import auth

    _set_password(app_with_gate)
    auth.set_password_disabled(app_with_gate.config["SETTINGS_STORE"], True)
    body = (
        app_with_gate.test_client()
        .get("/login", environ_base={"REMOTE_ADDR": "192.168.1.20"})
        .get_data(as_text=True)
    )
    assert 'id="primary-nav"' in body


def test_theme_toggle_offers_system_light_and_dark(app) -> None:
    """The topbar theme control cycles through following the device,
    light and dark, and the shell exposes the current mode for its icon."""
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    body = client.get("/settings/server").get_data(as_text=True)
    assert "data-theme-mode" in body
    for cls in ("is-mode-system", "is-mode-light", "is-mode-dark"):
        assert cls in body
    assert "Theme: follows the device" in body
