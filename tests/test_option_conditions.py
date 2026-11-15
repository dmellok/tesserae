"""``show_if`` on widget cell options (app/option_conditions.py).

The grammar is small on purpose: every key must match, a value is an exact
match, a list is any-of, ``{"not": ...}`` is none-of, and a field whose
controller is hidden is hidden too. The rendered form must keep a hidden
field's input in place, enabled, so its value is still submitted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask, render_template

from app.main import REPO_ROOT, create_app
from app.option_conditions import _matches, canonical_value, option_visible

OPTS = [
    {
        "name": "layout",
        "type": "select",
        "label": "Layout",
        "default": "list",
        "choices": [{"value": v, "label": v} for v in ("list", "cluster", "bleed")],
    },
    {
        "name": "cluster_style",
        "type": "select",
        "label": "Style",
        "default": "sheet",
        "choices": [{"value": v, "label": v} for v in ("sheet", "blend")],
        "show_if": {"layout": "cluster"},
    },
    {
        "name": "cluster_every",
        "type": "select",
        "label": "Every",
        "default": "3",
        "choices": [{"value": "3", "label": "3"}],
        "show_if": {"cluster_style": "blend"},
    },
    {"name": "url", "type": "string", "label": "URL", "default": ""},
    {"name": "token", "type": "string", "label": "Token", "show_if": {"url": {"not": ""}}},
    {"name": "show_sci", "type": "boolean", "label": "Sci", "default": True},
    {"name": "sci_style", "type": "string", "label": "Sci style", "show_if": {"show_sci": True}},
    {
        "name": "pictures",
        "type": "select",
        "label": "Pictures",
        "default": "photo",
        "choices": [{"value": v, "label": v} for v in ("photo", "gen", "plate_gen")],
    },
    {
        "name": "art_style",
        "type": "string",
        "label": "Art",
        "show_if": {"pictures": ["gen", "plate_gen"]},
    },
]


def _spec(name: str) -> dict:
    return next(o for o in OPTS if o["name"] == name)


def test_no_condition_always_shows() -> None:
    assert option_visible(_spec("layout"), {}, OPTS)


def test_equals_uses_default_when_unset() -> None:
    assert not option_visible(_spec("cluster_style"), {}, OPTS)
    assert option_visible(_spec("cluster_style"), {"layout": "cluster"}, OPTS)


def test_any_of_list() -> None:
    assert option_visible(_spec("art_style"), {"pictures": "plate_gen"}, OPTS)
    assert not option_visible(_spec("art_style"), {"pictures": "photo"}, OPTS)


def test_not_empty() -> None:
    assert not option_visible(_spec("token"), {"url": ""}, OPTS)
    assert option_visible(_spec("token"), {"url": "http://birdnet.local"}, OPTS)


def test_boolean_matches_as_text() -> None:
    assert option_visible(_spec("sci_style"), {}, OPTS)
    assert not option_visible(_spec("sci_style"), {"show_sci": False}, OPTS)


def test_hidden_controller_hides_dependent() -> None:
    """cluster_every needs blend, but blend only counts while the cluster
    layout is picked: a stale blend under the list layout hides it."""
    assert not option_visible(
        _spec("cluster_every"), {"layout": "list", "cluster_style": "blend"}, OPTS
    )
    assert option_visible(
        _spec("cluster_every"), {"layout": "cluster", "cluster_style": "blend"}, OPTS
    )


def test_unknown_controller_is_ignored() -> None:
    spec = {"name": "x", "type": "string", "label": "X", "show_if": {"nope": "1"}}
    assert option_visible(spec, {}, [*OPTS, spec])


def test_cycle_hides_instead_of_recursing() -> None:
    a = {"name": "a", "type": "string", "label": "A", "show_if": {"b": ""}}
    b = {"name": "b", "type": "string", "label": "B", "show_if": {"a": ""}}
    assert not option_visible(a, {}, [a, b])


def test_multiselect_matches_any_pick() -> None:
    spec = {"name": "x", "type": "string", "label": "X", "show_if": {"tags": "b"}}
    tags = {"name": "tags", "type": "multiselect", "label": "T", "default": []}
    assert option_visible(spec, {"tags": ["a", "b"]}, [tags, spec])
    assert not option_visible(spec, {"tags": ["a"]}, [tags, spec])


def test_shared_browser_condition_cases_match_web_visibility() -> None:
    cases = json.loads(
        (
            Path(__file__).parent
            / "companion/contract/Fixtures/dashboard-native-condition-cases.json"
        ).read_text()
    )
    for case in cases:
        controller = {"name": "mode", "type": "number", "default": case["actual"]}
        dependent = {"name": "child", "show_if": {"mode": case["expected"]}}
        assert _matches(case["actual"], case["expected"]) is case["matches"]
        assert option_visible(dependent, {}, [controller, dependent]) is case["matches"]


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    return create_app(
        testing=True,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )


def test_drawer_form_hides_but_keeps_inputs(app: Flask) -> None:
    with app.test_request_context():
        html = render_template(
            "panels_source_form.html", opts=OPTS, values={"layout": "list"}, sid="s1"
        )
    assert "data-opt-scope" in html
    # The hidden field is wrapped and hidden, and its input is still there,
    # named and not disabled, so the drawer still submits its value.
    start = html.index('name="opt_cluster_style"')
    wrap = html.rindex('<div class="opt-cond"', 0, start)
    assert " hidden>" in html[wrap:start]
    assert "disabled" not in html[wrap : start + 200]
    assert "&#34;layout&#34;" in html[wrap:start] or "&quot;layout&quot;" in html[wrap:start]


def test_drawer_form_shows_matching_field(app: Flask) -> None:
    with app.test_request_context():
        html = render_template(
            "panels_source_form.html", opts=OPTS, values={"layout": "cluster"}, sid="s1"
        )
    start = html.index('name="opt_cluster_style"')
    wrap = html.rindex('<div class="opt-cond"', 0, start)
    assert " hidden>" not in html[wrap:start]


def test_drawer_form_uses_browser_number_text_for_shared_condition_cases(app: Flask) -> None:
    cases = json.loads(
        (
            Path(__file__).parent
            / "companion/contract/Fixtures/dashboard-native-condition-cases.json"
        ).read_text()
    )
    for case in cases:
        if type(case["actual"]) not in (int, float):
            continue
        opts = [
            {"name": "mode", "type": "number", "label": "Mode", "default": 0},
            {
                "name": "child",
                "type": "string",
                "label": "Child",
                "show_if": {"mode": case["expected"]},
            },
        ]
        with app.test_request_context():
            html = render_template(
                "panels_source_form.html", opts=opts, values={"mode": case["actual"]}, sid="s1"
            )
        start = html.index('name="opt_child"')
        wrap = html.rindex('<div class="opt-cond"', 0, start)
        assert (" hidden>" not in html[wrap:start]) is case["matches"]


def _assert_drawer_numeric_conditions_in_browser(app: Flask, browser, tmp_path: Path) -> None:
    cases = json.loads(
        (
            Path(__file__).parent
            / "companion/contract/Fixtures/dashboard-native-condition-cases.json"
        ).read_text()
    )
    script = (REPO_ROOT / "static/components.js").read_text()
    page = browser.new_page()
    for index, case in enumerate(cases):
        if type(case["actual"]) not in (int, float):
            continue
        opts = [
            {"name": "mode", "type": "number", "label": "Mode", "default": 0},
            {
                "name": "child",
                "type": "string",
                "label": "Child",
                "show_if": {"mode": case["expected"]},
            },
            {
                "name": "empty_child",
                "type": "string",
                "label": "Empty child",
                "show_if": {"mode": ""},
            },
        ]
        with app.test_request_context():
            form = render_template(
                "panels_source_form.html", opts=opts, values={"mode": case["actual"]}, sid="s1"
            )
        path = tmp_path / f"numeric-conditions-{index}.html"
        path.write_text(f"<!doctype html><html><body>{form}<script>{script}</script></body></html>")
        page.goto(path.as_uri())
        child = page.locator('[name="opt_child"]').locator("xpath=ancestor::*[@data-show-if][1]")
        visible = child.evaluate("el => !el.hidden")
        assert visible is case["matches"], (
            f"{case}: browser_visible={visible}, expected={case['matches']}"
        )
        page.locator('[name="opt_mode"]').fill("")
        empty_child = page.locator('[name="opt_empty_child"]').locator(
            "xpath=ancestor::*[@data-show-if][1]"
        )
        assert empty_child.evaluate("el => !el.hidden") is True
    page.close()


def test_drawer_numeric_conditions_match_in_browser(app: Flask, tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        if not Path(p.chromium.executable_path).exists():
            pytest.skip("Playwright Chromium not installed")
        browser = p.chromium.launch()
        try:
            _assert_drawer_numeric_conditions_in_browser(app, browser, tmp_path)
        finally:
            browser.close()


def test_schema_accepts_show_if_and_rejects_bad_shapes() -> None:
    import json

    import jsonschema

    schema = json.loads((REPO_ROOT / "schema" / "plugin.schema.json").read_text(encoding="utf-8"))
    item = schema["properties"]["cell_options"]["items"]
    for good in (
        {"layout": "cluster"},
        {"pictures": ["gen", "plate_gen"]},
        {"url": {"not": ""}},
        {"show_sci": True},
    ):
        jsonschema.validate({"name": "x", "type": "string", "label": "X", "show_if": good}, item)
    for bad in ({}, {"layout": {"is": "x"}}, {"layout": [{"a": 1}]}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"name": "x", "type": "string", "label": "X", "show_if": bad}, item)


def test_textarea_presets_render_an_examples_dropdown(app: Flask) -> None:
    import json

    import jsonschema

    opts = [
        {
            "name": "scene",
            "type": "textarea",
            "label": "The scene",
            "default": "",
            "presets": [
                {"label": "Along a wire", "value": "sitting in a row along a telephone wire"},
                {"label": "At a feeder", "value": "gathered at a hanging bird feeder"},
            ],
        },
        {"name": "note", "type": "textarea", "label": "Note", "default": ""},
    ]
    with app.test_request_context():
        html = render_template("panels_source_form.html", opts=opts, values={}, sid="s1")
    start = html.index('name="opt_scene"')
    field = html[html.rindex('<div class="field"', 0, start) : start]
    assert "data-textarea-presets" in field and "data-textarea-preset-select" in field
    assert "Examples" in field and "Along a wire" in field
    assert 'value="sitting in a row along a telephone wire"' in field
    # the select carries no name: only the textarea is submitted
    assert "name=" not in field[field.index("<select") : field.index("</select>")]
    plain = html[
        html.rindex('<div class="field"', 0, html.index('name="opt_note"')) : html.index(
            'name="opt_note"'
        )
    ]
    assert "data-textarea-presets" not in plain
    schema = json.loads((REPO_ROOT / "schema" / "plugin.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(opts[0], schema["properties"]["cell_options"]["items"])


def test_alias_reads_as_its_choice() -> None:
    """A value an earlier manifest used for a choice (its ``aliases``) stands
    for that choice in ``show_if`` too, so a renamed value keeps showing the
    fields that hang off its successor."""
    layout = {
        "name": "layout",
        "type": "select",
        "default": "cluster",
        "choices": [
            {"value": "cluster", "label": "Cluster", "aliases": ["packed"]},
            {"value": "grid", "label": "Grid"},
        ],
    }
    packing = {"name": "packing", "type": "select", "show_if": {"layout": "cluster"}}
    assert option_visible(packing, {"layout": "packed"}, [layout, packing])
    assert not option_visible(packing, {"layout": "grid"}, [layout, packing])
    assert canonical_value(layout, "packed") == "cluster"
    assert canonical_value(layout, "grid") == "grid"
    assert canonical_value(layout, "gone") == "gone"
    assert canonical_value(layout, "") == ""
    assert canonical_value({"name": "n", "type": "string"}, "packed") == "packed"


# ----- option_layout: the dashboard editor's compact option grid ----------


def _layout(specs: list[dict], values: dict | None = None) -> list[tuple]:
    from app.option_conditions import option_layout

    out = []
    for b in option_layout(specs, values or {}, specs):
        if b["kind"] == "switches":
            out.append(("switches", [s["name"] for s in b["opts"]]))
        else:
            out.append((b["size"], b["opt"]["name"]))
    return out


def test_layout_gathers_switches_where_the_first_one_was() -> None:
    specs = [
        {"name": "path", "type": "string", "label": "Path", "placeholder": "lovelace/dashboard"},
        {"name": "kiosk", "type": "boolean", "label": "Kiosk"},
        {"name": "settle", "type": "number", "label": "Settle delay"},
        {"name": "interval", "type": "number", "label": "Capture interval"},
        {"name": "header", "type": "boolean", "label": "Hide header"},
        {"name": "x", "type": "number", "label": "Offset X"},
        {"name": "y", "type": "number", "label": "Offset Y"},
    ]
    assert _layout(specs) == [
        ("long", "path"),
        ("switches", ["kiosk", "header"]),
        ("short", "settle"),
        ("short", "interval"),
        ("short", "x"),
        ("short", "y"),
    ]


def test_layout_sizes() -> None:
    from app.option_conditions import option_size

    assert option_size({"type": "number", "label": "How many"}) == "short"
    assert option_size({"type": "slider", "label": "Size"}) == "long"
    assert (
        option_size({"type": "select", "label": "Units", "choices": ["Metric", "Imperial"]})
        == "short"
    )
    long_choice = {"value": "w", "label": "Current calendar week (fixed start day)"}
    assert option_size({"type": "select", "label": "Window", "choices": [long_choice]}) == "long"
    note = {
        "type": "select",
        "label": "List",
        "choices": [{"value": "", "label": "x"}],
        "choices_note": "Set up HA",
    }
    assert option_size(note) == "long"
    assert option_size({"type": "string", "label": "Place name", "placeholder": "Home"}) == "short"
    assert option_size({"type": "string", "label": "URL", "placeholder": "https://a.b"}) == "long"
    assert (
        option_size({"type": "string", "label": "Key", "placeholder": "abc", "secret": True})
        == "long"
    )
    assert option_size({"type": "number", "label": "A label far too long for half a row"}) == "long"


def test_short_fields_fit_by_width_not_just_by_count() -> None:
    """m and w are three times as wide as i and l, so a count alone let
    "Most comments" (13 characters) into a half-width select it overflows,
    and an "i" button wrap under "Avoid repeats (days)"."""
    from app.option_conditions import option_size

    fits = {"type": "select", "label": "Time shown", "choices": ["Arrival time", "Departure time"]}
    assert option_size(fits) == "short"
    wide = {"type": "select", "label": "Prioritize", "choices": ["Newest", "Most comments"]}
    assert option_size(wide) == "long"
    assert option_size({"type": "number", "label": "Avoid repeats (days)"}) == "short"
    assert option_size({"type": "number", "label": "Avoid repeats (days)", "help": "x"}) == "long"
    assert option_size({"type": "string", "label": "Name", "placeholder": "Mmmmmmmmmmm"}) == "long"
    assert (
        option_size({"type": "string", "label": "Name", "placeholder": "illustration"}) == "short"
    )


def test_a_marked_blank_row_is_a_guidance_note() -> None:
    from app.widget_choices import guidance_note

    row = {"value": "", "label": "Add an Immich library under Settings → Widgets."}
    assert guidance_note([row]) is None
    assert guidance_note([dict(row, note=True)]) == row["label"]
    assert guidance_note([dict(row, note=True), {"value": "a", "label": "A"}]) is None
    assert guidance_note([{"value": "", "label": "", "note": True}]) is None
    assert guidance_note([{"value": "", "label": "Set HA URL + token in Settings"}])


def test_layout_lifts_the_next_short_field_into_a_hole() -> None:
    specs = [
        {"name": "count", "type": "number", "label": "How many"},
        {"name": "url", "type": "textarea", "label": "Headers"},
        {"name": "refresh", "type": "number", "label": "Refresh"},
    ]
    assert _layout(specs) == [("short", "count"), ("short", "refresh"), ("long", "url")]


def test_layout_does_not_lift_over_what_a_field_depends_on() -> None:
    specs = [
        {"name": "title", "type": "string", "label": "Title", "placeholder": "Kitchen"},
        {"name": "time", "type": "boolean", "label": "Time"},
        {
            "name": "fmt",
            "type": "select",
            "label": "Time format",
            "choices": ["12 hour", "24 hour"],
            "show_if": {"time": True},
        },
    ]
    assert _layout(specs) == [("short", "title"), ("switches", ["time"]), ("short", "fmt")]


def test_layout_lifts_over_at_most_two_full_width_fields() -> None:
    long = {"type": "textarea", "label": "Notes"}
    specs = [
        {"name": "a", "type": "number", "label": "A"},
        dict(long, name="l1"),
        dict(long, name="l2"),
        dict(long, name="l3"),
        {"name": "b", "type": "number", "label": "B"},
    ]
    assert [n for _, n in _layout(specs)] == ["a", "l1", "l2", "l3", "b"]
    del specs[3]
    assert [n for _, n in _layout(specs)] == ["a", "b", "l1", "l2"]


def test_layout_ignores_hidden_fields_when_pairing() -> None:
    specs = [
        {
            "name": "mode",
            "type": "select",
            "label": "Mode",
            "choices": ["Year", "Life"],
            "default": "Year",
        },
        {"name": "born", "type": "number", "label": "Birth year", "show_if": {"mode": "Life"}},
        {"name": "url", "type": "textarea", "label": "Notes"},
        {"name": "n", "type": "number", "label": "How many"},
    ]
    # "born" is hidden, so "mode" is alone and "n" joins it.
    assert [n for _, n in _layout(specs)] == ["mode", "born", "n", "url"]


def test_switches_with_short_labels_share_a_row() -> None:
    from app.option_conditions import option_layout

    def sw(name: str, help: bool = True, **kw: object) -> dict:
        return {"name": name, "label": name, "type": "boolean", "help": "x" if help else None, **kw}

    specs = [
        sw("Leading icon"),
        sw("Time", help=False),
        sw("Temperature"),
        sw("Humidity"),
        sw("Battery"),
        sw("Wi-Fi"),
        sw("Broker"),
        sw("Firmware update"),
    ]
    (block,) = option_layout(specs, {}, specs)
    assert [[s["name"] for s in row] for row in block["rows"]] == [
        ["Leading icon"],
        ["Time", "Humidity"],
        ["Temperature"],
        ["Battery", "Wi-Fi"],
        ["Broker"],
        ["Firmware update"],
    ]


def test_a_hidden_switch_keeps_its_own_row() -> None:
    from app.option_conditions import option_layout

    specs = [
        {"name": "a", "label": "Wi-Fi", "type": "boolean"},
        {"name": "b", "label": "Broker", "type": "boolean", "show_if": {"a": False}},
        {"name": "c", "label": "Battery", "type": "boolean"},
    ]
    (block,) = option_layout(specs, {"a": True}, specs)
    assert [[s["name"] for s in row] for row in block["rows"]] == [["a", "c"], ["b"]]


def test_short_text_examples() -> None:
    from app.option_conditions import option_size

    assert (
        option_size({"type": "string", "label": "Title", "placeholder": "Evening routines"})
        == "short"
    )
    assert (
        option_size({"type": "string", "label": "Theme", "placeholder": "Google Dark Theme"})
        == "long"
    )
    assert option_size({"type": "string", "label": "Language", "placeholder": "pt-BR"}) == "short"
