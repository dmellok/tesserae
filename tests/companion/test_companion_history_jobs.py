"""Companion 0.4 History resend job persistence and correlation."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from flask import Flask

from app.companion_jobs import CompanionJobs, JobOutcome
from app.state.job_store import JobStore


def _wait_for_terminal(store: JobStore, job_id: str, timeout_s: float = 2.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        job = store.get(job_id)
        if job is not None and job.terminal:
            return
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach a terminal state")


def test_history_resend_job_persists_correlated_event_ids(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    store = JobStore(path)
    job = store.create(
        kind="history_resend",
        target_device_ids=["kitchen"],
        label="Shared photo",
    )

    store.mark_succeeded(
        job.id,
        result_status="published",
        device_ids=["kitchen"],
        history_event_ids=["42", "42", "43"],
    )

    # Read through a fresh store to prove the 0.4 result survives the
    # file-backed boundary used by a later Companion job poll.
    reloaded = JobStore(path).get(job.id)
    assert reloaded is not None
    assert reloaded.kind == "history_resend"
    assert reloaded.result == {
        "status": "published",
        "reason": None,
        "device_ids": ["kitchen"],
        "history_event_ids": ["42", "43"],
    }


def test_pre_04_job_without_history_ids_remains_compatible(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    # Timestamps are anchored to "now" so the record stays inside the 24h
    # retention window regardless of the calendar date the suite runs on; the
    # test is about parsing a pre-0.4 record shape, not retention sweeping.
    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "job_legacy",
                        "kind": "image_push",
                        "status": "succeeded",
                        "target_device_ids": ["kitchen"],
                        "created_at": created,
                        "updated_at": created,
                        "label": "Shared photo",
                        "result": {
                            "status": "published",
                            "reason": None,
                            "device_ids": ["kitchen"],
                        },
                        "error": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    job = JobStore(path).get("job_legacy")
    assert job is not None
    assert job.public_dict()["result"] == {
        "status": "published",
        "reason": None,
        "device_ids": ["kitchen"],
    }


def test_async_runner_carries_exact_republish_event_id(tmp_path: Path) -> None:
    app = Flask(__name__)
    store = JobStore(tmp_path / "jobs.json")
    runner = CompanionJobs(app, store, max_workers=1)
    job = store.create(
        kind="history_resend",
        target_device_ids=["kitchen"],
        label="Shared photo",
    )

    try:
        # The resend route supplies PushManager.republish(...).event_id here;
        # the runner must preserve that exact canonical EventLog ID.
        runner.enqueue(
            job.id,
            lambda: JobOutcome.published(
                ["kitchen"],
                history_event_ids=["731"],
            ),
        )
        _wait_for_terminal(store, job.id)
    finally:
        runner.shutdown()

    completed = store.get(job.id)
    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.result is not None
    assert completed.result["history_event_ids"] == ["731"]


def test_existing_published_outcome_omits_optional_history_ids(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.json")
    job = store.create(
        kind="dashboard_push",
        target_device_ids=["kitchen"],
        label="Pantry",
    )
    store.mark_succeeded(
        job.id,
        result_status="published",
        device_ids=["kitchen"],
    )

    completed = store.get(job.id)
    assert completed is not None
    assert completed.result is not None
    assert "history_event_ids" not in completed.result
