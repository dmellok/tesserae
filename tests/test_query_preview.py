"""Authenticated, opt-in widget query previews use unsaved editor options."""

import json

import pytest

from app.main import create_app


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "plugins"
    plugin = root / "picture_search"
    plugin.mkdir(parents=True)
    manifest = {
        "tesserae_compat": "1.x",
        "name": "Search",
        "version": "0.0.1",
        "kind": "widget",
        "query_preview": True,
        "supports": {"sizes": ["lg"]},
        "requires": ["network:example.com"],
        "cell_options": [
            {"name": "query", "type": "string", "label": "Query", "default": "saved"},
            {"name": "minimum", "type": "number", "label": "Minimum", "default": 2},
        ],
    }
    (plugin / "plugin.json").write_text(json.dumps(manifest))
    (plugin / "client.js").write_text("export default function() {}")
    (plugin / "server.py").write_text(
        """
def query_preview(options, settings, *, ctx):
    return {"images": [{"url": "https://example.com/image.jpg", "width": 2400, "height": 1600, "matches": [options["query"]]}] * 25,
            "total": 25, "options": options, "context": ctx}
"""
    )
    a = create_app(testing=False, data_root=tmp_path / "data", plugins_dir=root)
    a.config["TESTING"] = True
    return a


def sign_in(client):
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_preview_requires_login_and_post(app):
    client = app.test_client()
    response = client.post(
        "/plugins/picture_search/query-preview", data={"cell_w": 1600, "cell_h": 900}
    )
    assert response.status_code in (302, 401, 403)
    sign_in(client)
    assert client.get("/plugins/picture_search/query-preview").status_code in (404, 405)


def test_preview_parses_unsaved_options_and_is_not_settled(app):
    client = app.test_client()
    sign_in(client)
    response = client.post(
        "/plugins/picture_search/query-preview",
        data={"opt_query": "unsaved city", "opt_minimum": "9", "cell_w": "3840", "cell_h": "2160"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["images"]) == 20
    assert data["total"] == 25
    assert data["options"] == {"query": "unsaved city", "minimum": 9}
    assert data["context"]["cell_w"] == 3840
    assert data["context"]["cell_h"] == 2160
    assert data["context"]["preview"] is True
    assert data["context"]["settled"] is False


def test_canvas_options_include_preview_with_element_dimensions(app):
    client = app.test_client()
    sign_in(client)
    response = client.post(
        "/pages/canvas/source-form",
        json={
            "key": "picture_search",
            "sid": "element1",
            "options": {},
            "cell_w": 900,
            "cell_h": 1600,
        },
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "data-query-preview=" in html
    assert 'data-cell-w="900"' in html
    assert 'data-cell-h="1600"' in html
    assert html.index('name="opt_minimum"') < html.index("data-query-preview=")


def test_standalone_canvas_editor_loads_query_preview_assets(app):
    client = app.test_client()
    sign_in(client)
    created = client.post("/pages/canvas/new")
    html = client.get(created.location).get_data(as_text=True)
    assert 'src="/static/query-preview.js' in html
    assert 'href="/static/style/query-preview.css' in html


def test_grid_options_include_query_preview(app):
    client = app.test_client()
    sign_in(client)
    created = client.post("/pages/new", data={"name": "Art", "layout": "1_cell"})
    page_id = created.location.rstrip("/").rsplit("/", 1)[-1]
    page = app.config["PAGE_STORE"].get(page_id)
    cell = page.cells[0]
    client.post(f"/pages/{page_id}/cells/{cell.id}", data={"plugin": "picture_search"})
    html = client.get(f"/pages/{page_id}").get_data(as_text=True)
    assert 'data-query-preview="/plugins/picture_search/query-preview"' in html
    assert f'data-cell-w="{cell.w}"' in html
    assert f'data-cell-h="{cell.h}"' in html
    assert html.index('name="opt_minimum"') < html.index("data-query-preview=")


def test_preview_opt_in_and_capability_scope(app, monkeypatch):
    from app.capabilities import _active

    plugin = app.config["PLUGIN_REGISTRY"].get("picture_search")

    def query_preview(options, settings, *, ctx):
        scope = _active.get()
        assert scope.plugin_id == "picture_search"
        assert scope.network_hosts == frozenset({"example.com"})
        return {"images": []}

    monkeypatch.setattr(plugin.server_module, "query_preview", query_preview)
    client = app.test_client()
    sign_in(client)
    form = {"cell_w": "800", "cell_h": "480"}
    assert client.post("/plugins/picture_search/query-preview", data=form).status_code == 200
    plugin.manifest["query_preview"] = False
    assert client.post("/plugins/picture_search/query-preview", data=form).status_code == 404


@pytest.mark.parametrize("width", ["0", "nan", "-1", "99999999"])
def test_preview_rejects_invalid_dimensions(app, width):
    client = app.test_client()
    sign_in(client)
    assert (
        client.post(
            "/plugins/picture_search/query-preview", data={"cell_w": width, "cell_h": "900"}
        ).status_code
        == 400
    )
