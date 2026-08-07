"""ha_history smoke: fetch() coerces series to floats, downsamples, adds a
trend, for single + multiple entities, get_states + history monkeypatched."""

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


def test_number_format_fixes_current_min_max(app: Flask, monkeypatch) -> None:
    """#111: number_format fixes decimals on the current / min / max
    labels; blank keeps the round(2):g default."""
    hist, core = _mods(app)
    series = [{"state": str(v)} for v in [10, 11, 13, 16, 19]]
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        monkeypatch.setattr(core, "history", lambda eid, hours=24: series)
        out = hist.fetch(
            {"entities": "sensor.temp", "hours": 12, "number_format": "0.0"}, {}, ctx={}
        )
    it = out["items"][0]
    assert it["current"] == "21.4"  # raw state 21.4 formatted to one decimal
    assert it["min"] == "10.0" and it["max"] == "19.0"


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
    # 3-month upper bound, widened from 168h (1 week) when the new
    # ``window`` select grew long-range presets in v0.3.1.
    assert captured["hours"] == 2160
    vals = out["items"][0]["values"]
    assert len(vals) <= 80 and vals[0] == 0.0 and vals[-1] == 499.0


def _stamped(values: list[float], start: str, step_minutes: int) -> list[dict[str, str]]:
    """History samples with real UTC timestamps, ``step_minutes`` apart."""
    from datetime import datetime, timedelta

    t0 = datetime.fromisoformat(start)
    return [
        {
            "state": str(v),
            "last_changed": (t0 + timedelta(minutes=i * step_minutes)).isoformat(),
        }
        for i, v in enumerate(values)
    ]


def test_axis_labels_are_clock_times_within_a_day(app: Flask, monkeypatch) -> None:
    """#196: the x-axis was labelled with sample ordinals (1, 10, 19, …),
    which say nothing about when a reading was taken. A day-or-less window
    labels in clock time, one label per plotted point."""
    hist, core = _mods(app)
    samples = _stamped([10.0, 11.0, 12.0, 13.0], "2026-08-07T06:00:00+00:00", 60)
    with app.app_context():
        app.config["SETTINGS_STORE"].patch_section("app", {"timezone": "UTC"})
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        monkeypatch.setattr(core, "history", lambda eid, hours=24: samples)
        item = hist.fetch({"entities": "sensor.temp", "hours": 12}, {}, ctx={})["items"][0]

    assert item["times"] == ["06:00", "07:00", "08:00", "09:00"]
    assert len(item["times"]) == len(item["values"])


def test_axis_labels_coarsen_with_the_window(app: Flask, monkeypatch) -> None:
    """A multi-day window can't read as clock time alone: past three days
    the axis carries the date instead."""
    hist, core = _mods(app)
    samples = _stamped([1.0, 2.0, 3.0], "2026-08-05T09:00:00+00:00", 60 * 24)
    with app.app_context():
        app.config["SETTINGS_STORE"].patch_section("app", {"timezone": "UTC"})
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        monkeypatch.setattr(core, "history", lambda eid, hours=24: samples)
        item = hist.fetch({"entities": "sensor.temp", "window": "168"}, {}, ctx={})["items"][0]

    assert item["times"] == ["5 Aug", "6 Aug", "7 Aug"]


def test_axis_labels_stay_aligned_after_downsampling(app: Flask, monkeypatch) -> None:
    """Downsampling picks indexes, so each surviving value keeps its own
    timestamp: a label that described a discarded sample would be worse
    than no label at all."""
    hist, core = _mods(app)
    samples = _stamped([float(i) for i in range(500)], "2026-08-07T00:00:00+00:00", 1)
    with app.app_context():
        app.config["SETTINGS_STORE"].patch_section("app", {"timezone": "UTC"})
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        monkeypatch.setattr(core, "history", lambda eid, hours=24: samples)
        item = hist.fetch({"entities": "sensor.temp", "hours": 12}, {}, ctx={})["items"][0]

    assert len(item["times"]) == len(item["values"]) <= 80
    # Sample n was taken n minutes after midnight, so each kept value
    # names the minute it was recorded.
    for value, label in zip(item["values"], item["times"], strict=True):
        minutes = int(value)
        assert label == f"{minutes // 60:02d}:{minutes % 60:02d}"


def test_axis_labels_absent_without_timestamps(app: Flask, monkeypatch) -> None:
    """History with no usable timestamps reports no labels rather than
    inventing them; the client falls back to ordinals."""
    hist, core = _mods(app)
    with app.app_context():
        monkeypatch.setattr(core, "get_states", lambda: _STATES)
        monkeypatch.setattr(
            core, "history", lambda eid, hours=24: [{"state": str(v)} for v in (1, 2, 3)]
        )
        item = hist.fetch({"entities": "sensor.temp", "hours": 12}, {}, ctx={})["items"][0]

    assert item["times"] == []


def test_composer_mounts_widget(client: FlaskClient) -> None:
    resp = client.get("/_test/render?plugin=ha_history&size=md")
    assert resp.status_code == 200
    assert 'data-plugin="ha_history"' in resp.get_data(as_text=True)
