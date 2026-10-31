"""A push to a sleeping frame says it is queued, on every push surface.

The wording is built by ``push_delivery.notice_for`` (pure); the app-level
``delivery_notice`` reads reach, telemetry and quiet hours; each push route
flashes it (HTML) or returns it as ``delivery`` (JSON)."""

from __future__ import annotations

import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from flask import Flask
from PIL import Image

from app import next_update as nu
from app import push_delivery as pd
from app.main import REPO_ROOT, create_app
from app.push import PushResult
from app.state.page_store import Page

# 2030-03-15 14:00 UTC, a Friday.
NOW = datetime(2030, 3, 15, 14, 0, tzinfo=UTC).timestamp()
MIN = 60.0


def _notice(reach: nu.Reach, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "device_id": "hallway",
        "name": "Hallway Frame",
        "reach": reach,
        "now": NOW,
        "tz": UTC,
        "cycle": "h12",
        "transport": "rest",
        "last_heartbeat_at": NOW - 2 * MIN,
    }
    args.update(kw)
    return pd.notice_for(**args)


# -- the wording ------------------------------------------------------------


def test_a_live_connection_delivers_now() -> None:
    n = _notice(nu.Reach(nu.MODE_CONNECTED, live=True))
    assert n["mode"] == pd.LIVE and n["text"] == "Delivering now"
    assert n["expected_at"] is None and n["reason"] is None
    assert n["short"] == "Pushed · lands now"
    assert set(n) == {"device_id", "name", "mode", "expected_at", "reason", "text", "short"}


def test_an_awake_frame_delivers_now_unless_it_stopped_polling() -> None:
    awake = nu.Reach(nu.MODE_CONNECTED)
    assert _notice(awake, awake_poll_s=30)["mode"] == pd.LIVE
    stale = _notice(awake, awake_poll_s=30, last_heartbeat_at=NOW - 12 * MIN)
    assert stale["mode"] == pd.QUEUED and stale["reason"] == pd.REASON_OFFLINE
    assert stale["text"].startswith("Hallway Frame hasn't checked in for 12 min")


def test_a_sleeping_frame_picks_it_up_at_its_next_checkin() -> None:
    reach = nu.Reach(nu.MODE_SLEEPS, next_checkin_at=NOW + 108 * MIN, gap_s=15 * MIN)
    n = _notice(reach)
    assert n["mode"] == pd.QUEUED and n["reason"] == pd.REASON_SLEEPING
    assert n["expected_at"] == NOW + 108 * MIN
    assert n["text"] == (
        "Hallway Frame is asleep: it will pick this up at its next check-in "
        "(~3:48 PM), or press its wake button to get it now."
    )
    assert n["short"] == "Queued · picks up ~3:48 PM"
    # 24-hour locale, and a frame with no wake button.
    plain = _notice(reach, cycle="h23", wake_button=False)
    assert (
        plain["text"]
        == "Hallway Frame is asleep: it will pick this up at its next check-in (~15:48)."
    )


def test_a_checkin_on_another_day_names_the_day() -> None:
    reach = nu.Reach(nu.MODE_SLEEPS, next_checkin_at=NOW + 17 * 60 * MIN)  # Sat 07:00
    assert "(~Sat 7 AM)" in _notice(reach)["text"]


def test_quiet_hours_say_until_when() -> None:
    reach = nu.Reach(nu.MODE_SLEEPS, next_checkin_at=NOW + 18.5 * 60 * MIN)  # Sat 08:30
    n = _notice(reach, in_quiet_hours=True, quiet_until=NOW + 18.5 * 60 * MIN)
    assert n["reason"] == pd.REASON_QUIET_HOURS and n["mode"] == pd.QUEUED
    assert n["text"].startswith("Hallway Frame is in quiet hours until Sat 8:30 AM: ")
    assert n["expected_at"] == NOW + 18.5 * 60 * MIN


def test_quiet_hours_pick_up_matches_the_next_update_estimate() -> None:
    """In quiet hours the frame sleeps through: the notice names the first
    check-in after them, the same one the Lineups header and the Settings
    queued line get from ``compute_next_update``."""
    ends = NOW + 2 * 60 * MIN  # 16:00 (the user set 15:59)
    reach = nu.Reach(nu.MODE_SLEEPS, next_checkin_at=NOW + 10 * MIN, gap_s=15 * MIN)
    n = _notice(reach, in_quiet_hours=True, quiet_until=ends - 60)
    est = nu.compute_next_update(NOW, reach, now=NOW, hold=nu.Hold(ends, "quiet hours"))
    assert n["expected_at"] == est.at == NOW + 130 * MIN
    label = nu.pickup_label(est.at, reach=reach, tz=UTC, now=NOW, cycle="h12")
    assert label == "~4:10\u202fPM" and f"({label})" in n["text"]


def test_a_missed_checkin_reads_as_offline() -> None:
    reach = nu.Reach(nu.MODE_SLEEPS, next_checkin_at=NOW - 10 * MIN, gap_s=15 * MIN)
    n = _notice(reach, last_heartbeat_at=NOW - 25 * MIN)
    assert n["mode"] == pd.QUEUED and n["reason"] == pd.REASON_OFFLINE
    assert n["expected_at"] is None
    assert n["text"].startswith(
        "Hallway Frame hasn't checked in for 25 min (it was due at 1:50 PM): "
    )
    # Just past the prediction is not offline: it's arriving.
    late = _notice(nu.Reach(nu.MODE_SLEEPS, next_checkin_at=NOW - 30, gap_s=15 * MIN))
    assert late["reason"] == pd.REASON_SLEEPING


def test_a_disconnected_sign_waits_to_reconnect() -> None:
    n = _notice(nu.Reach(nu.MODE_SLEEPS), transport="visionect")
    assert n["reason"] == pd.REASON_OFFLINE
    assert n["text"] == "Hallway Frame isn't connected: it will pick this up when it reconnects."


def test_unknown_cadence_and_no_status() -> None:
    unknown = _notice(nu.Reach(nu.MODE_UNKNOWN))
    assert unknown["mode"] == pd.UNKNOWN and unknown["reason"] is None
    assert unknown["text"].startswith("Hallway Frame will pick this up the next time it checks in")
    never = _notice(nu.Reach(nu.MODE_UNKNOWN), last_heartbeat_at=None)
    assert never["reason"] == pd.REASON_NO_STATUS
    assert never["text"] == (
        "Hallway Frame hasn't checked in yet: it will pick this up the first time it does."
    )
    # An MQTT panel is subscribed: a publish lands on it right away.
    assert _notice(nu.Reach(nu.MODE_UNKNOWN), transport="mqtt")["mode"] == pd.LIVE


def test_flash_text_lists_waiting_frames_and_counts_the_rest() -> None:
    live = _notice(nu.Reach(nu.MODE_CONNECTED, live=True))
    asleep = [
        _notice(nu.Reach(nu.MODE_SLEEPS, next_checkin_at=NOW + 5 * MIN), name=f"Frame {i}")
        for i in range(6)
    ]
    assert pd.flash_text([live]) == ""
    assert pd.waiting([live, asleep[0]]) == [asleep[0]]
    two = pd.flash_text([live, *asleep[:2]]).split("\n")
    assert [line.split(" is ")[0] for line in two] == ["Frame 0", "Frame 1"]
    many = pd.flash_text(asleep).split("\n")
    assert len(many) == 4
    assert many[-1] == "3 more displays pick this up at their next check-in."


# -- reading the app ---------------------------------------------------------


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
    a.config["SETTINGS_STORE"].patch_section("app", {"locale": "en-US", "timezone": "UTC"})
    return a


@pytest.fixture
def client(app: Flask) -> Any:
    c = app.test_client()
    c.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    for did in ("hallway", "hall"):
        code = app.config["PAIRING_STORE"].issue(note="test").code
        resp = c.post(
            "/api/v1/device/register",
            headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
            data=json.dumps({"device_id": did, "kind": "esp32_client"}),
        )
        assert resp.status_code == 201
    return c


def _beat(app: Flask, device_id: str, *, wake_in_s: float, now: float | None = None) -> None:
    now = time.time() if now is None else now
    app.config["DEVICE_TELEMETRY"].record_heartbeat(
        device_id,
        received_at=now,
        parsed={"sleep_until": now + wake_in_s},
        configured_sleep_s=900,
    )


def _live(monkeypatch: pytest.MonkeyPatch, *live_ids: str) -> None:
    """Make ``live_ids`` hold a live connection; the rest keep their reach."""
    real = nu.reach_for_device

    def reach(device: Any, now: float | None = None) -> nu.Reach:
        if device.id in live_ids:
            return nu.Reach(nu.MODE_CONNECTED, live=True)
        return real(device, now=now)

    monkeypatch.setattr(nu, "reach_for_device", reach)


def _flashes(client: Any) -> list[tuple[str, str]]:
    with client.session_transaction() as session:
        return list(session.get("_flashes", []))


def _clear(client: Any) -> None:
    with client.session_transaction() as session:
        session.pop("_flashes", None)


def _queued(client: Any) -> list[str]:
    return [text for cat, text in _flashes(client) if cat == "queued"]


def test_delivery_notice_reads_the_wake_prediction(app: Flask, client: Any) -> None:
    _beat(app, "hallway", wake_in_s=108 * MIN, now=NOW)
    device = app.config["DEVICE_REGISTRY"].devices["hallway"]
    with app.test_request_context("/"):
        n = pd.delivery_notice(device, NOW)
    assert n["mode"] == pd.QUEUED and n["reason"] == pd.REASON_SLEEPING
    assert n["expected_at"] == pytest.approx(NOW + 108 * MIN)
    assert "(~3:48 PM)" in n["text"]


def test_delivery_notice_says_quiet_hours(app: Flask, client: Any) -> None:
    app.config["SETTINGS_STORE"].patch_section(
        "app",
        {"quiet_hours_enabled": True, "quiet_hours_start": "13:00", "quiet_hours_end": "16:30"},
    )
    _beat(app, "hallway", wake_in_s=150 * MIN, now=NOW)
    device = app.config["DEVICE_REGISTRY"].devices["hallway"]
    with app.test_request_context("/"):
        n = pd.delivery_notice(device, NOW)
    assert n["reason"] == pd.REASON_QUIET_HOURS
    assert "in quiet hours until 4:30 PM" in n["text"]


def test_delivery_notices_keep_order_and_drop_unknown_ids(app: Flask, client: Any) -> None:
    _beat(app, "hallway", wake_in_s=10 * MIN)
    with app.test_request_context("/"):
        notices = pd.delivery_notices(["hall", "nope", "hallway", "hall"])
    assert [n["device_id"] for n in notices] == ["hall", "hallway"]
    assert notices[0]["reason"] == pd.REASON_NO_STATUS  # hall never checked in
    assert notices[1]["reason"] == pd.REASON_SLEEPING


# -- every push surface -----------------------------------------------------


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (0, 128, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _mock_push(app: Flask) -> MagicMock:
    pm = MagicMock()
    sent = PushResult(status="sent", page_id="x", composition_digest="d")
    pm.push.return_value = sent
    pm.push_image.return_value = sent
    pm.push_url_image.return_value = sent
    pm.republish.return_value = sent
    pm.device_in_quiet_hours.return_value = False
    app.config["PUSH_MANAGER"] = pm
    return pm


def test_send_file_flashes_queued_for_a_sleeping_frame(app: Flask, client: Any) -> None:
    _beat(app, "hallway", wake_in_s=20 * MIN)
    _mock_push(app)
    resp = client.post(
        "/send/file",
        data={"image": (io.BytesIO(_png()), "x.png"), "device_id": "hallway"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    flashes = _flashes(client)
    assert flashes[0][0] == "ok" and "queued for" in flashes[0][1]  # today's message stays
    queued = _queued(client)
    assert len(queued) == 1 and "is asleep: it will pick this up at its next check-in" in queued[0]


def test_send_file_to_a_live_frame_keeps_todays_message(
    app: Flask, client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live(monkeypatch, "hallway")
    _mock_push(app)
    client.post(
        "/send/file",
        data={"image": (io.BytesIO(_png()), "x.png"), "device_id": "hallway"},
        content_type="multipart/form-data",
    )
    assert _queued(client) == []
    assert [cat for cat, _ in _flashes(client)] == ["ok"]


def test_send_to_two_frames_lists_only_the_one_that_waits(
    app: Flask, client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live(monkeypatch, "hallway")
    _beat(app, "hall", wake_in_s=20 * MIN)
    _mock_push(app)
    client.post(
        "/send/url", data={"url": "https://example.com/a.png", "device_id": ["hallway", "hall"]}
    )
    queued = _queued(client)
    assert len(queued) == 1 and "\n" not in queued[0]
    assert "is asleep: it will pick this up at its next check-in" in queued[0]
    assert "hallway" not in queued[0].lower()


def test_send_page_and_pages_flash_the_bound_frames(app: Flask, client: Any) -> None:
    app.config["PAGE_STORE"].save(Page(id="kitchen", name="Kitchen", device_ids=["hallway"]))
    _mock_push(app)
    client.post("/send/page", data={"page_id": "kitchen", "return_to": "dashboards"})
    assert len(_queued(client)) == 1
    _clear(client)
    client.post("/send/pages", data={"page_ids": ["kitchen"]})
    assert any("hasn't checked in yet" in t for t in _queued(client))


def test_history_resend_flashes_the_original_targets(app: Flask, client: Any) -> None:
    _mock_push(app)
    event_id = app.config["EVENT_LOG"].record(
        type="push",
        source="page",
        target="kitchen",
        status="sent",
        extra={"device_ids": ["hallway"]},
    )
    client.post(f"/send/history/{event_id}/resend")
    assert len(_queued(client)) == 1


def _seed_lineup(app: Flask) -> None:
    from app.state.deck_model import Deck, DeckPage

    for pid in ("news", "art"):
        app.config["PAGE_STORE"].save(Page(id=pid, name=pid.title(), device_ids=["hallway"]))
    app.config["DECK_STORE"].upsert(
        Deck(
            id="morning",
            name="Morning",
            device_ids=["hallway"],
            pages=[DeckPage(page_id="news"), DeckPage(page_id="art")],
            advance="timer",
            advance_interval_minutes=30,
        )
    )


def test_lineup_push_step_returns_delivery(app: Flask, client: Any) -> None:
    _seed_lineup(app)
    _beat(app, "hallway", wake_in_s=20 * MIN)
    _mock_push(app)
    data = client.post("/decks/morning/push-step", json={"index": 1}).get_json()
    assert data["ok"] is True
    [notice] = data["delivery"]
    assert notice["device_id"] == "hallway" and notice["mode"] == "queued"
    assert notice["short"].startswith("Queued · picks up ~")
    assert data["message"].startswith("Queued Art for hallway. hallway is asleep")


def test_lineup_push_step_to_a_live_frame_is_unchanged(
    app: Flask, client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_lineup(app)
    _live(monkeypatch, "hallway")
    _mock_push(app)
    data = client.post("/decks/morning/push-step", json={"index": 1}).get_json()
    assert data["delivery"][0]["mode"] == "live"
    assert data["message"].startswith("Pushed Art to ") and data["message"].endswith("lands now.")


def test_lineup_push_step_plain_form_flashes_queued(app: Flask, client: Any) -> None:
    _seed_lineup(app)
    _beat(app, "hallway", wake_in_s=20 * MIN)
    _mock_push(app)
    client.post("/decks/morning/push-step", data={"index": "0"}, headers={"Accept": "text/html"})
    flashes = _flashes(client)
    assert any(cat == "success" and text.startswith("Pushed News to") for cat, text in flashes)
    assert len(_queued(client)) == 1


def test_deck_push_and_step_flash_queued(app: Flask, client: Any) -> None:
    _seed_lineup(app)
    _beat(app, "hallway", wake_in_s=20 * MIN)
    pm = _mock_push(app)
    pm.warm_deck_page.return_value = True
    pm.promote_deck_page.return_value = True
    client.post("/decks/morning/push")
    assert len(_queued(client)) == 1
    _clear(client)
    client.post("/decks/morning/step", data={"dir": "next"})
    assert len(_queued(client)) == 1


def test_device_page_push_now_and_test_pattern_flash_queued(app: Flask, client: Any) -> None:
    _beat(app, "hallway", wake_in_s=20 * MIN)
    pm = _mock_push(app)
    pm.latest_render_for.return_value = {"page_id": "kitchen"}
    client.post("/settings/devices/hallway/push-now")
    assert len(_queued(client)) == 1
    _clear(client)
    client.post("/settings/devices/hallway/test-pattern", data={"pattern": "palette_swatches"})
    assert len(_queued(client)) == 1


def test_canvas_send_returns_delivery(app: Flask, client: Any) -> None:
    _beat(app, "hallway", wake_in_s=20 * MIN)
    _mock_push(app)
    app.config["SETTINGS_STORE"].update_section("experiments", {"composer": True})
    cid = client.post("/pages/canvas/new").location.rsplit("/", 1)[1]
    resp = client.post(f"/pages/canvas/c/{cid}/send", json={"device_ids": ["hallway"]})
    data = resp.get_json()
    assert resp.status_code == 200, data
    assert data["sent"] == ["hallway"] and data["delivery"][0]["mode"] == "queued"


def test_mcp_push_returns_delivery(
    app: Flask, client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _beat(app, "hallway", wake_in_s=20 * MIN)
    _mock_push(app)
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})
    monkeypatch.setattr("app.renderer.render_to_png", lambda req, pool=None: _png())
    pid = client.post("/api/mcp/pages", json={"name": "Agent", "w": 800, "h": 480}).get_json()["id"]
    data = client.post(f"/api/mcp/pages/{pid}/push", json={"device_ids": ["hallway"]}).get_json()
    assert data["sent"] == ["hallway"]
    assert data["delivery"][0]["mode"] == "queued" and data["delivery"][0]["expected_at"]


def test_webhook_image_push_returns_delivery(app: Flask, client: Any) -> None:
    _beat(app, "hallway", wake_in_s=20 * MIN)
    _mock_push(app)
    token = "t" * 32
    app.config["SETTINGS_STORE"].patch_section("app", {"webhook_token": token})
    resp = client.post(
        "/api/v1/push/image",
        data={"device_ids": "hallway", "image": (io.BytesIO(_png()), "x.png", "image/png")},
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {token}"},
    )
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["delivery"][0]["device_id"] == "hallway"
    assert body["delivery"][0]["reason"] == "sleeping"


def test_webhook_page_push_returns_delivery(app: Flask, client: Any) -> None:
    app.config["PAGE_STORE"].save(Page(id="kitchen", name="Kitchen", device_ids=["hallway"]))
    _beat(app, "hallway", wake_in_s=20 * MIN)
    _mock_push(app)
    token = "t" * 32
    app.config["SETTINGS_STORE"].patch_section("app", {"webhook_token": token})
    resp = client.post(
        "/api/v1/push",
        json={"page_id": "kitchen"},
        headers={"Authorization": f"Bearer {token}"},
    )
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert [n["device_id"] for n in body["delivery"]] == ["hallway"]


def test_queued_flash_renders_with_its_own_style(app: Flask, client: Any) -> None:
    _beat(app, "hallway", wake_in_s=20 * MIN)
    _mock_push(app)
    client.post(
        "/send/file",
        data={"image": (io.BytesIO(_png()), "x.png"), "device_id": "hallway"},
        content_type="multipart/form-data",
    )
    body = client.get("/history").get_data(as_text=True)
    assert 'class="flash flash--queued"' in body
    assert "ph-moon-stars" in body
    assert "is asleep: it will pick this up" in body
