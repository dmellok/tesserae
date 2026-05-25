"""End-to-end /send page via the test client."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask
from PIL import Image

from app.main import REPO_ROOT, create_app
from app.push import PushResult


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


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _png_bytes(w: int = 50, h: int = 50) -> bytes:
    img = Image.new("RGB", (w, h), (0, 128, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_send_index_renders_all_tabs(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/send")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    for slug in ("file", "saved", "url", "webpage", "history"):
        assert f"tab-{slug}" in body
        assert f"tab={slug}" in body


def test_file_upload_invokes_push_image(app: Flask) -> None:
    pm = MagicMock()
    pm.push_image.return_value = PushResult(status="sent", page_id="x.png", composition_digest="d")
    app.config["PUSH_MANAGER"] = pm

    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/send/file",
        data={"image": (io.BytesIO(_png_bytes()), "x.png")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    pm.push_image.assert_called_once()
    _args, kwargs = pm.push_image.call_args
    assert kwargs["source_label"] == "x.png"


def test_file_upload_rejects_missing_file(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/send/file", data={}, follow_redirects=True)
    assert b"No file selected" in resp.data


def test_saved_dashboard_invokes_push(app: Flask) -> None:
    pm = MagicMock()
    pm.push.return_value = PushResult(status="sent", page_id="home")
    app.config["PUSH_MANAGER"] = pm

    client = app.test_client()
    _sign_in(client)
    client.post("/send/page", data={"page_id": "home"}, follow_redirects=False)
    pm.push.assert_called_once_with("home")


def test_url_invokes_push_url_image(app: Flask) -> None:
    pm = MagicMock()
    pm.push_url_image.return_value = PushResult(status="sent", page_id="https://x")
    app.config["PUSH_MANAGER"] = pm

    client = app.test_client()
    _sign_in(client)
    client.post(
        "/send/url",
        data={"url": "https://example.com/img.png"},
        follow_redirects=False,
    )
    pm.push_url_image.assert_called_once_with("https://example.com/img.png")


def test_webpage_invokes_push_webpage_with_viewport(app: Flask) -> None:
    pm = MagicMock()
    pm.push_webpage.return_value = PushResult(status="sent", page_id="https://x")
    app.config["PUSH_MANAGER"] = pm

    client = app.test_client()
    _sign_in(client)
    client.post(
        "/send/webpage",
        data={"url": "https://example.com", "viewport_w": "800", "viewport_h": "600"},
        follow_redirects=False,
    )
    pm.push_webpage.assert_called_once_with("https://example.com", viewport_w=800, viewport_h=600)


def test_history_lists_event_log_rows(app: Flask, tmp_path: Path) -> None:
    log = app.config["EVENT_LOG"]
    log.record(type="push", source="page", target="home", status="sent", digest="abc")
    log.record(type="push", source="file", target="x.png", status="failed", error="boom")

    client = app.test_client()
    _sign_in(client)
    body = client.get("/send?tab=history").get_data(as_text=True)
    assert "home" in body
    assert "x.png" in body
    assert "boom" in body


def test_resend_invokes_republish(app: Flask) -> None:
    pm = MagicMock()
    pm.republish.return_value = PushResult(status="sent", page_id="resent")
    app.config["PUSH_MANAGER"] = pm
    client = app.test_client()
    _sign_in(client)
    client.post("/send/history/42/resend", follow_redirects=False)
    pm.republish.assert_called_once_with(42)


def test_delete_invokes_delete_history(app: Flask) -> None:
    pm = MagicMock()
    pm.delete_history.return_value = True
    app.config["PUSH_MANAGER"] = pm
    client = app.test_client()
    _sign_in(client)
    client.post("/send/history/7/delete", follow_redirects=False)
    pm.delete_history.assert_called_once_with(7)


def test_nav_links_to_send(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings").get_data(as_text=True)
    assert "/send" in body
