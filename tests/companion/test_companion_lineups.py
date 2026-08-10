"""Companion API: Lineups read + control (issue #205).

The first slice of native Lineup support. Read the list, see which
dashboard each display is on, flip a Lineup on or off, and step a panel
through it. Authoring is not here and can't be until the write path
behind #204 exists, so the contract these tests pin is: everything is
readable, everything bound is operable, and nothing edits a definition.

The same fake PushManager as the other write-path tests stands in for the
render pipeline.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app

_CLIENT = {
    "name": "Test iPhone",
    "platform": "ios",
    "app_version": "0.1.0",
    "installation_id": "A1B2C3D4-E5F6-47A8-9012-3456789ABCDE",
}


class _FakePush:
    def __init__(self, status: str = "sent") -> None:
        self.status = status
        self.push_calls: list[dict[str, Any]] = []
        self.promoted: list[tuple[str, str]] = []
        self.promote_ok = False

    def push(self, page_id: str, **kwargs: Any) -> Any:
        self.push_calls.append({"page_id": page_id, "device_ids": set(kwargs["device_ids"])})
        return SimpleNamespace(status=self.status, error=None, event_id=1)

    def promote_deck_page(self, device_id: str, page_id: str) -> bool:
        self.promoted.append((device_id, page_id))
        return self.promote_ok

    def device_in_quiet_hours(self, device_id: str) -> bool:
        return False


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
    a.config["PUSH_MANAGER"] = _FakePush()
    return a


def _token(app: Flask) -> str:
    code = app.config["COMPANION_PAIRING_STORE"].issue(note="t").code
    resp = app.test_client().post(
        "/api/app/v1/pair",
        data=json.dumps({"code": code, "client": _CLIENT}),
        content_type="application/json",
    )
    return str(resp.get_json()["token"])


def _auth(token: str, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


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


def _seed_pages(app: Flask, device_ids: list[str], ids: tuple[str, ...]) -> None:
    from app.state.page_store import Page

    for page_id in ids:
        app.config["PAGE_STORE"].save(
            Page(
                id=page_id,
                name=page_id.title(),
                layout_kind="grid",
                device_ids=list(device_ids),
            )
        )


def _seed_lineup(app: Flask, *, device_ids: list[str], **overrides: Any) -> Any:
    from app.state.deck_model import Deck, DeckPage

    fields: dict[str, Any] = {
        "id": "morning",
        "name": "Morning",
        "device_ids": list(device_ids),
        "pages": [DeckPage(page_id="pantry"), DeckPage(page_id="weather")],
        "advance": "timer",
        "advance_trigger": "cycle",
    }
    fields.update(overrides)
    deck = Deck(**fields)
    app.config["DECK_STORE"].upsert(deck)
    return deck


def _poll(app: Flask, token: str, job_id: str, timeout_s: float = 5.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    client = app.test_client()
    while time.time() < deadline:
        body = client.get(f"/api/app/v1/jobs/{job_id}", headers=_auth(token)).get_json()
        if body["job"]["status"] in ("succeeded", "failed"):
            return dict(body["job"])
        time.sleep(0.05)
    raise AssertionError("job never reached a terminal state")


# -- read ---------------------------------------------------------------


def test_list_reports_the_lineup_and_its_dashboards(app: Flask) -> None:
    device = _seed_device(app)
    _seed_pages(app, [device], ("pantry", "weather"))
    _seed_lineup(app, device_ids=[device])
    token = _token(app)
    body = app.test_client().get("/api/app/v1/lineups", headers=_auth(token)).get_json()
    assert [ln["id"] for ln in body["lineups"]] == ["morning"]
    lineup = body["lineups"][0]
    assert lineup["intent"] == "cycle"
    assert [d["name"] for d in lineup["dashboards"]] == ["Pantry", "Weather"]
    assert lineup["device_ids"] == [device]
    assert lineup["native_editable"] is True
    assert lineup["requires_web_reason"] is None


def test_a_deleted_dashboard_is_flagged_not_hidden(app: Flask) -> None:
    """The app has to draw the row; silently dropping it would make a
    broken Lineup look complete."""
    device = _seed_device(app)
    _seed_pages(app, [device], ("pantry",))  # 'weather' never created
    _seed_lineup(app, device_ids=[device])
    token = _token(app)
    body = app.test_client().get("/api/app/v1/lineups", headers=_auth(token)).get_json()
    dashboards = body["lineups"][0]["dashboards"]
    assert [d["missing"] for d in dashboards] == [False, True]


@pytest.mark.parametrize(
    ("overrides", "reason_fragment"),
    [
        ({"advance_smart_sync": True}, "smart sync"),
        ({"advance_mode": "priority"}, "priority"),
        ({"advance_fallback_page_id": "pantry"}, "fallback"),
        ({"advance_days_of_week": [0, 1, 2]}, "selected days"),
        ({"advance_window_start": "08:00"}, "time-of-day window"),
        ({"home_page_id": "pantry"}, "home dashboard"),
    ],
)
def test_an_advanced_lineup_is_readable_but_not_editable(
    app: Flask, overrides: dict[str, Any], reason_fragment: str
) -> None:
    """The server decides editability, not the client. A record using
    anything outside the four authoring intents stays fully readable and
    controllable, and names why it can't be edited natively."""
    device = _seed_device(app)
    _seed_pages(app, [device], ("pantry", "weather"))
    _seed_lineup(app, device_ids=[device], **overrides)
    token = _token(app)
    body = app.test_client().get("/api/app/v1/lineups/morning", headers=_auth(token)).get_json()
    assert body["lineup"]["native_editable"] is False
    assert reason_fragment in body["lineup"]["requires_web_reason"]


def test_unknown_lineup_is_a_404(app: Flask) -> None:
    token = _token(app)
    resp = app.test_client().get("/api/app/v1/lineups/nope", headers=_auth(token))
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"


def test_reads_need_a_credential(app: Flask) -> None:
    assert app.test_client().get("/api/app/v1/lineups").status_code == 401


# -- control ------------------------------------------------------------


def test_disable_and_enable_flip_the_stored_lineup(app: Flask) -> None:
    device = _seed_device(app)
    _seed_pages(app, [device], ("pantry", "weather"))
    _seed_lineup(app, device_ids=[device])
    token = _token(app)
    client = app.test_client()
    for action, expected in (("disable", False), ("enable", True)):
        resp = client.post(
            "/api/app/v1/lineups/morning/actions",
            headers=_auth(token),
            data=json.dumps({"action": action}),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["lineup"]["enabled"] is expected
        assert app.config["DECK_STORE"].get("morning").enabled is expected


def test_next_walks_from_where_the_display_actually_is(app: Flask) -> None:
    """Steps are per display, read off the nav record, so two panels on one
    Lineup don't get yanked to a shared index."""
    device = _seed_device(app)
    _seed_pages(app, [device], ("pantry", "weather"))
    _seed_lineup(app, device_ids=[device])
    app.config["DECK_NAV_STORE"].set(device, "morning", "pantry")
    token = _token(app)
    resp = app.test_client().post(
        "/api/app/v1/lineups/morning/actions",
        headers=_auth(token, "idem-next-0000000000"),
        data=json.dumps({"action": "next", "override_quiet_hours": False}),
        content_type="application/json",
    )
    assert resp.status_code == 202, resp.get_data(as_text=True)
    job = _poll(app, token, resp.get_json()["job"]["id"])
    assert job["status"] == "succeeded"
    pushed = app.config["PUSH_MANAGER"].push_calls
    assert [c["page_id"] for c in pushed] == ["weather"]
    assert app.config["DECK_NAV_STORE"].current_page(device, "morning") == "weather"


def test_previous_wraps_backwards(app: Flask) -> None:
    device = _seed_device(app)
    _seed_pages(app, [device], ("pantry", "weather"))
    _seed_lineup(app, device_ids=[device])
    app.config["DECK_NAV_STORE"].set(device, "morning", "pantry")
    token = _token(app)
    resp = app.test_client().post(
        "/api/app/v1/lineups/morning/actions",
        headers=_auth(token, "idem-prev-0000000000"),
        data=json.dumps({"action": "previous", "override_quiet_hours": False}),
        content_type="application/json",
    )
    assert resp.status_code == 202
    _poll(app, token, resp.get_json()["job"]["id"])
    assert app.config["DECK_NAV_STORE"].current_page(device, "morning") == "weather"


def test_play_requires_a_dashboard_in_the_lineup(app: Flask) -> None:
    device = _seed_device(app)
    _seed_pages(app, [device], ("pantry", "weather", "elsewhere"))
    _seed_lineup(app, device_ids=[device])
    token = _token(app)
    resp = app.test_client().post(
        "/api/app/v1/lineups/morning/actions",
        headers=_auth(token, "idem-play-0000000000"),
        data=json.dumps({"action": "play", "page_id": "elsewhere", "override_quiet_hours": False}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_request"


def test_a_repaint_needs_an_idempotency_key(app: Flask) -> None:
    device = _seed_device(app)
    _seed_pages(app, [device], ("pantry", "weather"))
    _seed_lineup(app, device_ids=[device])
    token = _token(app)
    resp = app.test_client().post(
        "/api/app/v1/lineups/morning/actions",
        headers=_auth(token),
        data=json.dumps({"action": "next", "override_quiet_hours": False}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_request"


def test_an_unbound_lineup_has_nothing_to_move(app: Flask) -> None:
    _seed_pages(app, [], ("pantry", "weather"))
    _seed_lineup(app, device_ids=[])
    token = _token(app)
    resp = app.test_client().post(
        "/api/app/v1/lineups/morning/actions",
        headers=_auth(token, "idem-unbound-000000"),
        data=json.dumps({"action": "next", "override_quiet_hours": False}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_target"


def test_an_unknown_action_is_refused(app: Flask) -> None:
    device = _seed_device(app)
    _seed_pages(app, [device], ("pantry", "weather"))
    _seed_lineup(app, device_ids=[device])
    token = _token(app)
    resp = app.test_client().post(
        "/api/app/v1/lineups/morning/actions",
        headers=_auth(token, "idem-bogus-000000000"),
        data=json.dumps({"action": "delete", "override_quiet_hours": False}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_an_advanced_lineup_is_still_controllable(app: Flask) -> None:
    """The point of read + control shipping before authoring: a Lineup the
    app can't edit is still one it can drive."""
    device = _seed_device(app)
    _seed_pages(app, [device], ("pantry", "weather"))
    _seed_lineup(app, device_ids=[device], advance_smart_sync=True)
    token = _token(app)
    resp = app.test_client().post(
        "/api/app/v1/lineups/morning/actions",
        headers=_auth(token),
        data=json.dumps({"action": "disable"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["lineup"]["native_editable"] is False
    assert app.config["DECK_STORE"].get("morning").enabled is False


# -- optional scopes (#207) ---------------------------------------------


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_authoring_is_not_granted_at_pairing(app: Flask) -> None:
    """The whole point of #207: a pairing hands over what an app needs to be
    an app, and rewriting household scheduling isn't in that set."""
    _token(app)
    record = app.config["COMPANION_TOKENS"].list_active()[0]
    assert "lineups:control" in record.scopes
    assert "lineups:write" not in record.scopes


def test_an_operator_can_grant_and_withdraw_authoring(app: Flask) -> None:
    _token(app)
    token_id = app.config["COMPANION_TOKENS"].list_active()[0].token_id
    client = app.test_client()
    _sign_in(client)
    for granted, expected in (("1", True), ("0", False)):
        resp = client.post(
            f"/settings/companion/session/{token_id}/scope",
            data={"scope": "lineups:write", "granted": granted},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        record = app.config["COMPANION_TOKENS"].list_active()[0]
        assert ("lineups:write" in record.scopes) is expected


def test_a_granted_scope_survives_without_re_pairing(app: Flask) -> None:
    """The bearer is untouched, so the app keeps working across the change."""
    token = _token(app)
    store = app.config["COMPANION_TOKENS"]
    token_id = store.list_active()[0].token_id
    assert store.set_optional_scope(token_id, "lineups:write", granted=True)
    assert store.lookup(token) is not None
    assert "lineups:write" in store.lookup(token).scopes


def test_a_scope_outside_the_optional_set_is_refused(app: Flask) -> None:
    """Only the optional scopes move here. Withdrawing a pairing scope would
    leave a working app failing in ways the operator didn't intend."""
    _token(app)
    store = app.config["COMPANION_TOKENS"]
    token_id = store.list_active()[0].token_id
    assert store.set_optional_scope(token_id, "push:write", granted=False) is False
    assert store.set_optional_scope(token_id, "made:up", granted=True) is False
    assert "push:write" in store.list_active()[0].scopes


def test_an_unknown_stored_scope_is_dropped_on_load(app: Flask, tmp_path: Path) -> None:
    """A typo in the file used to sit there looking like a grant while
    granting nothing, since the check is a membership test."""
    import json as _json

    from app.state.companion_token_store import CompanionTokenStore

    _token(app)
    path = tmp_path / "core" / "companion_tokens.json"
    raw = _json.loads(path.read_text(encoding="utf-8"))
    key = "tokens" if isinstance(raw, dict) and "tokens" in raw else None
    records = raw[key] if key else raw
    records[0]["scopes"].append("lineups:wrtie")
    path.write_text(_json.dumps(raw), encoding="utf-8")
    reloaded = CompanionTokenStore(path).list_active()[0]
    assert "lineups:wrtie" not in reloaded.scopes
    assert "push:write" in reloaded.scopes
