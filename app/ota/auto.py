"""Per-device automatic firmware updates (#121 follow-up).

The Firmware page's manual queue is one-shot per release version: setting a
release with a new version resets the offer set, so queueing a device once
never silently opts it into future releases. Devices whose "Automatic
updates" switch is on ride every release instead: they're folded into the
offer set whenever a release is set, and the heartbeat hook below re-checks
on every wake so a newly published release is picked up without anyone
opening the admin UI.

The heartbeat hook reuses the hourly ``firmware_check`` cache, so it costs
at most one outbound availability check per kind per hour, only when online
mode is on. A paused release is never auto-resumed: pause is an explicit
admin stop.
"""

from __future__ import annotations

import logging
from typing import Any

from app import firmware_check as fw_check
from app.online import online_enabled
from app.ota.release import is_newer
from app.ota.service import fetch_descriptor, verify_descriptor
from app.ota.verify import OtaVerificationError

logger = logging.getLogger(__name__)


def auto_update_enabled(settings: Any, device_id: str) -> bool:
    """The per-device switch, from the settings store's devices section."""
    section = (settings.get_section("devices") or {}).get(device_id) or {}
    return bool(section.get("auto_fw_update"))


def auto_update_ids(settings: Any, registry: Any, kind_id: str) -> set[str]:
    """Every device of ``kind_id`` with automatic updates switched on."""
    devs = settings.get_section("devices") or {}
    return {
        d.id
        for d in registry.all()
        if d.kind_of == kind_id and bool((devs.get(d.id) or {}).get("auto_fw_update"))
    }


def freshen_release(
    store: Any,
    kind_id: str,
    rel: dict[str, Any] | None,
    *,
    carryover: list[str],
) -> tuple[dict[str, Any] | None, str | None]:
    """Upgrade the kind's release to the newest published descriptor when the
    update check knows one newer than ``rel`` (or none is imported). The new
    release's offer set becomes ``carryover``. Returns ``(release, error)``
    where ``error`` is None on success or no-op; a failure keeps ``rel``.

    No Flask dependencies: callable from the admin routes (which flash the
    error) and from the heartbeat hook (which only logs it)."""
    info = fw_check.latest_for_kind(kind_id)
    if info is None or not info.version or not info.descriptor_url:
        return rel, None
    if rel is not None and not is_newer(info.version, str(rel.get("fw_version") or "")):
        return rel, None
    try:
        descriptor = fetch_descriptor(info.descriptor_url)
        manifest = verify_descriptor(descriptor)
    except ValueError as exc:
        return rel, f"fetch_failed: {exc}"
    except OtaVerificationError as exc:
        return rel, str(exc.reason)
    if str(manifest["device_kind"]) != kind_id:
        return rel, "kind_mismatch"
    entry: dict[str, Any] = store.set_target(
        kind_id,
        descriptor,
        fw_version=str(manifest["fw_version"]),
        canary_device_ids=sorted(carryover),
    )
    return entry, None


def maybe_auto_queue(app: Any, device: Any) -> None:
    """Heartbeat hook: when this device opted into automatic updates, make
    sure its kind's newest release is set and the device is in the offer
    set, so the same heartbeat's /status response can carry the descriptor.
    Quiet by design: failures log and return, never break a heartbeat."""
    try:
        settings = app.config.get("SETTINGS_STORE")
        store = app.config.get("OTA_RELEASE")
        registry = app.config.get("DEVICE_REGISTRY")
        if settings is None or store is None or getattr(device, "kind_of", None) is None:
            return
        if not auto_update_enabled(settings, device.id):
            return
        kind_id = str(device.kind_of)
        rel = store.get(kind_id)
        if online_enabled(settings):
            auto_ids = (
                auto_update_ids(settings, registry, kind_id) if registry else {str(device.id)}
            )
            rel, err = freshen_release(store, kind_id, rel, carryover=sorted(auto_ids))
            if err:
                logger.debug("auto-update: freshen failed for kind=%s: %s", kind_id, err)
        if rel is None:
            return
        state = rel.get("state")
        if state == "promoted":
            return  # already offered to every device of the kind
        if state == "paused":
            return  # explicit admin stop; never auto-resume
        queued = set(rel.get("canary_device_ids") or [])
        if device.id in queued:
            return
        queued.add(device.id)
        store.set_target(
            kind_id,
            rel["descriptor"],
            fw_version=str(rel["fw_version"]),
            canary_device_ids=sorted(queued),
        )
        logger.info("auto-update: queued %s for %s v%s", device.id, kind_id, rel.get("fw_version"))
    except Exception:
        logger.exception("auto-update hook failed for %s", getattr(device, "id", "?"))
