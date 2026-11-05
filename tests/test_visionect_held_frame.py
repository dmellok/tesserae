"""A Visionect sign ACKs every picture, so the server knows which frame it
holds, the same way it knows what a pull (REST) frame fetched.

The 2026-11-02 bug: a frame asleep through quiet hours was pushed lineup
frames at 06:00-06:15, and the Lineups header, the device page and the
Devices row all said the newest one was on the glass while it still showed the
night before. Every page branched on ``transport == "rest"`` to ask the push
manager's served record; a sign (transport ``push``) fell through to "the
latest render is on the glass". The sign's ACK now lands in that record
(``PushManager.record_frame_served``) and :func:`app.next_update.reports_held_frame`
is the one predicate the pages ask.
"""

from __future__ import annotations

import io
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask
from PIL import Image

from app import next_update, push_delivery
from app.main import REPO_ROOT, create_app
from app.next_update import reports_held_frame, tracks_held_frame
from app.state.deck_model import Deck, DeckPage
from app.state.page_store import Page

DEVICE = "frame"
KIND = "visionect_place_and_play_32"


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
    a.config["SETTINGS_STORE"].patch_section("app", {"locale": "en-GB"})
    for pid in ("night", "news"):
        a.config["PAGE_STORE"].save(Page(id=pid, name=pid.title(), device_ids=[DEVICE]))
    return a


@pytest.fixture
def client(app: Flask):
    c = app.test_client()
    c.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = c.post("/settings/devices/add", data={"id": DEVICE, "kind": KIND, "name": "Frame"})
    assert resp.status_code == 302
    app.config["DECK_STORE"].upsert(
        Deck(
            id="morning",
            name="Morning",
            device_ids=[DEVICE],
            pages=[DeckPage(page_id="news"), DeckPage(page_id="night")],
            advance="timer",
            advance_interval_minutes=30,
            advance_anchor="06:00",
            advance_end_at="09:00",
        )
    )
    return c


def _render(digest: str, comp: str, *, ts: float, page_id: str = "") -> dict[str, Any]:
    return {
        "digest": digest,
        "ext": "png",
        "filename": f"{digest}.png",
        "renderer_id": "visionect",
        "timestamp": ts,
        "composition_digest": comp,
        "preview_digest": None,
        "page_id": page_id,
    }


def _push(app: Flask, render: dict[str, Any]) -> None:
    pm = app.config["PUSH_MANAGER"]
    renders_dir: Path = app.config["RENDERS_DIR"]
    renders_dir.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, format="PNG")
    (renders_dir / f"{render['composition_digest']}.png").write_bytes(buf.getvalue())
    (renders_dir / render["filename"]).write_bytes(buf.getvalue())
    with pm._lock:
        if pm._latest_renders.get(DEVICE) is None:
            pm._latest_renders[DEVICE] = render
        else:
            pm._replace_latest_render_locked(DEVICE, render)


def _ack(app: Flask) -> None:
    """The sign ACKs the newest render (what the driver hands the manager)."""
    pm = app.config["PUSH_MANAGER"]
    pm.record_frame_served(DEVICE, dict(pm.latest_render_for(DEVICE)))


def _asleep_until(monkeypatch: pytest.MonkeyPatch, checkin: float) -> None:
    monkeypatch.setattr(
        next_update,
        "reach_for_device",
        lambda device, now=None: next_update.Reach(
            next_update.MODE_SLEEPS, next_checkin_at=checkin, gap_s=1800
        ),
    )


def _device(app: Flask) -> Any:
    return app.config["DEVICE_REGISTRY"].get(DEVICE)


def _night_then_newspaper(app: Flask) -> None:
    """Last night's picture is on the glass; the newspaper is newer."""
    _push(app, _render("night1", "c0ffee01", ts=1_700_000_000.0, page_id="night"))
    _ack(app)
    _push(app, _render("paper1", "c0ffee02", ts=1_700_040_000.0, page_id="news"))


# -- the predicate ------------------------------------------------------------


def test_predicate_covers_pull_displays_and_visionect_signs_only(app: Flask, client) -> None:
    assert reports_held_frame(SimpleNamespace(id="x", transport="rest"))
    assert reports_held_frame(_device(app))
    for transport in ("mqtt", "push", "relay"):
        assert not reports_held_frame(SimpleNamespace(id="x", transport=transport))


def test_a_sign_is_tracked_only_once_the_server_has_a_served_record(app: Flask, client) -> None:
    pm = app.config["PUSH_MANAGER"]
    sign = _device(app)
    _push(app, _render("night1", "c0ffee01", ts=1.0))
    assert not tracks_held_frame(pm, sign)  # never ACKed: today's assumption stands
    _ack(app)
    assert tracks_held_frame(pm, sign)
    # A pull display is always tracked: nothing fetched yet means "unknown".
    assert tracks_held_frame(pm, SimpleNamespace(id="other", transport="rest"))
    assert not tracks_held_frame(pm, SimpleNamespace(id="other", transport="mqtt"))


# -- queued vs delivered ----------------------------------------------------


def test_an_unacked_newer_render_is_queued_for_the_next_wake(
    app: Flask, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _night_then_newspaper(app)
    wake = time.time() + 3 * 3600
    _asleep_until(monkeypatch, wake)
    with app.test_request_context():
        out = next_update.pending_delivery(_device(app))
    assert out["queued"] is True
    assert out["queued_at"] == 1_700_040_000.0
    assert out["mode"] == next_update.MODE_SLEEPS
    assert out["expected_at"] == pytest.approx(wake)
    assert out["expected_when"].startswith("~")


def test_after_the_ack_nothing_is_queued(app: Flask, client) -> None:
    _night_then_newspaper(app)
    _ack(app)
    with app.test_request_context():
        assert next_update.pending_delivery(_device(app))["queued"] is False


def test_a_sign_that_never_acked_falls_back_to_not_queued(app: Flask, client) -> None:
    _push(app, _render("night1", "c0ffee01", ts=1.0))
    _push(app, _render("paper1", "c0ffee02", ts=2.0))
    with app.test_request_context():
        assert next_update.pending_delivery(_device(app))["queued"] is False


def test_a_push_display_is_never_queued(app: Flask, client) -> None:
    client.post("/settings/devices/add", data={"id": "kitchen", "kind": "esp32_client"})
    pm = app.config["PUSH_MANAGER"]
    pm._latest_renders["kitchen"] = _render("A", "compA", ts=1.0) | {"last_served_digest": "B"}
    with app.test_request_context():
        assert (
            next_update.pending_delivery(app.config["DEVICE_REGISTRY"].get("kitchen"))["queued"]
            is False
        )


def test_the_served_record_survives_a_restart(app: Flask, client, tmp_path: Path) -> None:
    _night_then_newspaper(app)
    pm = app.config["PUSH_MANAGER"]
    reloaded = pm._load_latest_renders()
    assert reloaded[DEVICE]["digest"] == "paper1"
    assert reloaded[DEVICE]["last_served_digest"] == "night1"
    assert reloaded[DEVICE]["last_served_page_id"] == "night"
    assert reloaded[DEVICE]["last_served_composition_digest"] == "c0ffee01"


# -- the Lineups page ---------------------------------------------------------


def _lineups_display(client) -> dict[str, Any]:
    data = client.get("/decks/view.json").get_json()
    return next(d for d in data["displays"] if d["id"] == DEVICE)


def test_lineups_header_shows_the_glass_and_a_queued_badge(
    app: Flask, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _night_then_newspaper(app)
    _asleep_until(monkeypatch, time.time() + 3 * 3600)
    display = _lineups_display(client)
    # The page on the glass is last night's, not the newspaper.
    assert display["on_screen"]["dashboard_id"] == "night"
    assert display["on_screen_known"] is True
    assert display["in_sync"] is False
    assert display["queued"]["dashboard_id"] == "news"
    assert display["queued"]["lands"].startswith("~")
    badge = display["queued_badge"]
    assert badge["label"] == "Queued" and "next check-in" in badge["title"]


def test_lineups_header_moves_to_the_new_picture_after_the_ack(app: Flask, client) -> None:
    _night_then_newspaper(app)
    _ack(app)
    display = _lineups_display(client)
    assert display["on_screen"]["dashboard_id"] == "news"
    assert display["queued"] is None and display["queued_badge"] is None
    assert display["in_sync"] is True


def test_lineups_view_helpers_for_a_sign(app: Flask, client) -> None:
    from app.deck_routes import _frame_pending, _frame_state, _panel_states

    _night_then_newspaper(app)
    sign = _device(app)
    with app.test_request_context("/decks"):
        state = _panel_states([sign])[DEVICE]
        assert state["page_id"] == "night" and state["verb"] == "shown" and state["reports"]
        assert _frame_pending(sign) is True
        frame, nothing_sent = _frame_state(sign)
        assert frame is not None and "c0ffee01" in frame.thumb_url and not nothing_sent
    _ack(app)
    with app.test_request_context("/decks"):
        assert _panel_states([sign])[DEVICE]["page_id"] == "news"
        assert _frame_pending(sign) is False
        frame, _ = _frame_state(sign)
        assert frame is not None and "c0ffee02" in frame.thumb_url


def test_lineups_helpers_keep_todays_reading_for_a_sign_that_never_acked(
    app: Flask, client
) -> None:
    from app.deck_routes import _frame_pending, _panel_states

    _push(app, _render("paper1", "c0ffee02", ts=2.0, page_id="news"))
    sign = _device(app)
    with app.test_request_context("/decks"):
        state = _panel_states([sign])[DEVICE]
        assert state["page_id"] == "news" and state["verb"] == "sent" and not state["reports"]
        assert _frame_pending(sign) is False


# -- the Devices row and the device page --------------------------------------


def test_overview_shows_the_glass_with_the_queued_image_beside_it(
    app: Flask, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _night_then_newspaper(app)
    _asleep_until(monkeypatch, time.time() + 3 * 3600)
    html = client.get("/settings/devices").get_data(as_text=True)
    assert "data-device-queued-thumb" in html
    assert "New image queued" in html
    assert "c0ffee01.png" in html  # the glass
    assert "shown " in html
    assert "c0ffee02" in html  # the queued image
    assert "next check-in" in html


def test_overview_is_one_calm_thumbnail_after_the_ack(app: Flask, client) -> None:
    _night_then_newspaper(app)
    _ack(app)
    html = client.get("/settings/devices").get_data(as_text=True)
    assert "data-device-queued-thumb" not in html
    assert "New image queued" not in html


# -- push notices -------------------------------------------------------------


def test_a_push_to_the_sleeping_sign_says_it_is_asleep(
    app: Flask, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    wake = time.time() + 3 * 3600
    _asleep_until(monkeypatch, wake)
    with app.test_request_context():
        notice = push_delivery.delivery_notice(_device(app))
    assert notice["mode"] == push_delivery.QUEUED
    assert notice["reason"] in (push_delivery.REASON_SLEEPING, push_delivery.REASON_QUIET_HOURS)
    assert notice["expected_at"] is not None and notice["expected_at"] >= wake - 1
    assert "Queued" in notice["short"]


# -- the Companion API --------------------------------------------------------


def test_companion_reads_the_served_frame_for_a_sign(app: Flask, client) -> None:
    from app import companion_api

    _night_then_newspaper(app)
    sign = _device(app)
    with app.test_request_context():
        view = companion_api._device_view(sign, None, app.config["PUSH_MANAGER"])
        assert view["has_pending_render"] is True
        before = companion_api._current_frame_at(sign)
    assert before is not None  # the ACK time, not the newspaper's render time
    _ack(app)
    with app.test_request_context():
        assert (
            companion_api._device_view(sign, None, app.config["PUSH_MANAGER"])["has_pending_render"]
            is False
        )
