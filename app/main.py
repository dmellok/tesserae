"""Flask app factory. Wires the plugin registry, renderer registry, composer,
per-plugin asset routes, and the artifact static route; later milestones bolt
the auth gate, scheduler, and admin pages onto the same factory.

Usage:
    from app.main import create_app
    app = create_app()
    app.run(host="0.0.0.0", port=8000)

For tests, ``create_app(testing=True)`` swaps in a tmp data root, enables the
/_test/render route, and skips long-running background threads / broker
connections.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, abort, send_from_directory
from werkzeug.wrappers import Response

from app import composer, plugin_loader, renderer_loader
from app.state.page_store import PageStore

logger = logging.getLogger(__name__)

REPO_ROOT: Path = Path(__file__).resolve().parent.parent


def create_app(
    *,
    testing: bool = False,
    data_root: Path | None = None,
    plugins_dir: Path | None = None,
    renderers_dir: Path | None = None,
) -> Flask:
    """Construct the Flask app with plugin + renderer registries attached."""
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

    app.config["PLUGIN_REGISTRY"] = plugins
    app.config["RENDERER_REGISTRY"] = renderers
    app.config["PAGE_STORE"] = PageStore(data_root / "core" / "pages.json")
    app.config["PREVIEW_CACHE"] = {}
    app.config["RENDERS_DIR"] = renders_dir

    plugin_loader.register_routes(app, plugins)
    app.register_blueprint(composer.bp)

    @app.get("/renders/<path:filename>")
    def renders(filename: str) -> Response:
        # The renders dir holds content-addressed artifacts each renderer
        # writes — Pi listeners and ESP32 clients fetch them here on every
        # MQTT publish. Path traversal is guarded by send_from_directory.
        if "/" in filename or filename.startswith("."):
            abort(404)
        return send_from_directory(renders_dir, filename)

    return app


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s"
    )
    create_app().run(host="0.0.0.0", port=8000, debug=True)
