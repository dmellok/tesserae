"""MCP surface accepts touch-v3 primitives: an agent can create + configure
button/switch/slider/stepper (the fields are validated against the Element model,
so they round-trip with no special-casing)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    a.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})
    return a


def _create_page(client: Any) -> str:
    resp = client.post("/api/mcp/pages", json={"name": "Agent touch", "w": 800, "h": 480})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return str(resp.get_json()["id"])


def _add(client: Any, pid: str, body: dict[str, Any]) -> Any:
    return client.post(f"/api/mcp/pages/{pid}/elements", json=body)


def test_mcp_accepts_and_roundtrips_touch_v3_primitives(app: Flask) -> None:
    client = app.test_client()
    pid = _create_page(client)

    assert (
        _add(
            client,
            pid,
            {
                "kind": "button",
                "x": 40,
                "y": 40,
                "w": 200,
                "h": 90,
                "label": "Movie",
                "icon": "film-slate",
                "on_tap": "page:scenes",
            },
        ).status_code
        == 200
    )
    assert (
        _add(
            client,
            pid,
            {
                "kind": "switch",
                "x": 40,
                "y": 150,
                "w": 200,
                "h": 90,
                "label": "Desk",
                "value_key": "ha:light.desk",
                "state": "on",
            },
        ).status_code
        == 200
    )
    assert (
        _add(
            client,
            pid,
            {
                "kind": "slider",
                "x": 40,
                "y": 260,
                "w": 300,
                "h": 70,
                "axis": "x",
                "value_key": "ha:light.desk:attributes.brightness_pct",
                "value_min": 0,
                "value_max": 100,
                "value_step": 5,
            },
        ).status_code
        == 200
    )
    assert (
        _add(
            client,
            pid,
            {
                "kind": "stepper",
                "x": 40,
                "y": 350,
                "w": 200,
                "h": 70,
                "value_key": "ha:media.vol",
                "value_min": 0,
                "value_max": 30,
            },
        ).status_code
        == 200
    )

    els = {e["kind"]: e for e in client.get(f"/api/mcp/pages/{pid}/canvas").get_json()["els"]}
    assert set(els) == {"button", "switch", "slider", "stepper"}
    assert els["switch"]["value_key"] == "ha:light.desk" and els["switch"]["state"] == "on"
    assert els["slider"]["axis"] == "x" and els["slider"]["value_max"] == 100
    assert els["button"]["icon"] == "film-slate"


def test_mcp_rejects_unknown_touch_field(app: Flask) -> None:
    client = app.test_client()
    pid = _create_page(client)
    # A typo'd binding field surfaces as a 422 (validated against the model), so
    # the agent fixes it rather than silently losing the binding.
    resp = _add(client, pid, {"kind": "switch", "x": 0, "y": 0, "w": 10, "h": 10, "valuekey": "x"})
    assert resp.status_code == 422
