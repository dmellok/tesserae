"""Composer route shape, markup well-formedness."""

from __future__ import annotations

import pytest
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


def test_test_render_honours_fragment(client: FlaskClient) -> None:
    """?fragment=<id> renders a single declared fragment (data-fragment flows to
    the widget's ctx.cell.fragment); an unknown fragment 400s."""
    resp = client.get("/_test/render?plugin=weather_now&size=md&fragment=temp")
    assert resp.status_code == 200
    assert 'data-fragment="temp"' in resp.get_data(as_text=True)
    # Default (no fragment) renders the whole widget.
    full = client.get("/_test/render?plugin=weather_now&size=md")
    assert 'data-fragment="full"' in full.get_data(as_text=True)
    # Unknown fragment id is rejected.
    assert client.get("/_test/render?plugin=weather_now&size=md&fragment=nope").status_code == 400


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


def test_canvas_render_loads_user_themes_and_bg_fallback(app: Flask) -> None:
    """The canvas render resolves user/community themes (parity with grid render)
    and falls back to the theme paper colour for an empty bg (not black), so the
    editor and the panel agree on the background."""
    from app.state.page_store import Page
    from app.state.panel_store import CanvasLayout

    app.config["PAGE_STORE"].save(
        Page(id="cbg", name="F", layout_kind="canvas", canvas=CanvasLayout(w=300, h=200, bg=""))
    )
    body = app.test_client().get("/compose/cbg").get_data(as_text=True)
    assert "user.css" in body and "community.css" in body  # user/community themes loaded
    assert "var(--bg, #F7F5F0)" in body  # empty bg → theme paper fallback, never black


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


def test_geocode_parses_lat_lon_literal_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``"lat,lon"`` location resolves without touching the network, so a
    hand-authored / MCP-set element with literal coordinates Just Works. Bad
    or out-of-range strings fall through to the name search, which we stub to
    fail here so the assertion stays hermetic."""

    def _no_network(*a: object, **k: object) -> object:
        raise RuntimeError("no network in tests")

    monkeypatch.setattr(composer, "fetch_json", _no_network)
    composer._GEOCODE_CACHE.clear()
    out = composer._geocode("-37.65, 145.09")
    assert out is not None
    assert out["latitude"] == -37.65 and out["longitude"] == 145.09
    # A non-coordinate string and an out-of-range pair both fall to the name
    # search, which is stubbed to fail → None (never a live lookup).
    assert composer._geocode("not a place") is None
    assert composer._geocode("999,999") is None


def test_resolved_options_geocodes_bare_string_location(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue: weather widgets ignored a bare-string location and always fell
    back to the sample city. ``_resolved_options`` (the one resolver the
    preview AND the push share) now geocodes a bare string, so a source with
    ``location: "South Morang"`` resolves to that place's coords and echoes
    "South Morang" as the label, not "Melbourne"."""

    def _fake_geocode(query: str) -> dict[str, float | str] | None:
        if query.strip().lower() == "south morang":
            return {"latitude": -37.65, "longitude": 145.09, "name": "South Morang"}
        return None

    monkeypatch.setattr(composer, "_geocode", _fake_geocode)  # type: ignore[attr-defined]
    with app.app_context():
        out = composer._resolved_options("weather_now", {"location": "South Morang"})
        assert out["latitude"] == -37.65
        assert out["longitude"] == 145.09
        assert out["label"] == "South Morang"

        # An unresolvable string does NOT fall back to the app-level location
        # (that was the silent-Melbourne bug): coords stay unset and the label
        # carries the query so the widget errors for the intended place.
        app.config["SETTINGS_STORE"].update_section(
            "app",
            {"location": {"name": "Melbourne", "latitude": -37.8, "longitude": 144.9}},
        )
        miss = composer._resolved_options("weather_now", {"location": "Nowhereville"})
        assert miss.get("latitude") in (None, "")
        assert miss.get("longitude") in (None, "")
        assert miss["label"] == "Nowhereville"


def test_canvas_render_surfaces_error_for_unresolvable_location(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In the render_preview code path (``_build_canvas_els``), a data element
    whose configured location can't be resolved keeps the widget's error
    payload instead of silently swapping in the demo sample."""
    from app.state.panel_store import Element

    monkeypatch.setattr(composer, "_geocode", lambda q: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        composer,
        "_fetch_plugin_data",
        lambda *a, **k: {"error": "Location has invalid coordinates."},  # type: ignore[attr-defined]
    )
    with app.app_context():
        out = composer._build_canvas_els(
            [
                Element(
                    id="d1",
                    kind="data",
                    source="weather_now",
                    field="temp",
                    options={"location": "Nowhereville"},
                    x=0,
                    y=0,
                    w=100,
                    h=60,
                )
            ],
            400,
            300,
        )
        assert out[0]["data"].get("error")  # real error, not the Melbourne sample
        assert out[0]["data"].get("label") != "Melbourne"


def test_build_canvas_els_shares_fetch_across_same_widget(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Several elements bound to the same widget + options fetch once, not
    once per element (canvas dashboards commonly bind temp / humidity / wind
    data primitives to one weather source)."""
    from app.state.panel_store import Element

    calls: list[str] = []

    def _counting_fetch(plugin_id: str, opts: object, *a: object, **k: object) -> dict[str, int]:
        calls.append(plugin_id)
        return {"temp": 19, "humidity": 62}

    monkeypatch.setattr(composer, "_fetch_plugin_data", _counting_fetch)  # type: ignore[attr-defined]
    with app.app_context():
        composer._build_canvas_els(
            [
                Element(
                    id="a", kind="data", source="weather_now", field="temp", x=0, y=0, w=80, h=40
                ),
                Element(
                    id="b",
                    kind="data",
                    source="weather_now",
                    field="humidity",
                    x=0,
                    y=50,
                    w=80,
                    h=40,
                ),
                Element(id="c", widget="weather_now", fragment="temp", x=100, y=0, w=120, h=120),
            ],
            400,
            300,
        )
    # weather_now with the same resolved options fetched exactly once.
    assert calls.count("weather_now") == 1


def test_compose_measure_route_serves_measure_page(client: FlaskClient) -> None:
    """The loopback measure page exposes window.__measure for the MCP text-measuring
    helper and is reachable under /compose/ (static rule wins over /compose/<id>)."""
    resp = client.get("/compose/_measure")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "__measure" in body and "__tesseraeComposed" in body


def test_build_canvas_els_tags_data_source(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each element carries a data_source (live | sample | error | static | none) so
    the render report can tell the agent whether it saw real data."""
    from app.state.panel_store import Element

    monkeypatch.setattr(composer, "_fetch_plugin_data", lambda *a, **k: {"error": "boom"})
    with app.app_context():
        out = composer._build_canvas_els(
            [
                Element(id="w", widget="weather_now", x=0, y=0, w=100, h=100),
                Element(
                    id="wl",
                    widget="weather_now",
                    options={"location": {"name": "X", "latitude": 1.0, "longitude": 2.0}},
                    x=0,
                    y=0,
                    w=100,
                    h=100,
                ),
                Element(id="r", kind="rect", x=0, y=0, w=10, h=10),
            ],
            400,
            300,
        )
    by_id = {e["id"]: e for e in out}
    assert by_id["w"]["data_source"] == "sample"  # no location → demo sample
    assert by_id["wl"]["data_source"] == "error"  # configured but failing → real error
    assert by_id["r"]["data_source"] == "static"  # decoration


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
