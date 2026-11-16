"""Meural account block in a real browser: layout, states and lock-in.

Netgear and Meural are never contacted: urllib is blocked, playback
restoration is replaced, and failures are stubbed at the page boundary.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app import device_service
from app.main import create_app

_EXE = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(_EXE or p.chromium.executable_path).exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _chromium_available(), reason="Playwright Chromium not installed"
)

FRAMES = [
    {"id": "101", "name": "Lounge Art", "host": ""},
    {"id": "102", "name": "Hallway Canvas", "host": ""},
]
OVERFLOW_JS = """(sel) => {
  const root = document.querySelector(sel);
  const box = root.getBoundingClientRect();
  const bad = [];
  for (const el of root.querySelectorAll('*')) {
    if (el.closest('[hidden]') || el.classList.contains('sr-only')) continue;
    const r = el.getBoundingClientRect();
    if (!r.width && !r.height) continue;
    if (r.left < box.left - 0.5 || r.right > box.right + 0.5)
      bad.push((el.className || el.tagName) + ' ' + Math.round(r.right - box.right));
    if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow === 'visible'
        && !['SELECT', 'OPTION'].includes(el.tagName))
      bad.push('scroll ' + (el.className || el.tagName));
  }
  if (document.documentElement.scrollWidth > innerWidth) bad.push('page scrolls sideways');
  return bad;
}"""


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Any]]:
    from werkzeug.serving import make_server

    app = create_app(testing=True, data_root=tmp_path_factory.mktemp("meural-ui"))
    assert device_service.create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DATA_ROOT"],
        instance_id="test_frame",
        kind_id="meural",
        name="Example frame",
    ).ok
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Meural UI tests must not reach the network")

    monkeypatch.setattr("urllib.request.urlopen", blocked)
    monkeypatch.setattr("app.settings.meural_routes._release_native", lambda device: None)


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()
        yield browser
        browser.close()


def account(app: Any, **values: Any) -> None:
    base = {
        "meural_refresh_token_secret": "example-refresh",
        "meural_email": "owner@example.com",
        "meural_devices": FRAMES,
        "cloud_device_id": "",
        "meural_management_paused": False,
    }
    app.config["SETTINGS_STORE"].update_item("devices", "test_frame", {**base, **values})


def open_block(browser: Any, url: str, width: int) -> Any:
    page = browser.new_context(viewport={"width": width, "height": 1000}).new_page()
    page.goto(f"{url}/settings/devices/test_frame#connection", wait_until="load")
    block = page.locator("#meural-account")
    block.scroll_into_view_if_needed()
    assert block.is_visible()
    return page


def words(locator: Any) -> str:
    return " ".join(locator.text_content().split())


def centre(locator: Any) -> float:
    box = locator.bounding_box()
    return box["y"] + box["height"] / 2


def visible_actions(page: Any) -> list[dict[str, Any]]:
    return page.evaluate(
        """() => [...document.querySelectorAll('#meural-account .mx-actions :is(a, button)')]
          .filter((el) => el.offsetParent !== null)
          .map((el) => ({text: el.textContent.trim(), button: el.classList.contains('mx-btn'),
                         primary: el.classList.contains('is-primary'),
                         x: el.getBoundingClientRect().left, y: el.getBoundingClientRect().top}))"""
    )


@pytest.mark.parametrize("width", [1440, 390])
def test_use_this_frame_locks_in_and_survives_reload(live: Any, browser: Any, width: int) -> None:
    url, app = live
    account(app)
    page = open_block(browser, url, width)
    try:
        block = page.locator("#meural-account")
        select = block.get_by_label("Meural frame")
        use = block.get_by_role("button", name="Use this frame")
        assert abs(centre(select) - centre(use)) <= 2 or width < 641
        if width < 641:
            # Phone: each control fills its own row with a 44px target.
            assert select.bounding_box()["height"] >= 44 and use.bounding_box()["height"] >= 44
        assert page.evaluate(OVERFLOW_JS, "#meural-account") == []
        select.select_option(label="Hallway Canvas")
        use.click()
        locked = block.locator("[data-mx-locked-line]")
        locked.wait_for(state="visible")
        assert "Showing on Hallway Canvas" in words(locked)
        assert block.get_by_text("Saved", exact=True).is_visible()
        assert not select.is_visible()
        page.wait_for_function(
            "document.querySelector('#meural-account [data-mx-live]').textContent"
            ".includes('Frame saved')"
        )
        live_region = block.locator("[data-mx-live]")
        assert live_region.get_attribute("aria-live") == "polite"
        assert (
            app.config["SETTINGS_STORE"].get_section("devices")["test_frame"]["cloud_device_id"]
            == "102"
        )
        page.reload(wait_until="load")
        block.scroll_into_view_if_needed()
        assert "Showing on Hallway Canvas" in words(block.locator("[data-mx-locked-line]"))
        assert not block.get_by_label("Meural frame").is_visible()
        # Change re-opens the picker on the current frame.
        block.get_by_role("button", name="Change frame").click()
        assert block.get_by_label("Meural frame").input_value() == "102"
        block.get_by_role("button", name="Cancel").click()
        assert block.locator("[data-mx-locked-line]").is_visible()
    finally:
        page.context.close()


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize(
    "state,primary",
    [
        ({"cloud_device_id": "101"}, None),
        ({"cloud_device_id": "101", "meural_management_paused": True}, "Let Tesserae manage"),
        ({"meural_refresh_token_secret": "", "meural_devices": []}, "Sign in to Netgear"),
    ],
    ids=["locked", "released", "signed-out"],
)
def test_actions_put_links_left_and_the_primary_last(
    live: Any, browser: Any, width: int, state: dict[str, Any], primary: str | None
) -> None:
    url, app = live
    account(app, **state)
    page = open_block(browser, url, width)
    try:
        actions = visible_actions(page)
        assert actions, "every state offers an action"
        buttons = [a for a in actions if a["button"]]
        assert all(a["text"] != "Sign out" for a in buttons)
        signout = [a for a in actions if a["text"] == "Sign out"]
        if signout:
            assert all(signout[0]["x"] <= b["x"] or signout[0]["y"] < b["y"] for b in buttons)
        primaries = [a for a in buttons if a["primary"]]
        assert len(primaries) <= 1
        if primary:
            assert primaries and primaries[0]["text"] == primary
            assert buttons[-1]["text"] == primary
        else:
            assert not primaries
        assert page.evaluate(OVERFLOW_JS, "#meural-account") == []
    finally:
        page.context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_failed_save_shows_the_reason_and_keeps_the_picker(
    live: Any, browser: Any, width: int
) -> None:
    url, app = live
    account(app)
    page = open_block(browser, url, width)
    try:
        page.route(
            "**/meural/frame",
            lambda route: route.fulfill(
                status=400, json={"error": "Meural didn't answer. Try again in a minute."}
            ),
        )
        block = page.locator("#meural-account")
        block.get_by_label("Meural frame").select_option(label="Lounge Art")
        block.get_by_role("button", name="Use this frame").click()
        error = block.locator("[data-mx-pick-error]")
        error.wait_for(state="visible")
        assert error.text_content() == "Meural didn't answer. Try again in a minute."
        page.wait_for_function(
            "document.querySelector('#meural-account [data-mx-live]').textContent"
            ".startsWith('Frame not saved')"
        )
        assert block.get_by_role("button", name="Use this frame").is_enabled()
        assert block.get_by_label("Meural frame").get_attribute("aria-invalid") == "true"
        assert not block.locator("[data-mx-locked-line]").is_visible()
    finally:
        page.context.close()


@pytest.mark.parametrize("width", [1440, 320])
def test_long_frame_names_stay_inside_the_card(live: Any, browser: Any, width: int) -> None:
    url, app = live
    long_name = "Living room Meural Canvas II by the north window " * 2
    account(
        app,
        cloud_device_id="101",
        meural_devices=[{"id": "101", "name": long_name.strip(), "host": ""}],
    )
    page = open_block(browser, url, width)
    try:
        assert page.evaluate(OVERFLOW_JS, "#meural-account") == []
        name = page.locator("#meural-account [data-mx-locked-line] [data-mx-frame-name]")
        assert name.get_attribute("title") == long_name.strip()
    finally:
        page.context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_add_device_tab_shows_the_account_block_before_add(
    live: Any, browser: Any, width: int
) -> None:
    url, _ = live
    page = browser.new_context(viewport={"width": width, "height": 1000}).new_page()
    try:
        page.goto(f"{url}/settings/devices", wait_until="load")
        page.locator("[data-add-toggle][aria-expanded]").click()
        page.get_by_role("tab", name="Netgear Meural", exact=True).click()
        branch = page.locator('[data-transport-branch="meural"]')
        block = branch.locator("[data-meural-account]")
        assert block.is_visible()
        name_box = branch.get_by_label("Display name", exact=True).bounding_box()
        assert block.bounding_box()["y"] > name_box["y"]
        buttons = branch.locator(".mx-btn:visible, .dx-btn-primary:visible")
        texts = [t.strip() for t in buttons.all_inner_texts()]
        assert texts[-1] == "Add Netgear Meural"
        assert block.locator(".mx-btn.is-primary:visible").count() == 0
        assert page.evaluate(OVERFLOW_JS, '[data-transport-branch="meural"]') == []
    finally:
        page.context.close()
