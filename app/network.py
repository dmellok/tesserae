"""Network helpers.

``detect_local_ip()`` is the only thing in here: it returns the host's
primary outbound IPv4 address — the one that would be used to reach
the public internet. Tesserae uses this to build the ``base_url`` the
panel listeners hit to fetch frame artifacts, so it has to be a LAN
address (not 127.0.0.1) on a multi-interface host.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

import os
import socket


def detect_local_ip(fallback: str = "127.0.0.1") -> str:
    """Return the host's primary outbound IPv4 address.

    Uses the classic trick of opening a UDP socket "to" a public host
    (no actual packets are sent) and reading ``getsockname()`` — that
    forces the OS to consult its routing table and pick the interface
    it would use, which is exactly the address we want for the
    artifact URLs we hand to panel listeners.

    Falls back to ``fallback`` (127.0.0.1 by default) if the trick
    fails — happens on hosts with no default route (e.g. CI sandboxes
    with networking stubbed out).
    """
    # Honour an explicit override — handy for unusual setups (reverse
    # proxies, Docker Compose networks) or when running behind NAT.
    override = os.environ.get("TESSERAE_HOST_IP", "").strip()
    if override:
        return override

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 8.8.8.8 is just a routing hint — no packet ever leaves.
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        return str(ip) if ip else fallback
    except OSError:
        return fallback
    finally:
        sock.close()


def detect_base_url(port: int | None = None) -> str:
    """``http://<lan-ip>:<port>`` for the panel listeners.

    ``port`` defaults to the value captured at first-request time by
    ``app/main.py`` (so it tracks the actual Flask bind port, whatever
    it is), and falls back to 8000 / the ``TESSERAE_HTTP_PORT`` env
    var if nothing has been captured yet.
    """
    if port is None:
        env_port = os.environ.get("TESSERAE_HTTP_PORT", "").strip()
        port = int(env_port) if env_port.isdigit() else 8000
    return f"http://{detect_local_ip()}:{port}"
