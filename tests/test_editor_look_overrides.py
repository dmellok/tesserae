"""First-paint appearance warnings use explicit widget metadata."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from flask import Flask, template_rendered

from app.main import REPO_ROOT, create_app
from app.state.page_store import Cell, Page


@pytest.mark.parametrize("extra", ["none", "empty", "status", "widget"])
@pytest.mark.parametrize("own", [{}, {"theme": "dark"}, {"style": "mono"}, {"font": "noto_serif"}])
def test_single_widget_uses_dashboard_look_without_hiding_saved_overrides(
    app: Flask, extra: str, own: dict[str, str]
) -> None:
    cells = [
        Cell(
            id="clock",
            plugin="clock_word",
            x=0,
            y=0,
            w=400,
            h=480,
            zoom=1.25,
            theme=own.get("theme"),
            style=own.get("style"),
            font=own.get("font"),
        )
    ]
    if extra != "none":
        cells.append(
            Cell(
                id="other",
                plugin={"empty": "", "status": "status_bar", "widget": "clock_word"}[extra],
                x=400,
                y=0,
                w=400,
                h=480,
            )
        )
    app.config["PAGE_STORE"].save(
        Page(
            id="single-look",
            name="Clock",
            cells=cells,
            status_bar_enabled=extra == "status",
            status_bar_cell_id="other" if extra == "status" else None,
        )
    )
    response = app.test_client().get("/pages/single-look")
    assert response.status_code == 200
    form = response.text.split('id="ev2-cell-form-clock"', 1)[1].split("</form>", 1)[0]
    look = re.search(r"<details[^>]*data-ev2-look[^>]*>", form)
    assert look is not None
    simplified = extra != "widget" and not own
    assert ("hidden" in look.group()) == simplified
    hint = re.search(r"<p[^>]*data-ev2-dashboard-look[^>]*>.*?</p>", form, re.S)
    assert hint is not None
    assert ("hidden" in hint.group().split(">", 1)[0]) != simplified
    assert 'href="#look"' in hint.group()
    zoom = re.findall(r'<input[^>]*name="zoom"[^>]*>', form)
    assert len(zoom) == 1
    assert 'value="1.25"' in zoom[0]
    # The last details start before Zoom identifies its containing group.
    starts = re.findall(r'<details[^>]*data-ev2-adv="([^"]+)"', form[: form.index(zoom[0])])
    assert starts[-1] == ("extras" if simplified else "look")


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
        ({"theme": "Graphite"}, "uses Home Assistant's theme", "opt_theme"),
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


def test_independent_widget_keeps_cell_controls_when_dashboard_look_is_hidden(app: Flask) -> None:
    app.config["PAGE_STORE"].save(
        Page(
            id="web-look",
            name="Web",
            cells=[Cell(id="web", plugin="webpage", x=0, y=0, w=800, h=480)],
        )
    )
    response = app.test_client().get("/pages/web-look")
    assert response.status_code == 200
    look = re.search(r"<details[^>]*data-ev2-look[^>]*>", response.text)
    hint = re.search(r"<p[^>]*data-ev2-dashboard-look[^>]*>", response.text)
    assert look is not None and "hidden" not in look.group()
    assert hint is not None and "hidden" in hint.group()


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


@pytest.mark.parametrize(
    ("plugin", "manifest", "fields", "visible"),
    [
        ("", {}, {}, False),
        ("missing", {}, {}, True),
        ("test", {}, {}, True),
        ("test", {"overrides_look": "Draws its own colours"}, {}, False),
        ("test", {"overrides_look": "  "}, {}, True),
        (
            "test",
            {},
            {"theme": "dark", "style": "mono", "font": "serif", "padding_override": 0},
            True,
        ),
        (
            "test",
            {
                "cell_options": [
                    {
                        "name": "dark",
                        "type": "boolean",
                        "default": False,
                        "overrides_look": "Own dark mode",
                    }
                ]
            },
            {"options": {"dark": True}},
            True,
        ),
    ],
)
def test_look_visibility_uses_whole_widget_declarations(plugin, manifest, fields, visible):
    from app.widget_look import editor_look_context

    widgets = [SimpleNamespace(id="test", name="Test", manifest=manifest)]
    cell = Cell(id="c", plugin=plugin, x=0, y=0, w=800, h=480, **fields)
    page = Page(id="p", name="P", cells=[cell])
    assert editor_look_context(page, widgets)["look_visible"] is visible


def test_status_bar_does_not_make_look_visible():
    from app.widget_look import editor_look_context

    page = Page(
        id="p",
        name="P",
        status_bar_cell_id="bar",
        cells=[
            Cell(id="bar", plugin="tesserae_status", x=0, y=0, w=800, h=48),
        ],
    )
    assert editor_look_context(page, [])["look_visible"] is False


@pytest.mark.parametrize(
    "settings",
    [
        {"gap": 12},
        {"corner_radius": 8},
        {"custom_css": ".cell {opacity:.8}"},
        {"bleed_color": "#1C1B19"},
        {"status_bar_enabled": True},
    ],
)
def test_saved_page_effects_keep_look_editable_for_independent_widgets(settings):
    from app.widget_look import editor_look_context

    widget = SimpleNamespace(id="test", name="Test", manifest={"overrides_look": "Own colours"})
    page = Page(
        id="p", name="P", cells=[Cell(id="c", plugin="test", x=0, y=0, w=800, h=480)], **settings
    )
    assert editor_look_context(page, [widget])["look_visible"] is True


@pytest.mark.parametrize(
    ("dither", "pressed"),
    [(None, "Display setting"), ("auto", "Display setting"), ("none", "Flat")],
)
def test_dither_control_offers_display_setting_or_flat(app, dither, pressed):
    """Issue #572: the control offered "Use dashboard" with no dashboard
    setting behind it, and "Smooth", which rendered the same. A saved
    "auto" shows as the display's setting."""
    _, body, _ = _render(app, dither=dither)
    found = re.findall(r'data-ev2-dither-set="[a-z]*" aria-pressed="(true|false)">([^<]+)<', body)
    buttons = {label: state == "true" for state, label in found}
    assert buttons == {name: name == pressed for name in ("Display setting", "Flat")}
