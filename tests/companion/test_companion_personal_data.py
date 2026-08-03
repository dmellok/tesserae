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


def _multi_list_snapshot(
    generated: datetime,
    *,
    ttl_hours: int = 47,
    lists: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if lists is None:
        lists = [
            {
                "id": "2b684174-fc12-4a28-9e6b-1ef61984e57e",
                "title": "Grocery List",
                "items": [
                    {
                        "id": "reminder-yogurt",
                        "title": "Yogurt",
                        "due_date": "2026-08-03",
                        "priority": "high",
                        "completed": False,
                    }
                ],
            },
            {
                "id": "b7b355f7-ce24-431b-b307-998f948613a5",
                "title": "Weekend",
                "items": [
                    {
                        "id": "reminder-clean-grill",
                        "title": "Clean grill",
                        "due_date": None,
                        "priority": "none",
                        "completed": False,
                    }
                ],
            },
        ]
    return {
        "version": "personal_data_bridge_v1",
        "source_id": "reminders",
        "generated_at": _iso(generated),
        "expires_at": _iso(generated + timedelta(hours=ttl_hours)),
        "data": {"lists": lists},
    }


def _put(app: Flask, token: str, snap: dict[str, Any]) -> Any:
    return app.test_client().put(
        "/api/app/v1/personal-data/reminders.fridge",
        headers=_auth(token),
        data=json.dumps(snap),
    )


def _put_multi(app: Flask, token: str, snap: dict[str, Any]) -> Any:
    return app.test_client().put(
        "/api/app/v1/personal-data/reminders",
        headers=_auth(token),
        data=json.dumps(snap),
    )


def test_capabilities_advertise_personal_data(app: Flask) -> None:
    body = app.test_client().get("/api/app/v1").get_json()
    jsonschema.validate(body, schema_for("Capabilities"), format_checker=_FMT)
    assert "personal_data_reminders" in body["features"]
    assert body["personal_data"]["sources"] == ["reminders", "reminders.fridge"]
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


def test_multi_list_put_then_status_round_trip(app: Flask) -> None:
    token = _token(app)
    now = datetime.now(UTC).replace(microsecond=0)
    resp = _put_multi(app, token, _multi_list_snapshot(now))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    status = resp.get_json()
    jsonschema.validate(status, schema_for("PersonalDataSourceStatus"), format_checker=_FMT)
    assert status["source_id"] == "reminders"
    assert status["state"] == "fresh"

    listing = app.test_client().get("/api/app/v1/personal-data/status", headers=_auth(token))
    assert listing.status_code == 200
    body = listing.get_json()
    jsonschema.validate(body, schema_for("PersonalDataStatusResponse"), format_checker=_FMT)
    assert [source["source_id"] for source in body["sources"]] == ["reminders"]


def test_empty_list_set_is_a_fresh_enabled_snapshot(app: Flask) -> None:
    token = _token(app)
    now = datetime.now(UTC).replace(microsecond=0)
    response = _put_multi(app, token, _multi_list_snapshot(now, lists=[]))

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["state"] == "fresh"
    record = app.config["PERSONAL_DATA_STORE"].get("reminders")
    assert record is not None
    assert record["snapshot"]["data"]["lists"] == []

    status = app.test_client().get(
        "/api/app/v1/personal-data/status",
        headers=_auth(token),
    )
    assert status.status_code == 200
    assert status.get_json()["sources"][0]["state"] == "fresh"


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

    invalid_due_date = _snapshot(now)
    invalid_due_date["data"]["items"][0]["due_date"] = "2026-02-30"
    r = _put(app, token, invalid_due_date)
    assert r.status_code == 400
    assert r.get_json()["error"]["message"] == "item due_date must be an ISO date or null"

    missing_due_date = _snapshot(now)
    del missing_due_date["data"]["items"][0]["due_date"]
    r = _put(app, token, missing_due_date)
    assert r.status_code == 400
    assert r.get_json()["error"]["message"] == "item due_date is required"

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


def test_multi_list_validation_is_strict_and_bounded(app: Flask) -> None:
    token = _token(app)
    now = datetime.now(UTC).replace(microsecond=0)

    duplicate_lists = _multi_list_snapshot(now)
    duplicate_lists["data"]["lists"][1]["id"] = duplicate_lists["data"]["lists"][0]["id"]
    response = _put_multi(app, token, duplicate_lists)
    assert response.status_code == 400
    assert response.get_json()["error"]["message"] == "list ids must be unique"

    too_many_lists = _multi_list_snapshot(
        now,
        lists=[
            {"id": f"list-{index}", "title": f"List {index}", "items": []} for index in range(21)
        ],
    )
    response = _put_multi(app, token, too_many_lists)
    assert response.status_code == 400
    assert response.get_json()["error"]["message"] == "too many lists (max 20)"

    item = {
        "id": "reminder-id",
        "title": "Reminder",
        "due_date": None,
        "priority": "none",
        "completed": False,
    }
    too_many_items = _multi_list_snapshot(
        now,
        lists=[
            {"id": "list-a", "title": "A", "items": [item | {"id": f"a-{i}"} for i in range(101)]},
            {"id": "list-b", "title": "B", "items": [item | {"id": f"b-{i}"} for i in range(100)]},
        ],
    )
    response = _put_multi(app, token, too_many_items)
    assert response.status_code == 400
    assert response.get_json()["error"]["message"] == "too many items across all lists (max 200)"

    unexpected = _multi_list_snapshot(now)
    unexpected["data"]["lists"][0]["eventkit_id"] = "must-not-be-uploaded"
    response = _put_multi(app, token, unexpected)
    assert response.status_code == 400
    assert response.get_json()["error"]["message"] == "a list has unexpected fields"

    invalid_due_date = _multi_list_snapshot(now)
    invalid_due_date["data"]["lists"][0]["items"][0]["due_date"] = "tomorrow"
    response = _put_multi(app, token, invalid_due_date)
    assert response.status_code == 400
    assert response.get_json()["error"]["message"] == "item due_date must be an ISO date or null"

    missing_due_date = _multi_list_snapshot(now)
    del missing_due_date["data"]["lists"][0]["items"][0]["due_date"]
    response = _put_multi(app, token, missing_due_date)
    assert response.status_code == 400
    assert response.get_json()["error"]["message"] == "item due_date is required"


def test_expiry_redacts_values_but_preserves_metadata_state(app: Flask) -> None:
    token = _token(app)
    now = datetime.now(UTC).replace(microsecond=0)
    store = app.config["PERSONAL_DATA_STORE"]
    store.put(
        "reminders",
        snapshot=_multi_list_snapshot(now - timedelta(hours=49)),
        generated_epoch=(now - timedelta(hours=49)).timestamp(),
        expires_epoch=(now - timedelta(hours=1)).timestamp(),
    )

    response = app.test_client().get(
        "/api/app/v1/personal-data/status",
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert response.get_json()["sources"][0]["state"] == "expired"
    record = store.get("reminders")
    assert record is not None
    assert record["expired"] is True
    assert "snapshot" not in record


def test_legacy_and_multi_list_sources_coexist_and_delete_independently(app: Flask) -> None:
    token = _token(app)
    now = datetime.now(UTC).replace(microsecond=0)
    assert _put(app, token, _snapshot(now)).status_code == 200
    assert _put_multi(app, token, _multi_list_snapshot(now)).status_code == 200

    status = (
        app.test_client().get("/api/app/v1/personal-data/status", headers=_auth(token)).get_json()
    )
    assert [source["source_id"] for source in status["sources"]] == [
        "reminders",
        "reminders.fridge",
    ]

    response = app.test_client().delete("/api/app/v1/personal-data/reminders", headers=_auth(token))
    assert response.status_code == 204
    status = (
        app.test_client().get("/api/app/v1/personal-data/status", headers=_auth(token)).get_json()
    )
    assert [source["source_id"] for source in status["sources"]] == ["reminders.fridge"]


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
