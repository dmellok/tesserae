"""Flask app factory.

Wires the plugin / renderer / device registries, composer, settings store,
auth gate, admin routes, MQTT transport (rebuildable on broker setting
changes), push manager, and device-status subscriptions.

Usage:
    from app.main import create_app
    app = create_app()
    app.run(host="0.0.0.0", port=8000)

For tests, ``create_app(testing=True)`` swaps in a tmp data root, enables the
/_test/render route, skips the broker connection, and short-circuits the
auth gate so test clients can hit /settings without juggling sessions.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import time
from datetime import tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, abort, redirect, send_from_directory, url_for
from werkzeug.wrappers import Response

from app import (
    auth,
    composer,
    device_loader,
    events_routes,
    onboarding,
    page_routes,
    plugin_loader,
    renderer_loader,
    schedule_routes,
    send_routes,
    settings_routes,
    themes_routes,
)
from app.discovery import DiscoveryCache, device_id_from_status_topic
from app.embedded_broker import EmbeddedBroker
from app.ha_discovery import HomeAssistantDiscovery
from app.mdns import MdnsAdvertiser
from app.network import detect_base_url
from app.push import PushManager
from app.scheduler import Scheduler
from app.state.event_log import EventLog
from app.state.page_store import PageStore
from app.state.schedule_store import ScheduleStore
from app.state.settings_store import SettingsStore
from app.transport import BrokerConfig, MqttTransport

logger = logging.getLogger(__name__)

REPO_ROOT: Path = Path(__file__).resolve().parent.parent


def create_app(
    *,
    testing: bool = False,
    dev: bool = False,
    data_root: Path | None = None,
    plugins_dir: Path | None = None,
    renderers_dir: Path | None = None,
    devices_dir: Path | None = None,
) -> Flask:
    """Construct the Flask app with everything wired."""
    app = Flask(
        __name__,
        template_folder=str(REPO_ROOT / "templates"),
        static_folder=str(REPO_ROOT / "static"),
        static_url_path="/static",
    )
    app.testing = testing
    # Surfaced to templates so the admin UI can flag a --dev instance
    # (reddish-orange accent) and the mDNS advertiser picks tesserae-dev.local.
    app.config["DEV_MODE"] = dev

    data_root = data_root or REPO_ROOT / "data"
    plugins_dir = plugins_dir or REPO_ROOT / "plugins"
    renderers_dir = renderers_dir or REPO_ROOT / "renderers"
    devices_dir = devices_dir or REPO_ROOT / "devices"
    plugin_schema = REPO_ROOT / "schema" / "plugin.schema.json"
    renderer_schema = REPO_ROOT / "schema" / "renderer.schema.json"
    device_schema = REPO_ROOT / "schema" / "device.schema.json"

    plugin_data_root = data_root / "plugins"
    plugin_data_root.mkdir(parents=True, exist_ok=True)
    renderer_data_root = data_root / "renderers"
    renderer_data_root.mkdir(parents=True, exist_ok=True)
    device_data_root = data_root / "devices"
    device_data_root.mkdir(parents=True, exist_ok=True)
    renders_dir = data_root / "core" / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)

    settings = SettingsStore(data_root / "core" / "settings.json")
    app.config["SETTINGS_STORE"] = settings
    app.secret_key = auth.secret_key(settings)

    plugins = plugin_loader.discover(
        plugins_dir,
        schema_path=plugin_schema,
        data_root=plugin_data_root,
    )
    for perr in plugins.errors:
        logger.warning("plugin loader: %s — %s", perr.plugin_id, perr.message)

    renderers = renderer_loader.discover(
        renderers_dir,
        schema_path=renderer_schema,
        data_root=renderer_data_root,
    )
    for rerr in renderers.errors:
        logger.warning("renderer loader: %s — %s", rerr.renderer_id, rerr.message)

    devices = device_loader.discover(
        devices_dir,
        schema_path=device_schema,
        data_root=device_data_root,
    )
    for derr in devices.errors:
        logger.warning("device loader: %s — %s", derr.device_id, derr.message)

    # Multi-head: for each user-created device instance, clone the
    # renderers of its kind with the instance's id substituted into
    # the topic pattern. After this, each physical device has its own
    # MQTT topics even when it inherits from a shared kind.
    renderer_loader.clone_for_instances(renderers, devices)

    page_store = PageStore(data_root / "core" / "pages.json")
    schedule_store = ScheduleStore(data_root / "core" / "schedules.json")
    # Global cap 2000; device heartbeats get a 500-row sub-cap so a busy
    # fleet can't evict push / scheduler / auth history. We also only log a
    # device event when the status actually changed (below), so steady
    # idle heartbeats don't churn the log at all.
    event_log = EventLog(data_root / "core" / "events.db", cap=2000, device_cap=500)

    # Cache of the most recent parsed status heartbeat per device. The
    # MQTT subscription updates it; the settings page reads it. Plain dict
    # — single-writer (the broker dispatcher) so no lock needed for reads.
    status_cache: dict[str, dict[str, Any]] = {}
    # Wildcard listener on tesserae/+/status feeds this cache for every
    # device id we don't yet know about; the Settings → Devices page
    # surfaces it as a "Discovered" strip with one-click register.
    discovery_cache = DiscoveryCache()

    app.config["PLUGIN_REGISTRY"] = plugins
    app.config["RENDERER_REGISTRY"] = renderers
    app.config["DEVICE_REGISTRY"] = devices
    app.config["DEVICE_DATA_ROOT"] = device_data_root
    app.config["DEVICE_SCHEMA_PATH"] = device_schema
    app.config["DISCOVERY_CACHE"] = discovery_cache
    app.config["PAGE_STORE"] = page_store
    app.config["SCHEDULE_STORE"] = schedule_store
    app.config["EVENT_LOG"] = event_log
    app.config["PREVIEW_CACHE"] = {}
    app.config["RENDERS_DIR"] = renders_dir
    app.config["DEVICE_STATUS"] = status_cache
    app.config["DATA_ROOT"] = data_root
    # Self-update + backup live under Settings → System. Both are no-ops
    # under --dev (the reloader handles restarts there) and gated behind
    # admin auth.
    from app.updater import Updater as _Updater

    app.config["UPDATER"] = _Updater(REPO_ROOT, data_root)

    # Anonymous, opt-in telemetry — disabled by default; configure in
    # Settings → Server → App. Tests skip it entirely so no test run can
    # accidentally hit a real endpoint.
    from importlib import metadata as _metadata

    from app.telemetry import Telemetry as _Telemetry

    if testing:
        telemetry = _Telemetry.disabled()
    else:
        try:
            _pkg_version = _metadata.version("tesserae")
        except _metadata.PackageNotFoundError:
            _pkg_version = "0.0.0"
        telemetry = _Telemetry.from_settings(
            data_root=data_root,
            app_version=_pkg_version,
            settings_app=settings.get_section("app"),
            event_log=event_log,
        )
        telemetry.send("app.started")
    app.config["TELEMETRY"] = telemetry

    # Transport + push manager are (re)built from current broker settings.
    # Holding the rebuilder in app.config lets settings_routes call it on a
    # broker change without restarting the process. Device subscriptions
    # are re-issued on every rebuild because the transport instance changes.
    def rebuild_transport() -> None:
        _rebuild_transport(
            app,
            settings,
            renderers,
            devices,
            status_cache,
            discovery_cache,
            page_store,
            event_log,
            renders_dir,
            testing=testing,
            dev=dev,
        )

    app.config["REBUILD_TRANSPORT"] = rebuild_transport
    rebuild_transport()

    # The Scheduler doesn't hold a static PushManager reference because
    # rebuild_transport replaces it on broker setting changes. The factory
    # resolves from app.config at fire-time so the scheduler always sees
    # the current instance.
    def _resolve_timezone() -> tzinfo | None:
        # Read at every tick so a settings change picks up without
        # restarting the scheduler thread. 'system' (or empty) means
        # host-local; anything else is parsed as an IANA name.
        raw = str(settings.get_section("app").get("timezone") or "system").strip()
        if not raw or raw.lower() == "system":
            return None
        try:
            return ZoneInfo(raw)
        except ZoneInfoNotFoundError:
            logger.warning("settings.app.timezone=%r is not a known IANA zone", raw)
            return None

    scheduler = Scheduler(
        store=schedule_store,
        push_manager=lambda: app.config["PUSH_MANAGER"],
        event_log=event_log,
        timezone_provider=_resolve_timezone,
    )
    app.config["SCHEDULER"] = scheduler
    if not testing:
        scheduler.start()

    plugin_loader.register_routes(app, plugins)
    app.register_blueprint(composer.bp)
    settings_routes.register(app)
    schedule_routes.register(app)
    send_routes.register(app)
    events_routes.register(app)
    themes_routes.register(app)
    page_routes.register(app)
    onboarding.register(app)

    if not testing:
        auth.install_gate(app, settings)

    @app.before_request
    def _capture_http_port() -> None:
        """Stash the actual HTTP port the server is bound to so the
        base_url emitted in MQTT payloads matches reality (the user
        might have started us with `flask run --port 5050` instead of
        the default 8000). On first request, also refresh HA discovery
        configs so HA's stored URLs pick up the new port."""
        from flask import request

        previous = app.config.get("DETECTED_HTTP_PORT")
        port = request.host.split(":", 1)[1] if ":" in request.host else None
        if not port or not port.isdigit():
            return
        port_n = int(port)
        if port_n == previous:
            return
        app.config["DETECTED_HTTP_PORT"] = port_n
        # HA's stored entity configs include image_url / configuration_url
        # which embed the URL — re-publish if discovery is running.
        ha: HomeAssistantDiscovery | None = app.config.get("HA_DISCOVERY")
        if ha is not None:
            try:
                ha.refresh_entity_configs()
            except Exception:
                logger.exception("refreshing HA configs after port capture")

    @app.context_processor
    def _inject_nav_data() -> dict[str, Any]:
        """Make the list of admin-equipped plugins available to every
        template — the top-nav Plugins dropdown enumerates them."""
        registry = app.config["PLUGIN_REGISTRY"]
        return {
            "nav_admin_plugins": sorted(
                (p for p in registry.plugins.values() if p.has_admin),
                key=lambda p: p.name.lower(),
            ),
        }

    @app.get("/")
    def index() -> Response:
        # First run (password set, but setup not finished) lands in the
        # wizard. Once onboarded, Send is the default destination — link
        # clicks from HA etc. all want to push something.
        if not onboarding.is_onboarded(settings):
            return redirect(url_for("onboarding.index"))
        return redirect(url_for("send.index"))

    @app.get("/renders/<path:filename>")
    def renders(filename: str) -> Response:
        # Content-addressed artifacts each renderer writes. Pi and ESP32
        # clients fetch them here on every MQTT publish. The auth gate
        # restricts this route to loopback at the network level.
        if "/" in filename or filename.startswith("."):
            abort(404)
        return send_from_directory(renders_dir, filename)

    @app.get("/healthz")
    def healthz() -> tuple[str, int]:
        return "ok", 200

    return app


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


# Heartbeat fields that drift every beat (battery, signal, uptime). They
# update the live status cache + HA sensors, but a change in one of these
# alone shouldn't write an event-log row — otherwise every heartbeat logs.
_VOLATILE_STATUS_KEYS: frozenset[str] = frozenset(
    {"battery_mv", "battery_pct", "rssi", "voltage", "uptime", "uptime_s", "last_paint"}
)


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


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _noop_client_factory(client_id: str) -> _NoopMqttClient:
    return _NoopMqttClient(client_id)


def _serve(argv: list[str] | None = None) -> None:
    """Entry point for ``python -m app.main`` and the ``tesserae``
    console script. Defaults to a production WSGI server (waitress);
    pass ``--dev`` to opt into Flask's reload + debugger dev server.
    """
    import argparse

    # Windows-only no-op on POSIX: when we were spawned by the in-app
    # updater's restart(), block until the parent has fully exited so the
    # listening socket is released before waitress tries to bind it.
    from app.updater import wait_for_parent_exit

    wait_for_parent_exit()

    parser = argparse.ArgumentParser(prog="tesserae", description="Tesserae dashboard server.")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Use Flask's dev server (auto-reload, debugger). "
        "Default is waitress, a production WSGI server.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s"
    )
    app = create_app(dev=args.dev)

    if args.dev:
        logging.getLogger(__name__).info(
            "Starting Flask DEV server on http://%s:%d/ (reload + debugger ON)",
            args.host,
            args.port,
        )
        app.run(host=args.host, port=args.port, debug=True)
        return

    # Production: waitress. Single-process, multi-threaded — fine for
    # the single-user appliance. Avoids the "DO NOT USE IN PRODUCTION"
    # warning Flask's dev server prints on startup.
    from waitress import serve

    logging.getLogger(__name__).info(
        "Starting waitress on http://%s:%d/  (--dev for Flask dev server)",
        args.host,
        args.port,
    )
    serve(app, host=args.host, port=args.port, threads=8, ident="tesserae")


if __name__ == "__main__":
    _serve()
