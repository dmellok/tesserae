"""#167 Phase 2b: the one-time schedules/rotations -> decks migration and the
projection stores that keep the legacy APIs working over the deck store.

Covers: startup migration + source rename (the resurrection guard), deletion
staying deleted across reboots, id-collision suffixing, idempotent
re-migration after a backup restore, projection CRUD ownership rules, the
single-fire guarantee (legacy records fire via the rotation pass, never the
deck pass too), and legacy records staying out of deck surfaces."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.scheduler import Scheduler
from app.state.deck_model import Deck, DeckPage
from app.state.deck_store import DeckStore
from app.state.legacy_projections import RotationProjection, ScheduleProjection
from app.state.rotation_model import Rotation, RotationStep
from app.state.schedule_model import Schedule
from app.state.schedule_store import ScheduleStore


def _rotation(rid: str = "evening", **kw) -> Rotation:
    base = dict(
        id=rid,
        name="Evening",
        steps=[
            RotationStep(page_id="a", dwell_minutes=15),
            RotationStep(page_id="b", dwell_minutes=15),
        ],
    )
    base.update(kw)
    return Rotation(**base)


def _schedule(sid: str = "hourly", **kw) -> Schedule:
    base = dict(
        id=sid,
        name="Hourly",
        page_id="dash",
        type="interval",
        interval_minutes=60,
    )
    base.update(kw)
    return Schedule(**base)


def _seed_legacy(data_root: Path, *, rotations=None, schedules=None) -> None:
    core = data_root / "core"
    core.mkdir(parents=True, exist_ok=True)
    if rotations is not None:
        (core / "rotations.json").write_text(
            json.dumps([r.model_dump(mode="json", exclude_none=True) for r in rotations]),
            encoding="utf-8",
        )
    if schedules is not None:
        (core / "schedules.json").write_text(
            json.dumps([s.model_dump(mode="json", exclude_none=True) for s in schedules]),
            encoding="utf-8",
        )


def _boot(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


# -- startup migration ----------------------------------------------------


def test_boot_migrates_legacy_stores_and_renames_sources(tmp_path: Path) -> None:
    _seed_legacy(tmp_path, rotations=[_rotation()], schedules=[_schedule()])
    app = _boot(tmp_path)

    core = tmp_path / "core"
    assert not (core / "rotations.json").exists()
    assert not (core / "schedules.json").exists()
    assert (core / "rotations.json.migrated").exists()
    assert (core / "schedules.json.migrated").exists()

    # The legacy APIs still serve the records, projected from the deck store.
    rotations = app.config["ROTATION_STORE"].all()
    assert [r.id for r in rotations] == ["evening"]
    assert rotations[0].steps[0].dwell_minutes == 15
    schedules = app.config["SCHEDULE_STORE"].all()
    assert [s.id for s in schedules] == ["hourly"]
    assert schedules[0].interval_minutes == 60

    # Stored as tagged decks in the one store.
    deck = app.config["DECK_STORE"].get("evening")
    assert deck is not None and deck.legacy_kind == "rotation"


def test_deleted_migrated_record_stays_deleted_across_reboots(tmp_path: Path) -> None:
    _seed_legacy(tmp_path, rotations=[_rotation()])
    app = _boot(tmp_path)
    assert app.config["ROTATION_STORE"].delete("evening") is True

    second = _boot(tmp_path)  # the restart every update performs
    assert second.config["ROTATION_STORE"].all() == []
    assert second.config["DECK_STORE"].get("evening") is None


def test_id_collision_with_real_deck_gets_suffixed(tmp_path: Path) -> None:
    core = tmp_path / "core"
    core.mkdir(parents=True, exist_ok=True)
    DeckStore(core / "decks.json").upsert(
        Deck(id="evening", name="Real deck", pages=[DeckPage(page_id="p")])
    )
    _seed_legacy(tmp_path, rotations=[_rotation()])
    app = _boot(tmp_path)

    assert [r.id for r in app.config["ROTATION_STORE"].all()] == ["evening-rotation"]
    real = app.config["DECK_STORE"].get("evening")
    assert real is not None and real.legacy_kind is None


def test_remigration_after_restore_is_idempotent(tmp_path: Path) -> None:
    _seed_legacy(tmp_path, rotations=[_rotation()])
    _boot(tmp_path)
    core = tmp_path / "core"
    # Simulate a pre-migration backup restore dropping the source file back.
    (core / "rotations.json.migrated").rename(core / "rotations.json")

    app = _boot(tmp_path)
    assert [r.id for r in app.config["ROTATION_STORE"].all()] == ["evening"]


def test_repeated_page_rotation_migrates(tmp_path: Path) -> None:
    rotation = _rotation(
        steps=[
            RotationStep(page_id="a", dwell_minutes=10),
            RotationStep(page_id="b", dwell_minutes=10),
            RotationStep(page_id="a", dwell_minutes=10),
        ]
    )
    _seed_legacy(tmp_path, rotations=[rotation])
    app = _boot(tmp_path)
    projected = app.config["ROTATION_STORE"].get("evening")
    assert projected is not None
    assert [s.page_id for s in projected.steps] == ["a", "b", "a"]


# -- projections ----------------------------------------------------------


def test_projection_round_trips_and_respects_ownership(tmp_path: Path) -> None:
    decks = DeckStore(tmp_path / "decks.json")
    rotations = RotationProjection(decks)
    schedules = ScheduleProjection(decks)

    rotations.upsert(_rotation())
    schedules.upsert(_schedule())
    assert rotations.get("evening") == _rotation()
    got = schedules.get("hourly")
    assert got is not None and got.interval_minutes == 60 and got.type == "interval"

    # Neither projection sees or touches the other's records or real decks.
    decks.upsert(Deck(id="real", name="Real", pages=[DeckPage(page_id="p")]))
    assert rotations.get("real") is None
    assert schedules.get("evening") is None
    assert rotations.delete("real") is False
    assert decks.get("real") is not None
    with pytest.raises(ValueError):
        rotations.upsert(_rotation("real"))

    # Toggle-style update round-trips.
    rotations.upsert(_rotation(enabled=False))
    projected = rotations.get("evening")
    assert projected is not None and projected.enabled is False


def test_daily_schedule_projection_round_trips_fires_at(tmp_path: Path) -> None:
    decks = DeckStore(tmp_path / "decks.json")
    schedules = ScheduleProjection(decks)
    schedules.upsert(
        _schedule(
            "morning",
            type="daily",
            interval_minutes=None,
            fires_at=datetime(2026, 3, 1, 7, 30),
        )
    )
    got = schedules.get("morning")
    assert got is not None and got.type == "daily"
    assert got.fires_at is not None and (got.fires_at.hour, got.fires_at.minute) == (7, 30)


# -- scheduler integration ------------------------------------------------


def test_migrated_rotation_fires_once_via_rotation_pass(tmp_path: Path) -> None:
    decks = DeckStore(tmp_path / "decks.json")
    projection = RotationProjection(decks)
    projection.upsert(_rotation(device_ids=[]))
    push = MagicMock()
    push.push.return_value = MagicMock(status="sent", error=None, duration_s=0.01, event_id="e")
    push.promote_deck_page.return_value = False
    push.device_in_quiet_hours.return_value = False
    scheduler = Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        rotation_store=projection,
        deck_store=decks,
        deck_nav_store=MagicMock(),
        push_manager=lambda: push,
        page_exists=lambda _pid: True,
        timezone_provider=lambda: UTC,
    )
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    # Exactly one fire: the rotation pass, with the deck pass skipping the
    # legacy record (double-fire would call push twice).
    assert push.push.call_count == 1
    assert push.push.call_args.kwargs.get("source") == "rotation"


# -- one-time migration notice --------------------------------------------


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_migration_notice_shows_once_and_dismisses(tmp_path: Path) -> None:
    _seed_legacy(tmp_path, rotations=[_rotation()], schedules=[_schedule()])
    app = _boot(tmp_path)
    client = app.test_client()
    _sign_in(client)

    # #167 Phase 3: both old pages redirect to the Decks page, which
    # carries the notice.
    for path in ("/schedules", "/rotations", "/decks"):
        assert "moved house" in client.get(path, follow_redirects=True).get_data(as_text=True)

    resp = client.post(
        "/schedules/migration-notice/dismiss",
        data={"back": "/rotations"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303) and resp.location.endswith("/rotations")
    for path in ("/schedules", "/rotations", "/decks"):
        assert "moved house" not in client.get(path, follow_redirects=True).get_data(as_text=True)

    # Dismissal persists across the restart every update performs.
    second = _boot(tmp_path)
    client2 = second.test_client()
    _sign_in(client2)
    assert "moved house" not in client2.get("/decks").get_data(as_text=True)


def test_migration_notice_absent_on_fresh_installs(tmp_path: Path) -> None:
    app = _boot(tmp_path)  # no legacy files: nothing migrated
    client = app.test_client()
    _sign_in(client)
    assert "moved house" not in client.get("/decks").get_data(as_text=True)


def test_legacy_decks_stay_out_of_deck_surfaces(tmp_path: Path) -> None:
    decks = DeckStore(tmp_path / "decks.json")
    RotationProjection(decks).upsert(_rotation(device_ids=["panel"]))
    decks.upsert(Deck(id="real", name="Real", device_ids=["panel"], pages=[DeckPage(page_id="p")]))
    assert [d.id for d in decks.for_device("panel")] == ["real"]
