"""Switching a widget or data plugin off without removing it.

A disabled plugin is parked in ``registry.disabled``: every picker, nav list
and background job that reads ``registry.plugins`` skips it, its routes answer
404, and a dashboard cell that uses it renders a placeholder."""

from __future__ import annotations

import json
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Blueprint, Flask
from flask.testing import FlaskClient

from app import plugin_loader
from app.main import REPO_ROOT, create_app
from app.plugin_loader import Plugin, PluginRegistry, register_routes

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "schema" / "plugin.schema.json"


def _plugin(tmp_path: Path, plugin_id: str, kind: str = "widget", *, admin: bool = True) -> Plugin:
    def blueprint() -> Blueprint:
        bp = Blueprint(f"{plugin_id}_bp", __name__)
        if admin:
            bp.add_url_rule("/", f"{plugin_id}_index", lambda: "admin page")
        bp.add_url_rule("/ping", f"{plugin_id}_ping", lambda: "pong")
        return bp

    return Plugin(
        id=plugin_id,
        path=tmp_path,
        manifest={"name": plugin_id.title(), "kind": kind, "version": "1.0.0"},
        data_dir=tmp_path,
        server_module=types.SimpleNamespace(blueprint=blueprint),  # type: ignore[arg-type]
    )


class _Store:
    def __init__(self) -> None:
        self.sections: dict[str, dict[str, Any]] = {}

    def get_section(self, name: str) -> dict[str, Any]:
        return dict(self.sections.get(name, {}))

    def patch_section(self, name: str, values: dict[str, Any]) -> None:
        self.sections.setdefault(name, {}).update(values)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[Flask, PluginRegistry, _Store]]:
    reg = PluginRegistry(
        plugins={
            "alpha": _plugin(tmp_path, "alpha"),
            "beta": _plugin(tmp_path, "beta"),
            "core": _plugin(tmp_path, "core", "data", admin=False),
            "fonty": _plugin(tmp_path, "fonty", "font", admin=False),
        }
    )
    app = Flask(__name__, template_folder=str(REPO / "templates"))
    app.config["PLUGIN_REGISTRY"] = reg
    app.config["DATA_ROOT"] = str(tmp_path)
    store = _Store()
    app.config["SETTINGS_STORE"] = store
    register_routes(app, reg)
    yield app, reg, store


def test_defaults_leave_everything_enabled() -> None:
    assert plugin_loader.disabled_ids(None) == set()
    assert plugin_loader.disabled_ids(_Store()) == set()
    assert PluginRegistry().disabled == {}


def test_disabled_ids_reads_only_true_entries() -> None:
    store = _Store()
    store.patch_section(plugin_loader.DISABLED_SECTION, {"a": True, "b": False, "c": "yes"})
    assert plugin_loader.disabled_ids(store) == {"a"}


def test_apply_disabled_filters_registry_and_ignores_unknown_and_fonts(tmp_path: Path) -> None:
    reg = PluginRegistry(
        plugins={
            "alpha": _plugin(tmp_path, "alpha"),
            "fonty": _plugin(tmp_path, "fonty", "font"),
        }
    )
    reg.apply_disabled({"alpha", "fonty", "ghost"})
    assert set(reg.plugins) == {"fonty"}
    assert set(reg.disabled) == {"alpha"}
    assert reg.widgets() == []
    assert reg.get("alpha") is None
    assert reg.is_disabled("alpha")


def test_set_disabled_round_trip(tmp_path: Path) -> None:
    reg = PluginRegistry(plugins={"alpha": _plugin(tmp_path, "alpha")})
    assert reg.set_disabled("alpha", True) is True
    assert reg.set_disabled("alpha", True) is False
    assert reg.set_disabled("alpha", False) is True
    assert "alpha" in reg.plugins and not reg.disabled


def _write_plugin(root: Path, plugin_id: str, kind: str = "widget") -> None:
    folder = root / plugin_id
    folder.mkdir(parents=True)
    manifest = {
        "tesserae_compat": "1.x",
        "name": plugin_id.title(),
        "version": "1.0.0",
        "kind": kind,
        "supports": {"sizes": ["md"]},
    }
    (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "client.js").write_text("export default function () {}", encoding="utf-8")


def test_discover_parks_disabled_plugins(tmp_path: Path) -> None:
    src = tmp_path / "plugins"
    _write_plugin(src, "one")
    _write_plugin(src, "two")
    reg = plugin_loader.discover(
        src, schema_path=SCHEMA, data_root=tmp_path / "data", disabled_ids={"two", "nope"}
    )
    assert set(reg.plugins) == {"one"}
    assert set(reg.disabled) == {"two"}
    assert not reg.errors


def test_routes_404_with_friendly_page_when_disabled(
    world: tuple[Flask, PluginRegistry, _Store],
) -> None:
    app, reg, _ = world
    client = app.test_client()
    assert client.get("/plugins/alpha/").status_code == 200
    reg.set_disabled("alpha", True)
    assert client.get("/plugins/alpha/").status_code == 404
    # Scripted callers and asset requests get a plain 404.
    assert client.get("/plugins/alpha/ping").status_code == 404
    assert client.get("/plugins/alpha/client.js").status_code == 404
    # Neighbours are untouched, and enabling restores the route.
    assert client.get("/plugins/beta/").status_code == 200
    reg.set_disabled("alpha", False)
    assert client.get("/plugins/alpha/ping").data == b"pong"


def test_plugin_disabled_at_startup_can_be_enabled_without_restart(tmp_path: Path) -> None:
    reg = PluginRegistry(plugins={"alpha": _plugin(tmp_path, "alpha")})
    reg.apply_disabled({"alpha"})
    app = Flask(__name__, template_folder=str(REPO / "templates"))
    app.config["PLUGIN_REGISTRY"] = reg
    register_routes(app, reg)
    client = app.test_client()
    assert client.get("/plugins/alpha/ping").status_code == 404
    reg.set_disabled("alpha", False)
    assert client.get("/plugins/alpha/ping").data == b"pong"
    with app.app_context():
        assert reg.plugins["alpha"].has_admin is True


def test_disable_and_enable_endpoints_persist_and_toggle(
    world: tuple[Flask, PluginRegistry, _Store],
) -> None:
    app, reg, store = world
    client = app.test_client()
    ok = client.post(
        "/plugins/disable", data={"plugin_id": "alpha"}, headers={"Accept": "application/json"}
    )
    assert ok.status_code == 200 and ok.get_json()["ok"] is True
    assert store.get_section(plugin_loader.DISABLED_SECTION) == {"alpha": True}
    assert "alpha" in reg.disabled
    assert plugin_loader.disabled_ids(store) == {"alpha"}

    ok = client.post(
        "/plugins/enable", data={"plugin_id": "alpha"}, headers={"Accept": "application/json"}
    )
    assert ok.status_code == 200
    assert plugin_loader.disabled_ids(store) == set()
    assert "alpha" in reg.plugins


@pytest.mark.parametrize("target", ["fonty", "ghost"])
def test_disable_refuses_other_kinds_and_unknown_ids(
    world: tuple[Flask, PluginRegistry, _Store], target: str
) -> None:
    app, reg, store = world
    res = app.test_client().post(
        "/plugins/disable", data={"plugin_id": target}, headers={"Accept": "application/json"}
    )
    assert res.status_code in (400, 404)
    assert res.get_json()["ok"] is False
    assert not reg.disabled
    assert store.get_section(plugin_loader.DISABLED_SECTION) == {}


def test_plugin_usage_lists_dashboards_using_a_widget() -> None:
    def page(name: str, cells: list[str], els: list[tuple[str, str]] | None = None) -> Any:
        canvas = (
            types.SimpleNamespace(els=[types.SimpleNamespace(kind=k, widget=w) for k, w in els])
            if els
            else None
        )
        return types.SimpleNamespace(
            name=name, cells=[types.SimpleNamespace(plugin=c) for c in cells], canvas=canvas
        )

    pages = [
        page("Hall", ["alpha", "beta"]),
        page("Desk", ["beta"], [("widget", "alpha")]),
        page("Spare", ["beta"], [("text", "alpha")]),
    ]
    assert plugin_loader.plugin_usage("alpha", pages) == ["Hall", "Desk"]
    assert plugin_loader.plugin_usage("zeta", pages) == []


def test_widgets_page_lists_disabled_section_and_in_use_warning(
    world: tuple[Flask, PluginRegistry, _Store],
) -> None:
    app, reg, _ = world

    class _Pages:
        def list(self) -> list[Any]:
            return [
                types.SimpleNamespace(
                    name="Hall", cells=[types.SimpleNamespace(plugin="alpha")], canvas=None
                ),
                types.SimpleNamespace(
                    name="Desk", cells=[types.SimpleNamespace(plugin="alpha")], canvas=None
                ),
            ]

    app.config["PAGE_STORE"] = _Pages()
    reg.set_disabled("beta", True)
    # The page renders through a stub base; assert on the route's view-model.
    captured: dict[str, Any] = {}

    def fake_render(name: str, **ctx: Any) -> str:
        captured.update(ctx)
        return "ok"

    import app.plugin_loader as pl

    original = pl.render_template
    pl.render_template = fake_render  # type: ignore[assignment]
    try:
        assert app.test_client().get("/plugins/").status_code == 200
    finally:
        pl.render_template = original  # type: ignore[assignment]
    assert [p.id for p in captured["disabled_plugins"]] == ["beta"]
    assert captured["plugin_usage"]["alpha"]["dashboards"] == ["Hall", "Desk"]
    assert "beta" not in {p.id for p in captured["plugins"]}
    assert "fonty" not in captured["plugin_usage"]


# -- the real app: nav, pickers, friendly page, placeholder, background jobs --


@pytest.fixture
def real_app(tmp_path: Path) -> Flask:
    return create_app(testing=True, data_root=tmp_path, plugins_dir=REPO_ROOT / "plugins")


@pytest.fixture
def real(real_app: Flask) -> FlaskClient:
    return real_app.test_client()


def _disable(client: FlaskClient, plugin_id: str) -> None:
    res = client.post(
        "/plugins/disable", data={"plugin_id": plugin_id}, headers={"Accept": "application/json"}
    )
    assert res.status_code == 200, res.get_data(as_text=True)


def _enable(client: FlaskClient, plugin_id: str) -> None:
    res = client.post(
        "/plugins/enable", data={"plugin_id": plugin_id}, headers={"Accept": "application/json"}
    )
    assert res.status_code == 200


def test_fresh_install_has_nothing_disabled(real_app: Flask) -> None:
    assert real_app.config["PLUGIN_REGISTRY"].disabled == {}
    assert real_app.config["SETTINGS_STORE"].get_section(plugin_loader.DISABLED_SECTION) == {}


def test_nav_admin_menu_drops_a_disabled_plugin_and_enable_restores_it(
    real_app: Flask, real: FlaskClient
) -> None:
    link = 'href="/plugins/gtfs/"'
    assert link in real.get("/plugins/").get_data(as_text=True)
    _disable(real, "gtfs")
    body = real.get("/plugins/").get_data(as_text=True)
    assert body.count(link) == 0
    assert "Disabled" in body
    _enable(real, "gtfs")
    assert link in real.get("/plugins/").get_data(as_text=True)


def test_picker_registry_and_widget_list_exclude_disabled(
    real_app: Flask, real: FlaskClient
) -> None:
    reg = real_app.config["PLUGIN_REGISTRY"]
    assert "gtfs" in {p.id for p in reg.widgets()}
    _disable(real, "gtfs")
    assert "gtfs" not in {p.id for p in reg.widgets()}
    catalog = real.get("/panels/catalog")
    if catalog.status_code == 200:
        assert "gtfs" not in catalog.get_data(as_text=True)


def test_disabled_admin_page_shows_friendly_404(real: FlaskClient) -> None:
    _disable(real, "gtfs")
    res = real.get("/plugins/gtfs/", headers={"Accept": "text/html"})
    assert res.status_code == 404
    text = res.get_data(as_text=True)
    assert "is disabled" in text and "Enable" in text
    assert real.get("/plugins/gtfs/client.js").status_code == 404
    _enable(real, "gtfs")
    asset = real.get("/plugins/gtfs/client.js")
    assert asset.status_code == 200
    asset.close()


def test_widgets_page_warns_when_the_widget_is_in_use(real_app: Flask, real: FlaskClient) -> None:
    from app.state.page_store import Cell, Page

    real_app.config["PAGE_STORE"].save(
        Page(
            id="p1",
            name="Kitchen",
            cells=[Cell(id="c1", plugin="gtfs", x=0, y=0, w=200, h=200)],
        )
    )
    body = real.get("/plugins/").get_data(as_text=True)
    assert "gtfs" in body
    assert "Used in 1 dashboard: Kitchen" in body
    assert "placeholder until you enable it again" in body


def test_cell_of_a_disabled_widget_renders_a_placeholder_and_keeps_options(
    real_app: Flask, real: FlaskClient
) -> None:
    from app.state.page_store import Cell, Page

    real_app.config["PAGE_STORE"].save(
        Page(
            id="p1",
            name="Kitchen",
            cells=[Cell(id="c1", plugin="gtfs", x=0, y=0, w=300, h=300, options={"stop": "X1"})],
        )
    )
    _disable(real, "gtfs")
    body = real.get("/compose/p1").get_data(as_text=True)
    assert 'data-disabled="GTFS / GTFS-RT"' in body
    _enable(real, "gtfs")
    body = real.get("/compose/p1").get_data(as_text=True)
    assert "data-disabled" not in body
    saved = real_app.config["PAGE_STORE"].get("p1")
    assert saved.cells[0].options == {"stop": "X1"}


def test_canvas_element_of_a_disabled_widget_gets_the_placeholder_flag(
    real_app: Flask, real: FlaskClient
) -> None:
    from app.state.page_store import Page
    from app.state.panel_store import CanvasLayout, Element

    real_app.config["PAGE_STORE"].save(
        Page(
            id="cv",
            name="Canvas",
            layout_kind="canvas",
            canvas=CanvasLayout(w=200, h=200, els=[Element(id="e1", widget="gtfs", w=200, h=200)]),
        )
    )
    _disable(real, "gtfs")
    body = real.get("/compose/cv").get_data(as_text=True)
    assert 'data-disabled="GTFS / GTFS-RT"' in body


def test_disabled_widget_is_not_scheduled(real_app: Flask) -> None:
    """Scheduled refresh and warm-ahead read ``registry.get``, so a switched
    off plugin contributes no placements and starts no work."""
    from app.scheduled_refresh import _supported

    reg = real_app.config["PLUGIN_REGISTRY"]
    candidates = [p for p in reg.widgets() if p.on_schedule_updates]
    if not candidates:
        pytest.skip("no bundled widget declares a schedule")
    target = candidates[0]
    kind = target.on_schedule_updates[0]["kind"]
    assert _supported(target.id, kind, reg) is True
    reg.set_disabled(target.id, True)
    assert _supported(target.id, kind, reg) is False


def test_disabling_survives_a_restart(tmp_path: Path) -> None:
    first = create_app(testing=True, data_root=tmp_path, plugins_dir=REPO_ROOT / "plugins")
    _disable(first.test_client(), "gtfs")
    second = create_app(testing=True, data_root=tmp_path, plugins_dir=REPO_ROOT / "plugins")
    reg = second.config["PLUGIN_REGISTRY"]
    assert "gtfs" in reg.disabled and "gtfs" not in reg.plugins
    assert second.test_client().get("/plugins/gtfs/ping").status_code == 404


def test_unknown_ids_in_settings_are_ignored(tmp_path: Path) -> None:
    app = create_app(testing=True, data_root=tmp_path, plugins_dir=REPO_ROOT / "plugins")
    app.config["SETTINGS_STORE"].patch_section(plugin_loader.DISABLED_SECTION, {"ghost": True})
    reg = app.config["REDISCOVER_PLUGINS"]()
    assert reg.disabled == {} and "gtfs" in reg.plugins


def test_core_data_plugins_stay_on(real_app: Flask, real: FlaskClient) -> None:
    for plugin_id in plugin_loader.ALWAYS_ON_IDS:
        res = real.post(
            "/plugins/disable",
            data={"plugin_id": plugin_id},
            headers={"Accept": "application/json"},
        )
        assert res.status_code == 400
        assert res.get_json()["ok"] is False
    reg = real_app.config["PLUGIN_REGISTRY"]
    assert not set(reg.disabled) & plugin_loader.ALWAYS_ON_IDS
    reg.apply_disabled(plugin_loader.ALWAYS_ON_IDS)
    assert not reg.disabled
    body = real.get("/plugins/").get_data(as_text=True)
    assert 'name="plugin_id" value="ha_core"' not in body


def test_plugin_usage_counts_data_and_code_elements() -> None:
    def el(kind: str, **fields: Any) -> Any:
        return types.SimpleNamespace(kind=kind, **fields)

    def page(name: str, els: list[Any]) -> Any:
        return types.SimpleNamespace(name=name, cells=[], canvas=types.SimpleNamespace(els=els))

    src = types.SimpleNamespace
    pages = [
        page("Data", [el("data", source="alpha")]),
        page("Code", [el("code", source="", sources=[src(key="beta"), src(key="alpha")])]),
        page("Legacy", [el("code", source="alpha", sources=[])]),
        page("Url", [el("code", source="", sources=[src(key="", url="https://x")])]),
    ]
    assert plugin_loader.plugin_usage("alpha", pages) == ["Data", "Code", "Legacy"]


def test_disabled_plugin_page_sends_a_signed_out_visitor_to_log_in(tmp_path: Path) -> None:
    app = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    app.config["PLUGIN_REGISTRY"].set_disabled("gtfs", True)
    res = app.test_client().get("/plugins/gtfs/", headers={"Accept": "text/html"})
    assert res.status_code == 302
    assert res.headers["Location"].endswith("/setup")
