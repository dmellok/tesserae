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


@bp.post("/settings/devices/pair")
def devices_pair_issue() -> Response:
    """Mint a fresh REST pairing code from the Pair card on the
    Devices area. Sets a session reveal so the next GET shows the
    code in a copy-friendly modal. The PairingStore is the same one
    the ``/api/v1/device/admin/pairing/issue`` endpoint feeds; this
    route is the no-curl admin affordance."""
    store = current_app.config.get("PAIRING_STORE")
    if store is None:
        flash("REST pairing is not configured on this install.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    note = (request.form.get("note") or "").strip()[:64]
    record = store.issue(note=note)
    session["_rest_pairing_reveal"] = {
        "code": record.code,
        "expires_at": record.expires_at,
        "note": record.note,
    }
    flash("Pairing code issued. Paste it into the firmware's setup form.", "ok")
    return redirect(url_for("auth.settings_area", area="devices") + "#pair-device")


@bp.post("/settings/devices/pair/<code>/revoke")
def devices_pair_revoke(code: str) -> Response:
    """Drop a pending pairing code (admin changed their mind, or the
    user generated by mistake). Idempotent: revoking a code that's
    already gone flashes nothing and returns the user to the Devices
    page so the workflow keeps moving."""
    store = current_app.config.get("PAIRING_STORE")
    if store is None:
        return redirect(url_for("auth.settings_area", area="devices"))
    if store.revoke(code):
        flash(f"Pairing code {code} revoked.", "ok")
    return redirect(url_for("auth.settings_area", area="devices") + "#pair-device")


@bp.post("/settings/devices/<instance_id>/set-transport")
def devices_set_transport(instance_id: str) -> Response:
    """Flip a device instance between MQTT and REST transports.

    The transport field on the instance manifest decides whether the
    push pipeline does an MQTT publish for this device. Both halves
    of the flip are reversible without losing the device's id, panel
    settings, etc.

    MQTT → REST:
        - Sets ``transport: "rest"`` on the manifest.
        - Mints a per-device access_token if one isn't already there
          (kinds that aren't TRMNL-style won't have one yet).
        - Pops a one-shot reveal modal so the user can copy the token
          straight into firmware without having to query the file.

    REST → MQTT:
        - Removes the ``transport`` field (absence reads as MQTT per
          ``Device.transport``).
        - Keeps the access_token in place so flipping back to REST
          later doesn't force re-pairing.
        - The status/config topics derived at instance creation stay
          on the manifest, so MQTT mode works without re-deriving."""
    anchor = f"device-{instance_id}"
    redirect_to = redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))

    devs = devices()
    device = devs.get(instance_id)
    if device is None or device.kind_of is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect_to

    new_transport = (request.form.get("transport") or "").strip().lower()
    if new_transport not in ("mqtt", "rest"):
        flash("Transport must be 'mqtt' or 'rest'.", "error")
        return redirect_to

    try:
        raw = json.loads(device.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        flash(f"Couldn't read {device.path.name}: {err}", "error")
        return redirect_to

    if new_transport == "rest":
        raw["transport"] = "rest"
        device.manifest["transport"] = "rest"
        token = device.manifest.get("access_token")
        if not isinstance(token, str) or not token:
            token = device_service.generate_native_access_token(devs)
            raw["access_token"] = token
            device.manifest["access_token"] = token
            # One-shot reveal for the user to copy into firmware.
            session["_trmnl_token_reveal"] = {
                "device_id": device.id,
                "device_name": device.name,
                "token": token,
            }
        flash(f"{device.name} switched to REST transport.", "ok")
    else:
        # REST → MQTT. Drop the transport field; access_token stays so
        # a flip back to REST is just one click + reveals the same token.
        raw.pop("transport", None)
        device.manifest.pop("transport", None)
        flash(f"{device.name} switched to MQTT transport.", "ok")

    device.path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    # Push pipeline reads ``device.transport`` per render; no transport
    # rebuild needed (MQTT broker connection unchanged, REST routes
    # already registered).
    return redirect_to


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


@bp.post("/settings/devices/<instance_id>/reveal-token")
def devices_reveal_token(instance_id: str) -> Response:
    """Reveal the full access token for a device (issue #20). The
    Connection details strip on the device card masks the token to
    its first 4 chars + "..."; admins who closed the one-shot reveal
    modal previously had no path back to the value without reading
    the on-disk manifest. This route stashes the token in the same
    session reveal slot the regenerate flow uses, so the next page
    render pops the modal with the existing token.

    The reveal is logged to the EventLog so an admin can audit who
    surfaced the token and when."""
    anchor = f"device-{instance_id}"
    redirect_to = redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))

    devs = devices()
    device = devs.get(instance_id)
    if device is None or device.kind_of is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect_to
    token = device.manifest.get("access_token")
    if not isinstance(token, str) or not token:
        flash(f"{device.name!r} doesn't use access tokens.", "error")
        return redirect_to

    session["_trmnl_token_reveal"] = {
        "device_id": device.id,
        "device_name": device.name,
        "token": token,
    }
    event_log = current_app.config.get("EVENT_LOG")
    if event_log is not None:
        try:
            event_log.record(
                type="device",
                source="settings_ui",
                target=device.id,
                status="ok",
                extra={"action": "token_revealed"},
            )
        except Exception:
            current_app.logger.exception(
                "event_log: record(token_revealed) failed for %s", device.id
            )
    return redirect_to


@bp.post("/settings/devices/kinds/<kind_id>/defaults")
def devices_kind_defaults_save(kind_id: str) -> Response:
    """Persist a per-kind defaults override (issue #22). Whitelisted
    fields only: ``display_name``, ``panel_preset``, ``panel_w``,
    ``panel_h``, ``panel_orientation``, ``sleep_interval_s``. The
    store does its own validation + coercion; this route just
    parses the form, dispatches to ``KindOverridesStore.set()``, and
    flashes a result.

    Built-in kinds only (``kind.kind_of`` is ``None``); instances
    have their own per-instance editing path."""
    from app.state.kind_overrides import KindOverridesStore

    redirect_to = redirect(url_for("auth.settings_area", area="devices", _anchor=f"kind-{kind_id}"))
    devs = devices()
    kind = devs.get(kind_id)
    if kind is None or kind.kind_of is not None:
        flash(f"Unknown built-in kind {kind_id!r}.", "error")
        return redirect_to

    values: dict[str, Any] = {}
    if "display_name" in request.form:
        values["display_name"] = request.form.get("display_name", "")
    if "panel_preset" in request.form:
        values["panel_preset"] = request.form.get("panel_preset", "")
    if "panel_w" in request.form:
        values["panel_w"] = request.form.get("panel_w", "")
    if "panel_h" in request.form:
        values["panel_h"] = request.form.get("panel_h", "")
    if "panel_orientation" in request.form:
        values["panel_orientation"] = request.form.get("panel_orientation", "")
    if "sleep_interval_s" in request.form:
        values["sleep_interval_s"] = request.form.get("sleep_interval_s", "")

    store = KindOverridesStore(device_data_root())
    saved = store.set(kind_id, values)
    event_log = current_app.config.get("EVENT_LOG")
    if event_log is not None:
        try:
            event_log.record(
                type="device",
                source="settings_ui",
                target=kind_id,
                status="ok",
                extra={"action": "kind_defaults_saved", "values": saved},
            )
        except Exception:
            current_app.logger.exception(
                "event_log: record(kind_defaults_saved) failed for %s", kind_id
            )
    flash(
        f"Defaults saved for {kind.name!r}. New instances of this kind will use them."
        if saved
        else f"Cleared overrides for {kind.name!r}; reverting to bundled defaults.",
        "ok",
    )
    return redirect_to


@bp.post("/settings/devices/kinds/<kind_id>/reset")
def devices_kind_defaults_reset(kind_id: str) -> Response:
    """Delete the per-kind overrides file and revert to bundled
    defaults (issue #22). Used by the inline confirm bar on the
    kind row."""
    from app.state.kind_overrides import KindOverridesStore

    redirect_to = redirect(url_for("auth.settings_area", area="devices", _anchor=f"kind-{kind_id}"))
    devs = devices()
    kind = devs.get(kind_id)
    if kind is None or kind.kind_of is not None:
        flash(f"Unknown built-in kind {kind_id!r}.", "error")
        return redirect_to

    store = KindOverridesStore(device_data_root())
    removed = store.delete(kind_id)
    event_log = current_app.config.get("EVENT_LOG")
    if event_log is not None:
        try:
            event_log.record(
                type="device",
                source="settings_ui",
                target=kind_id,
                status="ok",
                extra={"action": "kind_defaults_reset", "removed": removed},
            )
        except Exception:
            current_app.logger.exception(
                "event_log: record(kind_defaults_reset) failed for %s", kind_id
            )
    flash(
        f"Reset {kind.name!r} defaults to built-in."
        if removed
        else f"{kind.name!r} already on built-in defaults.",
        "ok",
    )
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

    # Guard against firmware reporting panel_w / panel_h as zero (a
    # default-int from a C struct that wasn't populated). Without this
    # check the resulting instance lands with a corrupted panel that
    # Panel(w > 0, h > 0) rejects later, breaking /send. Better to let
    # create_instance fall back to the kind's default panel.
    panel_overrides: dict[str, Any] = {}
    if entry.panel_w is not None and entry.panel_w > 0:
        panel_overrides["w"] = entry.panel_w
    if entry.panel_h is not None and entry.panel_h > 0:
        panel_overrides["h"] = entry.panel_h

    # TRMNL discoveries carry the original access_token in the cache
    # entry's parsed payload so create_instance can preserve it, the
    # user already has it pasted into their client config, and the
    # whole point of one-click pairing is not making them re-paste a
    # freshly-generated one. EXCEPT when the discovery flagged
    # ``needs_pairing``, in that case the client was polling with the
    # firmware's literal placeholder token (e.g.
    # ``paste-a-server-issued-token-into-your-client``), and preserving
    # it would lock the new instance to a publicly-known string.
    # Force a fresh mint there, and the reveal modal will surface it
    # for the user to paste back into the client.
    discovered_needs_pairing = (
        bool(entry.parsed.get("needs_pairing")) if kind_id == "trmnl_client" else False
    )
    discovered_token = entry.parsed.get("access_token") if kind_id == "trmnl_client" else None
    if discovered_needs_pairing:
        discovered_token = None
    # The synthetic discovery id for TRMNL entries (``trmnl_<token>``
    # or ``trmnl_<mac>``) leaks identifiers into the device id if used
    # as-is; default the instance id to a friendlier one and let the
    # user override if they want.
    default_id = discovered_id
    if kind_id == "trmnl_client" and default_id.startswith("trmnl_"):
        if isinstance(discovered_token, str):
            default_id = "trmnl_" + discovered_token[:5]
        else:
            default_id = "trmnl_new"
    # REST-discovered devices (via POST /api/v1/device/discover) carry
    # a ``transport: "rest"`` hint in the cache payload AND a MAC. We
    # propagate both into the new instance so the firmware's next
    # discover POST claims the token by MAC match, and the push
    # pipeline skips MQTT publish.
    is_rest_discovery = entry.parsed.get("transport") == "rest"
    transport_arg = "rest" if is_rest_discovery else None
    mac_arg = str(entry.parsed.get("mac") or "").strip() or None if is_rest_discovery else None
    result = device_service.create_instance(
        devices=devices(),
        renderers=renderers(),
        data_root=device_data_root(),
        instance_id=form.get("id") or default_id,
        kind_id=kind_id,
        name=form.get("name") or "",
        panel_overrides=panel_overrides,
        access_token=discovered_token if isinstance(discovered_token, str) else None,
        mac=mac_arg,
        transport=transport_arg,
    )
    if not result.ok or result.device is None:
        flash(result.error or "Failed to register device.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    cache.forget(discovered_id)
    # Same picture-quality seeding as the manual add-device path.
    renderer_loader.seed_device_settings_from_base(renderers(), settings_store())
    rebuild_transport_fn()()
    # If the discovery was unpaired (placeholder token) AND
    # create_instance therefore minted a fresh token, surface it via
    # the existing one-shot reveal modal. Stash the device's polling
    # IP too so the modal can say "paste this token into the client at
    # <IP>'s config" rather than just "into your client".
    new_token = result.device.manifest.get("access_token")
    if discovered_needs_pairing and isinstance(new_token, str) and new_token:
        session["_trmnl_token_reveal"] = {
            "device_id": result.device.id,
            "device_name": result.device.name,
            "token": new_token,
            "client_ip": entry.parsed.get("ip"),
        }
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
    device simply re-announces on its next heartbeat.

    The retained-message clear only matters when MQTT is in play. A
    REST-only install has no broker connection at all, so we skip the
    publish there silently rather than flashing a misleading "broker
    offline" error on what was actually a clean dismiss."""
    cache_had = discovery_cache().forget(discovered_id)
    tr = transport()
    if not tr.connected:
        # REST-only install or broker not yet connected: nothing to clear
        # on the broker side, the cache dismiss is the whole operation.
        if cache_had:
            flash(f"Dismissed {discovered_id!r}.", "ok")
        else:
            flash(f"{discovered_id!r} wasn't in the discovery cache.", "info")
        return redirect(url_for("auth.settings_area", area="devices"))
    try:
        tr.publish(f"tesserae/{discovered_id}/status", b"", qos=1, retain=True)
        flash(f"Dismissed {discovered_id!r} and cleared its retained heartbeat.", "ok")
    except Exception as exc:
        # Broker was connected but the publish itself failed: cache is
        # still cleared, surface the broker fault as a warning, not a
        # success-or-error binary.
        if cache_had:
            flash(
                f"Dismissed {discovered_id!r}, but couldn't clear the retained "
                f"message (broker error: {exc}); it may reappear on reconnect.",
                "error",
            )
        else:
            flash(f"{discovered_id!r} wasn't in the discovery cache.", "info")
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
    # Drop the device's smart-sync telemetry too (issue #10) so a
    # future device that happens to reuse the id starts with a clean
    # confidence counter instead of inheriting stale state.
    telemetry = current_app.config.get("DEVICE_TELEMETRY")
    if telemetry is not None:
        telemetry.forget(instance_id)
    # Same purge for the battery_history store. Without this the
    # ``/devices/battery`` dashboard kept showing a card for the
    # deleted device (the index iterates ``store.device_ids()``
    # union with currently-reporting devices, and historical rows
    # in the SQLite table outlived the registry entry).
    battery_history = current_app.config.get("BATTERY_HISTORY")
    if battery_history is not None:
        try:
            battery_history.forget(instance_id)
        except Exception:
            current_app.logger.exception("battery_history: forget failed for %s", instance_id)
    # And the live device_status cache so a stale "last seen" or
    # parsed-heartbeat block doesn't tail-render anywhere (events,
    # ha-discovery refresh callbacks, etc).
    status_cache = current_app.config.get("DEVICE_STATUS")
    if isinstance(status_cache, dict):
        status_cache.pop(instance_id, None)
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
