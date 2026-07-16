"""ha_sensor smoke: fetch() builds value blocks from one get_states call
(monkeypatched, no network) for single + multiple entities."""

from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

_STATES = [
    {
        "entity_id": "sensor.lounge_temp",
        "state": "21.4",
        "attributes": {
            "friendly_name": "Lounge",
            "unit_of_measurement": "°C",
            "device_class": "temperature",
        },
    },
    {
        "entity_id": "sensor.humidity",
        "state": "55",
        "attributes": {
            "friendly_name": "Humidity",
            "unit_of_measurement": "%",
            "device_class": "humidity",
        },
    },
    {"entity_id": "light.desk", "state": "on", "attributes": {"friendly_name": "Desk"}},
]


def _mods(app: Flask):
    reg = app.config["PLUGIN_REGISTRY"]
    return reg.get("ha_sensor").server_module, reg.get("ha_core").server_module


def test_empty_when_no_entities(app: Flask) -> None:
    sensor, _ = _mods(app)
    with app.app_context():
        assert sensor.fetch({"entities": "  "}, {}, ctx={})["empty"] is True


def test_single_entity_titles_from_name(app: Flask, monkeypatch) -> None:
    sensor, core = _mods(app)
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        out = sensor.fetch({"entities": "sensor.lounge_temp"}, {}, ctx={})
    assert out["title"] == "Lounge"  # single → entity name
    assert len(out["items"]) == 1
    it = out["items"][0]
    assert it["value"] == "21.4"
    assert it["unit"] == "°C"
    assert it["icon"] == "thermometer"


def test_choices_delegates_to_core(app: Flask, monkeypatch) -> None:
    sensor, core = _mods(app)
    with app.app_context():
        monkeypatch.setattr(
            core, "entity_choices", lambda *a, **k: [{"value": "sensor.x", "label": "X"}]
        )
        assert sensor.choices("entity") == [{"value": "sensor.x", "label": "X"}]
        assert sensor.choices("other") == []


def test_multiple_entities_grid_and_icons(app: Flask, monkeypatch) -> None:
    sensor, core = _mods(app)
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        # entities arrives as a list from the multiselect control.
        out = sensor.fetch(
            {"entities": ["sensor.lounge_temp", "sensor.humidity", "light.desk"]}, {}, ctx={}
        )
    assert out["title"] == "Sensors"  # several → generic
    names = [i["name"] for i in out["items"]]
    assert names == ["Lounge", "Humidity", "Desk"]
    icons = [i["icon"] for i in out["items"]]
    assert icons == ["thermometer", "drop", "lightbulb"]  # device_class + domain


def test_hide_units(app: Flask, monkeypatch) -> None:
    sensor, core = _mods(app)
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        out = sensor.fetch({"entities": "sensor.lounge_temp", "show_unit": False}, {}, ctx={})
    assert out["items"][0]["unit"] == ""


def test_missing_entity_marked_unavailable(app: Flask, monkeypatch) -> None:
    sensor, core = _mods(app)
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        out = sensor.fetch({"entities": "sensor.ghost"}, {}, ctx={})
    assert out["items"][0]["unavailable"] is True
    assert out["items"][0]["icon"] == "question"


def test_fetch_surfaces_errors(app: Flask, monkeypatch) -> None:
    sensor, core = _mods(app)

    def boom() -> list:
        raise RuntimeError("nope")

    with app.app_context():
        monkeypatch.setattr(core, "get_states", boom)
        out = sensor.fetch({"entities": "sensor.x"}, {}, ctx={})
    assert "error" in out


def test_composer_mounts_widget(client: FlaskClient) -> None:
    resp = client.get("/_test/render?plugin=ha_sensor&size=md")
    assert resp.status_code == 200
    assert 'data-plugin="ha_sensor"' in resp.get_data(as_text=True)


def test_overrides_replace_name_and_icon(app: Flask, monkeypatch) -> None:
    """Pipe-separated ``overrides`` textarea overrides the auto-derived
    friendly_name + icon per entity. Missing fields fall back to auto."""
    sensor, core = _mods(app)
    overrides = "\n".join(
        [
            "sensor.lounge_temp | Living Room | sun",  # both
            "sensor.humidity | | wind",  # icon only
            "sensor.ghost | Custom Only |",  # name only (entity missing)
            "# comment line",
            "",  # blank line
        ]
    )
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        out = sensor.fetch(
            {
                "entities": ["sensor.lounge_temp", "sensor.humidity", "sensor.ghost"],
                "overrides": overrides,
            },
            {},
            ctx={},
        )
    items = out["items"]
    assert items[0]["name"] == "Living Room"  # overridden
    assert items[0]["icon"] == "sun"
    assert items[1]["name"] == "Humidity"  # auto (no override)
    assert items[1]["icon"] == "wind"  # overridden
    assert items[2]["name"] == "Custom Only"  # override even for missing entity
    assert items[2]["unavailable"] is True


def test_overrides_parser_tolerates_garbage() -> None:
    """Empty overrides + malformed lines don't break the parser."""
    from plugins.ha_sensor.server import _parse_overrides

    assert _parse_overrides("") == {}
    assert _parse_overrides(None) == {}
    # No pipes, just an entity id, no override fields. Skipped.
    assert _parse_overrides("sensor.bare") == {}
    # Empty entity id (leading pipe), skipped.
    assert _parse_overrides("| Name | icon") == {}


def test_format_value_default_and_patterns() -> None:
    """#111: blank format keeps the round(2):g default; number patterns
    fix the decimal places; non-numeric states pass through."""
    from plugins.ha_sensor.server import _format_value

    # Default: round to 2 dp, trim trailing zeros.
    assert _format_value("21.00") == "21"
    assert _format_value("20.55") == "20.55"
    assert _format_value("on") == "on"
    # Explicit patterns fix the decimals so a column reads consistently.
    assert _format_value("21", "0.0") == "21.0"
    assert _format_value("20.554", "0.00") == "20.55"
    assert _format_value("21.6", "0") == "22"
    # An unrecognised pattern falls back to the default rather than erroring.
    assert _format_value("21.004", "junk") == "21"
    # A non-numeric state ignores the format entirely.
    assert _format_value("cooling", "0.00") == "cooling"


def test_number_format_option_and_per_entity_override(app: Flask, monkeypatch) -> None:
    """#111: the widget-level number_format applies to every entity, and a
    per-entity format (4th override field) wins over it."""
    sensor, core = _mods(app)
    overrides = "sensor.humidity | | | 0.00"  # per-entity format only
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        out = sensor.fetch(
            {
                "entities": ["sensor.lounge_temp", "sensor.humidity"],
                "number_format": "0.0",
                "overrides": overrides,
            },
            {},
            ctx={},
        )
    items = out["items"]
    assert items[0]["value"] == "21.4"  # widget default 0.0
    assert items[1]["value"] == "55.00"  # per-entity override 0.00 beats 0.0


def test_overrides_parser_reads_format_field() -> None:
    """The 4th pipe field parses into ``format`` and name/icon stay optional."""
    from plugins.ha_sensor.server import _parse_overrides

    parsed = _parse_overrides("sensor.a | Name | icon | 0.0\nsensor.b | | | 0.00")
    assert parsed["sensor.a"] == {"name": "Name", "icon": "icon", "format": "0.0"}
    assert parsed["sensor.b"] == {"format": "0.00"}
