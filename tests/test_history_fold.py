"""History press → push folding.

A button/touch press row carries ``extra.push_event_id`` linking it to
the push row its action logged; the History page merges the pair into
one display row by default and splits them again behind
``?split_presses=1``. These tests drive the /history route end-to-end
plus ``history_view`` directly for the merged-field contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app.history_routes import history_view
from app.main import REPO_ROOT, create_app
from app.state.event_log import EventLog


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _record_pair(log: EventLog) -> tuple[int, int]:
    """A deck-nav press and the push it triggered, newest (press) last."""
    push_id = log.record(
        type="push",
        source="deck",
        target="printer",
        status="sent",
        digest="pushdigest",
        duration_s=2.2,
        extra={"device_ids": ["panel_one"]},
    )
    press_id = log.record(
        type="push",
        source="deck",
        target="panel_one",
        status="dispatched",
        digest=None,
        extra={
            "button": "right",
            "action_spec": "deck:main:printer",
            "pushed_page_id": "printer",
            "device_ids": ["panel_one"],
            "push_event_id": push_id,
        },
    )
    return push_id, press_id


def test_press_row_folds_into_push_row_by_default(app: Flask) -> None:
    push_id, press_id = _record_pair(app.config["EVENT_LOG"])
    client = app.test_client()
    _sign_in(client)
    body = client.get("/history").get_data(as_text=True)

    assert body.count("dx-inset-row") == 1
    # Both halves' status chips on the one row.
    assert ">button</span>" in body
    assert ">pushed</span>" in body
    # Resend targets the push half; the press row's disabled form is gone.
    assert f"/send/history/{push_id}/resend" in body
    assert f"/send/history/{press_id}/resend" not in body
    # The checkbox selects both halves for bulk delete.
    assert f'value="{press_id},{push_id}"' in body
    # Thumbnail borrowed from the push half.
    assert "/renders/pushdigest.png" in body


def test_split_presses_shows_raw_rows(app: Flask) -> None:
    _record_pair(app.config["EVENT_LOG"])
    client = app.test_client()
    _sign_in(client)
    body = client.get("/history?split_presses=1").get_data(as_text=True)
    assert body.count("dx-inset-row") == 2


def test_press_with_missing_partner_renders_unfolded(app: Flask) -> None:
    log = app.config["EVENT_LOG"]
    press_id = log.record(
        type="push",
        source="deck",
        target="panel_one",
        status="dispatched",
        digest=None,
        extra={"button": "right", "push_event_id": 9999},
    )
    client = app.test_client()
    _sign_in(client)
    body = client.get("/history").get_data(as_text=True)
    assert body.count("dx-inset-row") == 1
    assert f'value="{press_id}"' in body
    # Nothing to resend: the press has no digest and no partner.
    assert "preview only; resend from the original push" in body


def test_history_view_merges_push_fields(app: Flask) -> None:
    log = app.config["EVENT_LOG"]
    push_id, press_id = _record_pair(log)
    rows = log.list(type="push", limit=10)
    with app.app_context():
        shaped = history_view(rows, fold_presses=True)

    assert len(shaped) == 1
    row = shaped[0]
    assert row["id"] == press_id
    assert row["ids"] == [press_id, push_id]
    assert row["status"] == "dispatched"
    assert row["push_status"] == "sent"
    assert row["preview_digest"] == "pushdigest"
    assert row["can_resend"] is True
    assert row["resend_id"] == push_id
    assert row["duration_s"] == pytest.approx(2.2)


def test_history_view_without_fold_keeps_both_rows(app: Flask) -> None:
    log = app.config["EVENT_LOG"]
    push_id, press_id = _record_pair(log)
    rows = log.list(type="push", limit=10)
    with app.app_context():
        shaped = history_view(rows, fold_presses=False)

    assert [row["id"] for row in shaped] == [press_id, push_id]
    press = shaped[0]
    assert press["push_status"] is None
    assert press["ids"] == [press_id]
    assert press["can_resend"] is False
    assert press["resend_id"] == press_id
