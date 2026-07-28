"""Optional mDNS / Zeroconf advertiser.

Opt-in (Settings → App → "Advertise over mDNS"; default off). Publishes
two services under the one ``<hostname>.local`` server name:

* ``_http._tcp`` so the appliance is reachable as e.g.
  ``http://tesserae.local:<port>`` from any Bonjour / Avahi client.
* ``_tesserae._tcp`` so the companion app finds *Tesserae specifically*
  instead of scanning every HTTP service on the network (discussion #147).
  Its TXT record carries the companion API path + version.

The trick: registering ``ServiceInfo`` with ``server="tesserae.local."``
plus the host's LAN IP publishes the **A record** for that name alongside
the service (PTR / SRV / TXT) records. So plain ``tesserae.local``
resolution works for everything, not just service-discovery-aware clients.

Runs alongside the OS resolver (Avahi on the Pi, mDNSResponder on macOS)
on the shared 5353 multicast group. ``start()`` is idempotent; ``stop()``
unregisters cleanly. Failures are non-fatal, the caller logs and carries
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
# Tesserae-specific service so the companion app can find this server
# without walking every _http._tcp responder on the LAN (discussion #147).
COMPANION_SERVICE_TYPE = "_tesserae._tcp.local."


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
        port: int = 8765,
        ip: str | None = None,
        zeroconf_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._hostname = (hostname or "tesserae").strip().rstrip(".") or "tesserae"
        self._port = int(port)
        self._ip = ip
        self._zeroconf_factory = zeroconf_factory
        self._zc: Any = None
        self._info: Any = None
        self._infos: list[Any] = []

    @property
    def server(self) -> str:
        """The advertised hostname, e.g. ``tesserae.local.`` (trailing dot
        is the mDNS fully-qualified form)."""
        return f"{self._hostname}.local."

    @property
    def running(self) -> bool:
        return self._zc is not None

    def _build_info(self) -> Any:
        """The ``_http._tcp`` service (plus the A record via ``server``)."""
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

    def _build_companion_info(self) -> Any:
        """The ``_tesserae._tcp`` service the companion app looks for. Its
        TXT record points at the companion API + its version so a client can
        confirm compatibility straight off discovery."""
        from zeroconf import ServiceInfo

        ip = self._ip or detect_local_ip()
        return ServiceInfo(
            COMPANION_SERVICE_TYPE,
            f"Tesserae.{COMPANION_SERVICE_TYPE}",
            addresses=[socket.inet_aton(ip)],
            port=self._port,
            properties={"path": "/api/app/v1", "api": "companion", "v": "1"},
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
        # Keep ``_info`` pointing at the HTTP service for back-compat; the
        # full set (HTTP + companion) lives in ``_infos``.
        self._info = self._build_info()
        self._infos = [self._info, self._build_companion_info()]
        for info in self._infos:
            self._zc.register_service(info)
        logger.info(
            "mDNS: advertising %s (%s + %s) on :%s",
            self.server,
            SERVICE_TYPE,
            COMPANION_SERVICE_TYPE,
            self._port,
        )

    def stop(self) -> None:
        zc = self._zc
        infos = self._infos
        self._zc = None
        self._info = None
        self._infos = []
        if zc is None:
            return
        for info in infos:
            try:
                zc.unregister_service(info)
            except Exception:
                logger.exception("mDNS: unregister failed")
        try:
            zc.close()
        except Exception:
            logger.exception("mDNS: close failed")
