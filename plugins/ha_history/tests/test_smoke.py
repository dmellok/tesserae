"""ha_history smoke: fetch() coerces series to floats, downsamples, adds a
trend, for single + multiple entities — get_states + history monkeypatched."""

from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

_STATES = [
    {
        "entity_id": "sensor.temp",
        "state": "21.4",
        "attributes": {"friendly_name": "Lounge", "unit_of_measurement": "°C"},
    },
    {
        "entity_id": "sensor.power",
        "state": "350",
        "attributes": {"friendly_name": "Power", "unit_of_measurement": "W"},
    },
]


def _mods(app: Flask):
    reg = app.config["PLUGIN_REGISTRY"]
    return reg.get("ha_history").server_module, reg.get("ha_core").server_module


def test_empty_when_no_entities(app: Flask) -> None:
    hist, _ = _mods(app)
    with app.app_context():
        assert hist.fetch({"entities": ""}, {}, ctx={})["empty"] is True


def test_single_series_trend_and_axis(app: Flask, monkeypatch) -> None:
    hist, core = _mods(app)
    rising = [{"state": str(v)} for v in [10, 11, 13, 16, 19]]
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        monkeypatch.setattr(core, "history", lambda eid, hours=24: rising)
        out = hist.fetch({"entities": "sensor.temp", "hours": 12}, {}, ctx={})
    assert out["title"] == "Lounge"
    assert out["hours"] == 12
    it = out["items"][0]
    assert it["sparse"] is False
    assert it["values"] == [10.0, 11.0, 13.0, 16.0, 19.0]
    assert it["min"] == "10" and it["max"] == "19"
    assert it["trend"] == "up"
    assert it["unit"] == "°C"


def test_multiple_series(app: Flask, monkeypatch) -> None:
    hist, core = _mods(app)
    series = {
        "sensor.temp": [{"state": "20"}, {"state": "18"}],
        "sensor.power": [{"state": "5"}, {"state": "5"}],
    }
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        monkeypatch.setattr(core, "history", lambda eid, hours=24: series[eid])
        out = hist.fetch({"entities": ["sensor.temp", "sensor.power"]}, {}, ctx={})
    assert out["title"] == "History"
    assert [i["name"] for i in out["items"]] == ["Lounge", "Power"]
    assert out["items"][0]["trend"] == "down"
    assert out["items"][1]["trend"] == "flat"  # 5 → 5


def test_sparse_when_no_numeric_history(app: Flask, monkeypatch) -> None:
    hist, core = _mods(app)
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        monkeypatch.setattr(core, "history", lambda eid, hours=24: [{"state": "unavailable"}])
        out = hist.fetch({"entities": "sensor.temp"}, {}, ctx={})
    assert out["items"][0]["sparse"] is True
    assert out["items"][0]["current"] == "21.4"


def test_downsamples_and_clamps_hours(app: Flask, monkeypatch) -> None:
    hist, core = _mods(app)
    captured = {}

    def fake_history(eid, hours=24):
        captured["hours"] = hours
        return [{"state": str(i)} for i in range(500)]

    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        monkeypatch.setattr(core, "history", fake_history)
        out = hist.fetch({"entities": "sensor.temp", "hours": 9999}, {}, ctx={})
    # 3-month upper bound — widened from 168h (1 week) when the new
    # ``window`` select grew long-range presets in v0.3.1.
    assert captured["hours"] == 2160
    vals = out["items"][0]["values"]
    assert len(vals) <= 80 and vals[0] == 0.0 and vals[-1] == 499.0


def test_composer_mounts_widget(client: FlaskClient) -> None:
    resp = client.get("/_test/render?plugin=ha_history&size=md")
    assert resp.status_code == 200
    assert 'data-plugin="ha_history"' in resp.get_data(as_text=True)
