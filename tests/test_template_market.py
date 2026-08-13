"""Template share + browse/install routes (experiment-gated, online-gated)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask

from app import online
from app.state.page_store import Page
from app.state.panel_store import CanvasLayout, Element

_FAKE_PNG = b"\x89PNG\r\n\x1a\nfake"


def _enable(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_TEMPLATES", "1")
    app.config["SETTINGS_STORE"].patch_section("app", {"online_features": True})


def _save_canvas(app: Flask, page_id: str = "cv1") -> Page:
    page = Page(
        id=page_id,
        name="Shareable",
        layout_kind="canvas",
        canvas=CanvasLayout(
            w=400,
            h=300,
            els=[Element(id="t1", kind="text", text="hello", x=0, y=0, w=100, h=40)],
        ),
    )
    app.config["PAGE_STORE"].save(page)
    return page


def _doc_payload(requires: list[str] | None = None) -> dict[str, Any]:
    return {
        "slug": "shareable-abc123",
        "author": {"name": "amber-heron-42", "sponsor": False},
        "template": {
            "schema_version": 1,
            "title": "Shareable",
            "requires": requires or [],
            "inputs": [
                {
                    "name": "city",
                    "label": "City",
                    "type": "string",
                    "targets": [{"el": "w1", "slot": "options", "key": "location"}],
                }
            ],
            "canvas": {
                "w": 400,
                "h": 300,
                "els": [
                    {
                        "id": "w1",
                        "kind": "widget",
                        "widget": "clock",
                        "options": {},
                        "x": 0,
                        "y": 0,
                        "w": 200,
                        "h": 200,
                    }
                ],
            },
        },
    }


# -- gating ---------------------------------------------------------------


def test_share_routes_404_when_experiment_off(app: Flask) -> None:
    assert app.test_client().post("/panels/c/cv1/share/prepare").status_code == 404
    assert app.test_client().get("/plugins/templates/index.json").status_code == 404


def test_market_403_when_offline(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_TEMPLATES", "1")
    app.config["SETTINGS_STORE"].patch_section("app", {"online_features": False})
    assert app.test_client().get("/plugins/templates/index.json").status_code == 403


# -- share ----------------------------------------------------------------


def test_prepare_returns_dialog_payload(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    _save_canvas(app)
    monkeypatch.setattr(
        "app.template_share_routes._quality", lambda page_id, page: {"available": False}
    )
    monkeypatch.setattr(
        online,
        "fetch_template_author",
        lambda install_id: {"name": "amber-heron-42", "sponsor": False},
    )
    body = app.test_client().post("/panels/c/cv1/share/prepare").get_json()
    assert body["blocking"] == []
    assert body["template"]["title"] == "Shareable"
    assert body["online"] is True
    assert body["author"]["name"] == "amber-heron-42"


def test_submit_rebuilds_and_posts(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    _save_canvas(app)
    sent: dict[str, Any] = {}

    def fake_submit(template: Any, png_b64: str, install_id: Any, version: Any) -> dict[str, Any]:
        sent["template"] = template
        sent["png_b64"] = png_b64
        return {"status": "pending", "id": "s1", "slug": "shareable-s1", "author": {}}

    monkeypatch.setattr(online, "submit_template", fake_submit)
    monkeypatch.setattr("app.mcp_api._render_png", lambda page_id, layout, fresh=False: _FAKE_PNG)
    resp = app.test_client().post(
        "/panels/c/cv1/share/submit",
        json={"title": "My Shared Board", "description": "desc", "tags": ["clock"], "inputs": []},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "pending"
    assert sent["template"]["title"] == "My Shared Board"
    assert sent["template"]["tags"] == ["clock"]
    # The dialog can't smuggle canvas content: it comes from the stored page.
    assert sent["template"]["canvas"]["els"][0]["text"] == "hello"


def test_submit_rejects_bad_inputs(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    _save_canvas(app)
    resp = app.test_client().post(
        "/panels/c/cv1/share/submit",
        json={
            "title": "T",
            "inputs": [
                {
                    "name": "x",
                    "label": "X",
                    "type": "string",
                    "targets": [{"el": "ghost", "slot": "options", "key": "k"}],
                }
            ],
        },
    )
    assert resp.status_code == 400
    assert "unknown element" in resp.get_json()["error"]


def test_submit_surfaces_server_rejection(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    _save_canvas(app)
    monkeypatch.setattr("app.mcp_api._render_png", lambda page_id, layout, fresh=False: _FAKE_PNG)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise online.TemplateSubmitError("daily submission limit reached")

    monkeypatch.setattr(online, "submit_template", boom)
    resp = app.test_client().post("/panels/c/cv1/share/submit", json={"title": "T"})
    assert resp.status_code == 502
    assert "limit" in resp.get_json()["error"]


# -- browse / install -----------------------------------------------------


def test_index_annotates_missing_requires(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    catalog = {
        "templates": [
            {
                "slug": "s1",
                "title": "Board",
                "author": {"name": "a-b-01", "sponsor": True},
                "requires": ["not-installed-widget"],
                "preview_url": "/templates/preview/x.png",
            }
        ]
    }
    monkeypatch.setattr(online, "fetch_template_index", lambda: catalog)
    body = app.test_client().get("/plugins/templates/index.json").get_json()
    entry = body["templates"][0]
    assert entry["missing_requires"] == ["not-installed-widget"]
    assert entry["preview_url"].startswith(online.API_BASE)


def test_install_creates_unbound_page_with_inputs(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(app, monkeypatch)
    monkeypatch.setattr(online, "fetch_template_doc", lambda slug: _doc_payload())
    monkeypatch.setattr(online, "report_template_install", lambda *a, **k: True)
    resp = app.test_client().post(
        "/plugins/templates/install",
        json={"slug": "shareable-abc123", "inputs": {"city": "Melbourne"}},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    page_id = resp.get_json()["page_id"]
    page = app.config["PAGE_STORE"].get(page_id)
    assert page.layout_kind == "canvas" and page.device_ids == []
    assert page.canvas.els[0].options["location"] == "Melbourne"
    # The editor URL comes from the server and must actually resolve (a
    # hardcoded client path 404'd here once).
    page_url = resp.get_json()["page_url"]
    assert page_url.endswith(f"/c/{page_id}")
    assert app.test_client().get(page_url).status_code == 200


def test_install_409_on_missing_requires(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    monkeypatch.setattr(
        online, "fetch_template_doc", lambda slug: _doc_payload(requires=["fancy-widget"])
    )
    resp = app.test_client().post("/plugins/templates/install", json={"slug": "s"})
    assert resp.status_code == 409
    assert "fancy-widget" in resp.get_json()["error"]


def test_install_410_on_revoked(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)

    def revoked(slug: str) -> Any:
        raise online.TemplateRevokedError(slug)

    monkeypatch.setattr(online, "fetch_template_doc", revoked)
    resp = app.test_client().post("/plugins/templates/install", json={"slug": "s"})
    assert resp.status_code == 410


def test_install_rejects_malformed_canvas(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    payload = _doc_payload()
    payload["template"]["canvas"] = {"w": "not-an-int", "els": "nope"}
    monkeypatch.setattr(online, "fetch_template_doc", lambda slug: payload)
    resp = app.test_client().post("/plugins/templates/install", json={"slug": "s"})
    assert resp.status_code == 502
    assert "validation" in resp.get_json()["error"]


def test_missing_requirements_helper() -> None:
    from app.template_market import missing_requirements

    records = {"have-it": SimpleNamespace(kind="widget", folders=["have_it"])}
    registry = SimpleNamespace(get=lambda pid: object() if pid == "bundled" else None)
    template = {"requires": ["have-it", "bundled", "nope"]}
    assert missing_requirements(template, records, registry) == ["nope"]


# -- templates page (resolution > device grouping) ------------------------


def test_templates_page_renders_with_grouping_data(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(app, monkeypatch)
    resp = app.test_client().get("/plugins/templates/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "tpl-market-data" in body and "resolution_devices" in body
    assert "800x480" in body  # preset resolutions serialized for grouping
    assert "template_browse.js" in body


def test_templates_page_offline_shows_notice_not_403(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_TEMPLATES", "1")
    app.config["SETTINGS_STORE"].patch_section("app", {"online_features": False})
    resp = app.test_client().get("/plugins/templates/")
    assert resp.status_code == 200
    assert "Online features are disabled" in resp.get_data(as_text=True)
    # Data + install endpoints stay strict.
    assert app.test_client().get("/plugins/templates/index.json").status_code == 403


def test_templates_page_404_when_experiment_off(app: Flask) -> None:
    assert app.test_client().get("/plugins/templates/").status_code == 404


def test_resolution_device_labels_and_my_resolutions() -> None:
    from app.template_market import registered_device_resolutions, resolution_device_labels

    labels = resolution_device_labels()
    assert "800x480" in labels
    joined = " ".join(labels["800x480"])
    assert "Inky Impression 7.3" in joined
    assert "800x480" not in joined  # dims suffix stripped from names

    dev = SimpleNamespace(panel={"w": 800, "h": 480})
    reg = SimpleNamespace(devices={"d1": dev, "d2": dev})

    assert registered_device_resolutions(reg) == ["800x480"]
    assert registered_device_resolutions(None) == []


# -- takedown reports ----------------------------------------------------


def test_report_proxies_to_api_and_logs(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app, monkeypatch)
    sent: dict[str, Any] = {}

    def fake_report(slug: str, reason: str, install_id: Any, version: Any) -> bool:
        sent.update({"slug": slug, "reason": reason})
        return True

    monkeypatch.setattr(online, "report_template", fake_report)
    resp = app.test_client().post(
        "/plugins/templates/report",
        json={"slug": "board-abc123", "reason": "shows a private address"},
    )
    assert resp.status_code == 200 and resp.get_json()["status"] == "received"
    assert sent == {"slug": "board-abc123", "reason": "shows a private address"}


def test_report_requires_slug_and_surfaces_failure(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(app, monkeypatch)
    assert app.test_client().post("/plugins/templates/report", json={}).status_code == 400
    monkeypatch.setattr(online, "report_template", lambda *a, **k: False)
    resp = app.test_client().post("/plugins/templates/report", json={"slug": "s", "reason": "x"})
    assert resp.status_code == 502


def test_report_gated_like_the_rest(app: Flask) -> None:
    assert (
        app.test_client().post("/plugins/templates/report", json={"slug": "s"}).status_code == 404
    )


# -- install-time input editors ------------------------------------------


def _template_with_input(target: dict[str, Any], element: dict[str, Any], **input_kw: Any) -> dict:
    spec = {"name": "pick", "label": "Pick one", "type": "string", "targets": [target]}
    spec.update(input_kw)
    return {
        "schema_version": 1,
        "title": "T",
        "inputs": [spec],
        "canvas": {"w": 400, "h": 300, "els": [element]},
    }


def test_input_resolves_to_the_widgets_own_control(app: Flask) -> None:
    """The author declares 'string'; the installer gets the real control from
    their own copy of the widget's schema. ha_sensor.entities is a multiselect
    whose choices come from the installer's Home Assistant, which is precisely
    what the author cannot know."""
    from app.template_market import resolve_input_specs

    template = _template_with_input(
        {"el": "w1", "slot": "options", "key": "entities"},
        {"id": "w1", "kind": "widget", "widget": "ha_sensor", "options": {}},
    )
    with app.app_context():
        specs = resolve_input_specs(template, app.config["PLUGIN_REGISTRY"])
    assert len(specs) == 1
    spec = specs[0]
    assert spec["resolved"] is True and spec["widget"] == "ha_sensor"
    assert spec["type"] == "multiselect"  # not the author's "string"
    assert spec["label"] == "Pick one"  # the author's wording survives
    assert "choices" in spec  # materialised against this install


def test_location_input_resolves_to_location_search(app: Flask) -> None:
    from app.template_market import resolve_input_specs

    template = _template_with_input(
        {"el": "w1", "slot": "options", "key": "location"},
        {"id": "w1", "kind": "widget", "widget": "weather_now", "options": {}},
    )
    with app.app_context():
        spec = resolve_input_specs(template, app.config["PLUGIN_REGISTRY"])[0]
    assert spec["type"] == "location_search" and spec["resolved"] is True


def test_secret_inputs_never_get_a_picker(app: Flask) -> None:
    """An API key has no picker, and must stay masked whatever the schema says."""
    from app.template_market import resolve_input_specs

    template = _template_with_input(
        {"el": "c1", "slot": "source_options", "index": 0, "key": "headers"},
        {
            "id": "c1",
            "kind": "code",
            "sources": [{"key": "rest_service", "options": {}}],
        },
        secret=True,
    )
    with app.app_context():
        spec = resolve_input_specs(template, app.config["PLUGIN_REGISTRY"])[0]
    assert spec["secret"] is True and spec["type"] == "string" and spec["resolved"] is False


def test_unresolvable_targets_fall_back_to_declared_type(app: Flask) -> None:
    from app.template_market import resolve_input_specs

    # Transport slot (no option schema behind it) and a widget not installed.
    for target, element in (
        (
            {"el": "c1", "slot": "source_url", "index": 0},
            {"id": "c1", "kind": "code", "sources": [{"url": "https://x"}]},
        ),
        (
            {"el": "w1", "slot": "options", "key": "thing"},
            {"id": "w1", "kind": "widget", "widget": "not_installed", "options": {}},
        ),
    ):
        template = _template_with_input(target, element, type="textarea")
        with app.app_context():
            spec = resolve_input_specs(template, app.config["PLUGIN_REGISTRY"])[0]
        assert spec["resolved"] is False and spec["type"] == "textarea"


def test_bind_options_slot_resolves(app: Flask) -> None:
    from app.template_market import resolve_input_specs

    template = _template_with_input(
        {"el": "r1", "slot": "bind_options", "index": 0, "key": "location"},
        {
            "id": "r1",
            "kind": "rect",
            "bind": [{"source": "weather_now", "field": "temp", "transform": "length"}],
        },
    )
    with app.app_context():
        spec = resolve_input_specs(template, app.config["PLUGIN_REGISTRY"])[0]
    assert spec["type"] == "location_search" and spec["resolved"] is True


def test_coerce_input_values_uses_shared_option_coercion() -> None:
    from werkzeug.datastructures import MultiDict

    from app.template_market import coerce_input_values

    specs = [
        {"name": "entities", "type": "multiselect", "choices": []},
        {"name": "count", "type": "number", "default": 1},
        {"name": "on", "type": "boolean"},
        {"name": "note", "type": "string"},
    ]
    form = MultiDict(
        [
            ("opt_entities", "sensor.a"),
            ("opt_entities", "sensor.b"),
            ("opt_count", "7"),
            ("opt_on", "1"),
            ("opt_note", "hello"),
        ]
    )
    values = coerce_input_values(specs, form)
    assert values == {
        "entities": ["sensor.a", "sensor.b"],  # demuxed to a list
        "count": 7,  # typed as int
        "on": True,  # presence means checked
        "note": "hello",
    }


def test_inputs_form_route_renders_real_controls(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(app, monkeypatch)
    payload = _doc_payload()
    payload["template"]["inputs"] = [
        {
            "name": "entities",
            "label": "Which sensors?",
            "type": "string",
            "targets": [{"el": "w1", "slot": "options", "key": "entities"}],
        }
    ]
    payload["template"]["canvas"]["els"][0] = {
        "id": "w1",
        "kind": "widget",
        "widget": "ha_sensor",
        "options": {},
        "x": 0,
        "y": 0,
        "w": 200,
        "h": 200,
    }
    monkeypatch.setattr(online, "fetch_template_doc", lambda slug: payload)
    resp = app.test_client().get("/plugins/templates/shareable-abc123/inputs")
    assert resp.status_code == 200 and resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)
    assert "Which sensors?" in body  # the author's label survives
    # The widget's real control, not a bare text box. With no Home Assistant
    # configured here it renders the multiselect plus the same setup hint the
    # widget's own config form shows, which is the point of reusing it.
    assert 'class="multiselect"' in body
    assert "Home Assistant Core" in body
    assert 'type="text"' not in body


def test_install_accepts_the_rendered_form(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """The modal posts the rendered form; values coerce server-side through the
    same machinery widget config uses."""
    _enable(app, monkeypatch)
    monkeypatch.setattr(online, "fetch_template_doc", lambda slug: _doc_payload())
    monkeypatch.setattr(online, "report_template_install", lambda *a, **k: True)
    resp = app.test_client().post(
        "/plugins/templates/install",
        data={"slug": "shareable-abc123", "opt_city": "Melbourne"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    page = app.config["PAGE_STORE"].get(resp.get_json()["page_id"])
    assert page.canvas.els[0].options["location"] == "Melbourne"


# -- Browse doorway + Settings hint (#224) ---------------------------------


def test_browse_doorway_hidden_when_experiment_off(app: Flask) -> None:
    """No opt-in, no section. Unchanged behaviour."""
    body = app.test_client().get("/plugins/browse").get_data(as_text=True)
    assert "Community templates" not in body


def test_browse_doorway_links_to_templates_when_online(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(app, monkeypatch)
    body = app.test_client().get("/plugins/browse").get_data(as_text=True)
    assert "Community templates" in body
    assert "Browse templates" in body
    assert "Enable online features" not in body


def test_browse_doorway_explains_itself_when_offline(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The section used to vanish, which read as a bug: the widget grid above
    it comes from a static index on GitHub and keeps working without the
    online switch, so nothing on the page connected the two (#224)."""
    monkeypatch.setenv("TESSERAE_EXPERIMENT_TEMPLATES", "1")
    app.config["SETTINGS_STORE"].patch_section("app", {"online_features": False})
    body = app.test_client().get("/plugins/browse").get_data(as_text=True)
    assert "Community templates" in body
    assert "need Online features" in body
    # And it points at the switch rather than leaving the user to find it.
    assert "Enable online features" in body
    assert "#online-features" in body
    assert "Browse templates" not in body


def test_settings_flags_the_experiment_as_needing_online(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.config["SETTINGS_STORE"].patch_section("app", {"online_features": False})
    body = app.test_client().get("/settings/system").get_data(as_text=True)
    assert "needs online features" in body
    assert 'id="online-features"' in body


def test_settings_drops_the_hint_once_online_is_on(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.config["SETTINGS_STORE"].patch_section("app", {"online_features": True})
    body = app.test_client().get("/settings/system").get_data(as_text=True)
    assert "needs online features" not in body
