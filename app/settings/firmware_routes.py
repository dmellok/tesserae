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
import urllib.request
from typing import Any
from urllib.parse import urlparse

from flask import current_app, flash, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from app import firmware_check as fw_check
from app.online import online_enabled
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


def _device_view(device: Any, rel_fw: str | None, canary_ids: set[str]) -> dict[str, Any]:
    entry = device_status().get(device.id) or {}
    parsed = entry.get("parsed") or {}
    report_raw = entry.get("ota")
    report: dict[str, Any] = report_raw if isinstance(report_raw, dict) else {}
    phase = report.get("phase")
    fw = parsed.get("fw_version")
    fw_str = str(fw) if isinstance(fw, (str, int, float)) else None
    capable = entry.get("ota_schema") is not None
    needs_attention = phase in ("failed", "rolled_back")
    report_at = report.get("received_at")
    return {
        "id": device.id,
        "name": device.display_name,
        "fw_version": fw_str,
        "capable": capable,
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
        status = device_status()
        return {
            d.id
            for d in devices().all()
            if d.kind_of == kind_id and (status.get(d.id) or {}).get("ota_schema") is not None
        }
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
            queued = bool(v["is_canary"])
            rows.append(
                {
                    **v,
                    "kind_id": kind_id,
                    "kind_name": names.get(kind_id, kind_id),
                    "queued": queued,
                    "available_fw": available,
                    "release_url": str(latest["url"]) if latest and latest.get("url") else None,
                    "notes_headline": (
                        str(latest["notes_headline"])
                        if latest and latest.get("notes_headline")
                        else None
                    ),
                    "update_available": update_available,
                    "can_queue": bool(
                        v["capable"] and update_available and not queued and offerable
                    ),
                    "can_withdraw": bool(queued and update_available),
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


# Hosts a descriptor may be fetched from for one-click import (anti-SSRF). The
# URL originates from api.tesserae.ink's update check and points at that host or
# a GitHub release asset; verify_descriptor is still the real trust gate.
_DESCRIPTOR_FETCH_HOSTS = {
    "api.tesserae.ink",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "raw.githubusercontent.com",
}
_DESCRIPTOR_MAX_BYTES = 64 * 1024


def _accept_descriptor(descriptor: Any, *, source: str) -> None:
    """Verify a descriptor and set it as its kind's release, preserving any
    already-queued devices. Flashes the outcome (and event-logs it)."""
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
    prior = store.get(kind_id)
    queued = sorted(_queued_ids(prior, kind_id)) if prior else []
    store.set_target(kind_id, descriptor, fw_version=fw_version, canary_device_ids=queued)
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


def _fetch_descriptor(url: str) -> Any:
    """Fetch a descriptor JSON from an allowlisted https host (anti-SSRF),
    with a size cap. Raises ValueError on any failure; verify_descriptor
    remains the real trust gate."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in _DESCRIPTOR_FETCH_HOSTS:
        raise ValueError("untrusted descriptor URL")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tesserae-firmware-import"})
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status}")
            raw = resp.read(_DESCRIPTOR_MAX_BYTES + 1)
        if len(raw) > _DESCRIPTOR_MAX_BYTES:
            raise ValueError("descriptor too large")
        return json.loads(raw)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(str(exc)) from exc


def _freshen_release(kind_id: str, rel: dict[str, Any] | None) -> dict[str, Any] | None:
    """Upgrade the kind's release to the newest published descriptor when the
    update check knows one newer than the imported release (or none is
    imported), preserving the queued device set. No-op when offline or when
    nothing newer is published; a failed fetch keeps the imported release."""
    if not online_enabled(settings_store()):
        return rel
    info = fw_check.latest_for_kind(kind_id)
    if info is None or not info.version or not info.descriptor_url:
        return rel
    if rel is not None and not is_newer(info.version, str(rel.get("fw_version") or "")):
        return rel
    try:
        descriptor = _fetch_descriptor(info.descriptor_url)
        manifest = verify_descriptor(descriptor)
    except ValueError as exc:
        flash(f"Could not fetch the published descriptor: {exc}.", "error")
        _log("import_url", kind_id, "error", reason="fetch_failed")
        return rel
    except OtaVerificationError as exc:
        flash(f"Published descriptor rejected: {exc.reason} ({exc}).", "error")
        _log("import_url", kind_id, "error", reason=exc.reason)
        return rel
    if str(manifest["device_kind"]) != kind_id:
        _log("import_url", kind_id, "error", reason="kind_mismatch")
        return rel
    queued = sorted(_queued_ids(rel, kind_id)) if rel else []
    fw_version = str(manifest["fw_version"])
    entry: dict[str, Any] = _release_store().set_target(
        kind_id, descriptor, fw_version=fw_version, canary_device_ids=queued
    )
    _log(
        "import_url", kind_id, "ok", fw_version=fw_version, key_id=str(manifest.get("key_id") or "")
    )
    return entry


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
    if (device_status().get(device_id) or {}).get("ota_schema") is None:
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
    valid = {
        d.id
        for d in reg.all()
        if d.kind_of == kind_id and (device_status().get(d.id) or {}).get("ota_schema") is not None
    }
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
