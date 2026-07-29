"""Live route coverage for Companion 0.4 History and resend."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest
from flask import Flask

from app.companion_jobs import JobOutcome
from app.main import REPO_ROOT, create_app

from ._schema import schema_for
from .test_companion_api import _seed_device, _token

_DIGEST = "0123456789abcdef"
_IDEMPOTENCY_KEY = "history-resend-test-key"


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    instance = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    instance.config["TESTING"] = True
    return instance


def _auth(app: Flask) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(app)}"}


def _record_history(app: Flask, *, device_id: str, fit: str = "blur") -> int:
    renders = Path(app.config["RENDERS_DIR"])
    renders.mkdir(parents=True, exist_ok=True)
    (renders / f"{_DIGEST}.png").write_bytes(b"\x89PNG\r\n\x1a\nhistory")
    return int(
        app.config["EVENT_LOG"].record(
            type="push",
            source="companion",
            target="Shared photo",
            status="sent",
            digest=_DIGEST,
            duration_s=1.25,
            extra={"device_ids": [device_id], "fit": fit},
        )
    )


class _InlineJobs:
    """Run route work immediately while preserving the real JobStore shape."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def enqueue(self, job_id: str, work: Any) -> None:
        self._store.mark_running(job_id)
        outcome: JobOutcome = work()
        if outcome.ok:
            self._store.mark_succeeded(
                job_id,
                result_status=outcome.result_status,
                device_ids=list(outcome.device_ids),
                reason=outcome.reason,
                history_event_ids=(
                    list(outcome.history_event_ids)
                    if outcome.history_event_ids is not None
                    else None
                ),
            )
        else:
            self._store.mark_failed(job_id, code=outcome.code, message=outcome.message)


class _Republisher:
    def __init__(self, app: Flask) -> None:
        self._app = app
        self.calls: list[tuple[int, set[str]]] = []

    def republish(self, event_id: int, *, device_ids: set[str]) -> Any:
        self.calls.append((event_id, device_ids))
        original = self._app.config["EVENT_LOG"].get(event_id)
        assert original is not None
        new_id = self._app.config["EVENT_LOG"].record(
            type="push",
            source="resend",
            target=original.target,
            status="sent",
            digest=original.digest,
            extra={
                "device_ids": sorted(device_ids),
                "fit": original.extra.get("fit"),
            },
        )
        return SimpleNamespace(status="sent", error=None, event_id=new_id)


def test_history_list_and_preview_are_authenticated_and_contract_valid(app: Flask) -> None:
    device_id = _seed_device(app)
    event_id = _record_history(app, device_id=device_id)
    client = app.test_client()

    assert client.get("/api/app/v1/history").status_code == 401
    auth = _auth(app)
    listing = client.get("/api/app/v1/history?limit=30", headers=auth)
    assert listing.status_code == 200
    body = listing.get_json()
    jsonschema.validate(body, schema_for("HistoryResponse"))
    item = body["items"][0]
    assert item["id"] == str(event_id)
    assert item["fit"] == "blur"
    assert item["preview_available"] is True
    assert item["resendable"] is True

    preview = client.get(f"/api/app/v1/history/{event_id}/preview", headers=auth)
    assert preview.status_code == 200
    assert preview.mimetype == "image/png"
    assert preview.headers["Cache-Control"] == "private, no-cache"
    etag = preview.headers["ETag"]
    cached = client.get(
        f"/api/app/v1/history/{event_id}/preview",
        headers={**auth, "If-None-Match": etag},
    )
    assert cached.status_code == 304
    assert cached.headers["ETag"] == etag


@pytest.mark.parametrize("query", ["?limit=0", "?limit=101", "?limit=nope", "?before_id=01"])
def test_history_list_rejects_invalid_pagination(app: Flask, query: str) -> None:
    response = app.test_client().get(f"/api/app/v1/history{query}", headers=_auth(app))
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_request"


def test_history_resend_preserves_targets_and_reports_exact_new_event(app: Flask) -> None:
    device_id = _seed_device(app)
    event_id = _record_history(app, device_id=device_id, fit="center")
    republisher = _Republisher(app)
    app.config["PUSH_MANAGER"] = republisher
    app.config["COMPANION_JOBS"] = _InlineJobs(app.config["JOB_STORE"])
    client = app.test_client()
    auth = {
        **_auth(app),
        "Idempotency-Key": _IDEMPOTENCY_KEY,
        "Content-Type": "application/json",
    }

    response = client.post(
        f"/api/app/v1/history/{event_id}/resend",
        headers=auth,
        data=json.dumps({"override_quiet_hours": False}),
    )

    assert response.status_code == 202
    body = response.get_json()
    jsonschema.validate(body, schema_for("JobResponse"))
    job = body["job"]
    assert job["kind"] == "history_resend"
    assert job["status"] == "succeeded"
    assert job["result"]["status"] == "published"
    assert job["result"]["device_ids"] == [device_id]
    new_event_id = job["result"]["history_event_ids"][0]
    assert int(new_event_id) > event_id
    assert republisher.calls == [(event_id, {device_id})]
    replay = app.config["EVENT_LOG"].get(int(new_event_id))
    assert replay is not None
    assert replay.source == "resend"
    assert replay.extra["fit"] == "center"

    # An uncertain client retry resolves to the same terminal job and does not
    # refresh the e-ink display twice.
    retried = client.post(
        f"/api/app/v1/history/{event_id}/resend",
        headers=auth,
        data=json.dumps({"override_quiet_hours": False}),
    )
    assert retried.status_code == 202
    assert retried.get_json()["job"]["id"] == job["id"]
    assert republisher.calls == [(event_id, {device_id})]


def test_history_resend_respects_quiet_hours(app: Flask) -> None:
    device_id = _seed_device(app)
    event_id = _record_history(app, device_id=device_id)
    republisher = _Republisher(app)
    app.config["PUSH_MANAGER"] = republisher
    app.config["COMPANION_JOBS"] = _InlineJobs(app.config["JOB_STORE"])
    now = datetime.now(UTC)
    app.config["SETTINGS_STORE"].patch_section(
        "app",
        {
            "quiet_hours_enabled": True,
            "quiet_hours_start": (now - timedelta(minutes=5)).strftime("%H:%M"),
            "quiet_hours_end": (now + timedelta(minutes=5)).strftime("%H:%M"),
        },
    )

    response = app.test_client().post(
        f"/api/app/v1/history/{event_id}/resend",
        headers={
            **_auth(app),
            "Idempotency-Key": _IDEMPOTENCY_KEY,
            "Content-Type": "application/json",
        },
        data=json.dumps({"override_quiet_hours": False}),
    )

    assert response.status_code == 202
    result = response.get_json()["job"]["result"]
    assert result == {
        "status": "quiet",
        "reason": "all_targets_in_quiet_hours",
        "device_ids": [device_id],
    }
    assert republisher.calls == []


def test_history_resend_rejects_rows_without_retained_target_snapshot(app: Flask) -> None:
    event_id = app.config["EVENT_LOG"].record(
        type="push",
        source="file",
        target="legacy.png",
        status="sent",
        digest=_DIGEST,
        extra={},
    )
    response = app.test_client().post(
        f"/api/app/v1/history/{event_id}/resend",
        headers={
            **_auth(app),
            "Idempotency-Key": _IDEMPOTENCY_KEY,
            "Content-Type": "application/json",
        },
        data=json.dumps({"override_quiet_hours": False}),
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "not_resendable"
