"""Flask app factory.

Wires the plugin + renderer registries, composer, settings store, auth gate,
admin routes, MQTT transport (rebuildable on broker setting changes), and
push manager.

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
from pathlib import Path

from flask import Flask, abort, send_from_directory
from werkzeug.wrappers import Response

from app import auth, composer, plugin_loader, renderer_loader, settings_routes
from app.push import PushManager
from app.state.page_store import PageStore
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
    plugin_schema = REPO_ROOT / "schema" / "plugin.schema.json"
    renderer_schema = REPO_ROOT / "schema" / "renderer.schema.json"

    plugin_data_root = data_root / "plugins"
    plugin_data_root.mkdir(parents=True, exist_ok=True)
    renderer_data_root = data_root / "renderers"
    renderer_data_root.mkdir(parents=True, exist_ok=True)
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

    page_store = PageStore(data_root / "core" / "pages.json")

    app.config["PLUGIN_REGISTRY"] = plugins
    app.config["RENDERER_REGISTRY"] = renderers
    app.config["PAGE_STORE"] = page_store
    app.config["PREVIEW_CACHE"] = {}
    app.config["RENDERS_DIR"] = renders_dir

    # Transport + push manager are (re)built from current broker settings.
    # Holding the rebuilder in app.config lets settings_routes call it on a
    # broker change without restarting the process.
    def rebuild_transport() -> None:
        _rebuild_transport(app, settings, renderers, page_store, renders_dir, testing=testing)

    app.config["REBUILD_TRANSPORT"] = rebuild_transport
    rebuild_transport()

    plugin_loader.register_routes(app, plugins)
    app.register_blueprint(composer.bp)
    settings_routes.register(app)

    # The auth gate is installed last so blueprints register their endpoints
    # first (the gate's redirect targets need url_for to resolve).
    if not testing:
        auth.install_gate(app, settings)

    @app.get("/renders/<path:filename>")
    def renders(filename: str) -> Response:
        # Content-addressed artifacts each renderer writes. Pi listeners and
        # ESP32 clients fetch them here on every MQTT publish. The auth gate
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
    page_store: PageStore,
    renders_dir: Path,
    *,
    testing: bool,
) -> None:
    """Tear down any existing transport, construct a fresh one from current
    broker settings, and rewire the PushManager. Safe to call repeatedly.

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

    factory = _noop_client_factory if testing else None
    config = BrokerConfig(
        host=host or "localhost",
        port=int(broker_raw.get("port") or 1883),
        username=broker_raw.get("username") or None,
        password=broker_raw.get("password_secret") or None,
        keepalive=int(broker_raw.get("keepalive") or 60),
        client_id=str(broker_raw.get("client_id") or "tesserae"),
    )
    transport = MqttTransport(config, client_factory=factory)
    if host:
        # Only attempt to connect when the user has actually set a host.
        try:
            transport.connect()
        except Exception:
            logger.exception("MQTT connect failed (host=%s); leaving transport offline", host)

    app_section = settings.get_section("app")
    base_url = str(app_section.get("base_url") or "http://127.0.0.1:8000")

    app.config["MQTT_TRANSPORT"] = transport
    app.config["PUSH_MANAGER"] = PushManager(
        registry=renderers,
        page_store=page_store,
        transport=transport,
        renders_dir=renders_dir,
        base_url=base_url,
    )


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


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s"
    )
    create_app().run(host="0.0.0.0", port=8000, debug=True)
