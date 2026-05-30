"""MQTT transport wiring — rebuild, subscriptions, and status merging.

Lifted out of :mod:`app.main` so the factory there is just app + route
wiring. Everything here is concerned with the broker side: tearing
down and rebuilding the transport on broker setting changes, attaching
the per-device status + wildcard discovery subscriptions, and merging
incoming heartbeats into the live status cache.

External imports (tests, settings_routes diagnostics) still reference
``status_changed_meaningfully`` / ``_resolve_client_id`` etc. through
:mod:`app.main`, which re-exports them from here.
"""

from __future__ import annotations

import logging
import os
import re
import socket
from pathlib import Path
from typing import Any

from flask import Flask

from app import device_loader, renderer_loader
from app.discovery import DiscoveryCache, device_id_from_status_topic
from app.embedded_broker import EmbeddedBroker
from app.ha_discovery import HomeAssistantDiscovery
from app.mdns import MdnsAdvertiser
from app.network import detect_base_url
from app.push import PushManager
from app.state.event_log import EventLog
from app.state.page_store import PageStore
from app.state.settings_store import SettingsStore
from app.transport import BrokerConfig, MqttTransport

logger = logging.getLogger(__name__)


# Heartbeat fields that drift every beat (battery, signal, uptime). They
# update the live status cache + HA sensors, but a change in one of these
# alone shouldn't write an event-log row — otherwise every heartbeat logs.
_VOLATILE_STATUS_KEYS: frozenset[str] = frozenset(
    {"battery_mv", "battery_pct", "rssi", "voltage", "uptime", "uptime_s", "last_paint"}
)


# -- small helpers ------------------------------------------------------


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _is_reloader_watcher(dev: bool) -> bool:
    """Werkzeug's ``--dev`` reloader spawns a child process to actually
    serve traffic; the parent just watches files and respawns the child
    on changes. We want heavyweight startup work (MQTT connect,
    scheduler tick loop, telemetry ``app.started`` send) to run ONLY in
    the child — otherwise both processes init with the same MQTT client
    id and the broker bounces each off the other in an endless
    ping-pong, the scheduler fires every job twice, and every dev
    restart costs two ``app.started`` events instead of one.

    The child sets ``WERKZEUG_RUN_MAIN=true`` before re-importing the
    module; the parent leaves it unset."""
    return dev and os.environ.get("WERKZEUG_RUN_MAIN") != "true"


def _resolve_client_id(configured: Any, *, dev: bool) -> str:
    """MQTT client id for this instance.

    A configured value (Settings → Broker → MQTT client id) wins. Otherwise
    default to ``tesserae-<hostname>`` so two machines sharing one broker
    don't collide — MQTT brokers evict a duplicate client id the moment
    another connects with it, which causes the endless reconnect loop
    ("disconnected unexpectedly" every couple of seconds). The ``--dev``
    server appends ``-dev`` so it never fights its own prod instance."""
    base = str(configured or "").strip()
    if not base:
        host = socket.gethostname().split(".", 1)[0].strip().lower()
        host = re.sub(r"[^a-z0-9_-]+", "-", host).strip("-") or "host"
        base = f"tesserae-{host}"
    return f"{base}-dev" if dev else base


# -- status merge -------------------------------------------------------


def status_changed_meaningfully(prev: dict[str, Any], merged: dict[str, Any]) -> bool:
    """True if the device status changed in a way worth an event-log row —
    ignoring volatile drift (battery / signal / uptime). First sighting
    (empty ``prev``) always counts."""
    if not prev:
        return True
    before = {k: v for k, v in prev.items() if k not in _VOLATILE_STATUS_KEYS}
    after = {k: v for k, v in merged.items() if k not in _VOLATILE_STATUS_KEYS}
    return before != after


def merge_status_parsed(prev: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Merge a freshly-parsed heartbeat into the prior cached dict.

    Why merge instead of overwrite: an MQTT last-will message (or any
    partial heartbeat) typically carries only a subset of the well-known
    fields — e.g. ``{"state": "offline"}`` published by the broker when
    the firmware disconnects. ``parse_status`` fills the absent fields
    with ``None``. Overwriting would blank the last-known battery / rssi
    / ip from the previous good heartbeat. Merging keeps them visible.

    Rules: a ``None`` in ``new`` doesn't overwrite a non-None in
    ``prev``. Everything else takes the new value, so a real heartbeat
    that brings a fresh number always wins."""
    merged: dict[str, Any] = dict(prev)
    for key, value in new.items():
        if value is None and merged.get(key) is not None:
            continue
        merged[key] = value
    return merged


# -- subscriptions ------------------------------------------------------


def _subscribe_device_status(
    transport: MqttTransport,
    device: device_loader.Device,
    status_cache: dict[str, dict[str, Any]],
    event_log: EventLog,
    app: Flask,
) -> None:
    """Register the per-device status callback on the transport.

    Every heartbeat updates the in-memory status cache (read by the
    settings page) and writes one ``device`` event row (read by /events).
    The event-log write is dedup-free for now; cap-based eviction handles
    a flood of frequent heartbeats. When HA discovery is running, the
    heartbeat also feeds that display's last-seen / battery / signal / IP
    sensors."""
    import time

    def on_status(topic: str, payload: bytes) -> None:
        del topic
        parsed = device.parse_status(payload)
        prev = status_cache.get(device.id, {}).get("parsed", {})
        merged = merge_status_parsed(prev, parsed)
        status_cache[device.id] = {
            "received_at": time.time(),
            "parsed": merged,
        }
        # Only log when the status actually changed (or carries an error).
        # The live cache + HA sensors still see every heartbeat; this just
        # keeps steady heartbeats from churning the capped event log.
        if "error" in parsed or status_changed_meaningfully(prev, merged):
            event_log.record(
                type="device",
                source=device.id,
                target=device.status_topic,
                status="error" if "error" in parsed else "ok",
                error=parsed.get("error") if isinstance(parsed.get("error"), str) else None,
                extra={"parsed": merged},
            )
        ha: HomeAssistantDiscovery | None = app.config.get("HA_DISCOVERY")
        if ha is not None:
            try:
                ha.note_device_heartbeat(device.id, merged)
            except Exception:
                logger.exception("HA discovery: heartbeat notify failed for %s", device.id)

    transport.subscribe(device.status_topic, on_status, qos=1)


def _subscribe_discovery(
    transport: MqttTransport,
    devices: device_loader.DeviceRegistry,
    discovery_cache: DiscoveryCache,
) -> None:
    """Wildcard listener on ``tesserae/+/status``. Heartbeats for known
    device ids are ignored here (the per-device handler already ran);
    anything else gets cached so the Settings UI can surface it for
    one-click registration."""

    def on_wildcard(topic: str, payload: bytes) -> None:
        # Skip topics already owned by a registered INSTANCE. Built-in
        # kinds aren't physical devices — heartbeats arriving on a
        # kind's default topic (e.g. fresh pi_bin install publishing
        # to tesserae/pi_bin/status) belong in discovery so the user
        # can promote them to instances.
        if any(d.status_topic == topic for d in devices.all() if d.kind_of is not None):
            return
        device_id = device_id_from_status_topic(topic)
        if device_id is None:
            return
        entry = discovery_cache.record(device_id, payload)
        if entry is not None:
            logger.info(
                "discovery: heartbeat from %s (kind=%s, fw=%s)",
                device_id,
                entry.kind,
                entry.fw_version,
            )

    transport.subscribe("tesserae/+/status", on_wildcard, qos=1)


# -- noop client for tests ---------------------------------------------


class _NoopMqttClient:
    """Stand-in MQTT client used during tests: silently accepts connect /
    publish / subscribe so the rest of the app can wire up without a
    broker. Tests that exercise transport behaviour build their own fakes."""

    on_connect: object = None
    on_disconnect: object = None
    on_message: object = None

    def __init__(self, client_id: str) -> None:
        self.client_id = client_id

    def username_pw_set(self, username: str, password: str | None) -> None:
        return None

    def connect(self, host: str, port: int, keepalive: int) -> int:
        return 0

    def disconnect(self) -> int:
        return 0

    def loop_start(self) -> int:
        return 0

    def loop_stop(self) -> int:
        return 0

    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> object:
        return type("R", (), {"rc": 0})()

    def subscribe(self, topic: str, qos: int) -> tuple[int, int]:
        return (0, 1)


def _noop_client_factory(client_id: str) -> _NoopMqttClient:
    return _NoopMqttClient(client_id)


# -- rebuild ------------------------------------------------------------


def _rebuild_transport(
    app: Flask,
    settings: SettingsStore,
    renderers: renderer_loader.RendererRegistry,
    devices: device_loader.DeviceRegistry,
    status_cache: dict[str, dict[str, Any]],
    discovery_cache: DiscoveryCache,
    page_store: PageStore,
    event_log: EventLog,
    renders_dir: Path,
    *,
    testing: bool,
    dev: bool = False,
) -> None:
    """Tear down any existing transport, construct a fresh one from current
    broker settings, rewire the PushManager, and re-subscribe every loaded
    device's status topic. Safe to call repeatedly.

    During testing the transport is constructed with a no-op fake client
    factory so test code can drive the system without a broker."""
    old_transport: MqttTransport | None = app.config.get("MQTT_TRANSPORT")
    if old_transport is not None:
        try:
            old_transport.disconnect()
        except Exception:
            logger.exception("disconnecting old transport")

    broker_raw = settings.get_section("broker")
    host = str(broker_raw.get("host") or "")

    # Optional in-process MQTT broker. If enabled and host isn't already
    # set, default the transport to talk to it on the embedded bind
    # address. When the operator's also set credentials we feed them
    # back into the transport so it can auth against its own broker.
    embedded_enabled = _truthy(broker_raw.get("embedded_enabled"))
    embedded_port = int(broker_raw.get("embedded_port") or 1883)
    embedded_bind = str(broker_raw.get("embedded_bind") or "127.0.0.1").strip() or "127.0.0.1"
    embedded_user = (str(broker_raw.get("embedded_username") or "")).strip()
    embedded_pass = str(broker_raw.get("embedded_password_secret") or "")
    old_embedded: EmbeddedBroker | None = app.config.get("EMBEDDED_BROKER")
    if old_embedded is not None:
        try:
            old_embedded.stop()
        except Exception:
            logger.exception("stopping old embedded broker")
        app.config.pop("EMBEDDED_BROKER", None)
    embedded_self_connect = False
    if embedded_enabled and not testing:
        # renders_dir lives at data_root/core/renders, so its parent is
        # the right home for the broker's password file.
        passwd_path = renders_dir.parent / ".amqtt-passwd"
        embedded = EmbeddedBroker(
            bind=embedded_bind,
            port=embedded_port,
            username=embedded_user or None,
            password=embedded_pass or None,
            passwd_path=passwd_path,
        )
        try:
            embedded.start()
            app.config["EMBEDDED_BROKER"] = embedded
            if not host:
                # The broker runs on this machine, so the transport always
                # connects over localhost. 0.0.0.0 means "bind all
                # interfaces" (so LAN clients can reach it) — it isn't a
                # connectable address, so map it to loopback here too.
                host = (
                    embedded_bind
                    if embedded_bind not in ("127.0.0.1", "localhost", "0.0.0.0")
                    else "127.0.0.1"
                )
                embedded_self_connect = True
        except Exception:
            logger.exception(
                "embedded broker failed to start on %s:%s; transport will stay offline",
                embedded_bind,
                embedded_port,
            )

    factory = _noop_client_factory if testing else None
    # When the transport is hitting our own embedded broker and the
    # operator set creds for it, reuse those creds — no need to set
    # them in two places.
    transport_user = broker_raw.get("username") or None
    transport_pass = broker_raw.get("password_secret") or None
    if embedded_self_connect and embedded_user and not transport_user:
        transport_user = embedded_user
        transport_pass = embedded_pass or None
    config = BrokerConfig(
        host=host or "localhost",
        port=int(broker_raw.get("port") or embedded_port or 1883),
        username=transport_user,
        password=transport_pass,
        keepalive=int(broker_raw.get("keepalive") or 60),
        client_id=_resolve_client_id(broker_raw.get("client_id"), dev=dev),
    )
    transport = MqttTransport(config, client_factory=factory)
    if host:
        try:
            transport.connect()
        except Exception:
            logger.exception("MQTT connect failed (host=%s); leaving transport offline", host)

    # Wire status subscriptions for every user-registered instance only;
    # built-in kinds are templates with no physical device behind them.
    # A client publishing on a kind's default topic (e.g. fresh pi_bin
    # install on tesserae/pi_bin/status) is caught by the wildcard
    # listener instead and surfaces in the Discovered strip — the user
    # registers it as an instance from there.
    for device in devices.all():
        if device.kind_of is None:
            continue
        _subscribe_device_status(transport, device, status_cache, event_log, app)

    # Wildcard listener for discovery: any heartbeat on tesserae/+/status
    # for an id we don't know about gets cached for the Settings UI.
    _subscribe_discovery(transport, devices, discovery_cache)

    app_section = settings.get_section("app")

    # Auto-detected from the host's primary outbound interface — panel
    # listeners need a LAN IP, not localhost. The port is captured
    # below from the first incoming HTTP request so we always emit the
    # actual bind port even when `flask run --port` is non-default.
    # Override via TESSERAE_HOST_IP / TESSERAE_HTTP_PORT env vars.
    def _base_url() -> str:
        return detect_base_url(app.config.get("DETECTED_HTTP_PORT"))

    app.config["BASE_URL_FN"] = _base_url
    app.config["MQTT_TRANSPORT"] = transport
    app.config["PUSH_MANAGER"] = PushManager(
        registry=renderers,
        page_store=page_store,
        transport=transport,
        settings=settings,
        event_log=event_log,
        renders_dir=renders_dir,
        base_url_fn=_base_url,
        devices=devices,
    )
    # Sweep render artifacts orphaned by event-log eviction (or a manual
    # history clear) at boot. Idempotent + never fatal.
    try:
        app.config["PUSH_MANAGER"].prune_orphan_renders()
    except Exception:
        logger.exception("startup render prune failed")

    # HA discovery is opt-in. If previously running, stop it first so the
    # old listeners detach from the previous PushManager (which has been
    # replaced above). Then start fresh if the toggle is on.
    old_ha: HomeAssistantDiscovery | None = app.config.get("HA_DISCOVERY")
    if old_ha is not None:
        try:
            old_ha.stop()
        except Exception:
            logger.exception("stopping previous HA discovery")
    ha_enabled = bool(app_section.get("ha_discovery_enabled"))
    if ha_enabled:
        new_ha = HomeAssistantDiscovery(
            transport=transport,
            push_manager=app.config["PUSH_MANAGER"],
            page_store=page_store,
            base_url_fn=_base_url,
            device_registry=devices,
            device_status=app.config["DEVICE_STATUS"],
        )
        try:
            new_ha.start()
            app.config["HA_DISCOVERY"] = new_ha
        except Exception:
            logger.exception("starting HA discovery")
            app.config["HA_DISCOVERY"] = None
    else:
        app.config["HA_DISCOVERY"] = None

    # mDNS: opt-in advertiser for tesserae.local (+ an _http._tcp service)
    # so the appliance is reachable by name without touching the host's
    # hostname. Stop any previous instance before (re)starting.
    old_mdns: MdnsAdvertiser | None = app.config.get("MDNS_ADVERTISER")
    if old_mdns is not None:
        try:
            old_mdns.stop()
        except Exception:
            logger.exception("stopping previous mDNS advertiser")
        app.config.pop("MDNS_ADVERTISER", None)
    if _truthy(app_section.get("mdns_enabled")) and not testing:
        # DETECTED_HTTP_PORT is captured on the first request; at boot fall
        # back to the env/default. Only the SRV record cares about the port
        # — the tesserae.local A record resolves regardless.
        env_port = os.environ.get("TESSERAE_HTTP_PORT", "").strip()
        default_port = int(env_port) if env_port.isdigit() else 8000
        port = int(app.config.get("DETECTED_HTTP_PORT") or default_port)
        # Dev server advertises tesserae-dev.local so it can coexist with a
        # production instance on the same LAN without a name clash.
        advertiser = MdnsAdvertiser(hostname="tesserae-dev" if dev else "tesserae", port=port)
        try:
            advertiser.start()
            app.config["MDNS_ADVERTISER"] = advertiser
        except Exception:
            logger.exception("starting mDNS advertiser")
            app.config["MDNS_ADVERTISER"] = None
    else:
        app.config["MDNS_ADVERTISER"] = None
