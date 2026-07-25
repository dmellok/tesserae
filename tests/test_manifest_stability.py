"""Bench-reported protocol v2 regressions (2026-07-25): the manifest
block must ride EVERY /frame 200 for a proto-2 device, including after a
periodic re-render mints a new frame digest; the manifest digest must
stay stable across pixel-only re-renders; /frame/manifest must answer
for a just-superseded digest; and /frame/data must keep serving values
for the current frame. Reproduced with real pushes (capture stubbed)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from PIL import Image

from app.main import REPO_ROOT, create_app
from app.state.page_store import Page

W, H = 1872, 1404


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
        data=json.dumps({"device_id": "e1003", "kind": "esp32_client", "panel_w": W, "panel_h": H}),
    )
    assert resp.status_code == 201
    return resp.get_json()["device_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _advertise_v2(client, token: str) -> None:
    resp = client.post(
        "/api/v1/device/e1003/status",
        headers=_auth(token),
        data=json.dumps({"proto": {"v": 2}, "overlay": {"schema": 2, "max_targets": 32}}),
    )
    assert resp.status_code == 200


def _png(shade: int, *, full: bool = False) -> bytes:
    """``full=True`` changes the whole canvas so the patch-divert's
    over-budget fallback fires and a re-render mints a NEW digest (the
    bench scenario); ``full=False`` keeps the change small."""
    img = Image.new("RGB", (W, H), (shade, shade, shade) if full else (255, 255, 255))
    img.paste((shade, shade, shade), (100, 100, 500, 400))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


INTERACTIVE = {
    "regions": [
        {
            "x": 100,
            "y": 100,
            "w": 400,
            "h": 300,
            "tap": json.dumps({"action": "ha", "domain": "light", "service": "toggle", "data": {}}),
        }
    ],
    "slots": [
        {
            "x": 600,
            "y": 100,
            "w": 200,
            "h": 40,
            "key": "ha:sensor.temp",
            "suffix": "",
            "align": "left",
            "px": 32,
            "weight": 700,
        }
    ],
}


def _fake_atlas(px: int, weight: int, charset: str):
    img = Image.new("L", (8 * len(charset), px), 255)
    boxes = [
        {"ch": ch, "x": i * 8, "y": 0, "w": 8, "h": px} for i, ch in enumerate(charset) if ch != " "
    ]
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), boxes


def _push(app: Flask, shade: int, *, full: bool = False) -> None:
    """A real page push with only the browser capture stubbed. The
    interactive capture result carries the same region + slot on both
    renders, so only pixels change between shades."""
    pm = app.config["PUSH_MANAGER"]
    with patch("app.push.capture_composed", return_value=(_png(shade, full=full), INTERACTIVE)):
        result = pm.push("dash", device_ids={"e1003"}, source="test")
    assert result.status in ("sent", "no_change"), result

    # Fix the touch region's provenance: capture-normalized regions read
    # as markup origin, but the bench dashboard's HA actions come from
    # validated config. Rewrite the sidecar the way the composer does
    # for config-origin actions.
    info = pm.latest_render_for("e1003")
    from app.touch_regions import load_regions, load_slots, save_regions

    comp = str(info["composition_digest"])
    regions = load_regions(app.config["RENDERS_DIR"], comp)
    for r in regions:
        r["origin"] = "config"
    save_regions(
        app.config["RENDERS_DIR"], comp, regions, slots=load_slots(app.config["RENDERS_DIR"], comp)
    )


@pytest.fixture
def dash_app(app: Flask) -> tuple[Flask, Any, str]:
    client = app.test_client()
    token = _register(app, client)
    _advertise_v2(client, token)
    app.config["PAGE_STORE"].save(Page(id="dash", name="Dash", device_id="e1003"))
    app.config["OVERLAY_ATLAS_RASTERIZER"] = _fake_atlas
    return app, client, token


def _get_frame(client, token: str) -> dict[str, Any]:
    resp = client.get("/api/v1/device/e1003/frame", headers=_auth(token))
    assert resp.status_code == 200
    return resp.get_json()


def test_manifest_survives_periodic_rerender(dash_app) -> None:
    app, client, token = dash_app
    _push(app, shade=0)
    first = _get_frame(client, token)
    assert "manifest" in first, "first render must carry the manifest block"

    # A periodic re-render: same layout, different pixels everywhere
    # (over the patch budget), so a NEW digest is minted -- the bench
    # scenario where the manifest block vanished.
    _push(app, shade=128, full=True)
    second = _get_frame(client, token)
    assert second["render_id"] != first["render_id"]
    assert "manifest" in second, "re-render dropped the manifest block (bench bug 1)"
    # Same layout -> same manifest digest: the device re-anchors its
    # held manifest without a re-fetch.
    assert second["manifest"]["digest"] == first["manifest"]["digest"]

    resp = client.get(
        f"/api/v1/device/e1003/frame/manifest?digest={second['render_id']}",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    doc = resp.get_json()
    assert doc["frame_digest"] == second["render_id"]
    assert doc["regions"] and doc["text"]


def test_manifest_answers_for_superseded_digest(dash_app) -> None:
    """A device mid-linger on the old digest must not be orphaned the
    moment a re-render lands: /frame/manifest keeps answering for a
    recently-superseded digest."""
    app, client, token = dash_app
    _push(app, shade=0)
    first = _get_frame(client, token)
    _push(app, shade=128, full=True)
    resp = client.get(
        f"/api/v1/device/e1003/frame/manifest?digest={first['render_id']}",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.get_json()["frame_digest"] == first["render_id"]


def test_frame_data_serves_values_for_current_frame(dash_app, monkeypatch) -> None:
    app, client, token = dash_app
    _push(app, shade=0)
    frame = _get_frame(client, token)

    import app.rest_api as rest_api

    monkeypatch.setattr(
        rest_api, "_ha_get_state", lambda: lambda eid: {"state": "21.4", "attributes": {}}
    )
    resp = client.get(
        f"/api/v1/device/e1003/frame/data?digest={frame['render_id']}", headers=_auth(token)
    )
    assert resp.status_code == 200, "frame/data went dark (bench bug 2)"
    body = resp.get_json()
    assert body["seq"] > 1_700_000_000_000
    # Values keys must equal the manifest text keys byte-for-byte
    # (bench bug 3: firmware does exact string matching).
    manifest = client.get(
        f"/api/v1/device/e1003/frame/manifest?digest={frame['render_id']}",
        headers=_auth(token),
    ).get_json()
    text_keys = {t["key"] for t in manifest["text"]}
    assert text_keys and text_keys <= set(body["values"]), (text_keys, body["values"])


def test_frame_data_grace_window_on_superseded_digest(dash_app, monkeypatch) -> None:
    """The bench's dark linger: a re-render mints a new digest while the
    device is still 1 s-polling /frame/data with the old one. The old
    digest keeps answering inside the grace window."""
    app, client, token = dash_app
    _push(app, shade=0)
    first = _get_frame(client, token)
    _push(app, shade=128, full=True)

    import app.rest_api as rest_api

    monkeypatch.setattr(
        rest_api, "_ha_get_state", lambda: lambda eid: {"state": "21.4", "attributes": {}}
    )
    resp = client.get(
        f"/api/v1/device/e1003/frame/data?digest={first['render_id']}", headers=_auth(token)
    )
    assert resp.status_code == 200
    assert resp.get_json()["values"]


def test_status_envelopes_for_proto_only_firmware(dash_app, monkeypatch) -> None:
    """A v2 firmware that advertises only proto (no v1 overlay block)
    still gets overlay_values on /status: gating reads the sticky caps,
    not the beat body alone."""
    app, client, token = dash_app
    _push(app, shade=0)
    # Wipe the sticky overlay so only proto remains, then beat with a
    # proto-only body (what a pure-v2 firmware sends).
    app.config["DEVICE_STATUS"]["e1003"].pop("overlay", None)

    import app.rest_api as rest_api

    monkeypatch.setattr(
        rest_api, "_ha_get_state", lambda: lambda eid: {"state": "21.4", "attributes": {}}
    )
    resp = client.post(
        "/api/v1/device/e1003/status",
        headers=_auth(token),
        data=json.dumps({"proto": {"v": 2}}),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert "overlay_values" in body and body["overlay_values"]["values"]


def test_noninteractive_frame_gets_empty_manifest_not_silence(dash_app) -> None:
    """A proto-2 device on a dashboard with no touch regions must see an
    explicit empty manifest, never a manifest-less 200 (which reads as
    'v1 server' and latches the device out of region dispatch)."""
    app, client, token = dash_app
    pm = app.config["PUSH_MANAGER"]
    with patch("app.push.capture_composed", return_value=(_png(0), {"regions": [], "slots": []})):
        result = pm.push("dash", device_ids={"e1003"}, source="test")
    assert result.status == "sent"
    frame = _get_frame(client, token)
    assert "manifest" in frame
    resp = client.get(
        f"/api/v1/device/e1003/frame/manifest?digest={frame['render_id']}",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    doc = resp.get_json()
    assert doc["regions"] == [] and doc["text"] == []


def test_manifest_classifies_json_string_actions(dash_app) -> None:
    """Bench round 2: code-element sidecar specs are raw JSON strings;
    the manifest must classify the PARSED action, not the string (which
    produced action.type == '{"action"' and tier-2 downgrades)."""
    app, client, token = dash_app
    _push(app, shade=0)
    frame = _get_frame(client, token)
    doc = client.get(
        f"/api/v1/device/e1003/frame/manifest?digest={frame['render_id']}",
        headers=_auth(token),
    ).get_json()
    assert doc["regions"], doc
    action = doc["regions"][0]["action"]
    assert action == {"tier": 1, "type": "ha"}


def test_region_report_dispatches_and_answers_ok(dash_app) -> None:
    """Bench round 2 blocker: a /tap region report on a JSON-string
    action must dispatch the HA call and answer the v2 wire vocabulary
    ('ok', not 'error' / internal outcome names)."""
    app, client, token = dash_app
    _push(app, shade=0)
    frame = _get_frame(client, token)
    doc = client.get(
        f"/api/v1/device/e1003/frame/manifest?digest={frame['render_id']}",
        headers=_auth(token),
    ).get_json()
    region_id = doc["regions"][0]["id"]

    svc = app.config["BUTTON_SERVICE"]
    ha_calls: list[tuple[str, str]] = []
    svc._call_ha = lambda domain, service, data: ha_calls.append((domain, service))
    svc._spawn_reconcile = lambda d: None

    resp = client.post(
        "/api/v1/device/e1003/tap",
        headers=_auth(token),
        data=json.dumps(
            {
                "region_id": region_id,
                "gesture": "tap",
                "digest": frame["render_id"],
                "event_id": 9174321,
            }
        ),
    )
    assert resp.status_code == 200
    assert resp.get_json()["outcome"] == "ok"
    assert ha_calls == [("light", "toggle")]

    # A replayed report answers the firmware's known vocabulary.
    replay = client.post(
        "/api/v1/device/e1003/tap",
        headers=_auth(token),
        data=json.dumps(
            {
                "region_id": region_id,
                "gesture": "tap",
                "digest": frame["render_id"],
                "event_id": 9174321,
            }
        ),
    )
    assert replay.get_json()["outcome"] == "deduped"

    # An id that mints from nothing in this frame gets a SPECIFIC name.
    unknown = client.post(
        "/api/v1/device/e1003/tap",
        headers=_auth(token),
        data=json.dumps(
            {
                "region_id": "el:nonexistent:tap",
                "gesture": "tap",
                "digest": frame["render_id"],
                "event_id": 9174322,
            }
        ),
    )
    assert unknown.get_json()["outcome"] == "no_action_for_region"


INTERACTIVE_MOVED = {
    "regions": [dict(INTERACTIVE["regions"][0], x=800, y=600)],
    "slots": [],
}

EMPTY_EXTRACTION: dict[str, Any] = {"regions": [], "slots": []}


def test_region_report_survives_pixel_only_rerender(dash_app) -> None:
    """Bench round 3 item 1: the device taps against the digest ON GLASS
    while the server has re-rendered pixels since. Same layout -> the
    report dispatches; a genuinely different layout -> stale."""
    app, client, token = dash_app
    _push(app, shade=0)
    first = _get_frame(client, token)
    manifest = client.get(
        f"/api/v1/device/e1003/frame/manifest?digest={first['render_id']}",
        headers=_auth(token),
    ).get_json()
    region_id = manifest["regions"][0]["id"]

    # Pixel-only re-render: over the patch budget, so a NEW digest mints.
    _push(app, shade=128, full=True)
    second = _get_frame(client, token)
    assert second["render_id"] != first["render_id"]

    svc = app.config["BUTTON_SERVICE"]
    ha_calls: list[str] = []
    svc._call_ha = lambda domain, service, data: ha_calls.append(domain)
    svc._spawn_reconcile = lambda d: None

    resp = client.post(
        "/api/v1/device/e1003/tap",
        headers=_auth(token),
        data=json.dumps(
            {
                "region_id": region_id,
                "gesture": "tap",
                "digest": first["render_id"],  # the frame the finger touched
                "event_id": 41,
            }
        ),
    )
    assert resp.get_json()["outcome"] == "ok"
    assert ha_calls == ["light"]

    # Now the layout genuinely changes: the same report goes stale.
    pm = app.config["PUSH_MANAGER"]
    with patch("app.push.capture_composed", return_value=(_png(64, full=True), INTERACTIVE_MOVED)):
        assert pm.push("dash", device_ids={"e1003"}, source="test").status == "sent"
    resp = client.post(
        "/api/v1/device/e1003/tap",
        headers=_auth(token),
        data=json.dumps(
            {
                "region_id": region_id,
                "gesture": "tap",
                "digest": first["render_id"],
                "event_id": 42,
            }
        ),
    )
    assert resp.get_json()["outcome"] == "stale"


def test_empty_extraction_never_overwrites_populated_sidecar(dash_app) -> None:
    """Bench round 3 item 3: a capture that raced the code-element
    mirrors extracts nothing; for an identical composition the populated
    sidecar must survive."""
    app, _client, _token = dash_app
    _push(app, shade=0)
    pm = app.config["PUSH_MANAGER"]
    comp = pm.latest_render_for("e1003")["composition_digest"]
    assert pm.touch_regions_for(comp)

    with patch("app.push.capture_composed", return_value=(_png(0), EMPTY_EXTRACTION)):
        pm.push("dash", device_ids={"e1003"}, source="test", force_publish=True)
    assert pm.touch_regions_for(comp), "empty extraction clobbered the sidecar"


def test_empty_manifest_rebuild_serves_last_populated_for_page(dash_app) -> None:
    """Bench round 3 item 3: a re-render whose extraction raced to empty
    must not serve a structurally-valid 0-region manifest for a page
    that had regions (the device holds it and touch dies)."""
    app, client, token = dash_app
    _push(app, shade=0)
    first = _get_frame(client, token)
    good = client.get(
        f"/api/v1/device/e1003/frame/manifest?digest={first['render_id']}",
        headers=_auth(token),
    ).get_json()
    assert good["regions"]

    pm = app.config["PUSH_MANAGER"]
    with patch("app.push.capture_composed", return_value=(_png(128, full=True), EMPTY_EXTRACTION)):
        assert pm.push("dash", device_ids={"e1003"}, source="test").status == "sent"
    second = _get_frame(client, token)
    assert second["render_id"] != first["render_id"]
    assert "manifest" in second
    doc = client.get(
        f"/api/v1/device/e1003/frame/manifest?digest={second['render_id']}",
        headers=_auth(token),
    ).get_json()
    assert doc["regions"], "0-region manifest served for an interactive page"
    assert doc["manifest_digest"] == good["manifest_digest"]  # device re-anchors


def test_sub_tolerance_jitter_holds_digest_and_stages_nothing(dash_app) -> None:
    """Bench round 3 item 2: anti-aliasing jitter between captures of
    visually identical content must not mint a frame (30 s GC16 flash
    per re-render) -- the digest holds and nothing is staged."""
    app, _client, _token = dash_app
    _push(app, shade=0)
    pm = app.config["PUSH_MANAGER"]
    before = pm.latest_render_for("e1003")["digest"]
    with patch("app.push.capture_composed", return_value=(_png(2), INTERACTIVE)):
        assert pm.push("dash", device_ids={"e1003"}, source="test").status == "sent"
    assert pm.latest_render_for("e1003")["digest"] == before
    assert pm.frame_patches_for("e1003", before) is None
