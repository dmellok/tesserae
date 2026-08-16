"""Local stats: the daily counter store, the event-log recorder, and the
/stats page's read paths.

The privacy claims on that page are the point of the feature, so they're
asserted here too: pause actually stops writes, delete actually empties
the file, and the export contains nothing but dates, metric names,
dimensions, and integers.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.event_log import EventRow
from app.state.stats_store import (
    ACTIVITY_BY_TYPE,
    DEVICE_WAKES,
    FRAMES_BY_DEVICE,
    PUSH_MS_SUM,
    PUSHES_BY_SOURCE,
    PUSHES_BY_STATUS,
    StatsStore,
    days_back,
    today,
)
from app.stats_recorder import StatsRecorder, bucket_for


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=True,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


@pytest.fixture
def store(tmp_path: Path) -> StatsStore:
    return StatsStore(tmp_path / "stats.db")


def _push(**kwargs: object) -> EventRow:
    row = {
        "id": 1,
        "type": "push",
        "timestamp": time.time(),
        "source": "scheduler",
        "target": "kitchen",
        "status": "sent",
        "digest": "abc",
        "error": None,
        "duration_s": 1.5,
        "extra": {"device_ids": ["frame01"]},
    }
    row.update(kwargs)
    return EventRow(**row)  # type: ignore[arg-type]


# -- store ------------------------------------------------------------


def test_bump_accumulates_per_day_metric_and_dim(store: StatsStore) -> None:
    store.bump(FRAMES_BY_DEVICE, "frame01")
    store.bump(FRAMES_BY_DEVICE, "frame01", 4)
    store.bump(FRAMES_BY_DEVICE, "frame02")
    store.bump(FRAMES_BY_DEVICE, "frame01", 2, day="2020-01-01")

    assert store.by_dim(FRAMES_BY_DEVICE, days=1) == {"frame01": 5, "frame02": 1}
    assert store.total(FRAMES_BY_DEVICE) == 8  # all time picks up the old day
    assert store.total(FRAMES_BY_DEVICE, dim="frame02") == 1


def test_series_returns_a_row_for_every_day_in_the_window(store: StatsStore) -> None:
    store.bump(PUSHES_BY_SOURCE, "webhook", 3)
    series = store.series(PUSHES_BY_SOURCE, days=7)
    assert list(series) == days_back(7)
    assert series[today()] == {"webhook": 3}
    # Days with nothing in them are present and empty, so a chart gets a
    # continuous axis without inventing the gap itself.
    assert series[days_back(7)[0]] == {}


def test_pause_stops_writes_without_dropping_what_is_there(store: StatsStore) -> None:
    store.bump(DEVICE_WAKES, "frame01")
    store.set_paused(True)
    store.bump(DEVICE_WAKES, "frame01")
    assert store.paused() is True
    assert store.total(DEVICE_WAKES) == 1

    store.set_paused(False)
    store.bump(DEVICE_WAKES, "frame01")
    assert store.total(DEVICE_WAKES) == 2


def test_delete_all_empties_the_store_and_restarts_the_clock(store: StatsStore) -> None:
    store.bump(DEVICE_WAKES, "frame01", 3, day="2020-01-01")
    store.bump(DEVICE_WAKES, "frame02")
    assert store.delete_all() == 2
    assert store.total(DEVICE_WAKES) == 0
    assert store.since() == today()


def test_export_is_dates_names_and_integers_only(store: StatsStore) -> None:
    store.bump(FRAMES_BY_DEVICE, "frame01", 2)
    payload = store.export()
    assert payload["schema"] == 1
    assert payload["daily"] == [
        {"day": today(), "metric": FRAMES_BY_DEVICE, "dim": "frame01", "value": 2}
    ]
    for row in payload["daily"]:
        assert set(row) == {"day", "metric", "dim", "value"}
        assert isinstance(row["value"], int)


def test_bump_never_raises_on_a_broken_store(tmp_path: Path) -> None:
    """A stats write must not be able to break a render, so a store
    pointed at something unusable degrades to a no-op."""
    store = StatsStore(tmp_path / "stats.db")
    store._path = tmp_path  # a directory: every write from here fails
    store.bump(FRAMES_BY_DEVICE, "frame01")


# -- recorder ---------------------------------------------------------


def test_recorder_counts_a_sent_push_across_its_displays(store: StatsStore) -> None:
    recorder = StatsRecorder(store)
    recorder.on_event(_push(extra={"device_ids": ["frame01", "frame02"]}, duration_s=2.0))

    assert store.total(PUSHES_BY_STATUS, dim="sent") == 1
    assert store.total(PUSHES_BY_SOURCE, dim="scheduler") == 1
    assert store.by_dim(FRAMES_BY_DEVICE) == {"frame01": 1, "frame02": 1}
    assert store.total(PUSH_MS_SUM) == 2000


def test_recorder_keeps_failed_pushes_out_of_the_frame_counts(store: StatsStore) -> None:
    """A push that painted nothing still counts as an outcome, but it
    isn't a frame on a display and doesn't belong in the render-time
    average."""
    recorder = StatsRecorder(store)
    recorder.on_event(_push(status="failed", error="boom"))
    recorder.on_event(_push(status="quiet"))

    assert store.total(PUSHES_BY_STATUS) == 2
    assert store.total(FRAMES_BY_DEVICE) == 0
    assert store.total(PUSH_MS_SUM) == 0


def test_recorder_files_other_event_types_under_activity(store: StatsStore) -> None:
    recorder = StatsRecorder(store)
    recorder.on_event(_push(type="touch", status="ok"))
    recorder.on_event(_push(type="ota", status="ok"))
    recorder.on_event(_push(type="touch", status="ok"))

    assert store.by_dim(ACTIVITY_BY_TYPE) == {"touch": 2, "ota": 1}
    assert store.total(PUSHES_BY_STATUS) == 0


def test_source_buckets_group_known_sources_and_keep_the_rest(store: StatsStore) -> None:
    assert bucket_for("scheduler") == "scheduled"
    assert bucket_for("resend") == "by hand"
    assert bucket_for("home_assistant") == "integrations"
    assert bucket_for("something_new") == "other"


# -- wiring + page ----------------------------------------------------


def test_events_recorded_by_the_app_reach_the_stats_store(app: Flask) -> None:
    """The recorder is attached to the event log, so anything that logs
    an event is counted without instrumenting the push path."""
    store = app.config["STATS_STORE"]
    app.config["EVENT_LOG"].record(
        type="push",
        source="webhook",
        target="kitchen",
        status="sent",
        extra={"device_ids": ["frame01"]},
    )
    assert store.total(PUSHES_BY_SOURCE, dim="webhook") == 1
    assert store.total(FRAMES_BY_DEVICE, dim="frame01") == 1


def test_stats_page_renders_empty_and_populated(app: Flask) -> None:
    client = app.test_client()
    empty = client.get("/stats/")
    assert empty.status_code == 200
    assert "Nothing counted yet" in empty.get_data(as_text=True)

    app.config["STATS_RECORDER"].on_event(_push(source="scheduler"))
    body = client.get("/stats/").get_data(as_text=True)
    assert "Nothing counted yet" not in body
    assert "frame01" in body
    assert "scheduled" in body


def test_stats_page_window_falls_back_to_the_default(app: Flask) -> None:
    client = app.test_client()
    assert client.get("/stats/?window=999").status_code == 200
    assert client.get("/stats/?window=nonsense").status_code == 200


def test_stats_export_pause_and_delete_round_trip(app: Flask) -> None:
    client = app.test_client()
    store = app.config["STATS_STORE"]
    app.config["STATS_RECORDER"].on_event(_push())

    export = client.get("/stats/export.json")
    assert export.status_code == 200
    assert "attachment" in export.headers["Content-Disposition"]
    assert export.get_json()["daily"]

    client.post("/stats/pause")
    assert store.paused() is True
    app.config["STATS_RECORDER"].on_event(_push())
    assert store.total(PUSHES_BY_STATUS) == 1

    client.post("/stats/delete")
    assert store.total(PUSHES_BY_STATUS) == 0


def test_chart_payload_is_parseable_json_in_the_page(app: Flask) -> None:
    """The series ride in a JSON script block, not a data- attribute:
    tojson emits raw double quotes, which end a quoted attribute early
    and leave the chart silently empty."""
    import json
    import re

    for source in ("scheduler", "webhook", "resend"):
        app.config["STATS_RECORDER"].on_event(_push(source=source))
    body = app.test_client().get("/stats/?window=7").get_data(as_text=True)

    match = re.search(
        r'<script id="pushes-data" type="application/json">(.*?)</script>', body, re.S
    )
    assert match is not None
    payload = json.loads(match.group(1))
    assert len(payload["days"]) == 7
    assert {s["label"] for s in payload["series"]} == {"scheduled", "integrations", "by hand"}
    assert all(len(s["values"]) == 7 for s in payload["series"])


# -- sponsor prompt ---------------------------------------------------


class _Settings:
    """Minimal stand-in for the settings store's section API."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, object]] = {}

    def get_section(self, section: str) -> dict[str, object]:
        return dict(self.data.get(section, {}))

    def patch_section(self, section: str, values: dict[str, object]) -> None:
        self.data.setdefault(section, {}).update(values)


def _minted(days_ago: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def test_sponsor_prompt_stays_quiet_below_both_milestones(store: StatsStore) -> None:
    from app import sponsor_prompt

    store.bump(FRAMES_BY_DEVICE, "frame01", 500)
    assert (
        sponsor_prompt.state(settings=_Settings(), stats=store, install_created_at=_minted(30))
        is None
    )


def test_sponsor_prompt_fires_on_frames_and_on_age(store: StatsStore) -> None:
    from app import sponsor_prompt

    store.bump(FRAMES_BY_DEVICE, "frame01", 10_000)
    by_frames = sponsor_prompt.state(
        settings=_Settings(), stats=store, install_created_at=_minted(30)
    )
    assert by_frames is not None
    assert by_frames["reason"] == "frames"
    assert by_frames["frames"] == 10_000

    quiet = StatsStore(store._path.with_name("other.db"))
    by_age = sponsor_prompt.state(
        settings=_Settings(), stats=quiet, install_created_at=_minted(400)
    )
    assert by_age is not None
    assert by_age["reason"] == "years"
    assert by_age["years"] == 1


def test_sponsor_prompt_dismissal_is_permanent(store: StatsStore) -> None:
    from app import sponsor_prompt

    settings = _Settings()
    store.bump(FRAMES_BY_DEVICE, "frame01", 20_000)
    assert sponsor_prompt.state(settings=settings, stats=store, install_created_at=_minted(500))

    sponsor_prompt.dismiss(settings)
    assert (
        sponsor_prompt.state(settings=settings, stats=store, install_created_at=_minted(500))
        is None
    )


def test_dismissal_survives_deleting_the_stats(app: Flask) -> None:
    """The dismissal flag lives in settings, not the stats store: using
    the privacy control must not bring the ask back."""
    from app import sponsor_prompt

    client = app.test_client()
    store = app.config["STATS_STORE"]
    store.bump(FRAMES_BY_DEVICE, "frame01", 12_000)

    client.post("/stats/sponsor/dismiss")
    client.post("/stats/delete")
    store.bump(FRAMES_BY_DEVICE, "frame01", 12_000)

    assert (
        sponsor_prompt.state(
            settings=app.config["SETTINGS_STORE"],
            stats=store,
            install_created_at=_minted(900),
        )
        is None
    )
    # The footer carries a permanent sponsor link, so the card is
    # identified by its own container rather than by the word.
    assert "dx-sponsor-card" not in client.get("/stats/").get_data(as_text=True)


def test_sponsor_card_appears_on_the_page_once_earned(app: Flask) -> None:
    client = app.test_client()
    app.config["STATS_STORE"].bump(FRAMES_BY_DEVICE, "frame01", 10_000)
    body = client.get("/stats/").get_data(as_text=True)
    assert "dx-sponsor-card" in body
    assert "10,000 frames" in body

    client.post("/stats/sponsor/dismiss")
    assert "dx-sponsor-card" not in client.get("/stats/").get_data(as_text=True)


def test_install_age_handles_a_missing_or_broken_timestamp() -> None:
    from app import sponsor_prompt

    assert sponsor_prompt.install_age_days("") == 0
    assert sponsor_prompt.install_age_days("not a date") == 0
    assert sponsor_prompt.install_age_days(_minted(10)) == 10
