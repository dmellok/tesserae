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
    page_routes,
    plugin_loader,
    renderer_loader,
    schedule_routes,
    send_routes,
    settings_routes,
    themes_routes,
)
from app.embedded_broker import EmbeddedBroker
from app.ha_discovery import HomeAssistantDiscovery
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

    page_store = PageStore(data_root / "core" / "pages.json")
    schedule_store = ScheduleStore(data_root / "core" / "schedules.json")
    # cap bumped from EventLog's default 500 to 2000 so per-renderer rows
    # (one per push per renderer) and device heartbeats (every minute or
    # so) don't crowd out the push history.
    event_log = EventLog(data_root / "core" / "events.db", cap=2000)

    # Cache of the most recent parsed status heartbeat per device. The
    # MQTT subscription updates it; the settings page reads it. Plain dict
    # — single-writer (the broker dispatcher) so no lock needed for reads.
    status_cache: dict[str, dict[str, Any]] = {}

    app.config["PLUGIN_REGISTRY"] = plugins
    app.config["RENDERER_REGISTRY"] = renderers
    app.config["DEVICE_REGISTRY"] = devices
    app.config["PAGE_STORE"] = page_store
    app.config["SCHEDULE_STORE"] = schedule_store
    app.config["EVENT_LOG"] = event_log
    app.config["PREVIEW_CACHE"] = {}
    app.config["RENDERS_DIR"] = renders_dir
    app.config["DEVICE_STATUS"] = status_cache

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
            page_store,
            event_log,
            renders_dir,
            testing=testing,
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
        # Send is the most common landing destination — first-run after
        # /setup, link clicks from HA, etc. all want to push something.
        # /pages is one nav hop away.
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


def _rebuild_transport(
    app: Flask,
    settings: SettingsStore,
    renderers: renderer_loader.RendererRegistry,
    devices: device_loader.DeviceRegistry,
    status_cache: dict[str, dict[str, Any]],
    page_store: PageStore,
    event_log: EventLog,
    renders_dir: Path,
    *,
    testing: bool,
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
                # Localhost loopback when broker binds there; otherwise
                # use the same bind for consistency.
                host = "127.0.0.1" if embedded_bind in ("127.0.0.1", "localhost") else embedded_bind
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
        client_id=str(broker_raw.get("client_id") or "tesserae"),
    )
    transport = MqttTransport(config, client_factory=factory)
    if host:
        try:
            transport.connect()
        except Exception:
            logger.exception("MQTT connect failed (host=%s); leaving transport offline", host)

    # Wire device status subscriptions. We always register them even when
    # the transport isn't connected — paho replays subscriptions on
    # connect, and tests want the dispatch path live even with a noop
    # client. Each callback closes over its device so parse_status routes
    # to the right module.
    for device in devices.all():
        _subscribe_device_status(transport, device, status_cache, event_log)

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
    )

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
        )
        try:
            new_ha.start()
            app.config["HA_DISCOVERY"] = new_ha
        except Exception:
            logger.exception("starting HA discovery")
            app.config["HA_DISCOVERY"] = None
    else:
        app.config["HA_DISCOVERY"] = None


def _subscribe_device_status(
    transport: MqttTransport,
    device: device_loader.Device,
    status_cache: dict[str, dict[str, Any]],
    event_log: EventLog,
) -> None:
    """Register the per-device status callback on the transport.

    Every heartbeat updates the in-memory status cache (read by the
    settings page) and writes one ``device`` event row (read by /events).
    The event-log write is dedup-free for now; cap-based eviction handles
    a flood of frequent heartbeats."""

    def on_status(topic: str, payload: bytes) -> None:
        del topic
        parsed = device.parse_status(payload)
        status_cache[device.id] = {
            "received_at": time.time(),
            "parsed": parsed,
        }
        event_log.record(
            type="device",
            source=device.id,
            target=device.status_topic,
            status="error" if "error" in parsed else "ok",
            error=parsed.get("error") if isinstance(parsed.get("error"), str) else None,
            extra={"parsed": parsed},
        )

    transport.subscribe(device.status_topic, on_status, qos=1)


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


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s"
    )
    create_app().run(host="0.0.0.0", port=8000, debug=True)
