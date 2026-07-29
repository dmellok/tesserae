"""Companion API Phase 2: write routes, jobs, and idempotency.

Exercises the async write path end to end through the real Flask app and
the real job runner + idempotency ledger, but with a fake PushManager
swapped into ``app.config`` so no Playwright render or panel publish
happens. That keeps the tests fast and deterministic while still proving
the lifecycle (accepted -> running -> terminal), the business outcome
mapping (published / quiet), Idempotency-Key replay + conflict, and the
upload guards (415 / 413).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest
from flask import Flask

from app import companion_api
from app.main import REPO_ROOT, create_app

from ._schema import schema_for

_FMT = jsonschema.FormatChecker()


class _FakePush:
    """Stand-in for PushManager: records calls, returns a canned status."""

    def __init__(self, status: str = "sent") -> None:
        self.status = status
        self.push_calls: list[dict[str, Any]] = []
        self.image_calls: list[dict[str, Any]] = []
        self._next_event_id = 100

    def push(self, page_id: str, **kwargs: Any) -> Any:
        self.push_calls.append({"page_id": page_id, "device_ids": set(kwargs["device_ids"])})
        self._next_event_id += 1
        return SimpleNamespace(status=self.status, error=None, event_id=self._next_event_id)

    def push_image(self, image_bytes: bytes, **kwargs: Any) -> Any:
        self.image_calls.append({"device_id": kwargs["device_id"], "fit": kwargs["fit"]})
        self._next_event_id += 1
        return SimpleNamespace(status=self.status, error=None, event_id=self._next_event_id)


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


def _install_fake_push(app: Flask, status: str = "sent") -> _FakePush:
    fake = _FakePush(status=status)
    app.config["PUSH_MANAGER"] = fake
    return fake


_CLIENT = {
    "name": "Test iPhone",
    "platform": "ios",
    "app_version": "0.1.0",
    "installation_id": "A1B2C3D4-E5F6-47A8-9012-3456789ABCDE",
}


def _token(app: Flask) -> str:
    code = app.config["COMPANION_PAIRING_STORE"].issue(note="t").code
    resp = app.test_client().post(
        "/api/app/v1/pair",
        data=json.dumps({"code": code, "client": _CLIENT}),
        content_type="application/json",
    )
    return str(resp.get_json()["token"])


def _seed_device(app: Flask, device_id: str = "kitchen") -> str:
    code = app.config["PAIRING_STORE"].issue(note="d").code
    resp = app.test_client().post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": device_id,
                "kind": "pico_bin_client",
                "panel_w": 1600,
                "panel_h": 1200,
                "fw_version": "1.8.0",
            }
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return device_id


def _seed_page(app: Flask, device_ids: list[str], page_id: str = "pantry") -> str:
    from app.state.page_store import Page

    app.config["PAGE_STORE"].save(
        Page(id=page_id, name="Pantry", layout_kind="grid", device_ids=device_ids)
    )
    return page_id


def _auth(token: str, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


def _poll(app: Flask, token: str, job_id: str, timeout_s: float = 5.0) -> dict[str, Any]:
    """Poll a job to a terminal state; validate every response shape."""
    deadline = time.time() + timeout_s
    client = app.test_client()
    while time.time() < deadline:
        resp = client.get(f"/api/app/v1/jobs/{job_id}", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.get_json()
        jsonschema.validate(body, schema_for("JobResponse"), format_checker=_FMT)
        if body["job"]["status"] in ("succeeded", "failed"):
            return body["job"]
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach a terminal state in {timeout_s}s")


def _png_bytes() -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (12, 8), (200, 50, 50)).save(buf, "PNG")
    return buf.getvalue()


# -- dashboard push ------------------------------------------------------


def test_dashboard_push_creates_job_that_publishes(app: Flask) -> None:
    fake = _install_fake_push(app)
    device = _seed_device(app)
    _seed_page(app, [device])
    token = _token(app)

    resp = app.test_client().post(
        "/api/app/v1/dashboards/pantry/push",
        data=json.dumps({"override_quiet_hours": True}),
        content_type="application/json",
        headers=_auth(token, "idem-dashboard-0001"),
    )
    assert resp.status_code == 202, resp.get_data(as_text=True)
    body = resp.get_json()
    jsonschema.validate(body, schema_for("JobResponse"), format_checker=_FMT)
    assert body["job"]["kind"] == "dashboard_push"
    assert resp.headers["Location"] == f"/api/app/v1/jobs/{body['job']['id']}"
    assert resp.headers["Retry-After"] == "2"

    job = _poll(app, token, body["job"]["id"])
    assert job["status"] == "succeeded"
    assert job["result"]["status"] == "published"
    assert job["result"]["device_ids"] == [device]
    assert job["result"]["history_event_ids"] == ["101"]
    assert fake.push_calls == [{"page_id": "pantry", "device_ids": {device}}]


def test_dashboard_push_unknown_dashboard_is_404(app: Flask) -> None:
    _install_fake_push(app)
    token = _token(app)
    resp = app.test_client().post(
        "/api/app/v1/dashboards/nope/push",
        data=json.dumps({"override_quiet_hours": False}),
        content_type="application/json",
        headers=_auth(token, "idem-missing-0001"),
    )
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"


def test_dashboard_push_requires_idempotency_key(app: Flask) -> None:
    _install_fake_push(app)
    _seed_page(app, [_seed_device(app)])
    token = _token(app)
    resp = app.test_client().post(
        "/api/app/v1/dashboards/pantry/push",
        data=json.dumps({"override_quiet_hours": True}),
        content_type="application/json",
        headers=_auth(token),
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_request"


def test_dashboard_push_unknown_target_is_invalid_target(app: Flask) -> None:
    _install_fake_push(app)
    _seed_page(app, [_seed_device(app)])
    token = _token(app)
    resp = app.test_client().post(
        "/api/app/v1/dashboards/pantry/push",
        data=json.dumps({"device_ids": ["ghost"], "override_quiet_hours": True}),
        content_type="application/json",
        headers=_auth(token, "idem-badtarget-01"),
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_target"


def test_dashboard_push_requires_push_scope(app: Flask) -> None:
    # A companion token without push:write can't push. Revoke-and-reissue
    # isn't needed; issue a token then strip the scope on the record.
    _install_fake_push(app)
    _seed_page(app, [_seed_device(app)])
    token = _token(app)
    store = app.config["COMPANION_TOKENS"]
    store.list_active()[0].scopes = ["devices:read", "dashboards:read"]
    resp = app.test_client().post(
        "/api/app/v1/dashboards/pantry/push",
        data=json.dumps({"override_quiet_hours": True}),
        content_type="application/json",
        headers=_auth(token, "idem-scope-00001"),
    )
    assert resp.status_code == 401
    assert resp.get_json()["error"]["code"] == "unauthorized"


# -- quiet hours ---------------------------------------------------------


def test_quiet_hours_hold_yields_a_successful_quiet_outcome(app: Flask) -> None:
    fake = _install_fake_push(app)
    device = _seed_device(app)
    _seed_page(app, [device])
    # A window (in UTC) that brackets now, robust across the midnight wrap.
    now = datetime.now(UTC)
    app.config["SETTINGS_STORE"].patch_section(
        "app",
        {
            "quiet_hours_enabled": True,
            "quiet_hours_start": (now - timedelta(minutes=5)).strftime("%H:%M"),
            "quiet_hours_end": (now + timedelta(minutes=5)).strftime("%H:%M"),
        },
    )
    token = _token(app)
    resp = app.test_client().post(
        "/api/app/v1/dashboards/pantry/push",
        data=json.dumps({"override_quiet_hours": False}),
        content_type="application/json",
        headers=_auth(token, "idem-quiet-000001"),
    )
    assert resp.status_code == 202
    job = _poll(app, token, resp.get_json()["job"]["id"])
    assert job["status"] == "succeeded"
    assert job["result"]["status"] == "quiet"
    assert job["result"]["reason"] == "all_targets_in_quiet_hours"
    # Held for quiet hours means nothing was published.
    assert fake.push_calls == []


# -- idempotency ---------------------------------------------------------


def _push_dashboard(app: Flask, token: str, idem: str, body: dict[str, Any]) -> Any:
    return app.test_client().post(
        "/api/app/v1/dashboards/pantry/push",
        data=json.dumps(body),
        content_type="application/json",
        headers=_auth(token, idem),
    )


def test_idempotent_resubmit_returns_the_same_job(app: Flask) -> None:
    fake = _install_fake_push(app)
    _seed_page(app, [_seed_device(app)])
    token = _token(app)
    body = {"override_quiet_hours": True}
    first = _push_dashboard(app, token, "idem-replay-00001", body)
    second = _push_dashboard(app, token, "idem-replay-00001", body)
    assert first.status_code == 202 and second.status_code == 202
    assert first.get_json()["job"]["id"] == second.get_json()["job"]["id"]
    _poll(app, token, first.get_json()["job"]["id"])
    # The work only ran once despite two submissions.
    assert len(fake.push_calls) == 1


def test_reused_key_with_different_payload_conflicts(app: Flask) -> None:
    _install_fake_push(app)
    device = _seed_device(app)
    _seed_page(app, [device])
    token = _token(app)
    first = _push_dashboard(app, token, "idem-conflict-001", {"override_quiet_hours": True})
    assert first.status_code == 202
    second = _push_dashboard(
        app, token, "idem-conflict-001", {"device_ids": [device], "override_quiet_hours": True}
    )
    assert second.status_code == 409
    assert second.get_json()["error"]["code"] == "idempotency_conflict"


# -- image push ----------------------------------------------------------


@pytest.mark.parametrize("fit", companion_api.IMAGE_FIT_MODES)
def test_image_push_accepts_every_advertised_fit_mode(app: Flask, fit: str) -> None:
    fake = _install_fake_push(app)
    device = _seed_device(app)
    token = _token(app)
    data = {
        "image": (BytesIO(_png_bytes()), "photo.png", "image/png"),
        "request": json.dumps({"device_ids": [device], "fit": fit, "override_quiet_hours": True}),
    }
    resp = app.test_client().post(
        "/api/app/v1/images",
        data=data,
        content_type="multipart/form-data",
        headers=_auth(token, "idem-image-000001"),
    )
    assert resp.status_code == 202, resp.get_data(as_text=True)
    body = resp.get_json()
    jsonschema.validate(body, schema_for("JobResponse"), format_checker=_FMT)
    assert body["job"]["kind"] == "image_push"

    job = _poll(app, token, body["job"]["id"])
    assert job["status"] == "succeeded"
    assert job["result"]["status"] == "published"
    assert job["result"]["device_ids"] == [device]
    assert job["result"]["history_event_ids"] == ["101"]
    assert fake.image_calls == [{"device_id": device, "fit": fit}]


@pytest.mark.parametrize("invalid_fit", ["tile", ["fit"]], ids=["unknown", "non-string"])
def test_image_push_rejects_unadvertised_fit_mode(app: Flask, invalid_fit: Any) -> None:
    _install_fake_push(app)
    device = _seed_device(app)
    token = _token(app)
    data = {
        "image": (BytesIO(_png_bytes()), "photo.png", "image/png"),
        "request": json.dumps(
            {"device_ids": [device], "fit": invalid_fit, "override_quiet_hours": True}
        ),
    }
    resp = app.test_client().post(
        "/api/app/v1/images",
        data=data,
        content_type="multipart/form-data",
        headers=_auth(token, "idem-image-invalid-fit"),
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_request"


def test_image_push_rejects_unsupported_media_type(app: Flask) -> None:
    _install_fake_push(app)
    device = _seed_device(app)
    token = _token(app)
    data = {
        "image": (BytesIO(b"GIF89a"), "x.gif", "image/gif"),
        "request": json.dumps({"device_ids": [device], "fit": "fit", "override_quiet_hours": True}),
    }
    resp = app.test_client().post(
        "/api/app/v1/images",
        data=data,
        content_type="multipart/form-data",
        headers=_auth(token, "idem-gif-0000001"),
    )
    assert resp.status_code == 415
    assert resp.get_json()["error"]["code"] == "unsupported_image"


def test_image_push_rejects_oversized_upload(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_push(app)
    device = _seed_device(app)
    token = _token(app)
    # Force the byte cap low so a normal PNG trips it.
    monkeypatch.setattr(companion_api, "IMAGE_UPLOAD_BYTES", 4)
    data = {
        "image": (BytesIO(_png_bytes()), "photo.png", "image/png"),
        "request": json.dumps(
            {"device_ids": [device], "fit": "fill", "override_quiet_hours": True}
        ),
    }
    resp = app.test_client().post(
        "/api/app/v1/images",
        data=data,
        content_type="multipart/form-data",
        headers=_auth(token, "idem-big-0000001"),
    )
    assert resp.status_code == 413
    assert resp.get_json()["error"]["code"] == "image_too_large"


# -- jobs ----------------------------------------------------------------


def test_unknown_job_is_404(app: Flask) -> None:
    token = _token(app)
    resp = app.test_client().get("/api/app/v1/jobs/job_missing", headers=_auth(token))
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"
