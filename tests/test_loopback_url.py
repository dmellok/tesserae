"""Loopback URL rewriting for the in-process renderer (#129).

``to_loopback_url`` must fetch ``/compose`` on the port Flask actually binds
inside the container, not the port the browser used. Behind a reverse proxy /
k8s Service / Docker port map, the external port (e.g. 4567) differs from the
internal bind (8765); a loopback fetch to the external port is refused because
nothing listens there inside the container. ``TESSERAE_BIND_PORT`` (set by the
``tesserae`` CLI from ``--port``, or by the HA add-on config) names the real
internal port and must win over the URL's port."""

from __future__ import annotations

import pytest

from app.renderer import to_loopback_url


def test_host_swapped_to_loopback_port_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESSERAE_BIND_PORT", raising=False)
    monkeypatch.delenv("TESSERAE_BIND_HOST", raising=False)
    assert (
        to_loopback_url("http://tess.example:8765/compose/abc?w=800")
        == "http://127.0.0.1:8765/compose/abc?w=800"
    )


def test_bind_port_overrides_external_request_port(monkeypatch: pytest.MonkeyPatch) -> None:
    # The browser reached the UI on the external port 4567; the container binds
    # 8765. Without the override the renderer would fetch 127.0.0.1:4567 and be
    # refused (#129).
    monkeypatch.delenv("TESSERAE_BIND_HOST", raising=False)
    monkeypatch.setenv("TESSERAE_BIND_PORT", "8765")
    assert (
        to_loopback_url("http://tess.example:4567/compose/abc")
        == "http://127.0.0.1:8765/compose/abc"
    )


def test_falls_back_to_url_port_without_bind_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bare-metal single-port install: no mapping, the request port is the bind
    # port, so trusting it stays correct.
    monkeypatch.delenv("TESSERAE_BIND_PORT", raising=False)
    assert to_loopback_url("http://tess.example:5050/compose/abc") == (
        "http://127.0.0.1:5050/compose/abc"
    )


def test_https_base_url_downgraded_to_http(monkeypatch: pytest.MonkeyPatch) -> None:
    # An https base_url (reverse proxy terminating TLS in front of the
    # container) must not leak its scheme into the loopback URL: nothing
    # inside the container speaks TLS, so Chromium would hang in the
    # handshake until page.goto's 15s timeout and the preview breaks.
    monkeypatch.setenv("TESSERAE_BIND_PORT", "8765")
    assert (
        to_loopback_url("https://tess.example/compose/abc?w=800")
        == "http://127.0.0.1:8765/compose/abc?w=800"
    )


def test_https_explicit_port_downgraded_without_bind_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TESSERAE_BIND_PORT", raising=False)
    assert (
        to_loopback_url("https://tess.example:8765/compose/abc")
        == "http://127.0.0.1:8765/compose/abc"
    )


def test_ipv6_bind_uses_v6_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``--host ::`` (IPv6-only on many stacks): 127.0.0.1 has no listener,
    # so the compose fetch must go to [::1] on the same bind port.
    monkeypatch.setenv("TESSERAE_BIND_HOST", "::")
    monkeypatch.setenv("TESSERAE_BIND_PORT", "8765")
    assert (
        to_loopback_url("http://tess.example:4567/compose/abc?w=800")
        == "http://[::1]:8765/compose/abc?w=800"
    )


def test_ipv6_bind_without_bind_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_BIND_HOST", "::")
    monkeypatch.delenv("TESSERAE_BIND_PORT", raising=False)
    assert (
        to_loopback_url("http://tess.example:5050/compose/abc") == "http://[::1]:5050/compose/abc"
    )


def test_ipv4_bind_keeps_v4_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("TESSERAE_BIND_PORT", "8765")
    assert to_loopback_url("http://tess.example/compose/abc") == "http://127.0.0.1:8765/compose/abc"


def test_ingress_prefix_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under HA Ingress every in-app path carries HA's
    ``/api/hassio_ingress/<token>`` prefix. Loopback skips HA's proxy, so the
    prefix has to come off: with it the path matches no route, the auth gate's
    ``/compose/`` loopback bypass doesn't apply, and the renderer screenshots
    the setup page instead of the dashboard (a canvas Send, the panel preview
    and the template share preview all did exactly that)."""
    from flask import Flask

    monkeypatch.delenv("TESSERAE_BIND_PORT", raising=False)
    app = Flask(__name__)
    env = {"SCRIPT_NAME": "/api/hassio_ingress/tok3n"}
    with app.test_request_context("/compose/abc", environ_overrides=env, base_url="http://ha:8123"):
        assert (
            to_loopback_url("http://ha:8123/api/hassio_ingress/tok3n/compose/abc?w=800")
            == "http://127.0.0.1:8123/compose/abc?w=800"
        )


def test_path_untouched_without_ingress(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path that merely starts with the same characters as no script root
    is left alone, and so is every non-Ingress install."""
    from flask import Flask

    monkeypatch.delenv("TESSERAE_BIND_PORT", raising=False)
    app = Flask(__name__)
    with app.test_request_context("/compose/abc", base_url="http://tess.example:8765"):
        assert (
            to_loopback_url("http://tess.example:8765/compose/abc")
            == "http://127.0.0.1:8765/compose/abc"
        )
