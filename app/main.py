"""Flask app factory. Wires the plugin registry, composer, and per-plugin
asset routes; later milestones bolt the renderer, transport, scheduler, and
admin pages onto the same factory.

Usage:
    from app.main import create_app
    app = create_app()
    app.run(host="0.0.0.0", port=8000)

For tests, ``create_app(testing=True)`` swaps in a tmp data root, enables the
/_test/render route, and skips long-running background threads.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask

from app import composer, plugin_loader
from app.state.page_store import PageStore

logger = logging.getLogger(__name__)

REPO_ROOT: Path = Path(__file__).resolve().parent.parent


def create_app(
    *,
    testing: bool = False,
    data_root: Path | None = None,
    plugins_dir: Path | None = None,
) -> Flask:
    """Construct the Flask app with the plugin registry attached."""
    app = Flask(
        __name__,
        template_folder=str(REPO_ROOT / "templates"),
        static_folder=str(REPO_ROOT / "static"),
        static_url_path="/static",
    )
    app.testing = testing

    data_root = data_root or REPO_ROOT / "data"
    plugins_dir = plugins_dir or REPO_ROOT / "plugins"
    schema_path = REPO_ROOT / "schema" / "plugin.schema.json"

    plugin_data_root = data_root / "plugins"
    plugin_data_root.mkdir(parents=True, exist_ok=True)

    registry = plugin_loader.discover(
        plugins_dir,
        schema_path=schema_path,
        data_root=plugin_data_root,
    )
    for err in registry.errors:
        logger.warning("plugin loader: %s — %s", err.plugin_id, err.message)

    app.config["PLUGIN_REGISTRY"] = registry
    app.config["PAGE_STORE"] = PageStore(data_root / "core" / "pages.json")
    app.config["PREVIEW_CACHE"] = {}

    plugin_loader.register_routes(app, registry)
    app.register_blueprint(composer.bp)

    return app


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s"
    )
    create_app().run(host="0.0.0.0", port=8000, debug=True)
