"""SSRF guard classification: strict widget mode vs. permissive operator mode.

Uses IP literals and ``localhost`` only so ``socket.getaddrinfo`` stays local
and the suite makes no DNS calls.
"""

from __future__ import annotations

import pytest

from app.net_guard import BlockedURLError, assert_operator_url, host_is_blocked

# --- strict mode (default): the generic widget fetch path ---


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",  # loopback
        "::1",
        "localhost",
        "10.0.0.5",  # RFC1918
        "172.16.5.5",
        "192.168.1.5",
        "169.254.169.254",  # link-local / cloud metadata
        "",  # no host
    ],
)
def test_strict_blocks_local_and_metadata(host: str) -> None:
    assert host_is_blocked(host) is True


@pytest.mark.parametrize("host", ["8.8.8.8", "1.1.1.1"])
def test_strict_allows_public(host: str) -> None:
    assert host_is_blocked(host) is False


# --- operator mode (allow_local=True): webpage screenshot + remote image ---


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "::1", "localhost", "10.0.0.5", "192.168.1.5", "8.8.8.8"],
)
def test_operator_allows_loopback_lan_public(host: str) -> None:
    # Same-host capture is a supported use case, so loopback + LAN pass here.
    assert host_is_blocked(host, allow_local=True) is False


@pytest.mark.parametrize(
    "host",
    [
        "169.254.169.254",  # cloud metadata is never legitimate
        "169.254.1.1",  # link-local generally
        "224.0.0.1",  # multicast
    ],
)
def test_operator_still_blocks_metadata_and_reserved(host: str) -> None:
    assert host_is_blocked(host, allow_local=True) is True


# --- assert_operator_url end to end ---


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:3000", "http://192.168.1.10/dashboard", "https://8.8.8.8/"],
)
def test_assert_operator_url_allows(url: str) -> None:
    assert_operator_url(url)  # does not raise


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # metadata
        "ftp://example.com/x",  # bad scheme
        "file:///etc/passwd",  # bad scheme
        "http://",  # no host
    ],
)
def test_assert_operator_url_refuses(url: str) -> None:
    with pytest.raises(BlockedURLError):
        assert_operator_url(url)
