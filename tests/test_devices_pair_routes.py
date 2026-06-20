"""Settings -> Devices Pair card: issue + revoke routes.

The /api/v1/device/admin/pairing/* JSON endpoints are still there for
firmware testing + scripts, these tests cover the HTML form path that
the Pair card on Settings -> Devices uses.
"""

from __future__ import annotations

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


def test_devices_pair_issue_redirects_back_with_a_reveal(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/devices/pair",
        data={"note": "manual test"},
        follow_redirects=False,
    )
    # Redirects to /settings/devices#pair-device.
    assert resp.status_code == 302
    assert "/settings/devices" in resp.location

    # The redirect target's session has the reveal payload, which
    # renders into the Pair card on the next GET. Hit the GET and
    # confirm the code shows up in the page.
    page = client.get("/settings/devices", follow_redirects=True)
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    # The reveal block carries the literal note we passed.
    assert "manual test" in body
    # And a 6-digit code is highlighted in the data-pair-code element.
    import re

    match = re.search(r"data-pair-code>(\d{6})</code>", body)
    assert match is not None, "no 6-digit code rendered in pair-reveal"

    # The store should also report exactly one pending code.
    store = app.config["PAIRING_STORE"]
    pending = store.list_pending()
    assert len(pending) == 1
    assert pending[0].code == match.group(1)
    assert pending[0].note == "manual test"


def test_devices_pair_revoke_removes_pending_code(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    store = app.config["PAIRING_STORE"]
    record = store.issue(note="to revoke")
    assert len(store.list_pending()) == 1

    resp = client.post(
        f"/settings/devices/pair/{record.code}/revoke",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert store.list_pending() == []


def test_devices_pair_revoke_unknown_code_is_idempotent(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/devices/pair/000000/revoke",
        follow_redirects=False,
    )
    assert resp.status_code == 302  # redirect, no error


def test_devices_pair_routes_require_session(app: Flask) -> None:
    """Without a logged-in session the auth gate redirects to /login.
    The Pair routes live under /settings/devices/ which is session-
    gated, NOT under /api/v1/ which is bearer-token-only."""
    client = app.test_client()
    # No _sign_in() -> no session.
    resp = client.post("/settings/devices/pair", follow_redirects=False)
    assert resp.status_code in (302, 401)
    if resp.status_code == 302:
        assert "/login" in resp.location or "/setup" in resp.location
