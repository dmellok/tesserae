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

mypy --strict applies to this module, see pyproject.toml.
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
    """A loaded device with its manifest and parse/validate hooks.

    Two flavours coexist in one registry:

    * **Kinds** (loaded from ``devices/<id>/device.json``), the
      built-in device manifests that ship with the app. ``kind_of`` is
      None: a kind IS its own kind.
    * **Instances** (loaded from ``data/devices/<id>.json``), user-
      created devices that pick a kind for the parse/validate hooks
      and config schema, then override id / name / topics / panel /
      config. Multi-head installs use instances so each physical
      display gets its own MQTT topic + panel.
    """

    id: str
    path: Path
    manifest: dict[str, Any]
    module: ModuleType
    data_dir: Path
    kind_of: str | None = None  # None = built-in kind; else the kind id this instance inherits from

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    @property
    def display_name(self) -> str:
        """Short label for device pickers. When the user didn't set a
        custom name, ``name`` is the auto-generated ``"Kind (id)"``
        default, which duplicates the id. Collapse that case to just
        the id so the picker reads ``lounge_frame`` instead of
        ``Pi BIN client (lounge_frame), lounge_frame``."""
        if self.name.endswith(f"({self.id})"):
            return self.id
        return self.name

    @property
    def icon(self) -> str:
        """Phosphor icon slug (no ``ph-`` prefix) for device pickers and the
        settings card. Comes from the manifest, kinds declare a sensible
        default in ``device.json`` and instances inherit it until the user
        picks their own. Falls back to a generic display glyph."""
        raw = self.manifest.get("icon")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return "monitor"

    @property
    def renderer_ids(self) -> list[str]:
        return [str(r) for r in self.manifest.get("renderers", [])]

    @property
    def status_topic(self) -> str | None:
        """MQTT topic for status heartbeats. ``None`` for devices that
        don't use MQTT, e.g. TRMNL clients poll ``/api/display`` and
        their status comes from HTTP request headers, parsed by
        ``app.trmnl_api`` rather than a transport subscription."""
        topic = self.manifest.get("status_topic")
        return str(topic) if isinstance(topic, str) and topic else None

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

    @property
    def panel(self) -> dict[str, Any] | None:
        """Declared panel dims for this device, or None if the device is
        renderer-only / panel-agnostic. Multi-head installs use this so
        each device sizes its pages independently of the global default."""
        block = self.manifest.get("panel")
        if not isinstance(block, dict):
            return None
        try:
            w = int(block["w"])
            h = int(block["h"])
        except (KeyError, TypeError, ValueError):
            return None
        out: dict[str, Any] = {"w": w, "h": h}
        orientation = block.get("orientation")
        if orientation in ("landscape", "landscape_flipped", "portrait", "portrait_flipped"):
            out["orientation"] = orientation
        if isinstance(block.get("name"), str):
            out["name"] = block["name"]
        if isinstance(block.get("gamut"), str):
            out["gamut"] = block["gamut"]
        try:
            underscan = int(block.get("underscan", 0))
        except (TypeError, ValueError):
            underscan = 0
        if underscan > 0:
            out["underscan"] = underscan
        # Firmware-native row stride (when the manifest carries it; the
        # v0.20 add-device path writes it for preset-picked panels, and
        # the startup backfill in app.device_service patches existing
        # esp32 instance manifests). Without this passthrough the dict
        # arrives at device_panel() missing the keys and the dims-only
        # preset-matching fallback wins, for a 1200×1600 Waveshare
        # 13.3" it picks Inky 13.3" (1600×1200) by dict order and the
        # renderer packs at the wrong stride.
        for stride_key in ("native_w", "native_h"):
            raw = block.get(stride_key)
            if raw is None:
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                out[stride_key] = value
        return out

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
        """Check a config payload against the device's validator.

        Used both before an MQTT publish (transport-bound devices like
        ESP32 / Pi clients) and before stashing config for the next
        non-MQTT exchange (HTTP-polled TRMNL clients embed the saved
        values in the next ``/api/display`` response). Independent of
        the transport, the validator decides validity, the caller
        decides what to do with the bytes."""
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
    raw = json.loads(schema_path.read_text(encoding="utf-8"))
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
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
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
            manifest.get("status_topic") or "http-polled",
        )

    # ----- user-defined instances ------------------------------------
    # data_root is already data/devices/ (see main.py), so scan it directly.
    if data_root.exists():
        for inst_file in sorted(data_root.iterdir()):
            if not inst_file.is_file() or not inst_file.name.endswith(".json"):
                continue
            load_instance_file(registry, inst_file=inst_file, data_root=data_root)

    return registry


def load_instance_file(
    registry: DeviceRegistry,
    *,
    inst_file: Path,
    data_root: Path,
) -> Device | None:
    """Load one user-defined instance file into ``registry`` in place.

    An instance file under ``data/devices/<id>.json`` is a JSON object:
      ``{ id, kind, name, status_topic?, config_topic?, panel? }``
    Instances inherit the kind's parse_status / validate_config, but
    override everything else (their own id, MQTT topics, panel dims).
    Returns the new Device on success, ``None`` if validation failed
    (the error is appended to ``registry.errors``).

    Shared by the startup discover() loop and the Settings UI's
    add-device flow so both go through identical validation."""
    instance_id = inst_file.stem
    try:
        raw_inst = json.loads(inst_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        registry.errors.append(LoaderError(instance_id, inst_file, f"invalid JSON: {err}"))
        return None
    if not isinstance(raw_inst, dict):
        registry.errors.append(
            LoaderError(instance_id, inst_file, "instance file must be a JSON object")
        )
        return None
    kind_id = str(raw_inst.get("kind") or "")
    kind = registry.devices.get(kind_id)
    if kind is None or kind.kind_of is not None:
        registry.errors.append(
            LoaderError(
                instance_id,
                inst_file,
                f"unknown or non-kind device {kind_id!r} (instances must reference a built-in kind)",
            )
        )
        return None
    if instance_id in registry.devices:
        registry.errors.append(
            LoaderError(instance_id, inst_file, f"id {instance_id!r} already in use")
        )
        return None
    inst_manifest: dict[str, Any] = dict(kind.manifest)
    inst_manifest["name"] = str(raw_inst.get("name") or kind.name)
    if isinstance(raw_inst.get("icon"), str) and raw_inst["icon"].strip():
        inst_manifest["icon"] = raw_inst["icon"].strip()
    if raw_inst.get("status_topic"):
        inst_manifest["status_topic"] = str(raw_inst["status_topic"])
    if "config_topic" in raw_inst:
        topic = raw_inst["config_topic"]
        if topic is None:
            inst_manifest.pop("config_topic", None)
        else:
            inst_manifest["config_topic"] = str(topic)
    if isinstance(raw_inst.get("panel"), dict):
        inst_manifest["panel"] = dict(raw_inst["panel"])
    # Per-device quiet-hours override (see app.quiet_hours). Optional;
    # absent or empty means fall back to the app-level setting.
    if isinstance(raw_inst.get("quiet_hours"), dict):
        inst_manifest["quiet_hours"] = dict(raw_inst["quiet_hours"])
    # Per-device access token, currently only TRMNL devices use this
    # (HTTP-polled clients identify themselves by token in lieu of MQTT
    # topics). Carry it through so app.trmnl_api can look up the
    # device on incoming /api/display requests.
    if isinstance(raw_inst.get("access_token"), str) and raw_inst["access_token"].strip():
        inst_manifest["access_token"] = raw_inst["access_token"].strip()
    # Point the instance at its cloned renderers so the settings UI
    # and any code that reads device.renderer_ids sees the per-
    # instance ids that clone_for_instances() will create.
    inst_manifest["renderers"] = [f"{r}__{instance_id}" for r in kind.renderer_ids]
    inst_data_dir = data_root / instance_id
    inst_data_dir.mkdir(parents=True, exist_ok=True)
    device = Device(
        id=instance_id,
        path=inst_file,
        manifest=inst_manifest,
        module=kind.module,
        data_dir=inst_data_dir,
        kind_of=kind_id,
    )
    registry.devices[instance_id] = device
    logger.info(
        "Loaded device instance %s (kind=%s, status=%s)",
        instance_id,
        kind_id,
        inst_manifest.get("status_topic"),
    )
    return device
