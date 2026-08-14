"""Local additions to the vendored Companion contract.

``contract/app-v1.openapi.yaml`` is a verbatim copy of
``charmmmz/tesserae-companion-ios`` ``Contracts/``. It stays verbatim: every
value Tesserae serves that the published contract hasn't caught up with
lives here instead, and ``_schema.py`` layers these over the spec at load.

Editing the vendored file directly is what this replaces. Doing that turned
re-vendoring from a copy into a merge (a plain overwrite would silently drop
our additions and fail a dozen tests with no clue why), and scattered the
deviations as comments only findable by whoever happened to read that region.

Each entry carries the version that started serving it and why it exists, so
"what are we ahead of the contract on" is one file rather than a grep.

**These are meant to disappear.** ``test_contract_errata.py`` asserts each
entry is still missing from the vendored file, so the first re-vendor that
includes one fails the suite and names the entry to delete. The list can't
quietly rot the way the inline comments did.

Two kinds of entry. :class:`Erratum` appends values to an enum, which is
most drift: a new feature flag, a new scope, a new error code.
:class:`SchemaErratum` adds whole keys to a mapping node, for the case an
enum can't express. Every object in the contract is
``additionalProperties: false``, so a response field we serve ahead of the
published contract isn't a lax validation, it's a hard failure, and the
schema has to gain the property before our own responses can be checked
against it at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Erratum:
    """One value we serve that the vendored contract doesn't list yet.

    ``pointer`` is a slash-separated path into the spec, naming the enum the
    values belong to. ``since`` is the Tesserae version that started serving
    them; ``why`` is what to tell the contract's owner when upstreaming."""

    pointer: str
    values: tuple[str, ...]
    since: str
    why: str


@dataclass(frozen=True)
class SchemaErratum:
    """One or more keys we serve that a mapping node doesn't carry yet.

    ``pointer`` names the mapping (a schema's ``properties``, or
    ``components/schemas`` itself); ``patch`` holds the keys to add, each
    transcribed from the client's own contract rather than invented here,
    so a re-vendor replaces them with the identical published text."""

    pointer: str
    patch: dict[str, Any] = field(default_factory=dict)
    since: str = ""
    why: str = ""


ERRATA: tuple[Erratum, ...] = (
    Erratum(
        pointer="components/schemas/ErrorResponse/properties/error/properties/code/enum",
        values=("invalid_framing",),
        since="v0.230.0",
        why=(
            "The image_framing extension defines this code but the published "
            "enum omits it. Predates the Lineups work; flagged upstream in "
            "charmmmz/tesserae-companion-ios#5."
        ),
    ),
    Erratum(
        pointer="components/schemas/Capabilities/properties/features/items/enum",
        values=("lineups", "lineup_control", "lineup_authoring"),
        since="v0.287.0",
        why=(
            "Lineups read, control and authoring are advertised in the "
            "handshake. Discussed in discussion #203; the paths themselves are "
            "deliberately not described here, they belong to the client's own "
            "contract PR."
        ),
    ),
    Erratum(
        pointer="components/schemas/PairingResponse/properties/scopes/items/enum",
        values=("lineups:read", "lineups:control"),
        since="v0.287.0",
        why=(
            "Both ride along with the pairing role. lineups:write deliberately "
            "does not: it's a per-client toggle an operator grants in Settings "
            "(#207), so it never appears in a pairing response."
        ),
    ),
    Erratum(
        pointer="components/schemas/Job/properties/kind/enum",
        values=("lineup_action",),
        since="v0.291.0",
        why=(
            "Stepping or playing a Lineup, so a client's activity view can name "
            "explicit Lineup control rather than presenting it as an ordinary "
            "dashboard push. Requested in discussion #203."
        ),
    ),
    Erratum(
        pointer="components/schemas/ErrorResponse/properties/error/properties/code/enum",
        values=("precondition_failed", "forbidden"),
        since="v0.292.0",
        why=(
            "precondition_failed is a stale If-Match on a Lineup edit (#206). "
            "forbidden is a valid credential missing a scope, which used to "
            "answer 401 and sent clients off to re-pair when the remedy is an "
            "operator toggle (#207)."
        ),
    ),
    Erratum(
        pointer="components/schemas/Capabilities/properties/features/items/enum",
        values=("session_read",),
        since="v0.295.0",
        why=(
            "GET /api/app/v1/session, so a client can read the scopes its "
            "credential carries now rather than the ones pairing issued: an "
            "optional scope granted or withdrawn after pairing is invisible "
            "otherwise. Requested in discussion #203; the feature name is "
            "ours and the client is free to rename it in its contract PR."
        ),
    ),
    Erratum(
        pointer="components/schemas/Capabilities/properties/features/items/enum",
        values=("gallery",),
        since="v0.300.0",
        why=(
            "Native Gallery browse, folder create, upload and Send hand-off. "
            "Advertised only when the picture_gallery plugin is installed, "
            "since the surface is that plugin's storage. Agreed in discussion "
            "#225; defined by contract 0.11 in "
            "charmmmz/tesserae-companion-ios#8, not yet on that repo's main."
        ),
    ),
    Erratum(
        pointer="components/schemas/PairingResponse/properties/scopes/items/enum",
        values=("gallery:read", "gallery:write"),
        since="v0.300.0",
        why=(
            "Both ride along with the pairing role: putting a photo into the "
            "library is the same class of action as media:write, which paired "
            "clients already have. Destructive Gallery management is not, and "
            "gets its own optional scope when those routes exist (#225)."
        ),
    ),
    Erratum(
        pointer="components/schemas/ErrorResponse/properties/error/properties/code/enum",
        values=("resource_conflict",),
        since="v0.300.0",
        why=(
            "An upload aimed at an external (read-only) folder, or a folder "
            "create whose normalized name already exists. Distinct from "
            "idempotency_conflict, which is about the request being replayed "
            "rather than the resource refusing it. Contract 0.11, #225."
        ),
    ),
)


# Transcribed verbatim from contract 0.11 (charmmmz/tesserae-companion-ios#8).
# That PR is stacked behind #7 and hasn't reached the client repo's main, so
# the vendored copy stays at the published version and these carry the shapes
# until it does. On the next re-vendor every entry below should fail
# ``test_each_schema_erratum_is_still_needed`` and be deleted wholesale.
_GALLERY_SINCE = "v0.300.0"
_GALLERY_WHY = (
    "Native Gallery management, agreed in discussion #225 and defined by "
    "contract 0.11 in charmmmz/tesserae-companion-ios#8. Transcribed from that "
    "PR so our responses are validated against the shapes the client will "
    "decode, rather than against nothing until the stack merges."
)

_CAPABILITY_SUPPORT_WHY = (
    "Support computed per protocol capability rather than per model, so no "
    "client carries a list of which panels have an SD card and an unreported "
    "display is distinguishable from one that answered no. Requested in "
    "discussion #225; contract 0.11 in charmmmz/tesserae-companion-ios#8."
)

SCHEMA_ERRATA: tuple[SchemaErratum, ...] = (
    SchemaErratum(
        pointer="components/schemas/Device/properties",
        patch={
            "capability_support": {
                "description": (
                    "Server-computed support states keyed by protocol capability "
                    "rather than device model."
                ),
                "allOf": [{"$ref": "#/components/schemas/DeviceCapabilitySupportMap"}],
            }
        },
        since=_GALLERY_SINCE,
        why=_CAPABILITY_SUPPORT_WHY,
    ),
    SchemaErratum(
        pointer="components/schemas",
        patch={
            "DeviceCapabilitySupportMap": {
                "type": "object",
                "minProperties": 1,
                "additionalProperties": {"$ref": "#/components/schemas/DeviceCapabilitySupport"},
            },
            "DeviceCapabilitySupport": {
                "type": "object",
                "additionalProperties": False,
                "required": ["state"],
                "properties": {
                    "state": {"type": "string", "enum": ["supported", "unsupported", "unknown"]},
                    "reason_code": {"type": "string", "minLength": 1, "maxLength": 80},
                    "observed_at": {"type": "string", "format": "date-time", "nullable": True},
                },
            },
        },
        since=_GALLERY_SINCE,
        why=_CAPABILITY_SUPPORT_WHY,
    ),
    SchemaErratum(
        pointer="components/schemas/Limits/properties",
        patch={
            "gallery_upload_bytes": {"type": "integer", "minimum": 1},
            "gallery_image_content_types": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"$ref": "#/components/schemas/GalleryImageContentType"},
            },
            "gallery_upload_batch_size": {"type": "integer", "minimum": 1},
        },
        since=_GALLERY_SINCE,
        why=_GALLERY_WHY,
    ),
    SchemaErratum(
        pointer="components/schemas",
        patch={
            "GalleryImageContentType": {
                "type": "string",
                "enum": ["image/jpeg", "image/png", "image/heic", "image/heif", "image/webp"],
            },
            "GalleryFoldersResponse": {
                "type": "object",
                "additionalProperties": False,
                "required": ["folders"],
                "properties": {
                    "folders": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/GalleryFolder"},
                    }
                },
            },
            "GalleryFolderResponse": {
                "type": "object",
                "additionalProperties": False,
                "required": ["folder", "images"],
                "properties": {
                    "folder": {"$ref": "#/components/schemas/GalleryFolder"},
                    "images": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/GalleryImage"},
                    },
                },
            },
            "GalleryFolderCreateRequest": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name"],
                "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 80}},
            },
            "GalleryFolder": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "name",
                    "kind",
                    "writable",
                    "image_count",
                    "cover_thumbnail_url",
                ],
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": 256},
                    "name": {"type": "string", "minLength": 1, "maxLength": 80},
                    "kind": {"type": "string", "enum": ["internal", "external"]},
                    "writable": {"type": "boolean"},
                    "image_count": {"type": "integer", "minimum": 0},
                    "cover_thumbnail_url": {"type": "string", "nullable": True},
                },
            },
            "GalleryImageResponse": {
                "type": "object",
                "additionalProperties": False,
                "required": ["image"],
                "properties": {"image": {"$ref": "#/components/schemas/GalleryImage"}},
            },
            "GalleryImage": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "folder_id",
                    "name",
                    "content_type",
                    "bytes",
                    "width",
                    "height",
                    "etag",
                    "thumbnail_url",
                    "content_url",
                ],
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": 512},
                    "folder_id": {"type": "string", "minLength": 1, "maxLength": 256},
                    "name": {"type": "string", "minLength": 1, "maxLength": 255},
                    "content_type": {"$ref": "#/components/schemas/GalleryImageContentType"},
                    "bytes": {"type": "integer", "minimum": 1},
                    "width": {"type": "integer", "minimum": 1},
                    "height": {"type": "integer", "minimum": 1},
                    "etag": {"type": "string", "minLength": 1},
                    "thumbnail_url": {"type": "string"},
                    "content_url": {"type": "string"},
                    "created_at": {"type": "string", "format": "date-time", "nullable": True},
                },
            },
        },
        since=_GALLERY_SINCE,
        why=_GALLERY_WHY,
    ),
)


def _resolve(spec: dict[str, Any], pointer: str) -> Any:
    node: Any = spec
    for part in pointer.split("/"):
        node = node[part]
    return node


def missing_values(spec: dict[str, Any], erratum: Erratum) -> tuple[str, ...]:
    """The erratum's values the vendored spec doesn't already carry.

    Empty means the contract caught up and the entry should be deleted."""
    present = set(_resolve(spec, erratum.pointer))
    return tuple(v for v in erratum.values if v not in present)


def missing_keys(spec: dict[str, Any], erratum: SchemaErratum) -> tuple[str, ...]:
    """The schema erratum's keys the vendored spec doesn't already carry."""
    try:
        present = _resolve(spec, erratum.pointer)
    except KeyError:
        return tuple(erratum.patch)
    return tuple(k for k in erratum.patch if k not in present)


def apply(spec: dict[str, Any]) -> dict[str, Any]:
    """Layer every erratum onto a freshly-loaded spec, in place.

    Adds rather than replaces, and skips anything the spec already carries,
    so applying to a re-vendored file that caught up is a no-op instead of a
    duplicate or an overwrite of the published shape."""
    for erratum in ERRATA:
        target = _resolve(spec, erratum.pointer)
        for value in missing_values(spec, erratum):
            target.append(value)
    for schema_erratum in SCHEMA_ERRATA:
        node = _resolve(spec, schema_erratum.pointer)
        for key in missing_keys(spec, schema_erratum):
            node[key] = schema_erratum.patch[key]
    return spec
