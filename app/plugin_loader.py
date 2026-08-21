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
from flask import Blueprint, Flask, abort, current_app, render_template, send_from_directory
from werkzeug.wrappers import Response

from app.capabilities import Capabilities, set_delegate_resolver
from app.capabilities import parse as _parse_capabilities

logger = logging.getLogger(__name__)

# Bumped on breaking changes to the plugin contract.
HOST_MAJOR_VERSION: int = 1

# Files inside a plugin folder that may be served over HTTP.
_ALLOWED_ASSETS: frozenset[str] = frozenset({"client.js", "client.css"})
_ALLOWED_ASSET_PREFIXES: tuple[str, ...] = ("static/", "files/")

_COMPAT_RE = re.compile(r"^(\d+)\.(x|\d+)")


# A folder skipped because an earlier scan root already claimed its id.
# Shared with the Widgets page, which offers to delete the shadowed copy,
# and matched exactly there so nothing else can be routed into a delete.
DUPLICATE_ID_MESSAGE = "duplicate plugin id"

# Scan roots under the data root that Tesserae itself writes, and can
# therefore offer to clean up. Bundled plugins ship with the image and are
# never removable from the UI.
REMOVABLE_ROOTS: tuple[str, ...] = ("authored", "marketplace")


@dataclass(frozen=True)
class LoaderError:
    plugin_id: str
    path: Path
    message: str


def shadowed_origin(path: Path, data_root: Path) -> str | None:
    """Which managed scan root ``path`` is a direct child of, if any.

    ``None`` for a bundled plugin, or for anything outside the data root,
    which is what keeps the Widgets page's delete action away from folders
    that ship with the image."""
    for name in REMOVABLE_ROOTS:
        try:
            if path.resolve().parent == (data_root / name).resolve():
                return name
        except OSError:
            continue
    return None


def _describe_error(err: LoaderError, registry: PluginRegistry, data_root: Path) -> dict[str, Any]:
    """A loader error plus what the Widgets page needs to act on it.

    A duplicate id is the one error a user can resolve without editing
    files: some other folder already claimed the id, this copy never
    loads, and deleting it is the whole fix. Anything else stays
    informational."""
    active = registry.plugins.get(err.plugin_id)
    origin = shadowed_origin(err.path, data_root) if err.message == DUPLICATE_ID_MESSAGE else None
    return {
        "plugin_id": err.plugin_id,
        "path": str(err.path),
        "message": err.message,
        "origin": origin,
        "active_path": str(active.path) if active is not None else None,
        "removable": origin is not None and active is not None,
    }


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

    @property
    def on_change_updates(self) -> list[dict[str, str]]:
        """Validated ``updates.on_change`` declarations for this widget.

        The manifest schema owns the public shape.  Keep this accessor
        defensive because tests and internal callers can construct ``Plugin``
        instances without going through discovery.
        """
        updates = self.manifest.get("updates")
        raw = updates.get("on_change") if isinstance(updates, dict) else None
        if not isinstance(raw, list):
            return []
        out: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("source"), str):
                continue
            spec = {"source": str(item["source"])}
            selector = item.get("selector_option")
            if isinstance(selector, str) and selector:
                spec["selector_option"] = selector
            out.append(spec)
        return out

    @property
    def on_schedule_updates(self) -> list[dict[str, str]]:
        """Validated ``updates.on_schedule`` declarations for this widget."""
        updates = self.manifest.get("updates")
        raw = updates.get("on_schedule") if isinstance(updates, dict) else None
        if not isinstance(raw, list):
            return []
        out: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict) or item.get("kind") != "daily":
                continue
            spec = {"kind": "daily"}
            suggested_at = item.get("suggested_at")
            if isinstance(suggested_at, str):
                spec["suggested_at"] = suggested_at
            out.append(spec)
        return out

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
    additional_plugins_dirs: list[Path] | None = None,
) -> PluginRegistry:
    """Walk ``plugins_dir`` and ``additional_plugins_dirs`` (if any) and
    return a registry of validated plugins.

    ``additional_plugins_dirs`` were added in 0.42.2 so the marketplace
    can install widgets to a persistent location outside the Docker
    image layer; in HA Add-on / Docker installs the bundled plugins
    sit at ``/app/plugins/`` (immutable, shipped with each image), and
    user-installed marketplace widgets sit at ``<data_root>/plugins/``
    (persistent volume). Both dirs are walked and merged. Bundled wins
    on duplicate ids, the user's marketplace install is rejected with
    an error so the user can resolve it (the bundled adoption of a
    previously-marketplace widget is the only way this happens in
    practice).
    """
    registry = PluginRegistry()
    schema = _load_schema(schema_path)

    # Bundled dir first so its ids are claimed; marketplace dir is
    # walked second and any duplicate ids get an error.
    dirs = [plugins_dir]
    if additional_plugins_dirs:
        dirs.extend(additional_plugins_dirs)

    for source_dir in dirs:
        if not source_dir.exists():
            continue
        _scan_plugin_dir(source_dir, registry, schema=schema, data_root=data_root)

    # Resolve ``plugin:<id>`` delegations (#244) against the finished
    # registry. Late-bound on purpose: a delegate can load after the
    # widget that names it, so binding at parse time would depend on
    # directory order.
    def _delegate(plugin_id: str) -> Capabilities | None:
        target = registry.get(plugin_id)
        return target.capabilities if target is not None else None

    set_delegate_resolver(_delegate)
    return registry


def _scan_plugin_dir(
    plugins_dir: Path,
    registry: PluginRegistry,
    *,
    schema: dict[str, Any],
    data_root: Path,
) -> None:
    """Walk one plugin source directory and append everything it
    contains to ``registry``. Split out of ``discover`` so the loader
    can walk multiple sources (bundled + user marketplace) without
    duplicating the per-folder validation loop."""
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

        updates = manifest.get("updates")
        raw_on_change = updates.get("on_change") if isinstance(updates, dict) else None
        on_change = raw_on_change if isinstance(raw_on_change, list) else []
        option_names = {
            str(option.get("name"))
            for option in manifest.get("cell_options", [])
            if isinstance(option, dict) and option.get("name")
        }
        unknown_selector = next(
            (
                str(spec["selector_option"])
                for spec in on_change
                if isinstance(spec, dict)
                and spec.get("selector_option")
                and str(spec["selector_option"]) not in option_names
            ),
            None,
        )
        if unknown_selector is not None:
            registry.errors.append(
                LoaderError(
                    plugin_id,
                    child,
                    "manifest updates.on_change selector_option "
                    f"{unknown_selector!r} is not declared in cell_options",
                )
            )
            continue

        if plugin_id in registry.plugins:
            registry.errors.append(LoaderError(plugin_id, child, DUPLICATE_ID_MESSAGE))
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


def register_routes(app: Flask, registry: PluginRegistry) -> None:
    """Register per-plugin static asset routes and any plugin-provided blueprints."""
    bp = Blueprint("plugins", __name__)

    # The route closures capture ``registry`` at startup, but an in-process
    # reload (the MCP widget-push path) swaps a fresh registry into
    # ``app.config["PLUGIN_REGISTRY"]``. Read that per request (falling back to
    # the closed-over one if the key is ever absent) so a newly-pushed widget's
    # assets are found without a restart, matching how composer.py /
    # condition_routes.py already resolve the registry.
    def _live_registry() -> PluginRegistry:
        live: PluginRegistry = current_app.config.get("PLUGIN_REGISTRY", registry)
        return live

    @bp.get("/")
    def plugins_index() -> str:
        """Top-level page listing every loaded plugin + loader errors."""
        reg = _live_registry()
        data_root = Path(current_app.config["DATA_ROOT"])
        return render_template(
            "plugins_index.html",
            plugins=sorted(reg.plugins.values(), key=lambda p: (p.kind, p.name.lower())),
            errors=[_describe_error(err, reg, data_root) for err in reg.errors],
        )

    @bp.get("/<plugin_id>/<path:asset>")
    def plugin_asset(plugin_id: str, asset: str) -> Response:
        plugin = _live_registry().plugins.get(plugin_id)
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
