"""ha_climate smoke: fetch() shaping for single + multiple thermostats,
single/range setpoints, with ha_core.get_states monkeypatched (no network)."""

from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

_STATES = [
    {
        "entity_id": "climate.hall",
        "state": "heat",
        "attributes": {
            "friendly_name": "Hallway",
            "current_temperature": 19.5,
            "temperature": 21.0,
            "hvac_action": "heating",
        },
    },
    {
        "entity_id": "climate.ac",
        "state": "heat_cool",
        "attributes": {"target_temp_low": 20, "target_temp_high": 24, "current_temperature": 22},
    },
]


def _mods(app: Flask):
    reg = app.config["PLUGIN_REGISTRY"]
    return reg.get("ha_climate").server_module, reg.get("ha_core").server_module


def test_empty_when_no_entities(app: Flask) -> None:
    clim, _ = _mods(app)
    with app.app_context():
        assert clim.fetch({"entities": ""}, {}, ctx={})["empty"] is True


def test_single_setpoint_heating(app: Flask, monkeypatch) -> None:
    clim, core = _mods(app)
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        out = clim.fetch({"entities": "climate.hall"}, {}, ctx={})
    assert out["title"] == "Hallway"  # single → entity name
    it = out["items"][0]
    assert it["mode"] == "heat"
    assert it["icon"] == "fire"
    assert it["action"] == "heating"
    assert it["current"] == "19.5"
    assert it["target"] == "21"  # 21.0 trimmed


def test_multiple_thermostats(app: Flask, monkeypatch) -> None:
    clim, core = _mods(app)
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        out = clim.fetch({"entities": ["climate.hall", "climate.ac"]}, {}, ctx={})
    assert out["title"] == "Climate"
    assert len(out["items"]) == 2
    ac = out["items"][1]
    assert ac["target"] == ""
    assert ac["target_low"] == "20" and ac["target_high"] == "24"
    assert ac["icon"] == "thermometer-simple"


def test_choices_filtered_to_climate(app: Flask, monkeypatch) -> None:
    clim, core = _mods(app)
    captured = {}

    def fake_choices(domains=None):
        captured["domains"] = domains
        return [{"value": "climate.x", "label": "X"}]

    with app.app_context():
        monkeypatch.setattr(core, "entity_choices", fake_choices)
        assert clim.choices("entity") == [{"value": "climate.x", "label": "X"}]
    assert captured["domains"] == ("climate",)


def test_missing_entity_flagged(app: Flask, monkeypatch) -> None:
    clim, core = _mods(app)
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        out = clim.fetch({"entities": "climate.ghost"}, {}, ctx={})
    assert out["items"][0]["unavailable"] is True
    assert out["items"][0]["icon"] == "question"


def test_composer_mounts_widget(client: FlaskClient) -> None:
    resp = client.get("/_test/render?plugin=ha_climate&size=md")
    assert resp.status_code == 200
    assert 'data-plugin="ha_climate"' in resp.get_data(as_text=True)
