"""ha_automation_history smoke: fetch() buckets logbook triggers into
1h/24h/7d counts, ranks most/least fired, dedupes the recent feed to one row
per automation, and flags stale automations — ha_core.get_states,
request_json, and is_configured monkeypatched (no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient


def _mods(app: Flask):
    reg = app.config["PLUGIN_REGISTRY"]
    return reg.get("ha_automation_history").server_module, reg.get("ha_core").server_module


def _iso(delta_s: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=delta_s)).isoformat()


def _states() -> list[dict]:
    """Two automations (one busy, one rare) + a non-automation entity that
    must be ignored. last_triggered sits inside the 7-day window so both get
    a logbook read."""
    return [
        {
            "entity_id": "automation.busy",
            "state": "on",
            "attributes": {"friendly_name": "Busy", "last_triggered": _iso(60)},
        },
        {
            "entity_id": "automation.rare",
            "state": "on",
            "attributes": {"friendly_name": "Rare", "last_triggered": _iso(3 * 86400)},
        },
        {
            "entity_id": "light.lamp",
            "state": "on",
            "attributes": {"friendly_name": "Lamp"},
        },
    ]


def _logbook_for(path: str) -> list[dict]:
    """Fake per-automation logbook keyed off the entity in the request path."""
    if "automation.busy" in path:
        # 2 in the last hour, +1 more in 24h, +1 more in 7d → c1=2, c24=3, c7=4
        return [
            {"when": _iso(60), "name": "Busy"},
            {"when": _iso(1800), "name": "Busy"},
            {"when": _iso(7200), "name": "Busy"},
            {"when": _iso(2 * 86400), "name": "Busy"},
            {"when": _iso(300), "name": "Busy", "state": "off"},  # enable/disable, ignored
        ]
    if "automation.rare" in path:
        return [{"when": _iso(3 * 86400), "name": "Rare"}]
    return []


def _wire(app: Flask, monkeypatch) -> None:
    _, core = _mods(app)
    monkeypatch.setattr(core, "is_configured", lambda: True)
    monkeypatch.setattr(core, "get_states", _states)
    monkeypatch.setattr(core, "request_json", lambda path, timeout=12: _logbook_for(path))


def test_error_when_not_configured(app: Flask, monkeypatch) -> None:
    mod, core = _mods(app)
    monkeypatch.setattr(core, "is_configured", lambda: False)
    with app.app_context():
        out = mod.fetch({}, {}, ctx={})
    assert "Home Assistant" in out["error"]


def test_empty_when_no_automations(app: Flask, monkeypatch) -> None:
    mod, core = _mods(app)
    monkeypatch.setattr(core, "is_configured", lambda: True)
    monkeypatch.setattr(core, "get_states", lambda: [_states()[2]])  # only the light
    with app.app_context():
        out = mod.fetch({}, {}, ctx={})
    assert out["empty"] is True


def test_counts_ranking_and_recent(app: Flask, monkeypatch) -> None:
    mod, _core = _mods(app)
    _wire(app, monkeypatch)
    with app.app_context():
        out = mod.fetch({}, {}, ctx={})

    assert out["count"] == 2
    assert out["tracked_all"] is True

    by_id = {a["eid"]: a for a in out["automations"]}
    busy = by_id["automation.busy"]
    assert (busy["c1"], busy["c24"], busy["c7"]) == (2, 3, 4)  # off-state entry ignored
    assert by_id["automation.rare"]["c7"] == 1

    assert out["total_1h"] == 2
    assert out["total_24h"] == 3
    assert out["total_7d"] == 5

    assert out["most_fired"]["eid"] == "automation.busy"
    assert out["most_fired"]["c7"] == 4
    assert out["least_fired"]["eid"] == "automation.rare"

    # Recent feed is one row per automation, most-recently-fired first.
    assert [r["eid"] for r in out["recent"]] == ["automation.busy", "automation.rare"]
    assert out["stale_count"] == 0


def test_stale_flag(app: Flask, monkeypatch) -> None:
    mod, _core = _mods(app)
    _wire(app, monkeypatch)
    with app.app_context():
        out = mod.fetch({"stale_hours": 1}, {}, ctx={})
    by_id = {a["eid"]: a for a in out["automations"]}
    assert by_id["automation.busy"]["stale"] is False  # fired 60s ago
    assert by_id["automation.rare"]["stale"] is True  # fired 3 days ago
    assert out["stale_count"] == 1


def test_composer_mounts_widget(client: FlaskClient) -> None:
    resp = client.get("/_test/render?plugin=ha_automation_history&size=md")
    assert resp.status_code == 200
    assert 'data-plugin="ha_automation_history"' in resp.get_data(as_text=True)
