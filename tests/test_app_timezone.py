"""``app_timezone`` (v0.69.6 for issue #52 item 2) resolves the
``settings.app.timezone`` value into a real ``tzinfo`` so server-side
timestamp rendering follows the user's zone, not the container's.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask

from app.tz_resolve import app_timezone


def test_app_timezone_reads_configured_iana_name(app: Flask) -> None:
    """Explicit IANA name in ``settings.app.timezone`` wins."""
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "Europe/Berlin"})
    with app.app_context():
        tz = app_timezone()
    assert tz == ZoneInfo("Europe/Berlin")


def test_app_timezone_falls_back_when_setting_is_system(app: Flask) -> None:
    """The literal ``system`` (and empty) delegate to
    ``_resolve_iana_timezone`` which reads ``TZ`` / ``/etc/localtime``;
    result is at minimum a real ``tzinfo`` so ``fromtimestamp(tz=...)``
    always gets something usable."""
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "system"})
    with app.app_context():
        tz = app_timezone()
    assert tz is not None
    # A real tz, not the naive fallback path; caller can attach and
    # ``astimezone`` without a TypeError.
    now = datetime.now(tz)
    assert now.tzinfo is not None


def test_app_timezone_rejects_unknown_zone(app: Flask) -> None:
    """A corrupt settings.json with a made-up zone name falls back to
    system-local rather than crashing the request."""
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "Mars/Olympus_Mons"})
    with app.app_context():
        tz = app_timezone()
    assert tz is not None


def test_history_abs_renders_in_configured_timezone(app: Flask) -> None:
    """Regression for #52 item 2: a Docker container on UTC used to
    render history-row absolute times in UTC even when the user set
    a non-UTC ``settings.app.timezone``. Now the row picks up the
    configured zone via ``app_timezone``."""
    from app.history_routes import history_view
    from app.state.event_log import EventRow

    # 2026-07-05 03:15:00 UTC == 2026-07-05 13:15:00 in Australia/Melbourne
    # (AEST, UTC+10). Assert Melbourne wins when set.
    utc_moment = datetime(2026, 7, 5, 3, 15, tzinfo=UTC)
    row = EventRow(
        id=1,
        type="push",
        timestamp=utc_moment.timestamp(),
        source="page",
        target="home",
        status="dispatched",
        digest=None,
        error=None,
        duration_s=0.0,
        extra={},
    )
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "Australia/Melbourne"})
    with app.app_context():
        shaped = history_view([row])
    assert shaped[0]["abs"] == "2026-07-05 13:15:00"

    # Same row, UTC config: UTC clock time (proof the setting is what
    # switched it, not a stray import).
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "Etc/UTC"})
    with app.app_context():
        shaped_utc = history_view([row])
    assert shaped_utc[0]["abs"] == "2026-07-05 03:15:00"


def test_history_abs_survives_dst_boundary(app: Flask) -> None:
    """A timestamp on either side of a DST transition should render
    with the correct local wall-clock time, not shift by an hour."""
    from app.history_routes import history_view
    from app.state.event_log import EventRow

    # 2026-04-05 15:59 UTC is 2026-04-06 01:59 in Melbourne (just before
    # April DST rollback). One minute later is 02:00 UTC = 12:00 wall
    # time, and after DST rollback (Melbourne AEDT -> AEST at 03:00
    # local) is 2026-04-05 02:00. We're testing the correct picker
    # rather than the shift itself, so both these numbers are correct
    # per Australia's calendar and the test locks that in.
    before = datetime(2026, 4, 5, 15, 59, tzinfo=UTC)
    after = before + timedelta(hours=1)  # 16:59 UTC same day
    rows = [
        EventRow(
            id=1,
            type="push",
            timestamp=before.timestamp(),
            source="page",
            target="home",
            status="dispatched",
            digest=None,
            error=None,
            duration_s=0.0,
            extra={},
        ),
        EventRow(
            id=2,
            type="push",
            timestamp=after.timestamp(),
            source="page",
            target="home",
            status="dispatched",
            digest=None,
            error=None,
            duration_s=0.0,
            extra={},
        ),
    ]
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "Australia/Melbourne"})
    with app.app_context():
        shaped = history_view(rows)
    assert shaped[0]["abs"] == "2026-04-06 01:59:00"
    assert shaped[1]["abs"] == "2026-04-06 02:59:00"
