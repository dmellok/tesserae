"""Plugin discovery, manifest validation, and route registration.

A plugin is a folder under ``plugins/`` containing at minimum a ``plugin.json``
manifest and a ``client.js``. Optional ``server.py`` provides server-side data
fetching and admin pages.

The loader runs once at app startup. Errors don't raise, they're collected on
the registry so the admin UI can surface them and the rest of the app keeps
working.

Differences from the v4 (inky-dash) loader:
  * No ``manifest_version`` field. Plugins declare ``tesserae_compat`` (e.g.
    ``"1.x"``) and the loader rejects anything that doesn't match the host
    major version.
  * The plugin id is the folder name. There is no ``id`` field in plugin.json.
  * Static ``cell_options[*].choices`` work without a server roundtrip;
    ``choices_from`` is the dynamic path.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
from flask import Blueprint, Flask, abort, render_template, send_from_directory
from werkzeug.wrappers import Response

from app.capabilities import Capabilities
from app.capabilities import parse as _parse_capabilities

logger = logging.getLogger(__name__)

# Bumped on breaking changes to the plugin contract.
HOST_MAJOR_VERSION: int = 1

# Files inside a plugin folder that may be served over HTTP.
_ALLOWED_ASSETS: frozenset[str] = frozenset({"client.js", "client.css"})
_ALLOWED_ASSET_PREFIXES: tuple[str, ...] = ("static/", "files/")

_COMPAT_RE = re.compile(r"^(\d+)\.(x|\d+)")


@dataclass(frozen=True)
class LoaderError:
    plugin_id: str
    path: Path
    message: str


@dataclass(frozen=True)
class Font:
    id: str
    name: str
    category: str  # "sans" | "serif" | "mono" | "display" | "handwriting" | ""
    weights: tuple[int, ...]
    files: dict[str, str]  # weight str → URL path (/plugins/<id>/files/<file>)
    plugin_id: str


@dataclass
class Plugin:
    id: str
    path: Path
    manifest: dict[str, Any]
    data_dir: Path
    server_module: ModuleType | None = None
    # Parsed capability declarations from the manifest's ``requires:``
    # block (or an undeclared snapshot when the field is absent).
    # See app/capabilities.py for the enforcement layer.
    capabilities: Capabilities | None = None

    @property
    def kind(self) -> str:
        kind = self.manifest["kind"]
        assert isinstance(kind, str)
        return kind

    @property
    def name(self) -> str:
        name = self.manifest["name"]
        assert isinstance(name, str)
        return name

    @property
    def supported_sizes(self) -> list[str]:
        sizes = self.manifest["supports"]["sizes"]
        assert isinstance(sizes, list)
        return [str(s) for s in sizes]

    @property
    def palette(self) -> str:
        """``"strict"`` (default) or ``"extended"``. Extended widgets
        opt into arbitrary CSS colours and rely on the renderer's
        dither pass to land them on the panel palette. See
        ``schema/plugin.schema.json``'s ``design.palette`` for the
        full contract."""
        design = self.manifest.get("design")
        if isinstance(design, dict):
            value = design.get("palette")
            if value in ("strict", "extended"):
                return str(value)
        return "strict"

    def cell_option_defaults(self) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        for opt in self.manifest.get("cell_options", []):
            if "default" in opt:
                defaults[str(opt["name"])] = opt["default"]
        return defaults

    @property
    def has_admin(self) -> bool:
        """True when the plugin's server.py exports a Flask ``blueprint()``
        , register_routes mounts it at /plugins/<id>/ as the plugin's
         admin page. The top-nav Plugins dropdown uses this to enumerate
         which plugins have a UI."""
        if self.server_module is None:
            return False
        return callable(getattr(self.server_module, "blueprint", None))


@dataclass
class PluginRegistry:
    plugins: dict[str, Plugin] = field(default_factory=dict)
    errors: list[LoaderError] = field(default_factory=list)
    fonts: dict[str, Font] = field(default_factory=dict)

    def get(self, plugin_id: str) -> Plugin | None:
        return self.plugins.get(plugin_id)

    def widgets(self) -> list[Plugin]:
        return [p for p in self.plugins.values() if p.kind == "widget"]

    def get_font(self, font_id: str) -> Font | None:
        return self.fonts.get(font_id)


def _load_schema(schema_path: Path) -> dict[str, Any]:
    raw = json.loads(schema_path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def _import_server_module(plugin_id: str, server_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"_tesserae_plugins.{plugin_id}.server", server_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load spec for {server_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _compat_ok(declared: str, host_major: int) -> bool:
    """Return True if ``declared`` (e.g. ``"1.x"`` or ``"1.2"``) is compatible
    with the host's major version. Same-major is the rule; minor / patch are
    advisory and never gate loading."""
    m = _COMPAT_RE.match(declared)
    if not m:
        return False
    major = int(m.group(1))
    return major == host_major


def discover(
    plugins_dir: Path,
    *,
    schema_path: Path,
    data_root: Path,
) -> PluginRegistry:
    """Walk ``plugins_dir`` and return a registry of validated plugins."""
    registry = PluginRegistry()
    if not plugins_dir.exists():
        return registry

    schema = _load_schema(schema_path)

    for child in sorted(plugins_dir.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue

        plugin_id = child.name

        manifest_path = child / "plugin.json"
        if not manifest_path.exists():
            registry.errors.append(LoaderError(plugin_id, child, "plugin.json missing"))
            continue

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            registry.errors.append(
                LoaderError(plugin_id, child, f"plugin.json invalid JSON: {err}")
            )
            continue

        if not isinstance(raw, dict):
            registry.errors.append(
                LoaderError(plugin_id, child, "plugin.json must be a JSON object")
            )
            continue
        manifest: dict[str, Any] = raw

        compat = manifest.get("tesserae_compat")
        if not isinstance(compat, str) or not _compat_ok(compat, HOST_MAJOR_VERSION):
            registry.errors.append(
                LoaderError(
                    plugin_id,
                    child,
                    f"tesserae_compat={compat!r} does not match host major {HOST_MAJOR_VERSION}",
                )
            )
            continue

        try:
            jsonschema.validate(manifest, schema)
        except jsonschema.ValidationError as err:
            field_path = ".".join(str(p) for p in err.absolute_path) or "<root>"
            registry.errors.append(
                LoaderError(plugin_id, child, f"manifest schema [{field_path}]: {err.message}")
            )
            continue

        if plugin_id in registry.plugins:
            registry.errors.append(LoaderError(plugin_id, child, "duplicate plugin id"))
            continue

        server_path = child / "server.py"
        server_module: ModuleType | None = None
        if server_path.exists():
            try:
                server_module = _import_server_module(plugin_id, server_path)
            except Exception as err:
                registry.errors.append(
                    LoaderError(plugin_id, child, f"server.py import failed: {err}")
                )
                continue

        data_dir = data_root / plugin_id
        data_dir.mkdir(parents=True, exist_ok=True)

        plugin = Plugin(
            id=plugin_id,
            path=child,
            manifest=manifest,
            data_dir=data_dir,
            server_module=server_module,
            capabilities=_parse_capabilities(plugin_id, manifest.get("requires")),
        )
        registry.plugins[plugin_id] = plugin

        if plugin.kind == "font":
            for raw_font in manifest.get("fonts", []):
                files_map = {
                    str(weight): f"/plugins/{plugin_id}/{path}"
                    for weight, path in raw_font["files"].items()
                }
                font = Font(
                    id=str(raw_font["id"]),
                    name=str(raw_font["name"]),
                    category=str(raw_font.get("category", "")),
                    weights=tuple(int(w) for w in raw_font["weights"]),
                    files=files_map,
                    plugin_id=plugin_id,
                )
                if font.id in registry.fonts:
                    registry.errors.append(
                        LoaderError(plugin_id, child, f"duplicate font id {font.id!r}")
                    )
                    continue
                registry.fonts[font.id] = font

        logger.info("Loaded plugin %s (kind=%s)", plugin_id, manifest["kind"])

    return registry


def register_routes(app: Flask, registry: PluginRegistry) -> None:
    """Register per-plugin static asset routes and any plugin-provided blueprints."""
    bp = Blueprint("plugins", __name__)

    @bp.get("/")
    def plugins_index() -> str:
        """Top-level page listing every loaded plugin + loader errors."""
        return render_template(
            "plugins_index.html",
            plugins=sorted(registry.plugins.values(), key=lambda p: (p.kind, p.name.lower())),
            errors=registry.errors,
        )

    @bp.get("/<plugin_id>/<path:asset>")
    def plugin_asset(plugin_id: str, asset: str) -> Response:
        plugin = registry.plugins.get(plugin_id)
        if plugin is None:
            abort(404)
        if asset not in _ALLOWED_ASSETS and not asset.startswith(_ALLOWED_ASSET_PREFIXES):
            abort(404)
        return send_from_directory(plugin.path, asset)

    app.register_blueprint(bp, url_prefix="/plugins")

    for plugin in registry.plugins.values():
        if plugin.server_module is None:
            continue
        blueprint_fn: Callable[[], Blueprint] | None = getattr(
            plugin.server_module, "blueprint", None
        )
        if blueprint_fn is None:
            continue
        try:
            plugin_bp = blueprint_fn()
        except Exception as err:
            logger.error("Plugin %s blueprint() raised: %s", plugin.id, err)
            registry.errors.append(
                LoaderError(plugin.id, plugin.path, f"blueprint() raised: {err}")
            )
            continue
        app.register_blueprint(plugin_bp, url_prefix=f"/plugins/{plugin.id}")
