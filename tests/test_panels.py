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
    assert client.get("/pages/canvas/catalog.json").status_code == 200
    landing = client.get("/pages/canvas/", follow_redirects=False)
    assert landing.status_code == 302
    assert "/pages/canvas/c/" in landing.location


def test_composer_can_be_disabled_via_settings(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    app.config["SETTINGS_STORE"].update_section("experiments", {"composer": False})
    assert client.get("/pages/canvas/").status_code == 404
    assert client.get("/pages/canvas/catalog.json").status_code == 404


def test_index_mints_and_opens_a_canvas(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    landing = client.get("/pages/canvas/", follow_redirects=False)
    editor = client.get(landing.location)
    assert editor.status_code == 200
    assert b"panels/editor.js" in editor.data
    # The editor loads user + community themes so a canvas on such a theme
    # resolves its tokens (and its background) the same as the render does.
    assert b"user.css" in editor.data and b"community.css" in editor.data


def test_catalog_requires_auth(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()  # no sign-in
    resp = client.get("/pages/canvas/catalog.json", follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


# -- catalog + fragments -------------------------------------------------


def test_catalog_lists_widgets_with_fragments(app: Flask) -> None:
    """Every renderable widget is placeable; each carries at least a 'full'
    fragment. Library plugins (_core, kind data/font) are excluded."""
    client = app.test_client()
    _sign_in(client)
    payload = client.get("/pages/canvas/catalog.json").get_json()
    by_key = {w["key"]: w for w in payload["widgets"]}
    assert "weather_now" in by_key
    assert "weather_core" not in by_key  # library plugin, not kind widget
    frags = {f["id"] for f in by_key["weather_now"]["fragments"]}
    assert "full" in frags  # whole-widget placement always available
    assert len(by_key) > 20  # all widgets, not a filtered subset

    # The composite HA widgets expose their visual centrepieces as fragments.
    assert {"full", "sankey", "trend", "stats"} <= {
        f["id"] for f in by_key["ha_energy"]["fragments"]
    }
    assert {"full", "dial", "chips"} <= {f["id"] for f in by_key["ha_climate"]["fragments"]}
    assert {"full", "chart", "value"} <= {f["id"] for f in by_key["ha_history"]["fragments"]}


def test_catalog_includes_appearance(app: Flask) -> None:
    """The catalog carries the theme / style / font options the editor's
    appearance pickers consume."""
    client = app.test_client()
    _sign_in(client)
    ap = client.get("/pages/canvas/catalog.json").get_json()["appearance"]
    assert any(t["value"] == "light" for t in ap["themes"])
    assert any(s["id"] == "standard" for s in ap["styles"])
    assert isinstance(ap["fonts"], list)


def test_appearance_roundtrips(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    client.post(
        f"/pages/canvas/c/{cid}/save",
        json={"theme": "dark", "style": "editorial", "bg": "#101014", "els": []},
    )
    doc = client.get(f"/pages/canvas/c/{cid}/doc.json").get_json()
    assert doc["theme"] == "dark" and doc["style"] == "editorial" and doc["bg"] == "#101014"


def test_doc_and_save_carry_rev(app: Flask) -> None:
    """doc.json and save both report a content rev; it changes when the layout
    does. The live-sync stream + editor use it to tell edits apart."""
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    r0 = client.get(f"/pages/canvas/c/{cid}/doc.json").get_json()["rev"]
    assert isinstance(r0, str) and r0
    saved = client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [{"id": "e1", "kind": "text", "text": "hi", "x": 1, "y": 1, "w": 10, "h": 10}]
        },
    ).get_json()
    assert saved["rev"] and saved["rev"] != r0
    assert client.get(f"/pages/canvas/c/{cid}/doc.json").get_json()["rev"] == saved["rev"]


def test_element_offcanvas_and_new_kinds() -> None:
    """The Element model allows negative x/y (partly off-canvas) and carries the
    data-primitive + custom-HTML fields."""
    e = Element(
        id="a",
        kind="data",
        source="weather_now",
        field="current.temp",
        display="sparkline",
        unit="°",
        precision=1,
        label="Now",
        x=-30,
        y=-5,
        w=120,
        h=60,
    )
    assert e.x == -30 and e.y == -5 and e.display == "sparkline" and e.source == "weather_now"
    e2 = Element(
        id="d", kind="data", source="clock", field="time", format="HH:mm", x=0, y=0, w=80, h=30
    )
    assert e2.format == "HH:mm"
    h = Element(
        id="b", kind="html", html="<i>x</i>", css="i{font-weight:700}", x=0, y=0, w=50, h=50
    )
    assert h.html == "<i>x</i>" and h.css == "i{font-weight:700}"
    s = Element(id="s", kind="svg", html="<svg/>", x=0, y=0, w=40, h=40)
    assert s.kind == "svg" and s.html == "<svg/>"


def test_live_stream_contract(app: Flask) -> None:
    """The SSE stream opens with a connected preamble and 404s for a bad id."""
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    resp = client.get(f"/pages/canvas/c/{cid}/stream")
    assert resp.status_code == 200 and resp.mimetype == "text/event-stream"
    assert b"connected" in next(resp.iter_encoded())
    resp.close()
    assert client.get("/pages/canvas/c/does-not-exist/stream").status_code == 404


def test_compose_applies_theme_and_background(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    client.post(
        f"/pages/canvas/c/{cid}/save",
        json={"theme": "dark", "style": "editorial", "bg": "#101014", "els": []},
    )
    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    assert 'data-theme="dark"' in body and 'data-style="editorial"' in body
    assert "#101014" in body  # background colour applied to the board


class _FakePlugin:
    def __init__(self, pid: str, manifest: dict[str, Any]) -> None:
        self.id = pid
        self.manifest = manifest

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    @property
    def on_schedule_updates(self) -> list[dict[str, str]]:
        updates = self.manifest.get("updates")
        raw = updates.get("on_schedule") if isinstance(updates, dict) else None
        if not isinstance(raw, list):
            return []
        return [
            dict(spec) for spec in raw if isinstance(spec, dict) and spec.get("kind") == "daily"
        ]


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
    p = _FakePlugin(
        "w",
        {
            "name": "W",
            "icon": "ph-x",
            "description": "d",
            "updates": {
                "on_change": [{"source": "personal_data.reminders"}],
                "on_schedule": [
                    {"kind": "daily", "suggested_at": "07:00"},
                    {"kind": "weekly"},
                ],
            },
        },
    )
    entry = catalog_entry(p)  # type: ignore[arg-type]
    assert entry["key"] == "w" and entry["icon"] == "ph-x"
    assert [f["id"] for f in entry["fragments"]] == ["full"]
    assert entry["updates_on_change"] is True
    assert entry["updates_on_schedule"] == [{"kind": "daily", "suggested_at": "07:00"}]

    plain = catalog_entry(_FakePlugin("plain", {"name": "Plain"}))  # type: ignore[arg-type]
    assert plain["updates_on_change"] is False
    assert plain["updates_on_schedule"] == []


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
                update_on_change=True,
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
    assert e.update_on_change is True
    assert e.dither is True and e.visible is True  # defaults

    blank = Element(id="e2")
    assert blank.widget == "" and blank.fragment == "full"  # unassigned box
    assert blank.update_on_change is False


def test_decoration_element_roundtrip(tmp_path: Path) -> None:
    """A decoration element (shape / line / icon) persists its props."""
    path = tmp_path / "panels.json"
    store = CanvasStore(path)
    store.save(
        CanvasPage(
            id="c1",
            els=[
                Element(
                    id="d1",
                    kind="rect",
                    color="var(--accent-1)",
                    fill=False,
                    stroke=3,
                    radius=12,
                    x=0,
                    y=0,
                    w=100,
                    h=60,
                )
            ],
        )
    )
    e = CanvasStore(path).get("c1").els[0]  # type: ignore[union-attr]
    assert e.kind == "rect" and e.color == "var(--accent-1)"
    assert e.fill is False and e.stroke == 3 and e.radius == 12


def test_migrate_canvases_to_pages(tmp_path: Path) -> None:
    """Legacy canvas docs migrate into the PageStore as canvas pages, without
    clobbering existing page ids, and idempotently."""
    from app.state.page_store import Page, PageStore, migrate_canvases_to_pages

    cstore = CanvasStore(tmp_path / "panels.json")
    cstore.save(
        CanvasPage(
            id="k1",
            name="Kitchen",
            w=800,
            h=480,
            theme="dark",
            device_ids=["dev_a"],
            els=[Element(id="e", widget="clock", x=0, y=0, w=100, h=100)],
        )
    )
    cstore.save(CanvasPage(id="collide", name="Canvas C2"))
    pstore = PageStore(tmp_path / "pages.json")
    pstore.save(Page(id="collide", name="Keep me", layout_kind="grid"))  # pre-existing page id

    assert migrate_canvases_to_pages(cstore, pstore) == 1  # only k1; collide skipped
    k = pstore.get("k1")
    assert k is not None and k.layout_kind == "canvas" and k.device_ids == ["dev_a"]
    assert k.canvas is not None and k.canvas.w == 800 and k.canvas.theme == "dark"
    assert k.canvas.els[0].widget == "clock"
    kept = pstore.get("collide")
    assert kept is not None and kept.name == "Keep me" and kept.layout_kind == "grid"
    assert migrate_canvases_to_pages(cstore, pstore) == 0  # idempotent


def test_deleted_canvas_page_does_not_resurrect_on_restart(tmp_path: Path) -> None:
    """Deleting a canvas-born page drops the legacy canvas doc too, so the
    every-startup migration can't re-create it on the next boot (the phantom
    dashboard that came back after every update)."""
    (tmp_path / "core").mkdir(parents=True)
    seed = CanvasStore(tmp_path / "core" / "panels.json")
    seed.save(
        CanvasPage(
            id="phantom",
            name="Old canvas",
            els=[Element(id="e", widget="clock", x=0, y=0, w=100, h=100)],
        )
    )

    def boot() -> Flask:
        a = create_app(
            testing=False,
            data_root=tmp_path,
            plugins_dir=REPO_ROOT / "plugins",
            renderers_dir=REPO_ROOT / "renderers",
        )
        a.config["TESTING"] = True
        return a

    first = boot()
    assert first.config["PAGE_STORE"].get("phantom") is not None  # migrated in
    assert first.config["PAGE_STORE"].delete("phantom") is True
    # The delete listener must have dropped the legacy doc with the page.
    assert first.config["PANEL_STORE"].get("phantom") is None

    second = boot()  # the restart every update performs
    assert second.config["PAGE_STORE"].get("phantom") is None


def test_compose_renders_decorations(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "d1",
                    "kind": "line",
                    "color": "var(--accent-2)",
                    "stroke": 4,
                    "x": 0,
                    "y": 0,
                    "w": 200,
                    "h": 10,
                }
            ]
        },
    )
    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    assert 'class="deco"' in body and "panels/decorate.js" in body


def test_fragment_part_scales_roundtrip_and_compose(app: Flask) -> None:
    """Per-part scale overrides persist on a widget element and reach the
    compose render as the cell's data-parts payload."""
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "e1",
                    "widget": "weather_now",
                    "fragment": "temp",
                    "parts": [{"sel": ".wx-temp", "scale": 150}],
                    "x": 0,
                    "y": 0,
                    "w": 200,
                    "h": 140,
                }
            ]
        },
    )
    doc = client.get(f"/pages/canvas/c/{cid}/doc.json").get_json()
    parts = doc["els"][0]["parts"]
    assert parts == [{"sel": ".wx-temp", "scale": 150}]

    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    assert "data-parts=" in body and ".wx-temp" in body


def test_crop_insets_roundtrip_and_compose(app: Flask) -> None:
    """A widget element's crop persists and reaches the compose render as the
    reduced footprint cell + full-size offset cell-content (drop a title /
    reclaim space, undistorted)."""
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "e1",
                    "widget": "weather_now",
                    "crop": {"top": 22, "bottom": 5},
                    "x": 0,
                    "y": 0,
                    "w": 200,
                    "h": 140,
                }
            ]
        },
    )
    doc = client.get(f"/pages/canvas/c/{cid}/doc.json").get_json()
    crop = doc["els"][0]["crop"]
    assert crop["top"] == 22 and crop["bottom"] == 5 and crop["left"] == 0

    # Crop is layout: the cell shrinks to the kept footprint and the widget
    # still renders at full size in an offset, clipped cell-content.
    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    assert "top: 30.8px" in body  # 22% of 140
    assert "height: 102.2px" in body  # (100-22-5)% of 140
    assert 'class="cell-content" style="position:absolute;' in body


def test_crop_omitted_from_compose_when_zero(app: Flask) -> None:
    """A widget with no crop renders as a plain full-size cell: no footprint
    clipping and no positioned cell-content, so the common case stays clean."""
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    client.post(
        f"/pages/canvas/c/{cid}/save",
        json={"els": [{"id": "e1", "widget": "weather_now", "x": 0, "y": 0, "w": 200, "h": 140}]},
    )
    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    assert 'data-el-id="e1"' in body
    assert '<div class="cell-content"></div>' in body  # plain, not offset/clipped


def test_crop_edge_capped_at_90(app: Flask) -> None:
    from pydantic import ValidationError

    from app.state.panel_store import CropInsets

    with pytest.raises(ValidationError):
        CropInsets(top=95)


def test_background_image(app: Flask) -> None:
    """A canvas background image + fit mode persists and reaches the render."""
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    client.post(
        f"/pages/canvas/c/{cid}/save",
        json={"bg_image": "https://example.com/bg.jpg", "bg_fit": "contain", "els": []},
    )
    doc = client.get(f"/pages/canvas/c/{cid}/doc.json").get_json()
    assert doc["bg_image"] == "https://example.com/bg.jpg" and doc["bg_fit"] == "contain"
    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    assert "https://example.com/bg.jpg" in body and "object-fit:contain" in body


def test_text_element_and_opacity(app: Flask) -> None:
    """Text elements and per-element opacity persist and reach the render."""
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "t1",
                    "kind": "text",
                    "text": "Hello",
                    "align": "center",
                    "size": 24,
                    "opacity": 50,
                    "x": 0,
                    "y": 0,
                    "w": 120,
                    "h": 40,
                }
            ]
        },
    )
    e = client.get(f"/pages/canvas/c/{cid}/doc.json").get_json()["els"][0]
    assert e["kind"] == "text" and e["text"] == "Hello" and e["align"] == "center"
    assert e["size"] == 24 and e["opacity"] == 50
    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    assert 'class="deco"' in body and "opacity: 0.5" in body


def test_rotation_and_icon_weight(app: Flask) -> None:
    """Rotation and icon weight persist and reach the compose render."""
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "w1",
                    "widget": "weather_now",
                    "rotate": 45,
                    "x": 0,
                    "y": 0,
                    "w": 120,
                    "h": 90,
                },
                {
                    "id": "i1",
                    "kind": "icon",
                    "icon": "heart",
                    "weight": "fill",
                    "rotate": 15,
                    "x": 0,
                    "y": 0,
                    "w": 60,
                    "h": 60,
                },
            ]
        },
    )
    doc = client.get(f"/pages/canvas/c/{cid}/doc.json").get_json()
    els = {e["id"]: e for e in doc["els"]}
    assert els["w1"]["rotate"] == 45
    assert els["i1"]["weight"] == "fill" and els["i1"]["rotate"] == 15
    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    assert "rotate(45deg)" in body  # widget rotation applied on the compose cell


# -- routes: save / doc / send / devices / render ------------------------


def test_save_and_doc_roundtrip(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
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
    resp = client.post(f"/pages/canvas/c/{cid}/save", json=good)
    assert resp.status_code == 200 and resp.get_json()["elements"] == 1
    doc = client.get(f"/pages/canvas/c/{cid}/doc.json").get_json()
    assert doc["name"] == "My Panel" and doc["els"][0]["widget"] == "weather_now"


def test_save_coerces_update_policies_to_manifest_capabilities(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The save endpoint, not only the editor UI, owns placement policy.

    A capable widget keeps its opt-in. An unsupported widget and a decoration
    cannot persist meaningless true bits, including after a widget swap that
    sends the previous placement value back to the server.
    """
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    weather = app.config["PLUGIN_REGISTRY"].get("weather_now")
    assert weather is not None
    monkeypatch.setitem(
        weather.manifest,
        "updates",
        {
            "on_change": [{"source": "test.weather"}],
            "on_schedule": [{"kind": "daily", "suggested_at": "07:00"}],
        },
    )
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    resp = client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "capable",
                    "widget": "weather_now",
                    "update_on_change": True,
                    "update_schedule": {"kind": "daily"},
                },
                {
                    "id": "unsupported",
                    "widget": "clock_analog",
                    "update_on_change": True,
                    "update_schedule": {"kind": "daily", "at": "08:30"},
                },
                {
                    "id": "decoration",
                    "kind": "line",
                    "update_on_change": True,
                    "update_schedule": {"kind": "daily"},
                },
            ]
        },
    )
    assert resp.status_code == 200
    doc = client.get(f"/pages/canvas/c/{cid}/doc.json").get_json()
    by_id = {element["id"]: element for element in doc["els"]}
    assert by_id["capable"]["update_on_change"] is True
    assert by_id["capable"]["update_schedule"] == {"kind": "daily", "at": None}
    assert by_id["unsupported"]["update_on_change"] is False
    assert by_id["unsupported"]["update_schedule"] is None
    assert by_id["decoration"]["update_on_change"] is False
    assert by_id["decoration"]["update_schedule"] is None


def test_canvas_management(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]

    # list starts with the auto-minted canvas
    listing = client.get("/pages/canvas/canvases.json").get_json()
    assert [c["id"] for c in listing["canvases"]] == [cid]

    # rename
    resp = client.post(f"/pages/canvas/c/{cid}/rename", json={"name": "Kitchen"})
    assert resp.status_code == 200 and resp.get_json()["name"] == "Kitchen"
    assert client.get(f"/pages/canvas/c/{cid}/doc.json").get_json()["name"] == "Kitchen"

    # duplicate carries the name (with a suffix) into a fresh id
    resp = client.post(f"/pages/canvas/c/{cid}/duplicate")
    dup_id = resp.get_json()["id"]
    assert dup_id != cid
    dup = client.get(f"/pages/canvas/c/{dup_id}/doc.json").get_json()
    assert dup["name"] == "Kitchen copy"
    assert len(client.get("/pages/canvas/canvases.json").get_json()["canvases"]) == 2

    # delete removes it
    resp = client.post(f"/pages/canvas/c/{dup_id}/delete")
    assert resp.status_code == 200 and resp.get_json()["deleted"] is True
    remaining = client.get("/pages/canvas/canvases.json").get_json()["canvases"]
    assert [c["id"] for c in remaining] == [cid]

    # missing canvas 404s on rename/duplicate
    assert client.post("/pages/canvas/c/nope/rename", json={"name": "x"}).status_code == 404
    assert client.post("/pages/canvas/c/nope/duplicate").status_code == 404


def test_devices_json_lists_instances_with_panel_dims(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/settings/devices/add", data={"id": "kitchen", "kind": "pimoroni_inky_4"})
    payload = client.get("/pages/canvas/devices.json").get_json()
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
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    resp = client.post(f"/pages/canvas/c/{cid}/send", json={"device_ids": ["dev_a"]})
    body = resp.get_json()
    assert resp.status_code == 200 and body["sent"] == ["dev_a"]
    assert calls[0]["png"] == b"PNGBYTES" and calls[0]["device_id"] == "dev_a"


def test_compose_canvas_mounts_widgets(app: Flask) -> None:
    """The render target emits a positioned .cell per widget element and loads
    composer.js to mount each as the real widget with its fragment."""
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    client.post(
        f"/pages/canvas/c/{cid}/save",
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
    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    assert 'data-plugin="weather_now"' in body
    assert 'data-fragment="temp"' in body
    assert "composer.js" in body  # real widget mount, not a primitive reconstruction


def test_editor_gated_but_render_not_when_flag_off(app: Flask) -> None:
    """The experimental editor is gated by the composer flag, but a canvas
    dashboard's render is not: push / scheduler must render it regardless of
    whether the editor is enabled."""
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    app.config["SETTINGS_STORE"].update_section("experiments", {"composer": False})
    assert client.get(f"/pages/canvas/c/{cid}").status_code == 404  # editor gated
    assert client.get(f"/compose/{cid}").status_code == 200  # render still works


def test_source_form_renders_widget_options(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-element config drawer renders a widget's cell_options via the
    shared macros."""
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/pages/canvas/source-form", json={"key": "weather_now", "sid": "e1"})
    assert resp.status_code == 200
    assert 'name="opt_units"' in resp.get_data(as_text=True)


def test_widget_data_live_with_sample_fallback(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """The editor's live-data endpoint fetches real data; with no location
    configured, weather_now errors and falls back to its dev-gallery sample so
    the preview still renders."""
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/pages/canvas/data.json", json={"widget": "weather_now", "options": {}})
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert isinstance(data, dict) and data.get("temp") == 19  # sample fallback


def test_widget_data_empty_widget(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/pages/canvas/data.json", json={"widget": ""})
    assert resp.status_code == 200 and resp.get_json()["data"] is None


def test_source_options_parses_form(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_EXPERIMENT_COMPOSER", "1")
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/pages/canvas/source-options",
        data={"key": "weather_now", "opt_units": "imperial", "opt_label": "Home"},
    )
    assert resp.status_code == 200
    options = resp.get_json()["options"]
    assert options["units"] == "imperial" and options["label"] == "Home"


# -- generative background (fal.ai, Approach A) --------------------------


def _mini_png() -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (32, 24), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_generate_bg_sets_bg_image(app: Flask, monkeypatch: Any) -> None:
    from app import fal_backgrounds as fb

    monkeypatch.setattr(fb, "resolve_fal_key", lambda reg, ss: "k")
    monkeypatch.setattr(fb, "generate", lambda prompt, **kw: _mini_png())
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    r = client.post(
        f"/pages/canvas/c/{cid}/generate-bg",
        json={"prompt": "a foggy shore", "style": "watercolor", "fit": "cover"},
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["status"] == "ok" and body["bg_image"].startswith("/renders/")
    doc = client.get(f"/pages/canvas/c/{cid}/doc.json").get_json()
    assert doc["bg_image"] == body["bg_image"] and doc["bg_fit"] == "cover"


def test_generate_bg_requires_prompt(app: Flask, monkeypatch: Any) -> None:
    from app import fal_backgrounds as fb

    monkeypatch.setattr(fb, "resolve_fal_key", lambda reg, ss: "k")
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    assert client.post(f"/pages/canvas/c/{cid}/generate-bg", json={}).status_code == 400


def test_generate_bg_no_key_400(app: Flask, monkeypatch: Any) -> None:
    from app import fal_backgrounds as fb

    monkeypatch.setattr(fb, "resolve_fal_key", lambda reg, ss: None)
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    r = client.post(f"/pages/canvas/c/{cid}/generate-bg", json={"prompt": "x"})
    assert r.status_code == 400 and "fal" in r.get_json()["error"].lower()


def test_editor_includes_code_editor_assets(app: Flask) -> None:
    # The canvas editor ships the CodeMirror popout + sidebar toggles, and the
    # vendored assets actually serve.
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    html = client.get(f"/pages/canvas/c/{cid}").get_data(as_text=True)
    assert "vendor/codemirror/codemirror.js" in html
    assert 'id="panels-code"' in html
    assert 'id="panels-toggle-left"' in html and 'id="panels-toggle-right"' in html
    assert 'id="panels-resize-left"' in html  # resizable sidebars
    assert 'id="panels-code-format"' in html and "beautifier.min.js" in html  # auto-format
    # The sandbox library map is exposed and points at vendored assets.
    assert "__TESSERAE_LIBS" in html and "vendor/phosphor/phosphor-bold.css" in html
    # The vendored sandbox libraries are present on disk.
    vendored = {
        "codemirror": ("codemirror.js", "codemirror.css", "htmlmixed.js", "javascript.js"),
        "chartjs": ("chart.umd.min.js", "chartjs-plugin-datalabels.min.js"),
        "canvasgauges": ("gauge.min.js",),
        "dayjs": ("dayjs.min.js", "utc.js", "timezone.js"),
        "qrcode": ("qrcode.js",),
        "marked": ("marked.min.js",),
        "chroma": ("chroma.min.js",),
        "svgjs": ("svg.min.js",),
        "jsbeautify": ("beautifier.min.js",),
        "phosphor": ("phosphor-bold.css",),
    }
    for pkg, files in vendored.items():
        for f in files:
            assert (REPO_ROOT / "static" / "vendor" / pkg / f).exists(), f"{pkg}/{f}"


def test_compose_page_wires_sandbox_libs(app: Flask) -> None:
    # The compose render page (what Playwright screenshots) also exposes the
    # library map so code elements can inline what they use into their sandbox.
    from app.state.page_store import Page
    from app.state.panel_store import CanvasLayout, Element

    client = app.test_client()
    _sign_in(client)
    page = Page(id="cc1", name="c", layout_kind="canvas")
    page.canvas = CanvasLayout(
        w=400, h=300, els=[Element(id="e", kind="code", js="x", w=100, h=100)]
    )
    app.config["PAGE_STORE"].save(page)
    html = client.get("/compose/cc1").get_data(as_text=True)
    assert "__TESSERAE_LIBS" in html
    assert "vendor/chartjs/chart.umd.min.js" in html and "vendor/phosphor/phosphor-bold.css" in html


def test_send_collapses_a_repeated_device(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """A binding is a set of devices. A repeat used to persist, which then
    double-listed the dashboard on the Dashboards page and pushed the same
    frame to the same panel twice (#229)."""
    monkeypatch.setattr("app.renderer.render_to_png", lambda request, pool=None: b"PNGBYTES")
    pushed: list[str] = []
    monkeypatch.setattr(
        app.config["PUSH_MANAGER"],
        "push_image",
        lambda png, **kw: (
            pushed.append(kw["device_id"]),
            SimpleNamespace(status="sent", error=None),
        )[1],
    )
    client = app.test_client()
    _sign_in(client)
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    resp = client.post(
        f"/pages/canvas/c/{cid}/send", json={"device_ids": ["dev_a", "dev_a", "dev_b"]}
    )
    assert resp.status_code == 200
    assert pushed == ["dev_a", "dev_b"]
    assert app.config["PAGE_STORE"].get(cid).device_ids == ["dev_a", "dev_b"]
