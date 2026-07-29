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
    "capabilities-previews.json": "Capabilities",
    "capabilities-extended.json": "Capabilities",
    "pair-request.json": "PairingRequest",
    "pair-response.json": "PairingResponse",
    "devices-response.json": "DevicesResponse",
    "dashboards-response.json": "DashboardsResponse",
    "dashboard-push-request.json": "DashboardPushRequest",
    "image-push-request.json": "ImagePushRequest",
    "history-response.json": "HistoryResponse",
    "history-resend-request.json": "HistoryResendRequest",
    "job-accepted.json": "JobResponse",
    "job-published.json": "JobResponse",
    "job-quiet.json": "JobResponse",
    "job-failed.json": "JobResponse",
    "job-history-resend.json": "JobResponse",
    "error-response.json": "ErrorResponse",
}


def test_openapi_shape_and_operation_ids_are_stable() -> None:
    assert SPEC["openapi"] == "3.0.3"
    assert SPEC["info"]["version"] == "0.4.0"
    assert set(SPEC["paths"]) == {
        "/api/app/v1",
        "/api/app/v1/pair",
        "/api/app/v1/session",
        "/api/app/v1/devices",
        "/api/app/v1/devices/{device_id}/preview",
        "/api/app/v1/dashboards",
        "/api/app/v1/dashboards/{dashboard_id}/preview",
        "/api/app/v1/dashboards/{dashboard_id}/push",
        "/api/app/v1/images",
        "/api/app/v1/jobs/{job_id}",
        "/api/app/v1/history",
        "/api/app/v1/history/{history_id}/preview",
        "/api/app/v1/history/{history_id}/resend",
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


def test_all_write_operations_require_idempotency_key() -> None:
    paths = SPEC["paths"]
    write_operations = [
        paths["/api/app/v1/dashboards/{dashboard_id}/push"]["post"],
        paths["/api/app/v1/images"]["post"],
        paths["/api/app/v1/history/{history_id}/resend"]["post"],
    ]
    for operation in write_operations:
        refs = [parameter.get("$ref") for parameter in operation["parameters"]]
        assert "#/components/parameters/IdempotencyKey" in refs


def test_preview_endpoints_are_read_only_and_conditional() -> None:
    paths = SPEC["paths"]
    device = paths["/api/app/v1/devices/{device_id}/preview"]
    dashboard = paths["/api/app/v1/dashboards/{dashboard_id}/preview"]
    history = paths["/api/app/v1/history/{history_id}/preview"]

    assert set(device) == {"parameters", "get"}
    assert set(dashboard) == {"parameters", "get"}
    assert set(history) == {"parameters", "get"}
    assert set(device["get"]["responses"]) == {"200", "304", "401", "404"}
    assert set(dashboard["get"]["responses"]) == {
        "200",
        "202",
        "304",
        "400",
        "401",
        "404",
    }
    assert set(history["get"]["responses"]) == {"200", "304", "401", "404"}
    features = SPEC["components"]["schemas"]["Capabilities"]["properties"]["features"]["items"][
        "enum"
    ]
    assert "previews" in features

    base = json.loads((FIXTURES_DIR / "capabilities.json").read_text())
    extension = json.loads((FIXTURES_DIR / "capabilities-previews.json").read_text())
    assert "previews" not in base["features"]
    assert "previews" in extension["features"]


def test_extended_capabilities_advertise_history_and_all_image_fit_modes() -> None:
    base = json.loads((FIXTURES_DIR / "capabilities.json").read_text())
    extension = json.loads((FIXTURES_DIR / "capabilities-extended.json").read_text())

    assert "image_fit_modes" not in base["limits"]
    assert extension["limits"]["image_fit_modes"] == [
        "fit",
        "fill",
        "blur",
        "stretch",
        "center",
    ]
    assert "history" in extension["features"]
    assert SPEC["components"]["schemas"]["ImageFitMode"]["enum"] == [
        "fit",
        "fill",
        "blur",
        "stretch",
        "center",
    ]


def test_history_contract_keeps_composition_preview_and_resend_correlatable() -> None:
    history = json.loads((FIXTURES_DIR / "history-response.json").read_text())
    photo = history["items"][0]
    resend = json.loads((FIXTURES_DIR / "job-history-resend.json").read_text())["job"]

    assert photo["preview_available"] is True
    assert photo["fit"] == "blur"
    assert resend["kind"] == "history_resend"
    assert resend["result"]["history_event_ids"]

    preview_description = SPEC["paths"]["/api/app/v1/history/{history_id}/preview"]["get"][
        "responses"
    ]["200"]["description"]
    assert "not a device-final preview" in preview_description
