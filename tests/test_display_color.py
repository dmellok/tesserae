"""Display-accurate previews follow the effective loaded panel, including renderer pins."""

from dataclasses import replace
from pathlib import Path

import pytest
from flask import Flask, render_template

from app.main import REPO_ROOT, create_app


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
    a.config["SETTINGS_STORE"].patch_section("app", {"onboarded": True})
    a.config["SETTINGS_STORE"].patch_section("auth", {"disabled": True})
    return a


@pytest.mark.parametrize(
    "kind,mode,levels",
    [
        ("xteink_x3", "mono-dither", 2),
        ("seeed_reterminal_e1001_gray_legacy", "grey4", 4),
        ("visionect_place_and_play_32", "grey16", 16),
        ("esp32_client", "color", 0),
    ],
)
def test_catalog_color(app: Flask, kind: str, mode: str, levels: int) -> None:
    from app.display_color import display_color_mode

    device = app.config["DEVICE_REGISTRY"].get(kind)
    assert device is not None
    color = display_color_mode(device)
    assert (color.gamut, color.levels) == (mode, levels)


def test_renderer_override_and_grey_levels(app: Flask) -> None:
    from app.display_color import display_color_mode

    device = app.config["DEVICE_REGISTRY"].get("visionect_place_and_play_32")
    for gamut, mode, levels in [
        ("gray_4", "grey4", 4),
        ("gray_8", "grey8", 8),
        ("mono", "mono-dither", 2),
    ]:
        pinned = replace(device, manifest={**device.manifest, "_renderer_gamut": gamut})
        color = display_color_mode(pinned)
        assert (color.gamut, color.levels) == (mode, levels)


def test_lineups_modes_are_additive_and_per_display(app: Flask) -> None:
    from app.lineups_view import sample_view_model

    vm = sample_view_model().to_dict()
    assert vm["schema_version"] == 1
    assert [(d["gamut"], d["grey_levels"]) for d in vm["displays"]] == [
        ("grey16", 16),
        ("color", 0),
    ]
    with app.test_request_context("/decks"):
        body = render_template("decks.html", vm=vm, automation_url="#")
    assert 'data-gamut="grey16" data-grey-levels="16"' in body
    assert 'data-gamut="color" data-grey-levels="0"' in body


def test_devices_dashboards_send_and_editor_color(app: Flask) -> None:
    from app.state.page_store import Page

    client = app.test_client()
    for did, kind in [("grey", "visionect_place_and_play_32"), ("color", "esp32_client")]:
        response = client.post("/settings/devices/add", data={"id": did, "kind": kind, "name": did})
        assert response.status_code == 302
    store = app.config["PAGE_STORE"]
    for pid, ids in [
        ("grey-page", ["grey"]),
        ("color-page", ["color"]),
        ("mixed", ["grey", "color"]),
        ("unbound", []),
    ]:
        store.save(Page(id=pid, name=pid, device_ids=ids))
    for route in [
        "/settings/devices",
        "/settings/devices/grey",
        "/pages",
        "/send",
        "/pages/grey-page",
    ]:
        response = client.get(route)
        assert response.status_code == 200, route
        body = response.get_data(as_text=True)
        assert 'data-gamut="grey16" data-grey-levels="16"' in body, route
    with app.test_request_context("/"):

        def color(ids: list[str]) -> str:
            return app.jinja_env.from_string("{{ preview_color(ids).gamut }}").render(ids=ids)

        assert color(["grey"]) == "grey16"
        assert color(["grey", "color"]) == "color"
        assert color([]) == "color"
        assert color(["removed", "grey"]) == "color"


def test_dither_none_keeps_sixteen_greys_and_renderer_pin_wins(app: Flask) -> None:
    from app.display_color import display_color_mode

    client = app.test_client()
    client.post(
        "/settings/devices/add", data={"id": "frame", "kind": "visionect_place_and_play_32"}
    )
    device = app.config["DEVICE_REGISTRY"].get("frame")
    settings = app.config["SETTINGS_STORE"]
    settings.patch_section("renderers", {device.renderer_ids[0]: {"dither": "none"}})
    mode = display_color_mode(device, app.config["RENDERER_REGISTRY"], settings)
    assert (mode.gamut, mode.levels, mode.dither) == ("grey16", 16, "none")
    # Device.panel has already resolved a selected renderer over a stale panel block.
    mono = replace(device, manifest={**device.manifest, "_renderer_gamut": "mono"})
    assert display_color_mode(mono).gamut == "mono-dither"


def test_live_lineups_and_history_use_display_modes(app: Flask) -> None:
    import re

    client = app.test_client()
    for did, kind in [("grey", "visionect_place_and_play_32"), ("color", "esp32_client")]:
        client.post("/settings/devices/add", data={"id": did, "kind": kind})
    from app.state.deck_model import Deck, DeckPage
    from app.state.page_store import Page

    app.config["PAGE_STORE"].save(Page(id="demo", name="Demo", device_ids=["grey", "color"]))
    app.config["DECK_STORE"].upsert(
        Deck(
            id="shared",
            name="Shared",
            device_ids=["grey", "color"],
            pages=[DeckPage(page_id="demo")],
        )
    )
    response = client.get("/decks/view.json")
    assert response.status_code == 200
    displays = {d["id"]: d for d in response.get_json()["displays"]}
    assert (displays["grey"]["gamut"], displays["grey"]["grey_levels"]) == ("grey16", 16)
    assert displays["color"]["gamut"] == "color"
    for ids in [["grey"], ["grey", "removed"], ["grey", "color"]]:
        app.config["EVENT_LOG"].record(
            type="push",
            source="page",
            target="demo",
            status="sent",
            digest="0123456789abcdef",
            extra={"device_ids": ids},
        )
    body = client.get("/history").get_data(as_text=True)
    modes = re.findall(r'<img[^>]*data-gamut="([^"]+)"', body)
    assert sorted(modes) == ["color", "color", "grey16"]


def test_unknown_and_unbound_keep_color() -> None:
    from app.display_color import DisplayColor, display_color_mode

    assert display_color_mode(None) == DisplayColor()


def test_latest_composition_is_filtered_but_legacy_artifact_is_preserved(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    import re

    client = app.test_client()
    client.post("/settings/devices/add", data={"id": "grey", "kind": "visionect_place_and_play_32"})
    latest = {"digest": "0123456789abcdef", "ext": "png"}
    monkeypatch.setattr(app.config["PUSH_MANAGER"], "latest_render_for", lambda did: latest)
    for composition in [False, True]:
        if composition:
            latest["composition_digest"] = "fedcba9876543210"
        body = client.get("/settings/devices/grey").get_data(as_text=True)
        thumb = re.search(r'<img src="/preview/grey.png(?:\?v=[^"]*)?"[^>]*>', body)
        assert thumb is not None
        assert ("data-device-quantised" in thumb.group()) is not composition


def test_mono_threshold_only_for_dither_none(app: Flask) -> None:
    from app.display_color import display_color_mode

    client = app.test_client()
    client.post("/settings/devices/add", data={"id": "frame", "kind": "xteink_x3"})
    device = app.config["DEVICE_REGISTRY"].get("frame")
    settings = app.config["SETTINGS_STORE"]
    renderers = app.config["RENDERER_REGISTRY"]
    rid = device.renderer_ids[0]
    for dither, gamut in [
        ("jarvis", "mono-dither"),
        ("floyd-steinberg", "mono-dither"),
        ("none", "mono"),
    ]:
        settings.patch_section("renderers", {rid: {"dither": dither}})
        mode = display_color_mode(device, renderers, settings)
        assert (mode.gamut, mode.levels, mode.dither) == (gamut, 2, dither)
