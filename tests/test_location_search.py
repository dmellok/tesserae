"""The ``location_search`` cell option type.

A search-for-a-city field whose chosen result stores a dict (``name``,
``country``, ``admin1``, ``latitude``, ``longitude``) instead of the
legacy three-field triplet (``latitude``, ``longitude``, ``label``).
Open-Meteo's free geocoding endpoint backs the autocomplete client-
side; the server-side contract here is the form coercion and the
``_resolved_options`` promotion that fills in the legacy fields from
the chosen location when the user hasn't manually overridden them.

Phase 1 lands the mechanism + wires it into ``weather_now``; the
other weather widgets follow in a separate pass."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from app.page_routes import _coerce_cell_option


def test_location_search_coercion_accepts_well_formed_dict() -> None:
    """The hidden input carries the JSON-encoded result Open-Meteo
    returned. Coercion parses it and extracts only the allow-listed
    fields, so a drifted / hostile payload can't slip extra keys into
    the cell options dict."""
    spec: dict[str, Any] = {"name": "location", "type": "location_search", "default": ""}
    raw = json.dumps(
        {
            "name": "Berlin",
            "country": "DE",
            "admin1": "Berlin",
            "latitude": 52.52,
            "longitude": 13.405,
            "id": 2950159,  # extra field, should be dropped
            "elevation": 74,  # ditto
        }
    )
    out = _coerce_cell_option(spec, raw, {})
    assert out == {
        "name": "Berlin",
        "country": "DE",
        "admin1": "Berlin",
        "latitude": 52.52,
        "longitude": 13.405,
    }


def test_location_search_coercion_empty_returns_empty_dict() -> None:
    """An empty hidden input means no location chosen; downstream
    expects a dict, not a string, so coercion returns ``{}``."""
    spec: dict[str, Any] = {"name": "location", "type": "location_search", "default": ""}
    assert _coerce_cell_option(spec, "", {}) == {}
    assert _coerce_cell_option(spec, None, {}) == {}


def test_location_search_coercion_garbage_returns_empty_dict() -> None:
    """Malformed JSON or non-dict JSON shouldn't 500; falls through
    to ``{}`` so the cell renders without a location."""
    spec: dict[str, Any] = {"name": "location", "type": "location_search", "default": ""}
    assert _coerce_cell_option(spec, "not json", {}) == {}
    assert _coerce_cell_option(spec, "[1, 2, 3]", {}) == {}
    assert _coerce_cell_option(spec, "null", {}) == {}


def test_location_search_coercion_skips_unparseable_coords() -> None:
    """A location with malformed coords (e.g. ``latitude: "abc"``)
    should drop the bad fields rather than corrupt the cell, the
    fallback chain in ``_resolved_options`` will fill in coords from
    the global location instead."""
    spec: dict[str, Any] = {"name": "location", "type": "location_search", "default": ""}
    raw = json.dumps({"name": "Bad", "latitude": "abc", "longitude": 13.405})
    out = _coerce_cell_option(spec, raw, {})
    assert "latitude" not in out
    assert out["longitude"] == 13.405
    assert out["name"] == "Bad"


def test_resolved_options_promotes_location_to_lat_lon_label() -> None:
    """When a cell has a ``location`` dict (set via the new search) and
    no explicit lat/lon/label override, the location's fields fill in
    the legacy slots so the weather widget's existing data-fetch path
    keeps working without changes."""
    from app import composer

    plugin = MagicMock()
    plugin.cell_option_defaults.return_value = {
        "location": "",
        "latitude": "",
        "longitude": "",
        "label": "",
        "units": "metric",
    }
    registry = MagicMock()
    registry.get.return_value = plugin

    raw: dict[str, Any] = {
        "location": {
            "name": "Berlin",
            "country": "DE",
            "admin1": "Berlin",
            "latitude": 52.52,
            "longitude": 13.405,
        },
    }

    fake_app = MagicMock()
    fake_app.config = {"PLUGIN_REGISTRY": registry, "SETTINGS_STORE": MagicMock()}
    fake_app.config["SETTINGS_STORE"].get_section.return_value = {}

    with patch.object(composer, "current_app", fake_app):
        out = composer._resolved_options("weather_now", raw)

    assert out["latitude"] == 52.52
    assert out["longitude"] == 13.405
    assert out["label"] == "Berlin"


def test_resolved_options_explicit_lat_overrides_location() -> None:
    """A power user who set lat/lon manually beats the location dict
    (e.g. they searched for "Berlin, DE" but want raw coords for a
    nearby village). The label override behaves the same way."""
    from app import composer

    plugin = MagicMock()
    plugin.cell_option_defaults.return_value = {
        "location": "",
        "latitude": "",
        "longitude": "",
        "label": "",
        "units": "metric",
    }
    registry = MagicMock()
    registry.get.return_value = plugin

    raw: dict[str, Any] = {
        "location": {
            "name": "Berlin",
            "latitude": 52.52,
            "longitude": 13.405,
        },
        "latitude": 52.6,
        "longitude": 13.5,
        "label": "Home",
    }

    fake_app = MagicMock()
    fake_app.config = {"PLUGIN_REGISTRY": registry, "SETTINGS_STORE": MagicMock()}
    fake_app.config["SETTINGS_STORE"].get_section.return_value = {}

    with patch.object(composer, "current_app", fake_app):
        out = composer._resolved_options("weather_now", raw)

    # Explicit overrides win, location stays in the cell options as a
    # record of what was searched but doesn't drive rendering.
    assert out["latitude"] == 52.6
    assert out["longitude"] == 13.5
    assert out["label"] == "Home"


def test_resolved_options_no_location_falls_back_to_global() -> None:
    """A cell with neither a ``location`` dict nor explicit coords
    falls through to the existing global-location fallback path,
    unchanged from the pre-location_search behaviour."""
    from app import composer

    plugin = MagicMock()
    plugin.cell_option_defaults.return_value = {
        "location": "",
        "latitude": "",
        "longitude": "",
        "label": "",
        "units": "metric",
    }
    registry = MagicMock()
    registry.get.return_value = plugin

    raw: dict[str, Any] = {}

    fake_app = MagicMock()
    fake_app.config = {"PLUGIN_REGISTRY": registry, "SETTINGS_STORE": MagicMock()}
    # No app-level lat/lon set → falls through to the Melbourne
    # constants in composer.py.
    fake_app.config["SETTINGS_STORE"].get_section.return_value = {}

    with patch.object(composer, "current_app", fake_app):
        out = composer._resolved_options("weather_now", raw)

    # Fallback constants, the existing behaviour.
    assert out["latitude"] == composer._FALLBACK_LAT
    assert out["longitude"] == composer._FALLBACK_LON
