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

        # Empty cell (no location picked) → no coords; widget code
        # is expected to return an error / empty state from fetch().
        empty = composer._resolved_options("weather_now", {})
        assert empty.get("latitude") in (None, "")
        assert empty.get("longitude") in (None, "")
