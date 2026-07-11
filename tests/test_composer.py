"""Composer route shape, markup well-formedness."""

from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

from app import composer


def test_test_render_route_returns_cell_markup(client: FlaskClient) -> None:
    resp = client.get("/_test/render?plugin=clock&size=md")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="clock"' in body
    assert 'class="cell' in body


def test_test_render_unknown_plugin_still_routes(client: FlaskClient) -> None:
    # /_test/render mounts whatever plugin id you give it; the client-side
    # composer surfaces the load failure rather than the server refusing.
    resp = client.get("/_test/render?plugin=does_not_exist&size=md")
    assert resp.status_code == 200
    assert 'data-plugin="does_not_exist"' in resp.get_data(as_text=True)


def test_test_render_rejects_bad_size(client: FlaskClient) -> None:
    resp = client.get("/_test/render?plugin=clock&size=enormous")
    assert resp.status_code == 400


def test_compose_route_404s_on_unknown_page(client: FlaskClient) -> None:
    resp = client.get("/compose/nonexistent")
    assert resp.status_code == 404


def test_canvas_page_renders_via_compose(app: Flask) -> None:
    """A dashboard with layout_kind='canvas' renders its freeform layout through
    the same /compose/<page_id> route grid pages use, so push / scheduler /
    rotation drive it by page id."""
    from app.state.page_store import Page
    from app.state.panel_store import CanvasLayout, Element

    store = app.config["PAGE_STORE"]
    store.save(
        Page(
            id="cvs1",
            name="Freeform",
            layout_kind="canvas",
            canvas=CanvasLayout(
                w=600,
                h=400,
                els=[
                    Element(id="e1", kind="text", text="HeyThere", x=10, y=10, w=120, h=40),
                    Element(id="e2", widget="clock", fragment="full", x=0, y=0, w=200, h=200),
                ],
            ),
        )
    )
    body = app.test_client().get("/compose/cvs1").get_data(as_text=True)
    assert "panels-stage" in body  # the scaled canvas stage
    assert 'data-plugin="clock"' in body  # widget element mounted
    assert 'class="deco"' in body and "HeyThere" in body  # decoration element present


def test_canvas_data_and_html_render_via_compose(app: Flask) -> None:
    """A data primitive (bound to a widget field) and a custom-HTML element render
    through compose, and an element may sit partly off-canvas (negative x)."""
    from app.state.page_store import Page
    from app.state.panel_store import CanvasLayout, Element

    app.config["PAGE_STORE"].save(
        Page(
            id="cvs3",
            name="F",
            layout_kind="canvas",
            canvas=CanvasLayout(
                w=400,
                h=300,
                els=[
                    Element(
                        id="d1",
                        kind="data",
                        source="weather_now",
                        field="temp",
                        display="number",
                        x=-10,
                        y=5,
                        w=120,
                        h=60,
                    ),
                    Element(
                        id="h1",
                        kind="html",
                        html="<b>mini</b>",
                        css="b{color:red}",
                        x=200,
                        y=10,
                        w=120,
                        h=80,
                    ),
                ],
            ),
        )
    )
    body = app.test_client().get("/compose/cvs3").get_data(as_text=True)
    assert 'class="deco"' in body
    assert "weather_now" in body  # the data primitive's source is embedded
    assert "mini" in body  # the custom-HTML markup is embedded for the sandbox
    assert "left: -10px" in body  # negative x renders (partly off-canvas)


def test_canvas_page_scales_to_target_panel(app: Flask) -> None:
    """An authored 300x200 canvas pushed to a 600x400 panel scales 2x."""
    from app.state.page_store import Page
    from app.state.panel_store import CanvasLayout

    store = app.config["PAGE_STORE"]
    store.save(Page(id="cvs2", name="F", layout_kind="canvas", canvas=CanvasLayout(w=300, h=200)))
    body = app.test_client().get("/compose/cvs2?w=600&h=400").get_data(as_text=True)
    assert "scale(2" in body  # min(600/300, 400/200) == 2.0


def test_canvas_page_roundtrips_in_page_store(tmp_path: object) -> None:
    """A canvas dashboard persists its layout_kind + canvas payload through the
    normal PageStore, so it's a first-class page (schedulable, bindable)."""
    from pathlib import Path

    from app.state.page_store import Page, PageStore
    from app.state.panel_store import CanvasLayout, Element

    path = Path(str(tmp_path)) / "pages.json"
    store = PageStore(path)
    store.save(
        Page(
            id="c1",
            name="Freeform",
            layout_kind="canvas",
            device_ids=["kitchen"],
            canvas=CanvasLayout(
                w=800,
                h=480,
                theme="dark",
                els=[Element(id="e", widget="clock", x=0, y=0, w=100, h=100)],
            ),
        )
    )
    reloaded = PageStore(path).get("c1")
    assert reloaded is not None
    assert reloaded.layout_kind == "canvas" and reloaded.device_ids == ["kitchen"]
    assert reloaded.canvas is not None
    assert reloaded.canvas.w == 800 and reloaded.canvas.theme == "dark"
    assert reloaded.canvas.els[0].widget == "clock"
    # A grid page keeps working unchanged (default layout_kind).
    grid = Page(id="g1", name="Grid")
    assert grid.layout_kind == "grid" and grid.canvas is None


def test_resolved_options_promotes_location_dict_to_coords_and_label(app: Flask) -> None:
    """The Settings → Server → Location fallback was removed in v0.64.14
    so weather widgets stop silently rendering "Melbourne weather" when
    the user hasn't picked anything. ``_resolved_options`` now promotes
    a ``location`` dict (from the search field) into the widget's
    ``latitude`` / ``longitude`` / ``label`` slots, and an empty cell
    returns missing-coords so widget code surfaces a friendly empty
    state instead of pretending."""
    with app.app_context():
        # Picked a city → coords + label flow through.
        out = composer._resolved_options(
            "weather_now",
            {
                "location": {
                    "name": "Berlin",
                    "latitude": 52.52,
                    "longitude": 13.405,
                },
            },
        )
        assert out["latitude"] == 52.52
        assert out["longitude"] == 13.405
        assert out["label"] == "Berlin"

        # Custom label overrides the city name from the location dict.
        out_custom = composer._resolved_options(
            "weather_now",
            {
                "location": {
                    "name": "Berlin",
                    "latitude": 52.52,
                    "longitude": 13.405,
                },
                "label": "Home",
            },
        )
        assert out_custom["label"] == "Home"
        assert out_custom["latitude"] == 52.52

        # Empty cell + no app-level location set → no coords, widget
        # code is expected to return an error / empty state from fetch().
        empty = composer._resolved_options("weather_now", {})
        assert empty.get("latitude") in (None, "")
        assert empty.get("longitude") in (None, "")


def test_resolved_options_falls_back_to_app_level_location_when_cell_empty(
    app: Flask,
) -> None:
    """v0.69.6 (issue #52 items 5 + 6): an empty cell inherits the
    Settings → Location picker so weather widgets Just Work after the
    user picks a global location, without having to pick again per cell.
    Same fallback catches the widget-type-swap case (swapping
    weather_now → weather_forecast on an existing cell): the new plugin
    sees the app-level location and doesn't force a re-search."""
    app.config["SETTINGS_STORE"].update_section(
        "app",
        {"location": {"name": "Berlin", "latitude": 52.52, "longitude": 13.405}},
    )
    with app.app_context():
        out = composer._resolved_options("weather_now", {})
        assert out["latitude"] == 52.52
        assert out["longitude"] == 13.405
        assert out["label"] == "Berlin"

        # A cell that HAS its own location still wins over the app-level
        # one (per-cell picks are the more specific signal).
        out_cell = composer._resolved_options(
            "weather_now",
            {"location": {"name": "Melbourne", "latitude": -37.8, "longitude": 144.9}},
        )
        assert out_cell["latitude"] == -37.8
        assert out_cell["label"] == "Melbourne"


def test_resolved_options_migrates_legacy_flat_lat_lon(app: Flask) -> None:
    """Pre-v0.69.6 installs stored the app-level default as separate
    ``latitude`` + ``longitude`` numbers. When ``settings.app.location``
    is empty but those legacy fields are set, the composer promotes
    them so weather widgets keep working through the upgrade without
    forcing the user to re-pick."""
    app.config["SETTINGS_STORE"].update_section("app", {"latitude": 52.52, "longitude": 13.405})
    with app.app_context():
        out = composer._resolved_options("weather_now", {})
        assert out["latitude"] == 52.52
        assert out["longitude"] == 13.405
        # Legacy fields have no ``name`` so no auto-label; ``label`` stays
        # unset and the cell can render "no label" cleanly.
        assert not out.get("label")
