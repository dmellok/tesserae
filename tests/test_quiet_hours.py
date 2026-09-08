"""Quiet hours: helper module + PushManager gating tests.

Covers the four interesting cases:

* App-level window only.
* Per-device override (full coverage of all three sub-cases:
  override enabled / disabled / partially-configured).
* Wrap-around windows (22:00 → 07:00 = overnight).
* PushManager.push() with respect_quiet_hours=True filters bound
  devices, skipping the render entirely when every bound device is
  quiet.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.quiet_hours import (
    QuietHoursWindow,
    device_is_quiet,
    is_in_window,
    resolve_quiet_hours,
)

# ----- _parse_hhmm + window resolution -------------------------------


def _device(qh: dict | None) -> SimpleNamespace:
    """Build a Device-like stand-in with a manifest dict."""
    manifest: dict = {"id": "d", "kind": "pi_png_client"}
    if qh is not None:
        manifest["quiet_hours"] = qh
    return SimpleNamespace(manifest=manifest)


def test_resolve_quiet_hours_app_level_only() -> None:
    app = {
        "quiet_hours_enabled": True,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
    }
    window = resolve_quiet_hours(app, _device(None))
    assert window == QuietHoursWindow(time(22, 0), time(7, 0))


def test_resolve_quiet_hours_app_disabled_returns_none() -> None:
    app = {
        "quiet_hours_enabled": False,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
    }
    assert resolve_quiet_hours(app, _device(None)) is None


def test_resolve_quiet_hours_app_blank_times_returns_none() -> None:
    """Both fields blank → both parse to ``None`` → quiet hours off,
    even with the enable flag on. Stops a half-configured install from
    silently suppressing pushes."""
    app = {"quiet_hours_enabled": True}
    assert resolve_quiet_hours(app, _device(None)) is None


def test_resolve_quiet_hours_app_start_equals_end_returns_none() -> None:
    """``start == end`` (e.g. both ``00:00``) is treated as "never"
    rather than "the whole day"."""
    app = {
        "quiet_hours_enabled": True,
        "quiet_hours_start": "00:00",
        "quiet_hours_end": "00:00",
    }
    assert resolve_quiet_hours(app, _device(None)) is None


def test_resolve_quiet_hours_device_override_wins() -> None:
    app = {
        "quiet_hours_enabled": True,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
    }
    dev = _device({"enabled": True, "start": "23:30", "end": "06:30"})
    window = resolve_quiet_hours(app, dev)
    assert window == QuietHoursWindow(time(23, 30), time(6, 30))


def test_resolve_quiet_hours_device_override_disabled_falls_back() -> None:
    """An override with ``enabled: false`` falls back to the app
    setting, it's an explicit opt-out, not a silent toggle."""
    app = {
        "quiet_hours_enabled": True,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
    }
    dev = _device({"enabled": False, "start": "23:30", "end": "06:30"})
    assert resolve_quiet_hours(app, dev) == QuietHoursWindow(time(22, 0), time(7, 0))


def test_resolve_quiet_hours_no_device_no_app_returns_none() -> None:
    assert resolve_quiet_hours({}, None) is None


# ----- is_in_window with wrap-around --------------------------------


def test_is_in_window_simple_range() -> None:
    window = QuietHoursWindow(time(9, 0), time(17, 0))
    tz = ZoneInfo("UTC")
    assert is_in_window(window, datetime(2026, 1, 1, 10, 0, tzinfo=tz), tz)
    assert is_in_window(window, datetime(2026, 1, 1, 17, 0, tzinfo=tz), tz)
    assert not is_in_window(window, datetime(2026, 1, 1, 8, 59, tzinfo=tz), tz)
    assert not is_in_window(window, datetime(2026, 1, 1, 17, 1, tzinfo=tz), tz)


def test_is_in_window_wraps_midnight() -> None:
    """22:00 → 07:00 must match the late-night side AND the early-
    morning side, but NOT the middle of the day."""
    window = QuietHoursWindow(time(22, 0), time(7, 0))
    tz = ZoneInfo("UTC")
    # Late evening, inside.
    assert is_in_window(window, datetime(2026, 1, 1, 23, 0, tzinfo=tz), tz)
    # Just before midnight, inside.
    assert is_in_window(window, datetime(2026, 1, 1, 23, 59, tzinfo=tz), tz)
    # Just after midnight (next "day" same window), inside.
    assert is_in_window(window, datetime(2026, 1, 2, 1, 0, tzinfo=tz), tz)
    # End boundary, inside.
    assert is_in_window(window, datetime(2026, 1, 2, 7, 0, tzinfo=tz), tz)
    # After end, outside.
    assert not is_in_window(window, datetime(2026, 1, 2, 7, 1, tzinfo=tz), tz)
    # Middle of the day, outside.
    assert not is_in_window(window, datetime(2026, 1, 1, 14, 0, tzinfo=tz), tz)
    # Just before start, outside.
    assert not is_in_window(window, datetime(2026, 1, 1, 21, 59, tzinfo=tz), tz)


def test_is_in_window_respects_timezone() -> None:
    """A 22:00-07:00 window in Australia/Melbourne should match
    13:00 UTC (= 00:00 next day Melbourne)."""
    window = QuietHoursWindow(time(22, 0), time(7, 0))
    melbourne = ZoneInfo("Australia/Melbourne")
    # 13:00 UTC == 00:00 Melbourne (next day), inside.
    utc_at_midnight = datetime(2026, 1, 1, 13, 0, tzinfo=ZoneInfo("UTC"))
    assert is_in_window(window, utc_at_midnight, melbourne)
    # 03:00 UTC == 14:00 Melbourne, outside.
    utc_afternoon = datetime(2026, 1, 1, 3, 0, tzinfo=ZoneInfo("UTC"))
    assert not is_in_window(window, utc_afternoon, melbourne)


# ----- device_is_quiet (combined) ------------------------------------


def test_device_is_quiet_app_window_during_night() -> None:
    app = {
        "quiet_hours_enabled": True,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
    }
    tz = ZoneInfo("UTC")
    assert device_is_quiet(app, _device(None), datetime(2026, 1, 1, 23, 0, tzinfo=tz), tz)
    assert not device_is_quiet(app, _device(None), datetime(2026, 1, 1, 12, 0, tzinfo=tz), tz)


def test_device_is_quiet_device_override_takes_priority() -> None:
    """Device override saying 'always quiet' (whole day) wins over an
    app setting that says 'never quiet'."""
    app = {"quiet_hours_enabled": False}
    dev = _device({"enabled": True, "start": "00:00", "end": "23:59"})
    tz = ZoneInfo("UTC")
    # The device override is enabled, so the device IS quiet during
    # the entire 00:00-23:59 window.
    assert device_is_quiet(app, dev, datetime(2026, 1, 1, 14, 0, tzinfo=tz), tz)


# ----- PushManager.push() filtering ----------------------------------


def test_push_with_respect_quiet_hours_skips_when_all_devices_quiet(
    tmp_path: object,
) -> None:
    """When every bound device is currently in its quiet window AND
    the caller passed ``respect_quiet_hours=True``, the push returns
    ``status="quiet"`` and never enters the render path."""
    import io
    from datetime import datetime as _datetime
    from datetime import timedelta as _timedelta
    from pathlib import Path
    from unittest.mock import patch

    from PIL import Image

    from app import device_loader, device_service, renderer_loader
    from app.main import REPO_ROOT
    from app.push import PushManager
    from app.state.event_log import EventLog
    from app.state.page_store import Page, PageStore
    from app.state.settings_store import SettingsStore
    from app.transport import BrokerConfig, MqttTransport

    # A window that reliably contains "now". A fixed 00:00-23:59 window
    # looks all-day but leaves the final 59 s of the day uncovered:
    # is_in_window compares at HH:MM granularity inclusively, so
    # 23:59:00-23:59:59 falls outside and this test flaked when CI ran it
    # in that minute. Centre a two-hour window on the current local time
    # instead (the push path resolves no timezone here, so it evaluates
    # naive datetime.now()); now then sits ~1 h from either boundary,
    # clear of the boundary-minute gap. is_in_window handles the midnight
    # wrap when the window straddles 00:00.
    now_local = _datetime.now()
    settings = SettingsStore(Path(tmp_path) / "settings.json")  # type: ignore[arg-type]
    settings.update_section(
        "app",
        {
            "quiet_hours_enabled": True,
            "quiet_hours_start": (now_local - _timedelta(hours=1)).strftime("%H:%M"),
            "quiet_hours_end": (now_local + _timedelta(hours=1)).strftime("%H:%M"),
        },
    )

    device_data = Path(tmp_path) / "devices"  # type: ignore[arg-type]
    devices = device_loader.discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=device_data,
    )
    renderers = renderer_loader.discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=Path(tmp_path) / "rdata",  # type: ignore[arg-type]
    )
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=device_data,
        instance_id="hallway",
        kind_id="esp32_client",
    )

    page_store = PageStore(Path(tmp_path) / "pages.json")  # type: ignore[arg-type]
    page_store.save(Page(id="home", name="Home", device_ids=["hallway"], cells=[]))

    class _Fake:
        def __init__(self, *a, **kw):
            self.on_connect = self.on_disconnect = None
            self.on_message = None

        def username_pw_set(self, *a, **kw):
            pass

        def connect(self, *a, **kw):
            return 0

        def disconnect(self):
            return 0

        def loop_start(self):
            return 0

        def loop_stop(self):
            return 0

        def publish(self, *a, **kw):
            return type("R", (), {"rc": 0})()

        def subscribe(self, *a, **kw):
            return (0, 1)

    transport = MqttTransport(BrokerConfig(host="x"), client_factory=_Fake)
    transport.connect()

    composition_png = io.BytesIO()
    Image.new("RGB", (10, 10), (0, 0, 0)).save(composition_png, format="PNG")

    manager = PushManager(
        registry=renderers,
        page_store=page_store,
        transport=transport,
        settings=settings,
        event_log=EventLog(Path(tmp_path) / "events.db"),  # type: ignore[arg-type]
        renders_dir=Path(tmp_path) / "renders",  # type: ignore[arg-type]
        base_url_fn=lambda: "http://broker.local:8000",
        devices=devices,
    )

    # The render path mustn't be hit, assert via a patch that fails
    # loudly if it is.
    with patch("app.push.render_to_png") as rtp:
        rtp.side_effect = AssertionError("render should not be called when quiet")
        result = manager.push("home", respect_quiet_hours=True)

    assert result.status == "quiet"
    assert "quiet" in (result.error or "").lower()


# ----- weekday windows, all-day days, sleep-through (#299) -----------

from app.quiet_hours import ALL_DAYS, NO_DAYS, days_to_keys, parse_days, quiet_ends_at  # noqa: E402

# 2026-09-07 is a Monday.
_MON = datetime(2026, 9, 7, tzinfo=UTC)


def _on(day_offset: int, hh: int, mm: int = 0) -> datetime:
    return _MON.replace(hour=hh, minute=mm) + timedelta(days=day_offset)


_OFFICE = {
    "quiet_hours_enabled": True,
    "quiet_hours_start": "20:00",
    "quiet_hours_end": "08:00",
    "quiet_hours_days": ["mon", "tue", "wed", "thu", "fri"],
    "quiet_hours_all_day": ["sat", "sun"],
    "quiet_hours_sleep": True,
}


def test_parse_days_accepts_keys_numbers_and_comma_strings() -> None:
    assert parse_days(["mon", "Fri", "sunday"], NO_DAYS) == frozenset({0, 4, 6})
    assert parse_days("sat,sun", NO_DAYS) == frozenset({5, 6})
    assert parse_days([1, 2, 9, "junk"], NO_DAYS) == frozenset({1, 2})
    assert parse_days(None, ALL_DAYS) == ALL_DAYS
    # Present but empty is literal: every day unticked.
    assert parse_days([], ALL_DAYS) == NO_DAYS
    assert days_to_keys({6, 0, 4}) == ["mon", "fri", "sun"]


def test_resolve_reads_days_all_day_and_sleep_from_the_app_layer() -> None:
    window = resolve_quiet_hours(_OFFICE, _device(None))
    assert window is not None
    assert window.days == frozenset({0, 1, 2, 3, 4})
    assert window.all_day == frozenset({5, 6})
    assert window.sleep_through is True


def test_resolve_defaults_to_every_day_and_no_sleep_for_old_settings() -> None:
    app = {"quiet_hours_enabled": True, "quiet_hours_start": "22:00", "quiet_hours_end": "07:00"}
    window = resolve_quiet_hours(app, _device(None))
    assert window == QuietHoursWindow(time(22, 0), time(7, 0))
    assert window.days == ALL_DAYS and window.all_day == NO_DAYS and not window.sleep_through


def test_resolve_keeps_all_day_days_when_the_times_are_blank() -> None:
    """A weekend-only office leaves the nightly window empty; the
    all-day days must still count rather than the whole thing vanishing."""
    app = {"quiet_hours_enabled": True, "quiet_hours_all_day": ["sat", "sun"]}
    window = resolve_quiet_hours(app, _device(None))
    assert window is not None
    assert window.days == NO_DAYS and window.all_day == frozenset({5, 6})
    assert is_in_window(window, _on(5, 12), UTC)  # Saturday noon
    assert not is_in_window(window, _on(0, 3), UTC)  # Monday 03:00


def test_resolve_device_override_carries_its_own_days() -> None:
    dev = _device(
        {
            "enabled": True,
            "start": "21:00",
            "end": "06:00",
            "days": "fri",
            "all_day": [],
            "sleep": True,
        }
    )
    window = resolve_quiet_hours(_OFFICE, dev)
    assert window == QuietHoursWindow(time(21, 0), time(6, 0), frozenset({4}), NO_DAYS, True)


def test_is_in_window_judges_each_day_by_its_own_clock() -> None:
    window = resolve_quiet_hours(_OFFICE, _device(None))
    assert window is not None
    assert is_in_window(window, _on(0, 6), UTC)  # Monday 06:00, morning side
    assert not is_in_window(window, _on(0, 12), UTC)  # Monday noon
    assert is_in_window(window, _on(4, 21), UTC)  # Friday 21:00, evening side
    assert is_in_window(window, _on(5, 12), UTC)  # Saturday, all day
    assert is_in_window(window, _on(6, 15), UTC)  # Sunday, all day
    assert not is_in_window(window, _on(2, 8, 1), UTC)  # Wednesday just past end


def test_quiet_ends_at_walks_a_weekend_to_monday_morning() -> None:
    window = resolve_quiet_hours(_OFFICE, _device(None))
    assert window is not None
    assert quiet_ends_at(window, _on(4, 21), UTC) == _on(7, 8, 1)  # Fri 21:00 -> Mon 08:01
    assert quiet_ends_at(window, _on(5, 12), UTC) == _on(7, 8, 1)  # Saturday noon
    assert quiet_ends_at(window, _on(1, 6), UTC) == _on(1, 8, 1)  # Tuesday 06:00 -> 08:01
    # Already outside: hands back the moment itself.
    assert quiet_ends_at(window, _on(2, 12), UTC) == _on(2, 12)


def test_quiet_ends_at_is_none_when_every_day_is_quiet() -> None:
    window = QuietHoursWindow(time(0, 0), time(0, 0), NO_DAYS, ALL_DAYS)
    assert quiet_ends_at(window, _on(0, 12), UTC) is None


def test_quiet_ends_at_resolves_in_the_given_zone() -> None:
    melbourne = ZoneInfo("Australia/Melbourne")
    window = QuietHoursWindow(time(22, 0), time(7, 0))
    # 2026-09-07 13:00 UTC = 23:00 Melbourne (AEST, UTC+10): inside.
    ends = quiet_ends_at(window, datetime(2026, 9, 7, 13, 0, tzinfo=UTC), melbourne)
    assert ends == datetime(2026, 9, 8, 7, 1, tzinfo=melbourne).astimezone(UTC)
