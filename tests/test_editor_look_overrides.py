"""First-paint appearance warnings use explicit widget metadata."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from flask import template_rendered

from app.main import REPO_ROOT, create_app
from app.state.page_store import Cell, Page


@pytest.fixture
def app(tmp_path: Path):
    app = create_app(testing=True, data_root=tmp_path, plugins_dir=REPO_ROOT / "plugins")
    app.config["SETTINGS_STORE"].update_section(
        "app", {"panel_preset": "custom", "panel_w": 800, "panel_h": 480}
    )
    return app


def _render(app, **fields):
    app.config["PAGE_STORE"].save(
        Page(
            id="look",
            name="Look",
            cells=[Cell(id="ha", plugin="ha_dashboard", x=0, y=0, w=800, h=480, **fields)],
        )
    )
    captured = []

    def record(sender, template, context, **extra):
        captured.append(context)

    with template_rendered.connected_to(record, app):
        response = app.test_client().get("/pages/look")
    assert response.status_code == 200
    warning = response.text.split("data-ev2-ovbox", 1)[1].split("<form", 1)[0]
    return captured[-1], response.text, warning


@pytest.mark.parametrize(
    ("options", "message", "field"),
    [
        ({"dark_mode": True}, "uses its own dark mode", "opt_dark_mode"),
    ],
)
def test_ha_look_options_warn_in_server_html(app, options, message, field):
    context, body, warning = _render(app, options=options)
    row = context["look_overrides"][0]
    assert row["cell_id"] == "ha"
    assert row["reasons"] == [{"label": message, "field": field, "resettable": False}]
    assert "hidden" not in warning.split(">", 1)[0]
    assert message in warning.replace("&#39;", "'")
    assert f'data-ev2-setting="{field}"' in warning
    assert "1 widget overrides it" in body
    assert "Reset cell look overrides" not in warning


@pytest.mark.parametrize("options", [{}, {"dark_mode": False}])
def test_default_ha_look_options_do_not_warn(app, options):
    context, body, warning = _render(app, options=options)
    assert context["look_overrides"] == []
    assert "hidden" in warning.split(">", 1)[0]
    assert "widget overrides it" not in body


def test_zero_padding_is_an_explicit_resettable_override(app):
    context, _, warning = _render(app, padding_override=0)
    assert context["look_overrides"][0]["reasons"] == [
        {"label": "own padding", "field": "padding_override", "resettable": True}
    ]
    assert "Reset cell look overrides" in warning


def test_multiple_cell_and_widget_overrides_count_as_one_widget(app):
    context, body, warning = _render(
        app,
        theme="dark",
        style="editorial",
        font="noto_serif",
        padding_override=8,
        options={"dark_mode": True},
    )
    assert len(context["look_overrides"]) == 1
    row = context["look_overrides"][0]
    assert [r["field"] for r in row["reasons"]] == [
        "theme",
        "style",
        "font",
        "padding_override",
        "opt_dark_mode",
    ]
    assert row["resettable"] and row["widget_owned"]
    assert "1 widget overrides it" in body
    assert "leaves those options unchanged" in warning


def test_webpage_declares_its_independent_appearance(app):
    app.config["PAGE_STORE"].save(
        Page(id="web", name="Web", cells=[Cell(id="web", plugin="webpage", x=0, y=0, w=800, h=480)])
    )
    body = app.test_client().get("/pages/web").text
    warning = body.split("data-ev2-ovbox", 1)[1].split("<form", 1)[0]
    assert "uses the website's own appearance" in warning.replace("&#39;", "'")
    assert "hidden" not in warning.split(">", 1)[0]


def test_option_names_do_not_implicitly_mark_appearance_overrides():
    from app.widget_look import editor_look_context

    widget = SimpleNamespace(
        id="test",
        name="Test",
        manifest={"cell_options": [{"name": "dark_mode", "type": "boolean", "default": False}]},
    )
    page = Page(
        id="p",
        name="P",
        cells=[Cell(id="test", plugin="test", x=0, y=0, w=800, h=480, options={"dark_mode": True})],
    )
    assert editor_look_context(page, [widget])["look_overrides"] == []


def test_inactive_widget_look_option_does_not_warn():
    from app.widget_look import editor_look_context

    widget = SimpleNamespace(
        id="test",
        name="Test",
        manifest={
            "cell_options": [
                {"name": "mode", "type": "select", "default": "auto"},
                {
                    "name": "custom_color",
                    "type": "color",
                    "default": "#ffffff",
                    "show_if": {"mode": "custom"},
                    "overrides_look": "uses its own colour",
                },
            ]
        },
    )
    cell = Cell(
        id="test", plugin="test", x=0, y=0, w=800, h=480, options={"custom_color": "#000000"}
    )
    page = Page(id="p", name="P", cells=[cell])
    assert editor_look_context(page, [widget])["look_overrides"] == []
    cell.options["mode"] = "custom"
    assert (
        editor_look_context(page, [widget])["look_overrides"][0]["reasons"][0]["label"]
        == "uses its own colour"
    )


def test_schema_accepts_explicit_look_descriptions_and_rejects_ambiguous_flags():
    schema = json.loads((REPO_ROOT / "schema" / "plugin.schema.json").read_text())
    manifest = json.loads((REPO_ROOT / "plugins" / "ha_dashboard" / "plugin.json").read_text())
    manifest["overrides_look"] = "uses its own appearance"
    jsonschema.validate(manifest, schema)
    manifest["cell_options"][0]["overrides_look"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(manifest, schema)
