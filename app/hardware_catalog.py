"""Hardware catalog: data-only SKU definitions that derive device kinds
from existing protocol-level device folders.

Background. Adding a new e-paper SKU to Tesserae used to mean a new
``devices/<id>/`` folder with ``device.py`` + ``device.json`` + ``tests/``,
even when the wire format and parse logic were identical to an existing
device kind. The reTerminal E Series, the TRMNL X lineup, and the LilyGo
T5 4.7" community contribution all hit this limit at once: a dozen SKUs
that differ only in panel dimensions, gamut, and a handful of vendor
metadata strings.

This module adds a second tier next to the folder-based loader. A JSON
file under ``hardware/<vendor>/<model>.json`` declares an SKU:

* Identity (``id``, ``name``, ``vendor``, ``url``, ``icon``).
* The protocol-level device folder it inherits from (``protocol``).
* The physical panel (``panel`` block, replaces the protocol's default).
* Free-form protocol-specific defaults (``protocol_config``), forwarded
  to the protocol's app-side reader code under the same key.
* Optional additions to the protocol's config form
  (``config_schema_extends``).
* Optional aliases for back-compat after a rename
  (``deprecated_aliases``).

At load time the catalog walks the hardware tree, validates each entry,
looks up the referenced protocol in the device registry, and constructs
a derived ``Device`` per SKU that shares the protocol's Python module
but carries its own manifest. The folder-based discover() runs first so
an existing folder always wins on id conflict (back-compat).

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

from app.device_loader import Device, DeviceRegistry, LoaderError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HardwareEntry:
    """One validated SKU entry from the hardware catalog."""

    id: str
    path: Path
    manifest: dict[str, Any]

    @property
    def protocol(self) -> str:
        return str(self.manifest["protocol"])

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    @property
    def vendor(self) -> str:
        return str(self.manifest["vendor"])

    @property
    def deprecated_aliases(self) -> list[str]:
        raw = self.manifest.get("deprecated_aliases") or []
        if not isinstance(raw, list):
            return []
        return [str(a) for a in raw if isinstance(a, str)]


def _load_schema(schema_path: Path) -> dict[str, Any]:
    raw = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{schema_path} must contain a JSON object")
    return raw


def discover_hardware(
    hardware_dir: Path,
    *,
    schema_path: Path,
) -> tuple[list[HardwareEntry], list[LoaderError]]:
    """Walk ``hardware_dir`` recursively and return validated SKU entries
    plus any per-file load errors.

    The directory structure is ``hardware/<vendor>/<model>.json``, but
    the walker accepts any JSON file at any depth so contributors can
    organise vendors and sublines however suits the catalog. Files
    starting with ``_`` are skipped (notes / drafts).

    Validation is purely schema-level here; protocol id existence and
    id-collision handling happen in ``apply_to_registry`` once the
    folder-based registry has been built.
    """
    entries: list[HardwareEntry] = []
    errors: list[LoaderError] = []
    if not hardware_dir.exists():
        return entries, errors

    schema = _load_schema(schema_path)

    for path in sorted(hardware_dir.rglob("*.json")):
        if path.name.startswith((".", "_")):
            continue
        rel_id = path.stem
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            errors.append(LoaderError(rel_id, path, f"invalid JSON: {err}"))
            continue
        if not isinstance(raw, dict):
            errors.append(LoaderError(rel_id, path, "hardware entry must be a JSON object"))
            continue
        manifest: dict[str, Any] = raw

        try:
            jsonschema.validate(manifest, schema)
        except jsonschema.ValidationError as err:
            field_path = ".".join(str(p) for p in err.absolute_path) or "<root>"
            errors.append(
                LoaderError(
                    str(manifest.get("id") or rel_id),
                    path,
                    f"hardware schema [{field_path}]: {err.message}",
                )
            )
            continue

        entry_id = str(manifest["id"])
        entries.append(HardwareEntry(id=entry_id, path=path, manifest=manifest))

    return entries, errors


def _derive_manifest(
    protocol_device: Device,
    entry: HardwareEntry,
) -> dict[str, Any]:
    """Build the manifest for a hardware-derived kind by layering the
    SKU's identity + panel + protocol-config over the protocol's
    defaults. Mutates a deep copy of the protocol's manifest; returns
    the result.

    Layering order (last wins):

    1. Protocol's ``device.json`` (base).
    2. Hardware entry's identity fields (``name``, ``icon``, ``description``,
       ``vendor`` exposed as ``vendor`` on the manifest).
    3. Hardware entry's ``panel`` block (replaces the protocol's wholesale).
    4. Hardware entry's ``protocol_config`` stashed at the same key for
       the protocol's app-side reader code.
    5. ``config_schema_extends`` merged additively into the protocol's
       ``config_schema``.

    Status / config topics and the ``tesserae_compat`` declaration are
    inherited verbatim from the protocol so the wire contract is
    identical. Renderers are inherited too, unless the hardware entry
    carries its own ``renderers`` override (used when the same wire
    protocol drives panels with different output formats, e.g. mono
    TRMNL panels take 1-bit output while colour TRMNL panels take an
    indexed-palette PNG).
    """
    manifest = copy.deepcopy(protocol_device.manifest)

    manifest["name"] = entry.name
    manifest["vendor"] = entry.vendor

    icon = entry.manifest.get("icon")
    if isinstance(icon, str) and icon.strip():
        manifest["icon"] = icon.strip()

    description = entry.manifest.get("description")
    if isinstance(description, str):
        manifest["description"] = description

    url = entry.manifest.get("url")
    if isinstance(url, str):
        manifest["url"] = url

    notes = entry.manifest.get("notes_md")
    if isinstance(notes, str):
        manifest["notes_md"] = notes

    panel = entry.manifest.get("panel")
    if isinstance(panel, dict):
        manifest["panel"] = dict(panel)

    protocol_config = entry.manifest.get("protocol_config")
    if isinstance(protocol_config, dict):
        manifest["protocol_config"] = dict(protocol_config)

    refresh_floor = entry.manifest.get("refresh_floor_s")
    if isinstance(refresh_floor, int) and refresh_floor > 0:
        manifest["refresh_floor_s"] = refresh_floor

    image_format = entry.manifest.get("image_format")
    if isinstance(image_format, str):
        manifest["image_format"] = image_format

    # Touch capability (issue #49): the panel has a digitizer, so Tesserae's
    # touch dispatch and the editors' Interaction UI apply to it. A hardware
    # fact (distinct from the ``touch_enabled`` firmware config); surfaced in
    # the device APIs so agents and the editor know which panels are tappable.
    if entry.manifest.get("touch") is True:
        manifest["touch"] = True

    extends = entry.manifest.get("config_schema_extends")
    if isinstance(extends, dict):
        merged_schema = dict(manifest.get("config_schema") or {})
        for field_name, field_spec in extends.items():
            if isinstance(field_spec, dict):
                merged_schema[str(field_name)] = dict(field_spec)
        manifest["config_schema"] = merged_schema

    # Per-hardware renderer override. Used when the same wire protocol
    # drives panels with meaningfully different output formats (mono
    # TRMNL panels take 1-bit; colour TRMNL panels take an indexed
    # palette PNG). Absent → inherit the protocol's renderers.
    renderers_override = entry.manifest.get("renderers")
    if isinstance(renderers_override, list) and renderers_override:
        manifest["renderers"] = [str(r) for r in renderers_override if isinstance(r, str)]

    # Track the catalog origin on the manifest so the Settings UI and
    # compatibility-matrix generator can distinguish folder-defined kinds
    # from catalog-defined ones (different docs link, different edit
    # affordances). Hidden under a single key so it doesn't pollute the
    # public schema.
    manifest["_catalog_entry"] = {
        "protocol": entry.protocol,
        "file": str(entry.path),
        "vendor": entry.vendor,
        # Whether a device's self-report may be resolved to this SKU
        # automatically. Defaults on; see ``auto_select`` in the hardware
        # schema for why a variant opts out.
        "auto_select": entry.manifest.get("auto_select", True) is not False,
    }

    return manifest


def apply_to_registry(
    registry: DeviceRegistry,
    entries: list[HardwareEntry],
    *,
    data_root: Path,
) -> None:
    """Register hardware-derived kinds onto ``registry`` in place.

    For each entry whose protocol exists in the registry as a kind
    (``kind_of is None``) and whose id isn't already taken, build a
    derived ``Device`` that borrows the protocol's Python module but
    carries the entry's manifest. Also register the derived Device
    under any ``deprecated_aliases`` so legacy device-instance files
    keep resolving.

    Errors land on ``registry.errors`` with the same shape as
    folder-loader errors so the Settings UI's loader-error panel
    surfaces both uniformly. Specifically:

    * Unknown protocol → "unknown protocol".
    * Protocol resolves to an instance (kind_of set), not a kind →
      "protocol must reference a built-in kind".
    * Id collision with an existing kind → "id already in use"
      (folder always wins, hardware entry skipped).
    * Alias collision → "alias already in use" (alias skipped,
      canonical id still registers if it's free).
    """
    for entry in entries:
        protocol_device = registry.devices.get(entry.protocol)
        if protocol_device is None:
            registry.errors.append(
                LoaderError(entry.id, entry.path, f"unknown protocol {entry.protocol!r}")
            )
            continue
        if protocol_device.kind_of is not None:
            registry.errors.append(
                LoaderError(
                    entry.id,
                    entry.path,
                    f"protocol {entry.protocol!r} must reference a built-in kind, not an instance",
                )
            )
            continue
        if entry.id in registry.devices:
            registry.errors.append(
                LoaderError(entry.id, entry.path, f"id {entry.id!r} already in use")
            )
            continue

        derived_manifest = _derive_manifest(protocol_device, entry)
        data_dir = data_root / entry.id
        data_dir.mkdir(parents=True, exist_ok=True)

        derived = Device(
            id=entry.id,
            path=entry.path,
            manifest=derived_manifest,
            module=protocol_device.module,
            data_dir=data_dir,
        )
        registry.devices[entry.id] = derived
        logger.info(
            "Loaded hardware-catalog kind %s (protocol=%s, vendor=%s)",
            entry.id,
            entry.protocol,
            entry.vendor,
        )

        for alias in entry.deprecated_aliases:
            if alias in registry.devices:
                registry.errors.append(
                    LoaderError(
                        alias,
                        entry.path,
                        f"alias {alias!r} already in use, skipped",
                    )
                )
                continue
            registry.devices[alias] = derived
            logger.info("Registered %s alias -> %s", alias, entry.id)
