"""Locales contract, render-pipeline half: composer.py resolves one
locale per page and each cell's own widget's strings, and hands both
to the browser as data-locale / data-strings (composer.js turns them
into ctx.locale / ctx.strings / ctx.t()).

/_test/render exercises the grid path (_hydrate_page, compose.html).
A canvas-kind Page exercises the freeform path (_build_canvas_els,
panels_compose.html) -- a second, separate render surface with its
own element-building code, so the locales contract needs wiring there
too, not just the grid."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.main import REPO_ROOT, create_app


def _write_i18n_widget(data_root: Path, plugin_id: str) -> None:
    """An authored widget (data_root/authored/<id>/) declaring one
    locale, so the real plugin registry resolves real strings without
    touching the bundled plugins/ dir."""
    d = data_root / "authored" / plugin_id
    (d / "strings").mkdir(parents=True)
    (d / "plugin.json").write_text(
        json.dumps(
            {
                "tesserae_compat": "1.x",
                "name": "i18n test widget",
                "version": "0.0.1",
                "kind": "widget",
                "supports": {"sizes": ["md"]},
                "locales": ["fr"],
            }
        )
    )
    (d / "client.js").write_text("export default function render(shadow, ctx) {}\n")
    (d / "strings" / "fr.json").write_text(json.dumps({"greeting": "Bonjour"}))


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    _write_i18n_widget(tmp_path, "mini_i18n")
    return create_app(testing=True, data_root=tmp_path, plugins_dir=REPO_ROOT / "plugins")


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def _cell_dataset(html: str, attr: str) -> str:
    """Pull one data-<attr>=...'s value out of the rendered cell markup.
    compose.html quotes plain-string attrs (data-locale) with " and
    tojson attrs (data-strings) with ', so this reads whichever
    quote character actually follows the attribute name."""
    marker = f"data-{attr}="
    idx = html.index(marker) + len(marker)
    quote = html[idx]
    start = idx + 1
    end = html.index(quote, start)
    return html[start:end]


def test_untranslated_widget_gets_english_locale_and_empty_strings(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No locales declared -> {} strings, so ctx.t() falls through to
    the widget's own literal text. Locks in that this whole contract
    is a no-op for the ~40 widgets that haven't opted in yet."""
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    resp = client.get("/_test/render?plugin=clock&size=md")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert _cell_dataset(body, "locale") == "en"
    assert json.loads(_cell_dataset(body, "strings")) == {}


def test_app_level_locale_setting_is_honoured(client: FlaskClient, app: Flask) -> None:
    app.config["SETTINGS_STORE"].patch_section("app", {"locale": "fr"})
    resp = client.get("/_test/render?plugin=clock&size=md")
    assert _cell_dataset(resp.get_data(as_text=True), "locale") == "fr"


def test_translated_widget_gets_its_resolved_strings(client: FlaskClient, app: Flask) -> None:
    app.config["SETTINGS_STORE"].patch_section("app", {"locale": "fr"})
    resp = client.get("/_test/render?plugin=mini_i18n&size=md")
    body = resp.get_data(as_text=True)
    assert _cell_dataset(body, "locale") == "fr"
    strings = json.loads(_cell_dataset(body, "strings"))
    assert strings == {"greeting": "Bonjour"}


def test_translated_widget_falls_back_to_english_for_unshipped_locale(
    client: FlaskClient, app: Flask
) -> None:
    """mini_i18n only ships fr; requesting de resolves to {} (it has no
    English strings/ either -- the fallback chain bottoms out cleanly
    rather than erroring)."""
    app.config["SETTINGS_STORE"].patch_section("app", {"locale": "de"})
    resp = client.get("/_test/render?plugin=mini_i18n&size=md")
    body = resp.get_data(as_text=True)
    assert _cell_dataset(body, "locale") == "de"
    strings = json.loads(_cell_dataset(body, "strings"))
    assert strings == {}


# -- canvas path (panels_compose.html / _build_canvas_els) --------------


def _canvas_page(page_id: str, plugin_id: str) -> object:
    from app.state.page_store import Page
    from app.state.panel_store import CanvasLayout, Element

    return Page(
        id=page_id,
        name="Canvas",
        layout_kind="canvas",
        canvas=CanvasLayout(w=200, h=200, els=[Element(id="e1", widget=plugin_id, w=200, h=200)]),
    )


def test_canvas_path_translated_widget_gets_its_resolved_strings(
    client: FlaskClient, app: Flask
) -> None:
    app.config["SETTINGS_STORE"].patch_section("app", {"locale": "fr"})
    app.config["PAGE_STORE"].save(_canvas_page("canvas_i18n", "mini_i18n"))
    resp = client.get("/compose/canvas_i18n")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert _cell_dataset(body, "locale") == "fr"
    assert json.loads(_cell_dataset(body, "strings")) == {"greeting": "Bonjour"}


def test_canvas_path_untranslated_widget_gets_empty_strings(
    client: FlaskClient, app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    app.config["PAGE_STORE"].save(_canvas_page("canvas_clock", "clock"))
    resp = client.get("/compose/canvas_clock")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert _cell_dataset(body, "locale") == "en"
    assert json.loads(_cell_dataset(body, "strings")) == {}


# -- calendar_day: the real pilot widget, not a synthetic fixture -------


def test_calendar_day_receives_its_real_french_strings(client: FlaskClient, app: Flask) -> None:
    """End-to-end proof against the actual bundled widget (not mini_i18n):
    calendar_day declares locales in plugins/calendar_day/plugin.json and
    ships plugins/calendar_day/strings/fr.json, so this is what a real
    contributed translation looks like flowing all the way to the DOM."""
    app.config["SETTINGS_STORE"].patch_section("app", {"locale": "fr"})
    resp = client.get("/_test/render?plugin=calendar_day&size=md")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert _cell_dataset(body, "locale") == "fr"
    strings = json.loads(_cell_dataset(body, "strings"))
    assert strings == {
        "no_events": "Aucun événement aujourd'hui.",
        "event": "événement",
        "events": "événements",
        "at": "à",
    }


# -- dev preview surfaces: the ?locale= override + the picker ----------


def test_test_render_locale_param_forces_render_language(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dev gallery's language picker rides on ``/_test/render?locale=``.
    It overrides the production resolution (no app-level locale set here,
    host locale cleared) so a reviewer can eyeball any widget in any
    language without touching Settings."""
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    plain = client.get("/_test/render?plugin=calendar_day&size=md")
    assert _cell_dataset(plain.get_data(as_text=True), "locale") == "en"

    forced = client.get("/_test/render?plugin=calendar_day&size=md&locale=fr")
    body = forced.get_data(as_text=True)
    assert _cell_dataset(body, "locale") == "fr"
    assert json.loads(_cell_dataset(body, "strings"))["event"] == "événement"


def test_widget_preview_page_honours_locale_param(client: FlaskClient) -> None:
    """The single-widget preview's iframe (``/_test/preview/page``) threads
    the picked locale through ``_parse_preview_args`` the same way."""
    resp = client.get("/_test/preview/page?widget=calendar_day&locale=fr")
    assert resp.status_code == 200
    assert _cell_dataset(resp.get_data(as_text=True), "locale") == "fr"


def test_widget_gallery_offers_the_locale_picker(client: FlaskClient) -> None:
    resp = client.get("/_test/widgets")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "data-locale-select" in body
    assert ">System default<" in body  # the leading "resolve as prod" option
    assert 'value="en"' in body
    assert 'value="fr"' in body


def test_widget_preview_controls_include_the_locale_select(client: FlaskClient) -> None:
    resp = client.get("/_test/preview?widget=calendar_day&locale=fr")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'name="locale"' in body
    assert ">System default<" in body
    # The picked tag round-trips as the selected option.
    assert 'value="fr" selected' in body
