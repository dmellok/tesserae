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
