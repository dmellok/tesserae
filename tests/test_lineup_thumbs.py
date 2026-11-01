"""Small immutable Lineups images, resolved once while building the view."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from flask import Flask
from PIL import Image, ImageDraw

from app.main import REPO_ROOT, create_app
from app.state.page_store import Page


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(testing=False, data_root=tmp_path, plugins_dir=REPO_ROOT / "plugins")
    a.config["TESTING"] = True
    a.config["SETTINGS_STORE"].patch_section("app", {"onboarded": True})
    a.config["SETTINGS_STORE"].patch_section("auth", {"disabled": True})
    a.config["PAGE_STORE"].save(Page(id="news", name="News"))
    return a


def _push(app: Flask, colour: str = "white") -> str:
    im = Image.new("RGB", (1440, 2560), colour)
    draw = ImageDraw.Draw(im)
    for n in range(500):
        x, y = n * 127 % 1400, n * 173 % 2520
        draw.rectangle((x, y, x + 40, y + 40), fill=(n % 256, n * 7 % 256, n * 13 % 256))
        draw.text((x, y), f"News {n}", fill="black")
    out = io.BytesIO()
    im.save(out, "PNG")
    png = out.getvalue()
    digest = hashlib.sha256(png).hexdigest()
    renders = Path(app.config["DATA_ROOT"]) / "core" / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    (renders / f"{digest}.png").write_bytes(png)
    app.config["EVENT_LOG"].record(
        type="push",
        source="page",
        target="news",
        status="sent",
        digest=digest,
        extra={"composition_digest": digest},
    )
    return digest


def _url(app: Flask, prefix: str = "") -> str:
    from app.deck_routes import _page_thumbs

    with app.test_request_context("/decks", environ_overrides={"SCRIPT_NAME": prefix}):
        return _page_thumbs(app.config["PAGE_STORE"].list())["news"]


def test_versioned_small_image_is_cached_without_event_queries(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = _push(app)
    url = _url(app)
    assert digest in url
    assert _url(app, "/api/hassio_ingress/test").startswith("/api/hassio_ingress/test/")
    monkeypatch.setattr(
        app.config["EVENT_LOG"],
        "list",
        Mock(side_effect=AssertionError("thumbnail queried events")),
    )
    monkeypatch.setattr(
        app.config["EVENT_LOG"],
        "latest_pushes",
        Mock(side_effect=AssertionError("thumbnail queried events")),
    )
    monkeypatch.setattr(
        app.config["PAGE_STORE"], "get", Mock(side_effect=AssertionError("thumbnail queried pages"))
    )
    client = app.test_client()
    r = client.get(url, buffered=True)
    assert r.status_code == 200
    assert "max-age=31536000" in r.headers["Cache-Control"]
    assert "immutable" in r.headers["Cache-Control"]
    assert r.mimetype == "image/webp"
    assert len(r.data) < 40_000
    with Image.open(io.BytesIO(r.data)) as image:
        assert image.size == (202, 360)
    etag = r.headers["ETag"]
    assert client.get(url, buffered=True, headers={"If-None-Match": etag}).status_code == 304
    # A cached variant can be served without opening or resizing the source.
    monkeypatch.setattr(Image, "open", Mock(side_effect=AssertionError("resized twice")))
    assert client.get(url, buffered=True).data == r.data


def test_same_dashboard_reuses_url_until_next_push(app: Flask) -> None:
    from app.state.deck_model import Deck, DeckPage

    _push(app)
    for n in range(2):
        app.config["DECK_STORE"].upsert(
            Deck(id=f"lu_{n}", name=f"Lineup {n}", pages=[DeckPage(page_id="news")])
        )
    c = app.test_client()
    first = c.get("/decks/view.json").get_json()
    urls = [step["thumb_url"] for lu in first["unassigned"] for step in lu["steps"]]
    assert len(urls) == 2 and urls[0] == urls[1] and urls[0]
    assert _url(app) == urls[0]
    digest = _push(app, "red")
    assert _url(app) != urls[0] and digest in _url(app)
    assert c.get(urls[0], buffered=True).status_code == 200


def test_cold_dashboard_is_placeholder_then_cached_snapshot(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import preview_cache
    from app.state.deck_model import Deck, DeckPage

    app.config["DECK_STORE"].upsert(Deck(id="lu", name="Lineup", pages=[DeckPage(page_id="news")]))
    queued: list[dict[str, Any]] = []
    monkeypatch.setattr(preview_cache, "submit", lambda **kw: queued.append(kw))
    c = app.test_client()
    vm = c.get("/decks/view.json").get_json()
    assert vm["unassigned"][0]["steps"][0]["thumb_url"] == ""
    body = c.get("/decks").get_data(as_text=True)
    assert 'class="lu-thumb-ph"' in body and "<iframe" not in body
    assert queued and all(not q.get("force") for q in queued)
    task = queued[0]
    task["cache_path"].parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 500), "blue").save(task["cache_path"], "PNG")
    queued.clear()
    url = _url(app)
    assert url and queued == []
    r = c.get(url, buffered=True)
    assert r.status_code == 200 and "immutable" in r.headers["Cache-Control"]
    assert (
        c.get(url, buffered=True, headers={"If-None-Match": r.headers["ETag"]}).status_code == 304
    )


def test_thumbnail_stays_behind_session_gate(app: Flask) -> None:
    _push(app)
    url = _url(app)
    app.config["SETTINGS_STORE"].patch_section("auth", {"disabled": False})
    r = app.test_client().get(url, environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
    assert r.status_code in (302, 401, 403)


def test_png_fallback_and_invalid_thumbnail_paths(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import lineup_thumbs

    _push(app)
    monkeypatch.setattr(lineup_thumbs, "_FORMAT", "PNG")
    monkeypatch.setattr(lineup_thumbs, "_EXT", "png")
    url = _url(app)
    assert url.endswith(".png")
    c = app.test_client()
    with c.get(url, buffered=True) as r:
        assert r.status_code == 200 and r.mimetype == "image/png"
        with Image.open(io.BytesIO(r.data)) as image:
            assert max(image.size) == 360
    assert c.get("/compose/thumbs/v1/sent/not-a-digest.png").status_code == 404
    assert c.get("/compose/thumbs/v1/other/abcdef.png").status_code == 404


def test_failed_render_is_deduped_and_backed_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from app.preview_cache import PreviewRenderQueue

    queue = PreviewRenderQueue()
    started, release = threading.Event(), threading.Event()
    calls = []

    def render(*args: Any) -> None:
        calls.append(1)
        started.set()
        assert release.wait(5)
        raise RuntimeError("renderer offline")

    monkeypatch.setattr(queue, "_render", render)
    task: dict[str, Any] = dict(
        base_url="http://127.0.0.1",
        page_id="news",
        width=1440,
        height=2560,
        cache_path=tmp_path / "preview.png",
        pool=None,
    )
    queue.submit(key="lineups", **task)
    assert started.wait(5)
    try:
        for _ in range(20):
            queue.submit(key="hover", **task)
    finally:
        release.set()
    queue.join()
    queue.submit(key="another-poll", **task)
    queue.join()
    assert len(calls) == 1


def test_batched_pushes_keep_latest_success_for_each_target(tmp_path: Path) -> None:
    from app.state.event_log import EventLog

    log = EventLog(tmp_path / "events.db")
    log.record(type="push", source="page", target="news", status="sent", digest="old")
    log.record(type="push", source="page", target="art", status="ok", digest="art")
    log.record(
        type="push",
        source="page",
        target="news",
        status="sent",
        digest="new",
        extra={"composition_digest": "composition"},
    )
    log.record(type="push", source="page", target="news", status="failed", digest="failed")
    log.record(type="device", source="page", target="news", status="sent", digest="device")
    rows = log.latest_pushes(["news", "art", "missing"])
    assert {k: v.digest for k, v in rows.items()} == {"news": "new", "art": "art"}
    assert rows["news"].extra["composition_digest"] == "composition"
    assert log.latest_pushes([]) == {}
