"""The canvas editor's page: it follows the app's design and theme, carries the
tabbed left sheet, the grouped inspector and the phone bottom sheet, and no
longer has its own theme toggle or a phone block-out."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.main import REPO_ROOT, create_app


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


def _sign_in(client: FlaskClient) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _editor(app: Flask, client: FlaskClient) -> str:
    _sign_in(client)
    cid = client.post("/pages/canvas/new").location.rsplit("/", 1)[1]
    resp = client.get(f"/pages/canvas/c/{cid}")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_editor_is_classic_by_default(app: Flask) -> None:
    html = _editor(app, app.test_client())
    assert '<html lang="en">' in html
    assert '<html lang="en" data-ui="paper">' not in html
    # Classic keeps a back link and the brand mark, without the app rail.
    assert 'id="panels-back"' in html and 'class="bmark"' in html
    assert 'id="panels-rail"' not in html


def test_editor_follows_the_paper_design(app: Flask) -> None:
    app.config["SETTINGS_STORE"].patch_section("app", {"ui_design": "paper"})
    html = _editor(app, app.test_client())
    assert '<html lang="en" data-ui="paper">' in html
    assert "style/paper-tokens.css" in html
    # The app's icon rail, Dashboards active, linking to the real pages.
    assert 'id="panels-rail"' in html
    assert 'class="ed-rail-i is-active" href="/pages"' in html
    for href in ("/decks", "/send", "/history", "/stats/", "/settings"):
        assert f'class="ed-rail-i" href="{href}"' in html


def test_editor_follows_the_app_theme_without_its_own_toggle(app: Flask) -> None:
    html = _editor(app, app.test_client())
    assert 'id="panels-theme"' not in html
    # Same bootstrap as _base.html: saved light / dark wins, else the system.
    assert "localStorage.getItem('tesserae-theme')" in html
    assert "data-theme-mode" in html and "prefers-color-scheme: dark" in html


def test_phone_block_out_is_gone(app: Flask) -> None:
    html = _editor(app, app.test_client())
    assert "ed-mobile-block" not in html
    assert "Best on a bigger screen" not in html


def test_left_sheet_has_layers_add_and_widgets_tabs(app: Flask) -> None:
    html = _editor(app, app.test_client())
    for tab in ("layers", "add", "widgets"):
        assert f'id="panels-tab-{tab}"' in html
        assert f'id="panels-pane-{tab}"' in html
    assert 'role="tablist"' in html
    # The panes keep the ids the editor script fills.
    for mount in ("panels-layers", "panels-elements", "panels-palette", "panels-palette-search"):
        assert f'id="{mount}"' in html
    # The old Appearance card moved into the inspector's Page view.
    assert 'id="panels-appearance"' not in html


def test_inspector_sheet_and_grouped_sections(app: Flask) -> None:
    html = _editor(app, app.test_client())
    for node in (
        "panels-inspector",
        "panels-insp-title",
        "panels-insp-more",
        "panels-insp-menu",
        "panels-data",
    ):
        assert f'id="{node}"' in html
    js = (REPO_ROOT / "static" / "panels" / "editor.js").read_text(encoding="utf-8")
    for title in ('"Content"', '"Position and size"', '"Style"', '"Data"'):
        assert title in js
    # Folding state is remembered per section.
    assert "tesserae.panels.sections" in js


def test_phone_bottom_sheet_markup(app: Flask) -> None:
    html = _editor(app, app.test_client())
    assert 'id="panels-psheet"' in html and 'id="panels-phandle"' in html
    for tab in ("add", "layers", "element", "agent"):
        assert f'data-ptab="{tab}"' in html
    assert 'data-psheet="half"' in html
    # Toolbar controls keep their ids; the less-used ones sit behind More.
    for node in (
        "panels-undo",
        "panels-redo",
        "panels-sim",
        "panels-touch",
        "panels-assets",
        "panels-preview",
        "panels-settings",
        "panels-save-btn",
        "panels-devices-btn",
        "panels-send",
        "panels-more",
        "panels-canvas-menu",
    ):
        assert f'id="{node}"' in html


def test_base_template_loads_the_split_paper_tokens(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/history").get_data(as_text=True)
    assert body.index("style/paper-tokens.css") < body.index("style/paper.css")
    tokens = (REPO_ROOT / "static" / "style" / "paper-tokens.css").read_text(encoding="utf-8")
    assert ':root[data-ui="paper"] {' in tokens and "--p-selected" in tokens
    assert '@font-face { font-family: "Tesserae UI Archivo"' in " ".join(tokens.split())
