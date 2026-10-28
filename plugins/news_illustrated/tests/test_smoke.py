"""news_illustrated through the host: the cell renders at every size from the
stories on disk, renders only read (a settled one may start the day's run in
the background, a preview never does), rotation, the picture route and the
widget's page. The run itself is replaced by a recorder: nothing here
touches the network."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from plugins.news_illustrated.tests.fakes import picture

PLUGIN = "news_illustrated"


@pytest.fixture
def mod(app: Flask) -> ModuleType:
    """The server module the host loaded (its helpers are the host's copies)."""
    plugin = app.config["PLUGIN_REGISTRY"].get(PLUGIN)
    assert plugin is not None and plugin.server_module is not None
    return plugin.server_module


@pytest.fixture
def data_dir(app: Flask) -> Path:
    path: Path = app.config["PLUGIN_REGISTRY"].get(PLUGIN).data_dir
    return path


@pytest.fixture
def starts(monkeypatch: pytest.MonkeyPatch, mod: ModuleType) -> list[dict[str, Any]]:
    """Record run starts instead of running anything."""
    calls: list[dict[str, Any]] = []

    def start(cfg: Any, data_dir: Path, **kw: Any) -> bool:
        calls.append({"cfg": cfg, "data_dir": data_dir, **kw})
        return True

    monkeypatch.setattr(mod.pipeline.RUNNER, "start", start)
    return calls


def _seed(mod: ModuleType, data_dir: Path, *, done: bool = True) -> list[dict[str, Any]]:
    store = mod.store
    name = store.save_image(data_dir, "tech-1a2b3c4d", picture())
    stories = [
        {
            "source_key": "tech-1a2b3c4d",
            "source": "Tech",
            "title": "Meta ignores court order",
            "summary": "Groups sent new evidence to the court.",
            "url": "https://example.com/meta",
            "date": "2026-10-25",
            "image": name,
            "provider": "kie",
        },
        {
            "source_key": "global-5e6f7a8b",
            "source": "Global",
            "title": "Starlink connection now stable",
            "summary": "The defence minister said the link is stable again.",
            "url": "https://example.com/starlink",
            "date": "2026-10-24",
            "image": "",
            "provider": "",
        },
    ]
    store.save_stories(data_dir, stories)
    if done:
        store.save_state(data_dir, {"edition": mod.pipeline.edition(datetime.now(), "06:05")})
    return stories


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_renders_a_story_at_every_size(
    client: FlaskClient, mod: ModuleType, data_dir: Path, starts: list[Any], size: str
) -> None:
    stories = _seed(mod, data_dir)
    resp = client.get(f"/_test/render?plugin={PLUGIN}&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f'data-plugin="{PLUGIN}"' in body
    assert "Meta ignores court order" in body
    assert f"/plugins/{PLUGIN}/image/{stories[0]['image']}" in body
    assert starts == [], "today's stories are made, nothing to start"


def test_a_settled_render_with_nothing_made_starts_the_run(
    client: FlaskClient, starts: list[dict[str, Any]]
) -> None:
    resp = client.get(f"/_test/render?plugin={PLUGIN}&size=lg")
    assert resp.status_code == 200
    assert len(starts) == 1
    assert starts[0]["reason"] == "schedule" and not starts[0].get("force")
    assert [s.label for s in starts[0]["cfg"].sources] == ["Tech", "Business", "Global"]


def _ctx(data_dir: Path, *, preview: bool, settled: bool) -> dict[str, Any]:
    return {"data_dir": str(data_dir), "preview": preview, "settled": settled, "cell_id": "c1"}


def test_a_preview_never_starts_a_run(
    app: Flask, mod: ModuleType, data_dir: Path, starts: list[Any]
) -> None:
    with app.app_context():
        out = mod.fetch({}, {}, ctx=_ctx(data_dir, preview=True, settled=False))
    assert starts == []
    assert out == {
        "run_at": "06:05",
        "running": False,
        "look": mod.LOOK_DEFAULTS | {p: True for p in mod.LOOK_PARTS},
        "notice": "No image provider has a key yet, so stories are text only.",
    }
    assert not (data_dir / "frames.json").exists(), "a preview is not a frame"


def test_pushes_rotate_through_the_stories(
    app: Flask, mod: ModuleType, data_dir: Path, starts: list[Any]
) -> None:
    _seed(mod, data_dir)
    settings = {"kie_api_key": "k"}
    with app.app_context():
        first = mod.fetch({}, settings, ctx=_ctx(data_dir, preview=False, settled=True))
        again = mod.fetch({}, settings, ctx=_ctx(data_dir, preview=False, settled=True))
        cursor = data_dir / "cursor.json"
        cursor.write_text(json.dumps({"c1": {"i": 0, "at": 0}}), encoding="utf-8")
        later = mod.fetch({}, settings, ctx=_ctx(data_dir, preview=False, settled=True))
        preview = mod.fetch({}, settings, ctx=_ctx(data_dir, preview=True, settled=False))
    assert (first["index"], first["count"], first["title"]) == (1, 2, "Meta ignores court order")
    assert first["image"].startswith(f"/plugins/{PLUGIN}/image/tech-1a2b3c4d-")
    assert "notice" not in first
    assert again["index"] == 1, "a follow-up of the same send shows the same story"
    assert (later["index"], later["title"], later["image"]) == (
        2,
        "Starlink connection now stable",
        "",
    )
    assert preview["index"] == 2
    assert starts == []


def test_a_run_in_progress_asks_for_follow_ups(
    app: Flask,
    mod: ModuleType,
    data_dir: Path,
    starts: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(mod, data_dir)
    monkeypatch.setattr(mod.pipeline.RUNNER, "running", lambda d: True)
    monkeypatch.setattr(mod.pipeline.RUNNER, "pending", lambda d: 2)
    with app.app_context():
        out = mod.fetch({}, {}, ctx=_ctx(data_dir, preview=True, settled=False))
    assert out["running"] and out["live_refresh_s"] == mod.LIVE_REFRESH_S
    assert out["live_pending"] == 2
    assert out["title"]


def test_no_sources_is_an_error(app: Flask, mod: ModuleType, data_dir: Path) -> None:
    mod.config.save(data_dir, {"sources": "  "})
    with app.app_context():
        out = mod.fetch({}, {}, ctx=_ctx(data_dir, preview=True, settled=False))
    assert out == {"error": "Add a feed under Sources in this widget's settings."}


def test_picture_route(client: FlaskClient, mod: ModuleType, data_dir: Path) -> None:
    stories = _seed(mod, data_dir)
    resp = client.get(f"/plugins/{PLUGIN}/image/{stories[0]['image']}")
    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    assert resp.data[:3] == b"\xff\xd8\xff"
    assert "immutable" in resp.headers["Cache-Control"]
    resp.close()
    assert client.get(f"/plugins/{PLUGIN}/image/missing-1.jpg").status_code == 404
    assert client.get(f"/plugins/{PLUGIN}/image/state.json").status_code == 404


def test_the_picture_route_is_render_safe(app: Flask) -> None:
    assert "news_illustrated_admin.image" in app.config["RENDER_SAFE_ENDPOINTS"]


def test_widget_page_and_generate_now(
    client: FlaskClient, mod: ModuleType, data_dir: Path, starts: list[dict[str, Any]]
) -> None:
    _seed(mod, data_dir)
    page = client.get(f"/plugins/{PLUGIN}/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Meta ignores court order" in html and "Generate now" in html
    assert "no provider is on with a key" in html
    resp = client.post(f"/plugins/{PLUGIN}/run")
    assert resp.status_code == 302
    assert len(starts) == 1 and starts[0]["reason"] == "manual" and starts[0]["force"] is True


def test_a_frame_render_notes_its_size_and_gets_the_look(
    app: Flask, mod: ModuleType, data_dir: Path, starts: list[Any]
) -> None:
    stories = _seed(mod, data_dir)
    stories[0]["size"] = [768, 1376]
    mod.store.save_stories(data_dir, stories)
    # The look is the page's settings (config.json); a value that doesn't
    # fit is the default. The keys come in ``settings``, from the store.
    (data_dir / "config.json").write_text(
        json.dumps(
            {
                "caption_bg": "#1A2B3C",
                "caption_fg": "nonsense",
                "show_date": False,
                "show_headline": False,
            }
        ),
        encoding="utf-8",
    )
    settings = {"kie_api_key": "k", "caption_bg": "#ff0000", "show_tag": False}
    ctx = {**_ctx(data_dir, preview=False, settled=True), "cell_w": 1440, "cell_h": 2560}
    with app.app_context():
        out = mod.fetch({}, settings, ctx={**ctx, "placement_id": "page1:cell2"})
    assert mod.store.frame_sizes(data_dir, time.time()) == [(1440, 2560)]
    assert json.loads((data_dir / "frames.json").read_text())["page1:cell2"]["w"] == 1440
    assert out["look"] == {
        "bg": "#1a2b3c",
        "fg": "#ffffff",
        "tag": True,
        "date": False,
        "count": True,
        "title": False,
        "summary": True,
    }
    assert (out["image_w"], out["image_h"]) == (768, 1376)


def test_admin_page_draws_the_redesigned_settings(
    app: Flask, client: FlaskClient, mod: ModuleType, data_dir: Path
) -> None:
    stories = _seed(mod, data_dir)
    stories[0]["usage"] = {"summary": {"provider": "groq", "tokens": 1312}}
    mod.store.save_stories(data_dir, stories)
    mod.store.save_state(
        data_dir,
        {
            "last_run": {
                "reason": "schedule",
                "started": 1,
                "finished": 2,
                "outcome": "2 new",
                "usage": {"tokens": {"groq": 2624}, "pictures": {"kie": 1}},
                "log": ["Run started"],
            }
        },
    )
    plugin = app.config["PLUGIN_REGISTRY"].get(PLUGIN)
    app.config["SETTINGS_STORE"].update_for_namespace(
        "plugins", PLUGIN, {"kie_api_key": "k"}, plugin.manifest["settings"]
    )
    mod.config.save(
        data_dir, {"sources": "Tech | https://a.example/rss\nBroken | feed.example/rss"}
    )
    html = client.get(f"/plugins/{PLUGIN}/").get_data(as_text=True)
    assert "<h1>" in html and "AI Illustrated News</h1>" in html
    assert "Time to make today&#39;s stories" in html
    for needle in (
        'name="summary_service" value="groq"',
        'name="provider_order"',
        'data-ain-prov="kie"',
        "data-ain-chips",
        'data-ain-reset="ain-image_prompt"',
        'data-ain-reset="ain-summary_prompt"',
        'data-ain-key="kie_api_key" data-state="set"',
        'name="image_shape"',
        'name="caption_bg"',
        'name="show_headline"',
        "https://console.groq.com/docs/models",
        "https://kie.ai/market",
        'id="ain-data"',
        "Groq 1,312 tokens",
        "Used: Groq 2,624 tokens; 1 picture from kie.ai.",
        "Sources line 2 is skipped",
    ):
        assert needle in html, needle
    assert "secret" not in html.split('id="ain-data"')[1].split("</script>")[0]
    js = client.get(f"/plugins/{PLUGIN}/admin.js")
    assert js.status_code == 200 and "openLightbox" in js.get_data(as_text=True)
    js.close()
