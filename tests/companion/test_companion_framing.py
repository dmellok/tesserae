"""Companion contract 0.6 ``image_framing``: capability, validation, adapter.

Capability advertisement and request validation run through the real Flask
app with a fake PushManager (same pattern as ``test_companion_jobs``). The
framing application itself is exercised against a constructed rotated-EXIF
JPEG so the crop provably resolves in orientation-normalized source space,
and the History adapter round-trips the original intent while resolved
rectangles stay internal.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest
from flask import Flask
from PIL import Image

from app import companion_api
from app.companion_history import history_item
from app.main import REPO_ROOT, create_app
from app.push import _apply_framing
from app.state.event_log import EventLog

from ._schema import schema_for

_FMT = jsonschema.FormatChecker()

_FRAMING = {"focus_x": 0.62, "focus_y": 0.38, "zoom": 1.35}


class _FakePush:
    def __init__(self) -> None:
        self.image_calls: list[dict[str, Any]] = []
        self._next_event_id = 100

    def push_image(self, image_bytes: bytes, **kwargs: Any) -> Any:
        self.image_calls.append(
            {
                "device_id": kwargs["device_id"],
                "fit": kwargs["fit"],
                "framing": kwargs.get("framing"),
            }
        )
        self._next_event_id += 1
        return SimpleNamespace(status="sent", error=None, event_id=self._next_event_id)


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


_CLIENT = {
    "name": "Test iPhone",
    "platform": "ios",
    "app_version": "0.1.0",
    "installation_id": "A1B2C3D4-E5F6-47A8-9012-3456789ABCDE",
}


def _token(app: Flask) -> str:
    code = app.config["COMPANION_PAIRING_STORE"].issue(note="t").code
    resp = app.test_client().post(
        "/api/app/v1/pair",
        data=json.dumps({"code": code, "client": _CLIENT}),
        content_type="application/json",
    )
    return str(resp.get_json()["token"])


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


def _auth(token: str, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


def _poll(app: Flask, token: str, job_id: str, timeout_s: float = 5.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    client = app.test_client()
    while time.time() < deadline:
        resp = client.get(f"/api/app/v1/jobs/{job_id}", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.get_json()
        if body["job"]["status"] in ("succeeded", "failed"):
            return body["job"]
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach a terminal state in {timeout_s}s")


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (12, 8), (200, 50, 50)).save(buf, "PNG")
    return buf.getvalue()


def _post_image(app: Flask, token: str, idem: str, spec: dict[str, Any]) -> Any:
    return app.test_client().post(
        "/api/app/v1/images",
        headers=_auth(token, idem),
        data={
            "image": (io.BytesIO(_png_bytes()), "photo.png", "image/png"),
            "request": json.dumps(spec),
        },
        content_type="multipart/form-data",
    )


# -- capability ----------------------------------------------------------


def test_capabilities_advertise_framing_with_mandatory_zoom_bound(app: Flask) -> None:
    body = app.test_client().get("/api/app/v1").get_json()
    jsonschema.validate(body, schema_for("Capabilities"), format_checker=_FMT)
    assert "image_framing" in body["features"]
    # Contract 0.6: the editor bound is mandatory whenever the feature is
    # advertised, so a client never hard-codes the zoom range.
    assert body["limits"]["image_framing_max_zoom"] == companion_api.IMAGE_FRAMING_MAX_ZOOM


# -- request validation --------------------------------------------------


def test_framed_push_threads_intent_per_target(app: Flask) -> None:
    fake = _FakePush()
    app.config["PUSH_MANAGER"] = fake
    a = _seed_device(app, "kitchen")
    b = _seed_device(app, "desk")
    token = _token(app)

    resp = _post_image(
        app,
        token,
        "framing-ok-000001",
        {
            "device_ids": [a, b],
            "fit": "fill",
            "framing": _FRAMING,
            "override_quiet_hours": True,
        },
    )
    assert resp.status_code == 202, resp.get_data(as_text=True)
    job = _poll(app, token, resp.get_json()["job"]["id"])
    assert job["status"] == "succeeded"
    assert sorted(call["device_id"] for call in fake.image_calls) == [b, a]
    for call in fake.image_calls:
        assert call["fit"] == "fill"
        assert call["framing"] == pytest.approx(_FRAMING)


def test_push_without_framing_keeps_the_existing_path(app: Flask) -> None:
    fake = _FakePush()
    app.config["PUSH_MANAGER"] = fake
    device = _seed_device(app)
    token = _token(app)

    resp = _post_image(
        app,
        token,
        "framing-absent-0001",
        {"device_ids": [device], "fit": "fill", "override_quiet_hours": True},
    )
    assert resp.status_code == 202
    _poll(app, token, resp.get_json()["job"]["id"])
    assert fake.image_calls[0]["framing"] is None


@pytest.mark.parametrize(
    "fit,framing",
    [
        # Framing refines the ordinary Fill crop; any other mode has none.
        ("blur", _FRAMING),
        ("fit", _FRAMING),
        ("fill", {"focus_x": -0.1, "focus_y": 0.5, "zoom": 1.5}),
        ("fill", {"focus_x": 0.5, "focus_y": 1.5, "zoom": 1.5}),
        ("fill", {"focus_x": 0.5, "focus_y": 0.5, "zoom": 0.99}),
        ("fill", {"focus_x": 0.5, "focus_y": 0.5, "zoom": 4.01}),
        # bool is an int subclass; zoom: true must not validate as 1.
        ("fill", {"focus_x": 0.5, "focus_y": 0.5, "zoom": True}),
        ("fill", {"focus_x": "0.5", "focus_y": 0.5, "zoom": 1.5}),
        ("fill", {"focus_x": 0.5, "focus_y": 0.5}),
        ("fill", "framing"),
    ],
)
def test_invalid_framing_is_rejected(app: Flask, fit: str, framing: Any) -> None:
    app.config["PUSH_MANAGER"] = _FakePush()
    device = _seed_device(app)
    token = _token(app)

    resp = _post_image(
        app,
        token,
        "framing-bad-000001",
        {
            "device_ids": [device],
            "fit": fit,
            "framing": framing,
            "override_quiet_hours": False,
        },
    )
    assert resp.status_code == 400
    body = resp.get_json()
    jsonschema.validate(body, schema_for("ErrorResponse"), format_checker=_FMT)
    assert body["error"]["code"] == "invalid_framing"


def test_max_zoom_boundary_is_inclusive(app: Flask) -> None:
    fake = _FakePush()
    app.config["PUSH_MANAGER"] = fake
    device = _seed_device(app)
    token = _token(app)

    resp = _post_image(
        app,
        token,
        "framing-edge-00001",
        {
            "device_ids": [device],
            "fit": "fill",
            "framing": {"focus_x": 0.0, "focus_y": 1.0, "zoom": 4},
            "override_quiet_hours": True,
        },
    )
    assert resp.status_code == 202
    job = _poll(app, token, resp.get_json()["job"]["id"])
    assert job["status"] == "succeeded"
    assert fake.image_calls[0]["framing"] == {"focus_x": 0.0, "focus_y": 1.0, "zoom": 4.0}


# -- framing application (the real adapter) ------------------------------


def _exif_top_green_jpeg() -> bytes:
    """A phone-style photo: a 40x30 stored buffer whose LEFT half is green
    and RIGHT half is magenta, tagged orientation 6 (rotate 90 CW for
    display). Displayed upright it is 30x40 with the GREEN half on TOP, so
    a focus in normalized display space provably addresses the upright
    image: resolving against the raw buffer instead would slice
    left/right, not top/bottom.
    """
    img = Image.new("RGB", (40, 30), (0, 200, 0))
    for x in range(20, 40):
        for y in range(30):
            img.putpixel((x, y), (200, 0, 200))
    exif = img.getexif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", exif=exif, quality=95)
    return buffer.getvalue()


def _dominant(png_bytes: bytes) -> tuple[int, int, int]:
    with Image.open(io.BytesIO(png_bytes)) as img:
        w, h = img.size
        return img.convert("RGB").getpixel((w // 2, h // 2))


def test_apply_framing_resolves_in_orientation_normalized_space() -> None:
    photo = _exif_top_green_jpeg()
    panel = {"w": 100, "h": 100}

    top = _apply_framing(photo, panel, {"focus_x": 0.5, "focus_y": 0.25, "zoom": 2.0})
    bottom = _apply_framing(photo, panel, {"focus_x": 0.5, "focus_y": 0.75, "zoom": 2.0})

    for framed in (top, bottom):
        with Image.open(io.BytesIO(framed)) as img:
            assert img.size == (100, 100)

    r, g, b = _dominant(top)
    assert g > 150 and r < 100 and b < 100, (r, g, b)
    r, g, b = _dominant(bottom)
    assert r > 150 and b > 150 and g < 100, (r, g, b)


def test_apply_framing_zoom_one_centre_matches_plain_fill() -> None:
    from app.quantizer import fit_to_panel

    photo = _exif_top_green_jpeg()
    framed = _apply_framing(photo, {"w": 60, "h": 80}, {"focus_x": 0.5, "focus_y": 0.5, "zoom": 1})

    from PIL import ImageOps

    with Image.open(io.BytesIO(photo)) as img:
        plain = fit_to_panel(
            ImageOps.exif_transpose(img).convert("RGB"),
            target_w=60,
            target_h=80,
            scale="fill",
        )
    with Image.open(io.BytesIO(framed)) as out:
        assert out.size == plain.size
        # JPEG decode is deterministic, so zoom 1 centred framing and the
        # ordinary Fill path must produce identical pixels.
        assert list(out.convert("RGB").getdata()) == list(plain.getdata())


# -- history round-trip --------------------------------------------------


def test_history_returns_the_original_intent_only(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db", cap=50)
    event_id = log.record(
        type="push",
        source="companion",
        target="Shared photo",
        status="sent",
        digest="0123456789abcdef",
        duration_s=1.0,
        extra={"device_ids": ["desk"], "fit": "fill", "framing": dict(_FRAMING)},
    )
    row = log.get(event_id)
    assert row is not None
    item = history_item(row, tmp_path / "renders")
    assert item["fit"] == "fill"
    assert item["framing"] == pytest.approx(_FRAMING)
    # Resolved per-target rectangles are derived server state and must
    # never surface on the contract item.
    assert "crop" not in item and "source_crop" not in item


def test_history_hides_malformed_framing(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db", cap=50)
    event_id = log.record(
        type="push",
        source="companion",
        target="Shared photo",
        status="sent",
        extra={"device_ids": ["desk"], "fit": "fill", "framing": {"focus_x": "a"}},
    )
    row = log.get(event_id)
    assert row is not None
    assert history_item(row, tmp_path / "renders")["framing"] is None
