"""History background-work rows (discussion #266).

Deck / album warms render into the side cache and never repaint a panel,
so their rows carry status ``warmed`` and stay hidden on /history by
default; ``?include_background=1`` (or filtering straight to the warm
source) brings them back, chipped distinctly from real pushes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

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


def _seed(log: EventLog) -> None:
    log.record(
        type="push",
        source="deck_warm",
        target="warm_page",
        status="warmed",
        digest="warmdigest",
        extra={"device_ids": ["panel_one"]},
    )
    log.record(
        type="push",
        source="deck",
        target="flip_page",
        status="sent",
        digest="flipdigest",
        extra={"device_ids": ["panel_one"], "promoted": True},
    )


def test_warm_rows_hidden_by_default(app: Flask) -> None:
    _seed(app.config["EVENT_LOG"])
    client = app.test_client()
    _sign_in(client)
    html = client.get("/history").get_data(as_text=True)
    assert "flip_page" in html
    assert "warm_page" not in html
    # The chip strip still advertises the hidden source with its count.
    assert "Show background" in html


def test_include_background_shows_warm_rows(app: Flask) -> None:
    _seed(app.config["EVENT_LOG"])
    client = app.test_client()
    _sign_in(client)
    html = client.get("/history?include_background=1").get_data(as_text=True)
    assert "warm_page" in html
    # Distinct pill + friendly source chip, not "pushed" / raw snake_case.
    assert "warmed" in html
    assert "Background refresh" in html


def test_source_filter_overrides_background_hiding(app: Flask) -> None:
    _seed(app.config["EVENT_LOG"])
    client = app.test_client()
    _sign_in(client)
    html = client.get("/history?source=deck_warm").get_data(as_text=True)
    assert "warm_page" in html
    assert "flip_page" not in html


def test_promoted_flip_row_reads_as_rotation_step(app: Flask) -> None:
    _seed(app.config["EVENT_LOG"])
    client = app.test_client()
    _sign_in(client)
    html = client.get("/history").get_data(as_text=True)
    assert "Rotation step" in html
