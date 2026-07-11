"""Route-level tests for the marketplace install/uninstall endpoints.

The class-level behaviour (fetch / validate / extract / persist) is
already covered by ``tests/test_marketplace.py``; this file is the
narrower coverage for the *route layer*. Two main concerns:

1. ``install`` / ``uninstall`` set ``MARKETPLACE_RESTART_PENDING`` but
   do NOT auto-restart the process. The reason is batching: users
   often install several widgets in one sitting, and a per-install
   restart is friction. The topbar "Restart required" button (lit by
   the same context-processor flag) takes care of the explicit
   one-click restart when the user is done.
2. The ``restart`` endpoint specifically calls ``Updater.restart()``.
   That's the single auto-restart wire; everything else just queues.

Together these guarantee the topbar button lights up after any
marketplace mutation and the user keeps full control of when to
restart.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.marketplace import CatalogEntry, InstallResult, Marketplace
from app.updater import Updater


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


def _sign_in(client: object) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})  # type: ignore[attr-defined]


def _fake_entry(catalog_id: str = "sample") -> CatalogEntry:
    return CatalogEntry(
        id=catalog_id,
        name="Sample",
        description="",
        icon=None,
        author_name="t",
        author_github=None,
        tags=[],
        kind="widget",
        tesserae_compat="0.x",
        official=False,
        screenshot_sizes=[],
        extra_screenshot_count=0,
        folders=None,
        release_version="1.0.0",
        release_tarball_url="https://example.invalid/sample.tar.gz",
        release_sha256="0" * 64,
        source=None,
    )


def _inject_mocks(app: Flask) -> tuple[MagicMock, MagicMock]:
    """Replace ``MARKETPLACE`` + ``UPDATER`` on the live app with mocks.
    Returns the (marketplace, updater) pair so the test can assert on
    them. ``spec=Marketplace`` makes the isinstance check in
    ``_marketplace()`` pass."""
    mkt = MagicMock(spec=Marketplace)
    mkt.fetch_index.return_value = [_fake_entry()]
    mkt.install.return_value = InstallResult(plugin_id="sample", version="1.0.0")
    mkt.uninstall.return_value = True
    updater = MagicMock(spec=Updater)
    app.config["MARKETPLACE"] = mkt
    app.config["UPDATER"] = updater
    return mkt, updater


def test_install_marks_restart_pending_but_does_not_restart(app: Flask) -> None:
    """Successful install lights the topbar "Restart required" flag but
    leaves the process alone so the user can keep installing."""
    client = app.test_client()
    _sign_in(client)
    mkt, updater = _inject_mocks(app)
    resp = client.post(
        "/plugins/browse/install",
        data={"catalog_id": "sample"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    mkt.install.assert_called_once()
    updater.restart.assert_not_called()
    assert app.config.get("MARKETPLACE_RESTART_PENDING") is True


def test_install_reports_and_logs_telemetry(app: Flask, monkeypatch: object) -> None:
    """A successful install pings api.tesserae.ink (best-effort) and logs a
    'telemetry' event so it shows on /events, when online features are on."""
    client = app.test_client()
    _sign_in(client)
    _inject_mocks(app)
    from app import online

    calls: list[tuple[str, object, object]] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        online, "report_widget_install", lambda w, i, v: bool(calls.append((w, i, v))) or True
    )
    client.post("/plugins/browse/install", data={"catalog_id": "sample"})
    assert calls and calls[0][0] == "sample"
    rows = app.config["EVENT_LOG"].list(type="telemetry")
    assert rows and rows[0].source == "install"
    assert rows[0].target == "sample" and rows[0].status == "sent"


def test_install_no_ping_when_online_off(app: Flask, monkeypatch: object) -> None:
    """With the master switch off, install makes no api.tesserae.ink call and
    logs no telemetry event."""
    client = app.test_client()
    _sign_in(client)
    _inject_mocks(app)
    app.config["SETTINGS_STORE"].patch_section("app", {"online_features": False})
    from app import online

    called: list[object] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        online, "report_widget_install", lambda *a: bool(called.append(a)) or True
    )
    client.post("/plugins/browse/install", data={"catalog_id": "sample"})
    assert called == []
    assert app.config["EVENT_LOG"].list(type="telemetry") == []


def test_uninstall_marks_restart_pending_but_does_not_restart(app: Flask) -> None:
    """Same expectation on the uninstall side."""
    client = app.test_client()
    _sign_in(client)
    mkt, updater = _inject_mocks(app)
    resp = client.post(
        "/plugins/browse/uninstall",
        data={"catalog_id": "sample"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    mkt.uninstall.assert_called_once_with("sample", delete_data=False)
    updater.restart.assert_not_called()
    assert app.config.get("MARKETPLACE_RESTART_PENDING") is True


def test_restart_endpoint_calls_updater_restart(app: Flask) -> None:
    """The dedicated ``/plugins/browse/restart`` endpoint is the only
    wire that actually re-execs the process. The topbar button posts
    here once the user is done batching."""
    client = app.test_client()
    _sign_in(client)
    _, updater = _inject_mocks(app)
    resp = client.post("/plugins/browse/restart", follow_redirects=False)
    assert resp.status_code == 302
    updater.restart.assert_called_once()


def test_install_failure_does_not_mark_restart_pending(app: Flask) -> None:
    """A failed install must NOT light the topbar restart button,
    otherwise the user sees the prompt and restarts for nothing."""
    from app.marketplace import InstallRefused

    client = app.test_client()
    _sign_in(client)
    mkt, updater = _inject_mocks(app)
    mkt.install.side_effect = InstallRefused("bundled collision")
    resp = client.post(
        "/plugins/browse/install",
        data={"catalog_id": "sample"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    updater.restart.assert_not_called()
    assert app.config.get("MARKETPLACE_RESTART_PENDING") is not True


def test_restart_pending_flag_surfaces_in_topbar(app: Flask) -> None:
    """When the flag is set, ``_base.html`` renders the
    "Restart required" button. Verifies the context-processor wiring
    is hooked up so the button actually appears site-wide rather than
    only on the Browse page."""
    client = app.test_client()
    _sign_in(client)
    _inject_mocks(app)
    # No restart pending yet -> button absent.
    resp = client.get("/settings", follow_redirects=True)
    assert b"Restart required" not in resp.data
    # Trigger an install -> flag set -> button present on the next render.
    client.post("/plugins/browse/install", data={"catalog_id": "sample"})
    resp = client.get("/settings", follow_redirects=True)
    assert b"Restart required" in resp.data
    # Also visible on a non-settings page, proving it's truly site-wide.
    resp = client.get("/events", follow_redirects=True)
    assert b"Restart required" in resp.data
