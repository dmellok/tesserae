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
    "capabilities-personal-data-retention.json": "Capabilities",
    "personal-data-reminders-never.json": "PersonalDataSnapshot",
    "personal-data-status-never.json": "PersonalDataSourceStatus",
    "capabilities.json": "Capabilities",
    "capabilities-previews.json": "Capabilities",
    "capabilities-extended.json": "Capabilities",
    "capabilities-framing.json": "Capabilities",
    "capabilities-personal-data.json": "Capabilities",
    "personal-data-reminders-fridge.json": "PersonalDataSnapshot",
    "personal-data-reminders.json": "PersonalDataSnapshot",
    "personal-data-reminders-empty.json": "PersonalDataSnapshot",
    "personal-data-put-response.json": "PersonalDataSourceStatus",
    "personal-data-reminders-put-response.json": "PersonalDataSourceStatus",
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
    "session-authorization.json": "CompanionSessionAuthorization",
    "capabilities-lineups.json": "Capabilities",
    "capabilities-lineup-authoring.json": "Capabilities",
    "capabilities-gallery.json": "Capabilities",
    "pair-response-lineups.json": "PairingResponse",
    "pair-response-gallery.json": "PairingResponse",
    "pair-response-personal-data.json": "PairingResponse",
    "devices-gallery-response.json": "DevicesResponse",
    "gallery-folders-response.json": "GalleryFoldersResponse",
    "gallery-folder-response.json": "GalleryFolderResponse",
    "gallery-external-folder-response.json": "GalleryFolderResponse",
    "gallery-folder-create-request.json": "GalleryFolderCreateRequest",
    "gallery-image-upload-response.json": "GalleryImageResponse",
    "lineups-response.json": "LineupsResponse",
    "lineup-response.json": "LineupResponse",
    "lineup-daily-resolved-response.json": "LineupResponse",
    "lineup-create-request.json": "LineupCreateRequest",
    "lineup-patch-request.json": "LineupPatchRequest",
    "lineup-action-request.json": "LineupActionRequest",
    "lineup-state-action-request.json": "LineupActionRequest",
    "personal-data-health-summary.json": "PersonalDataSnapshot",
    "personal-data-health-summary-partial.json": "PersonalDataSnapshot",
    "personal-data-health-summary-put-response.json": "PersonalDataSourceStatus",
    "capabilities-offline-albums.json": "Capabilities",
    "devices-offline-albums-response.json": "DevicesResponse",
    "session-authorization-offline-albums.json": "CompanionSessionAuthorization",
    "offline-album-draft.json": "OfflineAlbumDraft",
    "offline-album-put-request.json": "OfflineAlbumWriteRequest",
    "offline-album-response.json": "OfflineAlbumResponse",
    "offline-album-response-partial-observation.json": "OfflineAlbumResponse",
    "offline-album-preflight-response.json": "OfflineAlbumPreflightResponse",
    "error-offline-album-conflict.json": "ErrorResponse",
    "error-offline-album-unsupported-targets.json": "ErrorResponse",
    "job-lineup-action.json": "JobResponse",
    "error-forbidden.json": "ErrorResponse",
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
    assert SPEC["info"]["version"] == "0.14.0"
    assert set(SPEC["paths"]) == {
        "/api/app/v1",
        "/api/app/v1/pair",
        "/api/app/v1/device-pairings",
        "/api/app/v1/session",
        "/api/app/v1/personal-data/status",
        "/api/app/v1/personal-data/{source_id}",
        "/api/app/v1/devices",
        "/api/app/v1/devices/{device_id}/preview",
        "/api/app/v1/gallery/folders",
        "/api/app/v1/gallery/folders/{folder_id}",
        "/api/app/v1/gallery/folders/{folder_id}/images",
        "/api/app/v1/gallery/folders/{folder_id}/offline-album",
        "/api/app/v1/gallery/folders/{folder_id}/offline-album/preflight",
        "/api/app/v1/gallery/images/{image_id}/content",
        "/api/app/v1/gallery/images/{image_id}/thumbnail",
        "/api/app/v1/lineups",
        "/api/app/v1/lineups/{lineup_id}",
        "/api/app/v1/lineups/{lineup_id}/actions",
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
    firmware_code = SPEC["components"]["schemas"]["FirmwareDevicePairing"]["properties"]["code"]
    assert "writeOnly" not in firmware_code


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
    # 403 is a valid credential without the scope, distinct from the 401 that
    # sends a client off to re-pair when the remedy is an operator toggle.
    assert set(device["get"]["responses"]) == {"200", "304", "401", "403", "404"}
    assert set(dashboard["get"]["responses"]) == {
        "200",
        "202",
        "304",
        "400",
        "401",
        "403",
        "404",
    }
    assert set(history["get"]["responses"]) == {"200", "304", "401", "403", "404"}
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


def test_personal_data_capabilities_advertise_source_ids_directly() -> None:
    capabilities = json.loads((FIXTURES_DIR / "capabilities-personal-data.json").read_text())

    assert capabilities["personal_data"]["sources"] == [
        "reminders",
        "reminders.fridge",
        "health.summary",
    ]
    assert "personal_data_reminders" in capabilities["features"]
    assert "personal_data_health" in capabilities["features"]
    assert "personal_data_reminders_multi_list" not in capabilities["features"]


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

    # A focus point close enough to an edge that the crop would run off it
    # resolves to the same window as one clamped to the edge, rather than
    # being rejected or letting the crop leave the image.
    clamp = fixture["clamp_case"]
    clamped = resolve_framing_crop(
        source_w=normalized["width"],
        source_h=normalized["height"],
        target_w=target["width"],
        target_h=target["height"],
        focus_x=clamp["framing"]["focus_x"],
        focus_y=clamp["framing"]["focus_y"],
        zoom=clamp["framing"]["zoom"],
    )
    for key, expected in clamp["expected_crop"].items():
        assert clamped[{"width": "w", "height": "h"}.get(key, key)] == pytest.approx(expected)


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


def test_capability_support_reason_codes_are_the_ones_the_server_emits() -> None:
    """The contract names its reason codes in prose rather than an enum, so
    nothing else would catch us renaming one out from under the client."""
    from app.device_capability import (
        REASON_NO_HEARTBEAT,
        REASON_NOT_ADVERTISED,
        REASON_STALE_HEARTBEAT,
    )

    ours = {REASON_NOT_ADVERTISED, REASON_NO_HEARTBEAT, REASON_STALE_HEARTBEAT}
    described = SPEC["components"]["schemas"]["DeviceCapabilitySupport"]["properties"][
        "reason_code"
    ]["description"]
    for code in ours:
        assert code in described

    devices = json.loads((FIXTURES_DIR / "devices-gallery-response.json").read_text())["devices"]
    support = {
        device["capability_support"]["frame_cache"]["reason_code"]: device["capability_support"][
            "frame_cache"
        ]
        for device in devices
        if "reason_code" in device["capability_support"]["frame_cache"]
    }
    assert set(support) == ours

    # A stale beat is evidence that went out of date, not a display that
    # answered no, so it reads unknown and keeps the time it was observed.
    assert support[REASON_STALE_HEARTBEAT]["state"] == "unknown"
    assert support[REASON_STALE_HEARTBEAT]["observed_at"] is not None
    assert support[REASON_NO_HEARTBEAT]["state"] == "unknown"
    assert support[REASON_NO_HEARTBEAT]["observed_at"] is None
    assert support[REASON_NOT_ADVERTISED]["state"] == "unsupported"


def test_gallery_upload_and_stored_media_types_are_separate() -> None:
    """0.12 split the two: HEIC is a legal upload and can never be a stored
    file, and GIF/BMP are readable but not offered as upload sources."""
    from app.companion_gallery import (
        _NATIVE_CONTENT_TYPES,
        _RENDITION_CONTENT_TYPE,
        GALLERY_IMAGE_CONTENT_TYPES,
    )

    schemas = SPEC["components"]["schemas"]
    accepted = schemas["GalleryUploadContentType"]["enum"]
    stored = schemas["GalleryStoredContentType"]["enum"]

    assert list(GALLERY_IMAGE_CONTENT_TYPES) == accepted
    assert "image/heic" not in stored
    for served in {*_NATIVE_CONTENT_TYPES.values(), _RENDITION_CONTENT_TYPE}:
        assert served in stored
