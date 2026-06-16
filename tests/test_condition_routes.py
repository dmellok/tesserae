"""Tests for the two condition-picker HTTP endpoints:

* ``POST /api/conditions/test`` — the Test-conditions button in the
  schedule / rotation editor.
* ``GET /api/conditions/ha-entities`` — the HA entity autocomplete
  feed for the picker's <datalist>.

We hit the real Flask app (no condition evaluator mocking) and supply
stub HA state via ``CONDITION_EVALUATOR`` (POST tests) or by
monkeypatching the ha_core plugin's ``get_states`` (GET tests) so each
endpoint exercises the same code path the live editor uses.
"""

from __future__ import annotations

from typing import Any

from flask import Flask

from app.scheduler_conditions import ConditionEvaluator


def _wire_evaluator(app: Flask, ha_states: list[dict[str, Any]]) -> None:
    """Install a freshly-built evaluator on the app, replacing whatever
    create_app put there. The fixture used by the rest of the suite
    creates a minimal evaluator with empty cache; tests that need real
    states call this to swap one in."""
    evaluator = ConditionEvaluator(
        ha_get_states=lambda: ha_states,
        timezone_provider=lambda: None,
        location_provider=lambda: (None, None),
    )
    evaluator.refresh_ha_states()
    app.config["CONDITION_EVALUATOR"] = evaluator


def test_test_endpoint_rejects_non_list_body(app: Flask) -> None:
    client = app.test_client()
    resp = client.post("/api/conditions/test", json={"conditions": "not a list"})
    assert resp.status_code == 400
    assert "JSON array" in resp.get_json()["error"]


def test_test_endpoint_rejects_invalid_condition(app: Flask) -> None:
    client = app.test_client()
    resp = client.post(
        "/api/conditions/test",
        json={
            "conditions": [
                {"source_kind": "ha_entity", "operator": "==", "value": "on"}
                # missing source_id
            ]
        },
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "invalid conditions"
    assert body["details"][0]["index"] == 0


def test_test_endpoint_returns_per_condition_results(app: Flask) -> None:
    _wire_evaluator(
        app,
        [
            {"entity_id": "binary_sensor.home", "state": "on"},
            {"entity_id": "sensor.lux", "state": "12"},
        ],
    )
    client = app.test_client()
    resp = client.post(
        "/api/conditions/test",
        json={
            "conditions": [
                {
                    "source_kind": "ha_entity",
                    "source_id": "binary_sensor.home",
                    "operator": "==",
                    "value": "on",
                },
                {
                    "source_kind": "ha_entity",
                    "source_id": "sensor.lux",
                    "operator": ">",
                    "value": 30,
                },
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["all_passed"] is False
    assert len(body["results"]) == 2
    assert body["results"][0]["passed"] is True
    assert body["results"][1]["passed"] is False
    assert "sensor.lux=12" in body["results"][1]["observed"]


# ---------------------------------------------------------------------------
# GET /api/conditions/ha-entities, autocomplete feed for the picker.
# ---------------------------------------------------------------------------


def test_ha_entities_returns_shaped_sorted_list(app: Flask, monkeypatch: Any) -> None:
    """Happy path: ha_core is installed and HA responds with a mix of
    entities (with/without friendly_name, with/without domain). The
    endpoint should return one dict per entity in label-sorted order
    with the five keys the picker's <datalist> reads."""
    plugin = app.config["PLUGIN_REGISTRY"].get("ha_core")
    assert plugin is not None and plugin.server_module is not None
    monkeypatch.setattr(
        plugin.server_module,
        "get_states",
        lambda: [
            {
                "entity_id": "binary_sensor.front_door",
                "state": "on",
                "attributes": {"friendly_name": "Front door"},
            },
            {"entity_id": "sensor.lux", "state": "12", "attributes": {}},
            {"entity_id": "bogus_no_dot", "state": "x", "attributes": {}},
        ],
    )

    resp = app.test_client().get("/api/conditions/ha-entities")
    assert resp.status_code == 200
    entities = resp.get_json()["entities"]
    assert len(entities) == 3

    # Sorted by label.lower() for stable client-side fuzzy filtering.
    labels = [e["label"] for e in entities]
    assert labels == sorted(labels, key=str.lower)

    by_id = {e["value"]: e for e in entities}
    door = by_id["binary_sensor.front_door"]
    assert door["label"] == "Front door (binary_sensor.front_door)"
    assert door["friendly"] == "Front door"
    assert door["domain"] == "binary_sensor"
    assert door["state"] == "on"

    lux = by_id["sensor.lux"]
    assert lux["label"] == "sensor.lux"  # no friendly_name => bare eid
    assert lux["friendly"] == ""
    assert lux["domain"] == "sensor"

    # Entity id without a dot still appears; domain falls back to "".
    bogus = by_id["bogus_no_dot"]
    assert bogus["domain"] == ""


def test_ha_entities_returns_empty_when_ha_core_missing(app: Flask, monkeypatch: Any) -> None:
    """If the host doesn't have ha_core installed (the common case for
    users who don't run Home Assistant) the endpoint must return an
    empty list so the picker degrades to a plain text input rather
    than 500-ing on form load."""
    registry = app.config["PLUGIN_REGISTRY"]
    original_get = registry.get
    monkeypatch.setattr(
        registry, "get", lambda pid: None if pid == "ha_core" else original_get(pid)
    )

    resp = app.test_client().get("/api/conditions/ha-entities")
    assert resp.status_code == 200
    assert resp.get_json() == {"entities": []}


def test_ha_entities_returns_empty_when_get_states_raises(app: Flask, monkeypatch: Any) -> None:
    """HA unreachable / token rejected / network blip — get_states()
    raises. The route swallows any exception and returns an empty list
    so the editor stays usable while HA is down."""
    plugin = app.config["PLUGIN_REGISTRY"].get("ha_core")
    assert plugin is not None and plugin.server_module is not None

    def boom() -> list[dict[str, Any]]:
        raise RuntimeError("HA unreachable")

    monkeypatch.setattr(plugin.server_module, "get_states", boom)

    resp = app.test_client().get("/api/conditions/ha-entities")
    assert resp.status_code == 200
    assert resp.get_json() == {"entities": []}


def test_ha_entities_returns_empty_when_registry_absent(app: Flask) -> None:
    """Belt-and-braces: if PLUGIN_REGISTRY isn't on app.config at all
    (an unusual app-factory wiring), the route must still respond."""
    app.config.pop("PLUGIN_REGISTRY", None)
    resp = app.test_client().get("/api/conditions/ha-entities")
    assert resp.status_code == 200
    assert resp.get_json() == {"entities": []}
