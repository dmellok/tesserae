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

import json
import time
from collections.abc import Callable
from typing import Any

from flask import Blueprint, Flask, current_app, flash, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from app import auth
from app.device_loader import Device, DeviceRegistry
from app.plugin_loader import PluginRegistry
from app.renderer_loader import RendererRegistry
from app.state.settings_store import SECRET_MASK, SettingsStore
from app.transport import MqttTransport

# How fresh a heartbeat has to be to read as "ok" in the UI. Past this it
# decays through "warn" (2x) into "stale". Tuned for the typical pi_client
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
        "name": "base_url",
        "type": "string",
        "label": "Base URL",
        "default": "http://127.0.0.1:8000",
        "help": (
            "Public URL the panel listeners use to fetch artifacts. Use the host's "
            "LAN address (e.g. http://192.168.1.10:8000) so the Pi / ESP32 can reach it."
        ),
    },
    {
        "name": "panel_w",
        "type": "number",
        "label": "Default panel width (px)",
        "default": 1600,
        "min": 1,
        "help": (
            "Used for Send-page uploads that aren't a saved dashboard. Saved "
            "dashboards always use their own panel dims."
        ),
    },
    {
        "name": "panel_h",
        "type": "number",
        "label": "Default panel height (px)",
        "default": 1200,
        "min": 1,
    },
]

BROKER_FIELDS: list[dict[str, Any]] = [
    {"name": "host", "type": "string", "label": "Host", "default": ""},
    {"name": "port", "type": "number", "label": "Port", "default": 1883, "min": 1, "max": 65535},
    {"name": "username", "type": "string", "label": "Username", "default": ""},
    {"name": "password", "type": "string", "label": "Password", "default": "", "secret": True},
    {"name": "keepalive", "type": "number", "label": "Keepalive (s)", "default": 60, "min": 5},
    {
        "name": "client_id",
        "type": "string",
        "label": "MQTT client id",
        "default": "tesserae",
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
    Python type the field declares. Bools come back as bools, numbers as
    int/float, selects coerce to the choice-value's type, everything else
    as string."""
    ftype = field.get("type", "string")
    if ftype == "boolean":
        return raw in ("on", "true", "1")
    if ftype == "number":
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
        # Coerce to the choice values' type so an int-valued select
        # round-trips as int through the form. Form values are strings, so
        # we need to match by string-coerced equality.
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
        if field.get("type") == "boolean":
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
            return redirect(url_for("auth.settings"))
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
            return redirect(_safe_next(request.form.get("next")))
        flash("Incorrect password.", "error")
    return render_template("login.html", next=request.args.get("next", ""))


@bp.post("/logout")
def logout_view() -> Response:
    auth.logout()
    return redirect(url_for("auth.login_view"))


@bp.get("/settings")
def settings() -> str:
    """Render every editable section. The template takes a homogeneous
    list of ``Section`` dicts and walks each one's fields uniformly."""
    sections = _build_sections()
    return render_template("settings.html", sections=sections)


@bp.post("/settings/<section_kind>")
def settings_update(section_kind: str) -> Response:
    """Persist a single section. ``section_kind`` is one of ``app``,
    ``broker``, ``renderer-<id>``, or ``plugin-<id>``."""
    settings_store = _settings()

    handlers: dict[str, Callable[[], str]] = {
        "app": lambda: _update_core("app", APP_FIELDS),
        "broker": lambda: _update_core("broker", BROKER_FIELDS),
    }

    if section_kind in handlers:
        message = handlers[section_kind]()
        flash(message, "ok")
        # Broker changes need transport rewiring — apply now, no restart.
        if section_kind == "broker":
            _apply_broker_change()
        return redirect(url_for("auth.settings", _anchor=section_kind))

    if section_kind.startswith("renderer-"):
        rid = section_kind.removeprefix("renderer-")
        renderer = _renderers().get(rid)
        if renderer is None:
            return Response(f"unknown renderer {rid!r}", status=404)
        fields = list(renderer.manifest.get("settings", []))
        values = _values_from_form(fields)
        settings_store.update_for_namespace("renderers", rid, values, fields)
        flash(f"{renderer.name} settings saved.", "ok")
        return redirect(url_for("auth.settings", _anchor=section_kind))

    if section_kind.startswith("plugin-"):
        pid = section_kind.removeprefix("plugin-")
        plugin = _plugins().get(pid)
        if plugin is None:
            return Response(f"unknown plugin {pid!r}", status=404)
        fields = list(plugin.manifest.get("settings", []))
        values = _values_from_form(fields)
        settings_store.update_for_namespace("plugins", pid, values, fields)
        flash(f"{plugin.name} settings saved.", "ok")
        return redirect(url_for("auth.settings", _anchor=section_kind))

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
            return redirect(url_for("auth.settings", _anchor=section_kind))
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
        return redirect(url_for("auth.settings", _anchor=section_kind))

    return Response(f"unknown section {section_kind!r}", status=404)


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

    for renderer in _renderers().all():
        fields = list(renderer.manifest.get("settings", []))
        if not fields:
            continue
        sid = f"renderer-{renderer.id}"
        sections.append(
            {
                "id": sid,
                "kind": "renderer",
                "title": f"Renderer: {renderer.name}",
                "blurb": renderer.manifest.get("description") or "",
                "fields": fields,
                "state": _render_for_admin("renderers", renderer.id, fields),
                "endpoint": url_for("auth.settings_update", section_kind=sid),
                "meta": {
                    "Topic": renderer.topic,
                    "Retain": "yes" if renderer.retain else "no",
                    "Device": renderer.device,
                },
            }
        )

    for device in _devices().all():
        sid = f"device-{device.id}"
        fields = _config_fields_from_schema(device.config_schema)
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
                },
                "status": _status_view(device),
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
