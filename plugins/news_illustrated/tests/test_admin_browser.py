"""The widget's page in a live Chromium: previews drawn by client.js itself,
the lightbox, the image providers (ticks, chips, keyboard order,
Recommended, which keys are set), the summary service, prompt resets, the
caption's look followed live, and a save storing what the controls show.
admin.js does all of this, which no rendered-HTML assertion reaches. Nothing
leaves the machine: the page only reads stories already on disk."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import create_app
from plugins.news_illustrated.tests.fakes import picture

PLUGIN = "news_illustrated"
REPO = Path(__file__).resolve().parents[3]


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _chromium_available(), reason="Playwright Chromium not installed"
)


@pytest.fixture
def live(tmp_path: Path) -> Iterator[tuple[str, Flask]]:
    from werkzeug.serving import make_server

    app = create_app(testing=False, data_root=tmp_path / "data", plugins_dir=REPO / "plugins")
    app.config["TESTING"] = True
    settings = app.config["SETTINGS_STORE"]
    settings.patch_section("app", {"onboarded": True})
    settings.patch_section("auth", {"disabled": True})
    plugin = app.config["PLUGIN_REGISTRY"].get(PLUGIN)
    with app.test_request_context():
        settings.update_for_namespace(
            "plugins",
            PLUGIN,
            {"kie_api_key": "kie-test", "groq_api_key": "gsk-test"},
            plugin.manifest["settings"],
        )
    data_dir = Path(plugin.data_dir)
    stories = []
    for n, label in enumerate(("Tech", "Global")):
        name = f"{label.lower()}-1a2b3c4d-00000000000{n}.jpg"
        (data_dir / "images").mkdir(parents=True, exist_ok=True)
        (data_dir / "images" / name).write_bytes(picture(288, 512))
        stories.append(
            {
                "source_key": f"{label.lower()}-1a2b3c4d",
                "source": label,
                "title": f"{label} headline",
                "summary": f"{label} summary text.",
                "url": "https://example.com/a",
                "date": "2026-10-27",
                "image": name,
                "provider": "kie",
                "size": [288, 512],
            }
        )
    (data_dir / "stories.json").write_text(json.dumps({"stories": stories}), encoding="utf-8")
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


def _saved(app: Flask) -> dict[str, Any]:
    """The page's settings as saved (config.json), and the keys (the store)."""
    plugin = app.config["PLUGIN_REGISTRY"].get(PLUGIN)
    got: dict[str, Any] = json.loads((Path(plugin.data_dir) / "config.json").read_text("utf-8"))
    keys = app.config["SETTINGS_STORE"].get_for_runtime(
        "plugins", PLUGIN, plugin.manifest["settings"]
    )
    return {**got, **keys}


def test_previews_and_the_lightbox_draw_the_frames_render(live: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, _ = live
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/plugins/{PLUGIN}/", wait_until="networkidle")
        shadow_title = (
            "i => document.querySelector(`[data-ain-host='${i}']`)"
            ".shadowRoot.querySelector('.title')?.textContent"
        )
        page.wait_for_function(f"({shadow_title})(0) === 'Tech headline'")
        assert page.evaluate(f"({shadow_title})(1)") == "Global headline"
        # The caption follows the look as it is edited, before any save.
        page.locator('.tile:has([name="show_headline"])').click()
        page.wait_for_function(f"({shadow_title})(0) === undefined")
        page.locator('[name="caption_bg"]').evaluate(
            "el => { el.value = '#aa0000'; el.dispatchEvent(new Event('input', {bubbles: true})); }"
        )
        page.wait_for_function(
            "document.querySelector(\"[data-ain-host='0']\").shadowRoot"
            ".querySelector('.ain').style.getPropertyValue('--ain-bg') === '#aa0000'"
        )
        # The lightbox: large, steps through the stories, closes on Escape
        # and gives the focus back.
        opener = page.locator("[data-ain-open='0']")
        opener.click()
        box = page.locator(".ain-lb .ain-lb-stage")
        assert box.is_visible()
        size = box.bounding_box()
        assert (
            size
            and size["height"] > 600
            and abs(size["width"] / size["height"] - 1440 / 2560) < 0.01
        )
        assert "Tech headline" in page.locator(".ain-lb-text").inner_text()
        page.keyboard.press("ArrowRight")
        assert "Global headline" in page.locator(".ain-lb-text").inner_text()
        page.keyboard.press("Escape")
        assert page.locator(".ain-lb").count() == 0
        assert opener.evaluate("el => el === document.activeElement")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()
    assert errors == []


def test_providers_summaries_and_prompts_save_as_shown(live: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = live
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 420, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/plugins/{PLUGIN}/", wait_until="networkidle")
        form = page.locator("#settings")
        order = form.locator("[data-ain-order-input]")
        chips = form.locator("[data-ain-chips] .ain-chip")

        def chip_names() -> list[str]:
            return [chips.nth(i).get_attribute("data-id") or "" for i in range(chips.count())]

        def status(pid: str) -> str:
            return form.locator(f"[data-ain-prov='{pid}'] [data-ain-status]").inner_text()

        assert chip_names() == ["kie", "gemini", "pollinations", "huggingface"]
        recommended = form.locator("[data-ain-setup='recommended']")
        assert recommended.get_attribute("aria-pressed") == "true"
        assert status("kie") == "In use, tried first"
        assert status("gemini") == "Needs a key"
        # Untick Pollinations: off, out of the chips; Custom now.
        form.locator("[data-ain-prov='pollinations'] .tile").click()
        assert chip_names() == ["kie", "gemini", "huggingface"]
        assert status("pollinations") == "Off"
        assert recommended.get_attribute("aria-pressed") == "false"
        # Keyboard: HuggingFace to the front.
        form.locator("[data-ain-chips] [data-id='huggingface']").focus()
        page.keyboard.press("Home")
        assert chip_names() == ["huggingface", "kie", "gemini"]
        assert order.input_value() == "huggingface, kie, gemini"
        # Keys aren't typed here: each row says whether one is set and
        # links to Settings > Widgets, where they are kept.
        assert form.locator('[name$="_api_key"]').count() == 0
        gemini = form.locator("[data-ain-key='gemini_api_key']")
        assert gemini.locator(".tg").inner_text() == "Not set"
        assert gemini.locator("a").get_attribute("href") == (
            "/settings/plugins#plugin-news_illustrated"
        )
        assert form.locator("[data-ain-key='kie_api_key'] .tg").inner_text() == "Set"
        assert form.locator("[data-save-bar]").is_visible()
        # Recommended puts it all back.
        recommended.click()
        assert chip_names() == ["kie", "gemini", "pollinations", "huggingface"]
        assert recommended.get_attribute("aria-pressed") == "true"
        # Everything off is "none", not the default list.
        for pid in ("kie", "gemini", "pollinations", "huggingface"):
            form.locator(f"[data-ain-prov='{pid}'] .tile").click()
        assert order.input_value() == "none" and chip_names() == []
        form.locator("[data-ain-prov='kie'] .tile").click()
        assert order.input_value() == "kie"
        # Summaries off; Groq's row says so.
        form.locator('.seg-item:has([name="summary_service"][value="off"])').click()
        assert form.locator("[data-ain-groq] [data-ain-status]").inner_text() == "Off"
        # Prompt reset: off while the prompt is the default, back to it on click.
        reset = form.locator("[data-ain-reset$='image_prompt']")
        prompt = form.locator('[name="image_prompt"]')
        assert reset.is_disabled()
        prompt.fill("Draw {title}")
        assert reset.is_enabled()
        reset.click()
        assert prompt.input_value() == prompt.get_attribute("data-default")
        prompt.fill("Draw {title} as {shape}")
        with page.expect_navigation():
            form.locator(".dx-save-bar-save").click()
        page.wait_for_load_state("networkidle")
        browser.close()
    assert errors == []
    saved = _saved(app)
    assert saved["provider_order"] == "kie"
    assert saved["summary_service"] == "off"
    assert saved["image_prompt"] == "Draw {title} as {shape}"
    assert saved["kie_api_key"] == "kie-test" and saved["gemini_api_key"] == ""


def test_chips_drag_and_discard_puts_them_back(live: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, _ = live
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{base}/plugins/{PLUGIN}/", wait_until="networkidle")
        form = page.locator("#settings")
        form.locator("[data-ain-prov='kie'] .tile").click()
        assert form.locator("[data-ain-order-input]").input_value() == (
            "gemini, pollinations, huggingface"
        )
        # Dragging a chip with the pointer moves it.
        chip = form.locator("[data-ain-chips] [data-id='huggingface']").bounding_box()
        first = form.locator("[data-ain-chips] [data-id='gemini']").bounding_box()
        assert chip and first
        page.mouse.move(chip["x"] + 10, chip["y"] + chip["height"] / 2)
        page.mouse.down()
        page.mouse.move(first["x"] + 30, first["y"] + first["height"] / 2, steps=8)
        page.mouse.up()
        assert form.locator("[data-ain-order-input]").input_value() == (
            "huggingface, gemini, pollinations"
        )
        form.locator(".dx-save-bar-discard").click()
        page.wait_for_timeout(50)
        assert form.locator("[data-ain-order-input]").input_value() == (
            "kie, gemini, pollinations, huggingface"
        )
        assert form.locator("[data-ain-chips] .ain-chip").count() == 4
        assert form.locator("[data-ain-enable='kie']").is_checked()
        browser.close()
