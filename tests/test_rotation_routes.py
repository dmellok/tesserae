"""End-to-end /rotations route tests.

Regression for the v0.50.2 fix: Flask's tojson filter (which the
rotations + schedules edit forms use to seed the conditions textarea)
couldn't serialise Pydantic ``Condition`` instances, so the page
500'd as soon as any step gained a saved condition. The app factory
now installs a JSONProvider that handles BaseModel via
``model_dump``; these tests pin that behaviour.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.rotation_routes import _build_projection
from app.state.conditions import Condition
from app.state.rotation_model import Rotation, RotationStep
from app.state.schedule_model import Schedule


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


def test_rotation_index_renders_with_condition_on_step(app: Flask, tmp_path: Path) -> None:
    """The rotations index template seeds the per-step conditions
    textarea via ``step.conditions | tojson``. With a saved condition
    that's a Pydantic ``Condition`` instance, the page used to 500.
    """
    # #167 Phase 2b: rotations live in the deck store; write through the projection.
    store = app.config["ROTATION_STORE"]
    store.upsert(
        Rotation(
            id="evening",
            name="Evening",
            anchor="18:00",
            steps=[
                RotationStep(
                    page_id="home",
                    dwell_minutes=15,
                    conditions=[
                        Condition(
                            source_kind="time_window",
                            operator="in",
                            value={
                                "start_local": "18:00",
                                "end_local": "23:00",
                                "days_of_week": [0, 1, 2, 3, 4],
                            },
                        ),
                    ],
                ),
            ],
        )
    )
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/rotations?edit=evening", follow_redirects=True)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    body = resp.get_data(as_text=True)
    # The textarea should carry the serialised condition.
    assert "time_window" in body
    assert "start_local" in body


def test_schedule_index_renders_with_condition(app: Flask, tmp_path: Path) -> None:
    """Same shape, schedule edition: the schedules.html template uses
    ``s.conditions | tojson`` to seed its conditions textarea."""
    # #167 Phase 2b: schedules live in the deck store; read through the projection.
    store = app.config["SCHEDULE_STORE"]
    store.upsert(
        Schedule(
            id="morning",
            name="Morning refresh",
            page_id="home",
            type="interval",
            interval_minutes=15,
            conditions=[
                Condition(
                    source_kind="sun",
                    operator="after_sunset",
                    value={"offset_minutes": 0},
                ),
            ],
        )
    )
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/schedules?edit=morning", follow_redirects=True)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    body = resp.get_data(as_text=True)
    assert "after_sunset" in body


def test_form_submit_preserves_step_conditions_and_scheduler_skips_failing_step(
    app: Flask, tmp_path: Path
) -> None:
    """End-to-end: save a 4-step rotation via the form with an
    `is on` condition on step 4, read it back, and confirm
    `_pick_eligible_step` skips that step when the entity is `off`."""
    from datetime import UTC, datetime

    from app.scheduler import Scheduler
    from app.scheduler_conditions import ConditionEvaluator
    from app.state.page_store import Page, PageStore

    # Need pages or the route rejects the save (page-existence check).
    pages = PageStore(tmp_path / "core" / "pages.json")
    for pid in ("trmnl_todo", "trmnl_gen_image", "trmnl_home_assistant", "trmnl_3d_print"):
        pages.save(Page(id=pid, name=pid))

    from werkzeug.datastructures import MultiDict

    client = app.test_client()
    _sign_in(client)
    cond_json = (
        '[{"source_kind":"ha_entity","source_id":"binary_sensor.octoprint_printing",'
        '"operator":"==","value":"on"}]'
    )
    form = MultiDict()
    form.add("name", "TRMNL Rotation")
    form.add("anchor", "00:00")
    form.add("priority", "0")
    form.add("enabled", "on")
    for d in ("0", "1", "2", "3", "4", "5", "6"):
        form.add("days_of_week", d)
    for pid in (
        "trmnl_todo",
        "trmnl_gen_image",
        "trmnl_home_assistant",
        "trmnl_3d_print",
    ):
        form.add("step_page_ids[]", pid)
        form.add("step_dwell_minutes[]", "5")
        form.add("step_conditions_json[]", cond_json if pid == "trmnl_3d_print" else "")
    resp = client.post("/rotations/new", data=form, follow_redirects=False)
    # 302 on success; 200 with flash means validation failure.
    assert resp.status_code == 302, resp.get_data(as_text=True)[:500]

    # Read back what the route persisted (#167 Phase 2b: via the projection).
    store = app.config["ROTATION_STORE"]
    rotation = next(iter(store.all()))
    assert len(rotation.steps) == 4
    assert rotation.steps[3].conditions, (
        f"step 4 should have its condition persisted; got {rotation.steps[3].model_dump()}"
    )
    cond = rotation.steps[3].conditions[0]
    assert cond.source_kind == "ha_entity"
    assert cond.source_id == "binary_sensor.octoprint_printing"
    assert cond.operator == "=="
    assert cond.value == "on"

    # Now exercise the scheduler picker with a stub HA cache where the
    # entity is `off`. Should skip step 3 and pick step 0.
    evaluator = ConditionEvaluator(
        ha_get_states=lambda: [{"entity_id": "binary_sensor.octoprint_printing", "state": "off"}],
    )
    evaluator.refresh_ha_states()

    # Confirm the evaluator returns False, mirroring what Test conditions shows.
    assert not evaluator.all_pass(rotation.steps[3].conditions), (
        "evaluator should fail the condition when entity state is 'off' and we want 'on'"
    )

    # Build a minimal Scheduler so we can call _pick_eligible_step.
    sched = Scheduler(
        store=store,  # type: ignore[arg-type]
        push_manager=lambda: None,  # type: ignore[arg-type,return-value]
        condition_evaluator=evaluator,
    )
    picked = sched._pick_eligible_step(
        rotation,
        time_step_index=3,  # 3D Print is the time-current step
        now=datetime.now(UTC),
    )
    assert picked != 3, (
        "scheduler picked the 3D Print step despite its condition failing; "
        "this is the user-reported bug"
    )
    assert picked == 0, (
        "scheduler should wrap to step 0 (TRMNL - Todo) as the next eligible "
        f"step in scheduled mode; got {picked}"
    )


def test_manual_fire_button_respects_conditions(app: Flask, tmp_path: Path) -> None:
    """The "Fire now" button on the rotations index used to call
    ``_fire_rotation(state.step_index, ...)`` straight from the
    time-based step, bypassing the per-step condition check entirely.
    The user's bug: 3D-print step kept firing on the manual button
    even though its HA condition was failing. Fix routes the manual
    fire through ``_pick_eligible_step`` like the autonomous tick does.
    """
    from app.scheduler import Scheduler as _Scheduler
    from app.scheduler_conditions import ConditionEvaluator
    from app.state.page_store import Page, PageStore

    pages = PageStore(tmp_path / "core" / "pages.json")
    for pid in ("todo", "three_d_print"):
        pages.save(Page(id=pid, name=pid))

    # #167 Phase 2b: the fire endpoint reads the projection off app config,
    # so seed the rotation there (backed by the deck store).
    store = app.config["ROTATION_STORE"]
    store.upsert(
        Rotation(
            id="trmnl",
            name="TRMNL Rotation",
            anchor="00:00",
            steps=[
                RotationStep(page_id="todo", dwell_minutes=5),
                RotationStep(
                    page_id="three_d_print",
                    dwell_minutes=5,
                    conditions=[
                        Condition(
                            source_kind="ha_entity",
                            source_id="binary_sensor.octoprint_printing",
                            operator="==",
                            value="on",
                        ),
                    ],
                ),
            ],
        )
    )

    # Wire a stub HA cache where the printer is OFF and swap in a
    # scheduler that uses it. The fire endpoint reads the scheduler
    # off the app config, so we replace it for this test.
    evaluator = ConditionEvaluator(
        ha_get_states=lambda: [{"entity_id": "binary_sensor.octoprint_printing", "state": "off"}],
    )
    evaluator.refresh_ha_states()

    pushes: list[tuple[str, str]] = []

    class _StubPM:
        def push(self, page_id: str, *, respect_quiet_hours: bool, source: str):
            pushes.append((page_id, source))

            class _R:
                status = "sent"
                error = None

            return _R()

    sched = _Scheduler(
        store=store,  # type: ignore[arg-type]
        push_manager=lambda: _StubPM(),
        condition_evaluator=evaluator,
        rotation_store=store,
    )
    app.config["SCHEDULER"] = sched

    # Sneak in an in-memory force-state so the time-current step is the
    # 3D-print one (step idx 1). force_step writes the override that
    # compute_step_state honours, mirroring the user setup where the
    # natural cycle was sitting on the gated step.
    from datetime import UTC, datetime

    sched.force_step(store.get("trmnl"), 1, datetime.now(UTC))

    client = app.test_client()
    _sign_in(client)
    resp = client.post("/rotations/trmnl/fire", follow_redirects=False)
    assert resp.status_code == 302
    assert len(pushes) == 1, f"expected one push from the manual fire, got {pushes}"
    page_id, _source = pushes[0]
    assert page_id != "three_d_print", (
        "manual Fire fired the 3D-print step despite the printing condition "
        "being off; conditions must be checked on the manual path too"
    )
    assert page_id == "todo", (
        f"manual Fire should wrap to step 0 (todo) when step 1 is gated; got {page_id}"
    )


def test_projection_covers_full_window_for_short_dwells(app: Flask) -> None:
    """Regression for the projection-bar gap: the old hard-coded 200
    iter cap meant short-dwell rotations only filled ~70% of the 24h
    timeline. The iteration cap now scales with the window so every
    realistic rotation fills the bar."""
    rotation = Rotation(
        id="trmnl",
        name="TRMNL Rotation",
        anchor="00:00",
        steps=[
            RotationStep(page_id="home", dwell_minutes=5),
            RotationStep(page_id="home", dwell_minutes=5),
            RotationStep(page_id="home", dwell_minutes=5),
            RotationStep(page_id="home", dwell_minutes=5),
        ],
    )
    with app.app_context():
        proj = _build_projection(rotation, total_minutes=24 * 60)
    bands = proj["bands"]
    assert bands, "projection produced no bands"
    last = bands[-1]
    coverage_minutes = last["start_min"] + last["dwell_minutes"]
    # Within one step of the full window is "filled" for our purposes.
    assert coverage_minutes >= 24 * 60 - 5, (
        f"projection only covers {coverage_minutes} of {24 * 60} minutes; "
        "likely hit the iter cap mid-cycle"
    )
