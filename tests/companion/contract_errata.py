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
"""

from __future__ import annotations

from dataclasses import dataclass
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


def apply(spec: dict[str, Any]) -> dict[str, Any]:
    """Layer every erratum onto a freshly-loaded spec, in place.

    Appends rather than replaces, and skips a value the spec already lists,
    so applying to a re-vendored file that caught up is a no-op instead of a
    duplicate."""
    for erratum in ERRATA:
        target = _resolve(spec, erratum.pointer)
        for value in missing_values(spec, erratum):
            target.append(value)
    return spec
