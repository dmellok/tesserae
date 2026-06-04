"""Composer route shape — markup well-formedness."""

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


def test_resolved_options_fills_blank_coords_from_global_location(app: Flask) -> None:
    """Blank widget lat/long inherit the global location (Settings → Server);
    an explicit per-cell value wins; with neither set, the built-in fallback
    keeps the widget rendering."""
    with app.app_context():
        store = app.config["SETTINGS_STORE"]

        store.update_section("app", {"latitude": 51.5, "longitude": -0.12})
        filled = composer._resolved_options("weather_now", {})
        assert filled["latitude"] == 51.5
        assert filled["longitude"] == -0.12

        explicit = composer._resolved_options("weather_now", {"latitude": 40.7, "longitude": -74.0})
        assert (explicit["latitude"], explicit["longitude"]) == (40.7, -74.0)

        store.update_section("app", {"latitude": "", "longitude": ""})
        fallback = composer._resolved_options("weather_now", {})
        assert fallback["latitude"] == composer._FALLBACK_LAT
        assert fallback["longitude"] == composer._FALLBACK_LON
