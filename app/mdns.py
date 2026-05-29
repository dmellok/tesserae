"""Optional mDNS / Zeroconf advertiser.

Opt-in (Settings → App → "Advertise over mDNS"; default off). Publishes
an ``_http._tcp`` service whose server name is ``<hostname>.local`` so the
appliance is reachable as e.g. ``http://tesserae.local:<port>`` from any
Bonjour / Avahi client — without changing the host machine's hostname.

The trick: registering ``ServiceInfo`` with ``server="tesserae.local."``
plus the host's LAN IP publishes the **A record** for that name alongside
the service (PTR / SRV / TXT) records. So plain ``tesserae.local``
resolution works for everything, not just service-discovery-aware clients.

Runs alongside the OS resolver (Avahi on the Pi, mDNSResponder on macOS)
on the shared 5353 multicast group. ``start()`` is idempotent; ``stop()``
unregisters cleanly. Failures are non-fatal — the caller logs and carries
on with IP-based URLs.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Callable
from typing import Any

from app.network import detect_local_ip

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_http._tcp.local."


class MdnsAdvertiser:
    """Advertises ``<hostname>.local`` + an HTTP service over mDNS.

    ``zeroconf_factory`` is injectable so tests can pass a fake Zeroconf
    that records register/unregister/close calls without touching the
    network. ``ip`` overrides the auto-detected LAN address (also handy
    for tests / unusual network setups)."""

    def __init__(
        self,
        *,
        hostname: str = "tesserae",
        port: int = 8000,
        ip: str | None = None,
        zeroconf_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._hostname = (hostname or "tesserae").strip().rstrip(".") or "tesserae"
        self._port = int(port)
        self._ip = ip
        self._zeroconf_factory = zeroconf_factory
        self._zc: Any = None
        self._info: Any = None

    @property
    def server(self) -> str:
        """The advertised hostname, e.g. ``tesserae.local.`` (trailing dot
        is the mDNS fully-qualified form)."""
        return f"{self._hostname}.local."

    @property
    def running(self) -> bool:
        return self._zc is not None

    def _build_info(self) -> Any:
        from zeroconf import ServiceInfo

        ip = self._ip or detect_local_ip()
        return ServiceInfo(
            SERVICE_TYPE,
            f"Tesserae.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(ip)],
            port=self._port,
            properties={"path": "/"},
            server=self.server,
        )

    def start(self) -> None:
        if self._zc is not None:
            return
        if self._zeroconf_factory is not None:
            self._zc = self._zeroconf_factory()
        else:
            from zeroconf import Zeroconf

            self._zc = Zeroconf()
        self._info = self._build_info()
        self._zc.register_service(self._info)
        logger.info("mDNS: advertising %s on :%s", self.server, self._port)

    def stop(self) -> None:
        zc = self._zc
        info = self._info
        self._zc = None
        self._info = None
        if zc is None:
            return
        try:
            if info is not None:
                zc.unregister_service(info)
        except Exception:
            logger.exception("mDNS: unregister failed")
        try:
            zc.close()
        except Exception:
            logger.exception("mDNS: close failed")
