"""Settings index: ``GET /settings`` + ``GET /settings/<area>``.

The big ``_build_sections`` walker lives here too — it composes one
section dict per editable card across plugins, renderers, devices, and
the hardcoded core (App / Panel / Broker). The template walks the same
shape regardless of source. Helpers below (``_values_for_core``,
``_broker_mqtt_url``, ``_status_view``) exist solely to support that.
"""

from __future__ import annotations

import time
from typing import Any

from flask import current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.wrappers import Response

from app import backup as _backup_mod
from app import device_timetable
from app import updater as _updater_mod
from app.device_loader import Device
from app.network import detect_local_ip
from app.panel import PANEL_PRESET_CHOICES, PANEL_PRESETS
from app.state.settings_store import SECRET_MASK

from ._shared import (
    AREA_KINDS,
    AREAS,
    STATUS_FRESH_S,
    STATUS_WARN_S,
    bp,
    config_fields_from_schema,
    device_kinds,
    device_status,
    devices,
    discovery_cache,
    format_discovered,
    format_relative,
    plugins,
    render_for_admin,
    renderers,
    settings_store,
)
from .field_defs import APP_FIELDS, BROKER_FIELDS, PANEL_FIELDS


@bp.get("/settings")
def settings() -> Response:
    """Land on the Server sub-page by default."""
    return redirect(url_for("auth.settings_area", area="server"))


@bp.get("/settings/<area>", endpoint="settings_area")
def settings_area(area: str) -> str | Response:
    """Render one sub-page of /settings, scoped to a single area
    (server / renderers / devices / plugins)."""
    if area not in AREA_KINDS:
        return Response(f"unknown settings area {area!r}", status=404)
    sections = [s for s in _build_sections() if s["kind"] in AREA_KINDS[area]]
    # Devices area needs the kinds list (for the Add-device form) so the
    # template doesn't have to dig into the registry directly.
    device_kinds_list = (
        [{"id": d.id, "name": d.name, "panel": d.panel} for d in device_kinds()]
        if area == "devices"
        else []
    )
    discovered = (
        [d for d in format_discovered(discovery_cache().all()) if d["id"] not in devices().devices]
        if area == "devices"
        else []
    )
    # Signature of what we're rendering, so the client-side poller knows
    # the baseline and can auto-refresh when the discovered set changes.
    discovered_sig = ",".join(sorted(f"{d['id']}:{d.get('kind') or ''}" for d in discovered))

    # System page payload: current update state + cached check + recent
    # history + on-disk backups. The check is NOT auto-refreshed (it hits
    # the network); the user clicks "Check for updates" to refresh.
    system_state = None
    system_last_check = None
    system_history: list[Any] = []
    system_backups: list[Any] = []
    system_telemetry_enabled = False
    system_telemetry_host = ""
    system_webhook_token_set = False
    # One-shot reveal after /settings/system/webhook/regenerate — pop
    # so a refresh doesn't re-show the token. Only honoured on the
    # System tab, which is the only place the modal renders.
    system_webhook_reveal_token = (
        session.pop("_webhook_token_reveal", "") if area == "system" else ""
    )
    if area == "system":
        upd = current_app.config["UPDATER"]
        try:
            system_state = upd.current_state()
        except _updater_mod.UpdaterError as err:
            flash(f"Update state unavailable: {err}", "error")
        system_last_check = upd.last_check
        system_history = list(reversed(upd.history()))
        system_backups = _backup_mod.list_all(current_app.config["DATA_ROOT"])
        telemetry = current_app.config.get("TELEMETRY")
        if telemetry is not None:
            system_telemetry_enabled = telemetry.enabled
            # endpoint is empty when disabled; surface host as a hint either way.
            system_telemetry_host = telemetry._cfg.host
        # Surface only whether a webhook token is set — never the value
        # itself — so a screenshot of Settings → System doesn't leak it.
        # The disk key is ``webhook_token_secret`` (``_secret`` suffix
        # is the convention for masked fields); ``get_section`` returns
        # raw on-disk keys so we look up the suffixed form here.
        _app_raw = settings_store().get_section("app")
        system_webhook_token_set = bool(
            (_app_raw.get("webhook_token_secret") or _app_raw.get("webhook_token") or "").strip()
        )

    return render_template(
        "settings.html",
        sections=sections,
        active_area=area,
        areas=AREAS,
        device_kinds=device_kinds_list,
        panel_preset_choices=PANEL_PRESET_CHOICES if area == "devices" else [],
        panel_presets=PANEL_PRESETS if area == "devices" else {},
        discovered_devices=discovered,
        discovered_sig=discovered_sig,
        # When set (via ?calibrating=<id>), the matching device card shows
        # the "which number is in the top-left?" answer form.
        calibrating=request.args.get("calibrating") or "",
        system_state=system_state,
        system_last_check=system_last_check,
        system_history=system_history,
        system_backups=system_backups,
        system_dev_mode=bool(current_app.debug),
        system_telemetry_enabled=system_telemetry_enabled,
        system_telemetry_host=system_telemetry_host,
        system_webhook_token_set=system_webhook_token_set,
        system_webhook_reveal_token=system_webhook_reveal_token,
    )


# -- internals: section building ---------------------------------------


def _build_sections() -> list[dict[str, Any]]:
    store = settings_store()
    sections: list[dict[str, Any]] = []

    app_raw = store.get_section("app")
    sections.append(
        {
            "id": "app",
            "kind": "app",
            "title": "App",
            "blurb": "Where Tesserae lives on the network.",
            "fields": APP_FIELDS,
            "state": _values_for_core("app", APP_FIELDS, app_raw),
            "endpoint": url_for("auth.settings_update", section_kind="app"),
            "meta": {"Network IP": detect_local_ip()},
        }
    )

    broker_raw = store.get_section("broker")
    sections.append(
        {
            "id": "broker",
            "kind": "broker",
            "title": "MQTT broker",
            "blurb": "Tesserae publishes frames here; devices subscribe.",
            "fields": _broker_fields_with_client_id_hint(),
            "state": _values_for_core("broker", BROKER_FIELDS, broker_raw),
            "endpoint": url_for("auth.settings_update", section_kind="broker"),
            "meta": {"MQTT URL": _broker_mqtt_url(broker_raw)},
        }
    )

    # Virtual panel: the fallback canvas size for pages with no target
    # device (the "(any)" option in the page editor). Registered devices
    # bring their own panel, so this only matters before you've added a
    # device or for deliberately device-agnostic pages — hence it sits
    # below the broker rather than up top.
    sections.append(
        {
            "id": "panel",
            "kind": "panel",
            "title": "Virtual panel",
            "blurb": "Fallback canvas size for pages with no target device. Pick a preset or set a custom size; Portrait flips width and height. Devices you register override this with their own panel.",
            "fields": PANEL_FIELDS,
            "state": _values_for_core("app", PANEL_FIELDS, app_raw),
            "endpoint": url_for("auth.settings_update", section_kind="panel"),
        }
    )

    enabled_map = store.get_section("renderers_enabled")
    for renderer in renderers().all():
        # Per-instance clones inherit the base renderer's settings; the
        # cards add no UI value and just create N rows of the same
        # form. Filter them out — clone ids always contain '__'
        # (see renderer_loader.clone_for_instances).
        if "__" in renderer.id:
            continue
        # Renderer-wide fields only. Fields flagged ``device_setting:
        # true`` (e.g. pi_bin's dither/saturation/contrast) live on the
        # device card instead so each panel can be tuned independently.
        all_fields = list(renderer.manifest.get("settings", []))
        fields = [f for f in all_fields if not f.get("device_setting")]
        has_device_fields = len(fields) != len(all_fields)
        sid = f"renderer-{renderer.id}"
        rid = renderer.id
        is_enabled = enabled_map.get(rid)
        if is_enabled is None:
            is_enabled = True
        blurb = renderer.manifest.get("description") or ""
        if has_device_fields:
            blurb = (
                (blurb + " " if blurb else "")
                + "Per-display settings live on each device card under "
                + "Settings → Devices so every panel can be tuned independently."
            ).strip()
        sections.append(
            {
                "id": sid,
                "kind": "renderer",
                "title": f"Renderer: {renderer.name}",
                "blurb": blurb,
                "fields": fields,
                "state": render_for_admin("renderers", renderer.id, fields),
                "endpoint": url_for("auth.settings_update", section_kind=sid),
                "enabled": bool(is_enabled),
                "supports_toggle": True,
                "meta": {
                    "Topic": renderer.topic,
                    "Retain": "yes" if renderer.retain else "no",
                    "Device": renderer.device,
                },
            }
        )

    for device in devices().all():
        # Built-in kinds are templates, not bindable devices — they
        # never appear on the Devices tab. Every physical display is
        # represented by an instance (added manually or auto-registered
        # from the Discovered strip).
        if device.kind_of is None:
            continue
        sid = f"device-{device.id}"
        fields = config_fields_from_schema(device.config_schema)
        is_instance = device.kind_of is not None
        # Picture-quality (dither / saturation / contrast) lives on the
        # clone renderer keyed ``<base_id>__<device_id>`` — one clone
        # per renderer the device's kind consumes. Surface each clone's
        # device_setting-flagged fields as a "Picture quality" subsection;
        # the template renders them inside the combined form with the
        # name pattern ``<clone_id>:<field_name>`` so the save handler
        # can route each value back to the right clone's namespace.
        picture_quality: list[dict[str, Any]] = []
        if is_instance:
            for clone in renderers().for_device(device.id):
                dev_fields = [
                    f for f in clone.manifest.get("settings", []) if f.get("device_setting")
                ]
                if not dev_fields:
                    continue
                base_id = clone.id.split("__", 1)[0]
                picture_quality.append(
                    {
                        "clone_id": clone.id,
                        "base_id": base_id,
                        "base_name": clone.name.split(" (", 1)[0],
                        "fields": dev_fields,
                        "state": store.get_for_runtime("renderers", clone.id, dev_fields),
                    }
                )
        sections.append(
            {
                "id": sid,
                "kind": "device",
                "title": f"Device: {device.name}",
                "icon": device.icon,
                "blurb": device.manifest.get("description") or "",
                "fields": fields,
                "state": (store.get_for_runtime("devices", device.id, fields) if fields else {}),
                "endpoint": (url_for("auth.settings_update", section_kind=sid) if fields else None),
                # Single Save for the whole device card — the template
                # wraps the renderer-config + panel + quiet-hours fields
                # in one form posting here, and this handler fans out to
                # the same service helpers the per-subsection endpoints
                # call. Only present on instances (kinds aren't editable
                # in the UI). The per-subsection endpoints above stay
                # available for programmatic callers / direct hits.
                "combined_endpoint": (
                    url_for("auth.devices_update_combined", instance_id=device.id)
                    if is_instance
                    else None
                ),
                "meta": {
                    "Renderers": ", ".join(device.renderer_ids),
                    "Status topic": device.status_topic,
                    "Config topic": device.config_topic or "—",
                    **({"Instance of": str(device.kind_of)} if is_instance else {}),
                },
                "status": _status_view(device),
                # The colour-gamut control only matters for the .bin Pi path
                # (pi_bin packs server-side to a fixed palette). PNG clients
                # project their own gamut on-device; the ESP32 firmware is
                # always E6. Gate on the device's base renderer being pi_bin.
                "gamut_capable": any(
                    rid.split("__", 1)[0] == "pi_bin" for rid in device.renderer_ids
                ),
                "delete_endpoint": (
                    url_for("auth.devices_delete", instance_id=device.id) if is_instance else None
                ),
                # Panel edit (orientation + dims) is only offered on
                # instances — kinds aren't shown here at all.
                "panel": device.panel if is_instance else None,
                "panel_endpoint": (
                    url_for("auth.devices_update_panel", instance_id=device.id)
                    if is_instance
                    else None
                ),
                "device_id": device.id,
                "calibrate_endpoint": (
                    url_for("auth.devices_calibrate", instance_id=device.id)
                    if is_instance
                    else None
                ),
                "calibrate_apply_endpoint": (
                    url_for("auth.devices_calibrate_apply", instance_id=device.id)
                    if is_instance
                    else None
                ),
                # Per-device quiet-hours override. Read from the
                # manifest so the form can preselect the user's
                # current setting; ``quiet_hours_endpoint`` is None on
                # kinds (only instances can override).
                "picture_quality": picture_quality,
                "quiet_hours": (device.manifest.get("quiet_hours") or {} if is_instance else {}),
                "quiet_hours_endpoint": (
                    url_for("auth.devices_update_quiet_hours", instance_id=device.id)
                    if is_instance
                    else None
                ),
                # Per-device rotation view: every Schedule whose target
                # page binds to this device, sorted by window start.
                # Pure read view — each row deep-links to the Schedules
                # editor where the user can actually change it.
                "timetable_entries": (
                    device_timetable.timetable_for_device(
                        device.id,
                        devices=devices(),
                        pages=current_app.config["PAGE_STORE"],
                        schedules=current_app.config["SCHEDULE_STORE"],
                    )
                    if is_instance
                    else []
                ),
            }
        )

    for plugin in plugins().plugins.values():
        fields = list(plugin.manifest.get("settings", []))
        if not fields:
            continue
        sid = f"plugin-{plugin.id}"
        sections.append(
            {
                "id": sid,
                "kind": "plugin",
                "title": f"Plugin: {plugin.name}",
                "blurb": plugin.manifest.get("description") or "",
                "fields": fields,
                "state": render_for_admin("plugins", plugin.id, fields),
                "endpoint": url_for("auth.settings_update", section_kind=sid),
            }
        )

    return sections


def _status_view(device: Device) -> dict[str, Any]:
    """Build the status-block dict the template renders above the config
    form: freshness class (ok / warn / stale / unknown), relative time,
    and the parsed key/value pairs."""
    cache = device_status().get(device.id)
    if cache is None:
        return {"health": "unknown", "relative": "no heartbeat received yet", "parsed": {}}
    age = max(0.0, time.time() - float(cache.get("received_at", 0)))
    if age <= STATUS_FRESH_S:
        health = "ok"
    elif age <= STATUS_WARN_S:
        health = "warn"
    else:
        health = "stale"
    return {
        "health": health,
        "relative": format_relative(age),
        "parsed": cache.get("parsed", {}),
    }


def _values_for_core(
    section_name: str, fields: list[dict[str, Any]], raw: dict[str, Any]
) -> dict[str, Any]:
    """Translate raw disk values for a core section (app / broker) into
    UI-facing values, applying defaults + masking secrets."""
    out: dict[str, Any] = {}
    for field in fields:
        name = str(field["name"])
        is_secret = bool(field.get("secret"))
        disk = f"{name}_secret" if is_secret else name
        if disk in raw:
            out[name] = SECRET_MASK if is_secret else raw[disk]
        else:
            out[name] = field.get("default", "")
    return out


def _truthy_setting(value: object) -> bool:
    """Loose truthiness for stored switch values (bool, ``"true"``/``"on"``, 1)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _broker_mqtt_url(raw: dict[str, Any]) -> str:
    """The ``mqtt://host:port`` clients should point at, from the saved
    broker config. For the built-in broker a 0.0.0.0 bind resolves to the
    host's LAN IP (what other machines actually connect to) and a loopback
    bind stays 127.0.0.1; for an external broker it's the configured
    host:port. Returns ``—`` when no external host is set."""
    if _truthy_setting(raw.get("embedded_enabled")):
        bind = str(raw.get("embedded_bind") or "127.0.0.1").strip() or "127.0.0.1"
        port = raw.get("embedded_port") or 1883
        if bind in ("0.0.0.0", "::"):
            host = detect_local_ip()
        elif bind in ("127.0.0.1", "localhost", "::1"):
            host = "127.0.0.1"
        else:
            host = bind
    else:
        host = str(raw.get("host") or "").strip()
        port = raw.get("port") or 1883
        if not host:
            return "—"
    return f"mqtt://{host}:{port}"


def _broker_fields_with_client_id_hint() -> list[dict[str, Any]]:
    """BROKER_FIELDS with the client_id field's placeholder set to the live
    (auto) client id, so a blank field shows what it actually connects as
    (e.g. ``tesserae-<hostname>`` / ``…-dev``) rather than looking unset."""
    transport_obj = current_app.config.get("MQTT_TRANSPORT")
    auto = getattr(transport_obj, "client_id", "") or ""
    if not auto:
        return BROKER_FIELDS
    return [
        {**f, "placeholder": f"{auto} (auto)"} if f.get("name") == "client_id" else f
        for f in BROKER_FIELDS
    ]
