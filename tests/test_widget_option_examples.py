"""Examples for bundled widget inputs, shared by both dashboard editors."""

from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from flask import render_template_string

from app.main import REPO_ROOT, create_app
from app.state.page_store import Cell, Page

FREE_TEXT_TYPES = {"string", "text", "number", "textarea", "variables_textarea", "time"}
EXCEPTIONS = {
    ("ha_core", "settings", "token"): "User-supplied access token; never suggest a credential.",
}


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"input", "textarea"} and "name" in attrs:
            self.fields[attrs["name"]] = attrs


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, data_root=tmp_path, plugins_dir=REPO_ROOT / "plugins")


def test_bundled_free_text_options_have_an_example_or_default():
    missing = []
    used_exceptions = set()
    for path in sorted((REPO_ROOT / "plugins").glob("*/plugin.json")):
        manifest = json.loads(path.read_text())
        for group in ("cell_options", "settings"):
            for option in manifest.get(group, []):
                if option.get("type", "string") not in FREE_TEXT_TYPES:
                    continue
                key = (path.parent.name, group, option["name"])
                if key in EXCEPTIONS:
                    used_exceptions.add(key)
                    continue
                default = option.get("default")
                has_default = default is not None and str(default).strip() != ""
                if not option.get("placeholder", "").strip() and not has_default:
                    missing.append(key)
    assert not missing, f"Missing example placeholders: {missing}"
    assert used_exceptions == EXCEPTIONS.keys(), "Remove obsolete exceptions"


def test_bundled_placeholders_have_matching_english_strings():
    for path in sorted((REPO_ROOT / "plugins").glob("*/plugin.json")):
        manifest = json.loads(path.read_text())
        if not manifest.get("locales"):
            continue
        english = json.loads((path.parent / "strings/en.json").read_text())
        for group in ("cell_options", "settings"):
            for option in manifest.get(group, []):
                if "placeholder" in option:
                    key = f"{group}.{option['name']}.placeholder"
                    assert english.get(key) == option["placeholder"], (path.parent.name, key)


def test_numeric_examples_are_accepted_by_the_input_constraints():
    for path in sorted((REPO_ROOT / "plugins").glob("*/plugin.json")):
        manifest = json.loads(path.read_text())
        for group in ("cell_options", "settings"):
            for option in manifest.get(group, []):
                if option.get("type") != "number" or not option.get("placeholder"):
                    continue
                value = Decimal(option["placeholder"])
                if "min" in option:
                    assert value >= Decimal(str(option["min"]))
                if "max" in option:
                    assert value <= Decimal(str(option["max"]))
                if option.get("step") != "any":
                    base = Decimal(str(option.get("min", option.get("default") or 0)))
                    step = Decimal(str(option.get("step", 1)))
                    assert (value - base) % step == 0, (path.parent.name, option["name"])


@pytest.mark.parametrize("version", ["new", "classic"])
def test_dashboard_examples_in_both_editors(app, version):
    app.config["SETTINGS_STORE"].update_section("ui", {"dashboard_editor": version})
    app.config["PAGE_STORE"].save(
        Page(
            id="examples",
            name="Examples",
            cells=[
                Cell(id="ha", plugin="ha_dashboard", x=0, y=0, w=400, h=480),
                Cell(id="weather", plugin="weather_now", x=400, y=0, w=400, h=480),
            ],
        )
    )
    response = app.test_client().get("/pages/examples")
    assert response.status_code == 200
    fields = Inputs(response.text).fields
    for name, example in {
        "path": "dashboard-kitchen/overview",
        "label": "Home",
    }.items():
        assert fields[f"opt_{name}"]["placeholder"] == example
    assert fields["opt_label"]["value"] == ""


@pytest.mark.parametrize(
    ("spec", "value", "expected"),
    [
        ({"type": "string", "placeholder": "Google Dark Theme"}, "", "Google Dark Theme"),
        ({"type": "number", "placeholder": "24"}, "", "24"),
        (
            {"type": "number", "placeholder": "24", "presets": [{"value": 12, "label": "12"}]},
            "",
            "24",
        ),
        (
            {"type": "textarea", "placeholder": '{"Accept": "application/json"}'},
            "",
            '{"Accept": "application/json"}',
        ),
        ({"type": "variables_textarea", "placeholder": "Hello {name}"}, "", "Hello {name}"),
        ({"type": "string", "default": "Home"}, "", "Home"),
        ({"type": "string", "default": "Home"}, "Home", None),
    ],
)
def test_shared_fields_render_examples_without_changing_values(app, spec, value, expected):
    with app.test_request_context():
        html = render_template_string(
            '{% from "_components.html" import auto_field %}'
            '{{ auto_field("example", "example", spec, value) }}',
            spec={"name": "example", "label": "Example", **spec},
            value=value,
        )
    field = Inputs(html).fields["example"]
    assert field.get("placeholder") == expected
    if spec["type"] in {"string", "number"}:
        assert field["value"] == value


def test_placeholder_translations_and_english_fallback(app):
    plugin = app.config["PLUGIN_REGISTRY"].plugins["ha_dashboard"]
    manifest = deepcopy(plugin.manifest)
    plugin.strings["fr"]["cell_options.path.placeholder"] = "dashboard-cuisine/apercu"
    plugin.strings["en"]["cell_options.path.help"] = "English path help"
    app.config["SETTINGS_STORE"].update_section("app", {"locale": "fr-CA"})
    app.config["PAGE_STORE"].save(
        Page(
            id="examples",
            name="Examples",
            cells=[Cell(id="ha", plugin="ha_dashboard", x=0, y=0, w=800, h=480)],
        )
    )
    body = app.test_client().get("/pages/examples").text
    fields = Inputs(body).fields
    assert fields["opt_path"]["placeholder"] == "dashboard-cuisine/apercu"
    assert "English path help" in body
    assert plugin.manifest == manifest


def test_settings_placeholders_use_the_same_locale_lookup(app):
    plugin = app.config["PLUGIN_REGISTRY"].plugins["ha_core"]
    plugin.strings = {
        "en": {"settings.base_url.placeholder": "http://homeassistant.local:8123"},
        "fr": {"settings.base_url.placeholder": "http://maison.local:8123"},
    }
    app.config["SETTINGS_STORE"].update_section("app", {"locale": "fr"})
    response = app.test_client().get("/settings/plugins")
    assert response.status_code == 200
    fields = Inputs(response.text).fields
    assert fields["base_url"]["placeholder"] == "http://maison.local:8123"
    assert "placeholder" not in fields["token"]
