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
from pathlib import Path
from typing import Any

from flask import Response as FlaskResponse
from flask import current_app, flash, redirect, request, session, url_for
from werkzeug.wrappers import Response

from app import calibration, device_cleanup, device_service, renderer_loader, test_patterns
from app.button_actions import (
    ButtonActionError,
    parse_action_spec,
    registered_actions,
)
from app.calibration import build_calibration_card, target_orientation
from app.panel import panel_overrides_from_form
from app.state.deleted_device_markers import DeletedDeviceMarkers

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


def _dev_seed_kinds() -> list[tuple[str, str, str]]:
    """Dev-mode seed set: (kind_id, instance_id_prefix, human name).

    Covers the main client kinds so the tone/dither, calibration, and
    picture-quality UI paths all have something to render against
    without needing real hardware. Kept as a plain list rather than a
    scan of every available kind so seed output stays predictable.
    """
    return [
        ("esp32_client", "dev_esp32", "Dev ESP32"),
        ("pi_bin_client", "dev_pi_bin", "Dev Pi (bin)"),
        ("pi_png_client", "dev_pi_png", "Dev Pi (png)"),
        ("picpak_client", "dev_picpak", "Dev PicPak"),
        ("trmnl_client", "dev_trmnl", "Dev TRMNL"),
    ]


@bp.post("/settings/devices/dev-seed")
def devices_dev_seed() -> Response:
    """Seed a set of dummy devices for local UI testing.

    Guarded by ``DEV_MODE`` so the affordance doesn't ship to
    production installs. Idempotent: instance ids that already exist
    are skipped without error, so hitting the button repeatedly is
    safe. Useful when reproducing device-card bugs without real
    hardware attached.
    """
    if not current_app.config.get("DEV_MODE"):
        flash("Dev seeding is disabled outside of --dev mode.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))

    devices_registry = devices()
    renderers_registry = renderers()
    created: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for kind_id, instance_id, name in _dev_seed_kinds():
        if devices_registry.get(kind_id) is None:
            skipped.append(f"{instance_id} (kind {kind_id!r} not installed)")
            continue
        if instance_id in devices_registry.devices:
            skipped.append(instance_id)
            continue
        result = device_service.create_instance(
            devices=devices_registry,
            renderers=renderers_registry,
            data_root=device_data_root(),
            instance_id=instance_id,
            kind_id=kind_id,
            name=name,
        )
        if result.ok and result.device is not None:
            created.append(result.device.id)
        else:
            failed.append(f"{instance_id} ({result.error or 'unknown error'})")

    if created:
        renderer_loader.seed_device_settings_from_base(renderers_registry, settings_store())
        rebuild_transport_fn()()

    parts: list[str] = []
    if created:
        parts.append(f"added {len(created)} ({', '.join(created)})")
    if skipped:
        parts.append(f"skipped {len(skipped)}")
    if failed:
        parts.append(f"failed {len(failed)}: {', '.join(failed)}")
    if parts:
        flash("Dev-seed: " + "; ".join(parts) + ".", "ok" if not failed else "error")
    else:
        flash("Dev-seed: nothing to do.", "ok")
    return redirect(url_for("auth.settings_area", area="devices"))


# -- add / discover / dismiss ------------------------------------------


def _make_rest_instance(device: Any) -> str | None:
    """Put a freshly created instance on the REST transport and give it a
    token, the same end state as pairing or the card's transport switch.

    Returns the token, or None if the instance file couldn't be rewritten.
    The caller's one-shot reveal reads it back off the manifest."""
    try:
        raw = json.loads(device.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        flash(f"Created {device.name!r}, but couldn't set REST transport: {err}", "error")
        return None
    raw["transport"] = "rest"
    device.manifest["transport"] = "rest"
    token = device.manifest.get("access_token")
    if not isinstance(token, str) or not token:
        token = device_service.generate_native_access_token(devices())
        raw["access_token"] = token
        device.manifest["access_token"] = token
    device.path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return str(token)


@bp.post("/settings/devices/add")
def devices_add() -> Response:
    """Create a new device instance from the Devices-tab form. No restart
    needed, the new device shows up immediately in the page editor's
    Target-device dropdown."""
    form = request.form
    target_id = form.get("id") or ""
    # v0.71.x (issue #48 follow-up): manual add doesn't know the
    # incoming MAC (the form doesn't collect one), so the MAC-differs
    # heuristic the discovery path uses can't fire. Users typing an id
    # that matches a previously-deleted instance almost always mean
    # "fresh device, same id by choice" - keeping the leftover
    # dashboards / history / settings under that id from the deleted
    # device is a genuine surprise. Wipe orphan state when a marker is
    # present, then clear the marker so re-runs don't double-wipe.
    if target_id:
        markers = DeletedDeviceMarkers(Path(current_app.config["DATA_ROOT"]))
        if markers.get(target_id) is not None:
            device_cleanup.wipe_orphan_state(
                device_id=target_id,
                page_store=current_app.config["PAGE_STORE"],
                event_log=current_app.config["EVENT_LOG"],
                settings_store=settings_store(),
                data_root=Path(current_app.config["DATA_ROOT"]),
                push_manager=current_app.config.get("PUSH_MANAGER"),
                devices=devices(),
            )
            markers.clear(target_id)
    result = device_service.create_instance(
        devices=devices(),
        renderers=renderers(),
        data_root=device_data_root(),
        instance_id=target_id,
        kind_id=(form.get("kind") or "").strip(),
        name=form.get("name") or "",
        panel_overrides=panel_overrides_from_form(form),
        orientation=form.get("panel_orientation"),
    )
    if not result.ok or result.device is None:
        flash(result.error or "Failed to add device.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    # A device that will never call /register needs to exist without pairing:
    # a digital sign, a browser tab, anything that only fetches an image URL
    # (discussion #240). Pairing is still the right default for firmware that
    # can run the handshake; this is the escape hatch for what cannot.
    if (form.get("transport") or "").strip().lower() == "rest":
        _make_rest_instance(result.device)
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
    # Persist any config-schema values the add form actually supplied (e.g.
    # the OpenDisplay HA device id picked from the dropdown), the same way
    # the device card's Save does, so the device is usable without a second
    # trip to its card. Only fields present in the form are stored, so a
    # kind whose config fields aren't on the add form keeps its defaults
    # rather than getting them written out; only stored when valid.
    present_fields = [
        f
        for f in config_fields_from_schema(result.device.config_schema)
        if f["name"] in request.form
    ]
    if present_fields:
        values = values_from_form(present_fields)
        ok, _err = result.device.validate_config(values)
        if ok:
            settings_store().update_for_namespace(
                "devices", result.device.id, values, present_fields
            )
    # New device's clones inherit picture-quality (dither/saturation/
    # contrast) from the user's existing base-renderer values where
    # available, so a freshly-added device matches the rest of the
    # fleet rather than dropping back to the manifest defaults.
    renderer_loader.seed_device_settings_from_base(renderers(), settings_store())
    rebuild_transport_fn()()
    flash(f"Added device {result.device.name!r}.", "ok")
    return redirect(
        url_for(
            "auth.settings_area",
            area="devices",
            opened=result.device.id,
            _anchor=f"device-{result.device.id}",
        )
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
    redirect_to = redirect(
        url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
    )

    devs = devices()
    device = devs.get(instance_id)
    if device is None or device.kind_of is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect_to
    if device.transport == "relay":
        # A remote panel reaches this server only through the relay; flipping
        # it to MQTT/REST would silently orphan it. The card hides the switch
        # for relay devices, so this only guards a hand-crafted POST.
        flash(f"{device.name} is a remote relay panel; its transport can't be switched.", "error")
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
    redirect_to = redirect(
        url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
    )

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
    redirect_to = redirect(
        url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
    )

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


def _reproject_wake(instance_id: str, values: dict[str, Any]) -> None:
    """Re-derive the wake prediction after a sleep-interval edit (#246).

    No-op when the device publishes its own wake time, when the interval
    did not change, or when telemetry isn't wired.

    A device switched to stay awake reprojects against its awake poll
    cadence instead: that's the interval it will actually come back on,
    and leaving the prediction on the old sleep interval would make it
    read as overdue for the rest of that interval.
    """
    raw = device_service.awake_poll_interval_s(values) or values.get("sleep_interval_s")
    if raw in (None, ""):
        return
    try:
        interval = int(raw)
    except (TypeError, ValueError):
        return
    telemetry = current_app.config.get("DEVICE_TELEMETRY")
    if telemetry is None:
        return
    try:
        telemetry.reproject(instance_id, interval)
    except Exception:
        current_app.logger.exception("devices: wake reprojection failed for %s", instance_id)


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


@bp.get("/settings/devices/ha-devices.json")
def devices_ha_list() -> Response:
    """List a Home Assistant integration's devices for the config-form
    picker (issue: OpenDisplay). ``?integration=<domain>`` selects which.
    Always 200s: on no HA / error it returns an empty list plus a message
    so the picker degrades to manual entry."""
    from app.ha_device_picker import list_integration_devices

    integration = (request.args.get("integration") or "").strip().lower()
    registry = current_app.config.get("PLUGIN_REGISTRY")
    plugin = registry.get("ha_core") if registry is not None else None
    mod = getattr(plugin, "server_module", None) if plugin is not None else None
    found, error = list_integration_devices(mod, integration)
    payload = {"devices": found, "error": error}
    return current_app.response_class(json.dumps(payload), mimetype="application/json")


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
    explicit_kind = (form.get("kind") or "").strip()
    kind_id = explicit_kind or entry.kind or ""
    if not kind_id:
        flash(
            f"Discovered device {discovered_id!r} didn't advertise a kind; "
            "register it manually via the Add-device form.",
            "error",
        )
        return redirect(url_for("auth.settings_area", area="devices"))

    # Did the user pick a different variant from the picker (issue #82)?
    # The two 4" Inky panels share the pi_bin protocol but differ in gamut
    # and dims; a pi_bin client can't say which it is, so its heartbeat
    # dims / gamut are exactly what we distrust here. An explicit variant
    # pick is the stronger signal: skip the heartbeat panel overrides so
    # the chosen kind's authoritative panel stands unmodified.
    user_picked_variant = bool(explicit_kind) and explicit_kind != (entry.kind or "")

    # Guard against firmware reporting panel_w / panel_h as zero (a
    # default-int from a C struct that wasn't populated). Without this
    # check the resulting instance lands with a corrupted panel that
    # Panel(w > 0, h > 0) rejects later, breaking /send. Better to let
    # create_instance fall back to the kind's default panel.
    panel_overrides: dict[str, Any] = {}
    if not user_picked_variant:
        if entry.panel_w is not None and entry.panel_w > 0:
            panel_overrides["w"] = entry.panel_w
        if entry.panel_h is not None and entry.panel_h > 0:
            panel_overrides["h"] = entry.panel_h
        # Gamut carried in the discover payload (v0.69.1, issue #41) lets a
        # generic CircuitPython client tell Tesserae which colour target it
        # paints against so the same generic kind can drive different-shape
        # panels without a per-SKU manifest add. Canonicalise here so the
        # on-disk value always matches the .bin packer's lookup keys or an
        # accepted metadata label (mono / rgb24 / rgb16).
        if entry.gamut:
            from app.quantizer import canonicalise_gamut

            panel_overrides["gamut"] = canonicalise_gamut(entry.gamut)

    # A client that paints a tall panel used to land with portrait dims and the
    # kind's landscape orientation. Nothing reads that contradiction until the
    # first save of the panel form, which resolves it by rewriting the dims to
    # match the orientation: the user's 1200x1920 silently became 1920x1200
    # (issue #200). Resolve the announce's geometry up front so the stored panel
    # is self-consistent from the moment it's created, honouring a declared
    # ``rotation`` when the client sent one.
    geometry, reported_orientation = device_service.panel_geometry_from_report(
        w=panel_overrides.get("w"),
        h=panel_overrides.get("h"),
        rotation=entry.rotation,
    )
    panel_overrides.update(geometry)

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
    # ``usable_mac`` drops placeholders ("None", all-zero, ...) so one
    # never lands on an instance where a second client sending the same
    # string could claim its token (issue #226).
    mac_arg = device_service.usable_mac(entry.parsed.get("mac")) if is_rest_discovery else None
    # v0.69.2 (issue #48): if the id has orphan state from a previous
    # deletion AND the incoming MAC differs from what was stored, wipe
    # the leftovers so the new device starts pristine. Matching MACs
    # keep state. Marker gets cleared either way so future registers
    # don't keep triggering.
    target_id = form.get("id") or default_id
    markers = DeletedDeviceMarkers(Path(current_app.config["DATA_ROOT"]))
    if markers.mac_differs(target_id, mac_arg):
        device_cleanup.wipe_orphan_state(
            device_id=target_id,
            page_store=current_app.config["PAGE_STORE"],
            event_log=current_app.config["EVENT_LOG"],
            settings_store=settings_store(),
            data_root=Path(current_app.config["DATA_ROOT"]),
            push_manager=current_app.config.get("PUSH_MANAGER"),
            devices=devices(),
        )
    markers.clear(target_id)
    # Wire format the client asked for (png / bmp). A memory-constrained
    # CircuitPython client declares "bmp" so it gets the uncompressed-BMP
    # renderer and never runs zlib.decompress on-device. Resolved to a
    # concrete renderer of the chosen kind; None leaves the kind default.
    renderers_registry = renderers()
    renderer_id_arg = device_service.renderer_id_for_format(
        renderers_registry, devices().get(kind_id), entry.wire_format
    )
    # Display name: the form field wins when present (even cleared, the
    # admin's empty submit means "no name"); a poster without the field
    # (setup wizard JSON path) falls back to the announce's suggested
    # ``name`` so a client that sent one on /discover isn't ignored
    # (discussion #24).
    name_arg = form.get("name") if "name" in form else (entry.name or "")
    result = device_service.create_instance(
        devices=devices(),
        renderers=renderers_registry,
        data_root=device_data_root(),
        instance_id=form.get("id") or default_id,
        kind_id=kind_id,
        name=name_arg or "",
        panel_overrides=panel_overrides,
        orientation=reported_orientation,
        access_token=discovered_token if isinstance(discovered_token, str) else None,
        mac=mac_arg,
        transport=transport_arg,
        renderer_id=renderer_id_arg,
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
        url_for(
            "auth.settings_area",
            area="devices",
            opened=result.device.id,
            _anchor=f"device-{result.device.id}",
        )
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
        flash("Logical panel width and height must be whole numbers.", "error")
        return redirect(
            url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
        )

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
        return redirect(
            url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
        )
    rebuild_transport_fn()()
    panel = result.device.panel or {}
    flash(
        f"Updated {result.device.name!r} panel to "
        f"{panel.get('w')}×{panel.get('h')} at {orientation_label(panel.get('orientation', 'landscape'))}.",
        "ok",
    )
    return redirect(
        url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
    )


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
        return redirect(
            url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
        )
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
    return redirect(
        url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
    )


@bp.post("/settings/devices/<instance_id>/album/resync")
def devices_album_resync(instance_id: str) -> Response:
    """Force this device to re-sync its offline album (#247).

    The collection version is a digest of the manifest content, so it moves
    only when the album does. That is right almost always and useless in the
    one case you need a lever: server and device disagree for a reason the
    content can't express (an interrupted sync, a swapped card, frames the
    device dropped). Bumping a per-device token changes the version the next
    check-in sees, which firmware already knows how to handle.

    Nothing is pushed here. The device picks this up on its own next wake."""
    anchor = f"device-{instance_id}"
    redirect_to = redirect(
        url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
    )

    device = devices().get(instance_id)
    if device is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect_to

    store = current_app.config.get("ALBUM_STORE")
    resync_store = current_app.config.get("COLLECTION_RESYNC_STORE")
    if store is None or resync_store is None:
        flash("Offline albums aren't available on this install.", "error")
        return redirect_to

    from app.collection_sync import bound_album_for

    album = bound_album_for(store, device.id)
    if album is None:
        flash(f"No offline album is bound to {device.name!r}.", "error")
        return redirect_to

    resync_store.bump(device.id, album_id=album.id)
    flash(
        f"{album.name!r} will re-sync on {device.name!r}'s next check-in. "
        f"A sleeping panel won't act on this until it wakes.",
        "ok",
    )
    return redirect_to


@bp.post("/settings/devices/<instance_id>/battery-offset")
def devices_update_battery_offset(instance_id: str) -> Response:
    """Save a per-device battery-display offset.

    Two values, both signed: ``battery_offset_mv`` adjusts the
    displayed voltage to match a voltmeter reading; ``battery_offset_pct``
    adjusts the displayed percent. Both at zero drops the block from
    the manifest entirely so the device falls back to the raw
    firmware-reported values."""
    anchor = f"device-{instance_id}"
    form = request.form
    try:
        mv = int(form.get("battery_offset_mv") or 0)
        pct = int(form.get("battery_offset_pct") or 0)
    except ValueError:
        flash("Battery offsets must be whole numbers.", "error")
        return redirect(
            url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
        )
    result = device_service.update_instance_battery_offset(
        devices=devices(),
        renderers=renderers(),
        data_root=device_data_root(),
        instance_id=instance_id,
        mv=mv,
        pct=pct,
    )
    if not result.ok or result.device is None:
        flash(result.error or "Couldn't save battery offset.", "error")
        return redirect(
            url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
        )
    block = result.device.manifest.get("battery_offset") or {}
    if block:
        flash(
            f"Saved battery offset for {result.device.name!r}: "
            f"{block.get('mv', 0):+d} mV, {block.get('pct', 0):+d}%.",
            "ok",
        )
    else:
        flash(f"Cleared battery offset for {result.device.name!r}.", "ok")
    return redirect(
        url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
    )


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
    # v0.68: threading ``opened=<id>`` back through the redirect keeps
    # the device card expanded after Save. Without this the card
    # collapses on every save + reload, which meant the user's next
    # tweak needed another click on "Show settings" first.
    # v0.69.17: also thread ``tab=`` so save from General / Calibration
    # doesn't jump back to Status. The v0.69.14 tab-scoping fix reads
    # ``?tab=`` only when ``?opened=`` matches, so any redirect that
    # forgets ``tab=`` lands on the default (Status). The combined form
    # posts an ``_active_tab`` hidden field carrying whichever tab
    # rendered the card; we echo it back on redirect.
    _redirect_tab = (request.form.get("_active_tab") or "").strip()
    if _redirect_tab in ("status", "general", "schedule", "calibration"):
        redirect_to = redirect(
            url_for(
                "auth.settings_area",
                area="devices",
                opened=instance_id,
                tab=_redirect_tab,
                _anchor=anchor,
            )
        )
    else:
        redirect_to = redirect(
            url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
        )

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
    # Two paths: MQTT devices publish when ``config_topic`` is set;
    # REST devices save only and pick up config on their next status
    # poll. REST instances may retain dormant MQTT topics so they can
    # be switched back later, so topic presence alone is not enough to
    # decide whether to publish.
    schema_fields = config_fields_from_schema(device.config_schema)
    if schema_fields:
        values = values_from_form(schema_fields)
        ok, err = device.validate_config(values)
        if not ok:
            flash(f"Invalid {device.name} config: {err}", "error")
        elif device.transport == "relay":
            # Relay devices have no broker and no direct poll against this
            # server: save, then nudge the relay publisher so the sealed
            # config doc lands in the panel's relay mailbox for its next
            # wake. The device may retain dormant MQTT topics (kind
            # manifests declare them), so this branch must come before the
            # config_topic check.
            store.update_for_namespace("devices", instance_id, values, schema_fields)
            publisher = current_app.config.get("RELAY_PUBLISHER")
            if publisher is not None:
                publisher.on_config_change(instance_id)
            ok_messages.append("config saved and queued for the relay")
            any_change = True
        elif device.transport == "rest" or device.config_topic is None:
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

        # #246: the stored wake prediction was derived from the old
        # interval, so without this the card reads "overdue" and the
        # scheduler aims at a moment already past, until the device
        # happens to wake.
        _reproject_wake(instance_id, values)

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
            flash("Logical panel width and height must be whole numbers.", "error")
            return redirect_to
        underscan_raw = form.get("panel_underscan")
        underscan: int | None = None
        if underscan_raw:
            try:
                underscan = max(0, int(underscan_raw))
            except ValueError:
                underscan = 0
        # ``update_instance_panel`` normalises dims to match the orientation,
        # which is right when the rotation dropdown is what moved (its JS swaps
        # the dim fields live, and a hand-crafted POST should still land
        # consistent). It's wrong when the dims are what moved: typing tall dims
        # under an untouched landscape dropdown got them swapped straight back,
        # so a portrait panel could not be entered at all (issue #200). Decide
        # which side to trust by comparing against what's stored.
        submitted_orientation = (form.get("panel_orientation") or "").strip().lower()
        stored = devices_registry.get(instance_id)
        stored_panel = dict(getattr(stored, "panel", None) or {}) if stored else {}
        stored_orientation = str(stored_panel.get("orientation") or "").lower()
        orientation_arg = submitted_orientation or "landscape"
        if new_w and new_h and submitted_orientation in ("", stored_orientation):
            # The rotation didn't move, so the dims are the authority. This also
            # repairs a panel that was already stored inconsistent (firmware
            # reports dims but no orientation, so a tall panel used to land with
            # a landscape orientation) instead of swapping the dims on every
            # unrelated save. Keeps the ``_flipped`` half the user has set;
            # only landscape-vs-portrait follows the dims.
            flipped = submitted_orientation.endswith("_flipped") or stored_orientation.endswith(
                "_flipped"
            )
            aspect = "portrait" if new_h > new_w else "landscape"
            orientation_arg = f"{aspect}_flipped" if flipped else aspect
        panel_result = device_service.update_instance_panel(
            devices=devices_registry,
            renderers=renderers(),
            data_root=device_data_root(),
            instance_id=instance_id,
            w=new_w,
            h=new_h,
            orientation=orientation_arg,
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

    # 5. Battery-display offset. The two inputs always submit on the
    # combined form (mV + pct). Both at zero drops the block from the
    # manifest entirely.
    if "battery_offset_mv" in form or "battery_offset_pct" in form:
        try:
            mv = int(form.get("battery_offset_mv") or 0)
            pct = int(form.get("battery_offset_pct") or 0)
        except ValueError:
            flash("Battery offsets must be whole numbers.", "error")
            return redirect_to
        bo_result = device_service.update_instance_battery_offset(
            devices=devices_registry,
            renderers=renderers(),
            data_root=device_data_root(),
            instance_id=instance_id,
            mv=mv,
            pct=pct,
        )
        if not bo_result.ok:
            flash(bo_result.error or "Couldn't save battery offset.", "error")
        else:
            ok_messages.append("battery offset saved")
            any_change = True

    # 6. Per-device button map (physical button wakes). The textarea
    # ships raw JSON; parse strictly and reject on any bad shape so an
    # admin doesn't accidentally save a bogus map that silently no-ops
    # every button press. Empty submission (whitespace or absent
    # button_map key) clears the per-device override so the device
    # falls back to the global map / defaults.
    if "button_map_json" in form:
        raw = (form.get("button_map_json") or "").strip()
        bm_error: str | None = None
        parsed_map: dict[str, str] | None = None
        if raw:
            try:
                candidate = json.loads(raw)
            except json.JSONDecodeError as exc:
                bm_error = f"button map must be valid JSON: {exc.msg}"
                candidate = None
            if candidate is not None:
                if not isinstance(candidate, dict) or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in candidate.items()
                ):
                    bm_error = (
                        "button map must be a JSON object mapping button names to action specs."
                    )
                else:
                    known_actions = set(registered_actions())
                    for button_name, spec in candidate.items():
                        try:
                            action_name, _arg = parse_action_spec(spec)
                        except ButtonActionError as exc:
                            bm_error = f"button {button_name!r}: {exc}"
                            break
                        if action_name not in known_actions:
                            bm_error = (
                                f"button {button_name!r}: unknown action {action_name!r}. "
                                f"Available: {', '.join(sorted(known_actions))}."
                            )
                            break
                    else:
                        parsed_map = {k: v for k, v in candidate.items()}
        if bm_error is not None:
            flash(f"{device.name} button map: {bm_error}", "error")
        else:
            devices_section = store.get_section("devices") or {}
            device_section = dict(devices_section.get(instance_id) or {})
            existing = device_section.get("button_map")
            if parsed_map is None:
                # Empty submission -> clear the override.
                if isinstance(existing, dict) and existing:
                    device_section.pop("button_map", None)
                    store.patch_section("devices", {instance_id: device_section})
                    ok_messages.append("button map cleared")
                    any_change = True
            else:
                if existing != parsed_map:
                    device_section["button_map"] = parsed_map
                    store.patch_section("devices", {instance_id: device_section})
                    ok_messages.append("button map saved")
                    any_change = True

    if any_change:
        rebuild_transport_fn()()
        if device.transport == "relay":
            # Config-adjacent values outside the schema branch (button map,
            # always_on) also ride the relay config doc; sync after the
            # rebuild so the nudge lands on the freshly wired publisher.
            publisher = current_app.config.get("RELAY_PUBLISHER")
            if publisher is not None:
                publisher.on_config_change(instance_id)
    if ok_messages:
        flash(f"{device.name}: {', '.join(ok_messages)}.", "ok")
    return redirect_to


# -- delete + calibrate ------------------------------------------------


@bp.post("/settings/devices/<instance_id>/delete")
def devices_delete(instance_id: str) -> Response:
    """Remove a user-created device instance. Built-in kinds are
    refused, they ship with the app.

    v0.69.2 (issue #48): when the form carries ``wipe_orphan=1``, the
    per-device state that would otherwise stay (pages exclusively
    bound to the id, event log rows for those pages + test-pattern
    events, per-device settings, calibration image) gets wiped after
    the manifest / registry entry come down. Without the flag, the
    state stays and the device's last-known MAC gets stashed as a
    marker so a later re-register can decide whether to auto-wipe
    via the MAC-differs check (issue #48)."""
    device_to_delete = devices().get(instance_id)
    last_mac = (
        str(device_to_delete.manifest.get("mac") or "").strip()
        if device_to_delete is not None
        else ""
    )
    # A relay panel's mailbox + device token live on the relay, and the relay
    # has no auto-discovery, so deleting the local instance without revoking
    # would leave the token authenticating forever (the panel keeps polling an
    # abandoned mailbox). Same best-effort contract as the Cloud-relay page's
    # revoke: a relay error still removes the device locally.
    if device_to_delete is not None and device_to_delete.transport == "relay":
        from app.relay_client import RelayError
        from app.relay_config import build_client, relay_config

        client = build_client(relay_config(settings_store()))
        if client is not None:
            try:
                client.revoke_device(instance_id)
            except RelayError as exc:
                current_app.logger.warning("relay revoke %s: %s", instance_id, exc)
    wipe_orphan = bool(request.form.get("wipe_orphan"))
    result = device_service.delete_instance(
        devices=devices(),
        renderers=renderers(),
        instance_id=instance_id,
    )
    if not result.ok or result.device is None:
        flash(result.error or "Delete failed.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    if wipe_orphan:
        wiped = device_cleanup.wipe_orphan_state(
            device_id=instance_id,
            page_store=current_app.config["PAGE_STORE"],
            event_log=current_app.config["EVENT_LOG"],
            settings_store=settings_store(),
            data_root=Path(current_app.config["DATA_ROOT"]),
            push_manager=current_app.config.get("PUSH_MANAGER"),
            devices=devices(),
        )
        # Also clear any pending marker for this id; the state is
        # already gone, so a later re-register has nothing to compare
        # against and treats the id as pristine.
        DeletedDeviceMarkers(Path(current_app.config["DATA_ROOT"])).clear(instance_id)
        if wiped.total:
            flash(
                f"Wiped {len(wiped.page_ids)} dashboards, "
                f"{wiped.event_count} history rows, and per-device settings.",
                "ok",
            )
    else:
        DeletedDeviceMarkers(Path(current_app.config["DATA_ROOT"])).record(
            instance_id, last_mac or None
        )
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
    # Same for the persisted device facts (fw version / OTA capability), so a
    # future device reusing the id starts clean.
    device_facts = current_app.config.get("DEVICE_FACTS")
    if device_facts is not None:
        try:
            device_facts.forget(instance_id)
        except Exception:
            current_app.logger.exception("device_facts: forget failed for %s", instance_id)
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
        return redirect(
            url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
        )

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
        return redirect(
            url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
        )
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
    return redirect(
        url_for("auth.settings_area", area="devices", opened=instance_id, _anchor=anchor)
    )


# -- colour test patterns ----------------------------------------------


def _resolve_pattern_context(instance_id: str) -> tuple[Any, dict[str, Any]] | None:
    """Common device / panel / gamut / calibrated lookup for the test-
    pattern routes. Returns ``(device, context)`` on success, ``None``
    when the device is unknown or lacks a panel block (the caller
    flashes + redirects in that case). The context is what
    :func:`test_patterns.build_pattern` needs.

    ``calibrated`` reflects whether *any* renderer clone bound to this
    device has the calibrated-palette toggle on, so the preview PNG
    matches what the renderer will actually paint. False when nothing
    reads calibrated (nominal palette used everywhere)."""
    device = devices().get(instance_id)
    if device is None or device.kind_of is None or device.panel is None:
        return None
    panel = device.panel
    gamut = str(panel.get("gamut") or "waveshare_e6")
    store = settings_store()
    calibrated = False
    for clone in renderers().for_device(device.id):
        clone_state = store.get_for_runtime(
            "renderers", clone.id, [{"name": "calibrated", "type": "bool", "default": False}]
        )
        if bool(clone_state.get("calibrated")):
            calibrated = True
            break
    return device, {
        "w": int(panel["w"]),
        "h": int(panel["h"]),
        "gamut": gamut,
        "calibrated": calibrated,
    }


def _gray_level_count(gamut: str) -> int:
    """How many levels a grayscale gamut has, or 0 when it isn't one.

    Drives the calibration preview: a grey panel's profile carries a
    ramp rather than named inks, so the override, the live-preview query
    params and the completeness check all key off this instead of the
    colour-slot names."""
    if gamut == "gray_4":
        return 4
    if gamut == "gray_16":
        return 16
    return 0


def _custom_image_path_for(instance_id: str) -> Path:
    """Where an uploaded calibration reference lives on disk. One file
    per device; overwritten on subsequent uploads. Format is always PNG
    so the pattern generator can open it without sniffing content type."""
    root = Path(current_app.config["DATA_ROOT"]) / "calibration_images"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{instance_id}.png"


def _redirect_to_calibration_tab(instance_id: str) -> Response:
    """Redirect back to the Calibration tab of the given device card,
    keeping the card expanded (``?opened=``) and the tab selected
    (``?tab=calibration``). v0.69.14: without ``?opened=`` the card
    collapsed on every POST + 302 (e.g. custom-image upload)."""
    return redirect(
        url_for(
            "auth.settings_area",
            area="devices",
            tab="calibration",
            opened=instance_id,
            _anchor=f"device-{instance_id}",
        )
    )


@bp.post("/settings/devices/<instance_id>/test-pattern/custom-image/upload")
def devices_custom_image_upload(instance_id: str) -> Response:
    """Save a user-picked JPG / PNG as this device's custom test
    pattern. Content is re-encoded to PNG on write so downstream
    readers don't have to sniff the format. Panel-side fitting
    happens at render time in :func:`app.test_patterns._custom_image`
    to keep the storage step cheap."""
    from io import BytesIO

    from PIL import Image, UnidentifiedImageError

    ctx = _resolve_pattern_context(instance_id)
    if ctx is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        flash("Pick an image file to upload.", "error")
        return _redirect_to_calibration_tab(instance_id)
    try:
        img = Image.open(BytesIO(upload.read())).convert("RGB")
    except (OSError, UnidentifiedImageError) as err:
        flash(f"Couldn't read {upload.filename!r}: {err}", "error")
        return _redirect_to_calibration_tab(instance_id)
    _custom_image_path_for(instance_id).write_bytes(_png_bytes(img))
    flash(f"Uploaded {upload.filename!r} as this device's custom pattern.", "ok")
    return _redirect_to_calibration_tab(instance_id)


@bp.post("/settings/devices/<instance_id>/test-pattern/custom-image/delete")
def devices_custom_image_delete(instance_id: str) -> Response:
    """Remove the uploaded custom pattern. Idempotent (missing file is
    a no-op)."""
    path = _custom_image_path_for(instance_id)
    if path.exists():
        path.unlink()
        flash("Removed custom calibration image.", "ok")
    return _redirect_to_calibration_tab(instance_id)


def _png_bytes(img: Any) -> bytes:
    """Encode a PIL image as PNG bytes. Small helper so the upload
    route stays readable."""
    from io import BytesIO

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@bp.post("/settings/devices/<instance_id>/test-pattern")
def devices_send_test_pattern(instance_id: str) -> Response:
    """Push a colour test pattern to the device.

    Payload: ``pattern`` (one of :data:`test_patterns.PATTERN_IDS`) and
    an optional ``color_index`` for patterns that need one. The bytes
    go through the same push pipeline as ``push_image`` so the panel's
    real renderer + transport handle framing; palette-locked pixels
    make the dither step a no-op so what the user picked is what the
    panel paints."""
    ctx = _resolve_pattern_context(instance_id)
    if ctx is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    _, params = ctx
    pattern_id = (request.form.get("pattern") or "").strip()
    if pattern_id not in test_patterns.PATTERN_IDS:
        flash(f"Unknown test pattern {pattern_id!r}.", "error")
        return _redirect_to_calibration_tab(instance_id)
    color_index: int | None = None
    raw_color = request.form.get("color_index")
    if raw_color is not None and raw_color != "":
        try:
            color_index = int(raw_color)
        except ValueError:
            color_index = 0
    custom_path = _custom_image_path_for(instance_id)
    try:
        pattern_bytes = test_patterns.build_pattern(
            pattern_id,
            params["w"],
            params["h"],
            gamut=params["gamut"],
            calibrated=params["calibrated"],
            color_index=color_index,
            custom_image_path=str(custom_path) if custom_path.exists() else None,
        )
    except ValueError as err:
        flash(str(err), "error")
        return _redirect_to_calibration_tab(instance_id)
    result = push_manager().push_image(
        pattern_bytes,
        source_label=f"test-pattern:{pattern_id}:{instance_id}",
        device_id=instance_id,
    )
    if result.status == "sent":
        flash(f"Sent {pattern_id.replace('_', ' ')} to the panel.", "ok")
    else:
        flash(
            f"Test-pattern push {result.status}: {result.error or '(no detail)'}",
            "error",
        )
    return _redirect_to_calibration_tab(instance_id)


@bp.get("/settings/devices/<instance_id>/test-pattern/preview.png")
def devices_test_pattern_preview(instance_id: str) -> Response:
    """Return the raw PNG the send button would push, so the tab can
    show an inline preview before the user commits. Same generator, no
    push side-effect. 404s when the device or panel is unknown."""
    ctx = _resolve_pattern_context(instance_id)
    if ctx is None:
        return FlaskResponse("unknown device", status=404, mimetype="text/plain")
    _, params = ctx
    pattern_id = (request.args.get("pattern") or "palette_swatches").strip()
    if pattern_id not in test_patterns.PATTERN_IDS:
        return FlaskResponse("unknown pattern", status=400, mimetype="text/plain")
    color_index: int | None = None
    raw_color = request.args.get("color_index")
    if raw_color is not None and raw_color != "":
        try:
            color_index = int(raw_color)
        except ValueError:
            color_index = 0
    # v0.67.5: resolve the device's active palette profile so the
    # preview shows the applied palette + tone / edge pipeline
    # rather than the built-in gamut default. Query-string overrides
    # (populated by the tone sliders via inline JS) let users see
    # slider effects live before hitting Save.
    from pathlib import Path

    from app.palette_profiles import (
        PaletteProfileStore,
        bundled_profile,
    )

    def _q_int(name: str, default: int) -> int:
        raw = request.args.get(name)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    palette_override: tuple[tuple[int, int, int], ...] | None = None
    tone_defaults: dict[str, int] = {
        "exposure": 0,
        "s_curve": 0,
        "lab_compress_min": 0,
        "lab_compress_max": 100,
        "smoothing_radius": 0,
    }
    # v0.69.14: the palette-profile picker previews a candidate profile
    # before the user hits Apply via ``?slug=<slug>``. Empty ``slug=""``
    # explicitly means "no profile" (built-in default). Missing param
    # falls back to the saved slug the device is currently using.
    slug_raw_override = request.args.get("slug")
    if slug_raw_override is not None:
        slug = slug_raw_override.strip()
    else:
        slug_field = [{"name": "palette_profile_slug", "type": "string", "default": ""}]
        slug_raw = settings_store().get_for_runtime("devices", instance_id, slug_field)
        slug = str(slug_raw.get("palette_profile_slug") or "").strip()
    if slug:
        profile = bundled_profile(slug) or PaletteProfileStore(
            Path(current_app.config["DATA_ROOT"])
        ).load(slug)
        if profile is not None:
            # A grey profile carries no colours, only a ramp. Reading
            # ``palette`` here handed the preview a default PaletteColors,
            # i.e. literal Spectra 6 primaries, so the calibration preview
            # for a grayscale panel painted colours it cannot produce and
            # showed nothing of the ramp actually being applied.
            gray_levels = _gray_level_count(str(params["gamut"]))
            if gray_levels:
                palette_override = profile.gray.as_tuples(gray_levels)
            else:
                palette_override = profile.palette.as_tuples()
            tone_defaults["exposure"] = profile.tone.exposure
            tone_defaults["s_curve"] = profile.tone.s_curve
            tone_defaults["lab_compress_min"] = profile.tone.lab_compress_min
            tone_defaults["lab_compress_max"] = profile.tone.lab_compress_max
            tone_defaults["smoothing_radius"] = profile.edges.smoothing_radius

    # v0.68: live palette preview. When the palette editor swatches
    # change, the JS attaches ``black`` / ``white`` / ``yellow`` /
    # ``red`` / ``blue`` / ``green`` (+ optional ``orange``) as
    # ``#rrggbb`` query params. Any that arrive override the profile
    # palette on a per-slot basis so users see their colour choices
    # painted into the preview before hitting Save.
    import contextlib

    # Grey panels post ``level0..levelN`` instead of colour names: they
    # have no named inks, and the editor is a ramp rather than a set of
    # slots. Same live-preview contract otherwise.
    gray_levels = _gray_level_count(str(params["gamut"]))
    if gray_levels:
        palette_names = tuple(f"level{i}" for i in range(gray_levels))
    else:
        palette_names = ("black", "white", "yellow", "red", "blue", "green", "orange")
    swatches: list[tuple[int, int, int]] = []
    for name in palette_names:
        raw = request.args.get(name)
        if raw is None or raw == "":
            continue
        raw = raw.strip()
        if len(raw) == 7 and raw.startswith("#"):
            with contextlib.suppress(ValueError):
                swatches.append((int(raw[1:3], 16), int(raw[3:5], 16), int(raw[5:7], 16)))
    # How many swatches constitute a complete palette for this panel.
    # BWRY posts four (it has no blue / green ink); EVERY other gamut
    # keeps the original constant 6, deliberately. Deriving this from
    # the gamut's palette length reads better but is not behaviour-
    # neutral: it moves inky_7colour to 7 and the profile-less gamuts
    # (bwr_3 / mono / gray_4) to 3 / 2 / 4, and this change is scoped to
    # PicPak only. Leave the rest exactly as they were.
    required_swatches = gray_levels or (4 if str(params["gamut"]) == "bwry_4" else 6)
    if len(swatches) >= required_swatches:
        palette_override = tuple(swatches)

    custom_path = _custom_image_path_for(instance_id)
    try:
        png = test_patterns.build_pattern(
            pattern_id,
            params["w"],
            params["h"],
            gamut=params["gamut"],
            calibrated=params["calibrated"],
            color_index=color_index,
            palette_override=palette_override,
            exposure=_q_int("exposure", tone_defaults["exposure"]),
            s_curve=_q_int("s_curve", tone_defaults["s_curve"]),
            lab_compress_min=_q_int("lab_compress_min", tone_defaults["lab_compress_min"]),
            lab_compress_max=_q_int("lab_compress_max", tone_defaults["lab_compress_max"]),
            smoothing_radius=_q_int("smoothing_radius", tone_defaults["smoothing_radius"]),
            custom_image_path=str(custom_path) if custom_path.exists() else None,
        )
    except ValueError as err:
        return FlaskResponse(str(err), status=400, mimetype="text/plain")
    return FlaskResponse(
        png,
        mimetype="image/png",
        headers={"Cache-Control": "no-store"},
    )
