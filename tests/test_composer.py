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
