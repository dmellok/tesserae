"""Flask app factory.

Wires the plugin / renderer / device registries, composer, settings
store, auth gate, admin routes, MQTT transport (rebuildable on broker
setting changes via :func:`app.transport_wiring._rebuild_transport`),
push manager, and device-status subscriptions.

Usage::

    from app.main import create_app
    app = create_app()
    app.run(host="0.0.0.0", port=8000)

For tests, ``create_app(testing=True)`` swaps in a tmp data root, skips
the broker connection (via the no-op MQTT client in
``transport_wiring``), and short-circuits the auth gate so test
clients can hit /settings without juggling sessions.
"""

from __future__ import annotations

import logging
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
    trmnl_api,
    webhook_routes,
)
from app.discovery import DiscoveryCache
from app.ha_discovery import HomeAssistantDiscovery
from app.scheduler import Scheduler
from app.state.event_log import EventLog
from app.state.page_store import PageStore
from app.state.schedule_store import ScheduleStore
from app.state.settings_store import SettingsStore
from app.transport_wiring import _is_reloader_watcher, _rebuild_transport

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
    # MQTT topics even when it inherits from a shared kind. The seed
    # call carries legacy renderer-wide values for ``device_setting``
    # fields (dither/saturation/contrast) into the clone if it hasn't
    # been tuned yet — keeps upgrades invisible and gives new devices
    # the same defaults as the rest of the fleet.
    renderer_loader.clone_for_instances(renderers, devices)
    renderer_loader.seed_device_settings_from_base(renderers, settings)

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

    is_watcher = _is_reloader_watcher(dev) and not testing
    if is_watcher:
        logger.info(
            "dev reloader parent: skipping MQTT/scheduler/telemetry init (child process owns those)"
        )
    if testing or is_watcher:
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
            is_debug=dev,
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
    if not is_watcher:
        rebuild_transport()

    # Docker bridge networking gives us an internal IP that LAN
    # clients can't reach. If TESSERAE_HOST_IP isn't set and the
    # auto-detected IP looks like a Docker bridge, panels will see
    # broken MQTT broker URLs AND broken render-frame URLs (both
    # flow through detect_local_ip). Log a loud warning at startup
    # so anyone reading `docker compose logs` sees it before
    # spending time debugging.
    from app.network import docker_bridge_ip_warning

    if docker_bridge_ip_warning() and not testing:
        logger.warning(
            "%s",
            "Docker bridge IP detected and TESSERAE_HOST_IP is not set. "
            "Panels won't be able to reach the broker or fetch render frames. "
            "Set TESSERAE_HOST_IP=<your-host-lan-ip> in docker-compose.yml "
            "or use network_mode: host. See "
            "https://dmellok.github.io/tesserae/install/docker/",
        )

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
    if not testing and not is_watcher:
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
    webhook_routes.register(app)
    trmnl_api.register(app)

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
