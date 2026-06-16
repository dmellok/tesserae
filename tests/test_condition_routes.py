"""Tests for ``POST /api/conditions/test`` — the Test-conditions
endpoint used by the schedule / rotation editor preview button.

We hit the real Flask app (no condition evaluator mocking) and supply
a stub HA state list via ``CONDITION_EVALUATOR`` so the endpoint
exercises the same code path the live editor button does.
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
