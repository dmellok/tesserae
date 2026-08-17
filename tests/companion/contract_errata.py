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
        pointer="components/schemas/Capabilities/properties/features/items/enum",
        values=("device_timeline",),
        since="v0.311.0",
        why=(
            "Per-display projection of the next visible updates, agreed in "
            "discussion #232. Advertised only where a scheduler is running, "
            "since the projection replays that engine's own gates."
        ),
    ),
)

SCHEMA_ERRATA: tuple[SchemaErratum, ...] = (
    SchemaErratum(
        pointer="components/schemas/Limits/properties",
        patch={
            "device_timeline_max_hours": {
                "description": (
                    "Largest hours window GET /devices/{device_id}/upcoming "
                    "accepts. Required whenever device_timeline is advertised."
                ),
                "type": "integer",
                "minimum": 1,
            },
            "device_timeline_max_events": {
                "description": (
                    "Largest limit GET /devices/{device_id}/upcoming accepts. "
                    "Required whenever device_timeline is advertised."
                ),
                "type": "integer",
                "minimum": 1,
            },
        },
        since="v0.311.0",
        why="Query bounds for the #232 timeline, advertised rather than hard-coded.",
    ),
    SchemaErratum(
        pointer="components/schemas",
        patch={
            "DeviceUpcomingEvent": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "scheduled_at",
                    "cause",
                    "effect",
                    "certainty",
                    "lineup_id",
                    "lineup_name",
                    "dashboard_id",
                    "dashboard_name",
                ],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "scheduled_at": {"type": "string", "format": "date-time"},
                    "cause": {
                        "type": "string",
                        "enum": [
                            "daily",
                            "interval",
                            "cycle",
                            "home_return",
                            "dashboard_refresh",
                            "widget_refresh",
                        ],
                    },
                    "effect": {"type": "string", "enum": ["change_screen", "refresh_screen"]},
                    "certainty": {
                        "type": "string",
                        "enum": ["scheduled", "conditional", "estimated"],
                    },
                    "lineup_id": {"type": "string", "nullable": True},
                    "lineup_name": {"type": "string", "nullable": True},
                    "dashboard_id": {"type": "string", "nullable": True},
                    "dashboard_name": {"type": "string", "nullable": True},
                },
            },
            "DeviceUpcomingResponse": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "device_id",
                    "generated_at",
                    "timezone",
                    "through_at",
                    "current_frame_at",
                    "events",
                ],
                "properties": {
                    "device_id": {"type": "string", "minLength": 1},
                    "generated_at": {"type": "string", "format": "date-time"},
                    "timezone": {"type": "string", "minLength": 1},
                    "through_at": {"type": "string", "format": "date-time"},
                    "current_frame_at": {
                        "type": "string",
                        "format": "date-time",
                        "nullable": True,
                    },
                    "events": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/DeviceUpcomingEvent"},
                    },
                },
            },
        },
        since="v0.311.0",
        why=(
            "Response shapes for GET /devices/{device_id}/upcoming, transcribed "
            "from the proposal in discussion #232 so our own responses can be "
            "validated before the client publishes the endpoint."
        ),
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
