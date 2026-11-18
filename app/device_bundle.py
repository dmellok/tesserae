"""Displays in an export file (#541): the device half of :mod:`app.bundle`.

A bundle may carry a ``devices`` list beside its dashboards and lineups::

    "devices": [
      {
        "id": "kitchen",
        "kind": "esp32_client",
        "name": "Kitchen",
        "device": {"panel": {...}, "quiet_hours": {...}, "locale": "en-GB"},
        "settings": {"sleep_interval_s": 900, "button_map": {...}},
        "identity": {"access_token": "...", "mac": "AA:BB:..."}
      }
    ]

``device`` is the part of the instance file that describes the display, and
``settings`` its entry in the ``devices`` section of ``settings.json``. Both
travel by default. ``identity`` is the access token, MAC and friendly id, and
is only written when the operator asks for it: with it, the file lets whoever
holds it connect to a server as that display, and lets a display be moved
between two servers by changing nothing but the address it polls.

What never travels, with or without identity: a relay panel's frame key (it
belongs to one pairing through the relay), encrypted settings (wrapped with a
per-install key), anything that looks like a credential in the settings
entry, and what the server learned about the hardware at run time.

Import adds a display that is not on this server, or, when the operator ticks
Replace, applies the file's settings to the one already here. Replacing never
changes the access token of a display this server already knows: a display
polling this server would be locked out by it.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app import device_service
from app.device_loader import DeviceRegistry, load_instance_file
from app.renderer_loader import RendererRegistry, clone_for_instances
from app.secret_box import is_wrapped

# Instance-file keys that describe the display rather than one install's
# record of it. A whitelist, so a key added later stays put until someone
# decides it should travel.
_DEVICE_KEYS = ("icon", "panel", "quiet_hours", "locale", "battery_offset", "renderer_id")
_IDENTITY_KEYS = ("access_token", "mac", "friendly_id")
# Settings that belong to one account or one install even though nothing in
# their name says so.
_LOCAL_SETTINGS = frozenset(
    {"cloud_device_id", "meural_management_paused", "meural_playback_unrestored"}
)
_CREDENTIAL_RE = re.compile(r"token|secret|password|passwd|session|credential|api_?key", re.I)
_MAX_SETTINGS_BYTES = 256 * 1024
_MAX_DEVICES = 500


def _credential_like(key: str, value: Any) -> bool:
    if key.endswith("_secret") or key in _LOCAL_SETTINGS or _CREDENTIAL_RE.search(key):
        return True
    return _holds_wrapped(value)


def _holds_wrapped(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_holds_wrapped(v) for v in value.values())
    if isinstance(value, list):
        return any(_holds_wrapped(v) for v in value)
    return isinstance(value, str) and is_wrapped(value)


def safe_settings(entry: Any) -> tuple[dict[str, Any], list[str]]:
    """The part of a display's settings entry that can leave this server, and
    the names of the keys held back. Used on export and again on import: a
    file is never trusted to have been through the first pass."""
    kept: dict[str, Any] = {}
    dropped: list[str] = []
    if not isinstance(entry, dict):
        return kept, dropped
    for key, value in entry.items():
        if not isinstance(key, str) or not key:
            continue
        if _credential_like(key, value):
            dropped.append(key)
        else:
            kept[key] = value
    return kept, sorted(dropped)


# -- export ------------------------------------------------------------------


def export_devices(
    *,
    device_ids: list[str],
    devices: DeviceRegistry,
    settings: Any,
    include_identity: bool,
    notes: list[str],
) -> list[dict[str, Any]]:
    """Bundle entries for those displays. Kinds and unknown ids are skipped."""
    section = settings.get_section("devices") or {}
    out: list[dict[str, Any]] = []
    for device_id in dict.fromkeys(device_ids):
        device = devices.get(device_id)
        if device is None or device.kind_of is None:
            notes.append(f"display {device_id} no longer exists and was not exported")
            continue
        try:
            raw = json.loads(device.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            notes.append(f"display {device.name}: its file could not be read; not exported")
            continue
        if not isinstance(raw, dict):
            continue
        entry: dict[str, Any] = {
            "id": device.id,
            "kind": str(raw.get("kind") or device.kind_of),
            "name": str(raw.get("name") or device.name),
        }
        transport = str(raw.get("transport") or "").strip().lower()
        if transport:
            entry["transport"] = transport
        entry["device"] = {k: raw[k] for k in _DEVICE_KEYS if raw.get(k) not in (None, "", {})}
        kept, dropped = safe_settings(section.get(device.id) if isinstance(section, dict) else None)
        if kept:
            entry["settings"] = kept
        for key in dropped:
            notes.append(f"display {entry['name']}: left out the {key!r} setting")
        if include_identity:
            if transport == "relay":
                notes.append(
                    f"display {entry['name']}: a relay panel's pairing can't be exported; "
                    "only its settings are in the file"
                )
            else:
                identity = {
                    k: raw[k].strip()
                    for k in _IDENTITY_KEYS
                    if isinstance(raw.get(k), str) and raw[k].strip()
                }
                if identity:
                    entry["identity"] = identity
        out.append(entry)
    return out


def has_identity(bundle: dict[str, Any]) -> bool:
    return any(
        isinstance(d, dict) and isinstance(d.get("identity"), dict) and d["identity"]
        for d in bundle.get("devices") or []
    )


# -- import ------------------------------------------------------------------


def check_devices(entries: Any) -> str | None:
    """Why the ``devices`` list of an uploaded file can't be read, or None.
    Shape only: each display is judged on its own when the file is applied."""
    if not isinstance(entries, list) or len(entries) > _MAX_DEVICES:
        return "the file's list of displays is unreadable"
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return "a display in the file is unreadable"
        device_id = entry.get("id")
        if not isinstance(device_id, str) or not device_service.DEVICE_ID_RE.match(device_id):
            return "a display in the file has no usable id"
        if device_id in seen:
            return f"the file lists display {device_id} twice"
        seen.add(device_id)
        if not isinstance(entry.get("kind"), str) or not entry["kind"]:
            return f"display {device_id} in the file has no kind"
        for key in ("device", "settings", "identity"):
            if not isinstance(entry.get(key, {}), dict):
                return f"display {device_id} in the file is unreadable"
        if len(json.dumps(entry.get("settings") or {})) > _MAX_SETTINGS_BYTES:
            return f"display {device_id} in the file has more settings than a display can hold"
    return None


def _identity(entry: dict[str, Any]) -> dict[str, str]:
    raw = entry.get("identity") or {}
    return {
        k: raw[k].strip()
        for k in _IDENTITY_KEYS
        if isinstance(raw.get(k), str) and raw[k].strip() and len(raw[k]) <= 512
    }


def _others_with(devices: DeviceRegistry, key: str, value: str, *, exclude: str) -> str | None:
    """The id of another display whose ``key`` (a token or a MAC) is ``value``."""
    wanted = value.strip().lower()
    for other in devices.devices.values():
        if other.kind_of is None or other.id == exclude:
            continue
        held = other.manifest.get(key)
        if isinstance(held, str) and held.strip().lower() == wanted:
            return str(other.id)
    return None


def _verdict(entry: dict[str, Any], devices: DeviceRegistry) -> tuple[str, str]:
    """``(action, why)`` for one display in a file: ``add``, ``replace`` (only
    if the operator ticks it) or ``skip``."""
    device_id = entry["id"]
    kind = devices.get(entry["kind"])
    if kind is None or kind.kind_of is not None:
        return "skip", f"this server has no display type called {entry['kind']}"
    existing = devices.get(device_id)
    if existing is not None:
        if existing.kind_of is None:
            return "skip", "its id is the name of a display type here"
        if existing.kind_of != entry["kind"]:
            return "skip", f"a display with this id is already here as a {existing.kind_of}"
        return "replace", ""
    if str(entry.get("transport") or "") == "relay":
        return "skip", "a relay panel has to be paired through the relay on this server first"
    identity = _identity(entry)
    for key, label in (("access_token", "access token"), ("mac", "MAC address")):
        if key in identity:
            clash = _others_with(devices, key, identity[key], exclude=device_id)
            if clash:
                return "skip", f"its {label} already belongs to the display {clash} here"
    return "add", ""


def describe_devices(bundle: dict[str, Any], devices: DeviceRegistry) -> list[dict[str, Any]]:
    """What importing would do with each display, for the confirmation page."""
    rows = []
    for entry in bundle.get("devices") or []:
        action, why = _verdict(entry, devices)
        existing = devices.get(entry["id"])
        kind = devices.get(entry["kind"])
        _kept, dropped = safe_settings(entry.get("settings"))
        rows.append(
            {
                "id": entry["id"],
                "name": str(entry.get("name") or entry["id"]),
                "kind": kind.name if kind is not None else entry["kind"],
                "action": action,
                "why": why,
                "existing_name": existing.name if existing is not None else "",
                "has_identity": bool(_identity(entry)),
                "needs_pairing": action == "add"
                and not _identity(entry).get("access_token")
                and _mints_token(kind, entry),
                "dropped": dropped,
            }
        )
    return rows


def _mints_token(kind: Any, entry: dict[str, Any]) -> bool:
    if kind is None:
        return False
    transport = str(entry.get("transport") or kind.manifest.get("transport") or "").lower()
    return transport in ("rest", "relay") or device_service._kind_uses_access_token(kind)


@dataclass
class DeviceReport:
    added: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    # Added without an access token of their own: the server made one, and
    # the display has to be given it.
    needs_pairing: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.added) + len(self.replaced)


def _overlay(raw: dict[str, Any], entry: dict[str, Any], kind: Any) -> None:
    """Lay the file's description of a display over an instance file's JSON."""
    carried = entry.get("device") or {}
    for key in _DEVICE_KEYS:
        if key not in carried:
            continue
        value = carried[key]
        if key in ("panel", "quiet_hours", "battery_offset"):
            if isinstance(value, dict):
                raw[key] = value
        elif key == "renderer_id":
            if isinstance(value, str) and value in getattr(kind, "renderer_ids", []):
                raw[key] = value
        elif isinstance(value, str) and value.strip() and len(value) <= 200:
            raw[key] = value.strip()
    name = entry.get("name")
    if isinstance(name, str) and name.strip():
        raw["name"] = name.strip()[:120]


def _reload(
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    inst_file: Path,
    instance_id: str,
) -> Any:
    devices.devices.pop(instance_id, None)
    device_service._drop_clones(renderers, instance_id)
    device = load_instance_file(devices, inst_file=inst_file, data_root=data_root)
    if device is not None:
        clone_for_instances(renderers, devices)
    return device


@device_service.holds_instance_file_lock
def apply_devices(
    bundle: dict[str, Any],
    *,
    replace: set[str],
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    settings: Any,
) -> DeviceReport:
    """Add or update the displays in ``bundle``. Each display stands alone: one
    that can't be imported is reported and the rest go ahead. The caller
    rebuilds the transport when anything changed."""
    report = DeviceReport()
    for entry in bundle.get("devices") or []:
        name = str(entry.get("name") or entry["id"])
        action, why = _verdict(entry, devices)
        if action == "skip":
            report.skipped.append(f"display {name}: {why}")
            continue
        kind = devices.get(entry["kind"])
        kept, _dropped = safe_settings(entry.get("settings"))
        if action == "replace":
            if entry["id"] not in replace:
                report.skipped.append(f"display {name}: it is already here and was left as it is")
                continue
            problem = _replace(entry, kind, devices, renderers, data_root)
            if problem:
                report.skipped.append(f"display {name}: {problem}")
                continue
            if kept:
                settings.update_item("devices", entry["id"], kept)
            report.replaced.append(name)
            continue
        problem, minted = _add(entry, kind, devices, renderers, data_root, settings, kept)
        if problem:
            report.skipped.append(f"display {name}: {problem}")
            continue
        report.added.append(name)
        if minted:
            report.needs_pairing.append(name)
    return report


def _replace(
    entry: dict[str, Any],
    kind: Any,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
) -> str | None:
    """Apply the file's settings to the display already here. Its access
    token, MAC and transport are left alone."""
    existing = devices.get(entry["id"])
    if existing is None:
        return "it is no longer here"
    inst_file = existing.path
    try:
        before = inst_file.read_text(encoding="utf-8")
        raw = json.loads(before)
    except (OSError, ValueError):
        return "its file on this server could not be read"
    if not isinstance(raw, dict):
        return "its file on this server could not be read"
    _overlay(raw, entry, kind)
    inst_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    if _reload(devices, renderers, data_root, inst_file, entry["id"]) is None:
        # Put the display back exactly as it was.
        inst_file.write_text(before, encoding="utf-8")
        _reload(devices, renderers, data_root, inst_file, entry["id"])
        return "the file's settings for it are not ones this server accepts"
    return None


def _add(
    entry: dict[str, Any],
    kind: Any,
    devices: DeviceRegistry,
    renderers: RendererRegistry,
    data_root: Path,
    settings: Any,
    kept: dict[str, Any],
) -> tuple[str | None, bool]:
    """Create the display. Returns ``(problem, minted)``: ``minted`` is True
    when the server had to make an access token because the file had none."""
    identity = _identity(entry)
    transport = str(entry.get("transport") or "")
    result = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id=entry["id"],
        kind_id=entry["kind"],
        name=str(entry.get("name") or ""),
        access_token=identity.get("access_token"),
        mac=identity.get("mac"),
        transport=transport if transport == "rest" else None,
    )
    if not result.ok or result.device is None:
        return (result.error or "it could not be added").rstrip("."), False
    inst_file = result.device.path
    raw = json.loads(inst_file.read_text(encoding="utf-8"))
    _overlay(raw, entry, kind)
    has_token = isinstance(raw.get("access_token"), str) and bool(raw["access_token"])
    if has_token:
        if identity.get("mac"):
            raw["mac"] = identity["mac"]
        if identity.get("friendly_id") and "friendly_id" in raw:
            raw["friendly_id"] = identity["friendly_id"]
    carried_token = has_token and bool(identity.get("access_token"))
    if carried_token:
        # The display already holds this token. Its MAC must not be able to
        # collect it again from /discover (#515).
        from app import mac_claim

        raw[mac_claim.CLOSED_KEY] = True
    inst_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    device = _reload(devices, renderers, data_root, inst_file, entry["id"])
    if device is None:
        with contextlib.suppress(OSError):
            inst_file.unlink()
        devices.devices.pop(entry["id"], None)
        device_service._drop_clones(renderers, entry["id"])
        return "the file's settings for it are not ones this server accepts", False
    if kept:
        settings.update_item("devices", entry["id"], kept)
    device_service.device_added(settings, device)
    return None, has_token and not carried_token
