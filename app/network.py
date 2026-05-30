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


def is_docker_bridge_ip(ip: str) -> bool:
    """True if ``ip`` falls in a Docker bridge address range.

    Docker's default bridge network uses 172.17.0.0/16; user-defined
    bridges (the kind ``docker compose`` creates per project) typically
    fall in 172.18.0.0/16 through 172.31.0.0/16 — collectively
    172.16.0.0/12 by convention. These addresses route between
    containers but aren't reachable from outside the host without
    extra setup (host networking, port forwards, macvlan).

    The onboarding wizard + Settings → MQTT broker card use this to
    flag the case where ``detect_local_ip()`` returned a bridge
    address that would be useless for LAN clients to connect to —
    the user needs to set ``TESSERAE_HOST_IP`` to their actual host
    IP, or switch to ``network_mode: host`` in compose.
    """
    if not ip or "." not in ip:
        return False
    try:
        octets = [int(o) for o in ip.split(".")]
    except ValueError:
        return False
    if len(octets) != 4:
        return False
    # 172.16.0.0/12 covers 172.16.x.x through 172.31.x.x — the
    # standard Docker bridge range.
    return octets[0] == 172 and 16 <= octets[1] <= 31


def docker_bridge_ip_warning() -> bool:
    """True when the admin UI should warn that ``detect_local_ip()``
    returned a Docker bridge address — i.e. we're running inside the
    official Docker image, the user hasn't set ``TESSERAE_HOST_IP``,
    and the auto-detected IP is a bridge address that LAN clients
    can't reach.

    Centralises the "should we warn?" decision so the onboarding
    wizard and the settings page produce the same answer."""
    if not os.environ.get("TESSERAE_IN_DOCKER"):
        return False
    if os.environ.get("TESSERAE_HOST_IP", "").strip():
        return False
    return is_docker_bridge_ip(detect_local_ip())
