"""Shared helpers for validating Companion API payloads against the
vendored OpenAPI 0.5.2 contract.

The spec + fixtures under ``contract/`` are a verbatim copy of
``charmmmz/tesserae-companion-ios`` ``Contracts/``, and stay that way:
values Tesserae serves ahead of the published contract live in
``contract_errata.py`` and are layered on here at load. Keeping the vendored
copy pristine means refreshing it is a copy rather than a merge.

Keeping them in-tree lets the server suite prove its live responses match
the same shapes the iOS client decodes, and flags drift the moment the
vendored copy is refreshed.

The ``$ref``/``nullable``/``allOf`` resolution mirrors the client repo's
``test_contract.py`` so both sides interpret the OpenAPI dialect
identically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .contract_errata import apply as _apply_errata

CONTRACT_DIR = Path(__file__).parent / "contract"
FIXTURES_DIR = CONTRACT_DIR / "Fixtures"
SPEC: dict[str, Any] = _apply_errata(
    yaml.safe_load((CONTRACT_DIR / "app-v1.openapi.yaml").read_text())
)


def _pointer(ref: str) -> Any:
    assert ref.startswith("#/")
    value: Any = SPEC
    for part in ref[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def json_schema(value: Any) -> Any:
    """Convert an OpenAPI 3.0 schema node into a plain JSON Schema draft
    ``jsonschema`` can validate: resolve ``$ref``, expand ``nullable`` to
    an ``anyOf`` with null, and drop OpenAPI-only annotations."""
    if isinstance(value, list):
        return [json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        resolved = json_schema(_pointer(value["$ref"]))
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        if siblings:
            return {"allOf": [resolved, json_schema(siblings)]}
        return resolved

    converted = {
        key: json_schema(item)
        for key, item in value.items()
        if key not in {"nullable", "discriminator", "example", "writeOnly", "readOnly"}
    }
    if value.get("nullable"):
        return {"anyOf": [converted, {"type": "null"}]}
    return converted


def schema_for(component: str) -> Any:
    """JSON Schema for a named ``components.schemas`` entry."""
    return json_schema(SPEC["components"]["schemas"][component])
