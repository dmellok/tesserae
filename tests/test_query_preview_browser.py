"""Exercise the real popup, form parser, responsive grid, and keyboard close."""

import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import sync_playwright

from app.main import REPO_ROOT
from tests.test_query_preview import app as app
from tests.test_query_preview import sign_in


@pytest.fixture
def browser():
    with sync_playwright() as p:
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE", p.chromium.executable_path)
        if not Path(executable).exists():
            pytest.skip("Playwright Chromium not installed")
        b = p.chromium.launch(executable_path=executable)
        yield b
        b.close()


def preview_page(app, browser, width=1200):
    client = app.test_client()
    sign_in(client)
    fragment = client.post(
        "/pages/canvas/source-form",
        json={
            "key": "picture_search",
            "sid": "art",
            "options": {"query": "saved"},
            "cell_w": 1600,
            "cell_h": 900,
        },
    ).get_data(as_text=True)
    css = "\n".join(
        (REPO_ROOT / "static" / "style" / path).read_text()
        for path in ("base.css", "forms.css", "query-preview.css")
    )
    js = (REPO_ROOT / "static" / "query-preview.js").read_text()
    html = f'<!doctype html><html><head><meta name="viewport" content="width=device-width"><style>{css}</style></head><body class="admin"><form id="options">{fragment}</form><script>{js}</script></body></html>'
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.route(
        "http://preview.test/", lambda route: route.fulfill(body=html, content_type="text/html")
    )
    posts = []

    def api(route):
        data = {key: values[-1] for key, values in parse_qs(route.request.post_data).items()}
        posts.append(data)
        response = client.post("/plugins/picture_search/query-preview", data=data)
        route.fulfill(
            body=response.get_data(), status=response.status_code, content_type="application/json"
        )

    page.route("http://preview.test/plugins/picture_search/query-preview", api)
    page.route(
        "https://example.com/**",
        lambda route: route.fulfill(
            content_type="image/svg+xml",
            body='<svg xmlns="http://www.w3.org/2000/svg" width="480" height="320"><rect width="480" height="320" fill="#47595b"/><circle cx="320" cy="95" r="48" fill="#e8b184"/><path d="m0 320 160-230 170 230z" fill="#9eac97"/></svg>',
        ),
    )
    page.goto("http://preview.test/")
    return page, posts


@pytest.mark.parametrize("width,columns", [(1200, 4), (390, 2)])
def test_preview_current_values_twenty_images_and_keyboard_close(
    app, browser, width, columns, tmp_path
):
    page, posts = preview_page(app, browser, width)
    page.fill('[name="opt_query"]', '<img src=x onerror="alert(1)">')
    page.get_by_role("button", name="Preview query").click()
    page.wait_for_function("document.querySelectorAll('.query-preview-item').length === 20")
    assert posts[0]["opt_query"] == '<img src=x onerror="alert(1)">'
    assert page.locator(".query-preview-status").inner_text() == "20 of 25 eligible images"
    assert (
        page.locator(".query-preview-badge").first.inner_text() == '<img src=x onerror="alert(1)">'
    )
    assert page.locator(".query-preview-badge img").count() == 0
    show_tags = page.get_by_role("switch", name="Show tags")
    assert show_tags.is_checked()
    show_tags.press("Space")
    assert not show_tags.is_checked()
    assert page.locator(".query-preview-badges:visible").count() == 0
    assert page.locator(".query-preview-item img:visible").count() == 20
    page.get_by_text("Show tags", exact=True).click()
    assert show_tags.is_checked()
    assert page.locator(".query-preview-badges:visible").count() == 20
    assert len(posts) == 1
    assert (
        page.locator(".query-preview-grid").evaluate(
            "el => getComputedStyle(el).gridTemplateColumns.split(' ').length"
        )
        == columns
    )
    assert page.locator("dialog").evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.locator("body").evaluate("el => el.scrollWidth <= innerWidth")
    page.screenshot(path=str(tmp_path / "query-preview.png"))
    page.keyboard.press("Escape")
    page.locator("dialog").wait_for(state="detached")
    assert page.locator("dialog").count() == 0
    assert page.get_by_role("button", name="Preview query").evaluate(
        "el => el === document.activeElement"
    )
    page.close()


def test_empty_and_failed_previews_can_retry(app, browser, monkeypatch):
    plugin = app.config["PLUGIN_REGISTRY"].get("picture_search")
    monkeypatch.setattr(
        plugin.server_module,
        "query_preview",
        lambda *a, **k: {"images": [], "error": "No matching artwork"},
    )
    page, posts = preview_page(app, browser)
    page.get_by_role("button", name="Preview query").click()
    page.get_by_text("No matching artwork", exact=True).wait_for()
    assert page.locator(".query-preview-item").count() == 0
    monkeypatch.setattr(
        plugin.server_module, "query_preview", lambda *a, **k: {"images": [], "total": 0}
    )
    page.get_by_role("button", name="Try again").click()
    page.get_by_text(
        "Try fewer filters, a different search, or allowing smaller images.", exact=True
    ).wait_for()
    assert len(posts) == 2
    page.get_by_role("button", name="Close query preview").click()
    page.locator("dialog").wait_for(state="detached")
    assert page.locator("dialog").count() == 0
    page.close()


def test_real_canvas_drawer_opens_preview_from_unsaved_options(app, browser, tmp_path):
    client = app.test_client()
    sign_in(client)
    location = client.post("/pages/canvas/new").location
    cid = location.rsplit("/", 1)[-1]
    response = client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "art",
                    "kind": "widget",
                    "widget": "picture_search",
                    "x": 0,
                    "y": 0,
                    "w": 900,
                    "h": 600,
                    "options": {"query": "saved"},
                }
            ]
        },
    )
    assert response.status_code == 200
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.set_default_timeout(5000)
    posts = []

    def serve(route):
        request = route.request
        parsed = urlsplit(request.url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        if parsed.path.endswith("/stream"):
            route.fulfill(body="", content_type="text/event-stream")
            return
        if parsed.path.endswith("/query-preview"):
            posts.append(parse_qs(request.post_data))
        with client.open(
            path,
            method=request.method,
            data=request.post_data_buffer,
            content_type=request.headers.get("content-type"),
        ) as response:
            route.fulfill(
                status=response.status_code,
                body=response.get_data(),
                headers={"Content-Type": response.content_type},
            )

    page.route("http://preview.test/**", serve)
    page.route("https://example.com/**", lambda route: route.fulfill(status=404))
    page.goto(f"http://preview.test{location}")
    page.locator('#panels-artboard [data-id="art"]').click()
    page.get_by_role("button", name="Configure").click()
    page.fill('[name="opt_query"]', "unsaved city")
    page.get_by_role("button", name="Preview query", exact=True).click()
    page.wait_for_function("document.querySelectorAll('.query-preview-item').length === 20")
    assert posts[0]["opt_query"] == ["unsaved city"]
    assert posts[0]["cell_w"] == ["900"]
    assert page.locator("dialog.query-preview").is_visible()
    assert page.locator("dialog.query-preview").evaluate("el => el.scrollWidth <= el.clientWidth")
    page.screenshot(path=str(tmp_path / "canvas-query-preview.png"))
    page.keyboard.press("Escape")
    page.locator("dialog.query-preview").wait_for(state="detached")
    assert page.locator('[name="opt_query"]').input_value() == "unsaved city"
    page.close()
