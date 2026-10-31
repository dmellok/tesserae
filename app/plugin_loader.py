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
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    flash,
    has_app_context,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
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

# Matches schema/plugin.schema.json's `locales` item pattern exactly.
# This is the point that turns the value into a
# filename, so it validates again right before that happens instead
# of relying on a caller having gone through jsonschema first.
_LOCALE_TAG_RE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?\Z")


# A folder skipped because an earlier scan root already claimed its id.
# Shared with the Widgets page, which offers to delete the shadowed copy,
# and matched exactly there so nothing else can be routed into a delete.
DUPLICATE_ID_MESSAGE = "duplicate plugin id"

# Scan roots under the data root that Tesserae itself writes, and can
# therefore offer to clean up. Bundled plugins ship with the image and are
# never removable from the UI.
REMOVABLE_ROOTS: tuple[str, ...] = ("authored", "marketplace")


# Kinds an operator can switch off. Fonts, themes, renderers and devices are
# load-bearing for every dashboard, so they stay on.
DISABLEABLE_KINDS: frozenset[str] = frozenset({"widget", "data"})

# Data plugins the app itself looks up by id (touch-button and condition HA
# calls, Rooms, the REST API, OpenDisplay), so switching one off would break
# features well beyond its widgets. They stay on.
ALWAYS_ON_IDS: frozenset[str] = frozenset({"ha_core", "weather_core", "calendar_core"})


def can_disable(plugin: Plugin) -> bool:
    return plugin.kind in DISABLEABLE_KINDS and plugin.id not in ALWAYS_ON_IDS


# Settings section holding the switched-off ids as a flat ``{plugin_id: True}``
# map (the shape ``renderers_enabled`` uses). Absent means every plugin is on,
# so an existing install is unchanged.
DISABLED_SECTION = "plugins_disabled"


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
    # locale -> {key: translated text}, empty for a widget that hasn't
    # opted into the locales contract, so strings_for() always degrades
    # to the widget's own hardcoded text.
    strings: dict[str, dict[str, str]] = field(default_factory=dict)

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

    def strings_for(self, locale: str) -> dict[str, str]:
        """Resolved ``{key: text}`` map for ``locale``, or ``{}`` for a
        widget that hasn't declared any (the common case today — its
        ``client.js`` keeps its own hardcoded text and ``ctx.t()``
        degrades to returning the key/fallback text unchanged).

        Falls back in three steps so a caller doesn't need to know
        which locales a widget actually ships: exact tag (``fr-CA``),
        then base language (``fr``), then ``"en"`` — every widget's
        hardcoded fallback text is English, so that's the one locale
        ``strings_for`` can always assume exists rather than guess at
        a per-widget default. A locale a widget genuinely has no
        strings for still renders — in English — rather than blank."""
        if not self.strings:
            return {}
        if locale in self.strings:
            return self.strings[locale]
        base = locale.split("-", 1)[0]
        if base in self.strings:
            return self.strings[base]
        return self.strings.get("en", {})

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
    def has_blueprint(self) -> bool:
        """True when the plugin's server.py exports a Flask ``blueprint()``,
        which register_routes mounts under /plugins/<id>/."""
        if self.server_module is None:
            return False
        return callable(getattr(self.server_module, "blueprint", None))

    @property
    def has_admin(self) -> bool:
        """True when the plugin serves a page at /plugins/<id>/. The top-nav
        Admin pages menu and the Installed widgets list link there. A
        blueprint alone isn't enough: trmnl_proxy mounts only ``/image``, so
        linking it gave a 404 (tesserae-widget-trmnl-proxy#1)."""
        if not self.has_blueprint:
            return False
        if not has_app_context():
            return True
        served = current_app.config.get("PLUGIN_ADMIN_IDS")
        # Unset only before register_routes has run; keep the old answer.
        return True if served is None else self.id in served


@dataclass
class PluginRegistry:
    plugins: dict[str, Plugin] = field(default_factory=dict)
    errors: list[LoaderError] = field(default_factory=list)
    fonts: dict[str, Font] = field(default_factory=dict)
    # Loaded but switched off. Kept out of ``plugins`` so every lookup, picker
    # and background job skips them without knowing the feature exists; the
    # Widgets page and the cell placeholder read this map instead.
    disabled: dict[str, Plugin] = field(default_factory=dict)

    def get(self, plugin_id: str) -> Plugin | None:
        return self.plugins.get(plugin_id)

    def is_disabled(self, plugin_id: str) -> bool:
        return plugin_id in self.disabled

    def set_disabled(self, plugin_id: str, disabled: bool) -> bool:
        """Move one plugin between ``plugins`` and ``disabled`` in place, so
        the toggle takes effect on the next request with no re-import and no
        restart. False when there is nothing to do (unknown id, a kind that
        cannot be disabled, or already in that state)."""
        # Swap in fresh dicts rather than mutating, so a request thread
        # iterating ``plugins.values()`` meanwhile never sees it change size.
        if disabled:
            plugin = self.plugins.get(plugin_id)
            if plugin is None or not can_disable(plugin):
                return False
            self.disabled = {**self.disabled, plugin_id: plugin}
            self.plugins = {k: v for k, v in self.plugins.items() if k != plugin_id}
            return True
        plugin = self.disabled.get(plugin_id)
        if plugin is None:
            return False
        self.plugins = {**self.plugins, plugin_id: plugin}
        self.disabled = {k: v for k, v in self.disabled.items() if k != plugin_id}
        return True

    def apply_disabled(self, plugin_ids: Collection[str]) -> None:
        """Switch off every loaded, disableable plugin named in
        ``plugin_ids``; ids that are unknown or of another kind are ignored."""
        for plugin_id in plugin_ids:
            self.set_disabled(plugin_id, True)

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


def host_version() -> str:
    """The running server's version, as the footer shows it: the app's
    ``APP_VERSION`` when there is an app, else the checkout's pyproject.toml
    (a source install's package metadata goes stale between bumps), else
    the installed package's metadata; "" when it cannot be told."""
    try:
        from flask import current_app

        got = str(current_app.config.get("APP_VERSION") or "")
        if got:
            return got
    except Exception:
        pass
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        import tomllib

        return str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"])
    except (OSError, KeyError, ValueError, TypeError):
        pass
    try:
        from importlib.metadata import version

        return version("tesserae")
    except Exception:
        return ""


def version_tuple(text: str) -> tuple[int, ...]:
    """``"0.463.1"`` -> ``(0, 463, 1)``; anything unreadable is ``()``."""
    out: list[int] = []
    for part in str(text).strip().lstrip("v").split("."):
        m = re.match(r"\d+", part)
        if not m:
            break
        out.append(int(m.group(0)))
    return tuple(out)


def too_new_for_host(needed: Any, host: str | None = None) -> str | None:
    """A sentence when a plugin's ``tesserae_min`` is above the running
    server's version, else None. An unreadable or missing host version never
    blocks, since a developer checkout may carry none."""
    if not isinstance(needed, str) or not needed.strip():
        return None
    have = host if host is not None else host_version()
    need_t, have_t = version_tuple(needed), version_tuple(have)
    if not need_t or not have_t:
        return None
    if have_t < need_t:
        return f"needs Tesserae {needed.strip()} or later; this server is {have.strip()}"
    return None


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
    disabled_ids: Collection[str] = (),
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

    ``disabled_ids`` are the plugins the operator switched off. They are
    fully loaded (so enabling one later needs no re-import) but parked in
    ``registry.disabled`` rather than ``registry.plugins``.
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
        target = registry.get(plugin_id) or registry.disabled.get(plugin_id)
        return target.capabilities if target is not None else None

    set_delegate_resolver(_delegate)
    registry.apply_disabled(disabled_ids)
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

        too_new = too_new_for_host(manifest.get("tesserae_min"))
        if too_new:
            registry.errors.append(LoaderError(plugin_id, child, too_new))
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

        for raw_locale in manifest.get("locales", []):
            locale = str(raw_locale)
            if not _LOCALE_TAG_RE.match(locale):
                registry.errors.append(
                    LoaderError(
                        plugin_id, child, f"locales entry {locale!r} is not a valid locale tag"
                    )
                )
                continue
            strings_path = child / "strings" / f"{locale}.json"
            try:
                raw_strings = json.loads(strings_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                registry.errors.append(
                    LoaderError(
                        plugin_id,
                        child,
                        f"locales declares {locale!r} but strings/{locale}.json is missing",
                    )
                )
                continue
            except (json.JSONDecodeError, OSError) as err:
                registry.errors.append(
                    LoaderError(plugin_id, child, f"strings/{locale}.json invalid: {err}")
                )
                continue
            if not isinstance(raw_strings, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in raw_strings.items()
            ):
                registry.errors.append(
                    LoaderError(
                        plugin_id,
                        child,
                        f"strings/{locale}.json must be a flat {{string: string}} map",
                    )
                )
                continue
            plugin.strings[locale] = {str(k): str(v) for k, v in raw_strings.items()}

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


def _plugin_source(plugin: Plugin, data_root: Path) -> str:
    """Where a loaded plugin came from, for the Widgets page's filter:
    "catalog" (installed from the catalog into the data volume), "authored"
    (pushed by an authoring client, also under the data volume) or
    "bundled" (shipped with Tesserae)."""
    try:
        rel = plugin.path.resolve().relative_to(data_root.resolve())
    except (OSError, ValueError):
        return "bundled"
    return "catalog" if rel.parts[:1] == ("marketplace",) else "authored"


def disabled_ids(store: Any) -> set[str]:
    """Plugin ids switched off in the ``plugins_disabled`` settings section.
    A missing store or section means none, which is every existing install."""
    if store is None:
        return set()
    try:
        raw = store.get_section(DISABLED_SECTION)
    except Exception:
        return set()
    return {str(pid) for pid, off in raw.items() if off is True}


def _element_uses(el: Any, plugin_id: str) -> bool:
    """A canvas element that places ``plugin_id`` or reads its data: a widget
    element, a data element's source, or one of a code element's sources."""
    if el.kind == "widget":
        return bool(el.widget == plugin_id)
    if el.kind == "data":
        return bool(getattr(el, "source", "") == plugin_id)
    if el.kind == "code":
        if getattr(el, "source", "") == plugin_id:
            return True
        return any(
            getattr(src, "key", "") == plugin_id for src in getattr(el, "sources", None) or []
        )
    return False


def plugin_usage(plugin_id: str, pages: Any) -> list[str]:
    """Names of the dashboards that place ``plugin_id`` or read its data (grid
    cells and canvas widget, data and code elements), for the "in use" warning
    before a disable."""
    names: list[str] = []
    for page in pages:
        used = any(cell.plugin == plugin_id for cell in page.cells)
        canvas = getattr(page, "canvas", None)
        if not used and canvas is not None:
            used = any(_element_uses(el, plugin_id) for el in canvas.els)
        if used:
            names.append(str(page.name))
    return names


def _server_sources(registry: PluginRegistry) -> dict[str, str]:
    """Each enabled plugin's ``server.py`` text, read once per page view."""
    out: dict[str, str] = {}
    for other in registry.plugins.values():
        try:
            out[other.id] = (other.path / "server.py").read_text(encoding="utf-8")
        except OSError:
            continue
    return out


def plugin_dependents(
    plugin_id: str, registry: PluginRegistry, sources: dict[str, str] | None = None
) -> list[str]:
    """Names of enabled plugins whose server code looks this one up by id, so
    switching a data plugin off can warn what quietly stops working. A plain
    text match on the quoted id is crude but errs toward warning. Pass
    ``sources`` from :func:`_server_sources` to check many ids in one pass."""
    if sources is None:
        sources = _server_sources(registry)
    needles = (f'"{plugin_id}"', f"'{plugin_id}'")
    out: list[str] = []
    for other in registry.plugins.values():
        if other.id == plugin_id:
            continue
        source = sources.get(other.id)
        if source is not None and any(n in source for n in needles):
            out.append(other.name)
    return sorted(out, key=str.lower)


def install_disabled_gate(app: Flask, registry: PluginRegistry) -> None:
    """A disabled plugin's admin page and routes answer 404 with a friendly
    page instead of its own handler. Browser navigations get the page;
    scripted and asset requests get a plain 404. Flask runs ``before_request``
    hooks in registration order, so install this after the login gate."""

    @app.before_request
    def _gate_disabled_plugin() -> Response | tuple[str, int] | None:
        if not request.path.startswith("/plugins/"):
            return None
        plugin_id = request.path.split("/", 3)[2]
        live: PluginRegistry = current_app.config.get("PLUGIN_REGISTRY", registry)
        plugin = live.disabled.get(plugin_id)
        if plugin is None:
            return None
        if "text/html" in request.headers.get("Accept", ""):
            return render_template("plugin_disabled.html", plugin=plugin), 404
        return Response("plugin disabled", status=404, mimetype="text/plain")


def register_routes(app: Flask, registry: PluginRegistry, *, gate: bool = True) -> None:
    """Register per-plugin static asset routes and any plugin-provided blueprints.

    ``gate=False`` leaves the disabled-plugin gate for the caller to install
    with :func:`install_disabled_gate`, after the login gate, so a signed-out
    visitor is sent to log in rather than shown the disabled page."""
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
        everything = [*reg.plugins.values(), *reg.disabled.values()]
        pages_store = current_app.config.get("PAGE_STORE")
        pages = pages_store.list() if pages_store is not None else []
        usage: dict[str, dict[str, list[str]]] = {}
        sources = _server_sources(reg)
        for p in reg.plugins.values():
            if not can_disable(p):
                continue
            dashboards = plugin_usage(p.id, pages)
            dependents = plugin_dependents(p.id, reg, sources) if p.kind == "data" else []
            if dashboards or dependents:
                usage[p.id] = {"dashboards": dashboards, "dependents": dependents}
        return render_template(
            "plugins_index.html",
            plugins=sorted(reg.plugins.values(), key=lambda p: (p.kind, p.name.lower())),
            disabled_plugins=sorted(reg.disabled.values(), key=lambda p: p.name.lower()),
            disableable_kinds=DISABLEABLE_KINDS,
            always_on_ids=ALWAYS_ON_IDS,
            plugin_usage=usage,
            errors=[_describe_error(err, reg, data_root) for err in reg.errors],
            plugin_sources={p.id: _plugin_source(p, data_root) for p in everything},
        )

    def _toggle(disable: bool) -> Response | tuple[Response, int]:
        plugin_id = (request.form.get("plugin_id") or "").strip()
        reg = _live_registry()
        wants_json = request.accept_mimetypes.best == "application/json"

        def done(ok: bool, message: str, status: int = 200) -> Response | tuple[Response, int]:
            if wants_json:
                return jsonify({"ok": ok, "message": message}), status
            flash(message, "ok" if ok else "error")
            return redirect(url_for("plugins.plugins_index"))

        known = reg.plugins.get(plugin_id) or reg.disabled.get(plugin_id)
        if known is None:
            return done(False, f"No plugin {plugin_id!r} is loaded.", 404)
        if known.kind not in DISABLEABLE_KINDS:
            return done(
                False,
                f"{known.name} is a {known.kind} plugin; only widgets and data plugins can be disabled.",
                400,
            )
        if disable and known.id in ALWAYS_ON_IDS:
            return done(
                False,
                f"{known.name} is used by Tesserae itself, so it stays on.",
                400,
            )
        store = current_app.config.get("SETTINGS_STORE")
        if store is None:
            return done(False, "Settings are unavailable.", 503)
        store.patch_section(DISABLED_SECTION, {plugin_id: disable})
        reg.set_disabled(plugin_id, disable)
        if disable:
            return done(True, f"{known.name} is disabled. Enable it again from this page.")
        return done(True, f"{known.name} is enabled.")

    @bp.post("/disable")
    def disable_plugin() -> Response | tuple[Response, int]:
        """Switch a widget or data plugin off without removing it."""
        return _toggle(True)

    @bp.post("/enable")
    def enable_plugin() -> Response | tuple[Response, int]:
        return _toggle(False)

    @bp.get("/<plugin_id>/<path:asset>")
    def plugin_asset(plugin_id: str, asset: str) -> Response:
        plugin = _live_registry().plugins.get(plugin_id)
        if plugin is None:
            abort(404)
        if asset not in _ALLOWED_ASSETS and not asset.startswith(_ALLOWED_ASSET_PREFIXES):
            abort(404)
        # Bundled fonts use this endpoint, not Flask's /static endpoint.
        # Give their unversioned CSS URLs the same one-day cache lifetime
        # as unversioned icon fonts; revalidation on every navigation made
        # the entire UI repeatedly fall back and then change typeface.
        max_age = None
        if (
            plugin_id == "fonts_core"
            and asset.startswith("static/")
            and asset.endswith((".woff", ".woff2"))
            and not current_app.config.get("DEV_MODE")
        ):
            max_age = 86400
        return send_from_directory(plugin.path, asset, max_age=max_age)

    app.register_blueprint(bp, url_prefix="/plugins")

    if gate:
        install_disabled_gate(app, registry)

    # Endpoints a plugin declares safe to serve during a loopback render with
    # no session (see app/auth.py). A widget whose markup references its own
    # plugin's media route needs this, or the panel renders with broken
    # images; everything undeclared stays behind the password gate.
    render_safe: set[str] = set()

    # Disabled plugins mount too: enabling one later is then instant, and the
    # gate above keeps their routes dark meanwhile.
    for plugin in [*registry.plugins.values(), *registry.disabled.values()]:
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
        declared = getattr(plugin.server_module, "RENDER_SAFE_ENDPOINTS", ())
        if isinstance(declared, (list, tuple, set, frozenset)):
            render_safe.update(str(name) for name in declared)

    app.config["RENDER_SAFE_ENDPOINTS"] = frozenset(render_safe)
    # Plugins whose blueprint serves its index, for Plugin.has_admin.
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    app.config["PLUGIN_ADMIN_IDS"] = frozenset(
        pid for pid in [*registry.plugins, *registry.disabled] if f"/plugins/{pid}/" in rules
    )
