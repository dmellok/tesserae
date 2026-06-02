"""Network helpers.

``detect_local_ip()`` is the only thing in here: it returns the host's
primary outbound IPv4 address — the one that would be used to reach
the public internet. Tesserae uses this to build the ``base_url`` the
panel listeners hit to fetch frame artifacts, so it has to be a LAN
address (not 127.0.0.1) on a multi-interface host.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


# Cached Supervisor lookup. The host IP doesn't change between
# Tesserae restarts (and if it does, the user restarts the add-on
# anyway), so a process-lifetime cache is fine. The sentinel
# distinguishes "never looked up" from "looked up, got None" — without
# it we'd re-query on every detect_local_ip call when Supervisor is
# unreachable.
_SUPERVISOR_IP_CACHE: tuple[str | None] | None = None


def _supervisor_host_ip() -> str | None:
    """Ask HA Supervisor for the host's LAN address.

    Under the official HA Add-on, the container runs on a docker bridge
    network. ``detect_local_ip()``'s socket trick would return the
    bridge IP (172.x.y.z), which LAN panels can't reach. HA Supervisor
    knows the host's real LAN address and exposes it through the
    add-on API at ``http://supervisor/network/info`` — guarded by a
    bearer token Supervisor injects as ``SUPERVISOR_TOKEN`` whenever
    ``hassio_api: true`` is set on the add-on.

    Returns the primary interface's first IPv4 address with the CIDR
    suffix stripped, or ``None`` when we're not in an HA Add-on (no
    token), the API call fails (network blip), or the host has no
    routable IPv4 (IPv6-only). The caller falls back to the socket
    trick in those cases.

    Result is cached for the process lifetime so repeated calls don't
    hammer Supervisor."""
    global _SUPERVISOR_IP_CACHE
    if _SUPERVISOR_IP_CACHE is not None:
        return _SUPERVISOR_IP_CACHE[0]
    token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    if not token:
        _SUPERVISOR_IP_CACHE = (None,)
        return None
    resolved: str | None = None
    try:
        req = urllib.request.Request(
            "http://supervisor/network/info",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as err:
        logger.debug("supervisor host-ip lookup failed: %s", err)
        _SUPERVISOR_IP_CACHE = (None,)
        return None
    interfaces = (payload.get("data") or {}).get("interfaces") or []
    # Prefer the interface flagged ``primary: true``; fall back to the
    # first interface with a usable IPv4 address.
    interfaces = sorted(interfaces, key=lambda i: 0 if i.get("primary") else 1)
    for iface in interfaces:
        addrs = ((iface.get("ipv4") or {}).get("address")) or []
        for addr in addrs:
            ip = str(addr).split("/", 1)[0].strip()
            if ip and not ip.startswith("127.") and ":" not in ip:
                logger.info(
                    "Resolved host IP via HA Supervisor: %s (iface=%s)",
                    ip,
                    iface.get("interface", "?"),
                )
                resolved = ip
                break
        if resolved is not None:
            break
    _SUPERVISOR_IP_CACHE = (resolved,)
    return resolved


def detect_local_ip(fallback: str = "127.0.0.1") -> str:
    """Return the host's primary outbound IPv4 address.

    Resolution order:
      1. ``TESSERAE_HOST_IP`` env var (always wins).
      2. HA Supervisor's ``network/info`` API (only reachable from
         inside an HA Add-on with ``hassio_api: true``).
      3. The classic UDP-getsockname trick (opens a socket "to" a
         public host without sending packets; the OS consults its
         routing table and we read which source IP it'd use).

    Falls back to ``fallback`` (127.0.0.1 by default) when every probe
    fails — typical on hosts with no default route (CI sandboxes,
    locked-down test environments).
    """
    # 1. Explicit override — handy for unusual setups (reverse proxies,
    #    Docker Compose networks) or NAT.
    override = os.environ.get("TESSERAE_HOST_IP", "").strip()
    if override:
        return override

    # 2. HA Supervisor (only meaningful inside an HA Add-on).
    supervisor_ip = _supervisor_host_ip()
    if supervisor_ip:
        return supervisor_ip

    # 3. Socket-trick fallback.
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
