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


# -- v0.49.4: operator-supplied Public URL override --------------------


def test_public_url_setting_overrides_request_origin(app: Flask) -> None:
    """The app-level ``public_url`` setting is the escape hatch for
    deployments where ProxyFix can't infer the public URL (NPM not
    sending standard X-Forwarded-* headers, double-NAT setups, etc.).
    When set, every request's external URL builds from the configured
    scheme + host + port, regardless of what arrived on the wire."""
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"public_url": "https://tesserae.example.org:8443"})
    client = app.test_client()
    resp = client.get(
        "/healthz/external_url",
        headers={"Host": "internal-host:8765"},  # different from the configured override
    )
    url = resp.get_json()["url"]
    assert url.startswith("https://tesserae.example.org:8443"), url


def test_public_url_setting_trailing_slash_tolerated(app: Flask) -> None:
    """Operators paste URLs from address bars, which often have a trailing
    slash. Strip it so the resulting URL doesn't end up with a doubled
    slash in the middle."""
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"public_url": "https://tesserae.example.org:8443/"})
    client = app.test_client()
    resp = client.get("/healthz/external_url", headers={"Host": "internal-host:8765"})
    url = resp.get_json()["url"]
    assert url == "https://tesserae.example.org:8443/healthz", url


def test_public_url_setting_blank_falls_back_to_proxyfix(app: Flask) -> None:
    """Empty ``public_url`` setting means "auto-detect"; ProxyFix +
    request headers do their normal job. Confirms the override doesn't
    silently override with an empty string."""
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"public_url": ""})
    client = app.test_client()
    resp = client.get(
        "/healthz/external_url",
        headers={"X-Forwarded-Proto": "https", "Host": "tesserae.example.org"},
    )
    url = resp.get_json()["url"]
    assert url.startswith("https://tesserae.example.org"), url


def test_public_url_setting_malformed_value_falls_back_silently(app: Flask) -> None:
    """A typo / missing scheme in the ``public_url`` setting must not
    crash the appliance: the bad value is ignored and Flask falls back
    to its normal URL-building, keeping admin reachable so the operator
    can fix the typo."""
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"public_url": "not-a-real-url"})
    client = app.test_client()
    resp = client.get(
        "/healthz/external_url",
        headers={"Host": "tesserae.local:8765"},
    )
    assert resp.status_code == 200
    url = resp.get_json()["url"]
    # Falls back to request-derived URL; doesn't 500.
    assert url.startswith("http://tesserae.local"), url


# -- v0.49.5: Public URL must NOT corrupt DETECTED_HTTP_PORT -----------


def test_public_url_does_not_capture_proxy_port_for_device_urls(app: Flask) -> None:
    """Regression for 0.49.4: with Public URL set to e.g.
    ``https://tesserae.example.org:8443`` (the reverse proxy's HTTPS
    port), the Public URL middleware rewrites HTTP_HOST so request.host
    reads ``tesserae.example.org:8443``. Before this fix, the
    ``_capture_http_port`` before-request hook then stored 8443 as
    DETECTED_HTTP_PORT, and ``detect_base_url`` built LAN render URLs
    as ``http://<lan-ip>:8443/renders/…``, which NPM (HTTPS-only on
    8443) returned 400 for, breaking every device's frame fetch.

    The fix: ``_capture_http_port`` returns early when a Public URL is
    set, leaving DETECTED_HTTP_PORT alone so device-facing URLs keep
    using the actual Flask bind port (TESSERAE_HTTP_PORT / 8765)."""
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"public_url": "https://tesserae.example.org:8443"})
    client = app.test_client()
    # Hit a real endpoint so the before_request hook runs.
    client.get("/healthz")
    # DETECTED_HTTP_PORT must NOT be the proxy's external port (8443).
    # It can be unset (None) or the actual Flask bind port, but never
    # the proxy port.
    assert app.config.get("DETECTED_HTTP_PORT") != 8443


def test_public_url_unset_still_captures_real_port(app: Flask) -> None:
    """Sanity check: without Public URL, the existing port-capture
    behaviour is unchanged so users running Flask on a non-default
    port (e.g. ``flask run --port 5050``) still see correct device URLs."""
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"public_url": ""})
    client = app.test_client()
    client.get("/healthz", headers={"Host": "192.168.1.50:5050"})
    assert app.config.get("DETECTED_HTTP_PORT") == 5050
