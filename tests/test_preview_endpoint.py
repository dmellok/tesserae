"""Stable per-device preview alias — ``GET /preview/<device_id>.png``.

The URL is invariant across pushes (no content-addressed digest in the
path), so HA's generic camera + Grafana panels + wallboards can poll a
single bookmark and always see the most recent composition. The route
lives in ``app_factory`` next to ``/renders/`` and shares the LAN-bypass
auth path defined in ``auth.py``.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask


def _seed_latest_render(app: Flask, device_id: str, comp_digest: str, png_bytes: bytes) -> None:
    """Drop a fake composition PNG on disk and stamp the push manager's
    in-memory ``_latest_renders`` map so /preview/<id>.png finds something
    to serve. Skips the actual push pipeline (Playwright + MQTT)."""
    push_mgr = app.config["PUSH_MANAGER"]
    renders_dir: Path = app.config["RENDERS_DIR"]
    renders_dir.mkdir(parents=True, exist_ok=True)
    (renders_dir / f"{comp_digest}.png").write_bytes(png_bytes)
    push_mgr._latest_renders[device_id] = {
        "digest": "ignored_per_renderer_digest",
        "ext": "bin",
        "filename": "ignored.bin",
        "renderer_id": "test_renderer",
        "timestamp": 1.0,
        "composition_digest": comp_digest,
    }


def test_preview_serves_latest_composition_png(app: Flask) -> None:
    """The endpoint serves the *composition* PNG (Playwright output)
    even when the device's per-renderer artifact is a packed binary —
    so the picture is always viewable."""
    png = b"\x89PNG\r\n\x1a\nfakepng-bytes"
    _seed_latest_render(app, "bin_mini", "abc123def4567890", png)

    with app.test_client() as client:
        resp = client.get("/preview/bin_mini.png")
        body = resp.data
        status = resp.status_code
        cache = resp.headers.get("Cache-Control")
        resp.close()

    assert status == 200
    assert body == png
    # Stable URL + content that changes per push → must not be cached.
    assert cache == "no-store, max-age=0"


def test_preview_returns_404_when_no_render_yet(app: Flask) -> None:
    """A fresh device with no successful push should 404 — HA's camera
    entity then shows 'unavailable' instead of a stale or fake frame."""
    client = app.test_client()
    resp = client.get("/preview/never_pushed.png")
    assert resp.status_code == 404


def test_preview_rejects_invalid_device_ids(app: Flask) -> None:
    """Validation mirrors ``DEVICE_ID_RE`` from device_service. Path-
    traversal attempts (../) and out-of-spec characters 404 cleanly."""
    client = app.test_client()
    assert client.get("/preview/A_BAD_ID.png").status_code == 404
    # Path traversal — Flask's URL converter rejects the dot-slash but
    # the matcher's lowercase rule covers anything that gets through.
    assert client.get("/preview/dev/escape.png").status_code == 404


def test_preview_falls_back_to_per_renderer_artifact_when_no_comp_digest(
    app: Flask,
) -> None:
    """Latest-render entries written before 0.8.5 don't carry a
    composition_digest. Serve the per-renderer artifact in that case
    rather than 404 — it's the right bytes, just not always a viewable
    image. The next push refreshes the entry with the new shape."""
    png = b"\x89PNGlegacyfallback"
    push_mgr = app.config["PUSH_MANAGER"]
    renders_dir: Path = app.config["RENDERS_DIR"]
    renders_dir.mkdir(parents=True, exist_ok=True)
    (renders_dir / "legacy123.png").write_bytes(png)
    push_mgr._latest_renders["pre_085_device"] = {
        "digest": "legacy123",
        "ext": "png",
        "filename": "legacy123.png",
        "renderer_id": "pi_png",
        "timestamp": 1.0,
        # composition_digest absent — the pre-0.8.5 shape.
    }

    with app.test_client() as client:
        resp = client.get("/preview/pre_085_device.png")
        body = resp.data
        status = resp.status_code
        resp.close()
    assert status == 200
    assert body == png
