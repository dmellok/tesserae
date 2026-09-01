"""IPv6 support in outbound URLs and the auth gate.

Three failure modes on IPv6-first installs:

* ``detect_base_url`` interpolated the host bare, so an IPv6
  ``TESSERAE_HOST_IP`` produced ``http://2403:aa::1:8765/...``, address
  and port ambiguous. HA's MQTT image entity rejects such URLs
  ("Invalid image URL ... received at topic .../state/image_url").
* The renderer's loopback rewrite hardcoded 127.0.0.1, which nothing
  answers on under an IPv6-only ``--host ::`` bind (see
  test_loopback_url.py for that side).
* The auth gate compared ``remote_addr`` strings, so IPv4 clients of a
  dual-stack bind arriving as ``::ffff:a.b.c.d`` matched neither the
  loopback bypass nor the private-range check.
"""

from __future__ import annotations

import ipaddress

from flask import Flask

from app import auth
from app.network import detect_base_url, url_host

# -- url_host ----------------------------------------------------------


def test_ipv6_literal_bracketed():
    assert url_host("2403:5800:1:2::2430") == "[2403:5800:1:2::2430]"


def test_ipv4_untouched():
    assert url_host("192.168.1.10") == "192.168.1.10"


def test_hostname_untouched():
    assert url_host("tesserae.local") == "tesserae.local"


def test_already_bracketed_untouched():
    assert url_host("[2403:5800:1:2::2430]") == "[2403:5800:1:2::2430]"


def test_non_ip_with_colon_untouched():
    # A host:port string is not this helper's job to fix; pass it through
    # rather than mangle it.
    assert url_host("broker.local:1883") == "broker.local:1883"


# -- detect_base_url ---------------------------------------------------


def test_base_url_brackets_ipv6_host_override(monkeypatch):
    monkeypatch.setenv("TESSERAE_HOST_IP", "2403:5800:1:2::2430")
    assert detect_base_url(8767) == "http://[2403:5800:1:2::2430]:8767"


def test_base_url_ipv4_unchanged(monkeypatch):
    monkeypatch.setenv("TESSERAE_HOST_IP", "192.168.1.10")
    assert detect_base_url(8765) == "http://192.168.1.10:8765"


# -- auth gate: IPv4-mapped IPv6 ---------------------------------------


def test_canonical_ip_unwraps_v4_mapped():
    assert auth._canonical_ip("::ffff:192.168.1.20") == ipaddress.ip_address("192.168.1.20")


def test_canonical_ip_passthrough_and_edge_cases():
    assert auth._canonical_ip("fe80::1") == ipaddress.ip_address("fe80::1")
    assert auth._canonical_ip(None) is None
    assert auth._canonical_ip("") is None
    assert auth._canonical_ip("localhost") is None


def _request_ctx(remote_addr: str):
    app = Flask(__name__)
    return app.test_request_context("/", environ_overrides={"REMOTE_ADDR": remote_addr})


def test_v4_mapped_loopback_is_loopback():
    # Dual-stack ``::`` bind: an IPv4 loopback connection (the renderer's
    # own compose fetch) arrives as ::ffff:127.0.0.1 and must keep the
    # loopback bypass, or renders screenshot the login page.
    with _request_ctx("::ffff:127.0.0.1"):
        assert auth._is_loopback() is True


def test_ipv6_loopback_is_loopback():
    with _request_ctx("::1"):
        assert auth._is_loopback() is True


def test_public_ipv6_is_not_loopback_or_private():
    with _request_ctx("2403:5800:1:2::99"):
        assert auth._is_loopback() is False
        assert auth._is_private_client() is False


def test_v4_mapped_rfc1918_is_private():
    with _request_ctx("::ffff:192.168.1.20"):
        assert auth._is_loopback() is False
        assert auth._is_private_client() is True


def test_ipv6_ula_and_link_local_are_private():
    with _request_ctx("fd12:3456::7"):
        assert auth._is_private_client() is True
    with _request_ctx("fe80::1"):
        assert auth._is_private_client() is True
