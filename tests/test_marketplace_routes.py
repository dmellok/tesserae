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

import dataclasses
import json
from pathlib import Path
from typing import Any
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


def test_browse_shows_install_counts(app: Flask, monkeypatch: object) -> None:
    """The Browse page renders the per-widget install count fetched from
    api.tesserae.ink, keyed by catalog id, on each card."""
    client = app.test_client()
    _sign_in(client)
    app.config["SETTINGS_STORE"].patch_section("app", {"online_features": True})
    mkt = MagicMock(spec=Marketplace)
    entry = _fake_entry("spotify")
    mkt.index_url.return_value = "https://catalog.invalid/widgets.json"
    mkt.fetch_index.return_value = [entry]
    mkt.cached_index.return_value = [entry]
    mkt.installed.return_value = {}
    mkt.screenshots_base.return_value = ""
    mkt.plugins_dir.return_value = None
    app.config["MARKETPLACE"] = mkt

    from app import online

    monkeypatch.setattr(  # type: ignore[attr-defined]
        online, "widget_install_counts", lambda: {"spotify": 1234}
    )
    body = client.get("/plugins/browse").get_data(as_text=True)
    assert "1,234" in body  # the count is rendered on the card


def test_install_reports_and_logs_telemetry(
    app: Flask, monkeypatch: object, test_install_uuid: object
) -> None:
    """A successful install pings api.tesserae.ink (best-effort) and logs a
    'telemetry' event so it shows on /events, when online features are on."""
    client = app.test_client()
    _sign_in(client)
    _inject_mocks(app)
    app.config["SETTINGS_STORE"].patch_section("app", {"online_features": True})
    marked = test_install_uuid()  # type: ignore[operator]
    app.config["INSTALL_ID"] = marked
    from app import online

    calls: list[tuple[str, object, object]] = []
    monkeypatch.setattr(  # type: ignore[attr-defined]
        online, "report_widget_install", lambda w, i, v: bool(calls.append((w, i, v))) or True
    )
    client.post("/plugins/browse/install", data={"catalog_id": "sample"})
    assert calls and calls[0][0] == "sample"
    assert calls[0][1] == marked and marked.startswith("7e57c0de-")  # forwards the install id
    rows = app.config["EVENT_LOG"].list(type="telemetry")
    assert rows and rows[0].source == "install"
    assert rows[0].target == "sample" and rows[0].status == "sent"
    # The event names the widget (id + human name) so /events shows which
    # widget was installed, in the summary target and the expanded detail.
    assert rows[0].extra.get("widget") == "sample"
    assert rows[0].extra.get("name") == "Sample"
    # And it actually renders on the /events page.
    events_body = client.get("/events?type=telemetry").get_data(as_text=True)
    assert "sample" in events_body


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


# -- shadowed duplicate cleanup (Widgets page) ------------------------
#
# Scan order is bundled -> data/authored -> data/marketplace, first id wins.
# A widget pushed from Studio therefore shadows a marketplace install of the
# same id, and either can be shadowed by a bundled one. The loser sits on
# disk doing nothing but filling the Widgets page with errors.

# Ships in plugins/, so a copy under the data root always loses to it.
BUNDLED_ID = "ha_automation_history"


def _copy_widget(tmp_path: Path, root: str, plugin_id: str) -> Path:
    folder = tmp_path / root / plugin_id
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(
        json.dumps(
            {
                "tesserae_compat": "1.x",
                "name": "Shadowed copy",
                "version": "0.0.1",
                "kind": "widget",
                "supports": {"sizes": ["sm", "md", "lg"]},
            }
        ),
        encoding="utf-8",
    )
    (folder / "client.js").write_text("export default function () {}\n", encoding="utf-8")
    return folder


def _reload(app: Flask) -> None:
    app.config["PLUGIN_REGISTRY"] = app.config["REDISCOVER_PLUGINS"]()


def _errors_for(app: Flask, plugin_id: str) -> list[Any]:
    return [e for e in app.config["PLUGIN_REGISTRY"].errors if e.plugin_id == plugin_id]


def _remove(client: Any, plugin_id: str, path: Path) -> Any:
    return client.post(
        "/plugins/errors/remove",
        data={"plugin_id": plugin_id, "path": str(path)},
        follow_redirects=True,
    )


@pytest.mark.parametrize("root", ["authored", "marketplace"])
def test_a_copy_shadowed_by_the_bundled_widget_can_be_removed(
    app: Flask, tmp_path: Path, root: str
) -> None:
    folder = _copy_widget(tmp_path, root, BUNDLED_ID)
    _reload(app)
    assert _errors_for(app, BUNDLED_ID), "expected a duplicate id error"
    client = app.test_client()
    _sign_in(client)
    assert _remove(client, BUNDLED_ID, folder).status_code == 200
    assert not folder.exists()
    assert not _errors_for(app, BUNDLED_ID)
    # The bundled copy is untouched and still the one in use.
    assert app.config["PLUGIN_REGISTRY"].plugins[BUNDLED_ID].path != folder


def test_a_studio_push_shadowing_a_marketplace_install_can_be_cleaned_up(
    app: Flask, tmp_path: Path
) -> None:
    """The reported case: Studio pushes a widget to data/authored, which
    then wins over the marketplace install of the same id and leaves it
    erroring forever."""
    authored = _copy_widget(tmp_path, "authored", "studio_widget")
    installed = _copy_widget(tmp_path, "marketplace", "studio_widget")
    _reload(app)
    errors = _errors_for(app, "studio_widget")
    assert [str(e.path) for e in errors] == [str(installed)], "marketplace copy should lose"
    client = app.test_client()
    _sign_in(client)
    _remove(client, "studio_widget", installed)
    assert not installed.exists()
    assert authored.exists()
    assert app.config["PLUGIN_REGISTRY"].plugins["studio_widget"].path == authored


def test_a_path_the_loader_never_flagged_is_refused(app: Flask, tmp_path: Path) -> None:
    """The gate that stops a hand-crafted POST pointing this at any folder
    on disk."""
    victim = tmp_path / "authored" / "not_a_duplicate"
    victim.mkdir(parents=True)
    (victim / "keep.txt").write_text("x", encoding="utf-8")
    _reload(app)
    client = app.test_client()
    _sign_in(client)
    _remove(client, "not_a_duplicate", victim)
    assert victim.exists()


def test_the_bundled_copy_is_never_removable(app: Flask, tmp_path: Path) -> None:
    """Bundled plugins ship with the image; only folders under the data
    root are ours to delete."""
    folder = _copy_widget(tmp_path, "authored", BUNDLED_ID)
    _reload(app)
    bundled = app.config["PLUGIN_REGISTRY"].plugins[BUNDLED_ID].path
    client = app.test_client()
    _sign_in(client)
    _remove(client, BUNDLED_ID, bundled)
    assert bundled.exists()
    assert folder.exists()


def test_a_bundle_member_points_at_browse_instead(app: Flask, tmp_path: Path) -> None:
    """Removing one folder of a multi-folder catalog install would break
    the siblings that load fine, so it's refused."""
    from app.marketplace import InstalledRecord

    folder = _copy_widget(tmp_path, "marketplace", BUNDLED_ID)
    _reload(app)
    mkt = app.config["MARKETPLACE"]
    mkt._write_state(
        {
            "some_bundle": InstalledRecord(
                catalog_id="some_bundle",
                folders=[BUNDLED_ID, "sibling_widget"],
                version="1.0.0",
                sha256="0" * 64,
                source=None,
                installed_at="2026-01-01T00:00:00Z",
            )
        }
    )
    client = app.test_client()
    _sign_in(client)
    resp = _remove(client, BUNDLED_ID, folder)
    assert folder.exists()
    assert "bundle" in resp.get_data(as_text=True)


def test_removing_a_tracked_single_widget_drops_its_marketplace_record(
    app: Flask, tmp_path: Path
) -> None:
    """Otherwise Browse keeps claiming the entry is installed after the
    folder is gone."""
    from app.marketplace import InstalledRecord

    folder = _copy_widget(tmp_path, "marketplace", BUNDLED_ID)
    _reload(app)
    mkt = app.config["MARKETPLACE"]
    mkt._write_state(
        {
            BUNDLED_ID: InstalledRecord(
                catalog_id=BUNDLED_ID,
                folders=[BUNDLED_ID],
                version="1.0.0",
                sha256="0" * 64,
                source=None,
                installed_at="2026-01-01T00:00:00Z",
            )
        }
    )
    client = app.test_client()
    _sign_in(client)
    _remove(client, BUNDLED_ID, folder)
    assert not folder.exists()
    assert BUNDLED_ID not in mkt.installed()


def test_the_widgets_page_offers_removal_and_names_the_live_copy(
    app: Flask, tmp_path: Path
) -> None:
    folder = _copy_widget(tmp_path, "marketplace", BUNDLED_ID)
    _reload(app)
    client = app.test_client()
    _sign_in(client)
    body = client.get("/plugins/").get_data(as_text=True)
    assert "Remove this copy" in body
    assert str(folder) in body
    assert str(app.config["PLUGIN_REGISTRY"].plugins[BUNDLED_ID].path) in body


# -- image icons on the Browse card ------------------------------------


def _browse_with(app: Flask, entry: Any) -> str:
    mkt = MagicMock(spec=Marketplace)
    mkt.index_url.return_value = "https://catalog.invalid/widgets.json"
    mkt.fetch_index.return_value = [entry]
    mkt.cached_index.return_value = [entry]
    mkt.installed.return_value = {}
    mkt.screenshots_base.return_value = "https://catalog.invalid"
    mkt.plugins_dir.return_value = None
    app.config["MARKETPLACE"] = mkt
    client = app.test_client()
    _sign_in(client)
    return client.get("/plugins/browse").get_data(as_text=True)


def test_an_entry_with_its_own_mark_renders_the_image(app: Flask) -> None:
    entry = dataclasses.replace(
        _fake_entry("picture_immich"), icon="ph-images-square", icon_asset="immich.svg"
    )
    body = _browse_with(app, entry)
    assert "https://catalog.invalid/icons/immich.svg" in body


def test_without_a_mark_the_card_keeps_the_glyph(app: Flask) -> None:
    """The fallback that keeps an offline or air-gapped install looking
    the same as it always did."""
    entry = dataclasses.replace(_fake_entry("picture_immich"), icon="ph-images-square")
    body = _browse_with(app, entry)
    assert "catalog.invalid/icons/" not in body
    assert "ph-images-square" in body
