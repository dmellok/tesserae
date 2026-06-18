"""Route-level tests for the marketplace install/uninstall endpoints.

The class-level behaviour (fetch / validate / extract / persist) is
already covered by ``tests/test_marketplace.py``; this file is the
narrower coverage for the *route layer* and specifically the
auto-restart wiring added in 0.51.6: a successful POST to
``/plugins/browse/install`` or ``/plugins/browse/uninstall`` must call
``UPDATER.restart()`` so the marketplace mutation auto-applies. Without
this, the user has to hunt for the manual ``Restart now`` button after
every install, which is the whole UX win we're after.
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
    them. The Marketplace mock answers ``fetch_index`` with a single
    entry whose id matches ``sample``."""
    mkt = MagicMock(spec=Marketplace)
    mkt.fetch_index.return_value = [_fake_entry()]
    mkt.install.return_value = InstallResult(plugin_id="sample", version="1.0.0")
    mkt.uninstall.return_value = True
    updater = MagicMock(spec=Updater)
    app.config["MARKETPLACE"] = mkt
    app.config["UPDATER"] = updater
    return mkt, updater


def test_install_success_triggers_updater_restart(app: Flask) -> None:
    """POSTing a valid install id calls ``UPDATER.restart()`` so the
    new plugin is loaded without the user clicking ``Restart now``."""
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
    updater.restart.assert_called_once()
    assert app.config.get("MARKETPLACE_RESTART_PENDING") is True


def test_uninstall_success_triggers_updater_restart(app: Flask) -> None:
    """Same expectation on the uninstall side: removing a plugin
    auto-restarts so the running registry drops it."""
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
    updater.restart.assert_called_once()
    assert app.config.get("MARKETPLACE_RESTART_PENDING") is True


def test_install_without_updater_falls_back_gracefully(app: Flask) -> None:
    """When no ``UPDATER`` is configured (test harness, embedded use),
    the install still persists and the user gets the legacy
    ``Restart Tesserae to load it`` prompt rather than a 500."""
    client = app.test_client()
    _sign_in(client)
    mkt, _ = _inject_mocks(app)
    app.config["UPDATER"] = None
    resp = client.post(
        "/plugins/browse/install",
        data={"catalog_id": "sample"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    mkt.install.assert_called_once()
    # Restart-pending flag still flips so the banner appears on the next
    # render, the user just has to click Restart manually.
    assert app.config.get("MARKETPLACE_RESTART_PENDING") is True


def test_install_failure_does_not_restart(app: Flask) -> None:
    """An install that raises must NOT trigger a restart, otherwise the
    user loses their session for nothing."""
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
