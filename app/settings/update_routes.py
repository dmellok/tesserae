"""Generic ``POST /settings/<section_kind>`` save handler.

One endpoint persists any of the five section kinds (``app``,
``panel``, ``broker``, ``renderer-<id>``, ``device-<id>``, ``plugin-<id>``).
The branch each section takes mirrors the per-source field shape:

* ``app`` / ``broker`` / ``panel`` go through :func:`_update_core` against
  the hardcoded :mod:`app.settings.field_defs` lists.
* ``renderer-`` filters ``device_setting``-flagged fields out (those live
  on the device card) and saves the rest, plus the sibling Enabled
  toggle that lives outside the manifest.
* ``plugin-`` does the same shape with no extras.
* ``device-`` saves the renderer-defined config and publishes the
  config topic retained.

Changes to broker / app / panel trigger ``_apply_broker_change`` so the
running transport picks up the new values without a restart.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from flask import current_app, flash, request
from werkzeug.wrappers import Response

from app.state.settings_store import SECRET_MASK

from ._shared import (
    bp,
    coerce_form_value,
    config_fields_from_schema,
    devices,
    plugins,
    redirect_to_section,
    renderers,
    settings_store,
    transport,
    values_from_form,
)
from .field_defs import APP_FIELDS, BROKER_FIELDS, PANEL_FIELDS


@bp.post("/settings/<section_kind>")
def settings_update(section_kind: str) -> Response:
    """Persist a single section. ``section_kind`` is one of ``app``,
    ``broker``, ``renderer-<id>``, ``device-<id>``, or ``plugin-<id>``."""
    store = settings_store()

    handlers: dict[str, Callable[[], str]] = {
        "app": lambda: _update_core("app", APP_FIELDS),
        "panel": lambda: _update_core("app", PANEL_FIELDS),
        "broker": lambda: _update_core("broker", BROKER_FIELDS),
    }

    if section_kind in handlers:
        # Capture telemetry's pre-save state so we can detect an off→on
        # transition and fire a test event without making the user
        # restart the process.
        was_telemetry_on = (
            bool(store.get_section("app").get("telemetry_enabled", False))
            if section_kind == "app"
            else False
        )
        message = handlers[section_kind]()
        flash(message, "ok")
        if section_kind == "app":
            now_on = bool(store.get_section("app").get("telemetry_enabled", False))
            telemetry = current_app.config.get("TELEMETRY")
            if telemetry is not None and now_on != was_telemetry_on:
                telemetry.set_enabled(now_on)
                if now_on:
                    err = telemetry.test_send()
                    if err:
                        flash(
                            f"Telemetry enabled, but the test event failed: {err}. "
                            "Check the endpoint config in app/telemetry.py.",
                            "warn",
                        )
                    else:
                        flash("Telemetry enabled — test event delivered.", "ok")
        # Broker / App / Panel changes need a transport + HA-discovery
        # refresh to take effect without a restart (base_url, panel dims,
        # ha_discovery_enabled all flow through there).
        if section_kind in ("broker", "app", "panel"):
            _apply_broker_change()
        return redirect_to_section(section_kind)

    if section_kind.startswith("renderer-"):
        rid = section_kind.removeprefix("renderer-")
        renderer = renderers().get(rid)
        if renderer is None:
            return Response(f"unknown renderer {rid!r}", status=404)
        # device_setting fields belong on the device card, not the
        # renderer card. Filter them here too so a hand-crafted POST
        # to the renderer endpoint can't override per-device tuning.
        fields = [f for f in renderer.manifest.get("settings", []) if not f.get("device_setting")]
        values = values_from_form(fields)
        store.update_for_namespace("renderers", rid, values, fields)
        # Per-renderer Enabled toggle lives outside the manifest, so it's
        # stored in a sibling section keyed by id. Browsers don't submit
        # unchecked checkboxes — treat the field's absence as "off".
        enabled = coerce_form_value({"type": "switch"}, request.form.get("_enabled"))
        existing = store.get_section("renderers_enabled")
        store.patch_section("renderers_enabled", {**existing, rid: bool(enabled)})
        flash(f"{renderer.name} settings saved.", "ok")
        return redirect_to_section(section_kind)

    if section_kind.startswith("plugin-"):
        pid = section_kind.removeprefix("plugin-")
        plugin = plugins().get(pid)
        if plugin is None:
            return Response(f"unknown plugin {pid!r}", status=404)
        fields = list(plugin.manifest.get("settings", []))
        values = values_from_form(fields)
        store.update_for_namespace("plugins", pid, values, fields)
        flash(f"{plugin.name} settings saved.", "ok")
        return redirect_to_section(section_kind)

    if section_kind.startswith("device-"):
        did = section_kind.removeprefix("device-")
        device = devices().get(did)
        if device is None:
            return Response(f"unknown device {did!r}", status=404)
        if device.config_topic is None:
            return Response(f"device {did!r} has no config topic", status=400)
        fields = config_fields_from_schema(device.config_schema)
        values = values_from_form(fields)
        ok, err = device.validate_config(values)
        if not ok:
            flash(f"Invalid {device.name} config: {err}", "error")
            return redirect_to_section(section_kind)
        store.update_for_namespace("devices", did, values, fields)
        tr = transport()
        try:
            tr.publish(
                device.config_topic,
                json.dumps(values).encode("utf-8"),
                qos=1,
                retain=True,
            )
            flash(f"{device.name} config saved and published.", "ok")
        except RuntimeError as exc:
            # Saved-but-not-published: the next broker connect will retain
            # the saved value, but tell the user the publish didn't land.
            flash(f"{device.name} config saved, publish failed: {exc}", "error")
        return redirect_to_section(section_kind)

    return Response(f"unknown section {section_kind!r}", status=404)


# -- internals ----------------------------------------------------------


def _update_core(section_name: str, fields: list[dict[str, Any]]) -> str:
    """Apply a single core section's form values. Honours the secret-mask
    convention so re-submitting a masked field doesn't blow the value
    away."""
    values = values_from_form(fields)
    existing = settings_store().get_section(section_name)
    out: dict[str, Any] = dict(existing)
    for field in fields:
        name = str(field["name"])
        is_secret = bool(field.get("secret"))
        disk = f"{name}_secret" if is_secret else name
        incoming = values.get(name)
        if is_secret and incoming == SECRET_MASK:
            continue
        if is_secret and incoming == "":
            # An empty secret means "clear it"; drop the key from disk.
            out.pop(disk, None)
        else:
            out[disk] = incoming
    settings_store().update_section(section_name, out)
    return f"{section_name.capitalize()} settings saved."


def _apply_broker_change() -> None:
    """Hot-swap the MQTT transport on broker setting changes."""
    rebuild = current_app.config.get("REBUILD_TRANSPORT")
    if callable(rebuild):
        rebuild()
