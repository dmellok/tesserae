"""Device-instance lifecycle: create / update-panel / delete.

A thin service layer over the device + renderer registries and the
``data/devices/<id>.json`` instance files. Flask routes stay thin —
parse the form, call one of these, flash + redirect on the result.

Keeping topic derivation and the write → load → clone dance in one
place is the whole point: the Add-device form and the Discovered
one-click register used to inline near-identical logic and had already
drifted (one swapped the kind's topic prefix, the other hardcoded
``tesserae/<id>/status``). Both now go through ``create_instance``.

Transport re-subscription is deliberately NOT done here — it needs
``app.config`` and is the caller's job after a successful mutation, so
this module stays free of Flask.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.device_loader import Device, DeviceRegistry, load_instance_file
from app.renderer_loader import RendererRegistry, clone_for_instances

# Canonical instance-id rule, shared with the Settings routes. Lowercase,
# starts with a letter, 2–32 chars of [a-z0-9_-].
DEVICE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


@dataclass(frozen=True)
class InstanceResult:
    """Outcome of a lifecycle op. ``error`` is None on success and the
    ``device`` is the affected record (the new one for create/update,
    the removed one for delete)."""

    device: Device | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def derive_topic(kind_topic: str, new_id: str, *, suffix: str) -> str:
    """Derive an instance topic from the kind's by swapping the prefix
    segment: ``tesserae/<kind_prefix>/status`` → ``tesserae/<id>/status``.
    Falls back to ``tesserae/<id>/<suffix>`` if the kind's topic doesn't
    fit the expected shape (so an odd kind topic can't make two
    instances collide)."""
    parts = kind_topic.split("/")
    if len(parts) >= 2 and parts[0] == "tesserae":
        parts[1] = new_id
        return "/".join(parts)
    return f"tesserae/{new_id}/{suffix}"


def _drop_clones(renderers: RendererRegistry, instance_id: str) -> None:
    """Remove any renderer clones whose resolved device is this instance."""
    for rid in list(renderers.renderers):
        if renderers.renderers[rid].device == instance_id:
            renderers.renderers.pop(rid, None)


def create_instance(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    instance_id: str,
    kind_id: str,
    name: str = "",
    panel_overrides: dict[str, Any] | None = None,
    orientation: str | None = None,
    rotation: int | None = None,
) -> InstanceResult:
    """Validate, persist, load, and clone-renderers for a new instance.

    ``panel_overrides`` (``{"w":…, "h":…}``) layer on top of the kind's
    panel; ``orientation`` (``"portrait"`` / ``"landscape"``) is stamped
    on and, for portrait, swaps w/h once so the stored dims match what
    the user picked. ``rotation`` (0/90/180/270 CW degrees, or None for
    the renderer default) is stamped on as the physical-mount turn.
    Caller rebuilds the transport on success."""
    instance_id = instance_id.strip().lower()
    if not DEVICE_ID_RE.match(instance_id):
        return InstanceResult(
            None,
            "Device id must be 2-32 chars, start with a letter, and use only "
            "lowercase letters / digits / underscore / hyphen.",
        )
    if instance_id in devices.devices:
        return InstanceResult(None, f"Device id {instance_id!r} is already in use.")
    kind = devices.get(kind_id)
    if kind is None or kind.kind_of is not None:
        return InstanceResult(None, f"Unknown device kind {kind_id!r}.")

    manifest: dict[str, Any] = {
        "id": instance_id,
        "kind": kind_id,
        "name": name.strip() or f"{kind.name} ({instance_id})",
        "status_topic": derive_topic(kind.status_topic, instance_id, suffix="status"),
    }
    if kind.config_topic:
        manifest["config_topic"] = derive_topic(kind.config_topic, instance_id, suffix="config")

    panel = dict(kind.panel or {})
    if panel_overrides:
        panel.update(panel_overrides)
    _apply_orientation(panel, orientation)
    if rotation in (0, 90, 180, 270):
        panel["rotation"] = rotation
    if panel:
        manifest["panel"] = panel

    data_root.mkdir(parents=True, exist_ok=True)
    inst_file = data_root / f"{instance_id}.json"
    if inst_file.exists():
        return InstanceResult(None, f"An instance file at {inst_file.name} already exists.")
    inst_file.write_text(json.dumps(manifest, indent=2) + "\n")

    device = load_instance_file(devices, inst_file=inst_file, data_root=data_root)
    if device is None:
        last_err = devices.errors[-1] if devices.errors else None
        inst_file.unlink(missing_ok=True)
        return InstanceResult(None, last_err.message if last_err else "unknown error")
    clone_for_instances(renderers, devices)
    return InstanceResult(device)


def update_instance_panel(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    instance_id: str,
    w: int,
    h: int,
    orientation: str,
    rotation: int | None = None,
) -> InstanceResult:
    """Patch an instance's panel block on disk + reload in place.

    Unlike create, w/h are stored exactly as given (the edit form's JS
    already swaps them live when orientation flips), so no swap here.
    ``rotation`` (0/90/180/270 or None for auto) is the physical-mount
    turn the renderer + preview apply."""
    device = devices.get(instance_id)
    if device is None or device.kind_of is None:
        return InstanceResult(None, f"Unknown device {instance_id!r}.")
    if w < 1 or h < 1:
        return InstanceResult(None, "Panel width and height must be at least 1px.")
    o = orientation.strip().lower()
    if o not in ("landscape", "portrait"):
        o = "landscape"

    inst_file = device.path
    try:
        raw = json.loads(inst_file.read_text())
    except (OSError, json.JSONDecodeError) as err:
        return InstanceResult(None, f"Couldn't read {inst_file.name}: {err}")
    panel_block = dict(raw.get("panel") or {})
    panel_block["w"], panel_block["h"], panel_block["orientation"] = w, h, o
    if rotation in (0, 90, 180, 270):
        panel_block["rotation"] = rotation
    else:
        panel_block.pop("rotation", None)  # None = auto: drop the key
    raw["panel"] = panel_block
    inst_file.write_text(json.dumps(raw, indent=2) + "\n")

    devices.devices.pop(instance_id, None)
    _drop_clones(renderers, instance_id)
    reloaded = load_instance_file(devices, inst_file=inst_file, data_root=data_root)
    if reloaded is None:
        last_err = devices.errors[-1] if devices.errors else None
        return InstanceResult(None, last_err.message if last_err else "unknown error")
    clone_for_instances(renderers, devices)
    return InstanceResult(reloaded)


def delete_instance(
    *,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    instance_id: str,
) -> InstanceResult:
    """Remove an instance: delete its file, drop its registry record and
    renderer clones. Built-in kinds are refused."""
    device = devices.get(instance_id)
    if device is None:
        return InstanceResult(None, f"Unknown device {instance_id!r}.")
    if device.kind_of is None:
        return InstanceResult(None, f"Cannot delete built-in device kind {instance_id!r}.")
    if device.path.exists():
        device.path.unlink()
    devices.devices.pop(instance_id, None)
    _drop_clones(renderers, instance_id)
    return InstanceResult(device)


def _apply_orientation(panel: dict[str, Any], orientation: str | None) -> None:
    """Stamp orientation onto a panel dict, swapping w/h once for
    portrait so the stored dims match what the user selected."""
    o = (orientation or "").strip().lower()
    if o not in ("portrait", "landscape"):
        return
    panel["orientation"] = o
    if o == "portrait" and panel.get("w") and panel.get("h"):
        panel["w"], panel["h"] = panel["h"], panel["w"]
