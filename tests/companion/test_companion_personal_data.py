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

from app.data_change_refresh import DataChangeEvent
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


def _token(app: Flask, client: dict[str, str] | None = None) -> str:
    code = app.config["COMPANION_PAIRING_STORE"].issue(note="t").code
    resp = app.test_client().post(
        "/api/app/v1/pair",
        data=json.dumps({"code": code, "client": client or _CLIENT}),
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


def _health_snapshot(
    generated: datetime,
    *,
    ttl_hours: int = 47,
    sleep: dict[str, Any] | None = None,
) -> dict[str, Any]:
    window_end = generated.date()
    window_start = window_end - timedelta(days=6)
    return {
        "version": "personal_data_bridge_v1",
        "source_id": "health.summary",
        "generated_at": _iso(generated),
        "expires_at": _iso(generated + timedelta(hours=ttl_hours)),
        "data": {
            "time_zone": "UTC",
            "window_start_date": window_start.isoformat(),
            "window_end_date": window_end.isoformat(),
            "activity": None,
            "sleep": {"nights": []} if sleep is None else sleep,
            "workouts": None,
        },
    }


def _put_health(app: Flask, token: str, snap: dict[str, Any]) -> Any:
    return app.test_client().put(
        "/api/app/v1/personal-data/health.summary",
        headers=_auth(token),
        data=json.dumps(snap),
    )


def _health_contract_fixture() -> dict[str, Any]:
    fixture_path = (
        Path(__file__).parent / "contract" / "Fixtures" / "personal-data-health-summary.json"
    )
    return json.loads(fixture_path.read_text(encoding="utf-8"))


class _ChangeCapture:
    def __init__(self) -> None:
        self.events: list[DataChangeEvent] = []

    def notify(self, event: DataChangeEvent) -> None:
        self.events.append(event)


class _BrokenChangeCapture:
    def notify(self, event: DataChangeEvent) -> None:
        raise RuntimeError("coordinator unavailable")


def test_capabilities_advertise_personal_data(app: Flask) -> None:
    body = app.test_client().get("/api/app/v1").get_json()
    jsonschema.validate(body, schema_for("Capabilities"), format_checker=_FMT)
    assert "personal_data_reminders" in body["features"]
    assert "personal_data_health" in body["features"]
    assert "personal_data_retention" in body["features"]
    assert body["personal_data"]["sources"] == [
        "reminders",
        "reminders.fridge",
        "health.summary",
    ]
    assert body["limits"]["personal_data_stale_after_seconds"] == 86400
    assert body["limits"]["personal_data_max_ttl_seconds"] == 31_536_000


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


def test_health_summary_put_status_and_delete_round_trip(app: Flask) -> None:
    token = _token(app)
    now = datetime.now(UTC).replace(microsecond=0)
    snapshot = _health_snapshot(now)

    response = _put_health(app, token, snapshot)

    assert response.status_code == 200, response.get_data(as_text=True)
    status = response.get_json()
    jsonschema.validate(status, schema_for("PersonalDataSourceStatus"), format_checker=_FMT)
    assert status["source_id"] == "health.summary"
    assert status["state"] == "fresh"
    assert app.config["PERSONAL_DATA_STORE"].get("health.summary")["snapshot"] == snapshot

    listing = app.test_client().get("/api/app/v1/personal-data/status", headers=_auth(token))
    assert [source["source_id"] for source in listing.get_json()["sources"]] == ["health.summary"]

    deleted = app.test_client().delete(
        "/api/app/v1/personal-data/health.summary", headers=_auth(token)
    )
    assert deleted.status_code == 204
    assert (
        app.test_client()
        .get("/api/app/v1/personal-data/status", headers=_auth(token))
        .get_json()["sources"]
        == []
    )


def test_full_health_contract_fixture_is_accepted(app: Flask) -> None:
    token = _token(app)
    app.config["SETTINGS_STORE"].patch_section("app", {"timezone": "Asia/Shanghai"})
    snapshot = _health_contract_fixture()

    response = _put_health(app, token, snapshot)

    assert response.status_code == 200, response.get_data(as_text=True)
    jsonschema.validate(
        response.get_json(), schema_for("PersonalDataSourceStatus"), format_checker=_FMT
    )


def test_health_changes_are_source_wide_and_ttl_renewal_is_silent(app: Flask) -> None:
    token = _token(app)
    capture = _ChangeCapture()
    app.config["DATA_CHANGE_COORDINATOR"] = capture
    now = datetime.now(UTC).replace(microsecond=0)
    initial = _health_snapshot(now)

    assert _put_health(app, token, initial).status_code == 200
    assert capture.events == [DataChangeEvent("personal_data.health.summary")]

    capture.events.clear()
    renewal = _health_snapshot(now + timedelta(seconds=1))
    assert _put_health(app, token, renewal).status_code == 200
    assert capture.events == []

    capture.events.clear()
    changed = _health_snapshot(
        now + timedelta(seconds=2),
        sleep={
            "nights": [
                {
                    "wake_date": now.date().isoformat(),
                    "start_at": _iso(now - timedelta(hours=7)),
                    "end_at": _iso(now),
                    "in_bed_minutes": 420,
                    "asleep_minutes": 390,
                    "awake_minutes": 30,
                    "core_minutes": None,
                    "deep_minutes": None,
                    "rem_minutes": None,
                    "unspecified_minutes": 390,
                }
            ]
        },
    )
    assert _put_health(app, token, changed).status_code == 200
    assert capture.events == [DataChangeEvent("personal_data.health.summary")]

    capture.events.clear()
    response = app.test_client().delete(
        "/api/app/v1/personal-data/health.summary", headers=_auth(token)
    )
    assert response.status_code == 204
    assert capture.events == [DataChangeEvent("personal_data.health.summary")]


def test_health_validation_rejects_privacy_and_shape_violations(app: Flask) -> None:
    token = _token(app)
    app.config["SETTINGS_STORE"].patch_section("app", {"timezone": "Asia/Shanghai"})

    def rejected(snapshot: dict[str, Any]) -> Any:
        response = _put_health(app, token, snapshot)
        assert response.status_code == 400, response.get_data(as_text=True)
        assert response.get_json()["error"]["code"] == "invalid_snapshot"
        return response

    all_sections_null = _health_contract_fixture()
    all_sections_null["data"]["activity"] = None
    all_sections_null["data"]["sleep"] = None
    all_sections_null["data"]["workouts"] = None
    rejected(all_sections_null)

    wrong_timezone = _health_contract_fixture()
    wrong_timezone["data"]["time_zone"] = "UTC"
    rejected(wrong_timezone)

    unexpected_identity = _health_contract_fixture()
    unexpected_identity["data"]["workouts"]["items"][0]["healthkit_uuid"] = "private-id"
    response = rejected(unexpected_identity)
    assert "private-id" not in response.get_data(as_text=True)

    boolean_steps = _health_contract_fixture()
    boolean_steps["data"]["activity"]["days"][0]["steps"] = True
    rejected(boolean_steps)

    wrong_wake_date = _health_contract_fixture()
    wrong_wake_date["data"]["sleep"]["nights"][0]["wake_date"] = "2026-08-13"
    rejected(wrong_wake_date)

    invalid_activity_type = _health_contract_fixture()
    invalid_activity_type["data"]["workouts"]["items"][0]["activity_type"] = {"raw": "running"}
    rejected(invalid_activity_type)

    invalid_segment = _health_contract_fixture()
    workout = invalid_segment["data"]["workouts"]["items"][0]
    segment = {
        key: value
        for key, value in workout.items()
        if key not in {"id", "segments", "segments_truncated"}
    }
    segment["ordinal"] = 1
    workout["segments"] = [segment]
    rejected(invalid_segment)


def test_health_validation_enforces_ttl_window_and_request_size(app: Flask) -> None:
    token = _token(app)
    now = datetime.now(UTC).replace(microsecond=0)

    too_long = _put_health(app, token, _health_snapshot(now, ttl_hours=365 * 24 + 1))
    assert too_long.status_code == 400
    assert too_long.get_json()["error"]["code"] == "invalid_snapshot"

    wrong_window = _health_snapshot(now)
    wrong_window["data"]["window_start_date"] = (now.date() - timedelta(days=5)).isoformat()
    response = _put_health(app, token, wrong_window)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_snapshot"

    oversized = _health_snapshot(now)
    oversized["data"]["private_padding"] = "sensitive" * 40_000
    response = _put_health(app, token, oversized)
    assert response.status_code == 400
    error = response.get_json()["error"]
    assert error["code"] == "invalid_snapshot"
    assert error["message"] == "health.summary exceeds the 256 KiB limit"
    assert "sensitive" not in response.get_data(as_text=True)


@pytest.mark.parametrize("time_zone", ["..", "../../etc/passwd", "Nowhere/Nowhere"])
def test_health_time_zone_that_escapes_tzpath_is_a_400(app: Flask, time_zone: str) -> None:
    """``ZoneInfo`` raises ``ValueError`` (not ``ZoneInfoNotFoundError``) for a
    key that resolves outside TZPATH, and the key regex admits ``..`` as a path
    component. Validation must stay a contract error rather than a 500."""
    token = _token(app)
    snapshot = _health_snapshot(datetime.now(UTC).replace(microsecond=0))
    snapshot["data"]["time_zone"] = time_zone

    response = _put_health(app, token, snapshot)

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_snapshot"


def test_personal_data_changes_emit_semantic_events_after_storage(app: Flask) -> None:
    token = _token(app)
    capture = _ChangeCapture()
    app.config["DATA_CHANGE_COORDINATOR"] = capture
    now = datetime.now(UTC).replace(microsecond=0)
    initial = _multi_list_snapshot(now)

    assert _put_multi(app, token, initial).status_code == 200
    initial_list_ids = frozenset(str(item["id"]) for item in initial["data"]["lists"])
    assert capture.events == [
        DataChangeEvent("personal_data.reminders", selectors=initial_list_ids)
    ]
    assert app.config["PERSONAL_DATA_STORE"].get("reminders")["snapshot"] == initial

    # Republishing identical list contents only renews freshness/TTL.
    capture.events.clear()
    renewed = _multi_list_snapshot(now + timedelta(seconds=1))
    assert _put_multi(app, token, renewed).status_code == 200
    assert capture.events == []

    # A content edit narrows the event to the affected opaque list id.
    changed_lists = json.loads(json.dumps(renewed["data"]["lists"]))
    changed_lists[0]["items"][0]["title"] = "Oat milk"
    changed = _multi_list_snapshot(now + timedelta(seconds=2), lists=changed_lists)
    assert _put_multi(app, token, changed).status_code == 200
    assert capture.events == [
        DataChangeEvent(
            "personal_data.reminders",
            selectors=frozenset({changed_lists[0]["id"]}),
        )
    ]

    capture.events.clear()
    response = app.test_client().delete("/api/app/v1/personal-data/reminders", headers=_auth(token))
    assert response.status_code == 204
    assert capture.events == [
        DataChangeEvent("personal_data.reminders", selectors=initial_list_ids)
    ]

    # Idempotent DELETE has no second event.
    capture.events.clear()
    response = app.test_client().delete("/api/app/v1/personal-data/reminders", headers=_auth(token))
    assert response.status_code == 204
    assert capture.events == []


def test_data_change_enqueue_failure_does_not_change_accepted_put(app: Flask) -> None:
    token = _token(app)
    app.config["DATA_CHANGE_COORDINATOR"] = _BrokenChangeCapture()
    now = datetime.now(UTC).replace(microsecond=0)
    snapshot = _multi_list_snapshot(now)

    response = _put_multi(app, token, snapshot)

    assert response.status_code == 200
    assert app.config["PERSONAL_DATA_STORE"].get("reminders")["snapshot"] == snapshot


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


def test_publishers_cannot_overwrite_or_delete_each_others_reminders(app: Flask) -> None:
    client_a = {
        **_CLIENT,
        "name": "Alice iPhone",
        "installation_id": "11111111-1111-4111-8111-111111111111",
    }
    client_b = {
        **_CLIENT,
        "name": "Bob iPhone",
        "installation_id": "22222222-2222-4222-8222-222222222222",
    }
    token_a = _token(app, client_a)
    token_b = _token(app, client_b)
    now = datetime.now(UTC).replace(microsecond=0)
    alice = _multi_list_snapshot(
        now,
        lists=[{"id": "alice-list", "title": "Groceries", "items": []}],
    )
    # Deliberately older than Alice: publisher-local ordering must still accept it.
    bob = _multi_list_snapshot(
        now - timedelta(minutes=5),
        lists=[{"id": "bob-list", "title": "Groceries", "items": []}],
    )

    assert _put_multi(app, token_a, alice).status_code == 200
    assert _put_multi(app, token_b, bob).status_code == 200

    publications = app.config["PERSONAL_DATA_STORE"].publications("reminders")
    assert [item["publisher_name"] for item in publications] == ["Alice iPhone", "Bob iPhone"]
    assert {item["snapshot"]["data"]["lists"][0]["id"] for item in publications} == {
        "alice-list",
        "bob-list",
    }

    alice_status = app.test_client().get("/api/app/v1/personal-data/status", headers=_auth(token_a))
    bob_status = app.test_client().get("/api/app/v1/personal-data/status", headers=_auth(token_b))
    assert alice_status.get_json()["sources"][0]["generated_at"] == _iso(now)
    assert bob_status.get_json()["sources"][0]["generated_at"] == _iso(now - timedelta(minutes=5))

    assert (
        app.test_client()
        .delete("/api/app/v1/personal-data/reminders", headers=_auth(token_a))
        .status_code
        == 204
    )
    assert (
        app.test_client()
        .get("/api/app/v1/personal-data/status", headers=_auth(token_a))
        .get_json()["sources"]
        == []
    )
    assert [
        source["source_id"]
        for source in app.test_client()
        .get("/api/app/v1/personal-data/status", headers=_auth(token_b))
        .get_json()["sources"]
    ] == ["reminders"]
    remaining = app.config["PERSONAL_DATA_STORE"].publications("reminders")
    assert [item["publisher_name"] for item in remaining] == ["Bob iPhone"]


def test_repairing_the_same_installation_keeps_one_publisher(app: Flask) -> None:
    first_token = _token(app)
    second_token = _token(app)
    now = datetime.now(UTC).replace(microsecond=0)
    first = _multi_list_snapshot(
        now,
        lists=[{"id": "food", "title": "Groceries", "items": []}],
    )
    second = _multi_list_snapshot(
        now + timedelta(minutes=1),
        lists=[{"id": "food", "title": "Groceries", "items": []}],
    )

    assert _put_multi(app, first_token, first).status_code == 200
    assert _put_multi(app, second_token, second).status_code == 200

    publications = app.config["PERSONAL_DATA_STORE"].publications("reminders")
    assert len(publications) == 1
    assert publications[0]["snapshot"]["generated_at"] == _iso(now + timedelta(minutes=1))
    for token in (first_token, second_token):
        status = app.test_client().get("/api/app/v1/personal-data/status", headers=_auth(token))
        assert status.get_json()["sources"][0]["generated_at"] == _iso(now + timedelta(minutes=1))


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

    # TTL beyond the advertised 365-day maximum.
    r = _put(app, token, _snapshot(now, ttl_hours=366 * 24))
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
    assert (
        _put_multi(
            app,
            token,
            _multi_list_snapshot(now - timedelta(hours=49), ttl_hours=48),
        ).status_code
        == 200
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


@pytest.mark.parametrize("source", ["reminders", "reminders.fridge", "health.summary"])
@pytest.mark.parametrize("days", [7, 30, 90, 365, None])
def test_configurable_retention_survives_two_days_and_deletes(
    app: Flask, source: str, days: int | None
) -> None:
    token = _token(app)
    now = datetime.now(UTC).replace(microsecond=0)
    generated = now - timedelta(days=3)
    factories = {
        "reminders": _multi_list_snapshot,
        "reminders.fridge": _snapshot,
        "health.summary": _health_snapshot,
    }
    snapshot = factories[source](generated)
    snapshot["expires_at"] = _iso(generated + timedelta(days=days)) if days else None
    client = app.test_client()
    url = f"/api/app/v1/personal-data/{source}"
    response = client.put(url, json=snapshot, headers=_auth(token))
    assert response.status_code == 200, response.get_data(as_text=True)
    status = response.get_json()
    jsonschema.validate(status, schema_for("PersonalDataSourceStatus"), format_checker=_FMT)
    assert status["state"] == "stale"  # age is honest, values remain usable
    assert status["expires_at"] == snapshot["expires_at"]
    store = app.config["PERSONAL_DATA_STORE"]
    assert store.get(source)["snapshot"] == snapshot
    listing = client.get("/api/app/v1/personal-data/status", headers=_auth(token)).get_json()
    assert listing["sources"] == [status]
    # Restart/reload retains the selected policy; finite snapshots still redact.
    from app.state.personal_data_store import PersonalDataSnapshotStore

    reloaded = PersonalDataSnapshotStore(store._path)
    assert reloaded.get(source)["snapshot"] == snapshot
    reloaded.redact_expired((generated + timedelta(days=366)).timestamp())
    assert ("snapshot" in reloaded.get(source)) is (days is None)
    assert client.delete(url, headers=_auth(token)).status_code == 204
    assert store.get(source) is None


@pytest.mark.parametrize("source", ["reminders", "reminders.fridge", "health.summary"])
def test_retention_can_be_shortened_and_missing_deadline_is_rejected(
    app: Flask, source: str
) -> None:
    token = _token(app)
    now = datetime.now(UTC).replace(microsecond=0)
    factories = {
        "reminders": _multi_list_snapshot,
        "reminders.fridge": _snapshot,
        "health.summary": _health_snapshot,
    }
    client = app.test_client()
    url = f"/api/app/v1/personal-data/{source}"
    snapshot = factories[source](now)
    del snapshot["expires_at"]
    assert client.put(url, json=snapshot, headers=_auth(token)).status_code == 400
    snapshot["expires_at"] = None
    assert client.put(url, json=snapshot, headers=_auth(token)).status_code == 200
    replacement = factories[source](now + timedelta(seconds=1), ttl_hours=24)
    response = client.put(url, json=replacement, headers=_auth(token))
    assert response.status_code == 200
    assert response.get_json()["expires_at"] == replacement["expires_at"]
    store = app.config["PERSONAL_DATA_STORE"]
    store.redact_expired((now + timedelta(days=2)).timestamp())
    assert "snapshot" not in store.get(source)
