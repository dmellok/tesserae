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


def test_resolved_options_custom_label_overrides_location_name() -> None:
    """A user who picked Berlin from the search but typed "Home" in the
    Label field keeps "Home". The location dict's ``name`` only fills
    the slot if the user hasn't customised it. (Latitude / longitude
    coordinate overrides went away in v0.64.14 along with the
    Settings → Server → Location fallback chain; the cell editor no
    longer surfaces manual lat / lon inputs.)"""
    from app import composer

    plugin = MagicMock()
    plugin.cell_option_defaults.return_value = {
        "location": "",
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
        "label": "Home",
    }

    fake_app = MagicMock()
    fake_app.config = {"PLUGIN_REGISTRY": registry, "SETTINGS_STORE": MagicMock()}
    fake_app.config["SETTINGS_STORE"].get_section.return_value = {}

    with patch.object(composer, "current_app", fake_app):
        out = composer._resolved_options("weather_now", raw)

    # User's label wins over location.name; the picked coords still
    # flow through for the weather data fetch.
    assert out["latitude"] == 52.52
    assert out["longitude"] == 13.405
    assert out["label"] == "Home"


def test_resolved_options_no_location_returns_missing_coords() -> None:
    """A cell with no ``location`` picked surfaces missing coordinates
    rather than falling back to a global default. Widget server code
    is expected to check for the missing values and return a friendly
    "Pick a location" error, the v0.64.14 design choice was to stop
    silently rendering Melbourne weather when the user hadn't asked
    for that."""
    from app import composer

    plugin = MagicMock()
    plugin.cell_option_defaults.return_value = {
        "location": "",
        "label": "",
        "units": "metric",
    }
    registry = MagicMock()
    registry.get.return_value = plugin

    raw: dict[str, Any] = {}

    fake_app = MagicMock()
    fake_app.config = {"PLUGIN_REGISTRY": registry, "SETTINGS_STORE": MagicMock()}
    fake_app.config["SETTINGS_STORE"].get_section.return_value = {}

    with patch.object(composer, "current_app", fake_app):
        out = composer._resolved_options("weather_now", raw)

    # No coords promoted (manifest doesn't declare latitude/longitude
    # any more, so they're not in the merged dict at all). Label
    # likewise stays empty.
    assert "latitude" not in out
    assert "longitude" not in out
    assert out.get("label") == ""


def test_location_search_macro_value_attribute_survives_html_parse() -> None:
    """v0.64.23 regression. The ``location_search_field`` macro
    rendered the saved location via ``value | tojson``. ``tojson``
    marks its output safe (intended for ``<script>`` context), so
    the JSON's literal ``"`` characters used to be written verbatim
    into the ``value="..."`` HTML attribute and terminated the
    attribute at the first inner quote. The browser parsed
    ``value="{"`` and ``.value`` came back as the single character
    ``{``. The downstream symptom: every save-all loop POSTed
    ``opt_location={``, ``_coerce_cell_option`` JSON-failed back to
    ``{}``, and the cell location was wiped on the next reload.

    Renders the macro with a realistic saved-location dict, parses
    the result with ``html.parser`` (which decodes entities the way
    a browser does), and asserts the round-tripped value attribute
    round-trips through ``json.loads`` back to the original dict.
    Catches any future regression to ``tojson`` directly into an
    HTML attribute."""
    from html.parser import HTMLParser
    from pathlib import Path

    from flask import Flask, render_template_string

    repo_root = Path(__file__).resolve().parent.parent
    app = Flask(__name__, template_folder=str(repo_root / "templates"))

    saved_loc = {
        "name": "Tokyo",
        "country": "Japan",
        "admin1": "Tōkyō",
        "latitude": 35.6895,
        "longitude": 139.6917,
    }

    with app.app_context():
        rendered = render_template_string(
            '{% from "_components.html" import location_search_field %}'
            '{{ location_search_field("test", "opt_location", "Location", value=loc) }}',
            loc=saved_loc,
        )

    found: dict[str, str] = {}

    class Hunter(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            attr_dict = dict(attrs)
            # ``data-location-storage`` is a boolean attribute (no value),
            # so html.parser returns it with value ``None``. ``in``
            # rather than ``.get(...) is not None`` since None IS the
            # value here.
            if "data-location-storage" in attr_dict:
                found["value"] = attr_dict.get("value", "") or ""

    Hunter().feed(rendered)

    assert found.get("value"), (
        "hidden input with data-location-storage not found in rendered macro output"
    )

    # If the value attribute was terminated at the first inner quote,
    # ``found["value"]`` would be ``"{"`` and json.loads raises. The
    # whole point of the test is that this round-trips clean.
    parsed_back = json.loads(found["value"])
    assert parsed_back == saved_loc
