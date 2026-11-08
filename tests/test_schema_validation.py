"""``app.schema_validation.validate`` behaves like ``jsonschema.validate``,
but checks each schema against the meta-schema once instead of per call."""

from __future__ import annotations

import jsonschema
import pytest

from app import schema_validation

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["id"],
    "properties": {"id": {"type": "string"}, "n": {"type": "integer", "minimum": 0}},
}


def test_valid_instance_passes() -> None:
    schema_validation.validate({"id": "x", "n": 1}, SCHEMA)


@pytest.mark.parametrize("instance", [{}, {"id": 3}, {"id": "x", "n": -1}])
def test_raises_the_same_error_as_jsonschema(instance: dict) -> None:
    with pytest.raises(jsonschema.ValidationError) as ours:
        schema_validation.validate(instance, SCHEMA)
    with pytest.raises(jsonschema.ValidationError) as theirs:
        jsonschema.validate(instance, SCHEMA)
    assert ours.value.message == theirs.value.message
    assert list(ours.value.absolute_path) == list(theirs.value.absolute_path)


def test_a_bad_schema_still_raises_schema_error() -> None:
    with pytest.raises(jsonschema.SchemaError):
        schema_validation.validate({}, {"type": 12})


def test_the_meta_schema_check_runs_once_per_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    schema = {**SCHEMA, "title": "once"}
    cls = jsonschema.validators.validator_for(schema)
    calls = []
    real = cls.check_schema
    monkeypatch.setattr(cls, "check_schema", classmethod(lambda c, s: calls.append(1) or real(s)))
    for _ in range(5):
        schema_validation.validate({"id": "x"}, dict(schema))
    assert len(calls) == 1
