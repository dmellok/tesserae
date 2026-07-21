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
    assert (
        to_loopback_url("http://tess.example:8765/compose/abc?w=800")
        == "http://127.0.0.1:8765/compose/abc?w=800"
    )


def test_bind_port_overrides_external_request_port(monkeypatch: pytest.MonkeyPatch) -> None:
    # The browser reached the UI on the external port 4567; the container binds
    # 8765. Without the override the renderer would fetch 127.0.0.1:4567 and be
    # refused (#129).
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
