"""Tiny API: evaluate a condition list against the live system.

POST /api/conditions/test with a JSON body ``{"conditions": [...]}``;
the response carries the same shape ``ConditionEvaluator.evaluate``
produces so the schedule + rotation editors can wire a "Test
conditions" button without needing the saved record to exist yet.

This endpoint is the only HTTP entry into the evaluator; everything
else uses the in-process evaluator from ``app.config["CONDITION_EVALUATOR"]``.

mypy --strict applies, see pyproject.toml.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, Flask, current_app, jsonify, request
from flask.wrappers import Response
from pydantic import ValidationError

from app.scheduler_conditions import ConditionEvaluator
from app.state.conditions import Condition

bp = Blueprint("conditions", __name__, url_prefix="/api/conditions")

logger = logging.getLogger(__name__)


def _evaluator() -> ConditionEvaluator | None:
    evaluator = current_app.config.get("CONDITION_EVALUATOR")
    return evaluator if isinstance(evaluator, ConditionEvaluator) else None


@bp.post("/test")
def test_conditions() -> tuple[Response, int] | Response:
    """Evaluate the supplied conditions against the live system. The
    body should be JSON ``{"conditions": [{...}, ...]}`` matching the
    same shape stored on Schedule / RotationStep. The evaluator
    refreshes the HA state cache once on entry so the editor's "Test"
    button reflects what the scheduler would see on the next tick.
    """
    payload = request.get_json(silent=True) or {}
    raw = payload.get("conditions")
    if not isinstance(raw, list):
        return jsonify({"error": "conditions must be a JSON array"}), 400

    parsed: list[Condition] = []
    errors: list[dict[str, Any]] = []
    for idx, entry in enumerate(raw):
        try:
            parsed.append(Condition.model_validate(entry))
        except ValidationError as err:
            # err.errors() can include exception objects in ``ctx`` that
            # Flask's JSON encoder can't serialise. Reduce to scalars
            # the editor can show inline without help from a debugger.
            details = [
                {
                    "type": e.get("type"),
                    "loc": [str(part) for part in e.get("loc", ())],
                    "msg": e.get("msg"),
                }
                for e in err.errors()
            ]
            errors.append({"index": idx, "errors": details})
    if errors:
        return jsonify({"error": "invalid conditions", "details": errors}), 400

    evaluator = _evaluator()
    if evaluator is None:
        return jsonify({"error": "condition evaluator not configured"}), 503

    evaluator.refresh_ha_states()
    results = evaluator.evaluate(parsed)
    return jsonify(
        {
            "all_passed": all(r.passed for r in results),
            "results": [
                {
                    "passed": r.passed,
                    "observed": r.observed,
                    "reason": r.reason,
                    "source_kind": r.condition.source_kind,
                    "source_id": r.condition.source_id,
                    "operator": r.condition.operator,
                }
                for r in results
            ],
        }
    )


def register(app: Flask) -> None:
    app.register_blueprint(bp)
