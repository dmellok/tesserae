"""Device orphan-state accounting + wipe (v0.69.2, issue #48).

When a device is deleted, its manifest + registry entry go, but per-
device state scattered across pages, event log, settings-store, and
the calibration-images dir sticks around. If a new device is later
registered with the same id (Bernhard's scenario in issue #48), it
inherits the previous device's dashboards, history, and settings.
Sometimes that's what the user wants (re-registering the same
physical device); often it isn't (different physical device happening
to reuse the id).

This module offers two operations:

* :func:`list_orphan_state` returns counts + summaries of what a
  given device_id currently owns across every store. Used by the
  delete-confirm modal so the user can see what they're about to
  keep or wipe.

* :func:`wipe_orphan_state` deletes the leftovers idempotently. Safe
  to call after :func:`app.device_service.delete_instance` (which
  drops the manifest + registry entry + renderer clones) so the id
  is a clean slate for the next register.

The wipe surface intentionally excludes rotations, schedules, and
transport wiring: the caller is expected to rebuild those upstream
via ``rebuild_transport_fn()`` after cleanup. Pointer references to
wiped pages (a rotation referring to a deleted page) surface as
warnings in the scheduler log rather than errors, matching the
current behaviour for admin-deleted pages.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.state.event_log import EventLog
from app.state.page_store import PageStore
from app.state.settings_store import SettingsStore


@dataclass(frozen=True)
class OrphanSummary:
    """What a device_id currently owns across the stores. Counts are
    non-negative; empty lists / zeros mean nothing to wipe."""

    device_id: str
    page_ids: list[str] = field(default_factory=list)
    event_count: int = 0
    setting_keys_devices: int = 0
    setting_keys_renderers: int = 0
    has_calibration_image: bool = False
    # Whether the push manager still held a frame for this id. Renders are
    # content-addressed, so a surviving pointer means a re-registered device is
    # served the pre-wipe frame rather than a 204 (issue #199).
    has_latest_render: bool = False

    @property
    def total(self) -> int:
        """Rough total of items the cleanup would touch. Useful for the
        UI to decide whether to expose the checkbox at all (zero means
        the delete is already clean, no need to prompt)."""
        return (
            len(self.page_ids)
            + self.event_count
            + self.setting_keys_devices
            + self.setting_keys_renderers
            + (1 if self.has_calibration_image else 0)
            + (1 if self.has_latest_render else 0)
        )


def _pages_for_device(page_store: PageStore, device_id: str) -> list[str]:
    """Page ids whose device_ids binding names the target device.
    Pages bound to multiple devices are only listed if the target is
    the *only* device on the binding, otherwise wiping the page would
    unbind an unrelated device that still exists."""
    out: list[str] = []
    for page in page_store.list():
        if device_id in page.device_ids and len(page.device_ids) == 1:
            out.append(page.id)
    return out


def _event_count_for_device(event_log: EventLog, device_id: str, page_ids: list[str]) -> int:
    """Count of event rows the wipe would delete: pushes targeting one
    of the device's exclusively-bound pages, plus rows whose target
    was labelled with the device id (test patterns, button events)."""
    if not page_ids and not device_id:
        return 0
    with event_log._lock, event_log._conn() as conn:
        params: list[Any] = []
        clauses: list[str] = []
        if page_ids:
            placeholders = ",".join(["?"] * len(page_ids))
            clauses.append(f"target IN ({placeholders})")
            params.extend(page_ids)
        if device_id:
            clauses.append("target LIKE ?")
            params.append(f"%:{device_id}")
        if not clauses:
            return 0
        sql = "SELECT COUNT(*) FROM events WHERE " + " OR ".join(clauses)
        row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def _delete_events_for_device(event_log: EventLog, device_id: str, page_ids: list[str]) -> int:
    """Deletion counterpart of :func:`_event_count_for_device`. Returns
    the row count actually removed."""
    if not page_ids and not device_id:
        return 0
    with event_log._lock, event_log._conn() as conn:
        params: list[Any] = []
        clauses: list[str] = []
        if page_ids:
            placeholders = ",".join(["?"] * len(page_ids))
            clauses.append(f"target IN ({placeholders})")
            params.extend(page_ids)
        if device_id:
            clauses.append("target LIKE ?")
            params.append(f"%:{device_id}")
        if not clauses:
            return 0
        sql = "DELETE FROM events WHERE " + " OR ".join(clauses)
        cur = conn.execute(sql, params)
        conn.commit()
        return int(cur.rowcount)


def _renderer_clone_prefix(device_id: str) -> str:
    """Renderer clones for a device are keyed ``<base>__<device_id>``.
    The wipe finds them by suffix rather than iterating the renderer
    registry (which by the time we wipe has already dropped the
    clones)."""
    return f"__{device_id}"


def _count_settings_for_device(store: SettingsStore, device_id: str) -> tuple[int, int]:
    """Return ``(devices_ns_keys, renderers_ns_keys)`` for the device.
    Not surface-level "how many fields", just how many top-level entries
    the wipe would touch; the UI uses it as a rough "is there anything
    left" indicator."""
    devices_ns = store.get_section("devices") or {}
    devices_count = 1 if isinstance(devices_ns, dict) and device_id in devices_ns else 0
    renderers_ns = store.get_section("renderers") or {}
    suffix = _renderer_clone_prefix(device_id)
    if isinstance(renderers_ns, dict):
        renderers_count = sum(
            1 for key in renderers_ns if isinstance(key, str) and key.endswith(suffix)
        )
    else:
        renderers_count = 0
    return devices_count, renderers_count


def _wipe_settings_for_device(store: SettingsStore, device_id: str) -> tuple[int, int]:
    """Delete both the device's own settings namespace and every
    renderer-clone namespace whose key ends with ``__<device_id>``.
    Returns the same shape as :func:`_count_settings_for_device` for
    caller reporting."""
    devices_removed = 0
    devices_ns = store.get_section("devices") or {}
    if isinstance(devices_ns, dict) and device_id in devices_ns:
        new_devices = {k: v for k, v in devices_ns.items() if k != device_id}
        store.update_section("devices", new_devices)
        devices_removed = 1
    renderers_removed = 0
    renderers_ns = store.get_section("renderers") or {}
    suffix = _renderer_clone_prefix(device_id)
    if isinstance(renderers_ns, dict):
        keep = {
            k: v for k, v in renderers_ns.items() if not (isinstance(k, str) and k.endswith(suffix))
        }
        renderers_removed = len(renderers_ns) - len(keep)
        if renderers_removed:
            store.update_section("renderers", keep)
    return devices_removed, renderers_removed


def _calibration_image_path(data_root: Path, device_id: str) -> Path:
    return data_root / "calibration_images" / f"{device_id}.png"


def list_orphan_state(
    *,
    device_id: str,
    page_store: PageStore,
    event_log: EventLog,
    settings_store: SettingsStore,
    data_root: Path,
) -> OrphanSummary:
    """Snapshot the state a device_id currently owns."""
    pages = _pages_for_device(page_store, device_id)
    events = _event_count_for_device(event_log, device_id, pages)
    devices_keys, renderers_keys = _count_settings_for_device(settings_store, device_id)
    calibration = _calibration_image_path(data_root, device_id).exists()
    return OrphanSummary(
        device_id=device_id,
        page_ids=pages,
        event_count=events,
        setting_keys_devices=devices_keys,
        setting_keys_renderers=renderers_keys,
        has_calibration_image=calibration,
    )


def wipe_orphan_state(
    *,
    device_id: str,
    page_store: PageStore,
    event_log: EventLog,
    settings_store: SettingsStore,
    data_root: Path,
    push_manager: Any | None = None,
) -> OrphanSummary:
    """Remove per-device leftovers idempotently and return a summary of
    what was actually deleted. Safe to call after
    :func:`app.device_service.delete_instance`; caller is responsible
    for rebuilding the transport / renderer registry.

    ``push_manager`` is the live :class:`app.push.PushManager`. Passing it drops
    the device's frame pointers, without which a device re-registered under the
    same id is served the frame from before the wipe (issue #199). Optional so
    a caller with no manager wired (tests, CLI) still works."""
    pages = _pages_for_device(page_store, device_id)
    removed_events = _delete_events_for_device(event_log, device_id, pages)
    for pid in pages:
        page_store.delete(pid)
    devices_removed, renderers_removed = _wipe_settings_for_device(settings_store, device_id)
    calibration_removed = False
    path = _calibration_image_path(data_root, device_id)
    if path.exists():
        path.unlink()
        calibration_removed = True
    render_forgotten = False
    forget = getattr(push_manager, "forget_device", None)
    if callable(forget):
        render_forgotten = bool(forget(device_id))
    return OrphanSummary(
        device_id=device_id,
        page_ids=pages,
        event_count=removed_events,
        setting_keys_devices=devices_removed,
        setting_keys_renderers=renderers_removed,
        has_calibration_image=calibration_removed,
        has_latest_render=render_forgotten,
    )
