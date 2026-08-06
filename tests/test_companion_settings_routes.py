"""Settings -> Companion app page: render, issue, revoke.

The page owns the operator side of the companion API (#186): it mints pairing
codes, lists what is pending, and disconnects paired clients. These cover the
Flask wiring and the redirects; the token exchange itself lives in
tests/companion/test_companion_api.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.app_factory import create_app
from app.main import REPO_ROOT
from app.settings.companion_routes import TESTFLIGHT_URL


@pytest.fixture
def app_with_gate(tmp_path: Path) -> Flask:
    app = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    app.config["TESTING"] = True
    return app


def _setup(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_page_renders_with_the_beta_link_and_qr(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    _setup(client)
    resp = client.get("/settings/companion")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Companion app" in body
    assert "Issue pairing code" in body
    # The TestFlight link and the QR that encodes it both reach the page.
    assert TESTFLIGHT_URL in body
    assert "brand/testflight-qr.svg" in body


def test_issuing_a_code_redirects_back_and_reveals_it(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    _setup(client)
    resp = client.post("/settings/companion/pair", data={"note": "spare phone"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/settings/companion")

    pending = app_with_gate.config["COMPANION_PAIRING_STORE"].list_pending()
    assert len(pending) == 1
    code = pending[0].code

    # The reveal modal pops once, on the page the operator lands back on.
    body = client.get("/settings/companion").get_data(as_text=True)
    assert "companion-reveal-title" in body
    assert code in body
    assert "spare phone" in body

    # A reload keeps the code in the pending list but does not re-open the
    # modal, so the reveal stays a one-shot.
    reload_body = client.get("/settings/companion").get_data(as_text=True)
    assert "companion-reveal-title" not in reload_body
    assert code in reload_body


def test_revoking_a_pending_code_drops_it(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    _setup(client)
    client.post("/settings/companion/pair", data={"note": "typo"})
    code = app_with_gate.config["COMPANION_PAIRING_STORE"].list_pending()[0].code

    resp = client.post(f"/settings/companion/pair/{code}/revoke")
    assert resp.status_code == 302
    assert app_with_gate.config["COMPANION_PAIRING_STORE"].list_pending() == []


def test_revoking_an_unknown_code_is_a_no_op(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    _setup(client)
    resp = client.post("/settings/companion/pair/000000/revoke")
    assert resp.status_code == 302


def test_page_is_behind_the_auth_gate(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    resp = client.get("/settings/companion")
    assert resp.status_code in (302, 401)


def test_the_devices_page_no_longer_carries_the_card(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    _setup(client)
    body = client.get("/settings/devices").get_data(as_text=True)
    # The firmware pairing card also says "Issue pairing code", so key off the
    # companion form's action, which only the moved card posts to.
    assert 'action="/settings/companion/pair"' not in body
    # The tab bar still links to the page the card moved to.
    assert "/settings/companion" in body
