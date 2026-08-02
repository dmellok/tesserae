"""Home-side cloud-relay wiring: create_instance relay support, the sealing
publisher, and the rendezvous pairing poller (ECDH tying both sides together)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app import device_loader, device_service, renderer_loader
from app.main import REPO_ROOT
from app.ota._codec import b64u_decode, b64u_encode
from app.relay_config import RELAY_SECTION
from app.relay_crypto import derive_shared_key, generate_keypair, unseal
from app.relay_pairing import RelayPairingPoller
from app.relay_publisher import RelayPublisher
from app.state.settings_store import SettingsStore


@pytest.fixture
def registries(tmp_path: Path) -> Any:
    data_root = tmp_path / "devices"
    devices = device_loader.discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=data_root,
    )
    renderers = renderer_loader.discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path / "rdata",
    )
    assert devices.errors == []
    assert renderers.errors == []
    return devices, renderers, data_root


# --- create_instance relay support -----------------------------------------


def test_create_instance_relay_mints_token_and_persists_frame_key(registries: Any) -> None:
    devices, renderers, data_root = registries
    frame_key = b64u_encode(b"\x11" * 32)
    result = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="parents_panel",
        kind_id="esp32_client",
        transport="relay",
        relay_frame_key=frame_key,
    )
    assert result.error is None and result.device is not None
    device = result.device
    assert device.transport == "relay"
    assert device.manifest.get("relay_frame_key") == frame_key
    # A relay panel authenticates its poll with a native-strength token.
    assert isinstance(device.manifest.get("access_token"), str)
    assert len(str(device.manifest["access_token"])) >= 20


# --- RelayPublisher --------------------------------------------------------


class _FakeClient:
    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.config_uploads: list[dict[str, Any]] = []

    def put_frame(self, **kwargs: Any) -> None:
        self.uploads.append(kwargs)

    def put_config(self, **kwargs: Any) -> None:
        self.config_uploads.append(kwargs)


class _Dev:
    def __init__(self, id: str, key_b64: str) -> None:
        self.id = id
        self.transport = "relay"
        self.manifest = {"relay_frame_key": key_b64}
        self.panel = {"w": 800, "h": 480}
        self.config_schema = {"sleep_interval_s": {"type": "int", "default": 60}}


def test_publisher_seals_uploads_and_dedupes(tmp_path: Path) -> None:
    renders_dir = tmp_path / "renders"
    renders_dir.mkdir()
    frame = b"packed-e-ink-frame-bytes"
    (renders_dir / "abc123.bin").write_bytes(frame)

    key = b"\x22" * 32
    dev = _Dev("panel1", b64u_encode(key))
    latest = {
        "digest": "abc123",
        "filename": "abc123.bin",
        "ext": "bin",
        "renderer_id": "esp32_bin",
    }

    pub = RelayPublisher(
        app=None,  # type: ignore[arg-type]
        devices=type("R", (), {"devices": {"panel1": dev}})(),
        settings=None,
        renders_dir=renders_dir,
        latest_render_fn=lambda _id: latest,
        run_async=False,
    )
    client = _FakeClient()

    pub._maybe_send(client, dev)  # type: ignore[arg-type]
    assert len(client.uploads) == 1
    up = client.uploads[0]
    assert up["etag"] == "abc123"
    assert up["panel_w"] == 800 and up["panel_h"] == 480
    # The uploaded body is sealed and decrypts back to the packed frame.
    assert unseal(up["sealed"], key) == frame

    # Same digest again → deduped, no second upload.
    pub._maybe_send(client, dev)  # type: ignore[arg-type]
    assert len(client.uploads) == 1


def test_publisher_syncs_sealed_config_and_dedupes(tmp_path: Path) -> None:
    """A Settings edit reaches the relay as a sealed config doc; unchanged
    content never re-uploads; a change re-uploads under a new etag."""
    settings = SettingsStore(tmp_path / "settings.json")
    settings.patch_section("devices", {"panel1": {"sleep_interval_s": 300}})

    key = b"\x33" * 32
    dev = _Dev("panel1", b64u_encode(key))
    pub = RelayPublisher(
        app=None,  # type: ignore[arg-type]
        devices=type("R", (), {"devices": {"panel1": dev}})(),
        settings=settings,
        renders_dir=tmp_path,
        latest_render_fn=lambda _id: None,
        run_async=False,
    )
    client = _FakeClient()

    pub._maybe_send_config(client, dev)  # type: ignore[arg-type]
    assert len(client.config_uploads) == 1
    up = client.config_uploads[0]
    doc = json.loads(unseal(up["sealed"], key))
    # The doc is what a local REST device would see: stored values + the
    # always_on default the firmware contract requires.
    assert doc == {"sleep_interval_s": 300, "always_on": False}

    # Unchanged content → deduped.
    pub._maybe_send_config(client, dev)  # type: ignore[arg-type]
    assert len(client.config_uploads) == 1

    # A config change re-uploads under a new etag.
    settings.patch_section("devices", {"panel1": {"sleep_interval_s": 900}})
    pub._maybe_send_config(client, dev)  # type: ignore[arg-type]
    assert len(client.config_uploads) == 2
    assert client.config_uploads[1]["etag"] != up["etag"]
    assert json.loads(unseal(client.config_uploads[1]["sealed"], key))["sleep_interval_s"] == 900


def test_publisher_skips_config_for_unpaired_device(tmp_path: Path) -> None:
    settings = SettingsStore(tmp_path / "settings.json")
    dev = _Dev("panel1", "")
    dev.manifest = {}  # not paired yet: no frame key to seal with
    pub = RelayPublisher(
        app=None,  # type: ignore[arg-type]
        devices=type("R", (), {"devices": {"panel1": dev}})(),
        settings=settings,
        renders_dir=tmp_path,
        latest_render_fn=lambda _id: None,
        run_async=False,
    )
    client = _FakeClient()
    pub._maybe_send_config(client, dev)  # type: ignore[arg-type]
    assert client.config_uploads == []


# --- Rendezvous pairing poller ---------------------------------------------


class _FakePairingClient:
    def __init__(self, pending: list[dict[str, str]]) -> None:
        self._pending = pending
        self.completed: list[dict[str, Any]] = []

    def pending_pairings(self) -> list[dict[str, str]]:
        return self._pending

    def complete_pairing(self, **kwargs: Any) -> None:
        self.completed.append(kwargs)


def test_pairing_poller_completes_ecdh_and_creates_relay_device(
    registries: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    devices, renderers, data_root = registries
    settings = SettingsStore(tmp_path / "settings.json")

    # Home keypair (as register_this_install would persist it).
    home_priv, home_pub = generate_keypair()
    settings.patch_section(
        RELAY_SECTION,
        {
            "enabled": True,
            "base_url": "https://relay.example",
            "install_id": "inst_1",
            "publisher_token_secret": "ptok",
            "install_privkey_secret": b64u_encode(home_priv),
            "pending_pairings": {
                "CODE42": {"device_id": "parents_panel", "kind": "esp32_client", "panel": {}}
            },
        },
    )

    # Panel side generates its own keypair and presents its public key.
    panel_priv, panel_pub = generate_keypair()
    client = _FakePairingClient([{"code": "CODE42", "panel_pubkey": b64u_encode(panel_pub)}])
    monkeypatch.setattr("app.relay_pairing.build_client", lambda _cfg: client)

    poller = RelayPairingPoller(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        settings=settings,
        run_async=False,
    )
    assert poller.poll_once() == 1

    # A relay device was created with the ECDH-derived frame key.
    device = devices.get("parents_panel")
    assert device is not None and device.transport == "relay"
    stored_key = b64u_decode(str(device.manifest["relay_frame_key"]))
    assert stored_key == derive_shared_key(home_priv, panel_pub)

    # The panel derives the identical key from its own private key + home pub.
    completion = client.completed[0]
    assert completion["home_pubkey_b64u"] == b64u_encode(home_pub)
    panel_derived = derive_shared_key(panel_priv, b64u_decode(completion["home_pubkey_b64u"]))
    assert panel_derived == stored_key

    # The relay is handed the token hash it will validate polls against.
    import hashlib

    token = str(device.manifest["access_token"])
    assert completion["device_token"] == token
    assert completion["device_token_sha256"] == hashlib.sha256(token.encode()).hexdigest()

    # The consumed pairing slot is dropped so it isn't retried.
    assert "CODE42" not in (settings.get_section(RELAY_SECTION).get("pending_pairings") or {})


def test_poller_pulls_relay_status_into_heartbeat_pipeline(
    registries: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="parents_panel",
        kind_id="esp32_client",
        transport="relay",
        relay_frame_key=b64u_encode(b"\x44" * 32),
    )
    settings = SettingsStore(tmp_path / "settings.json")

    status = {"body": '{"battery":87}', "received_at": "2026-08-02T00:00:00Z"}

    class _StatusClient:
        def get_device_status(self, _id: str) -> Any:
            return status

    monkeypatch.setattr("app.relay_pairing.build_client", lambda _cfg: _StatusClient())

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.transport_wiring.record_status_heartbeat",
        lambda **kw: calls.append(kw),
    )

    fake_app = type("A", (), {"config": {"DEVICE_STATUS": {}, "EVENT_LOG": object()}})()
    poller = RelayPairingPoller(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        settings=settings,
        app=fake_app,
        run_async=False,
    )

    poller.poll_once()
    assert len(calls) == 1
    assert calls[0]["device"].id == "parents_panel"
    assert calls[0]["payload"] == b'{"battery":87}'
    assert calls[0]["event_target"] == "relay://parents_panel/status"

    # Same received_at → de-duped, no second ingest.
    poller.poll_once()
    assert len(calls) == 1

    # New received_at → ingested again.
    status["received_at"] = "2026-08-02T00:05:00Z"
    poller.poll_once()
    assert len(calls) == 2


def test_pairing_poller_uses_panel_self_report_when_slot_blank(
    registries: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    devices, renderers, data_root = registries
    settings = SettingsStore(tmp_path / "settings.json")
    home_priv, _home_pub = generate_keypair()
    # Slot carries only the device id: no kind, no panel dims.
    settings.patch_section(
        RELAY_SECTION,
        {
            "enabled": True,
            "install_id": "i1",
            "publisher_token_secret": "p",
            "install_privkey_secret": b64u_encode(home_priv),
            "pending_pairings": {"CODE": {"device_id": "parents_panel"}},
        },
    )
    _panel_priv, panel_pub = generate_keypair()

    class _Client:
        def __init__(self) -> None:
            self.completed: list[Any] = []

        def pending_pairings(self) -> Any:
            # The panel reported its geometry + kind at pairing.
            return [
                {
                    "code": "CODE",
                    "panel_pubkey": b64u_encode(panel_pub),
                    "panel_w": 800,
                    "panel_h": 480,
                    "model": "esp32_client",
                    "gamut": "waveshare_e6",
                }
            ]

        def complete_pairing(self, **kw: Any) -> None:
            self.completed.append(kw)

    monkeypatch.setattr("app.relay_pairing.build_client", lambda _cfg: _Client())

    poller = RelayPairingPoller(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        settings=settings,
        run_async=False,
    )
    assert poller.poll_once() == 1

    device = devices.get("parents_panel")
    assert device is not None
    assert device.transport == "relay"
    assert device.kind_of == "esp32_client"  # resolved from the panel's model
    assert device.panel["w"] == 800 and device.panel["h"] == 480  # from the panel's geometry
    assert device.panel.get("gamut") == "waveshare_e6"  # from the panel's self-report


def test_pairing_poller_noops_when_not_linked(tmp_path: Path) -> None:
    settings = SettingsStore(tmp_path / "settings.json")
    poller = RelayPairingPoller(
        devices=type("R", (), {"devices": {}, "get": lambda self, _i: None})(),
        renderers=None,  # type: ignore[arg-type]
        data_root=tmp_path,
        settings=settings,
        run_async=False,
    )
    assert poller.poll_once() == 0


def test_relay_settings_json_roundtrips(tmp_path: Path) -> None:
    """Sanity: the relay section (incl. pending_pairings) survives a store
    reload as plain JSON."""
    settings = SettingsStore(tmp_path / "settings.json")
    settings.patch_section(RELAY_SECTION, {"install_id": "x", "pending_pairings": {"c": {"k": 1}}})
    reloaded = json.loads((tmp_path / "settings.json").read_text())
    assert reloaded[RELAY_SECTION]["install_id"] == "x"


# --- Settings UI: relay devices are first-class -----------------------------


class _FakeRelayPublisher:
    def __init__(self) -> None:
        self.nudges: list[str | None] = []

    def on_config_change(self, device_id: str | None = None) -> None:
        self.nudges.append(device_id)


@pytest.fixture
def relay_app(tmp_path: Path) -> Any:
    from app.main import create_app

    app = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    with app.app_context():
        result = device_service.create_instance(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=tmp_path,
            instance_id="parents_panel",
            kind_id="esp32_client",
            name="Parents panel",
            panel_overrides={"w": 800, "h": 480},
            transport="relay",
            relay_frame_key=b64u_encode(b"\x44" * 32),
        )
        assert result.error is None
    client = app.test_client()
    client.post("/setup", data={"password": "hunter22z", "password_confirm": "hunter22z"})
    return app, client


def test_devices_tab_treats_relay_device_as_first_class(relay_app: Any) -> None:
    _app, client = relay_app
    html = client.get("/settings/devices").get_data(as_text=True)
    card_start = html.find('id="device-parents_panel"')
    assert card_start != -1
    # The relay panel is this app's only device instance, so everything from
    # its card onward belongs to it (the card body nests <section>s, which
    # defeats a clean single-section slice).
    card = html[card_start:]
    # Full config card: the same knobs a local device gets.
    for marker in ("sleep_interval_s", "quiet_hours_enabled", "panel_orientation", "panel_w"):
        assert marker in card, marker
    # Relay-aware header + connection details, no misleading LAN rows.
    assert ">Relay</span>" in card
    assert "Cloud relay (sealed frames)" in card
    # No MQTT/REST transport switch anywhere: the relay panel is the only
    # instance on this page, and flipping it would orphan the panel.
    assert "Switch to" not in html


def test_relay_device_transport_switch_is_refused(relay_app: Any) -> None:
    app, client = relay_app
    resp = client.post(
        "/settings/devices/parents_panel/set-transport",
        data={"transport": "rest"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    device = app.config["DEVICE_REGISTRY"].get("parents_panel")
    assert device is not None and device.transport == "relay"


def test_relay_config_save_queues_a_relay_sync_not_a_broker_publish(relay_app: Any) -> None:
    app, client = relay_app
    fake = _FakeRelayPublisher()
    app.config["RELAY_PUBLISHER"] = fake
    resp = client.post(
        "/settings/devices/parents_panel/save",
        data={
            "_active_tab": "general",
            "sleep_interval_s": "900",
            "button_wake_s": "30",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    stored = app.config["SETTINGS_STORE"].get_section("devices").get("parents_panel", {})
    assert stored.get("sleep_interval_s") == 900
    # The save nudged the relay publisher instead of publishing to a broker.
    assert "parents_panel" in fake.nudges


def test_relay_tab_links_each_panel_to_its_device_card(relay_app: Any) -> None:
    app, client = relay_app
    with app.app_context():
        settings = app.config["SETTINGS_STORE"]
        settings.patch_section(
            RELAY_SECTION,
            {"enabled": True, "install_id": "i1", "publisher_token_secret": "p"},
        )
    html = client.get("/settings/relay").get_data(as_text=True)
    assert "#device-parents_panel" in html
    assert "Configure" in html
