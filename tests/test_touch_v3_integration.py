"""Touch-v3 end-to-end: one canvas of primitives drives the full device round-
trip (spec + wire rects + atlases -> interact dispatch -> live values stream).
Ties the per-endpoint pieces together as a regression guard."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from PIL import Image

import app.rest_api as rest_api
from app.main import REPO_ROOT, create_app
from app.rest_api import _stream_events
from app.state.page_store import Page
from app.state.panel_store import CanvasLayout, Element


def _fake_rasterize(px: int, weight: int, charset: str) -> tuple[bytes, list[dict[str, Any]]]:
    cell = max(4, px // 2)
    img = Image.new("L", (cell * len(charset) + 2, px + 4), 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), [
        {"ch": ch, "x": i * cell, "y": 2, "w": cell, "h": px} for i, ch in enumerate(charset)
    ]


def _stub_ha(app: Flask, states: dict[str, str], calls: list) -> None:
    class _Mod:
        @staticmethod
        def is_configured() -> bool:
            return True

        @staticmethod
        def get_state(entity_id: str) -> dict[str, Any]:
            return {"state": states[entity_id]}

    class _Plugin:
        server_module = _Mod()

    class _Registry:
        @staticmethod
        def get(pid: str) -> Any:
            return _Plugin() if pid == "ha_core" else None

    app.config["PLUGIN_REGISTRY"] = _Registry()
    svc = app.config["BUTTON_SERVICE"]
    svc._call_ha = lambda d, s, data: calls.append((d, s, data))  # type: ignore[method-assign]
    svc._schedule_ha_reconcile = lambda did: calls.append(("RECONCILE", did))  # type: ignore[method-assign]


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
    a.config["SETTINGS_STORE"].update_section("experiments", {"touch_v3": True})
    a.config["OVERLAY_ATLAS_RASTERIZER"] = _fake_rasterize
    Path(a.config["RENDERS_DIR"]).mkdir(parents=True, exist_ok=True)
    return a


def _register(app: Flask, client) -> str:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": "e1003", "kind": "esp32_client", "panel_w": 1872, "panel_h": 1404}
        ),
    )
    assert resp.status_code == 201
    return resp.get_json()["device_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_full_touch_v3_round_trip(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    client = app.test_client()
    token = _register(app, client)
    app.config["PAGE_STORE"].save(
        Page(
            id="p1",
            name="T",
            layout_kind="canvas",
            canvas=CanvasLayout(
                w=600,
                h=400,
                els=[
                    Element(
                        id="sw",
                        kind="switch",
                        x=10,
                        y=10,
                        w=160,
                        h=80,
                        label="Desk",
                        value_key="ha:light.desk",
                    ),
                ],
            ),
        )
    )
    monkeypatch.setattr(
        app.config["PUSH_MANAGER"],
        "latest_render_for",
        lambda _d: {"digest": "a" * 16, "composition_digest": "c" * 16, "page_id": "p1"},
    )
    monkeypatch.setattr(
        rest_api,
        "_frame_info_for_digest",
        lambda _dev, _dig: {"page_id": "p1", "composition_digest": "c" * 16},
    )
    calls: list = []
    _stub_ha(app, {"light.desk": "on"}, calls)

    # 1. spec: the switch, in device-framebuffer coords (scaled up from canvas),
    #    with its label atlas attached.
    spec = client.get("/api/v1/device/e1003/frame/spec", headers=_h(token)).get_json()
    prim = spec["primitives"][0]
    assert prim["id"] == "sw" and prim["value_key"] == "ha:light.desk"
    assert prim["rect"]["w"] > 160  # canvas 600 -> panel 1872, scaled ~3x
    assert [a["id"] for a in spec["atlases"]] == ["l20"]

    # 2. interact: tapping the switch fires the HA toggle, with NO reconcile.
    r = client.post(
        "/api/v1/device/e1003/interact",
        headers=_h(token),
        data=json.dumps({"primitive_id": "sw", "event_id": 1}),
    )
    assert r.get_json()["outcome"] == "ha_dispatched"
    assert calls == [("light", "toggle", {"entity_id": "light.desk"})]  # no RECONCILE entry

    # 3. stream: the switch's entity state is pushed live.
    events = list(
        _stream_events(app, app.config["DEVICE_REGISTRY"].get("e1003"), max_ticks=1, scan_s=0)
    )
    values = [json.loads(c.partition("data: ")[2]) for c in events if c.startswith("event: values")]
    assert values and values[0]["values"]["ha:light.desk"] == "on"
