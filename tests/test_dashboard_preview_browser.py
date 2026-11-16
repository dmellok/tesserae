"""The editor refreshes captures without saving, and previews output at native size."""

import base64
import io
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright

from app.state.page_store import Cell, Page, Panel


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("bound", [False, True])
def test_refresh_preserves_draft_and_native_preview_uses_selected_dither(
    app, monkeypatch, width, tmp_path, bound
):
    from app.device_service import create_instance

    # Unsaved drafts, "Refresh preview" and the preview palette/dither pickers are
    # classic-editor features: the new editor autosaves every change and dropped
    # the pickers, so this runs against the classic one.
    app.config["SETTINGS_STORE"].patch_section("ui", {"dashboard_editor": "classic"})
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    buf = io.BytesIO()
    Image.new("RGB", (800, 480), "white").save(buf, "PNG")
    image = buf.getvalue()
    captures = []

    def fetch(options, settings, *, ctx):
        if ctx.get("fresh"):
            captures.append(dict(options))
        return {
            "frame": "data:image/png;base64," + base64.b64encode(image).decode(),
            "rendered_at": 1700000000 + len(captures),
            "w": 800,
            "h": 480,
        }

    monkeypatch.setattr(
        app.config["PLUGIN_REGISTRY"].get("ha_dashboard").server_module, "fetch", fetch
    )
    if bound:
        create_instance(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=app.config["DATA_ROOT"] / "devices",
            instance_id="mini",
            kind_id="seeed_reterminal_e1001_gray",
            name="Mini Frame",
        )
    app.config["PAGE_STORE"].save(
        Page(
            id="capture",
            name="Capture",
            panel=Panel(w=800, h=480),
            device_ids=["mini"] if bound else [],
            cells=[Cell(id="dash", plugin="ha_dashboard", x=0, y=0, w=800, h=480)],
        )
    )
    panel_urls = []

    with sync_playwright() as pw:
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE", pw.chromium.executable_path)
        if not Path(executable).exists():
            pytest.skip("Playwright Chromium not installed")
        browser = pw.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": width, "height": 1000})

        def route_request(route):
            req = route.request
            url = urlsplit(req.url)
            if url.path.endswith("/panel.png"):
                panel_urls.append(req.url)
                return route.fulfill(body=image, content_type="image/png")
            response = client.open(
                url.path + ("?" + url.query if url.query else ""),
                method=req.method,
                data=req.post_data_buffer,
                headers={
                    key: value
                    for key, value in req.headers.items()
                    if key in ("content-type", "x-requested-with")
                },
            )
            route.fulfill(
                status=response.status_code,
                body=response.get_data(),
                headers=dict(response.headers),
            )
            response.close()

        page.route("http://editor.test/**", route_request)
        page.goto("http://editor.test/pages/capture")
        refresh = page.get_by_role("button", name="Refresh preview", exact=True)
        refresh.wait_for(timeout=3000)
        footer = page.locator(".preview-footer").bounding_box()
        actions = page.locator(".preview-actions").bounding_box()
        dimensions = page.locator(".preview-dims").bounding_box()
        assert round(actions["x"] + actions["width"]) == round(footer["x"] + footer["width"])
        assert round(dimensions["x"]) == round(footer["x"])
        frame = page.locator(".preview-frame").bounding_box()
        assert actions["y"] >= frame["y"] + frame["height"]
        for selector in (".preview-card", "[data-preview-refresh]"):
            bounds = page.locator(selector).bounding_box()
            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
        refresh.click()
        page.wait_for_function("!document.querySelector('[data-preview-refresh]').disabled")
        assert page.locator("[data-save-all]").is_disabled()
        page.locator('[name="opt_path"]').fill("/unsaved/0")
        refresh.click()
        page.wait_for_function("!document.querySelector('[data-preview-refresh]').disabled")
        assert captures and captures[-1]["path"] == "/unsaved/0"
        assert app.config["PAGE_STORE"].get("capture").cells[0].options == {}
        assert page.locator("[data-capture-status]").inner_text().startswith("Last capture:")
        page.get_by_role("button", name="Panel view", exact=True).click()
        if bound:
            assert page.locator("[data-preview-dither]").count() == 0
            assert page.locator(".preview-card").inner_text().find("inherits Mini Frame") >= 0
            page.wait_for_timeout(150)
            assert parse_qs(urlsplit(panel_urls[-1]).query)["device"] == ["mini"]
        else:
            page.locator(".preview-options summary").click()
            page.get_by_label("Preview palette").select_option("gray_4")
            page.get_by_label("Preview dithering").select_option("none")
            page.wait_for_timeout(150)
            assert parse_qs(urlsplit(panel_urls[-1]).query)["dither"] == ["none"]
            assert parse_qs(urlsplit(panel_urls[-1]).query)["gamut"] == ["gray_4"]
        page.get_by_role("button", name="View at 100%", exact=True).click()
        image_el = page.locator("[data-native-preview] img")
        assert image_el.evaluate("e => e.getBoundingClientRect().width") == 800
        assert page.locator("[data-native-preview]").evaluate("e => e.open")
        page.keyboard.press("Escape")
        assert not page.locator("[data-native-preview]").evaluate("e => e.open")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate("scrollTo(0, 0)")
        page.screenshot(path=str(tmp_path / f"editor-{width}.png"))
        browser.close()
