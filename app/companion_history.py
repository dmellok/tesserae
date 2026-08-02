"""Read-only adapter from the internal EventLog to Companion History.

The admin History page and the Companion API intentionally read the same
canonical ``type="push"`` rows.  This module keeps the wire shaping and
retained-composition lookup independent from Flask so routes can stay small
and the retention behaviour is testable without an application context.

History rows outlive render artifacts.  Once a composition PNG has been
pruned, the row remains visible but both ``preview_available`` and
``resendable`` become false.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.state.event_log import EventLog, EventRow

DEFAULT_HISTORY_LIMIT = 30
MAX_HISTORY_LIMIT = 100

_DIGEST_RE = re.compile(r"^[0-9a-fA-F]{16,64}$")
_IMAGE_FIT_MODES = frozenset(("fit", "fill", "blur", "stretch", "center"))

HistoryLabelResolver = Callable[[EventRow], str]


class InvalidHistoryCursor(ValueError):
    """Raised when a client supplies a cursor outside this server's format."""


@dataclass(frozen=True)
class RetainedComposition:
    """A safe, retained composition artifact for a canonical History row."""

    path: Path
    etag: str


def parse_history_id(raw: str | int) -> int:
    """Parse the current EventLog-backed opaque id.

    The public contract treats ids as opaque strings so a future server can
    change its storage without changing clients.  This implementation uses
    positive SQLite row ids and deliberately rejects signs, whitespace and
    alternate numeric spellings.
    """

    value = str(raw)
    if not value.isascii() or not value.isdecimal() or value.startswith("0"):
        raise InvalidHistoryCursor("history id must be a positive decimal row id")
    event_id = int(value)
    if event_id <= 0:
        raise InvalidHistoryCursor("history id must be a positive decimal row id")
    return event_id


def list_history(
    event_log: EventLog,
    renders_dir: Path,
    *,
    before_id: str | None = None,
    limit: int = DEFAULT_HISTORY_LIMIT,
    label_resolver: HistoryLabelResolver | None = None,
) -> dict[str, Any]:
    """Return one contract-shaped page of canonical push History.

    The response page is always clamped to 1...100.  One extra row determines
    whether a next cursor exists without counting or scanning the full log.
    """

    cursor = parse_history_id(before_id) if before_id is not None else None
    page_limit = max(1, min(int(limit), MAX_HISTORY_LIMIT))

    rows = event_log.list(type="push", before_id=cursor, limit=page_limit + 1)

    has_more = len(rows) > page_limit
    selected = rows[:page_limit]
    items = [
        history_item(
            row,
            renders_dir,
            label_resolver=label_resolver,
        )
        for row in selected
    ]
    next_before_id = str(selected[-1].id) if has_more and selected else None
    return {"items": items, "next_before_id": next_before_id}


def history_item(
    row: EventRow,
    renders_dir: Path,
    *,
    label_resolver: HistoryLabelResolver | None = None,
) -> dict[str, Any]:
    """Shape one canonical push row for the Companion 0.4 contract."""

    preview = retained_composition_for_row(row, renders_dir)
    resend = retained_resend_composition_for_row(row, renders_dir)
    fit = _fit_mode(row.extra)
    label = label_resolver(row) if label_resolver is not None else row.target
    if not isinstance(label, str) or not label:
        label = row.target or row.source

    return {
        "id": str(row.id),
        "created_at": _utc_timestamp(row.timestamp),
        "source": row.source,
        "label": label,
        "device_ids": _device_ids(row.extra),
        "status": row.status,
        "duration_seconds": max(0.0, row.duration_s),
        "error": row.error,
        "preview_available": preview is not None,
        "resendable": resend is not None,
        "fit": fit,
        "framing": _framing(row.extra),
    }


def retained_composition_for_history(
    event_log: EventLog,
    renders_dir: Path,
    history_id: str | int,
) -> RetainedComposition | None:
    """Locate a canonical row's preview PNG, or return None when unavailable."""

    try:
        event_id = parse_history_id(history_id)
    except InvalidHistoryCursor:
        return None
    row = event_log.get(event_id)
    if row is None or row.type != "push":
        return None
    return retained_composition_for_row(row, renders_dir)


def retained_composition_for_row(
    row: EventRow,
    renders_dir: Path,
) -> RetainedComposition | None:
    """Locate the preview composition for a row.

    Normal pushes keep the composition digest in ``digest``.  Preview-only
    button/fetch rows intentionally leave that field empty and snapshot a
    ``composition_digest`` in ``extra``; those may be viewed but not resent.
    """

    digest = _nonempty_string(row.digest)
    if digest is None:
        digest = _nonempty_string(row.extra.get("composition_digest"))
    return retained_composition(renders_dir, digest)


def retained_resend_composition_for_row(
    row: EventRow,
    renders_dir: Path,
) -> RetainedComposition | None:
    """Locate the artifact that makes this row resendable.

    Only the top-level digest carries resend/source identity.  A preview-only
    ``extra.composition_digest`` must never silently become resendable.
    Companion V1 also promises a faithful resend to the original target
    snapshot, so legacy rows without ``device_ids`` remain previewable but are
    not exposed as resendable.
    """

    if not _device_ids(row.extra):
        return None
    return retained_composition(renders_dir, _nonempty_string(row.digest))


def retained_composition(
    renders_dir: Path,
    digest: str | None,
) -> RetainedComposition | None:
    """Resolve ``<digest>.png`` without permitting traversal or symlink escape."""

    if digest is None or _DIGEST_RE.fullmatch(digest) is None:
        return None

    root = renders_dir.resolve()
    candidate = root / f"{digest}.png"
    try:
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    return RetainedComposition(path=resolved, etag=digest.lower())


def _fit_mode(extra: dict[str, Any]) -> str | None:
    # Current pushes store ``fit``. Read the earlier ``image_fit`` spelling
    # first so retained development rows remain useful after upgrading.
    raw = extra.get("image_fit")
    if raw is None:
        raw = extra.get("fit")
    return raw if isinstance(raw, str) and raw in _IMAGE_FIT_MODES else None


def _framing(extra: dict[str, Any]) -> dict[str, float] | None:
    """Original ``image_framing`` intent stored on the push row, or None.

    Only the intent is public contract (reproduce / re-target); resolved
    per-target crop rectangles are derived state and stay server-internal.
    """
    raw = extra.get("framing")
    if not isinstance(raw, dict):
        return None
    values: dict[str, float] = {}
    for key in ("focus_x", "focus_y", "zoom"):
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        values[key] = float(value)
    return values


def _device_ids(extra: dict[str, Any]) -> list[str]:
    raw = extra.get("device_ids")
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(value for value in raw if isinstance(value, str) and value))


def _nonempty_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _utc_timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace("+00:00", "Z")
