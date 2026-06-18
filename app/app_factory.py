"""Flask app factory.

Wires the plugin / renderer / device registries, composer, settings
store, auth gate, admin routes, MQTT transport (rebuildable on broker
setting changes via :func:`app.transport_wiring._rebuild_transport`),
push manager, and device-status subscriptions.

Usage::

    from app.main import create_app
    app = create_app()
    app.run(host="0.0.0.0", port=8765)

For tests, ``create_app(testing=True)`` swaps in a tmp data root, skips
the broker connection (via the no-op MQTT client in
``transport_wiring``), and short-circuits the auth gate so test
clients can hit /settings without juggling sessions.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import time
from datetime import tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, abort, redirect, request, send_from_directory, url_for
from flask.json.provider import DefaultJSONProvider
from pydantic import BaseModel
from werkzeug.wrappers import Response

from app import (
    auth,
    composer,
    device_battery_routes,
    device_loader,
    events_routes,
    history_routes,
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
from app.state.rotation_store import RotationStore
from app.state.schedule_store import ScheduleStore
from app.state.settings_store import SettingsStore
from app.transport_wiring import _is_reloader_watcher, _rebuild_transport

logger = logging.getLogger(__name__)

REPO_ROOT: Path = Path(__file__).resolve().parent.parent


_MAX_THUMB_HEIGHT_MULTIPLIER: int = 8


def _collect_battery_status(app: Flask) -> list[dict[str, Any]]:
    """Snapshot of every registered device instance that reported a
    battery_pct in its last heartbeat. Returns a list of ``{id, name,
    pct, tone}`` dicts sorted worst-charge-first so the topbar
    indicator + popover always lead with the device that needs
    attention. ``tone`` is one of ``"critical"`` / ``"low"`` / ``"ok"``
    so the template can colour-code without re-doing the math.

    Mains-powered devices (no battery, the Pi paths) are omitted, the
    indicator doesn't render at all when the list is empty so a
    panel-only deployment stays uncluttered."""
    registry = app.config.get("DEVICE_REGISTRY")
    status_cache: dict[str, dict[str, Any]] = app.config.get("DEVICE_STATUS") or {}
    if registry is None:
        return []
    out: list[dict[str, Any]] = []
    for device in registry.devices.values():
        # Built-in kinds aren't real targets; only user-registered
        # instances heartbeat to the status cache.
        if device.kind_of is None:
            continue
        status = status_cache.get(device.id) or {}
        parsed = status.get("parsed") or {}
        raw = parsed.get("battery_pct")
        if raw is None:
            continue
        try:
            pct = int(raw)
        except (TypeError, ValueError):
            continue
        if pct <= 10:
            tone = "critical"
        elif pct <= 30:
            tone = "low"
        else:
            tone = "ok"
        out.append(
            {
                "id": device.id,
                "name": device.display_name,
                "pct": pct,
                "tone": tone,
            }
        )
    out.sort(key=lambda entry: entry["pct"])
    return out


def _serve_render_thumbnail(renders_dir: Path, filename: str, width: int) -> Response | None:
    """Return a downscaled cached variant of ``filename`` at the requested
    width, or ``None`` if anything goes wrong (caller falls through to the
    full image).

    Thumbnails live under ``<renders_dir>/.thumbs/<digest>-w<width>.png``
    so they share filesystem permissions with the originals + get cleaned
    up alongside them. Naming is content-addressed: the source PNG is
    immutable (its digest IS its filename), so a thumbnail can be cached
    forever without invalidation. Aspect ratio is preserved; the only
    height cap is a safety guard against a maliciously tall input.
    """
    src = renders_dir / filename
    if not src.is_file():
        return None
    thumbs_dir = renders_dir / ".thumbs"
    base = Path(filename).stem
    suffix = Path(filename).suffix or ".png"
    thumb_name = f"{base}-w{width}{suffix}"
    thumb_path = thumbs_dir / thumb_name
    if not thumb_path.is_file():
        try:
            from PIL import Image

            thumbs_dir.mkdir(parents=True, exist_ok=True)
            with Image.open(src) as im:
                im.thumbnail(
                    (width, width * _MAX_THUMB_HEIGHT_MULTIPLIER),
                    Image.Resampling.LANCZOS,
                )
                # Pass format= explicitly so Pillow doesn't try to
                # infer it from the temp path's extension. The tmp
                # filename has ``.tmp`` appended for atomic-rename
                # discipline, which used to break Pillow's
                # extension-based format guess with
                # ``unknown file extension: .tmp``.
                tmp_path = thumb_path.with_name(thumb_path.name + ".tmp")
                im.save(tmp_path, format=im.format or "PNG", optimize=True)
                tmp_path.replace(thumb_path)
        except Exception:
            logger.warning("thumbnail render failed for %s", filename, exc_info=True)
            return None
    return send_from_directory(thumbs_dir, thumb_name)


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
    # Modern Python registers ``.webmanifest`` → ``application/manifest+json``
    # in the standard mimetypes module, but Alpine-based containers and
    # Windows installs sometimes ship without that entry, which makes
    # browsers ignore the manifest. Register defensively at startup.
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    app = Flask(
        __name__,
        template_folder=str(REPO_ROOT / "templates"),
        static_folder=str(REPO_ROOT / "static"),
        static_url_path="/static",
    )
    app.testing = testing

    # Pydantic models in templates: Flask's tojson filter (and jsonify)
    # round-trip values through ``app.json.dumps``, which doesn't know
    # how to serialize Pydantic v2 BaseModel instances by default. The
    # rotation + schedule edit forms hand the live model's
    # ``conditions: list[Condition]`` straight to ``| tojson``, so the
    # second a step gains a condition the next edit re-render 500s.
    # Patching the JSON provider once here fixes that for every caller.
    class _PydanticJSONProvider(DefaultJSONProvider):
        def default(self, o: Any) -> Any:
            if isinstance(o, BaseModel):
                return o.model_dump()
            return super().default(o)

    app.json = _PydanticJSONProvider(app)

    # Home Assistant Add-on / Ingress support is split in two:
    #
    # * The URL-prefix middleware always wraps the WSGI app. It only
    #   acts when ``X-Ingress-Path`` is present on a request, a no-op
    #   for every non-HA install, so wrapping unconditionally is safe
    #   and avoids the "I set the env var but URLs still 404" footgun
    #   when only one of the two knobs is configured.
    #
    # * The auth-gate bypass requires the ``TESSERAE_HA_INGRESS=1`` env
    #   var AS WELL AS the header on the live request. The env var is
    #   the security knob, a stray header from a misconfigured reverse
    #   proxy on a non-ingress install can't bypass auth without it.
    app.config["HA_INGRESS_MODE"] = os.environ.get("TESSERAE_HA_INGRESS", "").strip() in {
        "1",
        "true",
        "yes",
        "on",
    }

    class _IngressPrefixMiddleware:
        """Read the public path HA Supervisor proxied this request from
        and patch the WSGI environ so Flask's ``url_for`` emits URLs
        the iframe can follow. Header looks like
        ``X-Ingress-Path: /api/hassio_ingress/<token>``; some Supervisor
        versions strip it from PATH_INFO, some don't, so we tolerate
        both."""

        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __call__(self, environ: dict[str, Any], start_response: Any) -> Any:
            prefix = environ.get("HTTP_X_INGRESS_PATH", "").rstrip("/")
            if prefix:
                environ["SCRIPT_NAME"] = prefix
                path = environ.get("PATH_INFO", "")
                if path.startswith(prefix):
                    environ["PATH_INFO"] = path[len(prefix) :] or "/"
            return self._inner(environ, start_response)

    app.wsgi_app = _IngressPrefixMiddleware(app.wsgi_app)  # type: ignore[method-assign]

    # Operator-supplied ``Public URL`` override (Settings → App). Force-
    # overrides the scheme + host + port Flask sees on every request, so
    # ``url_for(..., _external=True)`` builds URLs from the configured
    # public URL even when the upstream reverse proxy isn't sending the
    # ``X-Forwarded-*`` headers ProxyFix expects. Empty value = no-op,
    # falls back to ProxyFix + request auto-detection.
    class _PublicUrlOverrideMiddleware:
        """Inject ``wsgi.url_scheme`` / ``HTTP_HOST`` from the configured
        public URL so external link generation is reverse-proxy-agnostic.

        Reads the settings store per request rather than capturing at
        startup so the operator can change the value without a restart.
        Failing to parse the value (typo, missing scheme) silently
        falls back to the unchanged environ, keeping the appliance
        reachable while the bad value gets fixed.
        """

        def __init__(self, inner: Any, app_config: dict[str, Any]) -> None:
            self._inner = inner
            self._app_config = app_config

        def __call__(self, environ: dict[str, Any], start_response: Any) -> Any:
            store = self._app_config.get("SETTINGS_STORE")
            if store is None:
                return self._inner(environ, start_response)
            try:
                section = store.get_section("app") or {}
                raw = str(section.get("public_url") or "").strip().rstrip("/")
                if raw:
                    from urllib.parse import urlparse

                    parsed = urlparse(raw)
                    if parsed.scheme and parsed.netloc:
                        environ["wsgi.url_scheme"] = parsed.scheme
                        environ["HTTP_HOST"] = parsed.netloc
            except Exception:
                pass
            return self._inner(environ, start_response)

    app.wsgi_app = _PublicUrlOverrideMiddleware(app.wsgi_app, app.config)  # type: ignore[method-assign]

    # Trust ``X-Forwarded-*`` headers when a reverse proxy (NGINX Proxy
    # Manager, Caddy, Cloudflare Tunnel, an HA Ingress sidecar) is in
    # front of us. Without this, plugin OAuth callbacks ``url_for(...,
    # _external=True)`` build URLs from the internal HTTP / port-8765
    # connection instead of the real public ``https://...:8443`` the
    # browser saw, and the redirect URI registered with Spotify /
    # Google / etc. won't match.
    #
    # We trust ONE hop by default; that's the standard "behind one
    # reverse proxy" topology Tesserae is run in. Operators stacking
    # multiple proxies can override via the env var. Bare-metal
    # deployments without any reverse proxy still work cleanly: the
    # headers won't be present and ProxyFix becomes a no-op.
    from werkzeug.middleware.proxy_fix import ProxyFix

    try:
        _forwarded_hops = max(0, int(os.environ.get("TESSERAE_FORWARDED_HOPS", "1")))
    except ValueError:
        _forwarded_hops = 1
    if _forwarded_hops > 0:
        app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
            app.wsgi_app,
            x_for=_forwarded_hops,
            x_proto=_forwarded_hops,
            x_host=_forwarded_hops,
            x_port=_forwarded_hops,
            x_prefix=_forwarded_hops,
        )

    # Resolve the running package version. Prefer pyproject.toml on disk
    # (so a source checkout reflects post-pip-install bumps) and fall
    # back to importlib.metadata for installed wheels. Used by both
    # telemetry and the static asset cache-buster below.
    def _resolve_pkg_version() -> str:
        pyproject = REPO_ROOT / "pyproject.toml"
        if pyproject.exists():
            try:
                import tomllib

                return str(
                    tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
                )
            except (OSError, KeyError, ValueError):
                pass
        try:
            from importlib import metadata as _metadata

            return _metadata.version("tesserae")
        except Exception:
            return "0.0.0"

    pkg_version = _resolve_pkg_version()
    app.config["APP_VERSION"] = pkg_version

    # Bust browser caches on every ship. In prod the version alone is
    # enough (every release bumps it). In dev we suffix the startup time
    # so each `--dev` restart also breaks the cache without the user
    # having to hard-reload after editing client.js / .css.
    static_version = pkg_version if not dev else f"{pkg_version}-{int(time.time())}"
    app.config["STATIC_VERSION"] = static_version

    @app.url_defaults
    def _add_static_version(endpoint: str, values: dict[str, Any]) -> None:
        if endpoint == "static" and "v" not in values:
            values["v"] = static_version

    # Human-readable timestamp filter for templates. Used by the events
    # page so each row reads as "Jun  1 14:23:45" (local time) instead
    # of a raw unix float. Same shape on the client streamer.
    def _fmt_time(ts: float | int) -> str:
        from datetime import datetime as _dt

        try:
            return _dt.fromtimestamp(float(ts)).strftime("%b %e %H:%M:%S").replace("  ", " ")
        except (TypeError, ValueError, OSError):
            return ""

    app.jinja_env.filters["fmt_time"] = _fmt_time

    # Surfaced to templates so the admin UI can flag a --dev instance
    # (reddish-orange accent) and the mDNS advertiser picks tesserae-dev.local.
    app.config["DEV_MODE"] = dev

    # Data root resolution order: explicit ``data_root=`` kwarg (tests),
    # then the ``TESSERAE_DATA_ROOT`` env var (HA Add-on sets this to
    # ``/data`` so Supervisor's per-add-on persistent volume holds
    # Tesserae's state across upgrades), then the in-repo default.
    if data_root is None:
        env_root = os.environ.get("TESSERAE_DATA_ROOT", "").strip()
        data_root = Path(env_root) if env_root else REPO_ROOT / "data"
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
    # Marketplace-installed widgets land under the persistent data
    # volume so they survive Docker / HA Add-on image upgrades (which
    # replace the bundled plugins_dir at /app/plugins/). Added in
    # 0.42.2; see https://github.com/dmellok/tesserae/issues/11 (if any).
    user_plugins_dir = data_root / "marketplace"
    user_plugins_dir.mkdir(parents=True, exist_ok=True)

    settings = SettingsStore(data_root / "core" / "settings.json")
    app.config["SETTINGS_STORE"] = settings
    # When running as an HA Add-on, Supervisor's options.json is the
    # canonical place for MQTT connection + log level. Apply it before
    # auth.secret_key / the transport wiring read the broker section,
    # so they see the HA-supplied values on every restart. No-op
    # outside HA (no options.json present).
    if app.config.get("HA_INGRESS_MODE"):
        from app.ha_options import apply_ha_options

        apply_ha_options(settings)
    app.secret_key = auth.secret_key(settings)

    # v0.49: at-rest encryption for manifest-declared ``secret: true``
    # fields. Resolve the SecretBox after ``auth.secret_key`` has run
    # (so the session secret is guaranteed to exist) and inject it
    # back into the store; legacy plaintext values keep reading and
    # get migrated to ciphertext on the next save.
    from app.secret_box import SecretBox

    secret_box = SecretBox.resolve(app.secret_key)
    settings.set_secret_box(secret_box)
    app.config["SECRET_BOX"] = secret_box

    plugins = plugin_loader.discover(
        plugins_dir,
        schema_path=plugin_schema,
        data_root=plugin_data_root,
        # Marketplace-installed widgets live under the persistent
        # data volume; the loader walks both dirs and merges. See
        # the user_plugins_dir comment above.
        additional_plugins_dirs=[user_plugins_dir],
    )
    for perr in plugins.errors:
        logger.warning("plugin loader: %s, %s", perr.plugin_id, perr.message)

    renderers = renderer_loader.discover(
        renderers_dir,
        schema_path=renderer_schema,
        data_root=renderer_data_root,
    )
    for rerr in renderers.errors:
        logger.warning("renderer loader: %s, %s", rerr.renderer_id, rerr.message)

    # Backfill firmware-native panel dims on ESP32 instance manifests
    # that predate the v0.20.x PanelPreset refactor. Without this, a
    # Waveshare 13.3" added before the refactor (which has no
    # native_w / native_h on disk) gets misclassified at runtime as an
    # Inky 13.3" by the dims-only matching loop, packs at the wrong row
    # stride, and prints a distorted-looking frame. Idempotent, does
    # nothing on a fresh install or for already-migrated manifests.
    from app.device_service import backfill_native_panel_dims

    _patched_ids = backfill_native_panel_dims(device_data_root)
    if _patched_ids:
        logger.info(
            "device migration: backfilled native panel dims on %s",
            ", ".join(_patched_ids),
        )

    devices = device_loader.discover(
        devices_dir,
        schema_path=device_schema,
        data_root=device_data_root,
    )
    for derr in devices.errors:
        logger.warning("device loader: %s, %s", derr.device_id, derr.message)

    # Multi-head: for each user-created device instance, clone the
    # renderers of its kind with the instance's id substituted into
    # the topic pattern. After this, each physical device has its own
    # MQTT topics even when it inherits from a shared kind. The seed
    # call carries legacy renderer-wide values for ``device_setting``
    # fields (dither/saturation/contrast) into the clone if it hasn't
    # been tuned yet, keeps upgrades invisible and gives new devices
    # the same defaults as the rest of the fleet.
    renderer_loader.clone_for_instances(renderers, devices)
    renderer_loader.seed_device_settings_from_base(renderers, settings)

    page_store = PageStore(data_root / "core" / "pages.json")
    schedule_store = ScheduleStore(data_root / "core" / "schedules.json")
    rotation_store = RotationStore(data_root / "core" / "rotations.json")
    # User themes live alongside core stores at ``data/themes/user.json``.
    # The store creates the directory on first save so a fresh install
    # without any custom themes leaves no empty directory behind.
    from app.state.community_themes import CommunityThemeStore
    from app.state.user_themes import UserThemeStore

    user_themes_store = UserThemeStore(data_root / "themes" / "user.json")
    # Community themes installed from the marketplace live next door.
    # Each install drops a ``<id>/theme.json`` + ``<id>/theme.css``
    # under this dir; the store walks it lazily on every request.
    community_themes_store = CommunityThemeStore(data_root / "themes" / "community")
    # Global cap 2000; device heartbeats get a 500-row sub-cap so a busy
    # fleet can't evict push / scheduler / auth history. We also only log a
    # device event when the status actually changed (below), so steady
    # idle heartbeats don't churn the log at all.
    event_log = EventLog(data_root / "core" / "events.db", cap=2000, device_cap=500)

    # Cache of the most recent parsed status heartbeat per device. The
    # MQTT subscription updates it; the settings page reads it. Plain dict
    # , single-writer (the broker dispatcher) so no lock needed for reads.
    status_cache: dict[str, dict[str, Any]] = {}
    # Wildcard listener on tesserae/+/status feeds this cache for every
    # device id we don't yet know about; the Settings → Devices page
    # surfaces it as a "Discovered" strip with one-click register.
    discovery_cache = DiscoveryCache()

    app.config["PLUGIN_REGISTRY"] = plugins
    # Install the capability hooks once the registry is built. Idempotent,
    # so a hot-reload from the dev server re-entering create_app() doesn't
    # stack hooks. The hooks are a no-op when no widget is on the call
    # stack (see app/capabilities.py:_active for the contextvar gate).
    from app.capabilities import install as _install_capability_hooks

    _install_capability_hooks()
    app.config["RENDERER_REGISTRY"] = renderers
    app.config["DEVICE_REGISTRY"] = devices
    app.config["DEVICE_DATA_ROOT"] = device_data_root
    app.config["DEVICE_SCHEMA_PATH"] = device_schema
    app.config["DISCOVERY_CACHE"] = discovery_cache
    app.config["PAGE_STORE"] = page_store
    app.config["SCHEDULE_STORE"] = schedule_store
    app.config["ROTATION_STORE"] = rotation_store
    app.config["USER_THEMES_STORE"] = user_themes_store
    app.config["COMMUNITY_THEMES_STORE"] = community_themes_store
    app.config["EVENT_LOG"] = event_log
    # Smart-sync per-device telemetry (issue #10). Persisted under
    # data/core/device_telemetry.json. Step 1 only tracks heartbeats +
    # computes predictions; the scheduler hook that acts on them is
    # step 2 of the same feature.
    from app.state.battery_history import BatteryHistory
    from app.state.device_telemetry import TelemetryStore

    app.config["DEVICE_TELEMETRY"] = TelemetryStore(data_root / "core" / "device_telemetry.json")
    # Per-device battery history: one row per heartbeat with a
    # battery_pct field. Drives the device_battery widget's
    # "days-to-empty" projection and the /devices/battery admin page.
    app.config["BATTERY_HISTORY"] = BatteryHistory(data_root / "core" / "battery_history.db")
    app.config["PREVIEW_CACHE"] = {}
    app.config["RENDERS_DIR"] = renders_dir
    app.config["DEVICE_STATUS"] = status_cache
    app.config["DATA_ROOT"] = data_root
    app.config["PLUGINS_DIR"] = plugins_dir

    # Audit-only community widget marketplace. Browse / install /
    # uninstall via Settings → Plugins → Browse. Index URL is a
    # settings switch so a user can fork the catalog or empty the
    # field to disable Browse entirely. See app/marketplace.py for
    # the trust model + sequencing notes.
    from app.marketplace import IndexUrlProvider as _IndexUrlProvider
    from app.marketplace import Marketplace as _Marketplace

    # Settings-backed callable. Falls back to the catalog default
    # when the user hasn't customised the URL (settings_store returns
    # only stored keys, defaults are resolved by the consumer per the
    # convention in app/push.py). The literal here MUST stay in sync
    # with the ``marketplace_index_url`` field in field_defs.py.
    _MARKETPLACE_INDEX_DEFAULT = (
        "https://raw.githubusercontent.com/dmellok/tesserae-widgets/main/widgets.json"
    )

    class _SettingsIndexUrlProvider(_IndexUrlProvider):
        def __call__(self) -> str:
            app_settings = settings.get_section("app") or {}
            url = app_settings.get("marketplace_index_url", _MARKETPLACE_INDEX_DEFAULT)
            return str(url) if isinstance(url, str) else ""

    app.config["MARKETPLACE"] = _Marketplace(
        # Install / uninstall writes go to the persistent dir so they
        # survive Docker / HA image upgrades.
        plugins_dir=user_plugins_dir,
        # Read-only image layer; checked on install so a marketplace
        # entry can't clash with a bundled widget folder name.
        bundled_plugins_dir=plugins_dir,
        # Plugin data dirs stay under data/plugins/<id>/ where they
        # already are; only the widget CODE moved to data/marketplace/.
        plugin_data_root=plugin_data_root,
        state_path=data_root / "core" / "marketplace.json",
        schema_path=plugin_schema,
        index_schema_path=REPO_ROOT / "schema" / "marketplace.schema.json",
        index_url_provider=_SettingsIndexUrlProvider(),
        # Community themes land alongside user-themes, next to the
        # community-themes store reading from the same path.
        themes_dir=community_themes_store.root,
    )
    # Self-update + backup live under Settings → System. Both are no-ops
    # under --dev (the reloader handles restarts there) and gated behind
    # admin auth.
    from app.updater import Updater as _Updater

    app.config["UPDATER"] = _Updater(REPO_ROOT, data_root)

    # Anonymous, opt-in telemetry, disabled by default; configure in
    # Settings → Server → App. Tests skip it entirely so no test run can
    # accidentally hit a real endpoint.
    from app.telemetry import Telemetry as _Telemetry

    is_watcher = _is_reloader_watcher(dev) and not testing
    if is_watcher:
        logger.info(
            "dev reloader parent: skipping MQTT/scheduler/telemetry init (child process owns those)"
        )
    if testing or is_watcher:
        telemetry = _Telemetry.disabled()
    else:
        telemetry = _Telemetry.from_settings(
            data_root=data_root,
            app_version=pkg_version,
            settings_app=settings.get_section("app"),
            event_log=event_log,
            is_debug=dev,
        )
        # ``is_docker`` lets the maintainer see what proportion of installs
        # run via the official Docker image vs install.sh / from source.
        # ``is_homeassistant`` flags the subset of those that run as the
        # companion HA Add-on (the add-on's run.sh exports
        # ``TESSERAE_HA_INGRESS=1``). Both are set by the deployment
        # wrapper; safe-to-trust as long as nobody's spoofing them on a
        # native install (and if they did, they're just mis-labelling
        # their own data point).
        telemetry.send(
            "app.started",
            {
                "is_docker": "true" if os.environ.get("TESSERAE_IN_DOCKER") == "1" else "false",
                "is_homeassistant": "true" if app.config.get("HA_INGRESS_MODE") else "false",
            },
        )
    app.config["TELEMETRY"] = telemetry

    # Long-running Chromium owned by a dedicated thread. The pool is
    # *created* unconditionally but only *started* when something calls
    # .render() on it, see ``_current_browser_pool`` in transport_wiring
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

    def _device_ids_for_page(page_id: str) -> list[str]:
        page = page_store.get(page_id)
        return list(page.device_ids) if page else []

    # v0.48 conditional schedules / rotations: the evaluator's HA cache
    # is refreshed by the scheduler on every tick via ``ha_get_states``.
    # ``ha_core`` is loaded as a plugin; reaching it via the registry
    # keeps the host loosely coupled (ha_core can be uninstalled and
    # the evaluator just degrades to "all conditions fail-open" with
    # an empty cache).
    def _ha_get_states() -> list[dict[str, Any]]:
        plugin = plugins.get("ha_core")
        if plugin is None or plugin.server_module is None:
            return []
        # ha_core.server reads its base_url / token via ``current_app``,
        # which only resolves inside a Flask request OR an active app
        # context. The scheduler tick runs in a background thread, so
        # without this push every refresh raises "Working outside of
        # application context", the closure swallows it, returns [],
        # and refresh_ha_states wipes the cache. That's the v0.51.x
        # "every condition fails open in prod but Test conditions
        # works fine" bug.
        try:
            with app.app_context():
                return list(plugin.server_module.get_states())
        except Exception:
            return []

    def _resolve_location() -> tuple[Any, Any]:
        app_section = settings.get_section("app") or {}
        return app_section.get("latitude"), app_section.get("longitude")

    from app.scheduler_conditions import ConditionEvaluator

    condition_evaluator = ConditionEvaluator(
        ha_get_states=_ha_get_states,
        timezone_provider=_resolve_timezone,
        location_provider=_resolve_location,
    )
    app.config["CONDITION_EVALUATOR"] = condition_evaluator

    scheduler = Scheduler(
        store=schedule_store,
        rotation_store=rotation_store,
        push_manager=lambda: app.config["PUSH_MANAGER"],
        event_log=event_log,
        timezone_provider=_resolve_timezone,
        page_exists=lambda page_id: page_store.get(page_id) is not None,
        device_ids_for_page=_device_ids_for_page,
        device_telemetry=app.config["DEVICE_TELEMETRY"],
        condition_evaluator=condition_evaluator,
    )
    app.config["SCHEDULER"] = scheduler
    if not testing and not is_watcher:
        scheduler.start()

    plugin_loader.register_routes(app, plugins)
    # Marketplace mounts at /plugins/browse, AFTER plugin_loader's
    # blueprint so the static path wins over plugin_loader's
    # /<plugin_id>/<asset> parametric route by Flask's specificity
    # rules. Both blueprints share the /plugins prefix; their names
    # ('plugins' vs 'marketplace') keep them distinct.
    from app import marketplace_routes

    marketplace_routes.register(app)
    app.register_blueprint(composer.bp)
    settings_routes.register(app)
    from app import condition_routes

    condition_routes.register(app)
    schedule_routes.register(app)
    from app import rotation_routes

    rotation_routes.register(app)
    send_routes.register(app)
    history_routes.register(app)
    device_battery_routes.register(app)
    events_routes.register(app)
    page_routes.register(app)
    onboarding.register(app)
    themes_routes.register(app)
    webhook_routes.register(app)
    trmnl_api.register(app)

    if not testing:
        auth.install_gate(app, settings)

    # Heartbeat enrichment, fleet shape + activity counters since the
    # previous heartbeat. The provider is a closure so app_factory keeps
    # ownership of the registries; telemetry.py stays free of any direct
    # dependency on them. Everything here is a count or a kind id; no
    # device names, page names, theme palettes, or user data.
    if telemetry.enabled:
        import time as _time

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
            # Fleet shape, current state, not deltas.
            instances = [d for d in devices.all() if d.kind_of is not None]
            kinds = sorted({str(d.kind_of) for d in instances if d.kind_of})
            n_pages = len(page_store.list())
            # Themes shape, number of user-saved themes signals
            # whether the builder is actually getting used. Telemetry
            # docstring listed this as a planned prop pre-v0.27; wire
            # it now that the UserThemeStore is in app.config.
            n_user_themes = len(user_themes_store.list_all())
            # Activity counters, events recorded since the previous
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
                "is_homeassistant": "true" if app.config.get("HA_INGRESS_MODE") else "false",
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
        the default 8765). On first request, also refresh HA discovery
        configs so HA's stored URLs pick up the new port."""
        from flask import request

        # Inside HA Ingress, ``request.host`` is the HA host's address
        # (e.g. ``homeassistant.local:8123``), that's HA's port, not
        # Tesserae's. Capturing it would emit panel URLs pointing at
        # ``http://<lan-ip>:8123/renders/…`` which 404s at HA. Fall
        # back to TESSERAE_HTTP_PORT / the default instead.
        if request.headers.get("X-Ingress-Path"):
            return
        # Same shape under a Public URL override (0.49.4): the middleware
        # rewrites ``HTTP_HOST`` to the public host:port the browser
        # sees, which is the reverse proxy's HTTPS port (e.g. ``:8443``),
        # not the actual Flask bind port (still ``:8765``). Capturing
        # ``8443`` here would emit LAN render URLs as
        # ``http://<lan-ip>:8443/renders/…`` which devices (pi / esp32)
        # can't fetch, NPM is HTTPS-only on that port and returns 400.
        # Skip the capture entirely; bind-port discovery falls back to
        # TESSERAE_HTTP_PORT / the 8765 default the device side expects.
        settings_store = app.config.get("SETTINGS_STORE")
        if settings_store is not None:
            public_url = str(
                (settings_store.get_section("app") or {}).get("public_url") or ""
            ).strip()
            if public_url:
                return
        previous = app.config.get("DETECTED_HTTP_PORT")
        port = request.host.split(":", 1)[1] if ":" in request.host else None
        if not port or not port.isdigit():
            return
        port_n = int(port)
        if port_n == previous:
            return
        app.config["DETECTED_HTTP_PORT"] = port_n
        # HA's stored entity configs include image_url / configuration_url
        # which embed the URL, re-publish if discovery is running.
        ha: HomeAssistantDiscovery | None = app.config.get("HA_DISCOVERY")
        if ha is not None:
            try:
                ha.refresh_entity_configs()
            except Exception:
                logger.exception("refreshing HA configs after port capture")

    @app.context_processor
    def _inject_nav_data() -> dict[str, Any]:
        """Make the list of admin-equipped plugins available to every
        template, the top-nav Plugins dropdown enumerates them. Also
        forwards the ``app`` settings section so per-toggle UI knobs
        (e.g. mobile-zoom lock) can render conditional snippets in the
        base template without each route having to pass them in.

        ``nav_batteries`` is the registered device instances that
        reported a battery_pct in their last heartbeat, sorted by
        worst-charge-first. Powers the topbar battery indicator + its
        per-device popover."""
        registry = app.config["PLUGIN_REGISTRY"]
        store = app.config.get("SETTINGS_STORE")
        app_settings: dict[str, Any] = {}
        if store is not None:
            try:
                app_settings = dict(store.get_section("app") or {})
            except Exception:
                app_settings = {}
        return {
            "nav_admin_plugins": sorted(
                (p for p in registry.plugins.values() if p.has_admin),
                key=lambda p: p.name.lower(),
            ),
            "nav_batteries": _collect_battery_status(app),
            "app_settings": app_settings,
        }

    @app.get("/")
    def index() -> Response:
        # First run (password set, but setup not finished) lands in the
        # wizard. Once onboarded, Send is the default destination, link
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
        # Thumbnail mode: ``?w=<width>`` returns a downscaled cached
        # variant. The admin's History / Events pages display each push
        # at ~160 px wide, but the source is a 1600x1200 panel render -
        # that decodes to ~7.7 MB per IMG element in Chromium's bitmap
        # cache. Leaving an admin tab open overnight with frequent
        # push events accumulated multi-GB tabs in the wild. Thumbnails
        # cap each IMG at ~0.4 MB decoded.
        thumb_w = request.args.get("w", type=int)
        if thumb_w and 16 <= thumb_w <= 800:
            response_or_none = _serve_render_thumbnail(renders_dir, filename, thumb_w)
            if response_or_none is not None:
                return response_or_none
        return send_from_directory(renders_dir, filename)

    # Device ID validation mirrors device_service so a 404 reflects an
    # unknown device rather than a path-traversal attempt.
    from app.device_service import DEVICE_ID_RE as _DEVICE_ID_RE

    @app.get("/preview/<device_id>.png")
    def preview_png(device_id: str) -> Response:
        """Stable per-device alias for the most-recent composition PNG.

        Unlike ``/renders/<digest>.png`` (where the URL changes every push
        because it's content-addressed), this URL stays the same as long
        as the device exists, drop it into Home Assistant's `generic`
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
            # No render yet for this device, return 404 rather than a
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
        # The URL is stable but the bytes change every push, make sure
        # HA / browsers refetch instead of serving a cached frame.
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp

    @app.get("/healthz")
    def healthz() -> tuple[str, int]:
        return "ok", 200

    return app
