"""Interaction manifests (protocol v2): id minting (pinned el: vs mk:
hashes), tier/type classification, gesture explosion, the provenance
gate at build time, text regions, nav-priority trimming, the manifest
digest's frame-independence, the REST endpoint, and the /frame pointer
for proto-2 devices."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.manifest import build_interaction_manifest, resolve_region_action
from app.touch_regions import save_regions

PANEL = {"w": 800, "h": 480, "orientation": "landscape", "native_w": 800, "native_h": 480}


def _region(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "x": 100,
        "y": 100,
        "w": 200,
        "h": 80,
        "depth": 1,
        "order": 0,
        "tap": "page:lights",
        "swipe": None,
        "slide": None,
        "origin": "markup",
        "dangling": [],
        "invalid": [],
    }
    base.update(over)
    return base


def _build(regions: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    doc = build_interaction_manifest(
        frame_digest="a" * 16, regions=regions, slots=[], panel=PANEL, **kw
    )
    assert doc is not None
    return doc


def test_pinned_element_id_and_classification() -> None:
    doc = _build([_region(touch_id="tile_desk")])
    (entry,) = doc["regions"]
    assert entry["id"] == "el:tile_desk:tap"
    assert entry["rect"] == {"x": 100, "y": 100, "w": 200, "h": 80}
    assert entry["gestures"] == {"tap": True}
    assert entry["action"] == {"tier": 0, "type": "nav", "target": "page:lights"}
    assert entry["feedback"] == {"mode": "invert"}


def test_hash_ids_are_stable_and_collision_suffixed() -> None:
    a = _build([_region()])["regions"][0]["id"]
    b = _build([_region()])["regions"][0]["id"]
    assert a == b and a.startswith("mk:")
    # Two identical regions in one frame: deterministic ~2 suffix.
    doc = _build([_region(), _region(order=1)])
    ids = [r["id"] for r in doc["regions"]]
    assert ids == [a, f"{a}~2"]


def test_tier_classification_matrix() -> None:
    doc = _build(
        [
            _region(
                touch_id="e1",
                tap={"action": "ha", "domain": "light", "service": "toggle"},
                origin="config",
            ),
            _region(touch_id="e2", tap="refresh"),
            _region(touch_id="e3", tap="rotate_next"),
            _region(touch_id="e4", tap="webhook:https://x", origin="config"),
            _region(touch_id="e5", tap="fetch_latest"),
        ]
    )
    by_id = {r["id"]: r["action"] for r in doc["regions"]}
    assert by_id["el:e1:tap"] == {"tier": 1, "type": "ha"}
    assert by_id["el:e2:tap"] == {"tier": 2, "type": "refresh"}
    assert by_id["el:e3:tap"] == {"tier": 0, "type": "nav"}
    assert by_id["el:e4:tap"] == {"tier": 0, "type": "webhook"}
    assert by_id["el:e5:tap"] == {"tier": 2, "type": "fetch_latest"}


def test_swipe_directions_explode_and_slide_absorbs() -> None:
    doc = _build(
        [
            _region(
                touch_id="nav", tap=None, swipe={"left": "rotate_next", "right": "rotate_prev"}
            ),
            _region(
                touch_id="dim",
                tap=None,
                slide={
                    "axis": "y",
                    "action": {"action": "ha", "domain": "light", "service": "turn_on"},
                },
                origin="config",
            ),
        ]
    )
    ids = {r["id"] for r in doc["regions"]}
    assert ids == {"el:nav:swipe_left", "el:nav:swipe_right", "el:dim:slide"}
    slide = next(r for r in doc["regions"] if r["id"] == "el:dim:slide")
    assert slide["gestures"] == {"slide": {"axis": "y"}}
    assert slide["action"] == {"tier": 1, "type": "ha"}
    assert slide["feedback"]["mode"] == "slider"


def test_markup_origin_side_effects_never_enter_the_manifest() -> None:
    doc = _build(
        [
            _region(tap={"action": "ha", "domain": "light", "service": "toggle"}),  # markup!
            _region(touch_id="ok", tap="page:x"),
        ]
    )
    assert [r["id"] for r in doc["regions"]] == ["el:ok:tap"]


def test_nav_priority_trim_and_manifest_digest_frame_independence() -> None:
    regions = [_region(touch_id=f"r{i}", x=10 * i, tap="refresh", order=i) for i in range(4)]
    regions.append(_region(touch_id="nav", x=700, tap="page:x", order=4))
    doc = _build(regions, max_regions=3)
    ids = [r["id"] for r in doc["regions"]]
    assert "el:nav:tap" in ids and len(ids) == 3
    # Same layout under a different frame digest -> same manifest digest.
    other = build_interaction_manifest(
        frame_digest="b" * 16, regions=regions, slots=[], panel=PANEL, max_regions=3
    )
    assert other is not None
    assert other["manifest_digest"] == doc["manifest_digest"]
    assert other["frame_digest"] != doc["frame_digest"]


def test_text_regions_with_atlas_provider() -> None:
    slots = [
        {
            "x": 10,
            "y": 20,
            "w": 100,
            "h": 40,
            "key": "ha:sensor.temp",
            "suffix": "°",
            "align": "right",
            "px": 32,
            "weight": 700,
        }
    ]
    doc = build_interaction_manifest(
        frame_digest="a" * 16,
        regions=[],
        slots=slots,
        panel=PANEL,
        atlas_provider=lambda px, weight: {
            "digest": "d" * 16,
            "url": "/atlas",
            "format": "4bpp-gray",
            "height": px,
            "glyphs": {"0": {"x": 0, "w": 8}},
        },
    )
    assert doc is not None
    (tx,) = doc["text"]
    assert tx["id"].startswith("tx:")
    assert tx["key"] == "ha:sensor.temp"
    assert tx["atlas"]["height"] == 32
    assert tx["max_chars"] == 47


def test_resolve_region_action_round_trips_ids() -> None:
    regions = [
        _region(
            touch_id="e1",
            tap={"action": "ha", "domain": "light", "service": "toggle"},
            origin="config",
        ),
        _region(tap="page:x", order=1),
    ]
    doc = _build(regions)
    for entry in doc["regions"]:
        resolved = resolve_region_action(regions, entry["id"], PANEL)
        assert resolved is not None
        _region_rec, gesture, _spec = resolved
        assert entry["id"].endswith(gesture) or entry["id"].startswith("mk:")
    assert resolve_region_action(regions, "el:nope:tap", PANEL) is None


# -- REST surface ---------------------------------------------------------


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


def _register(app: Flask, client) -> str:
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
    return resp.get_json()["device_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _seed_frame(app: Flask, *, digest: str = "a" * 16, comp: str = "c" * 16) -> None:
    app.config["PUSH_MANAGER"]._latest_renders["e1003"] = {
        "digest": digest,
        "ext": "bin",
        "filename": f"{digest}.bin",
        "composition_digest": comp,
    }


def test_manifest_endpoint_serves_and_404s(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed_frame(app)
    save_regions(app.config["RENDERS_DIR"], "c" * 16, [_region(touch_id="t1")])

    resp = client.get(
        f"/api/v1/device/e1003/frame/manifest?digest={'a' * 16}", headers=_auth(token)
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["proto"] == 2
    assert body["frame_digest"] == "a" * 16
    assert body["regions"][0]["id"] == "el:t1:tap"

    assert (
        client.get(
            f"/api/v1/device/e1003/frame/manifest?digest={'f' * 16}", headers=_auth(token)
        ).status_code
        == 404
    )


def test_frame_carries_manifest_pointer_only_for_proto_2(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed_frame(app)
    save_regions(app.config["RENDERS_DIR"], "c" * 16, [_region(touch_id="t1")])

    # v1 device: byte-stable response, no manifest block.
    resp = client.get("/api/v1/device/e1003/frame", headers=_auth(token))
    assert resp.status_code == 200
    assert "manifest" not in resp.get_json()

    client.post(
        "/api/v1/device/e1003/status",
        headers=_auth(token),
        data=json.dumps({"proto": {"v": 2}}),
    )
    resp = client.get("/api/v1/device/e1003/frame", headers=_auth(token))
    body = resp.get_json()
    assert body["manifest"]["url"].endswith(f"digest={'a' * 16}")
    assert len(body["manifest"]["digest"]) == 16
