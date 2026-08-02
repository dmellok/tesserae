"""Companion contract 0.7 personal-data bridge: capability, snapshot PUT with
ordering, metadata-only status, and delete. Runs against the real Flask app;
snapshots use dynamic timestamps so freshness is deterministic."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app

from ._schema import schema_for

_FMT = jsonschema.FormatChecker()

_CLIENT = {
    "name": "Test iPhone",
    "platform": "ios",
    "app_version": "0.1.0",
    "installation_id": "A1B2C3D4-E5F6-47A8-9012-3456789ABCDE",
}


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


def _token(app: Flask) -> str:
    code = app.config["COMPANION_PAIRING_STORE"].issue(note="t").code
    resp = app.test_client().post(
        "/api/app/v1/pair",
        data=json.dumps({"code": code, "client": _CLIENT}),
        content_type="application/json",
    )
    return str(resp.get_json()["token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _snapshot(generated: datetime, *, ttl_hours: int = 47, title: str = "Yogurt") -> dict[str, Any]:
    return {
        "version": "personal_data_bridge_v1",
        "source_id": "reminders.fridge",
        "generated_at": _iso(generated),
        "expires_at": _iso(generated + timedelta(hours=ttl_hours)),
        "data": {
            "items": [
                {
                    "id": "reminder-yogurt",
                    "title": title,
                    "due_date": "2026-08-02",
                    "priority": "high",
                    "completed": False,
                }
            ]
        },
    }


def _put(app: Flask, token: str, snap: dict[str, Any]) -> Any:
    return app.test_client().put(
        "/api/app/v1/personal-data/reminders.fridge",
        headers=_auth(token),
        data=json.dumps(snap),
    )


def test_capabilities_advertise_personal_data(app: Flask) -> None:
    body = app.test_client().get("/api/app/v1").get_json()
    jsonschema.validate(body, schema_for("Capabilities"), format_checker=_FMT)
    assert "personal_data_reminders" in body["features"]
    assert body["limits"]["personal_data_stale_after_seconds"] == 86400
    assert body["limits"]["personal_data_max_ttl_seconds"] == 172800


def test_put_then_status_round_trip(app: Flask) -> None:
    token = _token(app)
    now = datetime.now(UTC).replace(microsecond=0)
    resp = _put(app, token, _snapshot(now))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    status = resp.get_json()
    jsonschema.validate(status, schema_for("PersonalDataSourceStatus"), format_checker=_FMT)
    assert status["source_id"] == "reminders.fridge"
    assert status["state"] == "fresh"

    listing = app.test_client().get("/api/app/v1/personal-data/status", headers=_auth(token))
    assert listing.status_code == 200
    body = listing.get_json()
    jsonschema.validate(body, schema_for("PersonalDataStatusResponse"), format_checker=_FMT)
    assert [s["source_id"] for s in body["sources"]] == ["reminders.fridge"]
    assert body["sources"][0]["state"] == "fresh"


def test_put_ordering(app: Flask) -> None:
    token = _token(app)
    base = datetime.now(UTC).replace(microsecond=0)
    assert _put(app, token, _snapshot(base)).status_code == 200

    # Older timestamp is refused.
    older = _put(app, token, _snapshot(base - timedelta(hours=1)))
    assert older.status_code == 409
    assert older.get_json()["error"]["code"] == "snapshot_out_of_order"

    # Same timestamp, different payload is a conflict.
    conflict = _put(app, token, _snapshot(base, title="Milk"))
    assert conflict.status_code == 409
    assert conflict.get_json()["error"]["code"] == "snapshot_conflict"

    # Same timestamp, identical payload is idempotent.
    assert _put(app, token, _snapshot(base)).status_code == 200

    # A newer snapshot replaces it.
    assert _put(app, token, _snapshot(base + timedelta(hours=1), title="Eggs")).status_code == 200


def test_validation_and_unknown_source(app: Flask) -> None:
    token = _token(app)
    now = datetime.now(UTC).replace(microsecond=0)

    bad_version = _snapshot(now) | {"version": "nope"}
    r = _put(app, token, bad_version)
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "invalid_snapshot"

    completed_item = _snapshot(now)
    completed_item["data"]["items"][0]["completed"] = True
    r = _put(app, token, completed_item)
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "invalid_snapshot"

    # ttl beyond the 48h max.
    r = _put(app, token, _snapshot(now, ttl_hours=72))
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "invalid_snapshot"

    # unknown source id.
    r = app.test_client().put(
        "/api/app/v1/personal-data/health.steps",
        headers=_auth(token),
        data=json.dumps(_snapshot(now)),
    )
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "unsupported_personal_data_source"


def test_delete_is_idempotent(app: Flask) -> None:
    token = _token(app)
    now = datetime.now(UTC).replace(microsecond=0)
    assert _put(app, token, _snapshot(now)).status_code == 200

    d = app.test_client().delete("/api/app/v1/personal-data/reminders.fridge", headers=_auth(token))
    assert d.status_code == 204
    body = (
        app.test_client().get("/api/app/v1/personal-data/status", headers=_auth(token)).get_json()
    )
    assert body["sources"] == []

    # Deleting again is still 204.
    d2 = app.test_client().delete(
        "/api/app/v1/personal-data/reminders.fridge", headers=_auth(token)
    )
    assert d2.status_code == 204


def test_unauthenticated_is_rejected(app: Flask) -> None:
    r = app.test_client().get("/api/app/v1/personal-data/status")
    assert r.status_code == 401
