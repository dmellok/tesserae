"""Guard the vendored Companion contract itself.

Mirrors the client repo's ``test_contract.py``: the shared fixtures must
validate against the component schemas, the operation ids stay unique,
and the job lifecycle stays separate from the business outcome. If the
vendored ``contract/`` copy is refreshed and drifts, this fails first,
before any server-shape test.
"""

from __future__ import annotations

import base64
import json

import jsonschema
import pytest

from ._schema import FIXTURES_DIR, SPEC, schema_for

CASES = {
    "capabilities.json": "Capabilities",
    "capabilities-previews.json": "Capabilities",
    "capabilities-extended.json": "Capabilities",
    "capabilities-framing.json": "Capabilities",
    "capabilities-personal-data.json": "Capabilities",
    "personal-data-reminders-fridge.json": "PersonalDataSnapshot",
    "personal-data-put-response.json": "PersonalDataSourceStatus",
    "personal-data-status.json": "PersonalDataStatusResponse",
    "pair-request.json": "PairingRequest",
    "pair-response.json": "PairingResponse",
    "devices-response.json": "DevicesResponse",
    "dashboards-response.json": "DashboardsResponse",
    "dashboard-push-request.json": "DashboardPushRequest",
    "image-push-request.json": "ImagePushRequest",
    "image-push-request-basic.json": "ImagePushRequest",
    "image-url-push-request.json": "ImageURLPushRequest",
    "webpage-push-request.json": "WebpagePushRequest",
    "history-response.json": "HistoryResponse",
    "history-link-response.json": "HistoryResponse",
    "history-resend-request.json": "HistoryResendRequest",
    "job-accepted.json": "JobResponse",
    "job-published.json": "JobResponse",
    "job-quiet.json": "JobResponse",
    "job-failed.json": "JobResponse",
    "job-history-resend.json": "JobResponse",
    "job-image-url-published.json": "JobResponse",
    "job-webpage-published.json": "JobResponse",
    "job-webpage-blocked.json": "JobResponse",
    "error-response.json": "ErrorResponse",
}


def test_openapi_shape_and_operation_ids_are_stable() -> None:
    assert SPEC["openapi"] == "3.0.3"
    assert SPEC["info"]["version"] == "0.7.0"
    assert set(SPEC["paths"]) == {
        "/api/app/v1",
        "/api/app/v1/pair",
        "/api/app/v1/session",
        "/api/app/v1/personal-data/status",
        "/api/app/v1/personal-data/{source_id}",
        "/api/app/v1/devices",
        "/api/app/v1/devices/{device_id}/preview",
        "/api/app/v1/dashboards",
        "/api/app/v1/dashboards/{dashboard_id}/preview",
        "/api/app/v1/dashboards/{dashboard_id}/push",
        "/api/app/v1/images",
        "/api/app/v1/image-urls",
        "/api/app/v1/webpages",
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


def test_pending_render_identifies_an_exact_retrievable_preview() -> None:
    devices = json.loads((FIXTURES_DIR / "devices-response.json").read_text())["devices"]
    pending = next(device for device in devices if device["has_pending_render"])

    assert pending["pending_render"] == {
        "revision": "a1b2c3d4e5f67890",
        "rendered_at": "2026-07-28T08:00:00Z",
        "preview_url": ("/api/app/v1/devices/picpak-kitchen/preview?revision=a1b2c3d4e5f67890"),
    }

    revision_parameter = SPEC["paths"]["/api/app/v1/devices/{device_id}/preview"]["get"][
        "parameters"
    ][1]
    assert revision_parameter["name"] == "revision"
    assert revision_parameter["required"] is False


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
    assert "image_url_push" in extension["features"]
    assert "webpage_push" in extension["features"]
    assert SPEC["components"]["schemas"]["ImageFitMode"]["enum"] == [
        "fit",
        "fill",
        "blur",
        "stretch",
        "center",
    ]


def test_image_framing_is_independently_gated_and_fill_only() -> None:
    base = json.loads((FIXTURES_DIR / "capabilities-extended.json").read_text())
    framing_capabilities = json.loads((FIXTURES_DIR / "capabilities-framing.json").read_text())
    basic = json.loads((FIXTURES_DIR / "image-push-request-basic.json").read_text())
    framed = json.loads((FIXTURES_DIR / "image-push-request.json").read_text())

    assert "image_framing" not in base["features"]
    assert "image_framing_max_zoom" not in base["limits"]
    assert "image_framing" in framing_capabilities["features"]
    assert framing_capabilities["limits"]["image_framing_max_zoom"] == 4

    assert "framing" not in basic
    assert framed["fit"] == "fill"
    assert framed["framing"] == {"focus_x": 0.62, "focus_y": 0.38, "zoom": 1.35}

    schema = SPEC["components"]["schemas"]["ImageFraming"]
    assert schema["required"] == ["focus_x", "focus_y", "zoom"]
    assert schema["properties"]["focus_x"]["minimum"] == 0
    assert schema["properties"]["focus_x"]["maximum"] == 1
    assert schema["properties"]["focus_y"]["minimum"] == 0
    assert schema["properties"]["focus_y"]["maximum"] == 1
    assert schema["properties"]["zoom"]["minimum"] == 1


def test_image_framing_fixture_matches_the_server_resolver() -> None:
    """The shared rotated-EXIF fixture drives the REAL server resolver.

    The client repo re-derives the formula in its own tests; here the same
    fixture must fall out of ``app.quantizer.resolve_framing_crop`` against
    the orientation-normalized dimensions, and must NOT match when resolved
    against the raw (stored, sideways) pixel buffer.
    """
    from PIL import Image, ImageOps

    from app.quantizer import resolve_framing_crop

    fixture = json.loads((FIXTURES_DIR / "image-framing-exif-rotate-90.json").read_text())
    image_bytes = base64.b64decode(fixture["jpeg_base64"], validate=True)
    raw = fixture["raw_source"]
    normalized = fixture["normalized_source"]
    target = fixture["target"]
    framing = fixture["framing"]

    from io import BytesIO

    with Image.open(BytesIO(image_bytes)) as img:
        assert (img.width, img.height) == (raw["width"], raw["height"])
        assert img.getexif().get(0x0112) == raw["exif_orientation"]
        upright = ImageOps.exif_transpose(img)
    assert (upright.width, upright.height) == (normalized["width"], normalized["height"])

    def resolve(source: dict[str, int]) -> dict[str, float]:
        crop = resolve_framing_crop(
            source_w=source["width"],
            source_h=source["height"],
            target_w=target["width"],
            target_h=target["height"],
            focus_x=framing["focus_x"],
            focus_y=framing["focus_y"],
            zoom=framing["zoom"],
        )
        return {"x": crop["x"], "y": crop["y"], "width": crop["w"], "height": crop["h"]}

    resolved = resolve(normalized)
    for key, expected in fixture["expected_crop"].items():
        assert resolved[key] == pytest.approx(expected)
    assert resolve(raw)["x"] != pytest.approx(resolved["x"])


def test_history_contract_keeps_composition_preview_and_resend_correlatable() -> None:
    history = json.loads((FIXTURES_DIR / "history-response.json").read_text())
    photo = history["items"][0]
    resend = json.loads((FIXTURES_DIR / "job-history-resend.json").read_text())["job"]

    assert photo["preview_available"] is True
    assert photo["fit"] == "fill"
    assert photo["framing"] == {"focus_x": 0.62, "focus_y": 0.38, "zoom": 1.35}
    assert resend["kind"] == "history_resend"
    assert resend["result"]["history_event_ids"]

    preview_description = SPEC["paths"]["/api/app/v1/history/{history_id}/preview"]["get"][
        "responses"
    ]["200"]["description"]
    assert "not a device-final preview" in preview_description
