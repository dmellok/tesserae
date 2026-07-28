"""Guard the vendored Companion contract itself.

Mirrors the client repo's ``test_contract.py``: the shared fixtures must
validate against the component schemas, the operation ids stay unique,
and the job lifecycle stays separate from the business outcome. If the
vendored ``contract/`` copy is refreshed and drifts, this fails first,
before any server-shape test.
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from ._schema import FIXTURES_DIR, SPEC, schema_for

CASES = {
    "capabilities.json": "Capabilities",
    "pair-request.json": "PairingRequest",
    "pair-response.json": "PairingResponse",
    "devices-response.json": "DevicesResponse",
    "dashboards-response.json": "DashboardsResponse",
    "dashboard-push-request.json": "DashboardPushRequest",
    "image-push-request.json": "ImagePushRequest",
    "job-accepted.json": "JobResponse",
    "job-published.json": "JobResponse",
    "job-quiet.json": "JobResponse",
    "job-failed.json": "JobResponse",
    "error-response.json": "ErrorResponse",
}


def test_openapi_shape_and_operation_ids_are_stable() -> None:
    assert SPEC["openapi"] == "3.0.3"
    assert SPEC["info"]["version"] == "0.2.0"
    assert set(SPEC["paths"]) == {
        "/api/app/v1",
        "/api/app/v1/pair",
        "/api/app/v1/session",
        "/api/app/v1/devices",
        "/api/app/v1/dashboards",
        "/api/app/v1/dashboards/{dashboard_id}/push",
        "/api/app/v1/images",
        "/api/app/v1/jobs/{job_id}",
    }
    operation_ids = [
        operation["operationId"]
        for path in SPEC["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operation_ids) == len(set(operation_ids))


@pytest.mark.parametrize(("fixture_name", "schema_name"), CASES.items())
def test_fixtures_match_component_schemas(fixture_name: str, schema_name: str) -> None:
    fixture = json.loads((FIXTURES_DIR / fixture_name).read_text())
    jsonschema.validate(
        fixture,
        schema_for(schema_name),
        format_checker=jsonschema.FormatChecker(),
    )


def test_job_lifecycle_and_business_outcome_are_separate() -> None:
    accepted = json.loads((FIXTURES_DIR / "job-accepted.json").read_text())["job"]
    published = json.loads((FIXTURES_DIR / "job-published.json").read_text())["job"]
    quiet = json.loads((FIXTURES_DIR / "job-quiet.json").read_text())["job"]
    failed = json.loads((FIXTURES_DIR / "job-failed.json").read_text())["job"]

    assert accepted["status"] == "accepted"
    assert accepted["result"] is None
    assert published["status"] == "succeeded"
    assert published["result"]["status"] == "published"
    assert quiet["status"] == "succeeded"
    assert quiet["result"]["status"] == "quiet"
    assert failed["status"] == "failed"
    assert failed["error"]["code"]
