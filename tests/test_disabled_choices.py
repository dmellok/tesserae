"""A choice that can't work for this setup: listed greyed with its reason,
never picked, kept with a warning when a cell already holds it, refreshed
when the editor renders the saved options (docs/widgets.md)."""

from __future__ import annotations

import html as _html
import re
from pathlib import Path

import pytest
from flask import Flask

from app.main import create_app
from app.state.page_store import Cell, Page, Panel
from tests.disabled_choices_fixture import PLUGIN_ID, REASON, write_plugin

PAGE_ID = "printers"


def _app(tmp_path: Path, editor: str) -> Flask:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    write_plugin(plugins)
    app = create_app(testing=False, data_root=tmp_path / "data", plugins_dir=plugins)
    app.config["TESTING"] = True
    settings = app.config["SETTINGS_STORE"]
    settings.patch_section("app", {"onboarded": True})
    settings.patch_section("auth", {"disabled": True})
    settings.update_section("ui", {"dashboard_editor": editor})
    app.config["PAGE_STORE"].save(
        Page(
            id=PAGE_ID,
            name="Printers",
            panel=Panel(w=800, h=480),
            cells=[
                Cell(
                    id="big",
                    plugin=PLUGIN_ID,
                    x=0,
                    y=0,
                    w=400,
                    h=480,
                    options={"printer": "alpha", "method": "toolpath", "size": "s"},
                ),
                Cell(
                    id="beta",
                    plugin=PLUGIN_ID,
                    x=400,
                    y=0,
                    w=400,
                    h=480,
                    options={"printer": "beta", "method": "toolpath", "size": "l"},
                ),
            ],
        )
    )
    return app


@pytest.fixture(params=["classic", "v2"])
def editor(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Flask, str]:
    return _app(tmp_path, request.param), request.param


def _field(html: str, cell: str, editor: str, name: str) -> str:
    """The markup of one option's field in one cell's form."""
    fid = f"{'cell' if editor == 'classic' else 'ev2'}-{cell}-opt-{name}"
    start = html.index(f'<label for="{fid}"')
    start = html.rfind('<div class="field', 0, start)
    return html[start : html.index("</select>", start) + 600]


def test_each_cell_gets_choices_for_its_own_printer(editor: tuple[Flask, str]) -> None:
    app, kind = editor
    html = app.test_client().get(f"/pages/{PAGE_ID}").get_data(as_text=True)
    big = _field(html, "big", kind, "method")
    beta = _field(html, "beta", kind, "method")

    # Printer A: Toolpaths greyed with the reason, the saved value kept,
    # a warning under the field.
    assert 'class="pic-opt is-disabled"' in big and 'aria-disabled="true"' in big
    assert _html.escape(REASON) in big
    assert 'class="pic-trigger is-unavailable"' in big
    assert "data-choice-warn" in big and big.count(_html.escape(REASON)) >= 2
    assert re.search(r'<option value="toolpath" selected data-choice-disabled', big), (
        "the saved disabled choice stays enabled, so a save submits it unchanged"
    )
    assert "Toolpaths (unavailable)" in big
    # The BETA: every choice open, no warning.
    assert "is-disabled" not in beta and "data-choice-warn" not in beta
    assert '<option value="toolpath" selected>Toolpaths</option>' in beta


def test_a_static_disabled_choice_is_greyed_in_the_dropdown(editor: tuple[Flask, str]) -> None:
    app, kind = editor
    html = app.test_client().get(f"/pages/{PAGE_ID}").get_data(as_text=True)
    big = _field(html, "big", kind, "size")
    assert (
        '<option value="l" disabled data-choice-disabled title="Too big for this display.">'
        "Large (Too big for this display)</option>"
    ) in big
    assert "data-choice-warn" not in big
    # A cell that holds it keeps it, with the warning, and the select points at it.
    beta = _field(html, "beta", kind, "size")
    assert '<option value="l" selected data-choice-disabled' in beta
    assert "Large (unavailable)" in beta
    assert re.search(r'aria-describedby="[^"]+-opt-size-warn"', beta)
    assert "Too big for this display.</span></p>" in beta


def test_choices_use_each_cells_saved_options(tmp_path: Path) -> None:
    from app.page_routes import _materialize_cell_choices, _materialize_cell_options

    app = _app(tmp_path, "classic")
    plugin = app.config["PLUGIN_REGISTRY"].get(PLUGIN_ID)
    page = app.config["PAGE_STORE"].get(PAGE_ID)
    with app.test_request_context("/"):
        shared = _materialize_cell_options([plugin])
        per_cell = _materialize_cell_choices(page, [plugin], shared)
    assert set(per_cell) == {"big", "beta"}
    method = {c: next(o for o in v if o["name"] == "method") for c, v in per_cell.items()}
    assert method["big"]["choices"][0]["disabled"] is True
    assert "disabled" not in method["beta"]["choices"][0]


def test_a_picture_pickers_text_choice_can_be_disabled_too(tmp_path: Path) -> None:
    app = _app(tmp_path, "classic")
    options = [
        {"value": "a", "label": "Drawn", "image": "/plugins/w/static/a.jpg"},
        {"value": "own", "label": "Your own prompt", "disabled": True},
    ]
    tmpl = (
        "{% from '_components.html' import select_field %}"
        "{{ select_field('f', 'style', 'Style', value='a', options=options) }}"
    )
    with app.test_request_context("/"):
        out = app.jinja_env.from_string(tmpl).render(options=options)
    assert 'class="pic-opt pic-opt-text is-disabled"' in out
    assert 'aria-describedby="f-why-2"' in out
    assert '<li id="f-why-2">' in out and "<b>Your own prompt.</b> Not available here." in out
    assert '<option value="own" disabled data-choice-disabled>Your own prompt (unavailable)' in out
    assert "data-choice-warn" not in out
