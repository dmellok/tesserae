"""The lineup editor and a display that sleeps: what the server tells the
editor about its wakes (display_caps), which dashboards may pay on each render
(manifest ``render.paid``), a new lineup on a sleeper sending just before each
wake, and the warning's words and numbers (static/lineup_wake.js, run in node
by tests/lineup_wake_check.mjs)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.deck_model import Deck, DeckPage
from app.state.page_store import Cell, Page


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=True,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    store = a.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": True})
    a.config["PAGE_STORE"].save(Page(id="a", name="A"))
    return a


def _register(app: Flask, device_id: str, kind: str = "esp32_client") -> None:
    code = app.config["PAIRING_STORE"].issue(note="t").code
    resp = app.test_client().post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": device_id, "kind": kind}),
    )
    assert resp.status_code == 201


def _sleeper(app: Flask, device_id: str = "door", every_s: int = 3600) -> None:
    _register(app, device_id)
    app.config["SETTINGS_STORE"].patch_section(
        "devices", {device_id: {"sleep_interval_s": every_s, "always_on": False}}
    )


def _tv(app: Flask) -> None:
    _register(app, "tv", "opendisplay_ha")
    app.config["DEVICE_REGISTRY"].get("tv").manifest["buttons"] = False


def _caps(app: Flask, device_id: str) -> dict[str, Any]:
    from app.lineup_editor import display_caps

    with app.test_request_context():
        return display_caps(app.config["DEVICE_REGISTRY"].get(device_id))


# -- the display's wakes -------------------------------------------------------


def test_a_sleeper_says_how_often_it_wakes_and_where_to_change_it(app: Flask) -> None:
    _sleeper(app)
    _tv(app)
    door = _caps(app, "door")
    assert door["sleeps"] and door["wake_minutes"] == 60
    assert door["wake_url"] == "/settings/devices/door#timing"
    # No check-in seen yet: no next wake, and smart sync can't aim at one.
    assert door["next_wake"] == "" and door["wake_learned"] is False
    tv = _caps(app, "tv")
    assert not tv["sleeps"] and tv["wake_minutes"] is None and tv["wake_url"] == ""


def test_wake_times_count_as_learned_only_when_smart_sync_trusts_them(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Test Display's telemetry said "firmware" but had no on-time wakes:
    the editor said "Wake times learned" while smart sync still sent on the
    timer. Learned means what the scheduler's gate means: trusted."""
    _sleeper(app)
    entry = SimpleNamespace(
        always_on=False,
        predicted_next_wake_at=time.time() + 25 * 60,
        prediction_source="firmware",
        last_sleep_interval_s=3593,
        wake_lead_ewma_s=None,
        is_trusted=False,
    )
    monkeypatch.setitem(app.config, "DEVICE_TELEMETRY", SimpleNamespace(get=lambda _id: entry))
    caps = _caps(app, "door")
    assert caps["wake_learned"] is False
    assert caps["next_wake"].startswith("~") and caps["wake_minutes"] == 60
    entry.is_trusted = True
    assert _caps(app, "door")["wake_learned"] is True


# -- widgets that pay per render -----------------------------------------------


def _install(app: Flask, monkeypatch: pytest.MonkeyPatch, **manifests: dict[str, Any]) -> None:
    reg = app.config["PLUGIN_REGISTRY"]
    real = reg.get

    def get(plugin_id: str) -> Any:
        if plugin_id in manifests:
            return SimpleNamespace(manifest=manifests[plugin_id], server_module=None)
        return real(plugin_id)

    monkeypatch.setattr(reg, "get", get)


METHOD = {
    "name": "method",
    "type": "select",
    "default": "toolpath",
    "choices": [{"value": "toolpath", "label": "Toolpath"}, {"value": "ai", "label": "AI"}],
}


def _page(*cells: tuple[str, dict[str, Any]]) -> Page:
    return Page(
        id="p",
        name="P",
        cells=[
            Cell(id=f"c{i}", plugin=plugin, x=0, y=0, w=10, h=10, options=options)
            for i, (plugin, options) in enumerate(cells)
        ],
    )


def test_render_paid_names_the_widgets_that_may_pay(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.lineup_editor import paid_widgets

    _install(
        app,
        monkeypatch,
        painter={"name": "Painter", "render": {"paid": True}},
        printer={"name": "Printer", "render": {"paid": {"method": "ai"}}, "cell_options": [METHOD]},
        clock={"name": "Clock", "render": {"needs_network": False}},
    )
    with app.app_context():
        assert paid_widgets(_page(("painter", {}), ("clock", {}))) == ["Painter"]
        # Like show_if: only the options that make it pay.
        assert paid_widgets(_page(("printer", {"method": "ai"}))) == ["Printer"]
        assert paid_widgets(_page(("printer", {"method": "toolpath"}))) == []
        assert paid_widgets(_page(("printer", {}))) == []  # the default draws a toolpath
        assert paid_widgets(_page(("clock", {}))) == []


def test_the_editor_state_carries_each_dashboards_paid_widgets(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(app, monkeypatch, painter={"name": "Painter", "render": {"paid": True}})
    app.config["PAGE_STORE"].save(
        Page(id="art", name="Art", cells=[Cell(id="c", plugin="painter", x=0, y=0, w=10, h=10)])
    )
    app.config["DECK_STORE"].upsert(
        Deck(id="lu", name="Lu", pages=[DeckPage(page_id="art"), DeckPage(page_id="a")])
    )
    html = app.test_client().get("/decks/lu/edit").get_data(as_text=True)
    state = json.loads(html.split('id="dxe-data">', 1)[1].split("</script>", 1)[0])
    assert state["pages"]["art"]["paid"] == ["Painter"]
    assert state["pages"]["a"]["paid"] == []


def test_the_manifest_schema_takes_render_paid() -> None:
    import jsonschema

    schema = json.loads((REPO_ROOT / "schema" / "plugin.schema.json").read_text())
    paid = schema["properties"]["render"]["properties"]["paid"]
    jsonschema.validate(True, paid)
    jsonschema.validate({"method": "ai"}, paid)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate("yes", paid)


# -- a new lineup's default ----------------------------------------------------


def _create(app: Flask, device: str, page: str = "a") -> Deck:
    resp = app.test_client().post(
        "/decks/new/lineup",
        data={
            "name": "New",
            "when": "between",
            "start": "07:00",
            "end": "09:00",
            "page_ids": [page],
            "device_ids": [device],
        },
    )
    data = resp.get_json()
    assert data and data["ok"], data
    deck = app.config["DECK_STORE"].get(data["id"])
    assert deck is not None
    return deck


def test_a_new_lineup_on_a_sleeper_sends_just_before_each_wake(app: Flask) -> None:
    _sleeper(app)
    _tv(app)
    app.config["PAGE_STORE"].save(Page(id="b", name="B"))
    assert _create(app, "door").advance_smart_sync is True
    assert _create(app, "tv", "b").advance_smart_sync is False


def _create_saying(app: Flask, page: str, start: str, said: list[str]) -> Deck:
    resp = app.test_client().post(
        "/decks/new/lineup",
        data={
            "name": "New",
            "when": "between",
            "start": start,
            "end": f"{int(start[:2]) + 2:02d}:00",
            "page_ids": [page],
            "device_ids": ["door"],
            "advance_smart_sync": said,
        },
    )
    data = resp.get_json()
    assert data and data["ok"], data
    deck = app.config["DECK_STORE"].get(data["id"])
    assert deck is not None
    return deck


def test_a_new_lineup_saves_the_choice_it_posts(app: Flask) -> None:
    """The default is only for a create that doesn't say: one that posts
    the choice ("on" after a "0", as an edit does) has it saved."""
    _sleeper(app)
    app.config["PAGE_STORE"].save(Page(id="b", name="B"))
    assert _create_saying(app, "a", "07:00", ["0"]).advance_smart_sync is False
    assert _create_saying(app, "b", "12:00", ["0", "on"]).advance_smart_sync is True


def test_an_existing_lineup_keeps_its_saved_choice(app: Flask) -> None:
    """Only new lineups default to it: one saved on the timer stays there
    (the editor offers the switch in its warning)."""
    _sleeper(app)
    app.config["DECK_STORE"].upsert(
        Deck(id="lu", name="Lu", device_ids=["door"], pages=[DeckPage(page_id="a")])
    )
    html = app.test_client().get("/decks/lu/edit").get_data(as_text=True)
    state = json.loads(html.split('id="dxe-data">', 1)[1].split("</script>", 1)[0])
    assert state["smartSync"] is False


# -- the words (static/lineup_wake.js) -------------------------------------------


def test_the_warning_words_and_numbers() -> None:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node is not on PATH in CI, so lineup_wake_check.mjs did not run")
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, str(REPO_ROOT / "tests" / "lineup_wake_check.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr or result.stdout
