"""Settings -> Firmware: the OTA rollout admin UI (issue #121).

A thin, guard-railed wrapper over the same rollout state the CLI writes
(`app.ota.release`), reading/writing `<data-root>/core/ota_releases.json` through
`OtaReleaseStore`, never a parallel store. The page shows, per device kind: the
current release + its verified manifest, rollout controls (set canary / promote /
pause), and a fleet status view driven by the heartbeat OTA reports the server
already stores. Descriptors are verified against the published trust anchor
before they can be offered; the UI never touches the image bytes.

v0.180.0 flattens the UI to a single device list: each device shows its
installed firmware, what's available, and a queue-for-update / withdraw
button. Queueing rides the release store's per-device offer set (the CLI's
"canary" list). The fleet-level canary / promote / pause controls live in a
collapsed Advanced section (and in the CLI) for multi-device rollouts.
"""

from __future__ import annotations

import json
import time
from typing import Any

from flask import current_app, flash, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from app import firmware_check as fw_check
from app.online import online_enabled
from app.ota import auto as ota_auto
from app.ota.release import is_newer
from app.ota.service import verify_descriptor
from app.ota.verify import OtaVerificationError
from app.settings.index_routes import _OTA_PILL_CLASS

from ._shared import (
    bp,
    device_kinds,
    device_status,
    devices,
    events,
    format_relative,
    settings_store,
)


def _release_store() -> Any:
    return current_app.config["OTA_RELEASE"]


def _known_kind_ids() -> set[str]:
    """Every registered device kind id (built-in + catalog-derived)."""
    return {k.id for k in device_kinds()}


def _kind_names() -> dict[str, str]:
    return {k.id: k.display_name for k in device_kinds()}


def _log(action: str, kind_id: str, status: str, **extra: Any) -> None:
    """Audit every rollout action to the shared event log (who/when/what)."""
    events().record(
        type="ota",
        source=action,
        target=kind_id,
        status=status,
        extra={"remote_addr": request.remote_addr or "(unknown)", **extra},
    )


def _ota_capable(device_id: str) -> bool:
    """Whether the device has ever advertised the OTA capability: the live
    status cache first, then the persisted facts (so a restart doesn't
    demote a sleeping device to USB-only). Descriptor delivery on /status
    still requires the live handshake; this only gates the admin UI."""
    if (device_status().get(device_id) or {}).get("ota_schema") is not None:
        return True
    facts_store = current_app.config.get("DEVICE_FACTS")
    facts = facts_store.get(device_id) if facts_store is not None else None
    return bool(facts and facts.get("ota_schema") is not None)


def _device_view(device: Any, rel_fw: str | None, canary_ids: set[str]) -> dict[str, Any]:
    entry = device_status().get(device.id) or {}
    parsed = entry.get("parsed") or {}
    report_raw = entry.get("ota")
    report: dict[str, Any] = report_raw if isinstance(report_raw, dict) else {}
    phase = report.get("phase")
    fw = parsed.get("fw_version")
    fw_str = str(fw) if isinstance(fw, (str, int, float)) else None
    capable = entry.get("ota_schema") is not None
    # The live status cache is in-memory; until a sleeping device's next
    # heartbeat after a restart, fall back to the persisted facts so the row
    # doesn't misreport an OTA-capable device as USB-only (or lose its fw).
    facts_store = current_app.config.get("DEVICE_FACTS")
    facts = (facts_store.get(device.id) if facts_store is not None else None) or {}
    if fw_str is None and facts.get("fw_version"):
        fw_str = str(facts["fw_version"])
    if not capable and facts.get("ota_schema") is not None:
        capable = True
    needs_attention = phase in ("failed", "rolled_back")
    report_at = report.get("received_at")
    return {
        "id": device.id,
        "name": device.display_name,
        "fw_version": fw_str,
        "capable": capable,
        # False only when we know nothing at all about this device (no live
        # heartbeat this process AND no persisted facts): the UI then says
        # "awaiting first heartbeat" instead of claiming USB-only.
        "known": bool(entry) or bool(facts),
        "phase": phase,
        "reason": report.get("reason"),
        "target_fw": report.get("target_fw"),
        "detail": report.get("detail"),
        "pill_class": _OTA_PILL_CLASS.get(str(phase), "is-accent") if phase else None,
        "report_relative": (
            format_relative(max(0.0, time.time() - float(report_at)))
            if isinstance(report_at, (int, float))
            else None
        ),
        "is_canary": device.id in canary_ids,
        "needs_attention": needs_attention,
        # Confirmed on exactly the release the kind is rolling out.
        "confirmed_on_release": bool(
            rel_fw and phase == "confirmed" and report.get("target_fw") == rel_fw
        ),
    }


def _queued_ids(rel: dict[str, Any], kind_id: str) -> set[str]:
    """The devices currently offered the kind's release. A promoted release
    (set via the CLI) offers to every device of the kind, so materialise
    that as the full capable set; the UI then edits it as a plain
    per-device list. Paused means nothing is offered."""
    state = rel.get("state")
    if state == "paused":
        return set()
    if state == "promoted":
        return {d.id for d in devices().all() if d.kind_of == kind_id and _ota_capable(d.id)}
    return set(rel.get("canary_device_ids") or [])


def _latest_view(kind_id: str, rel_fw: str | None, dev_views: list[dict[str, Any]]) -> Any:
    """The latest published firmware for a kind from api.tesserae.ink, plus
    whether it's newer than what's deployed. Only called when online mode is on
    (this is the outbound check); the result is cached for an hour."""
    # "Deployed" baseline: the imported release if any, else the newest firmware
    # version a device of this kind reports. A newer published version means a
    # descriptor is worth importing. Sent as ``current`` for version telemetry.
    deployed = rel_fw or ""
    if not deployed:
        reported = [v["fw_version"] for v in dev_views if v["fw_version"]]
        for fw in reported:
            if not deployed or is_newer(str(fw), deployed):
                deployed = str(fw)
    info = fw_check.latest_for_kind(kind_id, current=deployed)
    if info is None or not info.version:
        return None
    return {
        "version": info.version,
        "url": info.url,
        "notes_headline": info.notes_headline,
        "descriptor_url": info.descriptor_url,
        "update_available": bool(deployed) and is_newer(info.version, deployed),
    }


def _devices_model(*, online: bool) -> list[dict[str, Any]]:
    """One row per registered device instance across every kind: the unified
    list the page renders. "Available" is the imported release or, when
    online and its descriptor is fetchable, the newer published version
    (queueing then imports it first)."""
    reg = devices()
    releases = _release_store().all()
    names = _kind_names()

    by_kind: dict[str, list[Any]] = {}
    for d in reg.all():
        if d.kind_of is None:
            continue
        by_kind.setdefault(d.kind_of, []).append(d)

    device_settings = settings_store().get_section("devices") or {}
    rows: list[dict[str, Any]] = []
    for kind_id in sorted(by_kind):
        rel = releases.get(kind_id) if isinstance(releases.get(kind_id), dict) else None
        rel_fw = str(rel["fw_version"]) if rel and rel.get("fw_version") else None
        queued_ids = _queued_ids(rel, kind_id) if rel else set()

        dev_views = [_device_view(d, rel_fw, queued_ids) for d in by_kind[kind_id]]
        latest = _latest_view(kind_id, rel_fw, dev_views) if online else None

        # What the row's Available column shows: the newest of the imported
        # release and the published latest. Queueing needs something it can
        # actually offer: an imported release, or a fetchable descriptor.
        available = rel_fw
        if latest and (not available or is_newer(str(latest["version"]), available)):
            available = str(latest["version"])
        offerable = rel is not None or bool(latest and latest.get("descriptor_url"))

        for v in dev_views:
            fw = str(v["fw_version"] or "")
            update_available = bool(available) and (not fw or is_newer(str(available), fw))
            # Rollout membership is retained after a device updates so the
            # Fleet view can still identify its canaries.  The flat device
            # list, however, should only call that membership "queued" while
            # the imported release can actually be offered to this device.
            release_pending = bool(rel_fw) and (not fw or is_newer(str(rel_fw), fw))
            queued = bool(v["is_canary"] and release_pending)
            rows.append(
                {
                    **v,
                    "kind_id": kind_id,
                    "kind_name": names.get(kind_id, kind_id),
                    "queued": queued,
                    "queued_fw": rel_fw if queued else None,
                    "available_fw": available,
                    "release_url": str(latest["url"]) if latest and latest.get("url") else None,
                    "notes_headline": (
                        str(latest["notes_headline"])
                        if latest and latest.get("notes_headline")
                        else None
                    ),
                    "update_available": update_available,
                    "auto": bool((device_settings.get(v["id"]) or {}).get("auto_fw_update")),
                    "can_queue": bool(
                        v["capable"] and update_available and not queued and offerable
                    ),
                    "can_withdraw": queued,
                }
            )
    # rolled_back / failed float to the top, then by name.
    rows.sort(key=lambda r: (not r["needs_attention"], str(r["name"]).lower()))
    return rows


def _fleet_model() -> list[dict[str, Any]]:
    """Per-kind rollout view for the Advanced fleet controls: release state,
    capable devices, and the promote gate. No outbound check; the update
    availability shown in the device list is a separate concern."""
    reg = devices()
    releases = _release_store().all()
    names = _kind_names()

    by_kind: dict[str, list[Any]] = {}
    for d in reg.all():
        if d.kind_of is None:
            continue
        by_kind.setdefault(d.kind_of, []).append(d)

    out: list[dict[str, Any]] = []
    for kind_id in sorted(set(by_kind) | set(releases)):
        rel = releases.get(kind_id) if isinstance(releases.get(kind_id), dict) else None
        rel_fw = str(rel["fw_version"]) if rel and rel.get("fw_version") else None
        queued = _queued_ids(rel, kind_id) if rel else set()
        dev_views = [_device_view(d, rel_fw, queued) for d in by_kind.get(kind_id, [])]
        dev_views.sort(key=lambda v: str(v["name"]).lower())
        confirmed = sum(1 for v in dev_views if v["confirmed_on_release"])
        # How many capable devices a promote would newly offer the update to.
        offer_count = sum(
            1
            for v in dev_views
            if v["capable"] and rel_fw and is_newer(rel_fw, str(v["fw_version"] or ""))
        )
        out.append(
            {
                "kind_id": kind_id,
                "kind_name": names.get(kind_id, kind_id),
                "state": str(rel["state"]) if rel and rel.get("state") else None,
                "fw_version": rel_fw,
                "capable_devices": [v for v in dev_views if v["capable"]],
                "confirmed_count": confirmed,
                "offer_count": offer_count,
                "can_promote": bool(rel and rel.get("state") != "promoted" and confirmed > 0),
            }
        )
    return out


@bp.get("/settings/firmware")
def firmware_index() -> str:
    online = online_enabled(settings_store())
    return render_template(
        "settings_firmware.html",
        device_rows=_devices_model(online=online),
        fleet=_fleet_model(),
        online_enabled=online,
    )


def _redirect() -> Response:
    return redirect(url_for("auth.firmware_index") + "#top")


def _carryover_ids(kind_id: str, prior: dict[str, Any] | None, new_fw: str) -> list[str]:
    """The offer set for a newly-set release. The manual queue is one-shot
    per version: it carries over only when the version is unchanged (e.g. a
    re-import mid-rollout), so queueing once never opts a device into future
    releases. Auto-update devices ride every release."""
    base: set[str] = set()
    if prior is not None and str(prior.get("fw_version") or "") == new_fw:
        base = _queued_ids(prior, kind_id)
    base |= ota_auto.auto_update_ids(settings_store(), devices(), kind_id)
    return sorted(base)


def _accept_descriptor(descriptor: Any, *, source: str) -> None:
    """Verify a descriptor and set it as its kind's release. Flashes the
    outcome (and event-logs it)."""
    try:
        manifest = verify_descriptor(descriptor)
    except OtaVerificationError as exc:
        flash(f"Descriptor rejected: {exc.reason} ({exc}).", "error")
        _log(source, "(unknown)", "error", reason=exc.reason)
        return
    kind_id = str(manifest["device_kind"])
    if kind_id not in _known_kind_ids():
        flash(f"Descriptor rejected: no registered device kind {kind_id!r}.", "error")
        _log(source, kind_id, "error", reason="unknown_kind")
        return
    fw_version = str(manifest["fw_version"])
    store = _release_store()
    carry = _carryover_ids(kind_id, store.get(kind_id), fw_version)
    store.set_target(kind_id, descriptor, fw_version=fw_version, canary_device_ids=carry)
    _log(source, kind_id, "ok", fw_version=fw_version, key_id=str(manifest.get("key_id") or ""))
    flash(f"Imported {kind_id} release v{fw_version}. Queue a device to offer it.", "ok")


@bp.post("/settings/firmware/import")
def firmware_import() -> Response:
    """Verify an uploaded descriptor-<kind>.json and set it as the kind's release
    (canary state, empty canary list, so nothing ships until a canary is set)."""
    upload = request.files.get("descriptor")
    if upload is None or not upload.filename:
        flash("Choose a descriptor JSON file to import.", "error")
        return _redirect()
    try:
        descriptor = json.loads(upload.read())
    except (ValueError, TypeError):
        flash("That file is not valid JSON.", "error")
        return _redirect()
    _accept_descriptor(descriptor, source="import")
    return _redirect()


def _freshen_release(kind_id: str, rel: dict[str, Any] | None) -> dict[str, Any] | None:
    """Upgrade the kind's release to the newest published descriptor when the
    update check knows one newer than the imported release (or none is
    imported). No-op when offline or when nothing newer is published; a
    failed fetch keeps the imported release. The new release's offer set is
    auto-update devices only (the manual queue is one-shot per version)."""
    if not online_enabled(settings_store()):
        return rel
    carry = sorted(ota_auto.auto_update_ids(settings_store(), devices(), kind_id))
    fresh, err = ota_auto.freshen_release(_release_store(), kind_id, rel, carryover=carry)
    if err is not None:
        flash(f"Could not use the published descriptor: {err}.", "error")
        _log("import_url", kind_id, "error", reason=err)
        return rel
    if fresh is not None and fresh is not rel:
        _log("import_url", kind_id, "ok", fw_version=str(fresh.get("fw_version") or ""))
    return fresh


@bp.post("/settings/firmware/queue")
def firmware_queue() -> Response:
    """Queue one device for an OTA update: make sure the kind's release is the
    newest fetchable one, then add the device to the offer set. The heartbeat
    path still applies the firmware-version gate, so a queued device is only
    offered a build newer than what it reports."""
    device_id = (request.form.get("device_id") or "").strip()
    device = devices().get(device_id)
    if device is None or device.kind_of is None:
        flash("Unknown device.", "error")
        return _redirect()
    kind_id = device.kind_of
    if not _ota_capable(device_id):
        flash(f"{device.display_name} has not advertised OTA support; update it over USB.", "error")
        return _redirect()

    store = _release_store()
    rel = _freshen_release(kind_id, store.get(kind_id))
    if rel is None:
        flash(
            "No release to offer yet. Import a descriptor below, or turn on "
            "online features so it can be fetched automatically.",
            "error",
        )
        return _redirect()

    queued = _queued_ids(rel, kind_id)
    queued.add(device_id)
    fw_version = str(rel["fw_version"])
    store.set_target(
        kind_id, rel["descriptor"], fw_version=fw_version, canary_device_ids=sorted(queued)
    )
    _log("queue", kind_id, "ok", device_id=device_id, fw_version=fw_version)
    flash(
        f"{device.display_name} will be offered v{fw_version} on its next heartbeat.",
        "ok",
    )
    return _redirect()


@bp.post("/settings/firmware/withdraw")
def firmware_withdraw() -> Response:
    """Remove one device from its kind's offer set."""
    device_id = (request.form.get("device_id") or "").strip()
    device = devices().get(device_id)
    if device is None or device.kind_of is None:
        flash("Unknown device.", "error")
        return _redirect()
    kind_id = device.kind_of
    store = _release_store()
    rel = store.get(kind_id)
    if rel is None:
        flash("No release set for that kind.", "error")
        return _redirect()
    queued = _queued_ids(rel, kind_id)
    queued.discard(device_id)
    store.set_target(
        kind_id,
        rel["descriptor"],
        fw_version=str(rel["fw_version"]),
        canary_device_ids=sorted(queued),
    )
    _log("withdraw", kind_id, "ok", device_id=device_id)
    flash(f"{device.display_name} withdrawn; it will not be offered the update.", "ok")
    return _redirect()


@bp.post("/settings/firmware/auto")
def firmware_auto() -> Response:
    """Toggle a device's automatic-updates switch. On enables riding every
    new release (and queues the device for the current one right away); off
    also withdraws it from a pending offer so nothing updates unbidden."""
    device_id = (request.form.get("device_id") or "").strip()
    enabled = request.form.get("enabled") == "on"
    device = devices().get(device_id)
    if device is None or device.kind_of is None:
        flash("Unknown device.", "error")
        return _redirect()
    kind_id = device.kind_of
    if enabled and not _ota_capable(device_id):
        flash(f"{device.display_name} has not advertised OTA support; update it over USB.", "error")
        return _redirect()

    store_settings = settings_store()
    section = dict((store_settings.get_section("devices") or {}).get(device_id) or {})
    section["auto_fw_update"] = enabled
    store_settings.patch_section("devices", {device_id: section})
    _log("auto_update", kind_id, "ok", device_id=device_id, enabled=enabled)

    if enabled:
        # Immediate effect: queue for the current release (fetching the
        # newest published one when online), same as the heartbeat hook.
        ota_auto.maybe_auto_queue(current_app._get_current_object(), device)  # type: ignore[attr-defined]
        flash(
            f"Automatic updates on for {device.display_name}: new releases will be "
            "offered on its next heartbeat.",
            "ok",
        )
    else:
        store = _release_store()
        rel = store.get(kind_id)
        if rel is not None and rel.get("state") != "promoted":
            queued = _queued_ids(rel, kind_id)
            if device_id in queued:
                queued.discard(device_id)
                store.set_target(
                    kind_id,
                    rel["descriptor"],
                    fw_version=str(rel["fw_version"]),
                    canary_device_ids=sorted(queued),
                )
        flash(f"Automatic updates off for {device.display_name}.", "ok")
    return _redirect()


# -- Advanced fleet controls: staged canary / promote / pause per kind. The
# same release state the queue buttons edit, expressed at fleet altitude for
# multi-device installs; also drivable via ``python -m app.ota.release``.


@bp.post("/settings/firmware/canary")
def firmware_canary() -> Response:
    """Offer the kind's release to a chosen set of capable devices."""
    kind_id = (request.form.get("kind_id") or "").strip()
    rel = _release_store().get(kind_id)
    if not rel:
        flash("No release set for that kind. Import a descriptor first.", "error")
        return _redirect()

    picked = request.form.getlist("device_ids")
    reg = devices()
    # Only accept capable devices that belong to this kind.
    valid = {d.id for d in reg.all() if d.kind_of == kind_id and _ota_capable(d.id)}
    canary = [d for d in picked if d in valid]
    if not canary:
        flash("Select at least one OTA-capable device of this kind.", "error")
        return _redirect()

    _release_store().set_target(
        kind_id,
        rel["descriptor"],
        fw_version=str(rel["fw_version"]),
        canary_device_ids=canary,
    )
    _log("canary", kind_id, "ok", device_ids=canary, fw_version=str(rel["fw_version"]))
    flash(
        f"Canary set for {kind_id}: {len(canary)} device(s) will be offered v{rel['fw_version']}.",
        "ok",
    )
    return _redirect()


@bp.post("/settings/firmware/promote")
def firmware_promote() -> Response:
    """Promote the release to every device of the kind, gated on a confirmed
    canary. The gate is re-checked here, never trusted from the client."""
    kind_id = (request.form.get("kind_id") or "").strip()
    model = next((k for k in _fleet_model() if k["kind_id"] == kind_id), None)
    if model is None or model["fw_version"] is None:
        flash("No release set for that kind.", "error")
        return _redirect()
    if not model["can_promote"]:
        flash(
            "Promote is blocked until at least one canary device reports "
            "confirmed on this version.",
            "error",
        )
        _log("promote", kind_id, "error", reason="no_confirmed_canary")
        return _redirect()

    _release_store().promote(kind_id)
    _log("promote", kind_id, "ok", fw_version=str(model["fw_version"]))
    flash(
        f"Promoted {kind_id} v{model['fw_version']}: "
        f"{model['offer_count']} device(s) will be offered it on their next heartbeat.",
        "ok",
    )
    return _redirect()


@bp.post("/settings/firmware/pause")
def firmware_pause() -> Response:
    """Withdraw the offer (pause). Reversible: re-set a canary or promote again."""
    kind_id = (request.form.get("kind_id") or "").strip()
    if not _release_store().pause(kind_id):
        flash("No release set for that kind.", "error")
        return _redirect()
    _log("pause", kind_id, "ok")
    flash(f"Paused {kind_id}: no devices will be offered the update.", "ok")
    return _redirect()


@bp.post("/settings/firmware/check")
def firmware_check_now() -> Response:
    """Drop the hourly update-check cache so the redirect back to the page
    re-asks api.tesserae.ink for every kind. Same online-mode gate as the
    automatic check; the fetch itself happens during the following GET."""
    if not online_enabled(settings_store()):
        flash("Update checks need online features; turn them on under System.", "error")
        return _redirect()
    fw_check.clear_cache()
    flash("Checked api.tesserae.ink for the latest published firmware.", "ok")
    return _redirect()
