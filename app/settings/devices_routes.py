"""Settings → Devices endpoints, every CRUD action for multi-head devices.

The lifecycle (validate → write JSON → load → clone renderers) lives in
:mod:`app.device_service` so the Add-device form and the Discovered
one-click register share one implementation. These routes just parse
the form, call the service, and flash + redirect.

The standout is :func:`devices_update_combined`, which backs the single
"Save changes" button on each device card. It fans out to the same
service helpers as the per-subsection endpoints (``/panel``,
``/quiet-hours``, ``/settings/device-<id>``), routes namespaced
``<clone_id>:<field>`` inputs to picture-quality settings, and rebuilds
the transport once at the end.
"""

from __future__ import annotations

import json
from typing import Any

from flask import current_app, flash, redirect, request, session, url_for
from werkzeug.wrappers import Response

from app import calibration, device_service, renderer_loader
from app.calibration import build_calibration_card, target_orientation
from app.panel import panel_overrides_from_form

from ._shared import (
    bp,
    coerce_form_value,
    config_fields_from_schema,
    device_data_root,
    devices,
    discovery_cache,
    format_discovered,
    orientation_label,
    push_manager,
    rebuild_transport_fn,
    renderers,
    settings_store,
    transport,
    values_from_form,
)

# -- add / discover / dismiss ------------------------------------------


@bp.post("/settings/devices/add")
def devices_add() -> Response:
    """Create a new device instance from the Devices-tab form. No restart
    needed, the new device shows up immediately in the page editor's
    Target-device dropdown."""
    form = request.form
    result = device_service.create_instance(
        devices=devices(),
        renderers=renderers(),
        data_root=device_data_root(),
        instance_id=form.get("id") or "",
        kind_id=(form.get("kind") or "").strip(),
        name=form.get("name") or "",
        panel_overrides=panel_overrides_from_form(form),
        orientation=form.get("panel_orientation"),
    )
    if not result.ok or result.device is None:
        flash(result.error or "Failed to add device.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    # TRMNL-style clients need their access token displayed once so the
    # user can paste it into the client config. Stash in session and let
    # the Settings GET that follows the redirect pop it into a one-shot
    # reveal modal (same flow as the webhook-token reveal in System).
    token = result.device.manifest.get("access_token")
    if isinstance(token, str) and token:
        session["_trmnl_token_reveal"] = {
            "device_id": result.device.id,
            "device_name": result.device.name,
            "token": token,
        }
    # New device's clones inherit picture-quality (dither/saturation/
    # contrast) from the user's existing base-renderer values where
    # available, so a freshly-added device matches the rest of the
    # fleet rather than dropping back to the manifest defaults.
    renderer_loader.seed_device_settings_from_base(renderers(), settings_store())
    rebuild_transport_fn()()
    flash(f"Added device {result.device.name!r}.", "ok")
    return redirect(
        url_for("auth.settings_area", area="devices", _anchor=f"device-{result.device.id}")
    )


def _apply_rename(device: Any, new_name: str) -> None:
    """Rewrite the instance JSON's ``name`` field + the in-memory manifest,
    then nudge HA discovery to re-publish so its device tile title updates
    without a Tesserae restart. Caller is responsible for input validation
    (non-empty, sane length) and any flash messaging."""
    raw = json.loads(device.path.read_text(encoding="utf-8"))
    raw["name"] = new_name
    device.path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    device.manifest["name"] = new_name
    ha = current_app.config.get("HA_DISCOVERY")
    if ha is not None:
        try:
            ha.refresh_entity_configs()
        except Exception:
            current_app.logger.exception("HA discovery: refresh after rename failed")


@bp.post("/settings/devices/<instance_id>/regenerate-token")
def devices_regenerate_token(instance_id: str) -> Response:
    """Mint a fresh access token for a TRMNL device.

    Used when the existing token has been shoulder-surfed, written down
    in the wrong notebook, or otherwise needs invalidating. The new
    token replaces the stored one and gets stashed in the session so
    the next Settings → Devices render pops the same one-shot modal as
    the add-device flow. The old token stops working immediately -
    the client will fail its next poll and need its config updated."""
    anchor = f"device-{instance_id}"
    redirect_to = redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))

    devs = devices()
    device = devs.get(instance_id)
    if device is None or device.kind_of is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect_to
    if "access_token" not in device.manifest:
        flash(f"{device.name!r} doesn't use access tokens.", "error")
        return redirect_to

    new_token = device_service.generate_access_token(devs)
    # Rewrite the instance JSON on disk + the in-memory manifest. The
    # manifest dict is technically owned by the loader but it's a plain
    # dict so direct mutation is what every other "update an instance
    # field" path does (panel, quiet hours, etc.).
    try:
        raw = json.loads(device.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        flash(f"Couldn't read {device.path.name}: {err}", "error")
        return redirect_to
    raw["access_token"] = new_token
    device.path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    device.manifest["access_token"] = new_token

    session["_trmnl_token_reveal"] = {
        "device_id": device.id,
        "device_name": device.name,
        "token": new_token,
    }
    flash(f"Regenerated access token for {device.name!r}. Update the client config.", "ok")
    return redirect_to


@bp.get("/settings/devices/discovered.json")
def devices_discovered_json() -> Response:
    """Current discovered devices as JSON. The Devices page + setup wizard
    poll this so a newly-announced client appears without a manual refresh
    (the heartbeat may arrive seconds after the page loads)."""
    items = [
        {"id": d["id"], "kind": d["kind"]}
        for d in format_discovered(discovery_cache().all())
        if d["id"] not in devices().devices
    ]
    return current_app.response_class(json.dumps({"devices": items}), mimetype="application/json")


@bp.post("/settings/devices/discovery/<discovered_id>/register")
def devices_register_discovered(discovered_id: str) -> Response:
    """One-click register a discovered device, same lifecycle as the
    Add-device form, but kind / panel / id default from the cached
    heartbeat (the form may override any of them)."""
    cache = discovery_cache()
    entry = cache.get(discovered_id)
    if entry is None:
        flash(f"Discovered device {discovered_id!r} is no longer in the cache.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))

    form = request.form
    kind_id = (form.get("kind") or entry.kind or "").strip()
    if not kind_id:
        flash(
            f"Discovered device {discovered_id!r} didn't advertise a kind; "
            "register it manually via the Add-device form.",
            "error",
        )
        return redirect(url_for("auth.settings_area", area="devices"))

    panel_overrides: dict[str, Any] = {}
    if entry.panel_w is not None:
        panel_overrides["w"] = entry.panel_w
    if entry.panel_h is not None:
        panel_overrides["h"] = entry.panel_h

    # TRMNL discoveries carry the original access_token in the cache
    # entry's parsed payload so create_instance can preserve it, the
    # user already has it pasted into their client config, and the
    # whole point of one-click pairing is not making them re-paste a
    # freshly-generated one.
    discovered_token = entry.parsed.get("access_token") if kind_id == "trmnl_client" else None
    # The synthetic discovery id for TRMNL entries (``trmnl_<token>``)
    # leaks the token into the device id if used as-is; default the
    # instance id to a friendlier one and let the user override if
    # they want.
    default_id = discovered_id
    if kind_id == "trmnl_client" and default_id.startswith("trmnl_"):
        default_id = "trmnl_" + (discovered_token[:5] if isinstance(discovered_token, str) else "")
    result = device_service.create_instance(
        devices=devices(),
        renderers=renderers(),
        data_root=device_data_root(),
        instance_id=form.get("id") or default_id,
        kind_id=kind_id,
        name=form.get("name") or "",
        panel_overrides=panel_overrides,
        access_token=discovered_token if isinstance(discovered_token, str) else None,
    )
    if not result.ok or result.device is None:
        flash(result.error or "Failed to register device.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    cache.forget(discovered_id)
    # Same picture-quality seeding as the manual add-device path.
    renderer_loader.seed_device_settings_from_base(renderers(), settings_store())
    rebuild_transport_fn()()
    flash(f"Registered discovered device {result.device.name!r}.", "ok")
    return redirect(
        url_for("auth.settings_area", area="devices", _anchor=f"device-{result.device.id}")
    )


@bp.post("/settings/devices/discovery/<discovered_id>/dismiss")
def devices_dismiss_discovered(discovered_id: str) -> Response:
    """Drop a discovered device, and clear its retained status message on
    the broker so it stays gone.

    A client publishes its heartbeat retained, so the broker replays it to
    the discovery wildcard on every connect, dismissing only the
    in-memory cache lets a stale/renamed device (e.g. one reflashed to a
    new id) pop straight back. Publishing an empty retained payload to its
    status topic clears the retention; ``DiscoveryCache.record`` ignores
    that empty tombstone so it doesn't re-add a kind-less ghost. A live
    device simply re-announces on its next heartbeat."""
    cache_had = discovery_cache().forget(discovered_id)
    try:
        transport().publish(f"tesserae/{discovered_id}/status", b"", qos=1, retain=True)
        flash(f"Dismissed {discovered_id!r} and cleared its retained heartbeat.", "ok")
    except Exception as exc:  # transport offline, cache is still cleared
        if cache_had:
            flash(
                f"Dismissed {discovered_id!r}, but couldn't clear the retained "
                f"message (broker offline: {exc}); it may reappear on reconnect.",
                "error",
            )
        else:
            flash(f"{discovered_id!r} wasn't in the discovery cache.", "error")
    return redirect(url_for("auth.settings_area", area="devices"))


# -- per-subsection updates --------------------------------------------
# These match individual cards on the device card. The combined ``/save``
# endpoint below replaces them in the UI, but they stay available for
# programmatic callers / direct hits.


@bp.post("/settings/devices/<instance_id>/panel")
def devices_update_panel(instance_id: str) -> Response:
    """Update a registered instance's panel dims + orientation, then
    hot-reload it so pages bound to this device pick up the new size
    without a restart."""
    anchor = f"device-{instance_id}"
    form = request.form
    try:
        new_w = int(form.get("panel_w") or 0)
        new_h = int(form.get("panel_h") or 0)
    except ValueError:
        flash("Panel width and height must be whole numbers.", "error")
        return redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))

    underscan_raw = form.get("panel_underscan")
    underscan: int | None = None
    if underscan_raw:
        try:
            underscan = max(0, int(underscan_raw))
        except ValueError:
            underscan = 0

    result = device_service.update_instance_panel(
        devices=devices(),
        renderers=renderers(),
        data_root=device_data_root(),
        instance_id=instance_id,
        w=new_w,
        h=new_h,
        orientation=form.get("panel_orientation") or "landscape",
        gamut=form.get("panel_gamut"),
        underscan=underscan,
        icon=form.get("device_icon"),
    )
    if not result.ok or result.device is None:
        flash(result.error or "Panel update failed.", "error")
        return redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))
    rebuild_transport_fn()()
    panel = result.device.panel or {}
    flash(
        f"Updated {result.device.name!r} panel to "
        f"{panel.get('w')}×{panel.get('h')} at {orientation_label(panel.get('orientation', 'landscape'))}.",
        "ok",
    )
    return redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))


@bp.post("/settings/devices/<instance_id>/quiet-hours")
def devices_update_quiet_hours(instance_id: str) -> Response:
    """Save a per-device override for the global quiet-hours window.

    The override is keyed off the device's manifest ``quiet_hours``
    block; ``app.quiet_hours.resolve_quiet_hours`` reads it and prefers
    it over the app-level setting when ``enabled`` is true. Clearing
    every field drops the block entirely so the device falls back to
    the app default."""
    anchor = f"device-{instance_id}"
    form = request.form
    result = device_service.update_instance_quiet_hours(
        devices=devices(),
        renderers=renderers(),
        data_root=device_data_root(),
        instance_id=instance_id,
        enabled=bool(form.get("quiet_hours_enabled")),
        start=form.get("quiet_hours_start"),
        end=form.get("quiet_hours_end"),
    )
    if not result.ok or result.device is None:
        flash(result.error or "Couldn't save quiet hours.", "error")
        return redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))
    qh = (result.device.manifest.get("quiet_hours") or {}) if result.device else {}
    if qh.get("enabled") and qh.get("start") and qh.get("end"):
        flash(
            f"Saved quiet hours for {result.device.name!r}: {qh.get('start')} → {qh.get('end')}.",
            "ok",
        )
    else:
        flash(
            f"Cleared per-device quiet hours for {result.device.name!r} "
            f"(falls back to app setting).",
            "ok",
        )
    return redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))


# -- combined save ------------------------------------------------------


@bp.post("/settings/devices/<instance_id>/save")
def devices_update_combined(instance_id: str) -> Response:
    """One-shot save for the whole device card.

    The card composes three independent subsections, renderer-defined
    config fields, panel (orientation/dims/gamut/icon/underscan), and
    per-device quiet-hours override. The template now wraps them in
    one form posting here; this handler fans out to the same service
    helpers the per-subsection endpoints (``/panel``, ``/quiet-hours``,
    and ``/settings/device-<id>``) call so behaviour matches one-for-
    one. Each subsection is detected by presence of its inputs and
    runs independently, an error in one is flashed but doesn't block
    the others. Transport rebuild happens once at the end."""
    anchor = f"device-{instance_id}"
    redirect_to = redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))

    devices_registry = devices()
    device = devices_registry.get(instance_id)
    if device is None or device.kind_of is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect_to

    form = request.form
    store = settings_store()
    ok_messages: list[str] = []
    any_change = False

    # 0. Display name. Input is only present for instances; the field is
    # bounded at 64 chars in the template, but we trim + cap here too so a
    # crafted POST can't sneak past. A blank submission is rejected (not
    # silently restored) because that's likely a UI error worth flagging.
    if "device_name" in form:
        new_name = (form.get("device_name") or "").strip()[:64]
        if not new_name:
            flash(f"{device.name} display name can't be empty.", "error")
        elif new_name != device.name:
            rename_err: str | None = None
            try:
                _apply_rename(device, new_name)
            except (OSError, json.JSONDecodeError) as exc:
                rename_err = str(exc)
            if rename_err is not None:
                flash(f"Couldn't rename {device.name!r}: {rename_err}", "error")
            else:
                ok_messages.append(f"renamed to {new_name!r}")
                any_change = True

    # 1. Renderer-defined config fields. Mirror settings_update("device-<id>").
    # Two paths: MQTT publish (when ``config_topic`` is set) and
    # save-only (when it isn't, HTTP-polled TRMNL clients pick up
    # config from the next /api/display response).
    schema_fields = config_fields_from_schema(device.config_schema)
    if schema_fields:
        values = values_from_form(schema_fields)
        ok, err = device.validate_config(values)
        if not ok:
            flash(f"Invalid {device.name} config: {err}", "error")
        elif device.config_topic is None:
            store.update_for_namespace("devices", instance_id, values, schema_fields)
            ok_messages.append("config saved")
            any_change = True
        else:
            store.update_for_namespace("devices", instance_id, values, schema_fields)
            tr = transport()
            try:
                tr.publish(
                    device.config_topic,
                    json.dumps(values).encode("utf-8"),
                    qos=1,
                    retain=True,
                )
                ok_messages.append("config saved and published")
            except RuntimeError as exc:
                flash(f"{device.name} config saved, publish failed: {exc}", "error")
                ok_messages.append("config saved (publish failed)")
            any_change = True

    # 2. Picture-quality fields per renderer clone. Inputs are named
    # ``<clone_id>:<field>`` so a device with multiple renderers can
    # surface the same field name (e.g. "saturation") for each without
    # collision. Bucket by clone_id, then write each bucket to its
    # clone's renderer-settings namespace.
    pq_buckets: dict[str, dict[str, Any]] = {}
    for raw_key in form:
        if ":" not in raw_key:
            continue
        clone_id, _, field_name = raw_key.partition(":")
        if "__" not in clone_id or not field_name:
            continue
        pq_buckets.setdefault(clone_id, {})[field_name] = form.get(raw_key)
    if pq_buckets:
        renderers_registry = renderers()
        pq_changed = False
        for clone_id, raw_values in pq_buckets.items():
            clone = renderers_registry.get(clone_id)
            if clone is None:
                continue
            # Clone id is ``<base>__<instance>``; refuse cross-device
            # writes rather than letting one card poke another's clone.
            if clone_id.split("__", 1)[1] != instance_id:
                continue
            dev_fields = [f for f in clone.manifest.get("settings", []) if f.get("device_setting")]
            if not dev_fields:
                continue
            coerced: dict[str, Any] = {}
            for field in dev_fields:
                name = str(field["name"])
                if name in raw_values:
                    coerced[name] = coerce_form_value(field, raw_values[name])
            if coerced:
                store.update_for_namespace("renderers", clone_id, coerced, dev_fields)
                pq_changed = True
        if pq_changed:
            ok_messages.append("picture quality saved")
            any_change = True

    # 3. Panel (orientation + dims + gamut + icon + underscan).
    if "panel_w" in form or "panel_h" in form:
        try:
            new_w = int(form.get("panel_w") or 0)
            new_h = int(form.get("panel_h") or 0)
        except ValueError:
            flash("Panel width and height must be whole numbers.", "error")
            return redirect_to
        underscan_raw = form.get("panel_underscan")
        underscan: int | None = None
        if underscan_raw:
            try:
                underscan = max(0, int(underscan_raw))
            except ValueError:
                underscan = 0
        panel_result = device_service.update_instance_panel(
            devices=devices_registry,
            renderers=renderers(),
            data_root=device_data_root(),
            instance_id=instance_id,
            w=new_w,
            h=new_h,
            orientation=form.get("panel_orientation") or "landscape",
            gamut=form.get("panel_gamut"),
            underscan=underscan,
            icon=form.get("device_icon"),
        )
        if not panel_result.ok or panel_result.device is None:
            flash(panel_result.error or "Panel update failed.", "error")
        else:
            ok_messages.append("panel updated")
            any_change = True

    # 4. Quiet-hours override. The inputs always submit on the combined
    # form (toggle, start, end). Empty start+end with toggle off clears
    # the block; the service helper handles that.
    if "quiet_hours_enabled" in form or "quiet_hours_start" in form or "quiet_hours_end" in form:
        qh_result = device_service.update_instance_quiet_hours(
            devices=devices_registry,
            renderers=renderers(),
            data_root=device_data_root(),
            instance_id=instance_id,
            enabled=bool(form.get("quiet_hours_enabled")),
            start=form.get("quiet_hours_start"),
            end=form.get("quiet_hours_end"),
        )
        if not qh_result.ok:
            flash(qh_result.error or "Couldn't save quiet hours.", "error")
        else:
            ok_messages.append("quiet hours saved")
            any_change = True

    if any_change:
        rebuild_transport_fn()()
    if ok_messages:
        flash(f"{device.name}: {', '.join(ok_messages)}.", "ok")
    return redirect_to


# -- delete + calibrate ------------------------------------------------


@bp.post("/settings/devices/<instance_id>/delete")
def devices_delete(instance_id: str) -> Response:
    """Remove a user-created device instance. Built-in kinds are
    refused, they ship with the app."""
    result = device_service.delete_instance(
        devices=devices(),
        renderers=renderers(),
        instance_id=instance_id,
    )
    if not result.ok or result.device is None:
        flash(result.error or "Delete failed.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    rebuild_transport_fn()()
    flash(f"Deleted device {result.device.name!r}.", "ok")
    return redirect(url_for("auth.settings_area", area="devices"))


@bp.post("/settings/devices/<instance_id>/calibrate")
def devices_calibrate(instance_id: str) -> Response:
    """Push the orientation test card to a device through its real
    renderer (so the on-panel result reflects the current settings),
    then prompt the user for what they see."""
    device = devices().get(instance_id)
    if device is None or device.kind_of is None or device.panel is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    panel = device.panel
    card = build_calibration_card(int(panel["w"]), int(panel["h"]))
    result = push_manager().push_image(
        card, source_label=f"calibration:{instance_id}", device_id=instance_id
    )
    if result.status == "sent":
        flash("Calibration card sent, look at your panel, then answer below.", "ok")
    else:
        flash(f"Calibration push {result.status}: {result.error or '(no detail)'}", "error")
    # ?calibrating=<id> makes the device card render the answer form.
    return redirect(
        url_for(
            "auth.settings_area",
            area="devices",
            calibrating=instance_id,
            _anchor=f"device-{instance_id}",
        )
    )


@bp.post("/settings/devices/<instance_id>/calibrate/apply")
def devices_calibrate_apply(instance_id: str) -> Response:
    """Set the orientation derived from the calibration answer, then
    re-push the card so the user can confirm it's now upright."""
    anchor = f"device-{instance_id}"
    device = devices().get(instance_id)
    if device is None or device.kind_of is None or device.panel is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    try:
        top_left = int(request.form.get("top_left") or 0)
    except ValueError:
        top_left = 0
    if top_left not in (1, 2, 3, 4):
        flash("Pick which number is in the panel's top-left corner.", "error")
        return redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))

    panel = device.panel
    pushed = str(panel.get("orientation") or "landscape")
    target = target_orientation(pushed, top_left)
    # Aspect drives the canvas dims; swap when the target aspect differs
    # from how the card was just pushed.
    w, h = int(panel["w"]), int(panel["h"])
    if calibration.is_portrait(target) != calibration.is_portrait(pushed):
        w, h = h, w

    result = device_service.update_instance_panel(
        devices=devices(),
        renderers=renderers(),
        data_root=device_data_root(),
        instance_id=instance_id,
        w=w,
        h=h,
        orientation=target,
    )
    if not result.ok or result.device is None:
        flash(result.error or "Calibration failed.", "error")
        return redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))
    rebuild_transport_fn()()
    # Confirm re-push at the new orientation.
    if result.device.panel is not None:
        card = build_calibration_card(int(result.device.panel["w"]), int(result.device.panel["h"]))
        push_manager().push_image(
            card, source_label=f"calibration:{instance_id}", device_id=instance_id
        )
    flash(
        f"Set {result.device.name!r} to {orientation_label(target)}, your dashboard now reads "
        "upright in that orientation. Re-sent the card to confirm; adjust Rotation below if needed.",
        "ok",
    )
    return redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))
