"""ha_dashboard: an existing Home Assistant dashboard captured as a frame.

The plugin module is loaded standalone (the test_ha_cache pattern) with a
bare Flask app carrying a stub ha_core in the plugin registry. The browser
is replaced by a fake ``_capture`` that returns a synthetic PNG whose top
``header_px`` rows are red, so the crop is checked by pixel rather than by
trusting the arithmetic.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask
from PIL import Image

from app.main import REPO_ROOT
from app.renderer import RenderRequest

BASE = "http://ha.local:8123"
TOKEN = "llat-secret-token"


def _load_plugin(name: str) -> Any:
    path = REPO_ROOT / "plugins" / name / "server.py"
    spec = importlib.util.spec_from_file_location(f"_{name}_under_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _png(w: int, h: int, header: int, colour: tuple[int, int, int] = (0, 128, 255)) -> bytes:
    im = Image.new("RGB", (w, h), colour)
    if header:
        im.paste((255, 0, 0), (0, 0, w, header))
    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


class _FakeBrowser:
    """Stands in for ``_capture``: records requests, answers with a PNG sized
    to the request's viewport, and lets a test choose where the page lands."""

    def __init__(self) -> None:
        self.requests: list[RenderRequest] = []
        self.href = BASE + "/lovelace/0"
        self.delay_s = 0.0
        self.fail: Exception | None = None
        self.lock = threading.Lock()

    def __call__(self, request: RenderRequest, script: str) -> tuple[bytes, Any]:
        with self.lock:
            self.requests.append(request)
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.fail is not None:
            raise self.fail
        header = request.viewport_h - 480 if request.viewport_h > 480 else 0
        return _png(request.viewport_w, request.viewport_h, header), self.href


@pytest.fixture
def widget(monkeypatch: pytest.MonkeyPatch) -> Any:
    mod = _load_plugin("ha_dashboard")
    mod._reset()
    monkeypatch.setattr(mod, "FIRST_WAIT_S", 3.0)
    monkeypatch.setattr(mod, "_timezone_id", lambda: "Europe/London")
    return mod


@pytest.fixture
def browser(widget: Any, monkeypatch: pytest.MonkeyPatch) -> _FakeBrowser:
    fake = _FakeBrowser()
    monkeypatch.setattr(widget, "_capture", fake)
    return fake


def _core(configured: bool = True, verify_tls: bool = True) -> Any:
    return SimpleNamespace(
        is_configured=lambda: configured,
        base_url=lambda: BASE,
        token=lambda: TOKEN,
        _verify_tls=lambda: verify_tls,
    )


@pytest.fixture
def app() -> Flask:
    app = Flask(__name__)
    app.config["PLUGIN_REGISTRY"] = {"ha_core": SimpleNamespace(server_module=_core())}
    app.config["SETTINGS_STORE"] = None
    return app


def _ctx(**overrides: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {"cell_w": 800, "cell_h": 480, "panel_w": 800, "panel_h": 480}
    ctx.update(overrides)
    return ctx


def _decode(data: dict[str, Any]) -> Image.Image:
    assert data["frame"].startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(data["frame"].split(",", 1)[1])))


# -- option shaping ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", "/lovelace/0"),
        ("lovelace/0", "/lovelace/0"),
        ("/dashboard-kitchen/overview", "/dashboard-kitchen/overview"),
        ("  lovelace/1  ", "/lovelace/1"),
        ("http://ha.local:8123/dashboard-x/view?foo=1", "/dashboard-x/view?foo=1"),
        ("https://other.host/lovelace/2", "/lovelace/2"),
    ],
)
def test_path_normalisation(widget: Any, raw: str, expected: str) -> None:
    assert widget.normalise_path(raw) == expected


def test_dashboard_url_appends_kiosk_flag(widget: Any) -> None:
    p = widget.Params("/lovelace/0", 800, 480, 1.0, False, True, 2.0)
    assert widget.dashboard_url(BASE, p) == BASE + "/lovelace/0?kiosk"
    q = widget.Params("/lovelace/0?x=1", 800, 480, 1.0, False, True, 2.0)
    assert widget.dashboard_url(BASE, q) == BASE + "/lovelace/0?x=1&kiosk"
    plain = widget.Params("/lovelace/0", 800, 480, 1.0, False, False, 2.0)
    assert widget.dashboard_url(BASE, plain) == BASE + "/lovelace/0"


def test_init_script_seeds_the_frontend_session(widget: Any) -> None:
    p = widget.Params("/lovelace/0", 800, 480, 1.5, True, True, 2.0)
    script = widget.init_script(BASE, TOKEN, p)
    assert TOKEN in script
    assert '"hassUrl\\": \\"http://ha.local:8123\\"' in script or "http://ha.local:8123" in script
    assert "always_hidden" in script
    assert '{\\"dark\\": true}' in script or '"dark": true' in script.replace("\\", "")
    assert "document.body.style.zoom = String(zoom)" in script
    assert "const zoom = 1.5;" in script


# -- the render -------------------------------------------------------------


def test_first_fetch_renders_and_crops_the_header(
    widget: Any, browser: _FakeBrowser, app: Flask
) -> None:
    with app.app_context():
        data = widget.fetch({"path": "lovelace/0", "zoom": "1"}, {}, ctx=_ctx())
    assert "error" not in data
    assert data["stale"] is False
    assert data["path"] == "/lovelace/0"
    im = _decode(data)
    assert im.size == (800, 480)
    assert im.convert("RGB").getpixel((0, 0)) == (0, 128, 255)  # header row gone
    assert im.convert("RGB").getpixel((799, 479)) == (0, 128, 255)

    (request,) = browser.requests
    assert request.url == BASE + "/lovelace/0?kiosk"
    assert request.viewport_w == 800
    assert request.viewport_h == 480 + 56
    assert request.is_composer is False
    assert request.max_attempts == 1
    assert request.ready_js == widget.READY_JS
    assert request.settle_ms == 2000
    assert request.timezone_id == "Europe/London"
    assert request.ignore_https_errors is False
    assert request.init_script is not None and TOKEN in request.init_script
    # The token never rides on the data the cell receives.
    assert TOKEN not in json.dumps(data)


def test_zoom_scales_the_header_crop(widget: Any, browser: _FakeBrowser, app: Flask) -> None:
    with app.app_context():
        data = widget.fetch({"zoom": "1.5"}, {}, ctx=_ctx())
    (request,) = browser.requests
    assert request.viewport_h == 480 + 84
    assert _decode(data).size == (800, 480)


def test_self_signed_tls_follows_ha_core(widget: Any, browser: _FakeBrowser) -> None:
    app = Flask(__name__)
    app.config["PLUGIN_REGISTRY"] = {
        "ha_core": SimpleNamespace(server_module=_core(verify_tls=False))
    }
    app.config["SETTINGS_STORE"] = None
    with app.app_context():
        widget.fetch({}, {}, ctx=_ctx())
    assert browser.requests[0].ignore_https_errors is True


def test_login_redirect_is_reported_not_captured(
    widget: Any, browser: _FakeBrowser, app: Flask
) -> None:
    browser.href = BASE + "/auth/authorize?response_type=code"
    with app.app_context():
        data = widget.fetch({}, {}, ctx=_ctx())
    assert "frame" not in data
    assert "login page" in data["error"]
    assert "Home Assistant Core" in data["error"]


def test_cached_frame_is_reused_within_refresh_interval(
    widget: Any, browser: _FakeBrowser, app: Flask
) -> None:
    with app.app_context():
        first = widget.fetch({"refresh_seconds": 300}, {}, ctx=_ctx())
        second = widget.fetch({"refresh_seconds": 300}, {}, ctx=_ctx())
    assert len(browser.requests) == 1
    assert second["frame"] == first["frame"]
    assert second["stale"] is False


def test_stale_frame_goes_out_while_the_next_render_runs(
    widget: Any, browser: _FakeBrowser, app: Flask
) -> None:
    with app.app_context():
        first = widget.fetch({"refresh_seconds": 30}, {}, ctx=_ctx())
    # Age the frame past the interval, then make the next render slow.
    params = next(iter(widget._frames))
    old = widget._frames[params]
    widget._frames[params] = widget.Frame(
        png=old.png, rendered_at=old.rendered_at - 60, w=old.w, h=old.h
    )
    browser.delay_s = 0.5
    with app.app_context():
        t0 = time.monotonic()
        second = widget.fetch({"refresh_seconds": 30}, {}, ctx=_ctx())
        waited = time.monotonic() - t0
    assert second["stale"] is True
    assert second["frame"] == first["frame"]
    assert waited < 0.4  # did not block on the in-flight render
    # Let the background render land, then the fresh frame is served.
    job = widget._jobs.get(params)
    if job is not None:
        job.done.wait(3.0)
    with app.app_context():
        third = widget.fetch({"refresh_seconds": 30}, {}, ctx=_ctx())
    assert third["stale"] is False
    assert len(browser.requests) == 2


def test_fresh_flag_forces_a_new_render(widget: Any, browser: _FakeBrowser, app: Flask) -> None:
    with app.app_context():
        widget.fetch({}, {}, ctx=_ctx())
        widget.fetch({}, {}, ctx=_ctx(fresh=True))
    # The forced render is queued in the background; wait for it.
    for job in list(widget._jobs.values()):
        job.done.wait(3.0)
    assert len(browser.requests) == 2


def test_slow_first_render_returns_pending_then_the_frame(
    widget: Any, browser: _FakeBrowser, app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(widget, "FIRST_WAIT_S", 0.05)
    browser.delay_s = 0.4
    before = time.time()
    with app.app_context():
        data = widget.fetch({}, {}, ctx=_ctx())
    # The placeholder declares when to look again, so the device path
    # re-renders the page once the frame lands rather than keeping the
    # placeholder until the next Send.
    retry_at = data.pop("next_change_at")
    assert data == {"pending": True, "path": "/lovelace/0"}
    assert before + widget.PENDING_RETRY_S <= retry_at <= time.time() + widget.PENDING_RETRY_S
    params = next(iter(widget._jobs))
    widget._jobs[params].done.wait(3.0)
    with app.app_context():
        later = widget.fetch({}, {}, ctx=_ctx())
    assert "frame" in later
    assert len(browser.requests) == 1


def test_render_failure_is_throttled(widget: Any, browser: _FakeBrowser, app: Flask) -> None:
    browser.fail = RuntimeError("connection refused")
    with app.app_context():
        first = widget.fetch({}, {}, ctx=_ctx())
        second = widget.fetch({}, {}, ctx=_ctx())
    assert "connection refused" in first["error"]
    assert second == first
    assert len(browser.requests) == 1  # no relaunch inside ERROR_RETRY_S


def test_render_failure_keeps_the_last_good_frame(
    widget: Any, browser: _FakeBrowser, app: Flask
) -> None:
    with app.app_context():
        good = widget.fetch({"refresh_seconds": 30}, {}, ctx=_ctx())
    params = next(iter(widget._frames))
    old = widget._frames[params]
    widget._frames[params] = widget.Frame(
        png=old.png, rendered_at=old.rendered_at - 60, w=old.w, h=old.h
    )
    browser.fail = RuntimeError("HA down")
    with app.app_context():
        data = widget.fetch({"refresh_seconds": 30}, {}, ctx=_ctx())
    assert data["frame"] == good["frame"]
    for job in list(widget._jobs.values()):
        job.done.wait(3.0)
    with app.app_context():
        after = widget.fetch({"refresh_seconds": 30}, {}, ctx=_ctx())
    assert after["frame"] == good["frame"]  # error recorded, frame kept


def test_concurrent_fetches_share_one_render(
    widget: Any, browser: _FakeBrowser, app: Flask
) -> None:
    browser.delay_s = 0.3
    results: list[dict[str, Any]] = []

    def go() -> None:
        with app.app_context():
            results.append(widget.fetch({}, {}, ctx=_ctx()))

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5.0)
    assert len(browser.requests) == 1
    assert all("frame" in r for r in results)


def test_unconfigured_core_is_a_clear_error(widget: Any, browser: _FakeBrowser) -> None:
    app = Flask(__name__)
    app.config["PLUGIN_REGISTRY"] = {
        "ha_core": SimpleNamespace(server_module=_core(configured=False))
    }
    with app.app_context():
        data = widget.fetch({}, {}, ctx=_ctx())
    assert "Home Assistant Core" in data["error"]
    assert browser.requests == []


def test_missing_core_plugin_is_a_clear_error(widget: Any) -> None:
    app = Flask(__name__)
    app.config["PLUGIN_REGISTRY"] = {}
    with app.app_context():
        data = widget.fetch({}, {}, ctx=_ctx())
    assert "Home Assistant Core" in data["error"]


def test_cell_size_falls_back_to_panel_then_default(widget: Any) -> None:
    assert widget._params({}, {"cell_w": 0, "cell_h": 0, "panel_w": 640, "panel_h": 400}).w == 640
    p = widget._params({}, {})
    assert (p.w, p.h) == (800, 480)
