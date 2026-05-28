"""Admin auth + settings routes.

One blueprint covers:

* ``GET/POST /setup``        — first-run password set (only reachable while
                                no password is configured).
* ``GET/POST /login``        — sign-in form.
* ``POST /logout``           — drop the session and redirect to /login.
* ``GET /settings``          — list every setting section the user can
                                edit, with manifest-driven form fields and
                                masked secrets.
* ``POST /settings/<section>`` — persist a single section. Sections are
                                ``app``, ``broker``, ``renderer/<id>``, or
                                ``plugin/<id>``.

Form rendering, field types, secret handling, default values all flow from
the manifest declarations (or the hardcoded app/broker field lists). The
template walks the same shape and asks no questions.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flask import Blueprint, Flask, current_app, flash, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from app import auth, calibration, device_service
from app.calibration import build_calibration_card, target_orientation
from app.device_loader import Device, DeviceRegistry
from app.discovery import DiscoveredDevice, DiscoveryCache
from app.panel import DEFAULT_PRESET, PANEL_PRESET_CHOICES, PANEL_PRESETS
from app.plugin_loader import PluginRegistry
from app.push import PushManager
from app.renderer_loader import RendererRegistry
from app.state.event_log import EventLog
from app.state.settings_store import SECRET_MASK, SettingsStore
from app.transport import BrokerConfig, MqttTransport

# How fresh a heartbeat has to be to read as "ok" in the UI. Past this it
# decays through "warn" (2x) into "stale". Tuned for the typical Pi client
# 60s heartbeat — esp32_client wakes far less often but the broker-retained
# last value is still informative.
STATUS_FRESH_S: int = 90
STATUS_WARN_S: int = 5 * 60

bp = Blueprint("auth", __name__)


# Hardcoded "core" sections that aren't manifest-driven — same field-spec
# shape as plugin / renderer ``settings`` so the template can render them
# through the same path.

APP_FIELDS: list[dict[str, Any]] = [
    {
        "name": "timezone",
        "type": "string",
        "label": "Timezone (IANA name or 'system')",
        "default": "system",
        "help": (
            "Used by the scheduler when interpreting daily fire times and "
            "time-of-day windows. 'system' uses the host's local time; "
            "anything else must be an IANA zone like 'Australia/Melbourne'."
        ),
    },
    {
        "name": "ha_discovery_enabled",
        "type": "switch",
        "label": "Home Assistant MQTT discovery",
        "default": False,
        "help": (
            "Publish HA autodiscovery configs so a button per saved dashboard, "
            "an image entity for the most-recent render, and diagnostic sensors "
            "appear under a 'Tesserae' device in HA. Default off."
        ),
    },
]

PANEL_FIELDS: list[dict[str, Any]] = [
    {
        "name": "panel_preset",
        "type": "select",
        "label": "Panel size",
        "default": DEFAULT_PRESET,
        "choices": PANEL_PRESET_CHOICES,
        "help": "Common Inky / Waveshare panels. Pick Custom to set width + height manually.",
    },
    {
        "name": "panel_orientation",
        "type": "switch",
        "label": "Portrait orientation",
        "default": False,
        "help": "Swap the panel width + height. Default off renders the panel landscape-native.",
    },
    {
        "name": "panel_w",
        "type": "slider",
        "label": "Panel width (px)",
        "default": 1600,
        "min": 100,
        "max": 3000,
        "step": 1,
        "unit": "px",
        "help": "Only used when Panel size is Custom.",
    },
    {
        "name": "panel_h",
        "type": "slider",
        "label": "Panel height (px)",
        "default": 1200,
        "min": 100,
        "max": 3000,
        "step": 1,
        "unit": "px",
    },
]

BROKER_FIELDS: list[dict[str, Any]] = [
    {"name": "host", "type": "string", "label": "Host", "default": ""},
    {
        "name": "port",
        "type": "number",
        "label": "Port",
        "default": 1883,
        "min": 1,
        "max": 65535,
    },
    {"name": "username", "type": "string", "label": "Username", "default": ""},
    {"name": "password", "type": "string", "label": "Password", "default": "", "secret": True},
    {
        "name": "keepalive",
        "type": "slider",
        "label": "Keepalive (seconds)",
        "default": 60,
        "min": 10,
        "max": 600,
        "step": 5,
        "unit": "s",
    },
    {
        "name": "client_id",
        "type": "string",
        "label": "MQTT client id",
        "default": "tesserae",
    },
    {
        "name": "embedded_enabled",
        "type": "switch",
        "label": "Built-in broker",
        "default": False,
        "help": (
            "Run an in-process MQTT broker (amqtt). Convenient when you "
            "don't have a Mosquitto host handy; leave off for any "
            "non-trivial deployment."
        ),
    },
    {
        "name": "embedded_port",
        "type": "number",
        "label": "Built-in broker port",
        "default": 1883,
        "min": 1024,
        "max": 65535,
        "step": 1,
        "help": "Port the built-in broker listens on. Tesserae's transport auto-connects here when host is empty.",
    },
    {
        "name": "embedded_bind",
        "type": "string",
        "label": "Built-in broker bind address",
        "default": "127.0.0.1",
        "help": (
            "127.0.0.1 keeps the broker loopback-only (only this host can "
            "reach it). Set to 0.0.0.0 to accept connections from any LAN "
            "client — set a username + password below if you do."
        ),
    },
    {
        "name": "embedded_username",
        "type": "string",
        "label": "Built-in broker username",
        "default": "",
        "help": (
            "Optional. When set with a password, anonymous logins are "
            "rejected. Leave both blank for an open broker."
        ),
    },
    {
        "name": "embedded_password",
        "type": "string",
        "label": "Built-in broker password",
        "default": "",
        "secret": True,
        "help": "Stored on disk in a hashed password file the broker reads on start.",
    },
]


# -- helpers ------------------------------------------------------------


def _settings() -> SettingsStore:
    return current_app.config["SETTINGS_STORE"]  # type: ignore[no-any-return]


def _plugins() -> PluginRegistry:
    return current_app.config["PLUGIN_REGISTRY"]  # type: ignore[no-any-return]


def _renderers() -> RendererRegistry:
    return current_app.config["RENDERER_REGISTRY"]  # type: ignore[no-any-return]


def _devices() -> DeviceRegistry:
    return current_app.config["DEVICE_REGISTRY"]  # type: ignore[no-any-return]


def _device_status() -> dict[str, dict[str, Any]]:
    return current_app.config["DEVICE_STATUS"]  # type: ignore[no-any-return]


def _transport() -> MqttTransport:
    return current_app.config["MQTT_TRANSPORT"]  # type: ignore[no-any-return]


def _push_manager() -> PushManager:
    return current_app.config["PUSH_MANAGER"]  # type: ignore[no-any-return]


def _events() -> EventLog:
    return current_app.config["EVENT_LOG"]  # type: ignore[no-any-return]


def _log_auth(action: str, status: str, error: str | None = None) -> None:
    """Record an auth event. Target is always 'session' since we have one
    shared admin login — no per-user concept."""
    _events().record(
        type="auth",
        source=action,
        target="session",
        status=status,
        error=error,
        extra={"remote_addr": request.remote_addr or "(unknown)"},
    )


def _config_fields_from_schema(schema: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate a device's config_schema into the field-spec shape the
    settings template walks. ``int`` / ``float`` types collapse to
    ``number`` so the form rendering is uniform."""
    fields: list[dict[str, Any]] = []
    for name, spec in schema.items():
        out = dict(spec)
        out["name"] = name
        if out.get("type") in ("int", "float"):
            out["type"] = "number"
        out.setdefault("label", name.replace("_", " ").capitalize())
        fields.append(out)
    return fields


def _safe_next(target: str | None) -> str:
    """Bound the post-login redirect to in-app paths so we can't be used as
    an open redirector. Anything not starting with ``/`` (or starting with
    ``//`` which would be a protocol-relative URL) falls back to /settings."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return url_for("auth.settings")
    return target


def _coerce_form_value(field: dict[str, Any], raw: str | None) -> Any:
    """Turn a single form value (or ``None`` for unchecked boxes) into the
    Python type the field declares. ``slider`` aliases to ``number`` and
    ``switch`` to ``boolean`` so the new component macros line up with
    the same coercion rules."""
    ftype = field.get("type", "string")
    if ftype in ("boolean", "switch"):
        return raw in ("on", "true", "1")
    if ftype in ("number", "slider"):
        if raw is None or raw == "":
            return field.get("default")
        try:
            return int(raw)
        except ValueError:
            try:
                return float(raw)
            except ValueError:
                return field.get("default")
    if ftype == "select":
        if raw is None:
            return field.get("default")
        for choice in field.get("choices", []):
            if str(choice.get("value")) == raw:
                return choice["value"]
        return raw
    return raw if raw is not None else ""


def _values_from_form(fields: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in fields:
        name = str(field["name"])
        if field.get("type") in ("boolean", "switch"):
            # Unchecked checkboxes are absent from the form, present ones
            # send "on" — bare presence is what we use.
            values[name] = field["name"] in request.form
        else:
            values[name] = _coerce_form_value(field, request.form.get(name))
    return values


def _render_for_admin(namespace: str, item_id: str, fields: list[dict[str, Any]]) -> dict[str, Any]:
    return _settings().get_for_admin(namespace, item_id, fields)


# -- routes -------------------------------------------------------------


@bp.route("/setup", methods=["GET", "POST"])
def setup() -> Response | str:
    settings = _settings()
    # Setup only works while no password is set — otherwise it's a way to
    # silently take over the admin.
    if auth.password_is_set(settings):
        return redirect(url_for("auth.settings"))
    if request.method == "POST":
        pw = request.form.get("password", "")
        pw2 = request.form.get("password_confirm", "")
        if len(pw) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif pw != pw2:
            flash("Passwords do not match.", "error")
        else:
            auth.set_password(settings, pw)
            auth.login()
            _log_auth("setup", "ok")
            # First run drops into the setup wizard, not straight to
            # Settings — the wizard sequences broker → device → dashboard.
            return redirect(url_for("onboarding.index"))
    return render_template("setup.html")


@bp.route("/login", methods=["GET", "POST"], endpoint="login_view")
def login_view() -> Response | str:
    settings = _settings()
    if not auth.password_is_set(settings):
        return redirect(url_for("auth.setup"))
    if auth.is_authed():
        return redirect(_safe_next(request.args.get("next")))
    if request.method == "POST":
        pw = request.form.get("password", "")
        if auth.verify_password(settings, pw):
            auth.login()
            _log_auth("login", "ok")
            return redirect(_safe_next(request.form.get("next")))
        _log_auth("login", "denied", error="incorrect password")
        flash("Incorrect password.", "error")
    return render_template("login.html", next=request.args.get("next", ""))


@bp.post("/logout")
def logout_view() -> Response:
    auth.logout()
    _log_auth("logout", "ok")
    return redirect(url_for("auth.login_view"))


# Sub-page taxonomy. The 'app' and 'broker' sections live together under
# 'server' because they're both server-config; renderers / devices /
# plugins each get their own page since their lists grow independently.
_AREAS: tuple[tuple[str, str], ...] = (
    ("server", "Server"),
    ("renderers", "Renderers"),
    ("devices", "Devices"),
    ("plugins", "Plugins"),
)

# Map area → section kinds that belong on that page.
_AREA_KINDS: dict[str, set[str]] = {
    "server": {"app", "panel", "broker"},
    "renderers": {"renderer"},
    "devices": {"device"},
    "plugins": {"plugin"},
}


@bp.get("/settings")
def settings() -> Response:
    """Land on the Server sub-page by default."""
    return redirect(url_for("auth.settings_area", area="server"))


@bp.get("/settings/<area>", endpoint="settings_area")
def settings_area(area: str) -> str | Response:
    """Render one sub-page of /settings, scoped to a single area
    (server / renderers / devices / plugins)."""
    if area not in _AREA_KINDS:
        return Response(f"unknown settings area {area!r}", status=404)
    sections = [s for s in _build_sections() if s["kind"] in _AREA_KINDS[area]]
    # Devices area needs the kinds list (for the Add-device form) so the
    # template doesn't have to dig into the registry directly.
    device_kinds = (
        [{"id": d.id, "name": d.name, "panel": d.panel} for d in _device_kinds()]
        if area == "devices"
        else []
    )
    discovered = (
        [d for d in _format_discovered(_discovery_cache().all())
         if d["id"] not in _devices().devices]
        if area == "devices"
        else []
    )
    # Signature of what we're rendering, so the client-side poller knows
    # the baseline and can auto-refresh when the discovered set changes.
    discovered_sig = ",".join(
        sorted(f"{d['id']}:{d.get('kind') or ''}" for d in discovered)
    )
    return render_template(
        "settings.html",
        sections=sections,
        active_area=area,
        areas=_AREAS,
        device_kinds=device_kinds,
        panel_preset_choices=PANEL_PRESET_CHOICES if area == "devices" else [],
        panel_presets=PANEL_PRESETS if area == "devices" else {},
        discovered_devices=discovered,
        discovered_sig=discovered_sig,
        # When set (via ?calibrating=<id>), the matching device card shows
        # the "which number is in the top-left?" answer form.
        calibrating=request.args.get("calibrating") or "",
    )


def _area_for_section_kind(section_kind: str) -> str:
    """Which sub-page a section belongs on. Drives the post-save redirect
    so the user lands back on the page they were editing instead of being
    bounced to the default Server page."""
    if section_kind in ("app", "panel", "broker"):
        return "server"
    if section_kind.startswith("renderer-"):
        return "renderers"
    if section_kind.startswith("device-"):
        return "devices"
    if section_kind.startswith("plugin-"):
        return "plugins"
    return "server"


def _redirect_to_section(section_kind: str) -> Response:
    return redirect(
        url_for(
            "auth.settings_area",
            area=_area_for_section_kind(section_kind),
            _anchor=section_kind,
        )
    )


@bp.post("/settings/<section_kind>")
def settings_update(section_kind: str) -> Response:
    """Persist a single section. ``section_kind`` is one of ``app``,
    ``broker``, ``renderer-<id>``, ``device-<id>``, or ``plugin-<id>``."""
    settings_store = _settings()

    handlers: dict[str, Callable[[], str]] = {
        "app": lambda: _update_core("app", APP_FIELDS),
        "panel": lambda: _update_core("app", PANEL_FIELDS),
        "broker": lambda: _update_core("broker", BROKER_FIELDS),
    }

    if section_kind in handlers:
        message = handlers[section_kind]()
        flash(message, "ok")
        # Broker / App / Panel changes need a transport + HA-discovery
        # refresh to take effect without a restart (base_url, panel dims,
        # ha_discovery_enabled all flow through there).
        if section_kind in ("broker", "app", "panel"):
            _apply_broker_change()
        return _redirect_to_section(section_kind)

    if section_kind.startswith("renderer-"):
        rid = section_kind.removeprefix("renderer-")
        renderer = _renderers().get(rid)
        if renderer is None:
            return Response(f"unknown renderer {rid!r}", status=404)
        fields = list(renderer.manifest.get("settings", []))
        values = _values_from_form(fields)
        settings_store.update_for_namespace("renderers", rid, values, fields)
        # Per-renderer Enabled toggle lives outside the manifest, so it's
        # stored in a sibling section keyed by id. Browsers don't submit
        # unchecked checkboxes — treat the field's absence as "off".
        enabled = _coerce_form_value({"type": "switch"}, request.form.get("_enabled"))
        existing = settings_store.get_section("renderers_enabled")
        settings_store.patch_section("renderers_enabled", {**existing, rid: bool(enabled)})
        flash(f"{renderer.name} settings saved.", "ok")
        return _redirect_to_section(section_kind)

    if section_kind.startswith("plugin-"):
        pid = section_kind.removeprefix("plugin-")
        plugin = _plugins().get(pid)
        if plugin is None:
            return Response(f"unknown plugin {pid!r}", status=404)
        fields = list(plugin.manifest.get("settings", []))
        values = _values_from_form(fields)
        settings_store.update_for_namespace("plugins", pid, values, fields)
        flash(f"{plugin.name} settings saved.", "ok")
        return _redirect_to_section(section_kind)

    if section_kind.startswith("device-"):
        did = section_kind.removeprefix("device-")
        device = _devices().get(did)
        if device is None:
            return Response(f"unknown device {did!r}", status=404)
        if device.config_topic is None:
            return Response(f"device {did!r} has no config topic", status=400)
        fields = _config_fields_from_schema(device.config_schema)
        values = _values_from_form(fields)
        ok, err = device.validate_config(values)
        if not ok:
            flash(f"Invalid {device.name} config: {err}", "error")
            return _redirect_to_section(section_kind)
        settings_store.update_for_namespace("devices", did, values, fields)
        transport = _transport()
        try:
            transport.publish(
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
        return _redirect_to_section(section_kind)

    return Response(f"unknown section {section_kind!r}", status=404)


# -- device instances (multi-head) --------------------------------------
# The lifecycle (validate → write JSON → load → clone renderers) lives in
# app.device_service so the Add-device form and the Discovered one-click
# register share one implementation. These routes just parse the form,
# call the service, and flash + redirect.


def _device_data_root() -> Path:
    return current_app.config["DEVICE_DATA_ROOT"]  # type: ignore[no-any-return]


def _rebuild_transport_fn() -> Callable[[], None]:
    return current_app.config["REBUILD_TRANSPORT"]  # type: ignore[no-any-return]


def _device_kinds() -> list[Device]:
    """Built-in device kinds (the manifests under ``devices/``)."""
    return [d for d in _devices().all() if d.kind_of is None]


def _discovery_cache() -> DiscoveryCache:
    return current_app.config["DISCOVERY_CACHE"]  # type: ignore[no-any-return]


def _format_discovered(items: list[DiscoveredDevice]) -> list[dict[str, Any]]:
    """Shape DiscoveredDevice records for the template — flatten to plain
    dicts so Jinja doesn't trip over @property access, and pre-compute
    the relative-time string."""
    now = time.time()
    out: list[dict[str, Any]] = []
    for d in items:
        out.append(
            {
                "id": d.id,
                "kind": d.kind,
                "panel_w": d.panel_w,
                "panel_h": d.panel_h,
                "fw_version": d.fw_version,
                "ip": d.ip,
                "relative": _format_relative(max(0.0, now - d.received_at)),
                "parsed": d.parsed,
            }
        )
    return out


# Display label for each stored orientation value. The control is a
# rotation, so it reads in degrees; the stored values stay the
# aspect+flip strings the rest of the system uses. Order matches the
# calibration cycle (0/90/180/270).
_ORIENTATION_DEGREES: dict[str, str] = {
    "landscape": "0°",
    "portrait": "90°",
    "landscape_flipped": "180°",
    "portrait_flipped": "270°",
}


def _orientation_label(orientation: str) -> str:
    return _ORIENTATION_DEGREES.get(orientation, orientation)


def _panel_overrides_from_form(form: Any) -> dict[str, Any]:
    """Resolve the Add-device form's panel size into a ``{"w","h"}``
    override dict. A preset wins; otherwise the custom width/height
    inputs are used (ignored if non-numeric)."""
    preset = (form.get("panel_preset") or "").strip()
    if preset in PANEL_PRESETS:
        w, h = PANEL_PRESETS[preset]
        return {"w": w, "h": h}
    overrides: dict[str, Any] = {}
    for field_name, key in (("panel_w", "w"), ("panel_h", "h")):
        raw = form.get(field_name)
        if raw:
            try:
                overrides[key] = int(raw)
            except ValueError:
                pass
    return overrides


@bp.post("/settings/devices/add")
def devices_add() -> Response:
    """Create a new device instance from the Devices-tab form. No restart
    needed — the new device shows up immediately in the page editor's
    Target-device dropdown."""
    form = request.form
    result = device_service.create_instance(
        devices=_devices(),
        renderers=_renderers(),
        data_root=_device_data_root(),
        instance_id=form.get("id") or "",
        kind_id=(form.get("kind") or "").strip(),
        name=form.get("name") or "",
        panel_overrides=_panel_overrides_from_form(form),
        orientation=form.get("panel_orientation"),
    )
    if not result.ok or result.device is None:
        flash(result.error or "Failed to add device.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    _rebuild_transport_fn()()
    flash(f"Added device {result.device.name!r}.", "ok")
    return redirect(
        url_for("auth.settings_area", area="devices", _anchor=f"device-{result.device.id}")
    )


@bp.get("/settings/devices/discovered.json")
def devices_discovered_json() -> Response:
    """Current discovered devices as JSON. The Devices page + setup wizard
    poll this so a newly-announced client appears without a manual refresh
    (the heartbeat may arrive seconds after the page loads)."""
    items = [
        {"id": d["id"], "kind": d["kind"]}
        for d in _format_discovered(_discovery_cache().all())
        if d["id"] not in _devices().devices
    ]
    return current_app.response_class(
        json.dumps({"devices": items}), mimetype="application/json"
    )


@bp.post("/settings/devices/discovery/<discovered_id>/register")
def devices_register_discovered(discovered_id: str) -> Response:
    """One-click register a discovered device — same lifecycle as the
    Add-device form, but kind / panel / id default from the cached
    heartbeat (the form may override any of them)."""
    cache = _discovery_cache()
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

    result = device_service.create_instance(
        devices=_devices(),
        renderers=_renderers(),
        data_root=_device_data_root(),
        instance_id=form.get("id") or discovered_id,
        kind_id=kind_id,
        name=form.get("name") or "",
        panel_overrides=panel_overrides,
    )
    if not result.ok or result.device is None:
        flash(result.error or "Failed to register device.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    cache.forget(discovered_id)
    _rebuild_transport_fn()()
    flash(f"Registered discovered device {result.device.name!r}.", "ok")
    return redirect(
        url_for("auth.settings_area", area="devices", _anchor=f"device-{result.device.id}")
    )


@bp.post("/settings/devices/discovery/<discovered_id>/dismiss")
def devices_dismiss_discovered(discovered_id: str) -> Response:
    """Drop a discovered device, and clear its retained status message on
    the broker so it stays gone.

    A client publishes its heartbeat retained, so the broker replays it to
    the discovery wildcard on every connect — dismissing only the
    in-memory cache lets a stale/renamed device (e.g. one reflashed to a
    new id) pop straight back. Publishing an empty retained payload to its
    status topic clears the retention; ``DiscoveryCache.record`` ignores
    that empty tombstone so it doesn't re-add a kind-less ghost. A live
    device simply re-announces on its next heartbeat."""
    cache_had = _discovery_cache().forget(discovered_id)
    try:
        _transport().publish(
            f"tesserae/{discovered_id}/status", b"", qos=1, retain=True
        )
        flash(f"Dismissed {discovered_id!r} and cleared its retained heartbeat.", "ok")
    except Exception as exc:  # transport offline — cache is still cleared
        if cache_had:
            flash(
                f"Dismissed {discovered_id!r}, but couldn't clear the retained "
                f"message (broker offline: {exc}); it may reappear on reconnect.",
                "error",
            )
        else:
            flash(f"{discovered_id!r} wasn't in the discovery cache.", "error")
    return redirect(url_for("auth.settings_area", area="devices"))


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

    result = device_service.update_instance_panel(
        devices=_devices(),
        renderers=_renderers(),
        data_root=_device_data_root(),
        instance_id=instance_id,
        w=new_w,
        h=new_h,
        orientation=form.get("panel_orientation") or "landscape",
        gamut=form.get("panel_gamut"),
    )
    if not result.ok or result.device is None:
        flash(result.error or "Panel update failed.", "error")
        return redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))
    _rebuild_transport_fn()()
    panel = result.device.panel or {}
    flash(
        f"Updated {result.device.name!r} panel to "
        f"{panel.get('w')}×{panel.get('h')} at {_orientation_label(panel.get('orientation', 'landscape'))}.",
        "ok",
    )
    return redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))


@bp.post("/settings/devices/<instance_id>/delete")
def devices_delete(instance_id: str) -> Response:
    """Remove a user-created device instance. Built-in kinds are
    refused — they ship with the app."""
    result = device_service.delete_instance(
        devices=_devices(),
        renderers=_renderers(),
        instance_id=instance_id,
    )
    if not result.ok or result.device is None:
        flash(result.error or "Delete failed.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    _rebuild_transport_fn()()
    flash(f"Deleted device {result.device.name!r}.", "ok")
    return redirect(url_for("auth.settings_area", area="devices"))


@bp.post("/settings/devices/<instance_id>/calibrate")
def devices_calibrate(instance_id: str) -> Response:
    """Push the orientation test card to a device through its real
    renderer (so the on-panel result reflects the current settings),
    then prompt the user for what they see."""
    device = _devices().get(instance_id)
    if device is None or device.kind_of is None or device.panel is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    panel = device.panel
    card = build_calibration_card(int(panel["w"]), int(panel["h"]))
    result = _push_manager().push_image(
        card, source_label=f"calibration:{instance_id}", device_id=instance_id
    )
    if result.status == "sent":
        flash("Calibration card sent — look at your panel, then answer below.", "ok")
    else:
        flash(f"Calibration push {result.status}: {result.error or '(no detail)'}", "error")
    # ?calibrating=<id> makes the device card render the answer form.
    return redirect(
        url_for("auth.settings_area", area="devices", calibrating=instance_id,
                _anchor=f"device-{instance_id}")
    )


@bp.post("/settings/devices/<instance_id>/calibrate/apply")
def devices_calibrate_apply(instance_id: str) -> Response:
    """Set the orientation derived from the calibration answer, then
    re-push the card so the user can confirm it's now upright."""
    anchor = f"device-{instance_id}"
    device = _devices().get(instance_id)
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
        devices=_devices(),
        renderers=_renderers(),
        data_root=_device_data_root(),
        instance_id=instance_id,
        w=w,
        h=h,
        orientation=target,
    )
    if not result.ok or result.device is None:
        flash(result.error or "Calibration failed.", "error")
        return redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))
    _rebuild_transport_fn()()
    # Confirm re-push at the new orientation.
    if result.device.panel is not None:
        card = build_calibration_card(int(result.device.panel["w"]), int(result.device.panel["h"]))
        _push_manager().push_image(
            card, source_label=f"calibration:{instance_id}", device_id=instance_id
        )
    flash(
        f"Set {result.device.name!r} to {_orientation_label(target)} — your dashboard now reads "
        "upright in that orientation. Re-sent the card to confirm; adjust Rotation below if needed.",
        "ok",
    )
    return redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))


# -- diagnostics ----------------------------------------------------


@bp.post("/settings/diagnostics/test_broker")
def diagnostics_test_broker() -> Response:
    """Open a fresh connection with the currently-saved broker settings,
    publish a no-op probe, then disconnect. Independent of the running
    transport so it actually tests the saved values rather than whatever
    the app currently has loaded."""
    raw = _settings().get_section("broker")
    host = str(raw.get("host") or "").strip()
    if not host:
        flash("Broker test: no host configured.", "error")
        return redirect(url_for("auth.settings_area", area="server"))
    config = BrokerConfig(
        host=host,
        port=int(raw.get("port") or 1883),
        username=raw.get("username") or None,
        password=raw.get("password_secret") or None,
        keepalive=int(raw.get("keepalive") or 60),
        client_id=str(raw.get("client_id") or "tesserae") + "-probe",
    )
    probe = MqttTransport(config)
    try:
        probe.connect()
        probe.publish("tesserae/_probe", b"ping", qos=0, retain=False)
    except Exception as exc:
        flash(f"Broker test failed: {type(exc).__name__}: {exc}", "error")
    else:
        flash(f"Broker test ok: connected to {host}:{config.port} and published.", "ok")
    finally:
        with contextlib.suppress(Exception):
            probe.disconnect()
    return redirect(url_for("auth.settings_area", area="server"))


@bp.post("/settings/diagnostics/test_push")
def diagnostics_test_push() -> Response:
    """Generate a small synthetic PNG and run it through PushManager.push_image.
    Exercises every loaded renderer end-to-end (transform -> write -> publish)
    + the event-log path, without needing a saved dashboard."""
    import io

    from PIL import Image, ImageDraw

    panel = _settings().get_section("app")
    w = int(panel.get("panel_w") or 400)
    h = int(panel.get("panel_h") or 200)
    img = Image.new("RGB", (w, h), (240, 240, 235))
    draw = ImageDraw.Draw(img)
    # Geometric tesserae mark so the test push looks distinct in the
    # device's history.
    draw.rectangle((20, 20, w - 20, h - 20), outline=(13, 140, 126), width=4)
    draw.rectangle((w // 4, h // 4, 3 * w // 4, 3 * h // 4), fill=(13, 140, 126))
    draw.text((30, h - 40), "tesserae test push", fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    result = _push_manager().push_image(buf.getvalue(), source_label="diagnostics_test")
    if result.status == "sent":
        flash(
            f"Test push ok: {len(result.renderers)} renderer(s) published "
            f"in {result.duration_s:.2f}s.",
            "ok",
        )
    elif result.status == "busy":
        flash("Test push: another push is already in flight; try again.", "error")
    else:
        flash(f"Test push {result.status}: {result.error or '(no detail)'}", "error")
    return redirect(url_for("auth.settings_area", area="renderers"))


# -- internals ----------------------------------------------------------


def _update_core(section_name: str, fields: list[dict[str, Any]]) -> str:
    """Apply a single core section's form values. Honours the secret-mask
    convention so re-submitting a masked field doesn't blow the value
    away."""
    values = _values_from_form(fields)
    existing = _settings().get_section(section_name)
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
    _settings().update_section(section_name, out)
    return f"{section_name.capitalize()} settings saved."


def _build_sections() -> list[dict[str, Any]]:
    settings_store = _settings()
    sections: list[dict[str, Any]] = []

    app_raw = settings_store.get_section("app")
    sections.append(
        {
            "id": "app",
            "kind": "app",
            "title": "App",
            "blurb": "Where Tesserae lives on the network.",
            "fields": APP_FIELDS,
            "state": _values_for_core("app", APP_FIELDS, app_raw),
            "endpoint": url_for("auth.settings_update", section_kind="app"),
        }
    )

    broker_raw = settings_store.get_section("broker")
    sections.append(
        {
            "id": "broker",
            "kind": "broker",
            "title": "MQTT broker",
            "blurb": "Tesserae publishes frames here; devices subscribe.",
            "fields": BROKER_FIELDS,
            "state": _values_for_core("broker", BROKER_FIELDS, broker_raw),
            "endpoint": url_for("auth.settings_update", section_kind="broker"),
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

    enabled_map = settings_store.get_section("renderers_enabled")
    for renderer in _renderers().all():
        # Per-instance clones inherit the base renderer's settings; the
        # cards add no UI value and just create N rows of the same
        # form. Filter them out — clone ids always contain '__'
        # (see renderer_loader.clone_for_instances).
        if "__" in renderer.id:
            continue
        fields = list(renderer.manifest.get("settings", []))
        sid = f"renderer-{renderer.id}"
        rid = renderer.id
        is_enabled = enabled_map.get(rid)
        if is_enabled is None:
            is_enabled = True
        sections.append(
            {
                "id": sid,
                "kind": "renderer",
                "title": f"Renderer: {renderer.name}",
                "blurb": renderer.manifest.get("description") or "",
                "fields": fields,
                "state": _render_for_admin("renderers", renderer.id, fields),
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

    for device in _devices().all():
        # Built-in kinds are templates, not bindable devices — they
        # never appear on the Devices tab. Every physical display is
        # represented by an instance (added manually or auto-registered
        # from the Discovered strip).
        if device.kind_of is None:
            continue
        sid = f"device-{device.id}"
        fields = _config_fields_from_schema(device.config_schema)
        is_instance = device.kind_of is not None
        sections.append(
            {
                "id": sid,
                "kind": "device",
                "title": f"Device: {device.name}",
                "blurb": device.manifest.get("description") or "",
                "fields": fields,
                "state": (
                    settings_store.get_for_runtime("devices", device.id, fields) if fields else {}
                ),
                "endpoint": (url_for("auth.settings_update", section_kind=sid) if fields else None),
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
            }
        )

    for plugin in _plugins().plugins.values():
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
                "state": _render_for_admin("plugins", plugin.id, fields),
                "endpoint": url_for("auth.settings_update", section_kind=sid),
            }
        )

    return sections


def _status_view(device: Device) -> dict[str, Any]:
    """Build the status-block dict the template renders above the config
    form: freshness class (ok / warn / stale / unknown), relative time,
    and the parsed key/value pairs."""
    cache = _device_status().get(device.id)
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
        "relative": _format_relative(age),
        "parsed": cache.get("parsed", {}),
    }


def _format_relative(seconds: float) -> str:
    if seconds < 5:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)} s ago"
    if seconds < 3600:
        return f"{int(seconds / 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)} h ago"
    return f"{int(seconds / 86400)} d ago"


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


def _apply_broker_change() -> None:
    """Hot-swap the MQTT transport on broker setting changes."""
    rebuild = current_app.config.get("REBUILD_TRANSPORT")
    if callable(rebuild):
        rebuild()


def register(app: Flask) -> None:
    app.register_blueprint(bp)
