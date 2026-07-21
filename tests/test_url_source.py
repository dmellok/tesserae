"""Code-element URL data sources + the SSRF guard behind them.

A ``code`` element can carry a source that's a raw JSON API URL (no widget /
service plugin). The server fetches it through :mod:`app.net_guard` and delivers
the parsed body at ``ctx.data[name]``. These tests cover the guard, the
composer resolution, and the editor preview endpoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask

from app import net_guard
from app.main import REPO_ROOT, create_app
from app.state.panel_store import CodeSource, Element


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


# -- net_guard -----------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1", ""],
)
def test_host_is_blocked_refuses_internal(host: str) -> None:
    assert net_guard.host_is_blocked(host) is True


def test_host_is_blocked_allows_public_literal() -> None:
    assert net_guard.host_is_blocked("8.8.8.8") is False


def test_fetch_json_refuses_non_http_scheme() -> None:
    with pytest.raises(net_guard.BlockedURLError):
        net_guard.fetch_json("ftp://example.com/data")


def test_fetch_json_refuses_loopback_host() -> None:
    with pytest.raises(net_guard.BlockedURLError):
        net_guard.fetch_json("http://127.0.0.1:8765/api/v1/device")


def test_guarded_redirect_revalidates_target() -> None:
    """A 302 to a private host is refused at the redirect hop, not just the
    initial URL, so a public URL can't bounce the fetch into the LAN."""
    handler = net_guard._GuardedRedirect()
    with pytest.raises(net_guard.BlockedURLError):
        handler.redirect_request(
            None, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data"
        )


def test_fetch_json_enforces_size_cap() -> None:
    class _Resp:
        headers: dict[str, str] = {}

        def read(self, n: int) -> bytes:
            return b"x" * n  # always returns the full requested amount -> over cap

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *a: object) -> None:
            return None

    class _Opener:
        def open(self, *a: object, **k: object) -> _Resp:
            return _Resp()

    with patch("app.net_guard.urllib.request.build_opener", return_value=_Opener()):
        with pytest.raises(ValueError, match="cap"):
            net_guard.fetch_json("https://example.com/big.json", max_bytes=1024)


# -- composer resolution -------------------------------------------------


def test_code_element_url_source_delivers_parsed_json(app: Flask) -> None:
    from app import composer

    el = Element(
        id="c1",
        kind="code",
        x=0,
        y=0,
        w=200,
        h=100,
        sources=[CodeSource(url="https://api.example.com/weather", name="weather")],
    )
    with app.app_context():
        with patch("app.net_guard.fetch_json", return_value={"temp": 21, "city": "Melbourne"}):
            out = composer._build_canvas_els([el], 400, 300)
    assert out[0]["data_source"] == "live"
    assert out[0]["data"] == {"weather": {"temp": 21, "city": "Melbourne"}}


def test_code_element_url_source_error_is_delivered_not_raised(app: Flask) -> None:
    from app import composer

    el = Element(
        id="c1",
        kind="code",
        x=0,
        y=0,
        w=200,
        h=100,
        sources=[CodeSource(url="http://127.0.0.1/x", name="weather")],
    )
    with app.app_context():
        with patch(
            "app.net_guard.fetch_json",
            side_effect=net_guard.BlockedURLError("host is loopback/private and not allowed"),
        ):
            out = composer._build_canvas_els([el], 400, 300)
    assert out[0]["data_source"] == "error"
    assert "error" in out[0]["data"]["weather"]


def test_code_element_url_source_name_defaults_to_data(app: Flask) -> None:
    from app import composer

    el = Element(
        id="c1",
        kind="code",
        x=0,
        y=0,
        w=200,
        h=100,
        sources=[CodeSource(url="https://api.example.com/x")],
    )
    with app.app_context():
        with patch("app.net_guard.fetch_json", return_value={"ok": True}):
            out = composer._build_canvas_els([el], 400, 300)
    assert out[0]["data"] == {"data": {"ok": True}}


# -- editor preview endpoint ---------------------------------------------


def test_widget_data_endpoint_resolves_url_source(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    with patch("app.net_guard.fetch_json", return_value={"price": 42}):
        resp = client.post("/pages/canvas/data.json", json={"url": "https://api.example.com/q"})
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"price": 42}


def test_widget_data_endpoint_url_error_returns_error_payload(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/pages/canvas/data.json", json={"url": "http://10.0.0.1/x"})
    assert resp.status_code == 200
    assert "error" in resp.get_json()["data"]
