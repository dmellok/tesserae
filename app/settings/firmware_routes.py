"""Settings -> Firmware: the OTA rollout admin UI (issue #121).

A thin, guard-railed wrapper over the same rollout state the CLI writes
(`app.ota.release`), reading/writing `<data-root>/core/ota_releases.json` through
`OtaReleaseStore`, never a parallel store. The page shows, per device kind: the
current release + its verified manifest, rollout controls (set canary / promote /
pause), and a fleet status view driven by the heartbeat OTA reports the server
already stores. Descriptors are verified against the published trust anchor
before they can be offered; the UI never touches the image bytes.
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
from app.ota.service import manifest_summary, verify_descriptor
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


def _release_view(rel: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rel:
        return None
    descriptor = rel.get("descriptor") or {}
    summary: dict[str, Any] | None = None
    try:
        # Decode-only summary for display; the descriptor was verified at import.
        from app.ota.service import manifest_from_descriptor

        summary = manifest_summary(manifest_from_descriptor(descriptor))
    except OtaVerificationError:
        summary = None
    return {
        "state": rel.get("state"),
        "fw_version": rel.get("fw_version"),
        "canary_device_ids": list(rel.get("canary_device_ids") or []),
        "manifest": summary,
        "updated_relative": (
            format_relative(max(0.0, time.time() - float(rel["updated_at"])))
            if isinstance(rel.get("updated_at"), (int, float))
            else None
        ),
    }


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


def _kinds_model(*, online: bool) -> list[dict[str, Any]]:
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
        canary_ids = set(rel.get("canary_device_ids") or []) if rel else set()

        dev_views = [_device_view(d, rel_fw, canary_ids) for d in by_kind.get(kind_id, [])]
        # rolled_back / failed float to the top, then by name.
        dev_views.sort(key=lambda v: (not v["needs_attention"], v["name"].lower()))

        confirmed = sum(1 for v in dev_views if v["confirmed_on_release"])
        # How many capable devices a promote would newly offer the update to
        # (capable, not already on the release version).
        offer_count = sum(
            1
            for v in dev_views
            if v["capable"] and rel_fw and is_newer(rel_fw, str(v["fw_version"] or ""))
        )
        can_promote = bool(rel and rel.get("state") != "promoted" and confirmed > 0)

        out.append(
            {
                "kind_id": kind_id,
                "kind_name": names.get(kind_id, kind_id),
                "release": _release_view(rel),
                "latest": _latest_view(kind_id, rel_fw, dev_views) if online else None,
                "devices": dev_views,
                "capable_devices": [v for v in dev_views if v["capable"]],
                "has_attention": any(v["needs_attention"] for v in dev_views),
                "confirmed_count": confirmed,
                "offer_count": offer_count,
                "can_promote": can_promote,
            }
        )
    return out


@bp.get("/settings/firmware")
def firmware_index() -> str:
    online = online_enabled(settings_store())
    return render_template(
        "settings_firmware.html",
        kinds=_kinds_model(online=online),
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
    """Verify a descriptor and set it as its kind's release. Flashes the outcome
    (and event-logs it) for both the file upload and the one-click URL import."""
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
    _release_store().set_target(kind_id, descriptor, fw_version=fw_version, canary_device_ids=[])
    _log(source, kind_id, "ok", fw_version=fw_version, key_id=str(manifest.get("key_id") or ""))
    flash(f"Imported {kind_id} release v{fw_version}. Set a canary to begin.", "ok")


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


@bp.post("/settings/firmware/import-url")
def firmware_import_url() -> Response:
    """One-click import: fetch a descriptor from the release URL the update check
    surfaced, then verify + set it exactly like an upload. Gated on online mode;
    the URL host is allowlisted (anti-SSRF) and the body size is capped."""
    if not online_enabled(settings_store()):
        flash("Online mode is off; enable it to fetch a release descriptor.", "error")
        return _redirect()
    url = (request.form.get("descriptor_url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in _DESCRIPTOR_FETCH_HOSTS:
        flash("Refusing to fetch a descriptor from an untrusted URL.", "error")
        _log("import_url", "(unknown)", "error", reason="untrusted_host")
        return _redirect()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tesserae-firmware-import"})
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status}")
            raw = resp.read(_DESCRIPTOR_MAX_BYTES + 1)
        if len(raw) > _DESCRIPTOR_MAX_BYTES:
            raise ValueError("descriptor too large")
        descriptor = json.loads(raw)
    except Exception as exc:
        flash(f"Could not fetch descriptor: {exc}.", "error")
        _log("import_url", "(unknown)", "error", reason="fetch_failed")
        return _redirect()
    _accept_descriptor(descriptor, source="import_url")
    return _redirect()


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
    # online=False: the promote gate (confirmed canary) is local; no need for the
    # outbound availability check on a POST.
    model = next((k for k in _kinds_model(online=False) if k["kind_id"] == kind_id), None)
    if model is None or model["release"] is None:
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
    _log("promote", kind_id, "ok", fw_version=str(model["release"]["fw_version"]))
    flash(
        f"Promoted {kind_id} v{model['release']['fw_version']}: "
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
