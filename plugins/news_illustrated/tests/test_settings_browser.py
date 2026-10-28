"""The AI Illustrated News settings on the widget's page in a live Chromium:
they save and come back, fit a phone, flag an invalid field and save
without JavaScript. The API keys stay under Settings > Widgets. The page's
own controls are in test_admin_browser.py."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]


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

    app = create_app(testing=False, data_root=tmp_path / "data", plugins_dir=REPO_ROOT / "plugins")
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": True})
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


def _saved(app: Flask) -> dict[str, Any]:
    plugin = app.config["PLUGIN_REGISTRY"].get("news_illustrated")
    got: dict[str, Any] = json.loads((Path(plugin.data_dir) / "config.json").read_text("utf-8"))
    return got


def test_ai_news_settings_save_and_come_back(live):
    """AI news: the settings sit under Last run, open; an edit shows the save
    bar, and a save comes back to the settings. (The page's own controls are
    tested in plugins/news_illustrated/tests/test_admin_browser.py.)"""
    from playwright.sync_api import sync_playwright

    base, app = live
    plugin = app.config["PLUGIN_REGISTRY"].get("news_illustrated")
    (Path(plugin.data_dir) / "state.json").write_text(
        json.dumps({"last_run": {"reason": "manual", "started": 1, "finished": 2, "log": ["a"]}}),
        encoding="utf-8",
    )
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(f"{base}/plugins/news_illustrated/", wait_until="networkidle")
        ids = page.evaluate(
            "[...document.querySelector('.settings-section-list').children].map((el) => el.id)"
        )
        assert ids == ["stories", "last-run", "settings"]
        form = page.locator("#settings")
        sources = form.locator('[name="sources"]')
        assert sources.is_visible()
        bar = form.locator("[data-save-bar]")
        assert bar.is_hidden()
        sources.fill("Local | https://example.test/rss")
        assert bar.is_visible()
        with page.expect_navigation():
            bar.locator(".dx-save-bar-save").click()
        page.wait_for_load_state("networkidle")
        assert page.url.endswith("/plugins/news_illustrated/#settings")
        assert "Settings saved." in page.locator("[data-flash]").inner_text()
        assert page.locator('#settings [name="sources"]').input_value() == (
            "Local | https://example.test/rss"
        )
        # The log opens on its newest lines.
        page.locator("#last-run .dx-disclosure summary").click()
        assert page.locator("#last-run pre[data-ain-log]").is_visible()
        browser.close()
    assert errors == []
    assert _saved(app)["sources"] == "Local | https://example.test/rss"


def test_ai_news_admin_fits_desktop_and_mobile(live):
    import os

    from playwright.sync_api import sync_playwright

    base, _ = live
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for width, theme in ((1280, "light"), (390, "light"), (390, "dark")):
            page.emulate_media(color_scheme=theme)
            page.set_viewport_size({"width": width, "height": 950})
            for pid in ("news_illustrated",):
                page.goto(f"{base}/plugins/{pid}/", wait_until="networkidle")
                assert page.locator("#settings").is_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                artifacts = os.environ.get("TESSERAE_UI_ARTIFACT_DIR")
                if artifacts:
                    Path(artifacts).mkdir(parents=True, exist_ok=True)
                    page.screenshot(
                        path=str(Path(artifacts) / f"{pid}-{width}-{theme}.png"), full_page=True
                    )
        browser.close()


def test_ai_news_invalid_field_is_shown_before_saving(live):
    """A field that fails validation stops the save and gets the focus; once
    fixed, the save goes."""
    from playwright.sync_api import sync_playwright

    base, app = live
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{base}/plugins/news_illustrated/", wait_until="networkidle")
        form = page.locator("#settings")
        words = form.locator('[name="summary_words"]')
        words.fill("5")  # under the field's minimum
        form.locator(".dx-save-bar-save").click()
        page.wait_for_timeout(200)
        assert words.evaluate("el => el === document.activeElement && !el.validity.valid")
        words.fill("60")
        with page.expect_navigation():
            form.locator(".dx-save-bar-save").click()
        browser.close()
    assert _saved(app)["summary_words"] == 60


def test_ai_news_settings_save_without_javascript(live):
    """No scripts: the settings save through the plain Save button, and the
    provider order keeps its saved value."""
    from playwright.sync_api import sync_playwright

    base, app = live
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(java_script_enabled=False)
        page = context.new_page()
        page.goto(f"{base}/plugins/news_illustrated/", wait_until="networkidle")
        ai = page.locator("#settings")
        sources = ai.locator('[name="sources"]')
        sources.fill("Local | https://example.test/rss")
        ai.locator('[name="summary_words"]').fill("45")
        ai.locator('.tile:has([name="show_date"])').click()
        with page.expect_navigation():
            ai.locator("noscript button[type=submit]").click()
        page.wait_for_load_state("networkidle")
        assert page.url.endswith("/plugins/news_illustrated/#settings")
        assert "Settings saved." in page.locator("[data-flash]").inner_text()
        context.close()
        browser.close()
    saved = _saved(app)
    assert saved["sources"] == "Local | https://example.test/rss"
    assert (saved["summary_words"], saved["show_date"]) == (45, False)
    assert saved["provider_order"] == "kie, gemini, pollinations, huggingface"


def test_the_keys_stay_under_settings_widgets(live):
    """The keys card under Settings > Widgets saves them encrypted; the
    widget's page then says they're set, without showing them."""
    from playwright.sync_api import sync_playwright

    base, app = live
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{base}/plugins/news_illustrated/", wait_until="networkidle")
        with page.expect_navigation():
            page.locator("[data-ain-key='groq_api_key'] a").click()
        page.wait_for_load_state("networkidle")
        assert page.url.endswith("/settings/plugins#plugin-news_illustrated")
        card = page.locator("#plugin-news_illustrated")
        assert card.locator('[name="sources"]').count() == 0
        card.locator('[name="groq_api_key"]').fill("gsk-browser-test")
        with page.expect_navigation():
            card.locator(".dx-save-bar-save").click()
        page.goto(f"{base}/plugins/news_illustrated/", wait_until="networkidle")
        assert page.locator("[data-ain-key='groq_api_key'] .tg").inner_text() == "Set"
        assert page.locator("[data-ain-groq] [data-ain-status]").inner_text() == "In use"
        assert "gsk-browser-test" not in page.content()
        browser.close()
    plugin = app.config["PLUGIN_REGISTRY"].get("news_illustrated")
    keys = app.config["SETTINGS_STORE"].get_for_runtime(
        "plugins", plugin.id, plugin.manifest["settings"]
    )
    assert keys["groq_api_key"] == "gsk-browser-test"
