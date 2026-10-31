"""The Devices quick panel's one "what's on now" line: the lineup live on the
display and until when, else the next to run, else none; linking to Lineups."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.deck_model import Deck, DeckPage
from app.state.page_store import Page

# A fixed-offset zone where it is around midday now, so the windows these
# tests draw never cross midnight. Etc/GMT-k is UTC+k.
_OFFSET = 12 - datetime.now(UTC).hour
_MIDDAY_TZ = f"Etc/GMT{'-' if _OFFSET >= 0 else '+'}{abs(_OFFSET)}"


def _local_now() -> datetime:
    tz: tzinfo = ZoneInfo(_MIDDAY_TZ)
    dt = datetime.now(tz)
    return dt.replace(minute=dt.minute - dt.minute % 15, second=0, microsecond=0)


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
    a.config["SETTINGS_STORE"].patch_section("app", {"locale": "en-GB", "timezone": _MIDDAY_TZ})
    for pid in ("news", "art"):
        a.config["PAGE_STORE"].save(Page(id=pid, name=pid.title(), device_ids=["porch"]))
    return a


@pytest.fixture
def client(app: Flask) -> Any:
    c = app.test_client()
    c.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = c.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": "porch", "kind": "esp32_client"}),
    )
    assert resp.status_code == 201
    return c


def _lineup(app: Flask, id_: str, name: str, start: datetime, end: datetime) -> None:
    app.config["DECK_STORE"].upsert(
        Deck(
            id=id_,
            name=name,
            device_ids=["porch"],
            pages=[DeckPage(page_id="news"), DeckPage(page_id="art")],
            advance="timer",
            advance_interval_minutes=15,
            advance_anchor=start.strftime("%H:%M"),
            advance_end_at=end.strftime("%H:%M"),
            advance_days_of_week=[0, 1, 2, 3, 4, 5, 6],
        )
    )


def _now_line(client: Any) -> str:
    body = str(client.get("/settings/devices").get_data(as_text=True))
    start = body.index('class="dx-devnow')
    return body[start : body.index("</a>", start)]


def test_no_lineups_says_none_yet_and_links_to_lineups(client: Any) -> None:
    line = _now_line(client)
    assert "is-none" in line and "None yet, add one" in line
    assert 'href="/decks#display-porch"' in line
    assert "dx-devnow-dot" not in line


def test_a_live_lineup_says_until_when(app: Flask, client: Any) -> None:
    now = _local_now()
    _lineup(app, "morning", "Morning", now - timedelta(minutes=30), now + timedelta(hours=1))
    line = _now_line(client)
    assert "is-running" in line
    assert '<span class="dx-devnow-name">Morning</span>' in line
    assert f"until {(now + timedelta(hours=1)).strftime('%H:%M')}" in line
    assert "/decks?hl=morning#display-porch" in line


def test_then_names_the_lineup_that_takes_over(app: Flask, client: Any) -> None:
    now = _local_now()
    hand_over = now + timedelta(hours=1)
    _lineup(app, "morning", "Morning", now - timedelta(minutes=30), hand_over)
    _lineup(app, "lunch", "Lunch", hand_over, hand_over + timedelta(hours=1))
    assert "then Lunch" in _now_line(client)


def test_nothing_running_names_the_next(app: Flask, client: Any) -> None:
    now = _local_now()
    later = now + timedelta(hours=2)
    _lineup(app, "dinner", "Dinner", later, later + timedelta(hours=1))
    line = _now_line(client)
    assert "is-idle" in line and "Nothing running" in line
    assert f"next Dinner today {later.strftime('%H:%M')}" in line
