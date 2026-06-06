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
    # No pipes — just an entity id, no override fields. Skipped.
    assert _parse_overrides("sensor.bare") == {}
    # Empty entity id (leading pipe) — skipped.
    assert _parse_overrides("| Name | icon") == {}
