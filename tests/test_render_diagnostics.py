"""Acceptance tests for the render_report ``?debug=1`` diagnostics (live Chromium).

The point of the diagnostics block is that silent failures stop needing pixel
diffs: a throwing element script, a 404'ing font, and a browser-dropped CSS
declaration must all be named explicitly by ONE ``render_report?debug=1`` call.
These tests run the real pipeline (Flask served over loopback, headless
Chromium navigating back into it) and are skipped when Playwright's Chromium
isn't installed.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app


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
def live_server(tmp_path: Path) -> Iterator[str]:
    """The app served on a real loopback port, so the headless renderer can
    navigate back into ``/compose/<id>`` (the test client's fake ``localhost``
    host_url would send Chromium to port 80). Threaded: the render_report
    request thread blocks on Chromium, which then requests /compose."""
    from werkzeug.serving import make_server

    app: Flask = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    app.config["TESTING"] = True
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)


def _api(base: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    req = urllib.request.Request(
        f"{base}/api/mcp{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


# One fixture page carrying all three silent failure modes:
#   * "boom": a code element whose script throws AND whose CSS has a
#     declaration Chromium's parser silently drops.
#   * "ghost": an html element whose @font-face points at a URL that 404s
#     (the srcdoc iframe inherits the parent base URL, so the fetch hits the
#     server and fails for real).
_FIXTURE_ELS: list[dict[str, Any]] = [
    {
        "id": "boom",
        "kind": "code",
        "x": 0,
        "y": 0,
        "w": 300,
        "h": 200,
        "html": "<div id='out'>x</div>",
        "css": ".card { color: notarealcolor; padding: 4px; }",
        "js": "throw new Error('boom-goes-render');",
    },
    {
        "id": "ghost",
        "kind": "html",
        "x": 300,
        "y": 0,
        "w": 300,
        "h": 200,
        "html": "<div>father figure</div>",
        "css": (
            "@font-face{font-family:'GhostFace';"
            "src:url('/static/ghost-missing.woff2') format('woff2');}"
            "body{font-family:'GhostFace',sans-serif;}"
        ),
    },
    {"id": "t1", "kind": "text", "text": "hello", "x": 0, "y": 220, "w": 200, "h": 60},
]


def _make_fixture_page(base: str) -> str:
    page = _api(base, "POST", "/pages", {"name": "Diag fixture", "w": 640, "h": 400})
    pid = str(page["id"])
    saved = _api(base, "PUT", f"/pages/{pid}/canvas", {"w": 640, "h": 400, "els": _FIXTURE_ELS})
    assert saved.get("elements") == len(_FIXTURE_ELS)
    return pid


def _debug_report(base: str, pid: str) -> dict[str, Any]:
    report = _api(base, "GET", f"/pages/{pid}/render_report?debug=1")
    assert "diagnostics" in report, f"no diagnostics in {sorted(report)}"
    return report


def test_debug_report_names_all_three_failures(live_server: str) -> None:
    """The acceptance bar: throwing script, 404 font, and dropped CSS rule are
    all named by one render_report call; no diagnosis requires reading the PNG."""
    pid = _make_fixture_page(live_server)
    diag = _debug_report(live_server, pid)["diagnostics"]

    # 1. The element script's throw is surfaced, tagged with the element id.
    console = diag.get("console") or []
    assert any(
        "[code-el boom]" in c.get("text", "") and "boom-goes-render" in c.get("text", "")
        for c in console
    ), f"throwing script not surfaced: {console}"

    # 2. The failing font names its URL and what happened. Depending on the
    #    element's origin the same 404 surfaces as an HTTP status (parent
    #    page) or a failed font request (sandboxed iframe, where CORS masks
    #    the status) — either way the URL and resource type are explicit.
    network = diag.get("network") or []
    assert any(
        "ghost-missing.woff2" in n.get("url", "")
        and (n.get("status") == 404 or n.get("error"))
        and n.get("resource_type") == "font"
        for n in network
    ), f"failing font not surfaced: {network}"

    # 3. The silently-dropped CSS declaration names the element, selector,
    #    and declaration.
    css = diag.get("css") or []
    dropped = [c for c in css if c.get("el") == "boom"]
    assert any(
        c.get("declaration", "").startswith("color: notarealcolor")
        and ".card" in c.get("selector", "")
        for c in dropped
    ), f"dropped CSS declaration not surfaced: {css}"

    # The capture condition is visible, not inferable: the compose signal
    # fired and each settle phase reports its elapsed ms.
    settle = diag.get("settle") or {}
    assert settle.get("compose_signal") == "fired"
    for phase in ("goto_ms", "compose_ms", "images_ms", "fonts_ms"):
        assert isinstance(settle.get(phase), int)

    # Font faces report a per-face status (the parent page's fonts settled
    # by capture time — nothing may be left pending).
    fonts = diag.get("fonts") or []
    assert isinstance(fonts, list)
    assert not [f for f in fonts if f.get("status") == "pending-at-capture"]


def test_debug_report_is_deterministic_across_runs(live_server: str) -> None:
    """Same page content → same font/asset outcome, render after render. Guards
    the class of race where a font sometimes missed the screenshot."""
    pid = _make_fixture_page(live_server)
    first = _debug_report(live_server, pid)["diagnostics"]
    second = _debug_report(live_server, pid)["diagnostics"]
    assert first.get("fonts") == second.get("fonts")
    assert first.get("css") == second.get("css")
    assert first.get("libraries", {}).get("elements") == second.get("libraries", {}).get("elements")
    assert (first.get("settle") or {}).get("compose_signal") == (second.get("settle") or {}).get(
        "compose_signal"
    )
