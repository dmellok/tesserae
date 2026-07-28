"""mDNS advertiser: builds the right ServiceInfo (tesserae.local + LAN IP)
and registers / unregisters through an injected Zeroconf, no network."""

from __future__ import annotations

import socket

from app.mdns import COMPANION_SERVICE_TYPE, SERVICE_TYPE, MdnsAdvertiser


class _FakeZeroconf:
    def __init__(self) -> None:
        self.registered: list = []
        self.unregistered: list = []
        self.closed = False

    def register_service(self, info) -> None:
        self.registered.append(info)

    def unregister_service(self, info) -> None:
        self.unregistered.append(info)

    def close(self) -> None:
        self.closed = True


def test_build_info_uses_hostname_and_ip() -> None:
    adv = MdnsAdvertiser(hostname="tesserae", port=8000, ip="192.168.1.50")
    info = adv._build_info()
    assert adv.server == "tesserae.local."
    assert info.server == "tesserae.local."
    assert info.port == 8000
    assert info.type == SERVICE_TYPE
    assert socket.inet_aton("192.168.1.50") in info.addresses


def test_build_companion_info_advertises_the_companion_service() -> None:
    adv = MdnsAdvertiser(hostname="tesserae", port=8000, ip="192.168.1.50")
    info = adv._build_companion_info()
    assert info.type == COMPANION_SERVICE_TYPE
    assert info.server == "tesserae.local."
    assert info.port == 8000
    # TXT record points the companion client at the API + version.
    assert info.properties[b"path"] == b"/api/app/v1"
    assert info.properties[b"api"] == b"companion"
    assert info.properties[b"v"] == b"1"


def test_start_registers_both_services_then_stop_unregisters_and_closes() -> None:
    fake = _FakeZeroconf()
    adv = MdnsAdvertiser(port=8000, ip="10.0.0.5", zeroconf_factory=lambda: fake)
    adv.start()
    assert adv.running is True
    # Both the HTTP service and the Tesserae-specific companion service.
    assert len(fake.registered) == 2
    types = {info.type for info in fake.registered}
    assert types == {SERVICE_TYPE, COMPANION_SERVICE_TYPE}
    assert all(info.server == "tesserae.local." for info in fake.registered)
    adv.stop()
    assert adv.running is False
    assert len(fake.unregistered) == 2
    assert fake.closed is True


def test_start_is_idempotent() -> None:
    fake = _FakeZeroconf()
    adv = MdnsAdvertiser(ip="10.0.0.5", zeroconf_factory=lambda: fake)
    adv.start()
    adv.start()
    assert len(fake.registered) == 2  # two services, registered once


def test_hostname_normalised() -> None:
    adv = MdnsAdvertiser(hostname="tesserae.", ip="10.0.0.5")
    assert adv.server == "tesserae.local."  # trailing dot stripped, .local. appended
    blank = MdnsAdvertiser(hostname="  ", ip="10.0.0.5")
    assert blank.server == "tesserae.local."  # falls back to default


def test_mdns_toggle_present_and_default_off() -> None:
    from app.settings_routes import APP_FIELDS

    field = next((f for f in APP_FIELDS if f["name"] == "mdns_enabled"), None)
    assert field is not None
    assert field["default"] is False
