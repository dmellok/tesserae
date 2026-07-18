"""MCP API (agentic canvas dashboards): the token-authed /api/mcp surface.

Covers the experiment gate, token-or-loopback auth, the canvas create/get/set
roundtrip + 422 validation, the grid-page guardrail, and the preview/push paths
(with the renderer + push manager mocked so no Chromium is spun up).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.push import PushResult
from app.state.page_store import Page

_FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-bytes"
_REMOTE = {"REMOTE_ADDR": "203.0.113.9"}  # a non-loopback client


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


def _enable(app: Flask) -> None:
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})


def _set_token(app: Flask, token: str) -> None:
    app.config["SETTINGS_STORE"].patch_section("app", {"mcp_token_secret": token})


def _create_page(client: Any, name: str = "Agent board") -> str:
    resp = client.post("/api/mcp/pages", json={"name": name, "w": 800, "h": 480})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return str(resp.get_json()["id"])


# -- gate + auth --------------------------------------------------------


def test_404_when_experiment_off(app: Flask) -> None:
    # Default: mcp experiment off → the whole surface 404s.
    assert app.test_client().get("/api/mcp/catalog").status_code == 404


def test_loopback_allowed_without_token(app: Flask) -> None:
    _enable(app)
    resp = app.test_client().get("/api/mcp/catalog")
    assert resp.status_code == 200
    assert "widgets" in resp.get_json()


def test_remote_without_token_401(app: Flask) -> None:
    _enable(app)
    resp = app.test_client().get("/api/mcp/catalog", environ_overrides=_REMOTE)
    assert resp.status_code == 401


def test_remote_with_token_ok(app: Flask) -> None:
    _enable(app)
    _set_token(app, "s3cret-token")
    resp = app.test_client().get(
        "/api/mcp/catalog",
        headers={"Authorization": "Bearer s3cret-token"},
        environ_overrides=_REMOTE,
    )
    assert resp.status_code == 200


def test_remote_with_bad_token_401(app: Flask) -> None:
    _enable(app)
    _set_token(app, "s3cret-token")
    resp = app.test_client().get(
        "/api/mcp/catalog",
        headers={"Authorization": "Bearer wrong"},
        environ_overrides=_REMOTE,
    )
    assert resp.status_code == 401


# -- discovery ----------------------------------------------------------


def test_catalog_and_widget_options(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    cat = client.get("/api/mcp/catalog").get_json()
    assert isinstance(cat["widgets"], list) and cat["widgets"]
    assert "appearance" in cat
    key = str(cat["widgets"][0]["key"])
    opts = client.get(f"/api/mcp/widgets/{key}/options")
    assert opts.status_code == 200
    assert opts.get_json()["key"] == key
    assert client.get("/api/mcp/widgets/not_a_widget/options").status_code == 404


def test_devices_shape(app: Flask) -> None:
    _enable(app)
    body = app.test_client().get("/api/mcp/devices").get_json()
    assert "devices" in body and isinstance(body["devices"], list)


def test_services_listed_and_excluded_from_catalog(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    body = client.get("/api/mcp/services").get_json()
    assert "services" in body and isinstance(body["services"], list)
    keys = {str(s["key"]) for s in body["services"]}
    # The bundled reference services are present...
    assert {"rest_service", "openmeteo_service", "ha_service"} <= keys
    for svc in body["services"]:
        assert svc["name"] and "options" in svc
    # ...and none of them leak into the placeable widget catalog.
    cat_keys = {str(w["key"]) for w in client.get("/api/mcp/catalog").get_json()["widgets"]}
    assert not (keys & cat_keys)


def test_service_probe_returns_discovery(app: Flask) -> None:
    # A service probed with empty options returns its self-describing scope map,
    # the convention the agent relies on to explore the API.
    _enable(app)
    body = app.test_client().post("/api/mcp/widgets/openmeteo_service/data", json={"options": {}})
    assert body.status_code == 200
    data = body.get_json()["data"]
    assert data["service"] == "open-meteo" and "scopes" in data


# -- canvas CRUD --------------------------------------------------------


def test_create_get_set_roundtrip(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)

    doc = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()
    assert doc["w"] == 800 and doc["h"] == 480 and doc["els"] == []

    new_doc = {
        "w": 800,
        "h": 480,
        "els": [{"id": "e1", "kind": "text", "text": "Hi", "x": 10, "y": 10, "w": 200, "h": 60}],
    }
    saved = client.put(f"/api/mcp/pages/{pid}/canvas", json=new_doc)
    assert saved.status_code == 200
    assert saved.get_json()["elements"] == 1  # compact ack

    again = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()
    assert again["els"][0]["text"] == "Hi"

    # Marked as agent-made and listed.
    pages = client.get("/api/mcp/pages").get_json()["pages"]
    entry = next(p for p in pages if p["id"] == pid)
    assert entry["created_by"] == "mcp" and entry["elements"] == 1


def test_code_element_sources_a_service(app: Flask) -> None:
    # End-to-end: a code element names a service (rest_service) as a source, and
    # the composer resolves it through the same path a widget uses, injecting the
    # service's payload as ctx.data.<name>. Probed with no url, rest_service
    # returns its discovery map, so this stays offline.
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    body = {
        "w": 800,
        "h": 480,
        "els": [
            {
                "id": "c1",
                "kind": "code",
                "x": 0,
                "y": 0,
                "w": 400,
                "h": 300,
                "sources": [{"key": "rest_service", "name": "api", "options": {}}],
                "html": "<div id=out></div>",
                "js": "document.getElementById('out').textContent = ctx.data.api.service",
            }
        ],
    }
    assert client.put(f"/api/mcp/pages/{pid}/canvas", json=body).status_code == 200
    html = client.get(f"/compose/{pid}").get_data(as_text=True)
    # The discovery payload (service: "rest") reached the element's injected data.
    assert '"service": "rest"' in html or '"service":"rest"' in html


def test_delete_page(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    resp = client.delete(f"/api/mcp/pages/{pid}")
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    pages = client.get("/api/mcp/pages").get_json()["pages"]
    assert not any(p["id"] == pid for p in pages)
    # Gone now -> 404; a grid page is never deletable via this route.
    assert client.delete(f"/api/mcp/pages/{pid}").status_code == 404
    app.config["PAGE_STORE"].save(Page(id="grid9", name="Grid", layout_kind="grid"))
    assert client.delete("/api/mcp/pages/grid9").status_code == 404


def test_set_canvas_compact_and_return_doc(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    body = {
        "w": 800,
        "h": 480,
        "els": [{"id": "e1", "kind": "rect", "x": 0, "y": 0, "w": 20, "h": 20}],
    }
    # Default: compact ack, no document echoed.
    ack = client.put(f"/api/mcp/pages/{pid}/canvas", json=body).get_json()
    assert ack["ok"] is True and ack["elements"] == 1 and ack["rev"] and "els" not in ack
    # Opt-in: full document.
    doc = client.put(f"/api/mcp/pages/{pid}/canvas?return=doc", json=body).get_json()
    assert "els" in doc and len(doc["els"]) == 1


def test_set_canvas_accepts_and_persists_shape_bindings(app: Flask) -> None:
    """The MCP surface accepts live shape bindings with no endpoint change: the
    write paths validate the Element model, which carries `bind`, so a bound shape
    round-trips through set_canvas -> get_canvas."""
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    bind = {
        "source": "weather_now",
        "options": {"location": "Melbourne"},
        "field": "temp",
        "transform": "color",
        "params": {"stops": [[5, "#4a5aa8"], [25, "#c08a2c"]], "else": "#900000"},
    }
    body = {
        "w": 800,
        "h": 480,
        "els": [{"id": "badge", "kind": "rect", "x": 0, "y": 0, "w": 60, "h": 30, "bind": [bind]}],
    }
    assert client.put(f"/api/mcp/pages/{pid}/canvas", json=body).status_code == 200
    doc = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()
    el = doc["els"][0]
    assert el["bind"][0]["transform"] == "color"
    assert el["bind"][0]["field"] == "temp"


def test_append_element_saves_incrementally(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    r1 = client.post(
        f"/api/mcp/pages/{pid}/elements",
        json={"kind": "text", "text": "hi", "x": 5, "y": 5, "w": 80, "h": 30},
    )
    assert r1.status_code == 200
    j1 = r1.get_json()
    assert j1["ok"] and j1["elements"] == 1 and j1["element_id"]
    r2 = client.post(
        f"/api/mcp/pages/{pid}/elements", json={"kind": "rect", "x": 0, "y": 0, "w": 40, "h": 40}
    )
    assert r2.get_json()["elements"] == 2  # accumulates
    # Bad element → 422.
    assert (
        client.post(f"/api/mcp/pages/{pid}/elements", json={"kind": "rect", "w": 0}).status_code
        == 422
    )


def test_probe_widget_data_returns_payload(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    resp = client.post("/api/mcp/widgets/weather_now/data", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["key"] == "weather_now" and "data" in body
    assert client.post("/api/mcp/widgets/not_a_widget/data", json={}).status_code == 404


def test_catalog_omits_samples(app: Flask) -> None:
    _enable(app)
    cat = app.test_client().get("/api/mcp/catalog").get_json()
    assert cat["widgets"] and all("sample" not in w for w in cat["widgets"])


def test_set_invalid_returns_422(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    # w must be > 0 → a validation error with field-level details.
    resp = client.put(f"/api/mcp/pages/{pid}/canvas", json={"w": 0})
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"] == "invalid canvas document"
    assert isinstance(body["details"], list) and body["details"]


def test_grid_page_write_rejected(app: Flask) -> None:
    _enable(app)
    # A grid dashboard is not a canvas → MCP writes/reads 404 (guardrail).
    app.config["PAGE_STORE"].save(Page(id="grid1", name="Grid", layout_kind="grid"))
    client = app.test_client()
    assert client.get("/api/mcp/pages/grid1/canvas").status_code == 404
    assert client.put("/api/mcp/pages/grid1/canvas", json={"w": 800}).status_code == 404


# -- preview + push (renderer / push manager mocked) --------------------


def test_preview_returns_png(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    monkeypatch.setattr("app.renderer.render_to_png", lambda req, pool=None: _FAKE_PNG)
    client = app.test_client()
    pid = _create_page(client)
    resp = client.get(f"/api/mcp/pages/{pid}/preview.png")
    assert resp.status_code == 200 and resp.mimetype == "image/png"
    assert resp.get_data() == _FAKE_PNG


# -- faithful render / Screenshot Contract (widgets/<id>/render.png) ------


def _png_of_size(w: int, h: int) -> bytes:
    """A real PNG at exactly ``w x h`` so the render mock exercises the
    endpoint's dimensioning end to end (the browser viewport drives the size)."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def _viewport_render(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.renderer.render_to_png",
        lambda req, pool=None: _png_of_size(req.viewport_w, req.viewport_h),
    )


def test_render_png_lg_is_1200x800(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    from io import BytesIO

    from PIL import Image

    _enable(app)
    _viewport_render(monkeypatch)
    resp = app.test_client().get("/api/mcp/widgets/clock_analog/render.png?size=lg")
    assert resp.status_code == 200 and resp.mimetype == "image/png"
    assert Image.open(BytesIO(resp.get_data())).size == (1200, 800)


def test_render_png_explicit_wh_overrides_size(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    from io import BytesIO

    from PIL import Image

    _enable(app)
    _viewport_render(monkeypatch)
    resp = app.test_client().get("/api/mcp/widgets/clock_analog/render.png?w=300&h=200")
    assert resp.status_code == 200
    assert Image.open(BytesIO(resp.get_data())).size == (300, 200)


def test_render_png_wh_clamped(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    from io import BytesIO

    from PIL import Image

    _enable(app)
    _viewport_render(monkeypatch)
    resp = app.test_client().get("/api/mcp/widgets/clock_analog/render.png?w=999999&h=1")
    assert resp.status_code == 200
    # 4096 max / 16 min bounds (see composer.clamp_screenshot_dim).
    assert Image.open(BytesIO(resp.get_data())).size == (4096, 16)


def test_render_png_bad_wh_400(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    _viewport_render(monkeypatch)
    resp = app.test_client().get("/api/mcp/widgets/clock_analog/render.png?w=abc&h=200")
    assert resp.status_code == 400 and "error" in resp.get_json()


def test_render_png_unknown_widget_404(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    monkeypatch.setattr("app.renderer.render_to_png", lambda req, pool=None: _FAKE_PNG)
    resp = app.test_client().get("/api/mcp/widgets/does_not_exist/render.png")
    assert resp.status_code == 404 and "error" in resp.get_json()


def test_render_png_render_unavailable_503(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)

    def _boom(req: Any, pool: Any = None) -> bytes:
        raise RuntimeError("no browser")

    monkeypatch.setattr("app.renderer.render_to_png", _boom)
    resp = app.test_client().get("/api/mcp/widgets/clock_analog/render.png?size=lg")
    assert resp.status_code == 503 and "error" in resp.get_json()


def test_render_png_bad_options_400(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    monkeypatch.setattr("app.renderer.render_to_png", lambda req, pool=None: _FAKE_PNG)
    client = app.test_client()
    # Both spellings validate; malformed JSON is a 400, not a silent fallthrough.
    assert client.get("/api/mcp/widgets/clock_analog/render.png?opts=not-json").status_code == 400
    assert client.get("/api/mcp/widgets/clock_analog/render.png?options=%7Bbad").status_code == 400
    # Valid JSON that isn't an object is also rejected ([1,2]).
    assert (
        client.get("/api/mcp/widgets/clock_analog/render.png?opts=%5B1%2C2%5D").status_code == 400
    )
    # A valid object still renders.
    assert client.get("/api/mcp/widgets/clock_analog/render.png?opts=%7B%7D").status_code == 200


def test_render_png_unknown_fragment_400(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    monkeypatch.setattr("app.renderer.render_to_png", lambda req, pool=None: _FAKE_PNG)
    resp = app.test_client().get("/api/mcp/widgets/weather_now/render.png?fragment=nope")
    assert resp.status_code == 400 and "error" in resp.get_json()


# -- AI background generation (fal.ai, Approach A) -----------------------


def test_generate_background_sets_bg_image(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    from app import fal_backgrounds as fb

    monkeypatch.setattr(fb, "resolve_fal_key", lambda reg, ss: "test-key")
    # Stand in for the fal round-trip: return a PNG sized to the canvas.
    monkeypatch.setattr(
        fb, "generate", lambda prompt, **kw: _png_of_size(kw["width"], kw["height"])
    )
    client = app.test_client()
    pid = _create_page(client)
    resp = client.post(
        f"/api/mcp/pages/{pid}/background",
        json={"prompt": "a calm foggy sea", "style": "watercolor", "fit": "cover"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True
    assert body["bg_image"].startswith("/renders/") and body["bg_image"].endswith(".png")
    # Persisted on the canvas doc so the render + editor pick it up.
    doc = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()
    assert doc["bg_image"] == body["bg_image"] and doc["bg_fit"] == "cover"
    # The asset was actually written under the renders dir.
    fname = body["bg_image"].split("/renders/")[1]
    assert (app.config["RENDERS_DIR"] / fname).exists()


def test_generate_background_requires_prompt(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    from app import fal_backgrounds as fb

    monkeypatch.setattr(fb, "resolve_fal_key", lambda reg, ss: "k")
    client = app.test_client()
    pid = _create_page(client)
    assert client.post(f"/api/mcp/pages/{pid}/background", json={}).status_code == 400


def test_generate_background_no_key_400(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    from app import fal_backgrounds as fb

    monkeypatch.setattr(fb, "resolve_fal_key", lambda reg, ss: None)
    client = app.test_client()
    pid = _create_page(client)
    r = client.post(f"/api/mcp/pages/{pid}/background", json={"prompt": "x"})
    assert r.status_code == 400 and "fal" in r.get_json()["error"].lower()


def test_generate_background_fal_failure_502(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    from app import fal_backgrounds as fb

    monkeypatch.setattr(fb, "resolve_fal_key", lambda reg, ss: "k")

    def _boom(prompt: str, **kw: Any) -> bytes:
        raise fb.FalError("fal.ai returned 401: bad key")

    monkeypatch.setattr(fb, "generate", _boom)
    client = app.test_client()
    pid = _create_page(client)
    r = client.post(f"/api/mcp/pages/{pid}/background", json={"prompt": "x"})
    assert r.status_code == 502 and "generation failed" in r.get_json()["error"]


def test_generate_background_unknown_page_404(app: Flask) -> None:
    _enable(app)
    r = app.test_client().post("/api/mcp/pages/nope/background", json={"prompt": "x"})
    assert r.status_code == 404


# -- code elements (HTML/CSS/JS fed by a widget's data) ------------------


def test_code_element_roundtrips_via_mcp(app: Flask) -> None:
    # An agent authors a code element through the existing element endpoint
    # (kind is a free string; source/html/css/js already on the model), and it
    # validates + persists.
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    code_el = {
        "kind": "code",
        "source": "weather_now",
        "options": {"location": "Melbourne"},
        "html": "<div id='o'></div>",
        "css": "#o{color:red}",
        "js": "document.getElementById('o').textContent = ctx.data ? 'ok' : 'none';",
        "x": 0,
        "y": 0,
        "w": 200,
        "h": 100,
    }
    r = client.post(f"/api/mcp/pages/{pid}/elements", json=code_el)
    assert r.status_code == 200, r.get_data(as_text=True)
    doc = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()
    e = doc["els"][0]
    assert e["kind"] == "code" and e["source"] == "weather_now"
    assert e["js"].startswith("document.getElementById") and e["css"] == "#o{color:red}"


def test_code_element_multi_source_roundtrips_via_mcp(app: Flask) -> None:
    # The multi-source `sources` array round-trips through the element endpoint.
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    code_el = {
        "kind": "code",
        "sources": [
            {"key": "weather_now", "options": {"location": "Melbourne"}, "name": "weather"},
            {"key": "clock_word", "options": {}, "name": "time"},
        ],
        "js": "document.body.textContent = Object.keys(ctx.data).join(',');",
        "x": 0,
        "y": 0,
        "w": 300,
        "h": 200,
    }
    r = client.post(f"/api/mcp/pages/{pid}/elements", json=code_el)
    assert r.status_code == 200, r.get_data(as_text=True)
    e = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()["els"][0]
    assert [s["name"] for s in e["sources"]] == ["weather", "time"]
    assert e["sources"][0]["key"] == "weather_now"


def test_code_element_build_resolves_named_sources(app: Flask) -> None:
    # The composer resolves each named source and hands a {name: data} map, so
    # the client injects it as ctx.data.<name>.
    from app.composer import _build_canvas_els
    from app.state.panel_store import CodeSource, Element

    _enable(app)
    with app.app_context():
        els = _build_canvas_els(
            [
                Element(
                    id="c1",
                    kind="code",
                    sources=[
                        CodeSource(key="weather_now", name="weather"),
                        CodeSource(key="clock_word", name="time"),
                    ],
                    html="<div></div>",
                    js="x",
                    w=300,
                    h=200,
                )
            ],
            800,
            480,
        )
    out = els[0]
    assert out["kind"] == "code" and out["js"] == "x"
    assert isinstance(out["data"], dict) and set(out["data"].keys()) == {"weather", "time"}
    assert out["data_source"] in ("live", "sample", "error")


def test_code_element_legacy_single_source_maps(app: Flask) -> None:
    # A bare `source` (pre-multi-source form) still resolves, keyed by widget id.
    from app.composer import _build_canvas_els
    from app.state.panel_store import Element

    _enable(app)
    with app.app_context():
        els = _build_canvas_els(
            [Element(id="c1", kind="code", source="weather_now", js="x", w=200, h=100)],
            800,
            480,
        )
    assert "weather_now" in els[0]["data"]


def test_append_code_streams_and_saves(app: Flask) -> None:
    # Streaming a code element in: each append grows the field, bumps the rev
    # (which is what pushes a live update to an open editor), and persists.
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    created = client.post(
        f"/api/mcp/pages/{pid}/elements", json={"kind": "code", "x": 0, "y": 0, "w": 100, "h": 100}
    ).get_json()
    eid, rev0 = created["element_id"], created["rev"]
    a = client.post(
        f"/api/mcp/pages/{pid}/elements/{eid}/append", json={"field": "js", "text": "var a=1;\n"}
    )
    assert a.status_code == 200
    ja = a.get_json()
    assert ja["length"] == len("var a=1;\n") and ja["rev"] != rev0
    b = client.post(
        f"/api/mcp/pages/{pid}/elements/{eid}/append", json={"field": "js", "text": "var b=2;\n"}
    ).get_json()
    assert b["length"] == len("var a=1;\nvar b=2;\n")
    doc = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()
    assert doc["els"][0]["js"] == "var a=1;\nvar b=2;\n"


def test_append_code_validates_inputs(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    eid = client.post(
        f"/api/mcp/pages/{pid}/elements", json={"kind": "code", "x": 0, "y": 0, "w": 10, "h": 10}
    ).get_json()["element_id"]
    base = f"/api/mcp/pages/{pid}/elements/{eid}/append"
    assert client.post(base, json={"field": "nope", "text": "x"}).status_code == 400
    assert client.post(base, json={"field": "js"}).status_code == 400  # missing text
    assert (
        client.post(
            f"/api/mcp/pages/{pid}/elements/missing/append", json={"field": "js", "text": "x"}
        ).status_code
        == 404
    )


def test_push_requires_device_ids(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    assert client.post(f"/api/mcp/pages/{pid}/push", json={}).status_code == 400


def test_push_ok(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    monkeypatch.setattr("app.renderer.render_to_png", lambda req, pool=None: _FAKE_PNG)
    pm = MagicMock()
    pm.push_image.return_value = PushResult(status="sent", page_id="c", composition_digest="d")
    app.config["PUSH_MANAGER"] = pm
    client = app.test_client()
    pid = _create_page(client)
    resp = client.post(f"/api/mcp/pages/{pid}/push", json={"device_ids": ["dev1"]})
    assert resp.status_code == 200
    assert resp.get_json()["sent"] == ["dev1"]
    (payload,) = pm.push_image.call_args.args
    assert payload == _FAKE_PNG


def test_push_fans_out_to_multiple_devices(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    # One render, pushed to every device in the list.
    _enable(app)
    monkeypatch.setattr("app.renderer.render_to_png", lambda req, pool=None: _FAKE_PNG)
    pm = MagicMock()
    pm.push_image.return_value = PushResult(status="sent", page_id="c", composition_digest="d")
    app.config["PUSH_MANAGER"] = pm
    client = app.test_client()
    pid = _create_page(client)
    resp = client.post(f"/api/mcp/pages/{pid}/push", json={"device_ids": ["dev1", "dev2", "dev3"]})
    assert resp.status_code == 200
    assert resp.get_json()["sent"] == ["dev1", "dev2", "dev3"]
    assert pm.push_image.call_count == 3  # rendered once, pushed thrice


def test_bind_devices_persists_and_filters_unknown(app: Flask) -> None:
    from types import SimpleNamespace

    _enable(app)
    reg = MagicMock()
    reg.all.return_value = [
        SimpleNamespace(id="dev1", kind_of="pico_bin_client"),
        SimpleNamespace(id="dev2", kind_of="pico_bin_client"),
    ]
    app.config["DEVICE_REGISTRY"] = reg
    client = app.test_client()
    pid = _create_page(client)
    resp = client.post(
        f"/api/mcp/pages/{pid}/devices", json={"device_ids": ["dev1", "dev2", "ghost"]}
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["bound"] == ["dev1", "dev2"] and body["unknown"] == ["ghost"]
    # Persisted on the page so a later push / schedule / editor Send targets it.
    page = app.config["PAGE_STORE"].get(pid)
    assert page.device_ids == ["dev1", "dev2"]


def test_bind_devices_rejects_non_list(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    assert (
        client.post(f"/api/mcp/pages/{pid}/devices", json={"device_ids": "dev1"}).status_code == 400
    )


# -- partial updates (#3) -----------------------------------------------


def test_patch_element_updates_in_place(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    client.put(
        f"/api/mcp/pages/{pid}/canvas",
        json={
            "w": 800,
            "h": 480,
            "els": [{"id": "e1", "kind": "text", "text": "Hi", "x": 0, "y": 0, "w": 100, "h": 40}],
        },
    )
    ack = client.patch(f"/api/mcp/pages/{pid}/elements/e1", json={"text": "Bye", "x": 20})
    assert ack.status_code == 200 and ack.get_json()["elements"] == 1
    doc = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()
    assert doc["els"][0]["text"] == "Bye" and doc["els"][0]["x"] == 20
    assert doc["els"][0]["y"] == 0  # untouched field preserved
    # Unknown element → 404; invalid patch → 422.
    assert client.patch(f"/api/mcp/pages/{pid}/elements/nope", json={"x": 1}).status_code == 404
    assert client.patch(f"/api/mcp/pages/{pid}/elements/e1", json={"w": 0}).status_code == 422


def test_delete_element(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    client.put(
        f"/api/mcp/pages/{pid}/canvas",
        json={
            "w": 800,
            "h": 480,
            "els": [
                {"id": "a", "kind": "rect", "x": 0, "y": 0, "w": 10, "h": 10},
                {"id": "b", "kind": "rect", "x": 0, "y": 0, "w": 10, "h": 10},
            ],
        },
    )
    ack = client.delete(f"/api/mcp/pages/{pid}/elements/a")
    assert ack.status_code == 200 and ack.get_json()["elements"] == 1
    doc = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()
    assert [e["id"] for e in doc["els"]] == ["b"]
    assert client.delete(f"/api/mcp/pages/{pid}/elements/a").status_code == 404


def test_patch_canvas_meta_only(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    client.post(
        f"/api/mcp/pages/{pid}/elements",
        json={"kind": "rect", "x": 0, "y": 0, "w": 10, "h": 10},
    )
    ack = client.patch(f"/api/mcp/pages/{pid}/canvas", json={"theme": "dark", "w": 600})
    assert ack.status_code == 200
    doc = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()
    assert doc["theme"] == "dark" and doc["w"] == 600
    assert len(doc["els"]) == 1  # elements untouched


# -- drift guard (#8) ---------------------------------------------------


def test_base_rev_conflict(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    rev0 = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()["rev"]
    # Write with the correct base_rev succeeds and moves the rev on.
    ok = client.put(
        f"/api/mcp/pages/{pid}/canvas?base_rev={rev0}",
        json={
            "w": 800,
            "h": 480,
            "els": [{"id": "e1", "kind": "rect", "x": 0, "y": 0, "w": 9, "h": 9}],
        },
    )
    assert ok.status_code == 200 and ok.get_json()["updated_by"] == "mcp"
    # A second write with the now-stale rev is refused as a drift conflict.
    stale = client.put(
        f"/api/mcp/pages/{pid}/canvas?base_rev={rev0}",
        json={"w": 800, "h": 480, "els": []},
    )
    assert stale.status_code == 409
    body = stale.get_json()
    assert body["drifted"] is True and body["current_rev"]


# -- slim options + choices (#4/#5) -------------------------------------


def test_widget_options_format_hint_and_choice_strip(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    opts = client.get("/api/mcp/widgets/weather_now/options").get_json()["options"]
    loc = next(o for o in opts if o["name"] == "location")
    assert loc["type"] == "location_search" and "lat,lon" in loc["format"]
    # A long choice list is stripped by default; a short one is inlined.
    for o in opts:
        if "choices_count" in o:
            assert "choices" not in o and o["choices_endpoint"]


def test_widget_choices_endpoint(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    # Missing option → 400; unknown option → 404.
    assert client.get("/api/mcp/widgets/weather_now/choices").status_code == 400
    assert client.get("/api/mcp/widgets/weather_now/choices?option=nope").status_code == 404
    # A real option returns a paginated shape (units has concrete choices).
    r = client.get("/api/mcp/widgets/weather_now/choices?option=units&limit=1")
    assert r.status_code == 200
    body = r.get_json()
    assert body["option"] == "units" and "total" in body and isinstance(body["choices"], list)


# -- device capabilities (#6) -------------------------------------------


def test_gamut_info_palette_and_mono() -> None:
    from app import mcp_api

    e6 = mcp_api._gamut_info("waveshare_e6")
    assert e6["color_mode"].startswith("6-colour") and len(e6["colors"]) == 6
    assert e6["mono"] is False
    unknown = mcp_api._gamut_info("nonsense")
    assert unknown["colors"] == [] and unknown["mono"] is True


# -- probe data_source + fields (#1/#5) ---------------------------------


def test_probe_reports_data_source_and_fields(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    # Force the live fetch to fail so the branch is deterministic offline.
    monkeypatch.setattr("app.composer._fetch_plugin_data", lambda *a, **k: {"error": "boom"})
    client = app.test_client()
    # No location configured → falls back to the demo sample, flagged as such.
    j = client.post("/api/mcp/widgets/weather_now/data", json={}).get_json()
    assert j["data_source"] == "sample"
    assert any(f["path"] == "temp" for f in j["fields"])  # bindable paths surfaced
    # A configured (but here failing) location surfaces the real error, no sample.
    j2 = client.post(
        "/api/mcp/widgets/weather_now/data",
        json={"options": {"location": {"name": "X", "latitude": 1.0, "longitude": 2.0}}},
    ).get_json()
    assert j2["data_source"] == "error" and j2["reason"]


# -- layout arrange (#7) ------------------------------------------------


def test_arrange_grid_row_column(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    box = {"x": 0, "y": 0, "w": 300, "h": 200}
    grid = client.post(
        "/api/mcp/layout", json={"box": box, "count": 4, "layout": "grid", "cols": 2}
    ).get_json()["boxes"]
    assert len(grid) == 4
    assert grid[0]["x"] == 0 and grid[1]["x"] == 150  # two columns
    assert grid[2]["y"] == 100  # second row
    row = client.post(
        "/api/mcp/layout", json={"box": box, "count": 3, "layout": "row", "gap": 10}
    ).get_json()["boxes"]
    assert len(row) == 3 and all(b["h"] == 200 for b in row)
    col = client.post(
        "/api/mcp/layout", json={"box": box, "count": 2, "layout": "column"}
    ).get_json()["boxes"]
    assert col[0]["w"] == 300 and col[1]["y"] == 100
    assert client.post("/api/mcp/layout", json={"box": box, "count": 0}).status_code == 400


# -- render report + measure (#2/#7a) -----------------------------------


def test_render_report_shape(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    fake = {
        "board": {"w": 800, "h": 480, "background": "rgb(1,2,3)", "theme": "light"},
        "elements": [
            {"id": "e1", "kind": "text", "data_source": "static", "overflow_x": True, "text": "hi"}
        ],
    }
    monkeypatch.setattr("app.renderer.inspect_composed", lambda req, pool=None: fake)
    client = app.test_client()
    pid = _create_page(client)
    body = client.get(f"/api/mcp/pages/{pid}/render_report").get_json()
    assert body["id"] == pid and body["rev"]
    assert body["board"]["w"] == 800
    assert body["elements"][0]["overflow_x"] is True


def test_measure_text(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    monkeypatch.setattr(
        "app.renderer.inspect_composed",
        lambda req, pool=None: [{"text": "4.89 kg", "width": 120, "height": 24, "fits": False}],
    )
    client = app.test_client()
    out = client.post("/api/mcp/measure-text", json={"text": "4.89 kg", "max_width": 80})
    assert out.status_code == 200
    items = out.get_json()["items"]
    assert items[0]["width"] == 120 and items[0]["fits"] is False
    assert client.post("/api/mcp/measure-text", json={}).status_code == 400


# -- unknown element keys 422 (touch actions that would silently vanish) --


def test_append_element_rejects_unknown_keys(app: Flask) -> None:
    """Pydantic ignores unknown keys, so an agent writing ``tap`` instead
    of ``on_tap`` used to get a 200 while the interaction evaporated
    (then nothing fired on the panel and the touch monitor showed no
    regions). The MCP write paths now 422 with the bad keys named."""
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    resp = client.post(
        f"/api/mcp/pages/{pid}/elements",
        json={"kind": "box", "x": 0, "y": 0, "w": 100, "h": 50, "tap": "page:other"},
    )
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["unknown_fields"] == ["tap"]
    assert "on_tap" in body["error"]


def test_append_element_accepts_touch_fields(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    resp = client.post(
        f"/api/mcp/pages/{pid}/elements",
        json={
            "kind": "hotspot",
            "x": 0,
            "y": 0,
            "w": 100,
            "h": 50,
            "on_tap": {"action": "ha", "domain": "light", "service": "toggle"},
            "on_swipe": {"left": "rotate_next"},
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    doc = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()
    el = doc["els"][0]
    assert el["on_tap"]["action"] == "ha"
    assert el["on_swipe"] == {"left": "rotate_next"}


def test_append_element_accepts_structured_swipe(app: Flask) -> None:
    """A swipe direction can carry a structured HA object (issue #49 agent
    feedback: on_swipe was typed dict[str, str], so an inline HA action
    422'd at write time and the interaction never stored)."""
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    resp = client.post(
        f"/api/mcp/pages/{pid}/elements",
        json={
            "kind": "hotspot",
            "x": 0,
            "y": 0,
            "w": 100,
            "h": 50,
            "on_swipe": {
                "left": {"action": "ha", "domain": "light", "service": "toggle"},
                "right": "rotate_next",
            },
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    el = client.get(f"/api/mcp/pages/{pid}/canvas").get_json()["els"][0]
    assert el["on_swipe"]["left"]["service"] == "toggle"
    assert el["on_swipe"]["right"] == "rotate_next"


def test_patch_element_rejects_unknown_keys(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    made = client.post(
        f"/api/mcp/pages/{pid}/elements",
        json={"kind": "box", "x": 0, "y": 0, "w": 100, "h": 50},
    ).get_json()
    resp = client.patch(
        f"/api/mcp/pages/{pid}/elements/{made['element_id']}",
        json={"swipe": {"left": "rotate_next"}},
    )
    assert resp.status_code == 422
    assert resp.get_json()["unknown_fields"] == ["swipe"]


def test_set_canvas_rejects_unknown_element_keys_with_index(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    pid = _create_page(client)
    resp = client.put(
        f"/api/mcp/pages/{pid}/canvas",
        json={
            "w": 800,
            "h": 480,
            "els": [
                {"id": "a1", "kind": "box", "x": 0, "y": 0, "w": 10, "h": 10},
                {"id": "b2", "kind": "box", "x": 0, "y": 0, "w": 10, "h": 10, "onTap": "refresh"},
            ],
        },
    )
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["unknown_fields"] == ["onTap"]
    assert "els[1]" in body["error"]
