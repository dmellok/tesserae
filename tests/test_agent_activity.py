"""Agent activity bus: what the MCP agent is doing, as the UI reads it.

Covers the run splitting, the per-endpoint narration (label / verb / summary),
that MCP calls record themselves through the after-request hook, and that the
read surfaces are gated on the same experiment as the MCP surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.agent_activity import ActivityBus, summarise
from app.main import REPO_ROOT, create_app


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


def _enable(app: Flask) -> None:
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True, "composer": True})


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _create_page(client: Any, name: str = "Kitchen") -> str:
    resp = client.post("/api/mcp/pages", json={"name": name, "w": 800, "h": 480})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return str(resp.get_json()["id"])


# -- the bus ------------------------------------------------------------


def test_run_splits_on_a_long_gap() -> None:
    bus = ActivityBus(run_idle_s=30.0)
    a = bus.record(endpoint="create_page", status="ok", code=200, duration_ms=1, now=1000.0)
    b = bus.record(endpoint="append_element", status="ok", code=200, duration_ms=1, now=1005.0)
    c = bus.record(endpoint="append_element", status="ok", code=200, duration_ms=1, now=1100.0)
    assert a.run == b.run, "calls in one burst belong to the same run"
    assert c.run == b.run + 1, "a gap past the idle window starts a new run"
    assert [s.seq for s in (a, b, c)] == [1, 2, 3]


def test_snapshot_returns_only_newer_steps() -> None:
    bus = ActivityBus()
    for _ in range(3):
        bus.record(endpoint="append_element", status="ok", code=200, duration_ms=1)
    steps, run, seq = bus.snapshot()
    assert [s.seq for s in steps] == [1, 2, 3]
    assert (run, seq) == (1, 3)
    later, _, _ = bus.snapshot(since=2)
    assert [s.seq for s in later] == [3]


def test_current_run_excludes_the_previous_one() -> None:
    bus = ActivityBus(run_idle_s=10.0)
    bus.record(endpoint="create_page", status="ok", code=200, duration_ms=1, now=100.0)
    bus.record(endpoint="append_element", status="ok", code=200, duration_ms=1, now=500.0)
    bus.record(endpoint="push", status="ok", code=200, duration_ms=1, now=501.0)
    assert [s.endpoint for s in bus.current_run()] == ["append_element", "push"]


def test_ring_buffer_caps_without_losing_the_newest() -> None:
    bus = ActivityBus(cap=5)
    for _ in range(9):
        bus.record(endpoint="append_element", status="ok", code=200, duration_ms=1)
    steps, _, seq = bus.snapshot()
    assert seq == 9
    assert [s.seq for s in steps] == [5, 6, 7, 8, 9]


def test_listeners_fire_and_detach() -> None:
    bus = ActivityBus()
    seen: list[str] = []

    def listener(step: Any) -> None:
        seen.append(step.endpoint)

    bus.add_listener(listener)
    bus.record(endpoint="push", status="ok", code=200, duration_ms=1)
    bus.remove_listener(listener)
    bus.record(endpoint="preview", status="ok", code=200, duration_ms=1)
    assert seen == ["push"]


def test_a_raising_listener_does_not_break_recording() -> None:
    bus = ActivityBus()

    def boom(step: Any) -> None:
        raise RuntimeError("subscriber is broken")

    bus.add_listener(boom)
    step = bus.record(endpoint="push", status="ok", code=200, duration_ms=1)
    assert step.seq == 1


def test_unknown_endpoint_still_narrates() -> None:
    bus = ActivityBus()
    step = bus.record(endpoint="brand_new_tool", status="ok", code=200, duration_ms=1)
    assert step.label == "Brand new tool"
    assert step.verb  # never blank; the "now" line always has something to say
    assert step.kind == "probe"


# -- summaries ----------------------------------------------------------


@pytest.mark.parametrize(
    ("endpoint", "view_args", "body", "payload", "target", "detail"),
    [
        (
            "create_page",
            {},
            {"name": "Kitchen", "w": 800, "h": 480},
            {"id": "abc"},
            "Kitchen",
            "800x480",
        ),
        ("append_element", {"page_id": "p1"}, {"kind": "text", "source": "wx"}, None, "text", "wx"),
        (
            "append_elements_bulk",
            {"page_id": "p1"},
            {"elements": [{"kind": "rect"}, {"kind": "icon"}]},
            None,
            "2 elements",
            "icon, rect",
        ),
        (
            "append_element_code",
            {"page_id": "p1", "element_id": "e1"},
            {"field": "css", "text": "x" * 512},
            {"length": 2048},
            "css",
            "+512 B of 2.0 KB",
        ),
        (
            "bind_devices",
            {"page_id": "p1"},
            {"device_ids": ["e1003"]},
            None,
            "1 device",
            "e1003",
        ),
        ("push", {"page_id": "p1"}, {}, {"sent": ["e1003"], "errors": []}, "1 panel", ""),
        ("widget_data", {"key": "weather_now"}, {}, None, "weather_now", ""),
    ],
)
def test_summarise_describes_the_call(
    endpoint: str,
    view_args: dict[str, Any],
    body: Any,
    payload: dict[str, Any] | None,
    target: str,
    detail: str,
) -> None:
    got_target, got_detail, _ = summarise(endpoint, view_args, body, payload)
    assert (got_target, got_detail) == (target, detail)


def test_summarise_takes_the_page_id_from_the_path() -> None:
    _, _, page_id = summarise("append_element", {"page_id": "p1"}, {"kind": "text"}, None)
    assert page_id == "p1"


def test_summarise_survives_a_malformed_body() -> None:
    # Bodies reach the hook exactly as sent, including shapes the route
    # rejected. A summary is decoration and must never raise.
    assert summarise("append_elements_bulk", {}, {"elements": "not-a-list"}, None) == (
        "0 elements",
        "",
        None,
    )
    assert summarise("append_element", {}, "a string", None) == ("element", "", None)


# -- recording through the MCP surface ----------------------------------


def test_mcp_calls_land_on_the_bus(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    page_id = _create_page(client)
    client.post(
        f"/api/mcp/pages/{page_id}/elements",
        json={"kind": "text", "text": "hi", "x": 0, "y": 0, "w": 80, "h": 30},
    )
    steps, _, _ = app.config["AGENT_ACTIVITY"].snapshot()
    assert [(s.label, s.kind) for s in steps] == [
        ("Create dashboard", "build"),
        ("Add element", "build"),
    ]
    assert steps[0].target == "Kitchen"
    assert steps[0].page_id == page_id
    assert steps[1].verb == "Adding an element"


def test_a_failed_call_records_as_an_error(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    page_id = _create_page(client)
    # An unknown element key is refused by the route, so this is a 4xx the
    # rail should paint red rather than tick off.
    resp = client.post(
        f"/api/mcp/pages/{page_id}/elements", json={"kind": "text", "not_a_field": 1}
    )
    assert resp.status_code >= 400
    steps, _, _ = app.config["AGENT_ACTIVITY"].snapshot()
    assert steps[-1].status == "error"
    assert steps[-1].code == resp.status_code


def test_unauthorised_calls_are_not_recorded(app: Flask) -> None:
    _enable(app)
    app.config["SETTINGS_STORE"].patch_section("app", {"mcp_token_secret": "s3cret"})
    client = app.test_client()
    resp = client.get("/api/mcp/pages", environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
    assert resp.status_code == 401
    steps, _, _ = app.config["AGENT_ACTIVITY"].snapshot()
    assert steps == [], "a rejected call never reaches the bus"


# -- read surfaces ------------------------------------------------------


def test_activity_json_returns_steps_with_page_names(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    _sign_in(client)
    _create_page(client)
    body = client.get("/agent/activity.json").get_json()
    assert body["run"] == 1
    assert body["seq"] == 1
    assert body["steps"][0]["page_name"] == "Kitchen"
    assert body["steps"][0]["verb"] == "Creating the dashboard"
    # The delta a poller asks for next time is empty until something happens.
    assert client.get(f"/agent/activity.json?since={body['seq']}").get_json()["steps"] == []
    assert client.get("/agent/activity.json?since=nonsense").status_code == 200


def test_read_surfaces_404_without_the_experiment(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    assert client.get("/agent/activity.json").status_code == 404
    assert client.get("/agent/stream").status_code == 404


def test_stream_opens_with_a_snapshot_of_the_current_run(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    _sign_in(client)
    _create_page(client)
    resp = client.get("/agent/stream")
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    # Read just the opening frames; the generator then blocks on keepalives.
    chunks = resp.response.__iter__()
    assert next(chunks).strip() == b":connected"
    snapshot = next(chunks).decode()
    assert snapshot.startswith("event: snapshot")
    payload = json.loads(snapshot.split("data: ", 1)[1])
    assert [s["label"] for s in payload["steps"]] == ["Create dashboard"]
    resp.close()


def test_editor_only_gets_a_stream_url_when_mcp_is_on(app: Flask) -> None:
    app.config["SETTINGS_STORE"].patch_section("experiments", {"composer": True, "mcp": False})
    client = app.test_client()
    _sign_in(client)
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})
    page_id = _create_page(client)
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": False})
    html = client.get(f"/pages/canvas/c/{page_id}").get_data(as_text=True)
    assert 'data-agent-stream-url=""' in html
    assert "panels/agent-rail.js" not in html

    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})
    html = client.get(f"/pages/canvas/c/{page_id}").get_data(as_text=True)
    assert 'data-agent-stream-url="/agent/stream"' in html
    assert "panels/agent-rail.js" in html


def test_admin_shell_wires_the_follow_toast_only_when_mcp_is_on(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    assert 'id="agent-follow"' not in client.get("/send").get_data(as_text=True)
    _enable(app)
    html = client.get("/send").get_data(as_text=True)
    assert 'id="agent-follow"' in html
    assert 'data-editor-url="/pages/canvas/c/__ID__"' in html
