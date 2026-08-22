"""Protocol v2 SSE stream: sync/patches/values event emission on state
change, dedup across ticks, and keepalives. The generator is driven
directly with bounded ticks (no live socket)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.rest_api import _stream_events


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


def _register(app: Flask, client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": "e1003", "kind": "esp32_client", "panel_w": 800, "panel_h": 480}
        ),
    )
    assert resp.status_code == 201


def _seed_frame(app: Flask, digest: str) -> None:
    app.config["PUSH_MANAGER"]._latest_renders["e1003"] = {
        "digest": digest,
        "ext": "bin",
        "filename": f"{digest}.bin",
        "composition_digest": "c" * 16,
    }


def _events(chunks: list[str]) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for chunk in chunks:
        if chunk.startswith("event: "):
            name, _, rest = chunk.partition("\n")
            data = rest.partition("data: ")[2].strip()
            out.append((name[7:], json.loads(data)))
    return out


def _stub_ha(app: Flask, states: dict[str, str]) -> None:
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


def _canvas_page(app: Flask) -> None:
    from app.state.page_store import Page
    from app.state.panel_store import CanvasLayout, Element

    app.config["PAGE_STORE"].save(
        Page(
            id="p1",
            name="T",
            layout_kind="canvas",
            canvas=CanvasLayout(
                els=[
                    Element(id="sw", kind="switch", value_key="ha:light.desk"),
                    Element(id="dup", kind="switch", value_key="ha:light.desk"),  # deduped
                    Element(id="btn", kind="button", on_tap="refresh"),  # no binding
                    Element(id="w", kind="widget", widget="weather_now"),  # not a primitive
                ]
            ),
        )
    )


def test_touch_value_key_slots_collects_bindings(app: Flask) -> None:
    from app.rest_api import _touch_value_key_slots

    _canvas_page(app)
    slots = _touch_value_key_slots(app, "p1")
    assert [s["key"] for s in slots] == ["ha:light.desk"]
    assert _touch_value_key_slots(app, "") == []
    assert _touch_value_key_slots(app, "missing") == []


def test_touch_primitive_values_streamed(app: Flask) -> None:
    _register(app, app.test_client())
    device = app.config["DEVICE_REGISTRY"].get("e1003")
    _canvas_page(app)
    app.config["PUSH_MANAGER"]._latest_renders["e1003"] = {
        "digest": "a" * 16,
        "ext": "bin",
        "filename": "a.bin",
        "composition_digest": "c" * 16,
        "page_id": "p1",
    }
    _stub_ha(app, {"light.desk": "on"})
    evs = _events(list(_stream_events(app, device, max_ticks=1, scan_s=0)))
    values = [d for name, d in evs if name == "values"]
    assert values and values[0]["values"]["ha:light.desk"] == "on"


def test_sync_emitted_on_frame_change_and_deduped(app: Flask) -> None:
    _register(app, app.test_client())
    device = app.config["DEVICE_REGISTRY"].get("e1003")
    _seed_frame(app, "a" * 16)

    gen = _stream_events(app, device, max_ticks=2, scan_s=0)
    first = list(gen)
    evs = _events(first)
    assert [name for name, _ in evs] == ["sync"]  # tick 2 emits nothing new
    assert evs[0][1]["frame_digest"] == "a" * 16
    assert evs[0][1]["seq"] > 1_700_000_000_000

    # A frame change mid-stream produces exactly one new sync event.
    gen = _stream_events(app, device, max_ticks=3, scan_s=0)
    chunks = [next(gen)]  # first sync
    _seed_frame(app, "b" * 16)
    chunks.extend(gen)
    evs = _events(chunks)
    assert [(n, d["frame_digest"]) for n, d in evs if n == "sync"] == [
        ("sync", "a" * 16),
        ("sync", "b" * 16),
    ]


def test_patch_documents_stream_once_per_seq(app: Flask) -> None:
    _register(app, app.test_client())
    device = app.config["DEVICE_REGISTRY"].get("e1003")
    _seed_frame(app, "a" * 16)
    pm = app.config["PUSH_MANAGER"]
    blob = b"\xab" * 32
    blob_digest = hashlib.sha256(blob).hexdigest()[:16]
    (app.config["RENDERS_DIR"] / f"overlay-patch-{blob_digest}.bin").write_bytes(blob)
    with pm._lock:
        pm._patch_seq += 1
        pm._patch_docs["e1003"] = {
            "schema": 2,
            "frame_digest": "a" * 16,
            "seq": pm._patch_seq,
            "format": "fb-rect",
            "url": f"/api/v1/device/e1003/frame/patch/{blob_digest}",
            "bytes": len(blob),
            "rects": [{"x": 0, "y": 0, "w": 16, "h": 4, "offset": 0, "len": 32}],
            "blob_digest": blob_digest,
        }

    evs = _events(list(_stream_events(app, device, max_ticks=3, scan_s=0)))
    patch_events = [d for n, d in evs if n == "patches"]
    assert len(patch_events) == 1  # same seq never re-emitted
    assert patch_events[0]["frame_digest"] == "a" * 16
    assert "blob_digest" not in patch_events[0]


def test_keepalive_comment_when_quiet(app: Flask) -> None:
    _register(app, app.test_client())
    device = app.config["DEVICE_REGISTRY"].get("e1003")
    _seed_frame(app, "a" * 16)
    chunks = list(_stream_events(app, device, max_ticks=3, scan_s=0, keepalive_s=0))
    assert any(c.startswith(":ka") for c in chunks)


def test_stream_endpoint_requires_token(app: Flask) -> None:
    client = app.test_client()
    _register(app, client)
    assert client.get("/api/v1/device/e1003/stream").status_code == 401


# -- one stream per device (production incident, 2026-08-22) --------------


def test_a_second_stream_retires_the_first(app: Flask) -> None:
    """A firmware reconnect loop opened ``/stream`` every ~1.6 s. Each
    abandoned generator kept a waitress thread and kept scanning (including
    live Home Assistant queries) until a write finally failed, so ~16 of 24
    threads were parked on connections nobody was reading and the admin UI
    queued behind them for 9-23 s."""
    from app.rest_api import _claim_device_stream, _stream_events

    _register(app, app.test_client())
    device = app.config["DEVICE_REGISTRY"].get("e1003")
    _seed_frame(app, "a" * 16)

    first_gen = _claim_device_stream(app, "e1003")
    stream = _stream_events(app, device, max_ticks=5, scan_s=0, generation=first_gen)
    assert next(stream)  # first tick works while it is the current stream

    # The device reconnects: the newer claim must end the older generator.
    _claim_device_stream(app, "e1003")
    assert list(stream) == [], "superseded stream kept scanning"


def test_the_newest_stream_survives(app: Flask) -> None:
    from app.rest_api import _claim_device_stream, _stream_events

    _register(app, app.test_client())
    device = app.config["DEVICE_REGISTRY"].get("e1003")
    _seed_frame(app, "a" * 16)

    _claim_device_stream(app, "e1003")
    newest = _claim_device_stream(app, "e1003")
    stream = _stream_events(app, device, max_ticks=1, scan_s=0, generation=newest)
    assert _events(list(stream)), "newest stream should still emit"


def test_streams_are_tracked_per_device(app: Flask) -> None:
    """One panel reconnecting must not close another panel's stream."""
    from app.rest_api import _claim_device_stream, _stream_is_current

    a = _claim_device_stream(app, "panel_a")
    b = _claim_device_stream(app, "panel_b")
    _claim_device_stream(app, "panel_a")  # panel_a reconnects

    assert _stream_is_current(app, "panel_a", a) is False
    assert _stream_is_current(app, "panel_b", b) is True


def test_keepalive_is_short_enough_to_reap_a_dead_peer() -> None:
    """The keepalive write is the only thing that detects a hung-up client,
    so it bounds how long a dead connection holds a thread."""
    from app.rest_api import _STREAM_KEEPALIVE_S

    assert _STREAM_KEEPALIVE_S <= 10.0
