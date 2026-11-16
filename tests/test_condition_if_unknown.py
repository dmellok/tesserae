"""``if_unknown="fail"``: an unreadable Home Assistant entity counts as not met,
so an interrupting lineup (a print page) stays off while the printer can't be read."""

from __future__ import annotations

from typing import Any

from app.scheduler_conditions import ConditionEvaluator
from app.state.conditions import Condition


def _cond(**kw: Any) -> Condition:
    base = {
        "source_kind": "ha_entity",
        "source_id": "sensor.activity",
        "operator": "==",
        "value": "running",
    }
    return Condition(**{**base, **kw})


def _evaluator(states: Any) -> ConditionEvaluator:
    ev = ConditionEvaluator(ha_get_states=states)
    ev.refresh_ha_states()
    return ev


RUNNING = [{"entity_id": "sensor.activity", "state": "running"}]


def test_default_still_fails_open_on_a_missing_entity() -> None:
    ev = _evaluator(lambda: [{"entity_id": "sensor.other", "state": "on"}])
    assert ev.all_pass([_cond()]) is True


def test_fail_closed_on_missing_entity_and_unavailable_state() -> None:
    ev = _evaluator(lambda: [{"entity_id": "sensor.other", "state": "on"}])
    assert ev.all_pass([_cond(if_unknown="fail")]) is False
    ev = _evaluator(lambda: [{"entity_id": "sensor.activity", "state": "unavailable"}])
    res = ev.evaluate([_cond(if_unknown="fail", operator="!=", value="idle")])[0]
    assert res.passed is False and "unavailable" in res.observed


def test_fail_closed_when_home_assistant_stops_answering() -> None:
    calls = {"n": 0}

    def states() -> list[dict[str, str]]:
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("HA down")
        return RUNNING

    ev = _evaluator(states)
    assert ev.all_pass([_cond(if_unknown="fail")]) is True
    ev.refresh_ha_states()  # fails: the cache is now a stale snapshot
    assert ev.all_pass([_cond(if_unknown="fail")]) is False
    assert ev.all_pass([_cond()]) is True, "the default keeps reading the last snapshot"


def test_fail_closed_before_home_assistant_was_ever_read() -> None:
    assert ConditionEvaluator(ha_get_states=None).all_pass([_cond(if_unknown="fail")]) is False


def test_if_unknown_round_trips_and_defaults_to_pass() -> None:
    assert _cond().if_unknown == "pass"
    assert Condition.model_validate(_cond(if_unknown="fail").model_dump()).if_unknown == "fail"


def test_fail_closed_on_empty_refresh_and_recovers():
    snapshots = iter([RUNNING, [], RUNNING])
    ev = _evaluator(lambda: next(snapshots))
    assert ev.all_pass([_cond(if_unknown="fail")])
    assert not ev.refresh_ha_states()
    assert not ev.all_pass([_cond(if_unknown="fail")])
    assert ev.all_pass([_cond()])
    assert ev.refresh_ha_states()
    assert ev.all_pass([_cond(if_unknown="fail")])


def test_unreadable_states_fail_even_for_inequality():
    for state in ("unknown", "unavailable", ""):
        ev = _evaluator(lambda state=state: [{"entity_id": "sensor.activity", "state": state}])
        assert not ev.all_pass([_cond(if_unknown="fail", operator="!=", value="idle")])


def test_test_endpoint_accepts_fail_closed_condition(app):
    response = app.test_client().post(
        "/api/conditions/test", json={"conditions": [_cond(if_unknown="fail").model_dump()]}
    )
    assert response.status_code == 200
    assert response.json["all_passed"] is False
