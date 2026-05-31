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
import os
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
        # ``is_docker`` lets the maintainer see what proportion of installs
        # run via the official Docker image vs install.sh / from source.
        # Set by the Dockerfile; safe-to-trust as long as nobody's
        # spoofing it on a native install (and if they did, they're just
        # mis-labelling their own data point).
        telemetry.send(
            "app.started",
            {"is_docker": "true" if os.environ.get("TESSERAE_IN_DOCKER") == "1" else "false"},
        )
    app.config["TELEMETRY"] = telemetry

    # Long-running Chromium owned by a dedicated thread. The pool is
    # *created* unconditionally but only *started* when something calls
    # .render() on it — see ``_current_browser_pool`` in transport_wiring
    # for the App-settings toggle that gates routing. Tests + the dev
    # reloader parent skip it; the cold per-render path still works.
    from app.renderer import BrowserPool as _BrowserPool

    if testing or is_watcher:
        app.config["BROWSER_POOL"] = None
    else:
        _browser_pool = _BrowserPool()
        app.config["BROWSER_POOL"] = _browser_pool
        # Shut Chromium down cleanly on Python exit so the worker thread,
        # child Chromium process, and the playwright event loop unwind in
        # order. atexit fires for waitress + the dev reloader child alike.
        import atexit as _atexit

        _atexit.register(_browser_pool.stop)

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
        page_exists=lambda page_id: page_store.get(page_id) is not None,
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

    # Heartbeat enrichment — fleet shape + activity counters since the
    # previous heartbeat. The provider is a closure so app_factory keeps
    # ownership of the registries; telemetry.py stays free of any direct
    # dependency on them. Everything here is a count or a kind id; no
    # device names, page names, theme palettes, or user data.
    if telemetry.enabled:
        import time as _time

        from app.state.user_themes import UserThemeStore as _UserThemeStore

        # Reset baseline at startup so the first heartbeat counts the
        # work done in the first hour, not since epoch.
        _heartbeat_baseline = {"ts": _time.time()}

        # Bucket activity counts to keep cardinality low + still
        # distinguish "no activity" from "high activity" on Aptabase.
        def _bucket(n: int) -> str:
            if n == 0:
                return "0"
            if n <= 10:
                return "1-10"
            if n <= 50:
                return "11-50"
            if n <= 200:
                return "51-200"
            return "200+"

        def _heartbeat_props() -> dict[str, str]:
            now = _time.time()
            since = _heartbeat_baseline["ts"]
            # Fleet shape — current state, not deltas.
            instances = [d for d in devices.all() if d.kind_of is not None]
            kinds = sorted({str(d.kind_of) for d in instances if d.kind_of})
            n_pages = len(page_store.list())
            try:
                user_themes = _UserThemeStore(plugins_dir / "themes_core" / "user.json").load()
                n_user_themes = len(user_themes)
            except Exception:
                n_user_themes = 0
            # Activity counters — events recorded since the previous
            # heartbeat. event_log.list() returns most-recent-first; we
            # paginate by 500 and stop once we cross the baseline so
            # large logs stay cheap.
            n_pushes = 0
            n_push_failures = 0
            n_widget_errors = 0
            cursor_id: int | None = None
            scan_limit = 500
            for _ in range(10):  # cap at 5,000 rows scanned per heartbeat
                rows = event_log.list(limit=scan_limit)
                if cursor_id is not None:
                    rows = [r for r in rows if r.id < cursor_id]
                if not rows:
                    break
                still_in_window = False
                for r in rows:
                    if r.timestamp < since:
                        continue
                    still_in_window = True
                    if r.type == "push":
                        if r.status == "sent":
                            n_pushes += 1
                        elif r.status in {"failed", "not_found"}:
                            n_push_failures += 1
                    elif r.type == "renderer" and r.status == "error":
                        n_widget_errors += 1
                cursor_id = rows[-1].id
                if not still_in_window or rows[-1].timestamp < since:
                    break
            _heartbeat_baseline["ts"] = now
            return {
                # Static fleet metadata: deployment kind + current shape.
                # Static across heartbeats but cheap and lets the maintainer
                # slice Aptabase views by deployment without joining tables.
                "is_docker": "true" if os.environ.get("TESSERAE_IN_DOCKER") == "1" else "false",
                "n_devices": str(len(instances)),
                "device_kinds": ",".join(kinds),
                "n_pages": str(n_pages),
                "n_user_themes": str(n_user_themes),
                # Bucketed activity counters since the previous heartbeat.
                "n_pushes_since_last": _bucket(n_pushes),
                "n_push_failures_since_last": _bucket(n_push_failures),
                "n_widget_errors_since_last": _bucket(n_widget_errors),
            }

        telemetry.set_heartbeat_props_provider(_heartbeat_props)

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

    # Device ID validation mirrors device_service so a 404 reflects an
    # unknown device rather than a path-traversal attempt.
    from app.device_service import DEVICE_ID_RE as _DEVICE_ID_RE

    @app.get("/preview/<device_id>.png")
    def preview_png(device_id: str) -> Response:
        """Stable per-device alias for the most-recent composition PNG.

        Unlike ``/renders/<digest>.png`` (where the URL changes every push
        because it's content-addressed), this URL stays the same as long
        as the device exists — drop it into Home Assistant's `generic`
        camera, a Grafana panel, or any wallboard and you get a
        self-updating preview without subscribing to MQTT.

        Always serves the composition PNG (what Playwright wrote before
        the per-renderer transform), so it stays viewable even when the
        device's actual artifact is a packed binary buffer (pi_bin,
        esp32_bin). Reachable from any private-network client; the
        ``/preview/`` prefix is on the LAN-bypass list in auth.py."""
        if not _DEVICE_ID_RE.match(device_id):
            abort(404)
        push_mgr = app.config.get("PUSH_MANAGER")
        if push_mgr is None:
            abort(503)
        latest = push_mgr.latest_render_for(device_id)
        if not latest:
            # No render yet for this device — return 404 rather than a
            # placeholder so HA's camera entity shows "unavailable",
            # which matches reality.
            abort(404)
        comp_digest = latest.get("composition_digest")
        if not isinstance(comp_digest, str) or not comp_digest:
            # Pre-0.8.6 entries don't carry the composition digest. They
            # get refreshed on the next push; until then, fall back to
            # the per-renderer artifact, which is at least the right
            # bytes even if not a .png on packed-binary devices.
            comp_digest = latest.get("digest")
            ext = latest.get("ext", "png")
            if not comp_digest:
                abort(404)
            resp = send_from_directory(renders_dir, f"{comp_digest}.{ext}")
        else:
            resp = send_from_directory(renders_dir, f"{comp_digest}.png")
        # The URL is stable but the bytes change every push — make sure
        # HA / browsers refetch instead of serving a cached frame.
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp

    @app.get("/healthz")
    def healthz() -> tuple[str, int]:
        return "ok", 200

    return app
