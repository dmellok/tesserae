"""ha_entities smoke: fetch() builds a status grid from one get_states
call (monkeypatched), classifying on/off/other/missing entities."""

from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

_STATES = [
    {"entity_id": "light.desk", "state": "on", "attributes": {"friendly_name": "Desk lamp"}},
    {"entity_id": "lock.front", "state": "locked", "attributes": {"friendly_name": "Front door"}},
    {
        "entity_id": "sensor.temp",
        "state": "21.4",
        "attributes": {"friendly_name": "Lounge", "unit_of_measurement": "°C"},
    },
]


def _mods(app: Flask):
    reg = app.config["PLUGIN_REGISTRY"]
    return reg.get("ha_entities").server_module, reg.get("ha_core").server_module


def test_empty_when_no_entities(app: Flask) -> None:
    ent, _ = _mods(app)
    with app.app_context():
        out = ent.fetch({"entities": "  "}, {}, ctx={})
    assert out["empty"] is True


def test_classifies_and_shapes_rows(app: Flask, monkeypatch) -> None:
    ent, core = _mods(app)
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        out = ent.fetch(
            {"entities": ["light.desk", "lock.front", "sensor.temp", "sensor.ghost"]}, {}, ctx={}
        )
    rows = {r["name"]: r for r in out["items"]}
    assert rows["Desk lamp"]["status"] == "on"
    assert rows["Desk lamp"]["icon"] == "lightbulb"
    assert rows["Front door"]["status"] == "off"
    assert rows["Front door"]["icon"] == "lock"  # locked → closed padlock
    assert rows["Lounge"]["status"] == "other"
    assert rows["Lounge"]["label"] == "21.4 °C"
    # Unknown id surfaces as a missing row rather than vanishing.
    ghost = rows["sensor.ghost"]
    assert ghost["status"] == "missing"


def test_lock_icon_flips_when_unlocked(app: Flask, monkeypatch) -> None:
    ent, core = _mods(app)
    states = [{"entity_id": "lock.front", "state": "unlocked", "attributes": {}}]
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: states)
        out = ent.fetch({"entities": "lock.front"}, {}, ctx={})
    assert out["items"][0]["status"] == "on"
    assert out["items"][0]["icon"] == "lock-open"


def test_choices_delegates_to_core(app: Flask, monkeypatch) -> None:
    ent, core = _mods(app)
    with app.app_context():
        monkeypatch.setattr(
            core, "entity_choices", lambda *a, **k: [{"value": "light.x", "label": "X"}]
        )
        assert ent.choices("entity") == [{"value": "light.x", "label": "X"}]


def test_number_format_option_and_per_entity_override(app: Flask, monkeypatch) -> None:
    """#111: widget-level number_format fixes decimals on numeric states,
    and a per-entity format (4th override field) wins over it. The unit
    still follows the value."""
    ent, core = _mods(app)
    states = [
        {
            "entity_id": "sensor.temp",
            "state": "21",
            "attributes": {"friendly_name": "Lounge", "unit_of_measurement": "°C"},
        },
        {
            "entity_id": "sensor.humidity",
            "state": "55.5",
            "attributes": {"friendly_name": "Humidity", "unit_of_measurement": "%"},
        },
    ]
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: states)
        out = ent.fetch(
            {
                "entities": ["sensor.temp", "sensor.humidity"],
                "number_format": "0.0",
                "overrides": "sensor.humidity | | | 0.00",
            },
            {},
            ctx={},
        )
    rows = {r["name"]: r for r in out["items"]}
    assert rows["Lounge"]["label"] == "21.0 °C"  # widget default 0.0
    assert rows["Humidity"]["label"] == "55.50 %"  # per-entity 0.00 beats 0.0


def test_overrides_parser_reads_format_field() -> None:
    """The 4th pipe field parses into ``format``."""
    from plugins.ha_entities.server import _parse_overrides

    parsed = _parse_overrides("sensor.a | Name | icon | 0.0\nsensor.b | | | 0.00")
    assert parsed["sensor.a"] == {"name": "Name", "icon": "icon", "format": "0.0"}
    assert parsed["sensor.b"] == {"format": "0.00"}


def test_composer_mounts_widget(client: FlaskClient) -> None:
    resp = client.get("/_test/render?plugin=ha_entities&size=md")
    assert resp.status_code == 200
    assert 'data-plugin="ha_entities"' in resp.get_data(as_text=True)
