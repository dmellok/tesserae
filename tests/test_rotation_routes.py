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
from app.state.conditions import Condition
from app.state.rotation_model import Rotation, RotationStep
from app.state.rotation_store import RotationStore
from app.state.schedule_model import Schedule
from app.state.schedule_store import ScheduleStore


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
    store = RotationStore(tmp_path / "core" / "rotations.json")
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
    resp = client.get("/rotations?edit=evening")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    body = resp.get_data(as_text=True)
    # The textarea should carry the serialised condition.
    assert "time_window" in body
    assert "start_local" in body


def test_schedule_index_renders_with_condition(app: Flask, tmp_path: Path) -> None:
    """Same shape, schedule edition: the schedules.html template uses
    ``s.conditions | tojson`` to seed its conditions textarea."""
    store = ScheduleStore(tmp_path / "core" / "schedules.json")
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
    resp = client.get("/schedules?edit=morning")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    body = resp.get_data(as_text=True)
    assert "after_sunset" in body
