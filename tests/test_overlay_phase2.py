"""Phase-2 overlay tests: slot normalization + sidecar v3, atlas
packing/build (fake rasterizer, no Playwright), slot/atlas grouping in
build_spec, the values document, and the REST surface (spec with slots,
atlas fetch, /frame/data, /status overlay_values piggyback)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from PIL import Image

from app import overlay_sync
from app.main import REPO_ROOT, create_app
from app.touch_regions import (
    load_slots,
    normalize_slots,
    save_regions,
    split_capture_result,
)

# -- normalization + sidecar -------------------------------------------------


def _raw_slot(**over: Any) -> dict[str, Any]:
    base = {
        "x": 10,
        "y": 20,
        "w": 100,
        "h": 40,
        "key": "ha:sensor.temp",
        "suffix": "°",
        "align": "right",
        "px": 32,
        "weight": 650,
    }
    base.update(over)
    return base


def test_normalize_slots_buckets_and_validates() -> None:
    out = normalize_slots([_raw_slot()])
    assert out == [
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
    # Bad key grammar, zero box, silly px, non-dict: all dropped.
    assert normalize_slots([_raw_slot(key="sensor.temp")]) == []
    assert normalize_slots([_raw_slot(w=0)]) == []
    assert normalize_slots([_raw_slot(px=500)]) == []
    assert normalize_slots(["nope", None]) == []
    assert normalize_slots(None) == []
    # Unknown align collapses to left, weight buckets down to 400.
    got = normalize_slots([_raw_slot(align="justify", weight=500)])[0]
    assert got["align"] == "left" and got["weight"] == 400


def test_normalize_slots_accepts_attribute_paths_and_map() -> None:
    got = normalize_slots(
        [
            _raw_slot(
                key="ha:light.desk:attributes.brightness",
                map='{"on": "1", "off": "0"}',
            )
        ]
    )[0]
    assert got["key"] == "ha:light.desk:attributes.brightness"
    assert got["map"] == {"on": "1", "off": "0"}
    # Malformed / non-object / empty maps read as no map at all.
    for bad in ("not json", "[1,2]", "{}", '{"on": 3}', None):
        assert "map" not in normalize_slots([_raw_slot(map=bad)])[0]


def test_split_capture_result_tolerates_both_shapes() -> None:
    assert split_capture_result({"regions": [1], "slots": [2]}) == ([1], [2])
    assert split_capture_result([1, 2]) == ([1, 2], None)
    assert split_capture_result(None) == (None, None)


def test_sidecar_v3_roundtrip(tmp_path: Path) -> None:
    slots = normalize_slots([_raw_slot()])
    save_regions(tmp_path, "c" * 16, [{"x": 1, "y": 2, "w": 3, "h": 4}], slots=slots)
    assert load_slots(tmp_path, "c" * 16) == slots
    # Sidecar without slots reads as no slots.
    save_regions(tmp_path, "d" * 16, [])
    assert load_slots(tmp_path, "d" * 16) == []


# -- atlas pack + build -------------------------------------------------------


def test_pack_atlas_strip_layout_and_even_width() -> None:
    black = Image.new("L", (3, 4), 0)
    white = Image.new("L", (2, 4), 255)
    packed, table, strip_w, height = overlay_sync.pack_atlas_strip([("0", black), ("1", white)])
    # 3 + 2 = 5 px -> padded to 6; last glyph's declared width widens.
    assert (strip_w, height) == (6, 4)
    assert table == {"0": {"x": 0, "w": 3}, "1": {"x": 3, "w": 3}}
    assert len(packed) == 6 // 2 * 4
    # Row 0: black(0x0) x3, white(0xF) x2, pad white -> 0x00, 0x0F, 0xFF.
    assert packed[:3] == bytes([0x00, 0x0F, 0xFF])


def _fake_rasterize(px: int, weight: int, charset: str):
    """Deterministic strip: every glyph a black px//2-wide box, laid out
    left to right with the boxes reported like the browser would."""
    w_each = max(2, px // 2)
    img = Image.new("L", (w_each * len(charset), px), 255)
    boxes = []
    for i, ch in enumerate(charset):
        if ch != " ":
            img.paste(0, (i * w_each, 0, i * w_each + w_each, px))
            boxes.append({"ch": ch, "x": i * w_each, "y": 0, "w": w_each, "h": px})
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), boxes


def test_build_atlas_builds_and_caches(tmp_path: Path) -> None:
    atlas = overlay_sync.build_atlas(32, 700, renders_dir=tmp_path, rasterize=_fake_rasterize)
    assert atlas is not None
    assert atlas["format"] == "4bpp-gray"
    assert atlas["height"] == 32
    assert set(atlas["glyphs"]) == set(overlay_sync.ATLAS_CHARSET)
    assert (tmp_path / f"overlay-atlas-{atlas['digest']}.bin").is_file()
    # Undrawn space glyph synthesized as a blank.
    assert atlas["glyphs"][" "]["w"] >= 2

    calls: list[int] = []

    def counting(px: int, weight: int, charset: str):
        calls.append(px)
        return _fake_rasterize(px, weight, charset)

    again = overlay_sync.build_atlas(32, 700, renders_dir=tmp_path, rasterize=counting)
    assert again == atlas
    assert calls == []  # cache hit, rasterizer never invoked


def test_build_atlas_failure_returns_none(tmp_path: Path) -> None:
    def boom(px: int, weight: int, charset: str):
        raise RuntimeError("no browser")

    assert overlay_sync.build_atlas(32, 400, renders_dir=tmp_path, rasterize=boom) is None


# -- build_spec with slots ------------------------------------------------------


PANEL = {"w": 800, "h": 480, "orientation": "landscape", "native_w": 800, "native_h": 480}


def _norm_slot(x: int = 10, px: int = 32, weight: int = 700, key: str = "ha:sensor.temp"):
    return {
        "x": x,
        "y": 20,
        "w": 100,
        "h": 40,
        "key": key,
        "suffix": "°",
        "align": "right",
        "px": px,
        "weight": weight,
    }


def _atlas_provider(px: int, weight: int) -> dict[str, Any]:
    return {
        "digest": f"{px:02d}{weight}" + "e" * 10,
        "url": f"/atlas/{px}-{weight}",
        "format": "4bpp-gray",
        "height": px,
        "glyphs": {"0": {"x": 0, "w": 8}},
    }


def test_build_spec_emits_slots_and_atlases() -> None:
    spec = overlay_sync.build_spec(
        frame_digest="a" * 16,
        regions=[],
        panel=PANEL,
        slots=[_norm_slot(), _norm_slot(x=200, key="ha:sensor.hum")],
        atlas_provider=_atlas_provider,
    )
    assert spec is not None
    assert len(spec["atlases"]) == 1
    assert spec["atlases"][0]["id"] == "a1"
    assert [s["atlas"] for s in spec["slots"]] == ["a1", "a1"]
    assert spec["slots"][0] == {
        "id": "s1",
        "x": 10,
        "y": 20,
        "w": 100,
        "h": 40,
        "key": "ha:sensor.temp",
        "align": "right",
        "atlas": "a1",
    }


def test_build_spec_caps_atlas_groups_largest_first() -> None:
    slots = (
        [_norm_slot(x=10 * i, px=32) for i in range(3)]
        + [_norm_slot(x=100 + 10 * i, px=48) for i in range(2)]
        + [_norm_slot(x=300, px=64)]  # smallest group, dropped
    )
    spec = overlay_sync.build_spec(
        frame_digest="b" * 16,
        regions=[],
        panel=PANEL,
        slots=slots,
        atlas_provider=_atlas_provider,
    )
    assert spec is not None
    assert len(spec["atlases"]) == overlay_sync.MAX_ATLASES
    assert len(spec["slots"]) == 5
    heights = {a["height"] for a in spec["atlases"]}
    assert heights == {32, 48}


def test_build_spec_failed_atlas_degrades_to_rect_only() -> None:
    spec = overlay_sync.build_spec(
        frame_digest="c" * 16,
        regions=[{"x": 1, "y": 2, "w": 30, "h": 40}],
        panel=PANEL,
        slots=[_norm_slot()],
        atlas_provider=lambda px, weight: None,
    )
    assert spec is not None
    assert "slots" not in spec and "atlases" not in spec
    assert len(spec["targets"]) == 1


# -- values document -----------------------------------------------------------


def test_values_document_resolves_formats_and_clips() -> None:
    states = {
        "sensor.temp": {"state": "21.4"},
        "sensor.long": {"state": "x" * 60},
        "sensor.gone": {"state": "unavailable"},
    }
    slots = [
        _norm_slot(key="ha:sensor.temp"),
        {**_norm_slot(key="ha:sensor.long"), "suffix": ""},
        _norm_slot(key="ha:sensor.gone"),
        _norm_slot(key="ha:sensor.missing"),
    ]

    def get_state(entity_id: str):
        if entity_id == "sensor.missing":
            raise RuntimeError("404")
        return states[entity_id]

    doc = overlay_sync.values_document(slots, ha_get_state=get_state, now=1234.9)
    assert doc["seq"] == 1234900  # milliseconds
    assert doc["values"]["ha:sensor.temp"] == "21.4°"
    assert len(doc["values"]["ha:sensor.long"]) == overlay_sync.MAX_VALUE_CHARS
    assert "ha:sensor.gone" not in doc["values"]
    assert "ha:sensor.missing" not in doc["values"]


# -- REST surface ---------------------------------------------------------------


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
    a.config["OVERLAY_ATLAS_RASTERIZER"] = _fake_rasterize
    return a


def _register(app: Flask, client, device_id: str = "e1003") -> str:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": device_id, "kind": "esp32_client", "panel_w": 800, "panel_h": 480}
        ),
    )
    assert resp.status_code == 201
    return resp.get_json()["device_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _seed(app: Flask, device_id: str, *, slots: list[dict[str, Any]]) -> None:
    app.config["PUSH_MANAGER"]._latest_renders[device_id] = {
        "digest": "a" * 16,
        "ext": "bin",
        "filename": "aa.bin",
        "composition_digest": "c" * 16,
    }
    save_regions(app.config["RENDERS_DIR"], "c" * 16, [], slots=slots)


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


def test_overlay_spec_carries_slots_and_atlas(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed(app, "e1003", slots=normalize_slots([_raw_slot()]))

    resp = client.get(f"/api/v1/device/e1003/frame/overlay/{'a' * 16}", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["slots"]) == 1
    assert body["slots"][0]["key"] == "ha:sensor.temp"
    atlas = body["atlases"][0]
    assert atlas["id"] == "a1" and atlas["format"] == "4bpp-gray"
    assert atlas["url"].startswith("/api/v1/device/e1003/frame/overlay/atlas/")

    # And the atlas bytes are fetchable at that URL.
    got = client.get(atlas["url"], headers=_auth(token))
    assert got.status_code == 200
    assert len(got.data) > 0
    got.close()


def test_frame_data_serves_values(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed(app, "e1003", slots=normalize_slots([_raw_slot()]))
    _stub_ha(app, {"sensor.temp": "21.4"})

    resp = client.get(f"/api/v1/device/e1003/frame/data?digest={'a' * 16}", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["values"] == {"ha:sensor.temp": "21.4°"}
    assert isinstance(body["seq"], int)


def test_frame_data_404_without_slots_or_frame(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    resp = client.get(f"/api/v1/device/e1003/frame/data?digest={'a' * 16}", headers=_auth(token))
    assert resp.status_code == 404


def test_status_piggybacks_overlay_values_for_capable_device(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed(app, "e1003", slots=normalize_slots([_raw_slot()]))
    _stub_ha(app, {"sensor.temp": "21.4"})

    resp = client.post(
        "/api/v1/device/e1003/status",
        headers=_auth(token),
        data=json.dumps({"overlay": {"schema": 1}, "battery_mv": 4000}),
    )
    assert resp.status_code == 200
    assert resp.get_json()["overlay_values"]["values"] == {"ha:sensor.temp": "21.4°"}

    # No capability on the beat -> no piggyback.
    resp2 = client.post(
        "/api/v1/device/e1003/status",
        headers=_auth(token),
        data=json.dumps({"battery_mv": 4000}),
    )
    assert "overlay_values" not in resp2.get_json()
