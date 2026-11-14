"""``advanced`` on widget options: the collapsed "More options" section.

Options marked ``"advanced": true`` render last, in manifest order, inside a
closed ``<details data-opt-more>`` at the end of every options form (grid
editor cell form, canvas Configure drawer, a widget's settings card).
``show_if`` still applies inside it, the section hides when none of its
options would show, and folding is presentation only: every field is still
in the form and still saved.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from flask import Flask, render_template

from app.main import REPO_ROOT, create_app
from app.option_conditions import option_sections
from app.state.page_store import PageStore

OPTS: list[dict[str, Any]] = [
    {
        "name": "layout",
        "type": "select",
        "label": "Layout",
        "default": "list",
        "choices": [{"value": v, "label": v} for v in ("list", "cluster")],
    },
    {"name": "refresh", "type": "number", "label": "Refresh", "default": 15, "advanced": True},
    {"name": "title", "type": "string", "label": "Title", "default": ""},
    {
        "name": "cluster_gap",
        "type": "number",
        "label": "Gap",
        "default": 4,
        "show_if": {"layout": "cluster"},
        "advanced": True,
    },
    {"name": "typeface", "type": "string", "label": "Typeface", "default": "", "advanced": True},
]

# Every advanced option is conditional: the section shows only for cluster.
COND_ONLY: list[dict[str, Any]] = [
    OPTS[0],
    {
        "name": "cluster_gap",
        "type": "number",
        "label": "Gap",
        "default": 4,
        "show_if": {"layout": "cluster"},
        "advanced": True,
    },
]


def _more(html: str) -> str:
    """The More options disclosure's markup, start tag to end tag."""
    start = html.index('<details class="opt-more"')
    return html[start : html.index("</details>", start)]


def _write_plugin(
    plugin_dir: Path,
    *,
    cell_options: list[dict[str, Any]],
    settings: list[dict[str, Any]] | None = None,
) -> None:
    plugin_dir.mkdir(parents=True)
    manifest: dict[str, Any] = {
        "tesserae_compat": "1.x",
        "name": plugin_dir.name.replace("_", " ").title(),
        "version": "0.0.1",
        "kind": "widget",
        "supports": {"sizes": ["sm", "md", "lg"]},
        "cell_options": cell_options,
    }
    if settings is not None:
        manifest["settings"] = settings
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_dir / "client.js").write_text("export default function () {}\n", encoding="utf-8")


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_plugin(
        plugins_dir / "widget_adv",
        cell_options=OPTS,
        settings=[
            {"name": "api_key", "type": "string", "label": "API key"},
            {"name": "region", "type": "string", "label": "Region", "advanced": True},
        ],
    )
    a = create_app(testing=False, data_root=tmp_path / "data", plugins_dir=plugins_dir)
    a.config["TESTING"] = True
    a.config["SETTINGS_STORE"].update_section("ui", {"dashboard_editor": "classic"})
    return a


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _new_page(client: Any) -> str:
    resp = client.post("/pages/new", data={"name": "Home", "layout": "1_cell"})
    assert resp.status_code in (302, 303)
    return urlsplit(resp.headers["Location"]).path.rstrip("/").rsplit("/", 1)[-1]


# -- option_sections ------------------------------------------------------


def test_sections_keep_manifest_order() -> None:
    s = option_sections(OPTS, {})
    assert [o["name"] for o in s["basic"]] == ["layout", "title"]
    assert [o["name"] for o in s["advanced"]] == ["refresh", "cluster_gap", "typeface"]
    assert s["more_visible"] is True


def test_section_hidden_when_no_advanced_option_shows() -> None:
    assert option_sections(COND_ONLY, {"layout": "list"})["more_visible"] is False
    assert option_sections(COND_ONLY, {"layout": "cluster"})["more_visible"] is True
    # Only ``true`` counts; a truthy string is not a manifest's intent.
    plain = [{"name": "a", "type": "string", "label": "A", "advanced": "yes"}]
    assert option_sections(plain, {})["advanced"] == []


def test_no_advanced_options_means_no_section(app: Flask) -> None:
    with app.test_request_context():
        html = render_template(
            "panels_source_form.html", opts=[OPTS[0], OPTS[2]], values={}, sid="s1"
        )
    assert "data-opt-more" not in html


# -- the canvas Configure drawer -----------------------------------------


def test_drawer_puts_advanced_options_last_inside_a_closed_disclosure(app: Flask) -> None:
    with app.test_request_context():
        html = render_template(
            "panels_source_form.html", opts=OPTS, values={"layout": "list"}, sid="s1"
        )
    more = _more(html)
    tag = more[: more.index(">") + 1]
    assert "data-opt-more" in tag and " open" not in tag and "hidden" not in tag
    assert "<summary>More options</summary>" in more
    # Advanced options are inside, in manifest order; basic ones are not.
    names = re.findall(r'name="opt_(\w+)"', more)
    assert names == ["refresh", "cluster_gap", "typeface"]
    before = html[: html.index('<details class="opt-more"')]
    assert re.findall(r'name="opt_(\w+)"', before) == ["layout", "title"]
    # The disclosure sits inside the show_if scope, so conditions still apply.
    assert html.index("data-opt-scope") < html.index("data-opt-more")


def test_show_if_still_applies_inside_more_options(app: Flask) -> None:
    with app.test_request_context():
        hidden = render_template(
            "panels_source_form.html", opts=OPTS, values={"layout": "list"}, sid="s1"
        )
        shown = render_template(
            "panels_source_form.html", opts=OPTS, values={"layout": "cluster"}, sid="s1"
        )
    for html, is_hidden in ((hidden, True), (shown, False)):
        more = _more(html)
        start = more.index('name="opt_cluster_gap"')
        wrap = more.rindex('<div class="opt-cond"', 0, start)
        assert (" hidden>" in more[wrap:start]) is is_hidden
        assert "disabled" not in more[wrap : start + 200]


def test_disclosure_hidden_when_none_of_its_options_show(app: Flask) -> None:
    with app.test_request_context():
        html = render_template(
            "panels_source_form.html", opts=COND_ONLY, values={"layout": "list"}, sid="s1"
        )
        shown = render_template(
            "panels_source_form.html", opts=COND_ONLY, values={"layout": "cluster"}, sid="s1"
        )
    tag = _more(html)[: _more(html).index(">") + 1]
    assert " hidden" in tag
    # Its field is still in the form, named and enabled, so it still saves.
    assert 'name="opt_cluster_gap"' in _more(html)
    assert "hidden" not in _more(shown)[: _more(shown).index(">") + 1]


# -- the grid editor cell form + save ------------------------------------


def test_grid_editor_renders_more_options_and_save_keeps_hidden_values(
    app: Flask, tmp_path: Path
) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new_page(client)
    store = PageStore(tmp_path / "data" / "core" / "pages.json")
    cell_id = store.get(pid).cells[0].id
    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_adv"})

    html = client.get(f"/pages/{pid}").get_data(as_text=True)
    more = _more(html)
    assert re.findall(r'name="opt_(\w+)"', more) == ["refresh", "cluster_gap", "typeface"]
    # layout is "list", so the conditional Gap is hidden but present.
    start = more.index('name="opt_cluster_gap"')
    assert " hidden>" in more[more.rindex('<div class="opt-cond"', 0, start) : start]

    # The form submits every field, folded or hidden: all of them save.
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={
            "plugin": "widget_adv",
            "opt_layout": "list",
            "opt_title": "Birds",
            "opt_refresh": "30",
            "opt_cluster_gap": "9",
            "opt_typeface": "Inter",
        },
    )
    opts = PageStore(tmp_path / "data" / "core" / "pages.json").get(pid).cells[0].options
    assert opts["refresh"] == 30
    assert opts["cluster_gap"] == 9
    assert opts["typeface"] == "Inter"
    assert opts["title"] == "Birds"


# -- a widget's settings card ---------------------------------------------


def test_widget_settings_fold_advanced_settings(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    html = client.get("/settings/plugins").get_data(as_text=True)
    card = html[html.index('id="plugin-widget_adv"') :]
    more = _more(card)
    assert 'name="region"' in more and 'name="api_key"' not in more
    assert card.index('name="api_key"') < card.index("data-opt-more")


def test_mcp_widget_options_report_advanced(app: Flask) -> None:
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})
    resp = app.test_client().get("/api/mcp/widgets/widget_adv/options")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    by_name = {o["name"]: o for o in resp.get_json()["options"]}
    assert by_name["refresh"]["advanced"] is True
    assert by_name["cluster_gap"]["advanced"] is True
    assert "advanced" not in by_name["layout"]


# -- schema -----------------------------------------------------------------


def test_schema_accepts_advanced_booleans_only() -> None:
    import jsonschema

    schema = json.loads((REPO_ROOT / "schema" / "plugin.schema.json").read_text(encoding="utf-8"))
    for key in ("cell_options", "settings"):
        item = schema["properties"][key]["items"]
        base = {"name": "x", "type": "string", "label": "X"}
        jsonschema.validate({**base, "advanced": True}, item)
        jsonschema.validate({**base, "advanced": False}, item)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**base, "advanced": "yes"}, item)
