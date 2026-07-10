"""Panels canvas editor, phase 0 (issue #60).

Covers the experiment gating, the widget-catalog endpoint, and the
data-schema helpers (declared-block catalog + fetch-result introspection).
The interactive editor lands in later phases; these lock the vertical slice
(flag -> route -> catalog -> schema) and the schema contract.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.panels_schema import build_catalog, catalog_entry, derive_schema
from app.state.panel_store import CanvasPage, CanvasStore, Element


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    """App with the real bundled plugins (the catalog reads their
    data_schema) and the auth gate installed."""
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
    """The composer experiment is on by default (no env, no settings), so an
    admin who knows the URL gets in. It has no nav entry, so it stays hidden
    otherwise, that's the soft-launch posture."""
    client = app.test_client()
    _sign_in(client)
    assert client.get("/experiments/composer/catalog.json").status_code == 200
    landing = client.get("/experiments/composer/", follow_redirects=False)
    assert landing.status_code == 302
    assert "/experiments/composer/c/" in landing.location


def test_composer_can_be_disabled_via_settings(app: Flask) -> None:
    """Setting experiments.composer false turns the routes back to 404, no
    restart needed (the guard reads the flag per request)."""
    client = app.test_client()
    _sign_in(client)
    app.config["SETTINGS_STORE"].update_section("experiments", {"composer": False})
    assert client.get("/experiments/composer/").status_code == 404
    assert client.get("/experiments/composer/catalog.json").status_code == 404


def test_index_mints_and_opens_a_canvas(app: Flask) -> None:
    """With no docs yet, the landing route creates a blank canvas and
    redirects into its editor, which serves the shell."""
    client = app.test_client()
    _sign_in(client)
    landing = client.get("/experiments/composer/", follow_redirects=False)
    editor = client.get(landing.location)
    assert editor.status_code == 200
    assert b"panels/editor.js" in editor.data


def test_catalog_lists_widgets_with_data_schema(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    payload = client.get("/experiments/composer/catalog.json").get_json()
    keys = {w["key"] for w in payload["widgets"]}
    # The seeded worked examples show up; a widget without a data_schema
    # (clock_word is client-only, no fetch) does not.
    assert {"weather_now", "device_battery"} <= keys
    assert "clock_word" not in keys
    weather = next(w for w in payload["widgets"] if w["key"] == "weather_now")
    names = {f["name"] for f in weather["fields"]}
    assert {"temp", "cond", "icon"} <= names
    assert weather["sample"]["temp"] == 21


def test_catalog_requires_auth(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even with the flag on, the endpoint is behind the admin gate."""
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()  # no sign-in
    resp = client.get("/experiments/composer/catalog.json", follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


# -- schema helpers ------------------------------------------------------


def test_derive_schema_infers_types_and_skips_error() -> None:
    result = {
        "temp": 21,
        "ratio": 0.5,
        "cond": "Sunny",
        "flag": True,  # bool -> str (renders as state text, not a number)
        "hourly": [1, 2, 3],
        "error": "boom",  # transport channel, not a field
    }
    schema = derive_schema(result)
    by_name = {f["name"]: f["type"] for f in schema["fields"]}
    assert by_name == {
        "temp": "num",
        "ratio": "num",
        "cond": "str",
        "flag": "str",
        "hourly": "arr",
    }
    assert "error" not in by_name
    assert schema["sample"]["hourly"] == [1, 2, 3]


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


def test_catalog_entry_requires_valid_fields() -> None:
    ok = _FakePlugin(
        "w",
        {
            "name": "W",
            "icon": "ph-x",
            "description": "d",
            "data_schema": {
                "color": "#123456",
                "fields": [{"name": "a", "type": "num"}, {"bad": 1}],
                "sample": {"a": 5},
            },
        },
    )
    entry = catalog_entry(ok)  # type: ignore[arg-type]
    assert entry is not None
    assert entry["key"] == "w" and entry["color"] == "#123456"
    assert [f["name"] for f in entry["fields"]] == ["a"]  # malformed field dropped

    no_schema = _FakePlugin("n", {"name": "N"})
    assert catalog_entry(no_schema) is None  # type: ignore[arg-type]

    empty = _FakePlugin("e", {"name": "E", "data_schema": {"fields": []}})
    assert catalog_entry(empty) is None  # type: ignore[arg-type]


def test_canvas_store_roundtrips(tmp_path: Path) -> None:
    """A saved canvas survives a fresh store load from disk."""
    path = tmp_path / "panels.json"
    store = CanvasStore(path)
    doc = CanvasPage(
        id="abc123",
        name="Kitchen",
        w=800,
        h=480,
        sources=["weather_now"],
        els=[Element(id="e1", type="big", x=8, y=8, w=160, h=90, binding="weather_now.temp")],
    )
    store.save(doc)
    assert len(store) == 1

    reloaded = CanvasStore(path).get("abc123")
    assert reloaded is not None
    assert reloaded.name == "Kitchen" and (reloaded.w, reloaded.h) == (800, 480)
    assert reloaded.els[0].binding == "weather_now.temp"
    assert reloaded.els[0].dither is True  # default on

    assert CanvasStore(path).delete("abc123") is True
    assert CanvasStore(path).get("abc123") is None


def test_element_type_is_known() -> None:
    assert Element(id="a", type="text").type_is_known() is True
    assert Element(id="b", type="bogus").type_is_known() is False


def test_save_route_persists_and_validates(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    # Mint a canvas via the landing route, then read its id from the doc.
    cid = client.get("/experiments/composer/").location.rsplit("/", 1)[1]

    good = {
        "name": "My Panel",
        "w": 600,
        "h": 400,
        "sources": ["weather_now"],
        "els": [{"id": "e1", "type": "chip", "x": 10, "y": 10, "w": 120, "h": 40}],
    }
    resp = client.post(f"/experiments/composer/c/{cid}/save", json=good)
    assert resp.status_code == 200 and resp.get_json()["elements"] == 1
    doc = client.get(f"/experiments/composer/c/{cid}/doc.json").get_json()
    assert doc["name"] == "My Panel" and doc["els"][0]["type"] == "chip"

    # An unknown element type is rejected, the store is not corrupted.
    bad = dict(good, els=[{"id": "x", "type": "wormhole", "x": 0, "y": 0, "w": 5, "h": 5}])
    assert client.post(f"/experiments/composer/c/{cid}/save", json=bad).status_code == 400
    still = client.get(f"/experiments/composer/c/{cid}/doc.json").get_json()
    assert still["els"][0]["type"] == "chip"  # last good doc intact


def test_save_route_404_for_unknown_canvas(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    assert client.post("/experiments/composer/c/nope/save", json={}).status_code == 404


def test_devices_json_lists_instances_with_panel_dims(app: Flask) -> None:
    """Each device carries its real panel dims, so picking a target also sets
    the canvas resolution."""
    client = app.test_client()
    _sign_in(client)
    # Register an Inky 4" instance (600x400 Spectra 6).
    client.post("/settings/devices/add", data={"id": "kitchen", "kind": "pimoroni_inky_4"})
    payload = client.get("/experiments/composer/devices.json").get_json()
    assert isinstance(payload["devices"], list)
    kitchen = next(d for d in payload["devices"] if d["id"] == "kitchen")
    assert kitchen["w"] == 600 and kitchen["h"] == 400


def test_send_renders_and_pushes_to_devices(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """Send renders the canvas once and hands the PNG to push_image per bound
    device, persisting the selection. Browser + push are mocked."""
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
    assert resp.status_code == 200
    assert body["sent"] == ["dev_a"] and body["errors"] == []
    assert calls[0]["png"] == b"PNGBYTES" and calls[0]["device_id"] == "dev_a"
    # Selection persisted on the doc.
    doc = client.get(f"/experiments/composer/c/{cid}/doc.json").get_json()
    assert doc["device_ids"] == ["dev_a"]


def test_send_400_without_a_device(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/experiments/composer/").location.rsplit("/", 1)[1]
    assert (
        client.post(f"/experiments/composer/c/{cid}/send", json={"device_ids": []}).status_code
        == 400
    )


def test_compose_canvas_renders_elements_and_data(app: Flask) -> None:
    """The render target injects the element list + a data map (widget sample
    keyed widget.field), using the shared renderer. Lives under /compose/ so
    the headless renderer reaches it."""
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/experiments/composer/").location.rsplit("/", 1)[1]
    client.post(
        f"/experiments/composer/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "e1",
                    "type": "big",
                    "x": 10,
                    "y": 10,
                    "w": 120,
                    "h": 60,
                    "binding": "weather_now.temp",
                }
            ]
        },
    )
    body = client.get(f"/compose/canvas/{cid}").get_data(as_text=True)
    assert '"weather_now.temp"' in body  # data map keyed widget.field
    assert '"e1"' in body  # the element rides through
    assert "panels/render.js" in body  # shared renderer


def test_compose_canvas_404_when_flag_off_or_missing(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/experiments/composer/").location.rsplit("/", 1)[1]
    # Flag off -> 404 even for a real id.
    app.config["SETTINGS_STORE"].update_section("experiments", {"composer": False})
    assert client.get(f"/compose/canvas/{cid}").status_code == 404
    app.config["SETTINGS_STORE"].update_section("experiments", {"composer": True})
    assert client.get("/compose/canvas/nope").status_code == 404


def test_preview_png_screenshots_at_panel_dims(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """preview.png renders the compose page at the canvas dims and returns the
    PNG. The browser call is mocked (no Chromium in CI)."""
    seen = {}

    def fake_render(request: object, pool: object = None) -> bytes:
        seen["w"] = getattr(request, "viewport_w", None)
        seen["h"] = getattr(request, "viewport_h", None)
        return b"\x89PNGfake"

    monkeypatch.setattr("app.renderer.render_to_png", fake_render)
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/experiments/composer/").location.rsplit("/", 1)[1]
    client.post(f"/experiments/composer/c/{cid}/save", json={"w": 800, "h": 480, "els": []})
    resp = client.get(f"/experiments/composer/c/{cid}/preview.png")
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert resp.data == b"\x89PNGfake"
    assert (seen["w"], seen["h"]) == (800, 480)


def test_build_catalog_sorts_and_omits_schemaless() -> None:
    registry = _FakeRegistry(
        [
            _FakePlugin(
                "z", {"name": "Zed", "data_schema": {"fields": [{"name": "a", "type": "num"}]}}
            ),
            _FakePlugin(
                "a", {"name": "Alpha", "data_schema": {"fields": [{"name": "b", "type": "str"}]}}
            ),
            _FakePlugin("n", {"name": "NoSchema"}),
        ]
    )
    catalog = build_catalog(registry)  # type: ignore[arg-type]
    assert [c["key"] for c in catalog] == ["a", "z"]  # sorted by name, schemaless omitted
