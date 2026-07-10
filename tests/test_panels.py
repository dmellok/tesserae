"""Panels canvas editor (issue #60).

The composer places real widget renders on a freeform canvas: each element is a
widget instance rendered as one of its declared fragments. These lock the
vertical slice (flag -> routes -> catalog/fragments -> element model -> canvas
render) and the fragment contract.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.panels_schema import build_catalog, catalog_entry, fragments_of
from app.state.panel_store import CanvasPage, CanvasStore, Element


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


# -- experiment gating ---------------------------------------------------


def test_composer_reachable_by_default_but_unlinked(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    assert client.get("/experiments/composer/catalog.json").status_code == 200
    landing = client.get("/experiments/composer/", follow_redirects=False)
    assert landing.status_code == 302
    assert "/experiments/composer/c/" in landing.location


def test_composer_can_be_disabled_via_settings(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    app.config["SETTINGS_STORE"].update_section("experiments", {"composer": False})
    assert client.get("/experiments/composer/").status_code == 404
    assert client.get("/experiments/composer/catalog.json").status_code == 404


def test_index_mints_and_opens_a_canvas(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    landing = client.get("/experiments/composer/", follow_redirects=False)
    editor = client.get(landing.location)
    assert editor.status_code == 200
    assert b"panels/editor.js" in editor.data


def test_catalog_requires_auth(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()  # no sign-in
    resp = client.get("/experiments/composer/catalog.json", follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


# -- catalog + fragments -------------------------------------------------


def test_catalog_lists_widgets_with_fragments(app: Flask) -> None:
    """Every renderable widget is placeable; each carries at least a 'full'
    fragment. Library plugins (_core, kind data/font) are excluded."""
    client = app.test_client()
    _sign_in(client)
    payload = client.get("/experiments/composer/catalog.json").get_json()
    by_key = {w["key"]: w for w in payload["widgets"]}
    assert "weather_now" in by_key
    assert "weather_core" not in by_key  # library plugin, not kind widget
    frags = {f["id"] for f in by_key["weather_now"]["fragments"]}
    assert "full" in frags  # whole-widget placement always available
    assert len(by_key) > 20  # all widgets, not a filtered subset


class _FakePlugin:
    def __init__(self, pid: str, manifest: dict[str, Any]) -> None:
        self.id = pid
        self.manifest = manifest

    @property
    def name(self) -> str:
        return str(self.manifest["name"])


class _FakeRegistry:
    def __init__(self, plugins: list[_FakePlugin]) -> None:
        self._plugins = plugins

    def widgets(self) -> list[_FakePlugin]:
        return self._plugins


def test_fragments_of_declared_and_implicit_full() -> None:
    declared = _FakePlugin(
        "w",
        {
            "name": "W",
            "fragments": [
                {"id": "temp", "label": "Temperature", "w": 160, "h": 90, "icon": "ph-thermometer"},
                {"bad": 1},  # dropped
                {"id": "", "label": "empty"},  # dropped (no id)
            ],
        },
    )
    frags = fragments_of(declared)  # type: ignore[arg-type]
    ids = [f["id"] for f in frags]
    assert ids == ["full", "temp"]  # implicit full prepended, malformed dropped
    temp = next(f for f in frags if f["id"] == "temp")
    assert temp["w"] == 160 and temp["h"] == 90 and temp["icon"] == "ph-thermometer"

    plain = _FakePlugin("n", {"name": "N"})
    assert [f["id"] for f in fragments_of(plain)] == ["full"]  # implicit-only


def test_catalog_entry_shape() -> None:
    p = _FakePlugin("w", {"name": "W", "icon": "ph-x", "description": "d"})
    entry = catalog_entry(p)  # type: ignore[arg-type]
    assert entry["key"] == "w" and entry["icon"] == "ph-x"
    assert [f["id"] for f in entry["fragments"]] == ["full"]


def test_build_catalog_sorts_by_name() -> None:
    registry = _FakeRegistry(
        [_FakePlugin("z", {"name": "Zed"}), _FakePlugin("a", {"name": "Alpha"})]
    )
    catalog = build_catalog(registry)  # type: ignore[arg-type]
    assert [c["key"] for c in catalog] == ["a", "z"]


# -- element model -------------------------------------------------------


def test_element_defaults_and_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "panels.json"
    store = CanvasStore(path)
    doc = CanvasPage(
        id="c1",
        name="Kitchen",
        w=800,
        h=480,
        els=[
            Element(
                id="e1",
                widget="weather_now",
                fragment="temp",
                options={"units": "metric"},
                x=8,
                y=8,
                w=160,
                h=90,
            )
        ],
    )
    store.save(doc)
    reloaded = CanvasStore(path).get("c1")
    assert reloaded is not None
    e = reloaded.els[0]
    assert e.widget == "weather_now" and e.fragment == "temp"
    assert e.options["units"] == "metric"
    assert e.dither is True and e.visible is True  # defaults

    blank = Element(id="e2")
    assert blank.widget == "" and blank.fragment == "full"  # unassigned box


# -- routes: save / doc / send / devices / render ------------------------


def test_save_and_doc_roundtrip(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/experiments/composer/").location.rsplit("/", 1)[1]
    good = {
        "name": "My Panel",
        "w": 600,
        "h": 400,
        "els": [
            {
                "id": "e1",
                "widget": "weather_now",
                "fragment": "full",
                "x": 10,
                "y": 10,
                "w": 200,
                "h": 140,
            }
        ],
    }
    resp = client.post(f"/experiments/composer/c/{cid}/save", json=good)
    assert resp.status_code == 200 and resp.get_json()["elements"] == 1
    doc = client.get(f"/experiments/composer/c/{cid}/doc.json").get_json()
    assert doc["name"] == "My Panel" and doc["els"][0]["widget"] == "weather_now"


def test_devices_json_lists_instances_with_panel_dims(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/settings/devices/add", data={"id": "kitchen", "kind": "pimoroni_inky_4"})
    payload = client.get("/experiments/composer/devices.json").get_json()
    kitchen = next(d for d in payload["devices"] if d["id"] == "kitchen")
    assert kitchen["w"] == 600 and kitchen["h"] == 400


def test_send_renders_and_pushes(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.renderer.render_to_png", lambda request, pool=None: b"PNGBYTES")
    calls: list[dict[str, Any]] = []

    def fake_push_image(png: bytes, **kw: Any) -> object:
        calls.append({"png": png, **kw})
        return SimpleNamespace(status="sent", error=None)

    monkeypatch.setattr(app.config["PUSH_MANAGER"], "push_image", fake_push_image)
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/experiments/composer/").location.rsplit("/", 1)[1]
    resp = client.post(f"/experiments/composer/c/{cid}/send", json={"device_ids": ["dev_a"]})
    body = resp.get_json()
    assert resp.status_code == 200 and body["sent"] == ["dev_a"]
    assert calls[0]["png"] == b"PNGBYTES" and calls[0]["device_id"] == "dev_a"


def test_compose_canvas_mounts_widgets(app: Flask) -> None:
    """The render target emits a positioned .cell per widget element and loads
    composer.js to mount each as the real widget with its fragment."""
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/experiments/composer/").location.rsplit("/", 1)[1]
    client.post(
        f"/experiments/composer/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "e1",
                    "widget": "weather_now",
                    "fragment": "temp",
                    "x": 10,
                    "y": 10,
                    "w": 160,
                    "h": 90,
                }
            ]
        },
    )
    body = client.get(f"/compose/canvas/{cid}").get_data(as_text=True)
    assert 'data-plugin="weather_now"' in body
    assert 'data-fragment="temp"' in body
    assert "composer.js" in body  # real widget mount, not a primitive reconstruction


def test_compose_canvas_404_when_flag_off(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/experiments/composer/").location.rsplit("/", 1)[1]
    app.config["SETTINGS_STORE"].update_section("experiments", {"composer": False})
    assert client.get(f"/compose/canvas/{cid}").status_code == 404


def test_source_form_renders_widget_options(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-element config drawer renders a widget's cell_options via the
    shared macros."""
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/experiments/composer/source-form", json={"key": "weather_now", "sid": "e1"}
    )
    assert resp.status_code == 200
    assert 'name="opt_units"' in resp.get_data(as_text=True)


def test_widget_data_live_with_sample_fallback(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """The editor's live-data endpoint fetches real data; with no location
    configured, weather_now errors and falls back to its dev-gallery sample so
    the preview still renders."""
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/experiments/composer/data.json", json={"widget": "weather_now", "options": {}}
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert isinstance(data, dict) and data.get("temp") == 19  # sample fallback


def test_widget_data_empty_widget(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/experiments/composer/data.json", json={"widget": ""})
    assert resp.status_code == 200 and resp.get_json()["data"] is None


def test_source_options_parses_form(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/experiments/composer/source-options",
        data={"key": "weather_now", "opt_units": "imperial", "opt_label": "Home"},
    )
    assert resp.status_code == 200
    options = resp.get_json()["options"]
    assert options["units"] == "imperial" and options["label"] == "Home"
