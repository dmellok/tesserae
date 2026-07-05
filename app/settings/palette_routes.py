"""Palette-profile routes for the Calibration tab.

Each device stores its active profile as
``settings.devices.<instance_id>.palette_profile_slug``. Bundled
profiles ship in :mod:`app.palette_profiles.bundled`; user profiles
live under ``data/palette_profiles/<slug>.json``. All routes redirect
back to the device's Calibration tab with a flash message; JSON
callers can peek at the flash but the primary caller is the
Calibration tab form.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Response as FlaskResponse
from flask import current_app, flash, redirect, request, url_for
from werkzeug.wrappers import Response

from app.palette_profiles import (
    BUNDLED_PROFILES,
    PaletteProfile,
    PaletteProfileStore,
    bundled_profile,
    profile_from_dict,
    slugify,
)

from ._shared import bp, devices, settings_store


def _profile_store() -> PaletteProfileStore:
    """Cheap builder off ``DATA_ROOT``; the store is stateless apart
    from the on-disk dir so there's no benefit to caching it in
    ``app.config``."""
    return PaletteProfileStore(Path(current_app.config["DATA_ROOT"]))


def _write_device_slug(instance_id: str, slug: str) -> None:
    """Set the device's active palette profile slug via the same
    single-field patch pattern the rest of the devices routes use."""
    field = [{"name": "palette_profile_slug", "type": "string", "default": ""}]
    settings_store().update_for_namespace(
        "devices", instance_id, {"palette_profile_slug": slug}, field
    )


def _redirect_to_calibration(instance_id: str) -> Response:
    """Every action lands the user back on the Calibration tab of the
    device they were editing.

    Threads ``opened=<instance_id>`` so the device card stays expanded
    across the redirect (v0.69.9, issue #52 item 3 follow-up). Prior to
    v0.69.9 only ``devices_update_combined`` did this; every palette /
    tone / calibrate action fell through the else branch and the card
    collapsed on every Apply.
    """
    return redirect(
        url_for(
            "auth.settings_area",
            area="devices",
            tab="calibration",
            opened=instance_id,
            _anchor=f"device-{instance_id}",
        )
    )


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@bp.post("/settings/devices/<instance_id>/palette/apply")
def devices_palette_apply(instance_id: str) -> Response:
    """Apply a bundled or user profile to the device. Empty slug
    clears the override (device falls back to nominal / built-in
    calibrated palette)."""
    device = devices().get(instance_id)
    if device is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    slug = (request.form.get("slug") or "").strip()
    if slug:
        found = bundled_profile(slug) or _profile_store().load(slug)
        if found is None:
            flash(f"Unknown palette profile {slug!r}.", "error")
            return _redirect_to_calibration(instance_id)
    _write_device_slug(instance_id, slug)
    label = "cleared" if not slug else f"applied {slug!r}"
    flash(f"Palette profile {label} for {device.name!r}.", "ok")
    return _redirect_to_calibration(instance_id)


@bp.post("/settings/devices/<instance_id>/palette/save")
def devices_palette_save(instance_id: str) -> Response:
    """Save the currently-picked bundled profile as a new user
    profile (with a new name), then apply it to the device so the
    user is editing "their" copy."""
    device = devices().get(instance_id)
    if device is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Give the profile a name before saving.", "error")
        return _redirect_to_calibration(instance_id)
    base_slug = (request.form.get("base_slug") or "").strip()
    base = bundled_profile(base_slug) or _profile_store().load(base_slug)
    if base is None:
        flash(f"Unknown base profile {base_slug!r}.", "error")
        return _redirect_to_calibration(instance_id)
    store = _profile_store()
    new_slug = slugify(name)
    suffix = 2
    original = new_slug
    while not store.slug_available(new_slug):
        new_slug = f"{original}-{suffix}"
        suffix += 1
    new_profile = PaletteProfile(
        slug=new_slug,
        name=name,
        family=base.family,
        palette=base.palette,
        tone=base.tone,
        dither=base.dither,
        edges=base.edges,
        bundled=False,
        based_on=base.based_on or base.slug,
        attribution=base.attribution,
        notes=base.notes,
        saved_at=_now_iso(),
    )
    store.save(new_profile)
    _write_device_slug(instance_id, new_slug)
    flash(f"Saved palette profile {name!r} and applied it to {device.name!r}.", "ok")
    return _redirect_to_calibration(instance_id)


@bp.post("/settings/devices/<instance_id>/palette/update-tone")
def devices_palette_update_tone(instance_id: str) -> Response:
    """Update the tone / dither knobs on the device's active profile.

    Bundled profiles fork into a user profile named "<Base name> (edited)"
    on first tweak; subsequent tweaks update the fork in place. User
    profiles are edited directly. The renderer picks up the new values
    on the next push (no restart needed)."""
    from app.palette_profiles.schema import DitherSettings, EdgeSettings, ToneSettings

    device = devices().get(instance_id)
    if device is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    slug_field = [{"name": "palette_profile_slug", "type": "string", "default": ""}]
    raw = settings_store().get_for_runtime("devices", instance_id, slug_field)
    slug = str(raw.get("palette_profile_slug") or "").strip()
    if not slug:
        flash("Pick a palette profile before editing tone.", "error")
        return _redirect_to_calibration(instance_id)
    base = bundled_profile(slug) or _profile_store().load(slug)
    if base is None:
        flash(f"Unknown palette profile {slug!r}.", "error")
        return _redirect_to_calibration(instance_id)

    def _as_int(key: str, default: int) -> int:
        try:
            return int(request.form.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    new_tone = ToneSettings(
        exposure=max(-100, min(100, _as_int("exposure", base.tone.exposure))),
        contrast=base.tone.contrast,
        saturation=base.tone.saturation,
        s_curve=max(-100, min(100, _as_int("s_curve", base.tone.s_curve))),
        lab_compress_min=max(0, min(100, _as_int("lab_compress_min", base.tone.lab_compress_min))),
        lab_compress_max=max(0, min(100, _as_int("lab_compress_max", base.tone.lab_compress_max))),
    )
    color_match_raw = (request.form.get("color_match") or base.dither.color_match).strip()
    if color_match_raw not in ("rgb", "lab", "chroma-aware"):
        color_match_raw = base.dither.color_match
    new_dither = DitherSettings(
        algorithm=base.dither.algorithm,
        serpentine=bool(request.form.get("serpentine")),
        color_match=color_match_raw,
        diffusion_strength=max(0, min(200, _as_int("diffusion_strength", 100))),
    )
    new_edges = EdgeSettings(
        preserve_line_art=bool(request.form.get("preserve_line_art")),
        smoothing_radius=max(0, min(3, _as_int("smoothing_radius", 0))),
    )
    store = _profile_store()
    if base.bundled:
        # Fork the bundled preset. Naming skips a suffix on the first
        # fork; subsequent forks tack on ``-2``, ``-3``... same suffix
        # loop the Save-as-new flow uses.
        forked_slug = f"{base.slug}-edited"
        suffix = 2
        original = forked_slug
        while not store.slug_available(forked_slug):
            forked_slug = f"{original}-{suffix}"
            suffix += 1
        forked = PaletteProfile(
            slug=forked_slug,
            name=f"{base.name} (edited)",
            family=base.family,
            palette=base.palette,
            tone=new_tone,
            dither=new_dither,
            edges=new_edges,
            bundled=False,
            based_on=base.based_on or base.slug,
            attribution=base.attribution,
            notes=base.notes,
            saved_at=_now_iso(),
        )
        store.save(forked)
        _write_device_slug(instance_id, forked_slug)
        flash(f"Forked {base.name!r} into an editable copy and applied it.", "ok")
        return _redirect_to_calibration(instance_id)
    # Non-bundled: edit in place.
    updated = PaletteProfile(
        slug=base.slug,
        name=base.name,
        family=base.family,
        palette=base.palette,
        tone=new_tone,
        dither=new_dither,
        edges=base.edges,
        bundled=False,
        based_on=base.based_on,
        attribution=base.attribution,
        notes=base.notes,
        saved_at=_now_iso(),
    )
    store.save(updated)
    flash(f"Updated tone for profile {base.name!r}.", "ok")
    return _redirect_to_calibration(instance_id)


@bp.post("/settings/devices/<instance_id>/palette/update-palette")
def devices_palette_update_palette(instance_id: str) -> Response:
    """Update the RGB values on the device's active profile.

    Accepts six (Spectra 6) or seven (Inky 7-colour) ``#rrggbb`` hex
    values keyed ``black`` / ``white`` / ``yellow`` / ``red`` / ``blue``
    / ``green`` (+ optional ``orange`` for Inky). Bundled profiles fork
    on first edit, mirroring the tone-editor flow; user profiles are
    edited in place. The renderer picks up the new palette on the next
    push."""
    from app.palette_profiles.schema import PaletteColors

    device = devices().get(instance_id)
    if device is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    slug_field = [{"name": "palette_profile_slug", "type": "string", "default": ""}]
    raw = settings_store().get_for_runtime("devices", instance_id, slug_field)
    slug = str(raw.get("palette_profile_slug") or "").strip()
    if not slug:
        flash("Pick a palette profile before editing colours.", "error")
        return _redirect_to_calibration(instance_id)
    base = bundled_profile(slug) or _profile_store().load(slug)
    if base is None:
        flash(f"Unknown palette profile {slug!r}.", "error")
        return _redirect_to_calibration(instance_id)

    def _hex(key: str, default: str) -> str:
        val = (request.form.get(key) or "").strip()
        # ``<input type="color">`` always submits ``#rrggbb``. Reject
        # anything that doesn't match the shape so a broken submit
        # can't put garbage in the profile.
        if len(val) == 7 and val.startswith("#"):
            try:
                int(val[1:], 16)
                return val
            except ValueError:
                return default
        return default

    new_palette = PaletteColors(
        black=_hex("black", base.palette.black),
        white=_hex("white", base.palette.white),
        yellow=_hex("yellow", base.palette.yellow),
        red=_hex("red", base.palette.red),
        blue=_hex("blue", base.palette.blue),
        green=_hex("green", base.palette.green),
        orange=(
            _hex("orange", base.palette.orange or "#ff8c00")
            if base.palette.orange is not None
            else None
        ),
    )
    store = _profile_store()
    if base.bundled:
        forked_slug = f"{base.slug}-edited"
        suffix = 2
        original = forked_slug
        while not store.slug_available(forked_slug):
            forked_slug = f"{original}-{suffix}"
            suffix += 1
        forked = PaletteProfile(
            slug=forked_slug,
            name=f"{base.name} (edited)",
            family=base.family,
            palette=new_palette,
            tone=base.tone,
            dither=base.dither,
            edges=base.edges,
            bundled=False,
            based_on=base.based_on or base.slug,
            attribution=base.attribution,
            notes=base.notes,
            saved_at=_now_iso(),
        )
        store.save(forked)
        _write_device_slug(instance_id, forked_slug)
        flash(f"Forked {base.name!r} into an editable copy with your palette.", "ok")
        return _redirect_to_calibration(instance_id)
    updated = PaletteProfile(
        slug=base.slug,
        name=base.name,
        family=base.family,
        palette=new_palette,
        tone=base.tone,
        dither=base.dither,
        edges=base.edges,
        bundled=False,
        based_on=base.based_on,
        attribution=base.attribution,
        notes=base.notes,
        saved_at=_now_iso(),
    )
    store.save(updated)
    flash(f"Updated palette for profile {base.name!r}.", "ok")
    return _redirect_to_calibration(instance_id)


@bp.post("/settings/devices/<instance_id>/palette/reset")
def devices_palette_reset(instance_id: str) -> Response:
    """Clear the device's palette profile override so the built-in
    calibrated / nominal palette wins again."""
    device = devices().get(instance_id)
    if device is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    _write_device_slug(instance_id, "")
    flash(f"Reset {device.name!r} to the built-in palette.", "ok")
    return _redirect_to_calibration(instance_id)


@bp.get("/settings/palette-profiles/<slug>/export.json")
def palette_profile_export(slug: str) -> Response:
    """Download a profile as JSON. Bundled + user profiles both
    exportable so users can share them or move between installs."""
    found = bundled_profile(slug) or _profile_store().load(slug)
    if found is None:
        return FlaskResponse("unknown profile", status=404, mimetype="text/plain")
    body = json.dumps(found.to_dict(), indent=2, sort_keys=True) + "\n"
    return FlaskResponse(
        body,
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{slug}.json"',
            "Cache-Control": "no-store",
        },
    )


@bp.post("/settings/palette-profiles/import")
def palette_profile_import() -> Response:
    """Upload a profile JSON. Rejects payloads that don't match the
    schema; on success, redirects to the calibration tab of the
    device supplied in ``instance_id`` (so imports flow naturally
    from the device the user was already editing)."""
    instance_id = (request.form.get("instance_id") or "").strip()
    upload = request.files.get("profile")
    if upload is None or not upload.filename:
        flash("Pick a JSON file to import.", "error")
        return (
            _redirect_to_calibration(instance_id)
            if instance_id
            else redirect(url_for("auth.settings_area", area="devices"))
        )
    try:
        raw: Any = json.loads(upload.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        flash(f"Import failed: {err}", "error")
        return (
            _redirect_to_calibration(instance_id)
            if instance_id
            else redirect(url_for("auth.settings_area", area="devices"))
        )
    if not isinstance(raw, dict):
        flash("Import failed: JSON root must be an object.", "error")
        return (
            _redirect_to_calibration(instance_id)
            if instance_id
            else redirect(url_for("auth.settings_area", area="devices"))
        )
    profile = profile_from_dict(raw)
    store = _profile_store()
    slug = profile.slug or slugify(profile.name or "imported")
    suffix = 2
    original = slug
    while not store.slug_available(slug):
        slug = f"{original}-{suffix}"
        suffix += 1
    profile = PaletteProfile(
        slug=slug,
        name=profile.name or slug,
        family=profile.family,
        palette=profile.palette,
        tone=profile.tone,
        dither=profile.dither,
        edges=profile.edges,
        bundled=False,
        based_on=profile.based_on,
        attribution=profile.attribution,
        notes=profile.notes,
        saved_at=_now_iso(),
    )
    store.save(profile)
    if instance_id and devices().get(instance_id) is not None:
        _write_device_slug(instance_id, slug)
        flash(f"Imported profile {profile.name!r} and applied it.", "ok")
        return _redirect_to_calibration(instance_id)
    flash(f"Imported profile {profile.name!r}.", "ok")
    return redirect(url_for("auth.settings_area", area="devices"))


@bp.post("/settings/palette-profiles/<slug>/delete")
def palette_profile_delete(slug: str) -> Response:
    """Remove a user profile. Bundled profiles are refused. Devices
    still pointing at the deleted slug fall back to the built-in
    palette until the user re-applies something."""
    if bundled_profile(slug) is not None:
        flash("Bundled profiles can't be deleted.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    store = _profile_store()
    if store.delete(slug):
        flash(f"Deleted profile {slug!r}.", "ok")
    else:
        flash(f"Unknown profile {slug!r}.", "error")
    return redirect(url_for("auth.settings_area", area="devices"))


def profile_choices_for(family: str) -> list[dict[str, Any]]:
    """The picker's dropdown for a device gamut. Bundled entries in
    canonical order first, then user profiles sorted newest-first (via
    ``PaletteProfileStore.list_all``). Consumed by
    :func:`app.settings.index_routes._build_sections`."""
    out: list[dict[str, Any]] = []
    for p in BUNDLED_PROFILES:
        if p.family == family:
            out.append(_choice_row(p))
    for p in _profile_store().list_all():
        if p.family == family:
            out.append(_choice_row(p))
    return out


def _choice_row(p: PaletteProfile) -> dict[str, Any]:
    return {
        "slug": p.slug,
        "name": p.name,
        "family": p.family,
        "bundled": p.bundled,
        "based_on": p.based_on,
        "attribution": p.attribution,
        "notes": p.notes,
    }
