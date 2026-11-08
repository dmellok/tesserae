"""Settings → Logs: the in-memory log ring and its page.

Covers the buffer (levels, sequence, redaction, capacity), the watch level
as a view filter that never changes what's recorded, the record level as a
runtime change to the root logger, the JSON poll, download, clear, and that
the page is behind the admin gate like /events.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from app import log_buffer
from app.log_buffer import LogBuffer, parse_level, redact
from app.main import REPO_ROOT, create_app


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Flask]:
    root = logging.getLogger()
    before = root.level
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    root.setLevel(logging.INFO)
    a.config["LOG_BUFFER"].clear()
    yield a
    root.setLevel(before)


def _signed_in(app: Flask):
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    return client


# -- the buffer --------------------------------------------------------


def test_buffer_keeps_newest_lines_with_increasing_seq() -> None:
    buf = LogBuffer(capacity=3)
    lg = logging.getLogger("tesserae.test.ring")
    lg.addHandler(buf)
    lg.setLevel(logging.DEBUG)
    lg.propagate = False
    try:
        for i in range(5):
            lg.info("line %d", i)
    finally:
        lg.removeHandler(buf)
        lg.propagate = True
    lines = buf.lines()
    assert [ln.message for ln in lines] == ["line 2", "line 3", "line 4"]
    assert [ln.seq for ln in lines] == [3, 4, 5]
    assert [ln.message for ln in buf.lines(after=4)] == ["line 4"]


def test_buffer_filters_by_level_and_text_and_keeps_tracebacks() -> None:
    buf = LogBuffer()
    lg = logging.getLogger("tesserae.test.filter")
    lg.addHandler(buf)
    lg.setLevel(logging.DEBUG)
    lg.propagate = False
    try:
        lg.debug("quiet detail")
        lg.warning("panel slow")
        try:
            raise ValueError("boom")
        except ValueError:
            lg.exception("render failed")
    finally:
        lg.removeHandler(buf)
        lg.propagate = True
    assert [ln.level for ln in buf.lines(min_level=logging.WARNING)] == ["warning", "error"]
    assert [ln.message for ln in buf.lines(query="PANEL")] == ["panel slow"]
    err = buf.lines(min_level=logging.ERROR)[0]
    assert "ValueError: boom" in (err.exc or "")
    assert buf.counts()["debug"] == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("GET /api/v1/device/kitchen/frame.bmp?k=abcd1234XYZ HTTP/1.1", "?k=… HTTP/1.1"),
        ("headers {'Authorization': 'Bearer eyJ0eXAi.abc'}", "Bearer …'}"),
        ("X-Tesserae-Token: a1b2c3d4", "X-Tesserae-Token: …"),
        ("/renders/abc.bin?sig=deadbeef&w=240", "?sig=…&w=240"),
    ],
)
def test_credentials_are_masked_before_a_line_is_kept(raw: str, expected: str) -> None:
    out = redact(raw)
    assert expected in out
    assert "abcd1234XYZ" not in out and "eyJ0eXAi" not in out and "a1b2c3d4" not in out


def test_parse_level_accepts_names_and_numbers_only() -> None:
    assert parse_level("WARNING") == logging.WARNING
    assert parse_level("warn") == logging.WARNING
    assert parse_level(10) == logging.DEBUG
    assert parse_level("verbose") is None
    assert parse_level(True) is None


def test_install_is_one_shared_root_handler(app: Flask) -> None:
    root = logging.getLogger()
    assert log_buffer.install() is app.config["LOG_BUFFER"]
    assert sum(1 for h in root.handlers if isinstance(h, LogBuffer)) == 1


# -- the page and its endpoints -----------------------------------------


def test_logs_page_is_gated_like_events(app: Flask) -> None:
    resp = app.test_client().get("/logs")
    assert resp.status_code in (302, 401, 403)


def test_page_lists_lines_at_the_watch_level_and_is_in_the_nav(app: Flask) -> None:
    client = _signed_in(app)
    lg = logging.getLogger("tesserae.test.page")
    lg.info("an info line")
    lg.warning("a warning line")
    body = client.get("/logs?level=warning").get_data(as_text=True)
    assert "a warning line" in body
    assert "an info line" not in body
    assert 'href="/logs"' in body or '/logs"' in body
    body = client.get("/logs").get_data(as_text=True)
    assert "an info line" in body and "a warning line" in body


def test_watching_debug_says_the_server_does_not_record_it(app: Flask) -> None:
    client = _signed_in(app)
    body = client.get("/logs?level=debug").get_data(as_text=True)
    assert "data-record-gap" in body
    assert "Record debug until restart" in body
    # Watching never changes the record level.
    assert logging.getLogger().level == logging.INFO


def test_record_level_change_takes_effect_and_is_logged(app: Flask) -> None:
    client = _signed_in(app)
    resp = client.post("/logs/record_level", json={"level": "debug"})
    assert resp.status_code == 200
    assert resp.get_json()["record_level"] == "debug"
    assert logging.getLogger().level == logging.DEBUG
    logging.getLogger("tesserae.test.dbg").debug("now visible")
    lines = client.get("/logs/lines?level=debug").get_json()["lines"]
    messages = [ln["message"] for ln in lines]
    assert "now visible" in messages
    assert any("changed from info to debug" in m for m in messages)
    assert "data-record-gap" not in client.get("/logs?level=debug").get_data(as_text=True)


def test_record_level_rejects_junk(app: Flask) -> None:
    client = _signed_in(app)
    assert client.post("/logs/record_level", json={"level": "loud"}).status_code == 400
    assert logging.getLogger().level == logging.INFO


def test_lines_endpoint_returns_only_newer_lines(app: Flask) -> None:
    client = _signed_in(app)
    lg = logging.getLogger("tesserae.test.poll")
    lg.warning("first")
    seq = client.get("/logs/lines?level=warning").get_json()["last_seq"]
    lg.warning("second")
    body = client.get(f"/logs/lines?level=warning&after={seq}").get_json()
    assert [ln["message"] for ln in body["lines"]] == ["second"]


def test_download_is_plain_text_at_the_watch_level(app: Flask) -> None:
    client = _signed_in(app)
    lg = logging.getLogger("tesserae.test.dl")
    lg.info("kept info")
    lg.error("kept error")
    resp = client.get("/logs/download?level=error")
    assert resp.mimetype == "text/plain"
    assert "attachment" in resp.headers["Content-Disposition"]
    text = resp.get_data(as_text=True)
    assert "ERROR tesserae.test.dl, kept error" in text
    assert "kept info" not in text


def test_clear_empties_the_ring(app: Flask) -> None:
    client = _signed_in(app)
    logging.getLogger("tesserae.test.clear").warning("gone soon")
    assert client.post("/logs/clear").status_code == 200
    assert client.get("/logs/lines?level=debug").get_json()["lines"] == []
