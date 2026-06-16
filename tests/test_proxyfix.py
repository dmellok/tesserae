"""ProxyFix wiring: requests carrying ``X-Forwarded-*`` headers (e.g. via
NGINX Proxy Manager, Caddy, Cloudflare Tunnel) must surface the public
scheme + host + port to Flask, so ``url_for(_external=True)`` builds the
real callback URL the browser used. Without this, plugin OAuth flows
(e.g. spotify_core) build callbacks pointing at the internal
``http://internal-host:8765`` instead of ``https://public.example:8443``,
and the OAuth provider rejects the redirect URI mismatch.

ProxyFix lives at the WSGI layer, so these tests have to go through
the test client (which routes via wsgi_app) rather than
``test_request_context`` (which bypasses WSGI).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask, jsonify, url_for

from app.main import REPO_ROOT, create_app


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True

    @a.route("/healthz/external_url")
    def _external_url() -> object:
        return jsonify(url=url_for("healthz", _external=True))

    return a


def test_x_forwarded_proto_promotes_scheme_to_https(app: Flask) -> None:
    """A reverse proxy terminating TLS forwards plaintext to Tesserae but
    sets ``X-Forwarded-Proto: https``. After ProxyFix, ``url_for(...,
    _external=True)`` must emit the public ``https://`` scheme so OAuth
    callbacks match what the user registered with the provider."""
    client = app.test_client()
    resp = client.get(
        "/healthz/external_url",
        headers={"X-Forwarded-Proto": "https", "Host": "tesserae.example.org"},
    )
    assert resp.status_code == 200
    url = resp.get_json()["url"]
    assert url.startswith("https://"), url
    assert "tesserae.example.org" in url


def test_x_forwarded_host_with_port_preserved(app: Flask) -> None:
    """When the public URL is on a non-standard port (e.g. external 8443
    because the ISP blocks 443), the forwarded host header carries
    ``host:port``; ProxyFix must keep the port intact so the callback URL
    matches the registered redirect URI."""
    client = app.test_client()
    resp = client.get(
        "/healthz/external_url",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "tesserae.example.org:8443",
            "Host": "internal-host",
        },
    )
    url = resp.get_json()["url"]
    assert "tesserae.example.org:8443" in url, url
    assert url.startswith("https://"), url


def test_no_forwarded_headers_falls_back_to_request_host(app: Flask) -> None:
    """Without any reverse proxy in front (bare-metal install), Flask
    must keep using the request's own host. ProxyFix becomes a no-op so
    direct connections keep working unchanged."""
    client = app.test_client()
    resp = client.get(
        "/healthz/external_url",
        headers={"Host": "tesserae.local:8765"},
    )
    url = resp.get_json()["url"]
    assert url.startswith("http://tesserae.local:8765"), url
