"""Unit tests for the EventLog-backed Companion History adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.companion_history import (
    MAX_HISTORY_LIMIT,
    InvalidHistoryCursor,
    history_item,
    list_history,
    parse_history_id,
    retained_composition,
    retained_composition_for_history,
)
from app.state.event_log import EventLog

_DIGEST_A = "0123456789abcdef"
_DIGEST_B = "fedcba9876543210"


def _log(tmp_path: Path, *, cap: int = 500) -> EventLog:
    return EventLog(tmp_path / "events.db", cap=cap)


def _retain(renders_dir: Path, digest: str, payload: bytes = b"\x89PNG\r\n") -> Path:
    renders_dir.mkdir(parents=True, exist_ok=True)
    path = renders_dir / f"{digest}.png"
    path.write_bytes(payload)
    return path


def test_list_is_newest_first_push_only_and_contract_shaped(tmp_path: Path) -> None:
    log = _log(tmp_path)
    renders = tmp_path / "renders"
    _retain(renders, _DIGEST_A)
    first = log.record(
        type="push",
        source="page",
        target="pantry",
        status="sent",
        digest=_DIGEST_A,
        duration_s=2.4,
        extra={"device_ids": ["kitchen", "kitchen"], "image_fit": "blur"},
    )
    log.record(type="renderer", source="pi_bin", target="topic", status="sent")
    second = log.record(
        type="push",
        source="companion",
        target="Shared photo",
        status="failed",
        error="panel unavailable",
        duration_s=-1,
        extra={"device_ids": ["desk", 42, ""], "fit": "center"},
    )

    body = list_history(
        log,
        renders,
        label_resolver=lambda row: (
            f"Dashboard: {row.target}" if row.source == "page" else row.target
        ),
    )

    assert [item["id"] for item in body["items"]] == [str(second), str(first)]
    assert body["next_before_id"] is None
    latest, oldest = body["items"]
    assert latest == {
        "id": str(second),
        "created_at": latest["created_at"],
        "source": "companion",
        "label": "Shared photo",
        "device_ids": ["desk"],
        "status": "failed",
        "duration_seconds": 0.0,
        "error": "panel unavailable",
        "preview_available": False,
        "resendable": False,
        "fit": "center",
    }
    assert latest["created_at"].endswith("Z")
    assert oldest["label"] == "Dashboard: pantry"
    assert oldest["device_ids"] == ["kitchen"]
    assert oldest["preview_available"] is True
    assert oldest["resendable"] is True
    assert oldest["fit"] == "blur"


def test_before_id_pages_without_overlap_and_limit_is_bounded(tmp_path: Path) -> None:
    log = _log(tmp_path, cap=MAX_HISTORY_LIMIT + 20)
    renders = tmp_path / "renders"
    ids = [
        log.record(type="push", source="page", target=f"page-{index}", status="sent")
        for index in range(MAX_HISTORY_LIMIT + 5)
    ]

    first = list_history(log, renders, limit=MAX_HISTORY_LIMIT + 500)
    assert len(first["items"]) == MAX_HISTORY_LIMIT
    assert first["next_before_id"] == str(ids[5])

    second = list_history(log, renders, before_id=first["next_before_id"], limit=3)
    assert [item["id"] for item in second["items"]] == [
        str(ids[4]),
        str(ids[3]),
        str(ids[2]),
    ]
    assert second["next_before_id"] == str(ids[2])

    final = list_history(log, renders, before_id=second["next_before_id"], limit=10)
    assert [item["id"] for item in final["items"]] == [str(ids[1]), str(ids[0])]
    assert final["next_before_id"] is None


def test_event_log_before_id_filters_in_sql_and_preserves_other_filters(tmp_path: Path) -> None:
    log = _log(tmp_path)
    first = log.record(type="push", source="page", target="one", status="sent")
    log.record(type="renderer", source="page", target="not-a-push", status="sent")
    second = log.record(type="push", source="page", target="two", status="sent")
    log.record(type="push", source="file", target="other-source", status="sent")

    rows = log.list(type="push", source="page", before_id=second, limit=10)

    assert [row.id for row in rows] == [first]


@pytest.mark.parametrize("raw", ["", "0", "-1", "+1", " 1", "01", "1.0", "one"])
def test_history_cursor_rejects_noncanonical_values(raw: str) -> None:
    with pytest.raises(InvalidHistoryCursor):
        parse_history_id(raw)


def test_preview_only_composition_is_visible_but_not_resendable(tmp_path: Path) -> None:
    log = _log(tmp_path)
    renders = tmp_path / "renders"
    path = _retain(renders, _DIGEST_B)
    event_id = log.record(
        type="push",
        source="button",
        target="kitchen",
        status="fetched",
        digest=None,
        extra={"composition_digest": _DIGEST_B, "device_ids": ["kitchen"]},
    )
    row = log.get(event_id)
    assert row is not None

    item = history_item(row, renders)
    preview = retained_composition_for_history(log, renders, str(event_id))

    assert item["preview_available"] is True
    assert item["resendable"] is False
    assert preview is not None
    assert preview.path == path.resolve()
    assert preview.etag == _DIGEST_B


def test_missing_original_target_snapshot_disables_only_resend(tmp_path: Path) -> None:
    log = _log(tmp_path)
    renders = tmp_path / "renders"
    _retain(renders, _DIGEST_A)
    log.record(
        type="push",
        source="file",
        target="legacy-photo.png",
        status="sent",
        digest=_DIGEST_A,
        extra={},
    )

    item = list_history(log, renders)["items"][0]

    assert item["preview_available"] is True
    assert item["resendable"] is False


def test_pruned_composition_leaves_row_but_disables_preview_and_resend(tmp_path: Path) -> None:
    log = _log(tmp_path)
    renders = tmp_path / "renders"
    artifact = _retain(renders, _DIGEST_A)
    event_id = log.record(
        type="push",
        source="file",
        target="photo.png",
        status="sent",
        digest=_DIGEST_A,
        extra={"device_ids": ["desk"], "image_fit": "fill"},
    )

    before = list_history(log, renders)["items"][0]
    artifact.unlink()
    after = list_history(log, renders)["items"][0]

    assert before["preview_available"] is True
    assert before["resendable"] is True
    assert after["id"] == str(event_id)
    assert after["preview_available"] is False
    assert after["resendable"] is False
    assert after["fit"] == "fill"
    assert retained_composition_for_history(log, renders, event_id) is None


def test_artifact_lookup_rejects_traversal_malformed_digest_and_symlink(
    tmp_path: Path,
) -> None:
    renders = tmp_path / "renders"
    renders.mkdir()
    outside = tmp_path / f"{_DIGEST_A}.png"
    outside.write_bytes(b"outside")
    (renders / f"{_DIGEST_B}.png").symlink_to(outside)

    assert retained_composition(renders, "../0123456789abcdef") is None
    assert retained_composition(renders, "not-a-content-digest") is None
    assert retained_composition(renders, _DIGEST_B) is None


def test_preview_lookup_rejects_non_push_and_unknown_rows(tmp_path: Path) -> None:
    log = _log(tmp_path)
    renders = tmp_path / "renders"
    _retain(renders, _DIGEST_A)
    renderer_id = log.record(
        type="renderer",
        source="pi_bin",
        target="topic",
        status="sent",
        digest=_DIGEST_A,
    )

    assert retained_composition_for_history(log, renders, str(renderer_id)) is None
    assert retained_composition_for_history(log, renders, "999999") is None
    assert retained_composition_for_history(log, renders, "../1") is None


def test_unknown_fit_mode_is_not_exposed(tmp_path: Path) -> None:
    log = _log(tmp_path)
    event_id = log.record(
        type="push",
        source="file",
        target="photo.png",
        status="sent",
        extra={"image_fit": "tile"},
    )
    row = log.get(event_id)
    assert row is not None

    assert history_item(row, tmp_path / "renders")["fit"] is None
