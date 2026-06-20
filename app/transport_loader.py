"""Transport discovery + registry.

A transport is one delivery channel by which devices receive frames and
post status. Today there are two: MQTT (push, broker-mediated) and REST
(pull, polled over HTTP). The loader walks ``transports/<id>/`` and
loads each manifest, mirroring the renderer / device / plugin loaders.

The intent is metadata + visibility, NOT a rewriting of the working
transport implementations. The MQTT path lives in
``app/transport.py`` + ``app/transport_wiring.py``; the REST path lives
in ``app/rest_api.py``. Both are registered via the existing factory
plumbing. The loader's job is to surface the LIST of available
transports + their capabilities so the Settings UI can show "MQTT + REST
both active", and so any future third transport (WebSocket, gRPC, etc.)
can be added by dropping a folder + landing its implementation,
without having to thread a new manifest field through five places.

The drop-a-folder pattern is the same as renderers and devices, but
the per-transport implementation is more diverse than a renderer's
``transform()`` + ``payload()`` contract: MQTT needs a long-lived
broker connection, REST needs HTTP routes, a future SSE transport
would need persistent connections + write-backpressure. Forcing a
common ABC on these would be a fiction. The loader stays metadata-
only; each transport's actual wiring continues to live where it
makes sense in the app.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

logger = logging.getLogger(__name__)

HOST_MAJOR_VERSION: int = 1

_COMPAT_RE = re.compile(r"^(\d+)\.(x|\d+)")


@dataclass(frozen=True)
class LoaderError:
    transport_id: str
    path: Path
    message: str


@dataclass(frozen=True)
class Transport:
    """A loaded transport. Metadata-only; the implementation lives
    elsewhere in the app."""

    id: str
    path: Path
    manifest: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    @property
    def description(self) -> str:
        return str(self.manifest.get("description") or "")

    @property
    def kind(self) -> str:
        """``"push"`` (the transport pushes frames to devices, e.g. MQTT
        publish) or ``"pull"`` (the transport serves frames on poll,
        e.g. REST). Informational; the push pipeline doesn't switch on
        this, the Device.transport per-instance choice does."""
        return str(self.manifest.get("kind") or "push")

    @property
    def broker_required(self) -> bool:
        return bool(self.manifest.get("broker_required", False))

    @property
    def capabilities(self) -> dict[str, bool]:
        caps = self.manifest.get("capabilities") or {}
        if not isinstance(caps, dict):
            return {}
        return {str(k): bool(v) for k, v in caps.items()}

    @property
    def default_for_new_devices(self) -> bool:
        return bool(self.manifest.get("default_for_new_devices", False))


@dataclass
class TransportRegistry:
    transports: dict[str, Transport] = field(default_factory=dict)
    errors: list[LoaderError] = field(default_factory=list)

    def get(self, transport_id: str) -> Transport | None:
        return self.transports.get(transport_id)

    def all(self) -> list[Transport]:
        # Sort so the order is stable across renders (alphabetical by id).
        return sorted(self.transports.values(), key=lambda t: t.id)

    def default(self) -> Transport | None:
        """The transport flagged ``default_for_new_devices: true`` in its
        manifest. When more than one declares the flag, pick the first
        alphabetically (deterministic across restarts)."""
        candidates = [t for t in self.all() if t.default_for_new_devices]
        return candidates[0] if candidates else None


def _load_schema(schema_path: Path) -> dict[str, Any]:
    raw = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{schema_path} must contain a JSON object")
    return raw


def _compat_ok(declared: str, host_major: int) -> bool:
    m = _COMPAT_RE.match(declared)
    if not m:
        return False
    return int(m.group(1)) == host_major


def discover(transports_dir: Path, *, schema_path: Path) -> TransportRegistry:
    """Walk ``transports_dir`` and return a registry of validated
    transport manifests. Errors are collected on the registry rather
    than raised so the boot path keeps moving when one transport's
    manifest is malformed."""
    registry = TransportRegistry()
    if not transports_dir.exists():
        return registry

    schema = _load_schema(schema_path)

    for child in sorted(transports_dir.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue

        transport_id = child.name
        manifest_path = child / "transport.json"
        if not manifest_path.exists():
            registry.errors.append(LoaderError(transport_id, child, "transport.json missing"))
            continue

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            registry.errors.append(
                LoaderError(transport_id, child, f"transport.json invalid JSON: {err}")
            )
            continue

        if not isinstance(raw, dict):
            registry.errors.append(
                LoaderError(transport_id, child, "transport.json must be a JSON object")
            )
            continue
        manifest: dict[str, Any] = raw

        compat = manifest.get("tesserae_compat")
        if not isinstance(compat, str) or not _compat_ok(compat, HOST_MAJOR_VERSION):
            registry.errors.append(
                LoaderError(
                    transport_id,
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
                LoaderError(
                    transport_id,
                    child,
                    f"manifest schema [{field_path}]: {err.message}",
                )
            )
            continue

        # The folder name and the manifest id must match. Without this,
        # the Device.transport field could refer to a transport id that
        # doesn't match the directory it lives in, which would confuse
        # any future per-transport hooks.
        manifest_id = str(manifest.get("id") or "")
        if manifest_id != transport_id:
            registry.errors.append(
                LoaderError(
                    transport_id,
                    child,
                    f"manifest id {manifest_id!r} does not match folder name {transport_id!r}",
                )
            )
            continue

        if transport_id in registry.transports:
            registry.errors.append(LoaderError(transport_id, child, "duplicate transport id"))
            continue

        registry.transports[transport_id] = Transport(
            id=transport_id,
            path=child,
            manifest=manifest,
        )
        logger.info(
            "Loaded transport %s (kind=%s, broker_required=%s)",
            transport_id,
            manifest.get("kind"),
            manifest.get("broker_required"),
        )

    return registry
