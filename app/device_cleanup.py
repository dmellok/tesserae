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

import json
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
    # Pages that SURVIVED the wipe but had the device id dropped from their
    # binding, because another live device still shows them (issue #229). Not
    # deleted, just unbound from this id.
    unbound_page_ids: list[str] = field(default_factory=list)
    # History rows that SURVIVED because they were also delivered to another
    # device, with this id dropped from their delivery snapshot (issue #229).
    relabelled_event_count: int = 0

    @property
    def total(self) -> int:
        """Rough total of items the cleanup would touch. Useful for the
        UI to decide whether to expose the checkbox at all (zero means
        the delete is already clean, no need to prompt)."""
        return (
            len(self.page_ids)
            + len(self.unbound_page_ids)
            + self.event_count
            + self.relabelled_event_count
            + self.setting_keys_devices
            + self.setting_keys_renderers
            + (1 if self.has_calibration_image else 0)
            + (1 if self.has_latest_render else 0)
        )


def _is_live(devices: Any | None, device_id: str) -> bool:
    """Whether ``device_id`` still resolves to a registered device. A missing
    registry answers False for everything, which keeps the caller conservative:
    bindings are then judged on presence alone."""
    if devices is None:
        return False
    return getattr(devices, "devices", {}).get(device_id) is not None


def _pages_for_device(
    page_store: PageStore, device_id: str, devices: Any | None = None
) -> list[str]:
    """Page ids the target device exclusively owns: it is on the binding and no
    OTHER device on that binding is still registered.

    The test is deliberately about LIVE co-bindings, not binding length (issue
    #229). Deleting a device leaves its id behind on any page it shared, so a
    length test read ``["deleted_a", "b"]`` as "shared, leave it alone" and never
    offered the page again once b was deleted too. Counting only live co-owners
    means the last real device to go takes the page with it, and a page already
    carrying a dead id self-heals on the next delete.

    Deliberately tolerant about the target itself: the delete route wipes AFTER
    dropping the device from the registry, so the target is usually gone by the
    time this runs. Only the OTHER ids are resolved.

    With no registry there is no way to tell a live co-owner from a dead one, so
    the rule falls back to the conservative one it replaced: ANY other id on the
    binding protects the page. Deleting a page a live device still shows is the
    one outcome worth ruling out, so the ambiguous case keeps it."""
    out: list[str] = []
    for page in page_store.list():
        if device_id not in page.device_ids:
            continue
        others = [d for d in page.device_ids if d != device_id]
        if others and (devices is None or any(_is_live(devices, o) for o in others)):
            continue
        out.append(page.id)
    return out


def _unbind_device_from_pages(
    page_store: PageStore, device_id: str, *, skip: list[str]
) -> list[str]:
    """Drop ``device_id`` from every surviving page that still names it,
    returning the page ids changed. ``skip`` names the pages the caller is
    deleting outright, so a page isn't rewritten on its way out.

    Only ever called from the wipe path. A wipe says "this id is a clean slate",
    and a dashboard still listing a device that no longer exists contradicts
    that: it inflates the dashboards list's link count and, left alone, would
    keep the page from ever being recognised as another device's to delete."""
    changed: list[str] = []
    skipped = set(skip)
    for page in page_store.list():
        if page.id in skipped or device_id not in page.device_ids:
            continue
        page.device_ids = [d for d in page.device_ids if d != device_id]
        page_store.save(page)
        changed.append(page.id)
    return changed


def _candidate_event_rows(conn: Any, device_id: str, page_ids: list[str]) -> list[tuple[Any, ...]]:
    """``(id, target, extra_json)`` for every row that might belong to
    this device: a push at one of its exclusively-bound pages, a row
    whose target carries the id (test patterns, button events), or a row
    whose payload mentions the id at all.

    The last clause is a coarse text match, deliberately. It only has to
    be a superset; :func:`_partition_event_rows` parses each candidate
    and decides. The quotes matter: ``"kitchen"`` does not match
    ``"kitchen-2"``, so a device whose id prefixes another's doesn't drag
    its neighbour's rows in."""
    params: list[Any] = []
    clauses: list[str] = []
    if page_ids:
        placeholders = ",".join(["?"] * len(page_ids))
        clauses.append(f"target IN ({placeholders})")
        params.extend(page_ids)
    if device_id:
        clauses.append("target LIKE ?")
        params.append(f"%:{device_id}")
        clauses.append("extra_json LIKE ?")
        params.append(f'%"{device_id}"%')
    if not clauses:
        return []
    sql = "SELECT id, target, extra_json FROM events WHERE " + " OR ".join(clauses)
    return list(conn.execute(sql, params).fetchall())


def _row_device_ids(extra_json: Any) -> list[str] | None:
    """The devices a push row was actually delivered to, or None when the
    row doesn't record them (rows written before the snapshot existed,
    and every non-push type)."""
    if not isinstance(extra_json, str) or not extra_json:
        return None
    try:
        extra = json.loads(extra_json)
    except (ValueError, TypeError):
        return None
    if not isinstance(extra, dict):
        return None
    raw = extra.get("device_ids")
    if not isinstance(raw, list):
        return None
    return [d for d in raw if isinstance(d, str) and d]


def _partition_event_rows(
    rows: list[tuple[Any, ...]], device_id: str, page_ids: list[str]
) -> tuple[list[int], list[tuple[int, str]]]:
    """Split candidate rows into ``(delete_ids, relabel)``.

    A push row names its page in ``target`` and the devices it was
    delivered to in ``extra.device_ids``. Scoping the wipe on ``target``
    alone can only ever be page-shaped, which got both halves wrong on a
    dashboard shared between two panels (#229): deleting the first device
    left its solo pushes behind, and deleting the second took the whole
    dashboard's history including the first device's rows.

    So the device's own rows are found by payload:

    * every row for a page being deleted goes, whatever it names, because
      the dashboard itself is going;
    * a row delivered only to this device goes;
    * a row delivered to this device *and* others stays, with the id
      dropped from its snapshot, so History keeps the entry for the
      devices that still exist and stops chipping it with a dead one;
    * a row that records no devices is left to the page rule alone.
    """
    page_set = set(page_ids)
    delete_ids: list[int] = []
    relabel: list[tuple[int, str]] = []
    for row_id, target, extra_json in rows:
        if target in page_set:
            delete_ids.append(int(row_id))
            continue
        if isinstance(target, str) and device_id and target.endswith(f":{device_id}"):
            delete_ids.append(int(row_id))
            continue
        devices_on_row = _row_device_ids(extra_json)
        if not devices_on_row or device_id not in devices_on_row:
            continue
        remaining = [d for d in devices_on_row if d != device_id]
        if not remaining:
            delete_ids.append(int(row_id))
            continue
        extra = json.loads(extra_json)
        extra["device_ids"] = remaining
        relabel.append((int(row_id), json.dumps(extra, default=str)))
    return delete_ids, relabel


def _event_count_for_device(event_log: EventLog, device_id: str, page_ids: list[str]) -> int:
    """Count of event rows the wipe would delete. Relabelled rows are not
    counted here: they survive, and telling the operator a shared push is
    about to be deleted when it isn't would be the wrong warning."""
    if not page_ids and not device_id:
        return 0
    with event_log._lock, event_log._conn() as conn:
        rows = _candidate_event_rows(conn, device_id, page_ids)
    deleted, _relabel = _partition_event_rows(rows, device_id, page_ids)
    return len(deleted)


def _relabel_count_for_device(event_log: EventLog, device_id: str, page_ids: list[str]) -> int:
    """How many surviving rows would have this device dropped from their
    delivery snapshot."""
    if not page_ids and not device_id:
        return 0
    with event_log._lock, event_log._conn() as conn:
        rows = _candidate_event_rows(conn, device_id, page_ids)
    _deleted, relabel = _partition_event_rows(rows, device_id, page_ids)
    return len(relabel)


def _delete_events_for_device(
    event_log: EventLog, device_id: str, page_ids: list[str]
) -> tuple[int, int]:
    """Apply :func:`_partition_event_rows`. Returns
    ``(rows_deleted, rows_relabelled)``."""
    if not page_ids and not device_id:
        return 0, 0
    with event_log._lock, event_log._conn() as conn:
        rows = _candidate_event_rows(conn, device_id, page_ids)
        delete_ids, relabel = _partition_event_rows(rows, device_id, page_ids)
        if delete_ids:
            placeholders = ",".join(["?"] * len(delete_ids))
            conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", delete_ids)
        for row_id, extra_json in relabel:
            conn.execute("UPDATE events SET extra_json = ? WHERE id = ?", (extra_json, row_id))
        conn.commit()
    return len(delete_ids), len(relabel)


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
    devices: Any | None = None,
) -> OrphanSummary:
    """Snapshot the state a device_id currently owns.

    ``devices`` is the live :class:`app.device_loader.DeviceRegistry`. Passing it
    lets a page co-bound to an already-deleted device count as this device's to
    delete (issue #229); without it the summary falls back to counting only
    pages bound to this device alone."""
    pages = _pages_for_device(page_store, device_id, devices)
    events = _event_count_for_device(event_log, device_id, pages)
    relabelled = _relabel_count_for_device(event_log, device_id, pages)
    devices_keys, renderers_keys = _count_settings_for_device(settings_store, device_id)
    calibration = _calibration_image_path(data_root, device_id).exists()
    unbound = [
        page.id
        for page in page_store.list()
        if page.id not in pages and device_id in page.device_ids
    ]
    return OrphanSummary(
        device_id=device_id,
        page_ids=pages,
        event_count=events,
        relabelled_event_count=relabelled,
        setting_keys_devices=devices_keys,
        setting_keys_renderers=renderers_keys,
        has_calibration_image=calibration,
        unbound_page_ids=unbound,
    )


def wipe_orphan_state(
    *,
    device_id: str,
    page_store: PageStore,
    event_log: EventLog,
    settings_store: SettingsStore,
    data_root: Path,
    push_manager: Any | None = None,
    devices: Any | None = None,
) -> OrphanSummary:
    """Remove per-device leftovers idempotently and return a summary of
    what was actually deleted. Safe to call after
    :func:`app.device_service.delete_instance`; caller is responsible
    for rebuilding the transport / renderer registry.

    ``push_manager`` is the live :class:`app.push.PushManager`. Passing it drops
    the device's frame pointers, without which a device re-registered under the
    same id is served the frame from before the wipe (issue #199). Optional so
    a caller with no manager wired (tests, CLI) still works.

    ``devices`` is the live :class:`app.device_loader.DeviceRegistry`, used to
    tell a page this device exclusively owns from one another LIVE device still
    shows (issue #229). Pages kept for a live co-owner have this device's id
    dropped from their binding rather than being left pointing at a device that
    no longer exists."""
    pages = _pages_for_device(page_store, device_id, devices)
    removed_events, relabelled_events = _delete_events_for_device(event_log, device_id, pages)
    for pid in pages:
        page_store.delete(pid)
    unbound = _unbind_device_from_pages(page_store, device_id, skip=pages)
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
        relabelled_event_count=relabelled_events,
        setting_keys_devices=devices_removed,
        setting_keys_renderers=renderers_removed,
        has_calibration_image=calibration_removed,
        has_latest_render=render_forgotten,
        unbound_page_ids=unbound,
    )
