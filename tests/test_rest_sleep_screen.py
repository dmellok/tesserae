"""Single-request frame routes for clients that cannot walk the ``/frame``
envelope: ``GET /frame.bmp`` and ``GET /frames.json``.

The client these exist for makes exactly one HTTP request from a
declarative download handler, follows no redirects, attaches no headers
of its own, and renames whatever it downloaded over its live sleep screen
on ANY 2xx. That last property is what most of these tests are about: a
204, or a 200 with an empty body, would replace a working screen with a
zero-byte file, so every "nothing to serve" case has to be a 404.
"""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import pytest
from flask import Flask
from PIL import Image

from app.main import REPO_ROOT, create_app

PANEL_W = 800
PANEL_H = 480
COMP_DIGEST = "comp0001"


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


def _register(app: Flask, client, device_id: str = "reader") -> str:
    """Register a device whose own frame format is a packed ``.bin``, so
    the BMP route is provably not just echoing the device's artifact."""
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": device_id,
                "kind": "pico_bin_client",
                "panel_w": PANEL_W,
                "panel_h": PANEL_H,
            }
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["device_token"]


def _seed_frame(app: Flask, device_id: str = "reader") -> None:
    """Fake a rendered frame: a latest-render entry plus the composition
    PNG on disk that the BMP route re-transforms from."""
    push_mgr = app.config["PUSH_MANAGER"]
    renders_dir = Path(app.config["RENDERS_DIR"])
    renders_dir.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (PANEL_W, PANEL_H), "white")
    for x in range(0, PANEL_W, 2):
        for y in range(0, PANEL_H, 3):
            img.putpixel((x, y), (0, 0, 0))
    img.save(renders_dir / f"{COMP_DIGEST}.png")
    push_mgr._latest_renders[device_id] = {
        "digest": "art0001",
        "ext": "bin",
        "filename": "art0001.bin",
        "composition_digest": COMP_DIGEST,
    }


def _bmp_dims(body: bytes) -> tuple[int, int]:
    """Width + height out of a BITMAPINFOHEADER. Height is stored signed
    and negative for a top-down bitmap, so take the magnitude."""
    width, height = struct.unpack_from("<ii", body, 18)
    return width, abs(height)


# -- GET /frame.bmp ------------------------------------------------------


def test_returns_bmp_bytes_and_content_type(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed_frame(app)
    resp = client.get(
        "/api/v1/device/reader/frame.bmp", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.mimetype == "image/bmp"
    body = resp.get_data()
    assert body[:2] == b"BM"
    assert len(body) > 2


def test_body_matches_the_devices_panel_dimensions(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed_frame(app)
    resp = client.get(
        "/api/v1/device/reader/frame.bmp", headers={"Authorization": f"Bearer {token}"}
    )
    assert _bmp_dims(resp.get_data()) == (PANEL_W, PANEL_H)


def test_body_is_a_decodable_image(app: Flask) -> None:
    """A complete, parseable image, not just the right magic bytes: a
    truncated body is the failure mode this route exists to prevent."""
    client = app.test_client()
    token = _register(app, client)
    _seed_frame(app)
    resp = client.get(
        "/api/v1/device/reader/frame.bmp", headers={"Authorization": f"Bearer {token}"}
    )
    with Image.open(io.BytesIO(resp.get_data())) as img:
        img.load()
        assert img.size == (PANEL_W, PANEL_H)


def test_stays_under_the_client_download_cap(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed_frame(app)
    resp = client.get(
        "/api/v1/device/reader/frame.bmp", headers={"Authorization": f"Bearer {token}"}
    )
    assert len(resp.get_data()) < 1024 * 1024


def test_no_frame_is_404_and_never_204(app: Flask) -> None:
    """The envelope route answers 204 here. This one must not: the client
    renames its download over the live sleep screen on any 2xx, so a 204
    would blank a working screen."""
    client = app.test_client()
    token = _register(app, client)
    resp = client.get(
        "/api/v1/device/reader/frame.bmp", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404
    assert resp.status_code != 204


def test_missing_composition_is_404_not_a_partial_200(app: Flask) -> None:
    """A latest-render entry whose composition PNG is gone (pruned, or a
    pre-0.8.6 entry with no digest) has nothing to transform."""
    client = app.test_client()
    token = _register(app, client)
    _seed_frame(app)
    Path(app.config["RENDERS_DIR"], f"{COMP_DIGEST}.png").unlink()
    resp = client.get(
        "/api/v1/device/reader/frame.bmp", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404


def test_bad_token_is_401(app: Flask) -> None:
    client = app.test_client()
    _register(app, client)
    _seed_frame(app)
    resp = client.get("/api/v1/device/reader/frame.bmp", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_missing_token_is_401(app: Flask) -> None:
    client = app.test_client()
    _register(app, client)
    _seed_frame(app)
    assert client.get("/api/v1/device/reader/frame.bmp").status_code == 401


def test_query_token_is_accepted(app: Flask) -> None:
    """The on-device downloader owns the request and cannot attach
    headers, so the token has to be able to ride in the URL."""
    client = app.test_client()
    token = _register(app, client)
    _seed_frame(app)
    resp = client.get(f"/api/v1/device/reader/frame.bmp?k={token}")
    assert resp.status_code == 200
    assert resp.get_data()[:2] == b"BM"


def test_bad_query_token_is_401(app: Flask) -> None:
    client = app.test_client()
    _register(app, client)
    _seed_frame(app)
    assert client.get("/api/v1/device/reader/frame.bmp?k=nope").status_code == 401


def test_query_token_is_rejected_on_the_envelope_route(app: Flask) -> None:
    """The query-parameter form is opt-in per route, not a global auth
    change: a query string leaks into logs and history in a way a header
    doesn't, so it stays confined to the routes that can't use headers."""
    client = app.test_client()
    token = _register(app, client)
    _seed_frame(app)
    assert client.get(f"/api/v1/device/reader/frame?k={token}").status_code == 401


def test_serves_bytes_without_redirecting(app: Flask) -> None:
    """The client follows no redirects, so a 3xx to /renders/ would leave
    it with nothing."""
    client = app.test_client()
    token = _register(app, client)
    _seed_frame(app)
    resp = client.get(
        "/api/v1/device/reader/frame.bmp",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "Location" not in resp.headers


def test_bmp_is_served_regardless_of_the_devices_frame_format(app: Flask) -> None:
    """The device's own artifact is a packed ``.bin``; adding this route
    must not change what the envelope route hands that same device."""
    client = app.test_client()
    token = _register(app, client)
    _seed_frame(app)
    envelope = client.get(
        "/api/v1/device/reader/frame", headers={"Authorization": f"Bearer {token}"}
    )
    assert envelope.get_json()["format"] == "bin"
    bmp = client.get(
        "/api/v1/device/reader/frame.bmp", headers={"Authorization": f"Bearer {token}"}
    )
    assert bmp.get_data()[:2] == b"BM"


def test_repeat_requests_are_byte_identical(app: Flask) -> None:
    """Unchanged content re-serves the cached transform rather than
    re-running the quantise, and the two must agree."""
    client = app.test_client()
    token = _register(app, client)
    _seed_frame(app)
    headers = {"Authorization": f"Bearer {token}"}
    first = client.get("/api/v1/device/reader/frame.bmp", headers=headers)
    second = client.get("/api/v1/device/reader/frame.bmp", headers=headers)
    assert first.get_data() == second.get_data()
    assert first.headers["ETag"] == second.headers["ETag"]


# -- GET /frames.json ----------------------------------------------------


def test_frames_index_items_is_an_array(app: Flask) -> None:
    """Clients cast ``items`` to an array; an object would silently yield
    an empty picker."""
    client = app.test_client()
    token = _register(app, client)
    _seed_frame(app)
    resp = client.get(
        "/api/v1/device/reader/frames.json", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body["items"], list)
    assert len(body["items"]) >= 1


def test_frames_index_url_resolves_to_the_frame_route(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed_frame(app)
    resp = client.get(
        "/api/v1/device/reader/frames.json", headers={"Authorization": f"Bearer {token}"}
    )
    item = resp.get_json()["items"][0]
    assert item["url"].startswith("http://")
    assert "/api/v1/device/reader/frame.bmp" in item["url"]
    # Absolute, and directly downloadable with no second hop: fetch the
    # advertised URL exactly as given and expect image bytes back.
    followed = client.get(item["url"], follow_redirects=False)
    assert followed.status_code == 200
    assert followed.mimetype == "image/bmp"
    assert followed.get_data()[:2] == b"BM"


def test_frames_index_titles_are_ascii(app: Flask) -> None:
    """The target's e-ink UI font has no guaranteed coverage for arrows or
    symbols; a missing glyph renders as a tofu box."""
    client = app.test_client()
    token = _register(app, client)
    resp = client.get(
        "/api/v1/device/reader/frames.json", headers={"Authorization": f"Bearer {token}"}
    )
    for item in resp.get_json()["items"]:
        for key in ("id", "title", "subtitle"):
            assert item[key].isascii(), f"{key} is not ASCII: {item[key]!r}"


def test_frames_index_is_listable_before_any_frame_exists(app: Flask) -> None:
    """The picker is a list of what can be pulled, not a claim that a
    frame is ready; the download itself 404s until one is."""
    client = app.test_client()
    token = _register(app, client)
    resp = client.get(
        "/api/v1/device/reader/frames.json", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert len(resp.get_json()["items"]) == 1


def test_frames_index_bad_token_is_401(app: Flask) -> None:
    client = app.test_client()
    _register(app, client)
    assert (
        client.get(
            "/api/v1/device/reader/frames.json", headers={"Authorization": "Bearer nope"}
        ).status_code
        == 401
    )
