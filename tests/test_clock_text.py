"""Every clock time the UI shows follows the app locale's cycle through one
formatter (:mod:`app.clock_text`), while machine-facing values (Home Assistant
sensor text, ISO stamps, inputs) keep the 24-hour data form."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from datetime import time as dt_time
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import next_update
from app.clock_text import clock_of, clock_time, epoch_label
from app.main import REPO_ROOT, create_app

NBSP = "\u202f"
# 2026-10-30 15:48:09 UTC
AFTERNOON = datetime(2026, 10, 30, 15, 48, 9, tzinfo=UTC)
MORNING = datetime(2026, 10, 30, 7, 0, 0, tzinfo=UTC)
MIDNIGHT = datetime(2026, 10, 30, 0, 0, 0, tzinfo=UTC)
NOON = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)


def test_clock_of_twelve_hour_form() -> None:
    assert clock_of(AFTERNOON, "h12") == f"3:48{NBSP}PM"
    assert clock_of(MORNING, "h12") == f"7{NBSP}AM"
    assert clock_of(NOON, "h12") == f"12{NBSP}PM"
    assert clock_of(MIDNIGHT, "h12") == f"12{NBSP}AM"


def test_clock_of_twenty_four_hour_form() -> None:
    assert clock_of(AFTERNOON, "h23") == "15:48"
    assert clock_of(MORNING, "h23") == "07:00"
    assert clock_of(MIDNIGHT, "h23") == "00:00"


def test_clock_of_seconds() -> None:
    assert clock_of(AFTERNOON, "h12", seconds=True) == f"3:48:09{NBSP}PM"
    assert clock_of(MORNING, "h12", seconds=True) == f"7:00:00{NBSP}AM"
    assert clock_of(AFTERNOON, "h23", seconds=True) == "15:48:09"


def test_epoch_label_names_another_day() -> None:
    now = AFTERNOON.timestamp()
    tomorrow = datetime(2026, 10, 31, 7, 0, tzinfo=UTC).timestamp()
    assert epoch_label(now, tz=UTC, now=now, cycle="h12") == f"3:48{NBSP}PM"
    assert epoch_label(tomorrow, tz=UTC, now=now, cycle="h12") == f"Sat 7{NBSP}AM"
    assert epoch_label(tomorrow, tz=UTC, now=now, cycle="h23") == "Sat 07:00"


@pytest.mark.parametrize(
    ("value", "h12", "h23"),
    [
        ("15:48", f"3:48{NBSP}PM", "15:48"),
        ("07:00", f"7{NBSP}AM", "07:00"),
        ("00:00", f"12{NBSP}AM", "00:00"),
        (dt_time(23, 59), f"11:59{NBSP}PM", "23:59"),
        (AFTERNOON, f"3:48{NBSP}PM", "15:48"),
    ],
)
def test_clock_time_filter_inputs(value: Any, h12: str, h23: str) -> None:
    assert clock_time(value, "h12") == h12
    assert clock_time(value, "h23") == h23


@pytest.mark.parametrize("bad", [None, "", "soon", "25:00", "12:75", True, object()])
def test_clock_time_filter_unreadable_input_is_empty(bad: Any) -> None:
    assert clock_time(bad, "h12") == ""


def test_clock_time_filter_with_date() -> None:
    assert clock_time(AFTERNOON, "h12", date=True) == f"Oct 30 3:48{NBSP}PM"
    assert clock_time(AFTERNOON, "h23", date=True, seconds=True) == "Oct 30 15:48:09"


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


def _locale(app: Flask, locale: str) -> None:
    app.config["SETTINGS_STORE"].patch_section("app", {"locale": locale, "timezone": "UTC"})


def test_jinja_filter_follows_the_locale(app: Flask) -> None:
    stamp = AFTERNOON.replace(tzinfo=None).timestamp()
    local = datetime.fromtimestamp(stamp)
    for locale, cycle, hm in (("en-US", "h12", f"3:48{NBSP}PM"), ("en-GB", "h23", "15:48")):
        _locale(app, locale)
        with app.test_request_context():
            plain = app.jinja_env.from_string("{{ '15:48'|clock_time }}").render()
            stamped = app.jinja_env.from_string("{{ ts|fmt_time }}").render(ts=stamp)
        assert plain == hm
        assert stamped == f"{local:%b} {local.day} {clock_of(local, cycle, seconds=True)}"


def test_filter_in_the_template_environment(app: Flask) -> None:
    assert "clock_time" in app.jinja_env.filters
    assert "fmt_time" in app.jinja_env.filters


# -- the Next update line: machine text stays 24-hour -----------------------

DEVICE = "door"


@pytest.fixture
def client(app: Flask):
    c = app.test_client()
    c.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = c.post(
        "/settings/devices/add",
        data={"id": DEVICE, "kind": "esp32_client", "name": "Hallway Frame", "transport": "rest"},
    )
    assert resp.status_code == 302
    return c


@pytest.fixture
def sleeping(monkeypatch: pytest.MonkeyPatch) -> float:
    """A sleeping frame whose next check-in is later today (15:48 UTC)."""
    checkin = AFTERNOON.replace(second=0).timestamp()
    monkeypatch.setattr(
        next_update,
        "reach_for_device",
        lambda device, now=None: next_update.Reach(
            next_update.MODE_SLEEPS, next_checkin_at=checkin, gap_s=3600
        ),
    )
    monkeypatch.setattr(next_update, "scheduled_push_at", lambda device, now=None: checkin)
    return checkin


def _view(app: Flask, locale: str, checkin: float) -> dict[str, Any]:
    _locale(app, locale)
    with app.test_request_context():
        device = app.config["DEVICE_REGISTRY"].get(DEVICE)
        timing = next_update.DeviceTiming(device, now=checkin - 600)
        return timing.view()


def test_view_keeps_machine_text_24_hour_in_every_locale(app: Flask, client, sleeping) -> None:
    for locale in ("en-US", "en-GB"):
        view = _view(app, locale, sleeping)
        assert view["when"] == "~15:48"
        assert "15:48" in view["text"] and "PM" not in view["text"]
        assert "15:48" in view["detail"] and "PM" not in view["detail"]


def test_view_display_twins_follow_the_locale(app: Flask, client, sleeping) -> None:
    us = _view(app, "en-US", sleeping)
    assert us["when_display"] == f"~3:48{NBSP}PM"
    assert f"~3:48{NBSP}PM" in us["detail_display"] and "~3:48" in us["text_display"]
    gb = _view(app, "en-GB", sleeping)
    assert gb["when_display"] == "~15:48"
    assert gb["detail_display"] == gb["detail"] and gb["text_display"] == gb["text"]


def test_home_assistant_state_text_unchanged_in_12_hour_locale(
    app: Flask, client, sleeping
) -> None:
    _locale(app, "en-US")
    with app.test_request_context():
        state = next_update.ha_state_for_device(app.config["DEVICE_REGISTRY"].get(DEVICE))
    assert re.fullmatch(r"~(?:\w{3} )?\d\d:\d\d", state["when"])
    assert "PM" not in state["detail"] and "AM" not in state["detail"]
    assert "PM" not in state["text"] and "AM" not in state["text"]
    assert isinstance(state["checkin_at"], float)


def test_settings_devices_next_update_tile_follows_the_locale(app: Flask, client, sleeping) -> None:
    def tile() -> str:
        html = client.get("/settings/devices").get_data(as_text=True)
        m = re.search(r"Next update</span>\s*<[^>]*>\s*([^<]+)<", html)
        assert m, "Next update tile not found"
        return m.group(1).strip()

    _locale(app, "en-US")
    assert re.fullmatch(rf"~(?:\w{{3}} )?\d{{1,2}}(?::\d\d)?{NBSP}[AP]M", tile())
    _locale(app, "en-GB")
    value = tile()
    assert re.fullmatch(r"~?(?:\w{3} )?\d\d:\d\d", value), value
