"""Renderer discovery, manifest validation, and registry.

A renderer is a folder under ``renderers/`` with a ``renderer.json`` manifest
and a ``renderer.py`` exporting ``transform()`` and ``payload()``. Mirrors the
plugin loader's drop-a-folder pattern so adding a new wire format / device
family is a contained change.

The loader runs once at app startup. Errors don't raise — they're collected
on the registry so the admin UI can surface them while the rest of the system
keeps working.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from inspect import signature
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema

from app.state.page_store import Panel

logger = logging.getLogger(__name__)

HOST_MAJOR_VERSION: int = 1

_COMPAT_RE = re.compile(r"^(\d+)\.(x|\d+)")

# Required exports on renderer.py. Signatures are validated by introspection
# at load time so a plugin with a drifted signature errors loudly at startup
# rather than mid-push.
_REQUIRED_EXPORTS: tuple[str, ...] = ("transform", "payload")


@dataclass(frozen=True)
class LoaderError:
    renderer_id: str
    path: Path
    message: str


@dataclass(frozen=True)
class Renderer:
    """A loaded renderer with its manifest, resolved topic, and callable
    ``transform`` / ``payload`` references.

    The registry holds these by id. The push pipeline iterates them, calls
    ``transform`` on the composition PNG, writes the artifact, then asks
    ``payload`` to construct the MQTT payload."""

    id: str
    path: Path
    manifest: dict[str, Any]
    module: ModuleType
    data_dir: Path

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    @property
    def device(self) -> str:
        return str(self.manifest["device"])

    @property
    def orientation(self) -> str:
        return str(self.manifest["orientation"])

    @property
    def mime(self) -> str:
        return str(self.manifest["mime"])

    @property
    def extension(self) -> str:
        return str(self.manifest["extension"])

    @property
    def retain(self) -> bool:
        return bool(self.manifest["retain"])

    @property
    def topic(self) -> str:
        """Resolved publish topic. ``{device}`` in ``topic_pattern`` is
        substituted from the manifest's ``device`` field at load time."""
        return str(self.manifest["topic_pattern"]).replace("{device}", self.device)

    def settings_defaults(self) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        for setting in self.manifest.get("settings", []):
            if "default" in setting:
                defaults[str(setting["name"])] = setting["default"]
        return defaults

    def transform(self, png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
        """Run the renderer's transform. Composition-orientation PNG in,
        artifact bytes (PNG, .bin, ...) out."""
        result = self.module.transform(png_bytes, panel=panel, settings=settings)
        if not isinstance(result, bytes):
            raise TypeError(
                f"renderer {self.id} transform() returned {type(result).__name__}, expected bytes"
            )
        return result

    def payload(self, digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
        """Construct the MQTT JSON payload. Must include a ``url`` key."""
        result = self.module.payload(digest, base_url, settings=settings)
        if not isinstance(result, dict) or "url" not in result:
            raise TypeError(f"renderer {self.id} payload() must return a dict containing 'url'")
        return dict(result)


@dataclass
class RendererRegistry:
    renderers: dict[str, Renderer] = field(default_factory=dict)
    errors: list[LoaderError] = field(default_factory=list)

    def get(self, renderer_id: str) -> Renderer | None:
        return self.renderers.get(renderer_id)

    def all(self) -> list[Renderer]:
        return list(self.renderers.values())

    def for_device(self, device_id: str) -> list[Renderer]:
        """All renderers whose ``device`` matches the given device id."""
        return [r for r in self.renderers.values() if r.device == device_id]


def clone_for_instances(renderers: RendererRegistry, devices: Any) -> None:
    """For each device instance (a user-created copy of a built-in kind),
    add a cloned Renderer record per renderer the kind consumes. The
    clone overrides ``device`` so its ``topic_pattern`` resolves against
    the instance's id — each physical device gets its own MQTT topics
    without changes to the rendering core.

    The link between an instance and its renderers is the **kind's**
    ``renderer_ids`` list (e.g. ``esp32_client`` → ``["esp32_bin"]``).
    We can't match on ``renderer.device`` because that's a topic prefix
    (``"esp32"``), not a device id (``"esp32_client"``)."""
    get_all = getattr(devices, "all", None)
    get_one = getattr(devices, "get", None)
    if not callable(get_all) or not callable(get_one):
        return
    for dev in get_all():
        kind_id = getattr(dev, "kind_of", None)
        if kind_id is None:
            continue  # built-in kind — already has its own renderers
        kind = get_one(kind_id)
        if kind is None:
            continue
        for renderer_id in getattr(kind, "renderer_ids", []):
            base = renderers.get(renderer_id)
            if base is None:
                continue
            clone_id = f"{base.id}__{dev.id}"
            if clone_id in renderers.renderers:
                continue
            cloned_manifest = dict(base.manifest)
            cloned_manifest["device"] = dev.id
            cloned_manifest["name"] = f"{base.name} ({dev.id})"
            renderers.renderers[clone_id] = Renderer(
                id=clone_id,
                path=base.path,
                manifest=cloned_manifest,
                module=base.module,
                data_dir=base.data_dir,
            )


def _load_schema(schema_path: Path) -> dict[str, Any]:
    raw = json.loads(schema_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{schema_path} must contain a JSON object")
    return raw


def _import_renderer_module(renderer_id: str, module_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"_tesserae_renderers.{renderer_id}.renderer", module_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _compat_ok(declared: str, host_major: int) -> bool:
    m = _COMPAT_RE.match(declared)
    if not m:
        return False
    return int(m.group(1)) == host_major


def _validate_exports(module: ModuleType) -> str | None:
    """Return an error message if the renderer module's exports are missing
    or have unexpected signatures. ``None`` on success."""
    for name in _REQUIRED_EXPORTS:
        fn = getattr(module, name, None)
        if not callable(fn):
            return f"renderer.py is missing required export {name!r}"
    transform_sig = signature(module.transform)
    if "png_bytes" not in transform_sig.parameters:
        return "transform() must accept a 'png_bytes' positional/keyword parameter"
    if "panel" not in transform_sig.parameters or "settings" not in transform_sig.parameters:
        return "transform() must accept 'panel' and 'settings' keyword parameters"
    payload_sig = signature(module.payload)
    payload_params = list(payload_sig.parameters)
    if len(payload_params) < 2:
        return "payload() must accept (digest, base_url, *, settings)"
    if "settings" not in payload_sig.parameters:
        return "payload() must accept a 'settings' keyword parameter"
    return None


def discover(
    renderers_dir: Path,
    *,
    schema_path: Path,
    data_root: Path,
) -> RendererRegistry:
    """Walk ``renderers_dir`` and return a registry of validated renderers."""
    registry = RendererRegistry()
    if not renderers_dir.exists():
        return registry

    schema = _load_schema(schema_path)

    for child in sorted(renderers_dir.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue

        renderer_id = child.name

        manifest_path = child / "renderer.json"
        if not manifest_path.exists():
            registry.errors.append(LoaderError(renderer_id, child, "renderer.json missing"))
            continue

        try:
            raw = json.loads(manifest_path.read_text())
        except json.JSONDecodeError as err:
            registry.errors.append(
                LoaderError(renderer_id, child, f"renderer.json invalid JSON: {err}")
            )
            continue

        if not isinstance(raw, dict):
            registry.errors.append(
                LoaderError(renderer_id, child, "renderer.json must be a JSON object")
            )
            continue
        manifest: dict[str, Any] = raw

        compat = manifest.get("tesserae_compat")
        if not isinstance(compat, str) or not _compat_ok(compat, HOST_MAJOR_VERSION):
            registry.errors.append(
                LoaderError(
                    renderer_id,
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
                LoaderError(renderer_id, child, f"manifest schema [{field_path}]: {err.message}")
            )
            continue

        module_path = child / "renderer.py"
        if not module_path.exists():
            registry.errors.append(LoaderError(renderer_id, child, "renderer.py missing"))
            continue

        try:
            module = _import_renderer_module(renderer_id, module_path)
        except Exception as err:
            registry.errors.append(
                LoaderError(renderer_id, child, f"renderer.py import failed: {err}")
            )
            continue

        export_err = _validate_exports(module)
        if export_err is not None:
            registry.errors.append(LoaderError(renderer_id, child, export_err))
            continue

        if renderer_id in registry.renderers:
            registry.errors.append(LoaderError(renderer_id, child, "duplicate renderer id"))
            continue

        data_dir = data_root / renderer_id
        data_dir.mkdir(parents=True, exist_ok=True)

        registry.renderers[renderer_id] = Renderer(
            id=renderer_id,
            path=child,
            manifest=manifest,
            module=module,
            data_dir=data_dir,
        )
        logger.info(
            "Loaded renderer %s (device=%s, topic=%s)",
            renderer_id,
            manifest["device"],
            manifest["topic_pattern"].replace("{device}", manifest["device"]),
        )

    return registry
