"""Manifest validation with one compiled validator per schema.

``jsonschema.validate`` checks the schema itself against the JSON Schema
meta-schema on every call. Loading plugins, devices, renderers, transports
and the hardware catalog validates about 150 manifests against a handful of
schemas, so that meta-check ran ~150 times per app start and was most of
``create_app``'s time (and of every test that builds an app). The validator
is now built and checked once per distinct schema and reused.

Raises exactly what ``jsonschema.validate`` raises: ``SchemaError`` for a bad
schema, otherwise the best-matching ``ValidationError``.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import jsonschema
from jsonschema.exceptions import best_match
from jsonschema.protocols import Validator

_LOCK = threading.Lock()
_VALIDATORS: dict[str, Validator] = {}


def _validator_for(schema: dict[str, Any]) -> Validator:
    key = json.dumps(schema, sort_keys=True, default=str)
    cached = _VALIDATORS.get(key)
    if cached is not None:
        return cached
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    validator = cls(schema)
    with _LOCK:
        _VALIDATORS.setdefault(key, validator)
    return validator


def validate(instance: Any, schema: dict[str, Any]) -> None:
    """``jsonschema.validate(instance, schema)``, without re-checking the schema."""
    error = best_match(_validator_for(schema).iter_errors(instance))
    if error is not None:
        raise error
