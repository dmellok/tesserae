"""Device discovery, manifest validation, and registry.

A device is a folder under ``devices/`` with a ``device.json`` manifest and
a ``device.py`` exporting ``parse_status`` and (if the device declares a
``config_topic``) ``validate_config``. Mirrors the plugin / renderer
loaders' drop-a-folder pattern.

Devices are the consumer side of one or more renderers:

* The device's ``status_topic`` is subscribed at boot; each incoming
  heartbeat passes through ``parse_status()`` to be normalised for the UI.
* If a ``config_topic`` is declared, the device's ``validate_config()`` is
  called before each publish so the broker never sees a malformed payload.

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

logger = logging.getLogger(__name__)

HOST_MAJOR_VERSION: int = 1

_COMPAT_RE = re.compile(r"^(\d+)\.(x|\d+)")


@dataclass(frozen=True)
class LoaderError:
    device_id: str
    path: Path
    message: str


@dataclass(frozen=True)
class Device:
    """A loaded device with its manifest and parse/validate hooks."""

    id: str
    path: Path
    manifest: dict[str, Any]
    module: ModuleType
    data_dir: Path

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    @property
    def renderer_ids(self) -> list[str]:
        return [str(r) for r in self.manifest.get("renderers", [])]

    @property
    def status_topic(self) -> str:
        return str(self.manifest["status_topic"])

    @property
    def config_topic(self) -> str | None:
        topic = self.manifest.get("config_topic")
        return str(topic) if isinstance(topic, str) else None

    @property
    def config_schema(self) -> dict[str, dict[str, Any]]:
        schema = self.manifest.get("config_schema") or {}
        if not isinstance(schema, dict):
            return {}
        return {str(k): dict(v) for k, v in schema.items() if isinstance(v, dict)}

    def parse_status(self, payload: bytes) -> dict[str, Any]:
        """Normalise a heartbeat payload. Falls back to ``{"raw": ...}`` if
        the device's parser raises so the UI still has something to show."""
        try:
            result = self.module.parse_status(payload)
        except Exception as err:
            logger.warning("device %s parse_status raised: %s", self.id, err)
            return {"error": f"{type(err).__name__}: {err}"}
        if not isinstance(result, dict):
            return {"error": f"parse_status returned {type(result).__name__}, expected dict"}
        return dict(result)

    def validate_config(self, payload: dict[str, Any]) -> tuple[bool, str | None]:
        """Pre-publish validation. Devices without a config topic raise."""
        if self.config_topic is None:
            raise RuntimeError(f"device {self.id} has no config_topic")
        validate_fn = getattr(self.module, "validate_config", None)
        if not callable(validate_fn):
            return True, None
        result = validate_fn(payload)
        if (
            not isinstance(result, tuple)
            or len(result) != 2
            or not isinstance(result[0], bool)
            or (result[1] is not None and not isinstance(result[1], str))
        ):
            return False, f"validate_config returned malformed result: {result!r}"
        return bool(result[0]), result[1]

    def config_field_defaults(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, field_spec in self.config_schema.items():
            if "default" in field_spec:
                out[name] = field_spec["default"]
        return out


@dataclass
class DeviceRegistry:
    devices: dict[str, Device] = field(default_factory=dict)
    errors: list[LoaderError] = field(default_factory=list)

    def get(self, device_id: str) -> Device | None:
        return self.devices.get(device_id)

    def all(self) -> list[Device]:
        return list(self.devices.values())


def _load_schema(schema_path: Path) -> dict[str, Any]:
    raw = json.loads(schema_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{schema_path} must contain a JSON object")
    return raw


def _import_device_module(device_id: str, module_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"_tesserae_devices.{device_id}.device", module_path
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


def _validate_exports(module: ModuleType, *, requires_config: bool) -> str | None:
    parse_fn = getattr(module, "parse_status", None)
    if not callable(parse_fn):
        return "device.py is missing required export 'parse_status'"
    parse_sig = signature(parse_fn)
    if len(parse_sig.parameters) < 1:
        return "parse_status must accept a single payload (bytes) argument"
    if requires_config:
        validate_fn = getattr(module, "validate_config", None)
        if not callable(validate_fn):
            return "device.py declares a config_topic but is missing 'validate_config'"
        validate_sig = signature(validate_fn)
        if len(validate_sig.parameters) < 1:
            return "validate_config must accept a single payload (dict) argument"
    return None


def discover(
    devices_dir: Path,
    *,
    schema_path: Path,
    data_root: Path,
) -> DeviceRegistry:
    """Walk ``devices_dir`` and return a registry of validated devices."""
    registry = DeviceRegistry()
    if not devices_dir.exists():
        return registry

    schema = _load_schema(schema_path)

    for child in sorted(devices_dir.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue

        device_id = child.name

        manifest_path = child / "device.json"
        if not manifest_path.exists():
            registry.errors.append(LoaderError(device_id, child, "device.json missing"))
            continue

        try:
            raw = json.loads(manifest_path.read_text())
        except json.JSONDecodeError as err:
            registry.errors.append(
                LoaderError(device_id, child, f"device.json invalid JSON: {err}")
            )
            continue

        if not isinstance(raw, dict):
            registry.errors.append(
                LoaderError(device_id, child, "device.json must be a JSON object")
            )
            continue
        manifest: dict[str, Any] = raw

        compat = manifest.get("tesserae_compat")
        if not isinstance(compat, str) or not _compat_ok(compat, HOST_MAJOR_VERSION):
            registry.errors.append(
                LoaderError(
                    device_id,
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
                LoaderError(device_id, child, f"manifest schema [{field_path}]: {err.message}")
            )
            continue

        module_path = child / "device.py"
        if not module_path.exists():
            registry.errors.append(LoaderError(device_id, child, "device.py missing"))
            continue

        try:
            module = _import_device_module(device_id, module_path)
        except Exception as err:
            registry.errors.append(LoaderError(device_id, child, f"device.py import failed: {err}"))
            continue

        export_err = _validate_exports(module, requires_config="config_topic" in manifest)
        if export_err is not None:
            registry.errors.append(LoaderError(device_id, child, export_err))
            continue

        if device_id in registry.devices:
            registry.errors.append(LoaderError(device_id, child, "duplicate device id"))
            continue

        data_dir = data_root / device_id
        data_dir.mkdir(parents=True, exist_ok=True)

        registry.devices[device_id] = Device(
            id=device_id,
            path=child,
            manifest=manifest,
            module=module,
            data_dir=data_dir,
        )
        logger.info(
            "Loaded device %s (renderers=%s, status=%s)",
            device_id,
            manifest.get("renderers"),
            manifest["status_topic"],
        )

    return registry
