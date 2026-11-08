"""A dashboard shared by displays of different shapes shows each display its
own picture in Lineups: that display's ratio and the picture last pushed to it,
not the largest panel's shape and a picture sent to another display."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest
from flask import Flask
from PIL import Image

from app.main import REPO_ROOT, create_app
from app.state.deck_model import Deck, DeckPage
from app.state.page_store import Page


def _register(app: Flask, client: Any, device_id: str, w: int, h: int) -> None:
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": device_id, "kind": "esp32_client", "panel_w": w, "panel_h": h}
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    a.config["SETTINGS_STORE"].patch_section("app", {"locale": "en-GB"})
    return a


@pytest.fixture
def client(app: Flask) -> Any:
    c = app.test_client()
    c.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    # The landscape one is the larger panel, so it sets the dashboard's own shape.
    _register(app, c, "wide", 1600, 900)
    _register(app, c, "tall", 480, 800)
    _register(app, c, "twin", 800, 450)
    app.config["PAGE_STORE"].save(
        Page(id="photos", name="Photos", device_ids=["wide", "tall", "twin"])
    )
    app.config["PAGE_STORE"].save(Page(id="clock", name="Clock", device_ids=["tall"]))
    app.config["DECK_STORE"].upsert(
        Deck(
            id="all",
            name="All",
            device_ids=["wide", "tall", "twin"],
            pages=[DeckPage(page_id="photos")],
            advance="timer",
            advance_interval_minutes=30,
            advance_anchor="06:00",
            advance_end_at="09:00",
            advance_days_of_week=[0, 1, 2, 3, 4, 5, 6],
        )
    )
    return c


def _steps(client: Any) -> dict[str, dict[str, Any]]:
    """The "photos" step on each display, from one view."""
    view = client.get("/decks/view.json").get_json()
    return {
        d["id"]: next(lu for lu in d["lineups"] if lu["id"] == "all")["steps"][0]
        for d in view["displays"]
    }


def _step(client: Any, display: str) -> dict[str, Any]:
    return _steps(client)[display]


def _keep_previews(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record the preview renders asked for, without running a browser."""
    from app import preview_cache

    asked: list[dict[str, Any]] = []
    monkeypatch.setattr(preview_cache, "submit", lambda **kw: asked.append(kw))
    return asked


def _composition(app: Flask, w: int, h: int, *devices: str) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "navy").save(buf, format="PNG")
    digest = hashlib.sha256(buf.getvalue()).hexdigest()[:16]
    (app.config["RENDERS_DIR"] / f"{digest}.png").write_bytes(buf.getvalue())
    app.config["EVENT_LOG"].record(
        type="push",
        source="page",
        target="photos",
        status="sent",
        digest="a" * 16,
        extra={"composition_digest": digest, "device_ids": list(devices)},
    )
    return digest


def test_each_display_gets_its_own_shape(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _keep_previews(monkeypatch)
    steps = _steps(client)
    assert [(steps[d]["aspect"], steps[d]["orientation"]) for d in ("wide", "tall", "twin")] == [
        ("1600/900", "l"),
        ("480/800", "p"),
        ("800/450", "l"),
    ]
    # Each was drawn at its own panel size for that display (a render, not a
    # crop of the largest panel's picture).
    drawn = {
        parse_qs(kw["query"].lstrip("&"))["device_id"][0]: (kw["width"], kw["height"])
        for kw in asked
        if kw["page_id"] == "photos" and kw.get("query")
    }
    assert drawn == {"wide": (1600, 900), "tall": (480, 800), "twin": (800, 450)}


def test_displays_of_one_ratio_share_a_picture(
    client: Any, app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _keep_previews(monkeypatch)
    app.config["PAGE_STORE"].save(Page(id="pair", name="Pair", device_ids=["wide", "twin"]))
    app.config["DECK_STORE"].upsert(
        Deck(
            id="pair",
            name="Pair",
            device_ids=["wide", "twin"],
            pages=[DeckPage(page_id="pair")],
            advance="timer",
            advance_interval_minutes=30,
            advance_anchor="06:00",
            advance_end_at="09:00",
            advance_days_of_week=[0, 1, 2, 3, 4, 5, 6],
        )
    )
    client.get("/decks/view.json")
    assert [kw for kw in asked if kw["page_id"] == "pair"]
    assert not [kw for kw in asked if kw["page_id"] == "pair" and kw.get("query")]


def test_a_cached_draft_is_that_displays_thumbnail(
    client: Any, app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _keep_previews(monkeypatch)
    _step(client, "tall")
    draft = next(kw for kw in asked if (kw["width"], kw["height"]) == (480, 800))
    Path(draft["cache_path"]).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (480, 800), "teal").save(draft["cache_path"])
    step = _step(client, "tall")
    assert "/compose/thumbs/v1/draft/" in step["thumb_url"]
    assert step["composition_digest"] is None
    with client.get(step["thumb_url"]) as r:
        assert r.status_code == 200
        with Image.open(io.BytesIO(r.data)) as thumb:
            assert thumb.size[0] / thumb.size[1] == pytest.approx(480 / 800, abs=0.02)


def test_a_picture_sent_to_another_display_is_not_shown_here(
    client: Any, app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _keep_previews(monkeypatch)
    sent_tall = _composition(app, 480, 800, "tall")
    tall, wide = _step(client, "tall"), _step(client, "wide")
    assert tall["composition_digest"] == sent_tall and sent_tall in tall["thumb_url"]
    # The landscape display shows no portrait picture in its landscape card.
    assert sent_tall not in wide["thumb_url"]
    assert wide["composition_digest"] != sent_tall
    assert wide["aspect"] == "1600/900"


def test_the_latest_push_for_each_display(
    client: Any, app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _keep_previews(monkeypatch)
    older = _composition(app, 480, 800, "tall")
    newer = _composition(app, 1600, 900, "wide", "twin")
    assert _step(client, "tall")["composition_digest"] == older
    assert _step(client, "wide")["composition_digest"] == newer


def test_latest_pushes_matches_the_whole_device_id(app: Flask) -> None:
    log = app.config["EVENT_LOG"]
    for who in ("reterminal_a", "reterminalXa", "reterminal_ab"):
        log.record(
            type="push",
            source="page",
            target=f"for-{who}",
            status="sent",
            digest="b" * 16,
            extra={"device_ids": [who]},
        )
    targets = ["for-reterminal_a", "for-reterminalXa", "for-reterminal_ab"]
    assert set(log.latest_pushes(targets, "reterminal_a")) == {"for-reterminal_a"}
    assert set(log.latest_pushes(targets)) == set(targets)
