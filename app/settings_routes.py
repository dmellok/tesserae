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
import zoneinfo
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    Flask,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.wrappers import Response

from app import auth, calibration, device_service, device_timetable, renderer_loader
from app import backup as _backup_mod
from app import updater as _updater_mod
from app.calibration import build_calibration_card, target_orientation
from app.device_loader import Device, DeviceRegistry
from app.discovery import DiscoveredDevice, DiscoveryCache
from app.network import detect_local_ip
from app.panel import (
    DEFAULT_PRESET,
    PANEL_PRESET_CHOICES,
    PANEL_PRESETS,
    panel_overrides_from_form,
)
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

# Canonical Area/City zones only, plus a "system" sentinel and explicit UTC.
# ``zoneinfo.available_timezones()`` also returns legacy/compat buckets
# (Etc/* fixed offsets, SystemV/*, country aliases like US/* and Brazil/*,
# and single-word names like GMT or Japan) that just clutter a picker — we
# drop those and keep the modern ``Area/City`` form most pickers show.
# ``tzdata`` is a dependency so the list is complete regardless of host OS.
_LEGACY_TZ_PREFIXES = ("Etc/", "SystemV/", "US/", "Canada/", "Brazil/", "Mexico/", "Chile/")
_TZ_CHOICES: list[dict[str, str]] = [
    {"value": "system", "label": "System (host local time)"},
    {"value": "UTC", "label": "UTC"},
    *(
        {"value": tz, "label": tz}
        for tz in sorted(zoneinfo.available_timezones())
        if "/" in tz and not tz.startswith(_LEGACY_TZ_PREFIXES)
    ),
]

APP_FIELDS: list[dict[str, Any]] = [
    {
        "name": "timezone",
        "type": "select",
        "label": "Timezone",
        "default": "system",
        "choices": _TZ_CHOICES,
        "help": (
            "Used by the scheduler when interpreting daily fire times and "
            "time-of-day windows. 'System' uses the host's local time."
        ),
    },
    {
        "name": "latitude",
        "type": "number",
        "label": "Latitude",
        "default": "",
        "step": "any",  # decimal degrees — a default step of 1 rejects e.g. -37.8136
        "help": (
            "Default location for weather / sky / sunrise widgets, so you don't "
            "re-enter coordinates per widget. A widget can still override it with "
            "its own latitude/longitude. Decimal degrees, e.g. -37.8136."
        ),
    },
    {
        "name": "longitude",
        "type": "number",
        "label": "Longitude",
        "default": "",
        "step": "any",
        "help": "Decimal degrees, e.g. 144.9631. Paired with Latitude above.",
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
    {
        "name": "mdns_enabled",
        "type": "switch",
        "label": "Advertise tesserae.local over mDNS",
        "default": False,
        "help": (
            "Announce this server as tesserae.local (and an _http._tcp service) "
            "over mDNS / Bonjour so you can reach it by name without changing "
            "the host machine's hostname. Default off."
        ),
    },
    {
        "name": "quiet_hours_enabled",
        "type": "switch",
        "label": "Quiet hours",
        "default": False,
        "help": (
            "Suppress automated pushes (scheduler firings, webhook calls) during "
            "a daily time window — typical use is to stop the panel waking the room "
            "overnight. Manual pushes from the Send page or Push-now buttons still go "
            "through; quiet hours filter automation, not deliberate user intent. "
            "Each device can override the window in Settings → Devices."
        ),
    },
    {
        "name": "quiet_hours_start",
        "type": "time",
        "label": "Quiet hours start",
        "default": "22:00",
        "help": "When the daily quiet window begins. Honours Settings → Server → App → Timezone.",
    },
    {
        "name": "quiet_hours_end",
        "type": "time",
        "label": "Quiet hours end",
        "default": "07:00",
        "help": (
            "When the daily quiet window ends. If end < start, the window wraps "
            "across midnight (e.g. 22:00 → 07:00 = overnight)."
        ),
    },
    {
        "name": "telemetry_enabled",
        "type": "switch",
        "label": "Send anonymous usage telemetry",
        "default": False,
        "help": (
            "Two events to the project's analytics backend — app.started "
            "(version + platform) and update.applied (from/to short SHA + "
            "channel). Identified only by a random instance UUID; no IPs, "
            "paths, settings, secrets, or push contents. Suggested on during "
            "onboarding so the maintainer can see how many people are running "
            "Tesserae and what versions they're on — flip it off here if "
            "you'd rather not. TESSERAE_TELEMETRY=0 also disables."
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

# Field order matters: the settings template groups the external-broker
# fields (host/port/username/password) and the built-in-broker fields
# (embedded_*) into two contiguous blocks it can show/hide. The
# ``embedded_enabled`` switch leads the card; flipping it hides whichever
# block is irrelevant. ``keepalive``/``client_id`` configure the client
# connection either way, so they stay visible at the bottom.
BROKER_FIELDS: list[dict[str, Any]] = [
    {
        "name": "embedded_enabled",
        "type": "switch",
        "label": "Built-in broker",
        "default": False,
        "help": (
            "Run an in-process MQTT broker (amqtt). Convenient when you "
            "don't have a Mosquitto host handy; leave off to point Tesserae "
            "at an external broker instead. "
            "Heads-up: amqtt only speaks MQTT v3.1.1. Tesserae's own Pi / "
            "ESP32 clients are fine (paho-mqtt defaults to 3.1.1), but if "
            "you connect with MQTT Explorer / MQTTX / Home Assistant / "
            "Node-RED you'll need to set their protocol version to 3.1.1 — "
            'v5 clients get rejected with "Invalid protocol". Need full '
            "v5 support? Install Mosquitto (apt/brew) and point Tesserae "
            "at it via the Host / Port fields below."
        ),
    },
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
        "default": "",
        "help": (
            "Must be unique per instance — a broker evicts a duplicate client "
            "id the moment another connects with it, which causes an endless "
            "reconnect loop. Leave blank to auto-use 'tesserae-<hostname>'; the "
            "--dev server appends '-dev'."
        ),
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
    ("system", "System"),
)

# Map area → section kinds that belong on that page. The "system" page
# is hand-built (updates + backups) — no manifest-driven sections.
_AREA_KINDS: dict[str, set[str]] = {
    "server": {"app", "panel", "broker"},
    "renderers": {"renderer"},
    "devices": {"device"},
    "plugins": {"plugin"},
    "system": set(),
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
        [
            d
            for d in _format_discovered(_discovery_cache().all())
            if d["id"] not in _devices().devices
        ]
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
        updater = current_app.config["UPDATER"]
        try:
            system_state = updater.current_state()
        except _updater_mod.UpdaterError as err:
            flash(f"Update state unavailable: {err}", "error")
        system_last_check = updater.last_check
        system_history = list(reversed(updater.history()))
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
        _app_raw = _settings().get_section("app")
        system_webhook_token_set = bool(
            (_app_raw.get("webhook_token_secret") or _app_raw.get("webhook_token") or "").strip()
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


# ====================================================================
# Settings → System: self-update + backup endpoints.
#
# Updates and restore-from-backup both restart the process via os.execv
# (the in-process Playwright renderer + admin UI come back ~1 s later).
# Refused in --dev: the werkzeug reloader owns restarts there.
# All gated by the push manager's lock so a frame push can't race with a
# tree-mutating operation.
# ====================================================================


def _system_redirect() -> Response:
    return redirect(url_for("auth.settings_area", area="system"))


def _updater() -> _updater_mod.Updater:
    return current_app.config["UPDATER"]  # type: ignore[no-any-return]


def _data_root() -> Path:
    return current_app.config["DATA_ROOT"]  # type: ignore[no-any-return]


def _refuse_in_dev() -> Response | None:
    if current_app.debug:
        flash(
            "Updates and restores only run on the production (waitress) server — "
            "the --dev reloader owns restarts.",
            "error",
        )
        return _system_redirect()
    return None


@bp.post("/settings/system/update/check")
def system_update_check() -> Response:
    channel = (request.form.get("channel") or "edge").strip()
    try:
        check = _updater().check_remote(channel)
    except _updater_mod.UpdaterError as err:
        flash(f"Check failed: {err}", "error")
        return _system_redirect()
    if check.available:
        flash(
            f"Update available: {check.commits_behind} commit"
            f"{'s' if check.commits_behind != 1 else ''} behind "
            f"{check.target_ref}.",
            "ok",
        )
    else:
        flash(f"Up to date with {check.target_ref}.", "ok")
    return _system_redirect()


@bp.post("/settings/system/update/apply")
def system_update_apply() -> Response:
    refused = _refuse_in_dev()
    if refused is not None:
        return refused
    channel = (request.form.get("channel") or "edge").strip()
    force = request.form.get("force") == "1"
    push_mgr = _push_manager()
    try:
        result = _updater().apply_update(channel, force=force, push_lock=push_mgr._lock)
    except _updater_mod.UpdaterError as err:
        flash(str(err), "error")
        return _system_redirect()
    if not result.ok:
        flash(f"Update failed: {result.error or 'unknown error'}", "error")
        return _system_redirect()
    if result.from_sha == result.to_sha:
        flash(f"Already up to date ({result.to_sha[:7]}).", "ok")
        return _system_redirect()
    note = f"Updated {result.from_sha[:7]} → {result.to_sha[:7]}"
    if result.pip_changed:
        note += " (deps reinstalled)"
    flash(note + ". Restarting…", "ok")
    telemetry = current_app.config.get("TELEMETRY")
    if telemetry is not None:
        telemetry.send(
            "update.applied",
            {
                "from": result.from_sha[:7],
                "to": result.to_sha[:7],
                "channel": channel,
                "pip_changed": "yes" if result.pip_changed else "no",
            },
        )
    _updater().restart(delay_s=1.5)
    return _system_redirect()


@bp.post("/settings/system/update/rollback")
def system_update_rollback() -> Response:
    refused = _refuse_in_dev()
    if refused is not None:
        return refused
    push_mgr = _push_manager()
    try:
        result = _updater().rollback_last(push_lock=push_mgr._lock)
    except _updater_mod.UpdaterError as err:
        flash(str(err), "error")
        return _system_redirect()
    if not result.ok:
        flash(f"Rollback failed: {result.error or 'unknown error'}", "error")
        return _system_redirect()
    flash(f"Rolled back to {result.to_sha[:7]}. Restarting…", "ok")
    _updater().restart(delay_s=1.5)
    return _system_redirect()


@bp.post("/settings/system/backup/create")
def system_backup_create() -> Response:
    note = (request.form.get("note") or "").strip()[:200]
    try:
        backup = _backup_mod.create(_data_root(), label=_backup_mod.LABEL_MANUAL, note=note)
    except OSError as err:
        flash(f"Backup failed: {err}", "error")
        return _system_redirect()
    flash(f"Backup created: {backup.id} ({backup.bytes // 1024} KB).", "ok")
    return _system_redirect()


@bp.get("/settings/system/backup/<backup_id>/download")
def system_backup_download(backup_id: str) -> Response:
    backup = _backup_mod.get(_data_root(), backup_id)
    if backup is None:
        return Response("backup not found", status=404)
    # Serve from a BytesIO so the underlying file is fully read + closed
    # before the response is built — avoids a lingering file handle that
    # finalizers complain about under the test client.
    import io

    data = backup.path.read_bytes()
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=f"tesserae-backup-{backup_id}.zip",
        mimetype="application/zip",
    )


@bp.post("/settings/system/backup/<backup_id>/restore")
def system_backup_restore(backup_id: str) -> Response:
    refused = _refuse_in_dev()
    if refused is not None:
        return refused
    push_mgr = _push_manager()
    if not push_mgr._lock.acquire(blocking=True, timeout=10):
        flash("Another push is in flight — try again in a moment.", "error")
        return _system_redirect()
    try:
        try:
            _backup_mod.restore(_data_root(), backup_id)
        except (FileNotFoundError, ValueError, OSError) as err:
            flash(f"Restore failed: {err}", "error")
            return _system_redirect()
    finally:
        push_mgr._lock.release()
    flash(f"Restored from {backup_id}. Restarting…", "ok")
    _updater().restart(delay_s=1.5)
    return _system_redirect()


@bp.post("/settings/system/backup/<backup_id>/delete")
def system_backup_delete(backup_id: str) -> Response:
    if _backup_mod.delete(_data_root(), backup_id):
        flash(f"Deleted backup {backup_id}.", "ok")
    else:
        flash(f"No backup named {backup_id}.", "error")
    return _system_redirect()


@bp.post("/settings/system/webhook/regenerate")
def system_webhook_regenerate() -> Response:
    """Mint a fresh random webhook token and persist it. Stashed in the
    session as ``_webhook_token_reveal`` so the Settings GET that follows
    the redirect can pop it into a one-shot modal with a copy button.
    After that render it's gone — the disk value is masked like any
    other ``_secret`` field."""
    from app.webhook_routes import generate_token

    token = generate_token()
    _settings().update_section("app", {"webhook_token_secret": token})
    session["_webhook_token_reveal"] = token
    return _system_redirect()


@bp.post("/settings/system/webhook/set")
def system_webhook_set() -> Response:
    """Set or clear the webhook bearer token by hand. ``clear=1`` wipes
    the on-disk value (webhooks return 503 until re-set). Otherwise the
    pasted ``webhook_token`` value replaces whatever was there. Used
    when an automation tool already has a specific secret and the user
    wants Tesserae to match it instead of issuing a fresh one."""
    if request.form.get("clear"):
        _settings().update_section("app", {"webhook_token_secret": ""})
        flash("Webhook token cleared. POST /api/v1/push will return 503.", "ok")
        return _system_redirect()
    token = (request.form.get("webhook_token") or "").strip()
    if not token:
        flash("Paste a token first, or use Clear to disable webhooks.", "error")
        return _system_redirect()
    _settings().update_section("app", {"webhook_token_secret": token})
    flash("Webhook token saved.", "ok")
    return _system_redirect()


@bp.post("/settings/system/telemetry/test")
def system_telemetry_test() -> Response:
    """Fire a synchronous app.started and surface the outcome in a flash
    + the Events tab. Dev-only — the card is hidden in production
    builds, so this route is gated to ``current_app.debug`` to avoid
    leaving an undocumented endpoint exposed."""
    if not current_app.debug:
        return _system_redirect()
    telemetry = current_app.config.get("TELEMETRY")
    if telemetry is None or not telemetry.enabled:
        flash(
            "Telemetry is off. Tick the toggle in Settings → Server → App first.",
            "error",
        )
        return _system_redirect()
    err = telemetry.test_send()
    if err:
        flash(f"Test event failed: {err}. Check the endpoint config.", "error")
    else:
        flash("Test event delivered. Check the Events tab for the row.", "ok")
    return _system_redirect()


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
        # Capture telemetry's pre-save state so we can detect an off→on
        # transition and fire a test event without making the user
        # restart the process.
        was_telemetry_on = (
            bool(_settings().get_section("app").get("telemetry_enabled", False))
            if section_kind == "app"
            else False
        )
        message = handlers[section_kind]()
        flash(message, "ok")
        if section_kind == "app":
            now_on = bool(_settings().get_section("app").get("telemetry_enabled", False))
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
        return _redirect_to_section(section_kind)

    if section_kind.startswith("renderer-"):
        rid = section_kind.removeprefix("renderer-")
        renderer = _renderers().get(rid)
        if renderer is None:
            return Response(f"unknown renderer {rid!r}", status=404)
        # device_setting fields belong on the device card, not the
        # renderer card. Filter them here too so a hand-crafted POST
        # to the renderer endpoint can't override per-device tuning.
        fields = [f for f in renderer.manifest.get("settings", []) if not f.get("device_setting")]
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
        panel_overrides=panel_overrides_from_form(form),
        orientation=form.get("panel_orientation"),
    )
    if not result.ok or result.device is None:
        flash(result.error or "Failed to add device.", "error")
        return redirect(url_for("auth.settings_area", area="devices"))
    # New device's clones inherit picture-quality (dither/saturation/
    # contrast) from the user's existing base-renderer values where
    # available, so a freshly-added device matches the rest of the
    # fleet rather than dropping back to the manifest defaults.
    renderer_loader.seed_device_settings_from_base(_renderers(), _settings())
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
    return current_app.response_class(json.dumps({"devices": items}), mimetype="application/json")


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
    # Same picture-quality seeding as the manual add-device path.
    renderer_loader.seed_device_settings_from_base(_renderers(), _settings())
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
        _transport().publish(f"tesserae/{discovered_id}/status", b"", qos=1, retain=True)
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

    underscan_raw = form.get("panel_underscan")
    underscan: int | None = None
    if underscan_raw:
        try:
            underscan = max(0, int(underscan_raw))
        except ValueError:
            underscan = 0

    result = device_service.update_instance_panel(
        devices=_devices(),
        renderers=_renderers(),
        data_root=_device_data_root(),
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
    _rebuild_transport_fn()()
    panel = result.device.panel or {}
    flash(
        f"Updated {result.device.name!r} panel to "
        f"{panel.get('w')}×{panel.get('h')} at {_orientation_label(panel.get('orientation', 'landscape'))}.",
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
        devices=_devices(),
        renderers=_renderers(),
        data_root=_device_data_root(),
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


@bp.post("/settings/devices/<instance_id>/save")
def devices_update_combined(instance_id: str) -> Response:
    """One-shot save for the whole device card.

    The card composes three independent subsections — renderer-defined
    config fields, panel (orientation/dims/gamut/icon/underscan), and
    per-device quiet-hours override. The template now wraps them in
    one form posting here; this handler fans out to the same service
    helpers the per-subsection endpoints (``/panel``, ``/quiet-hours``,
    and ``/settings/device-<id>``) call so behaviour matches one-for-
    one. Each subsection is detected by presence of its inputs and
    runs independently — an error in one is flashed but doesn't block
    the others. Transport rebuild happens once at the end."""
    anchor = f"device-{instance_id}"
    redirect_to = redirect(url_for("auth.settings_area", area="devices", _anchor=anchor))

    devices_registry = _devices()
    device = devices_registry.get(instance_id)
    if device is None or device.kind_of is None:
        flash(f"Unknown device {instance_id!r}.", "error")
        return redirect_to

    form = request.form
    settings_store = _settings()
    ok_messages: list[str] = []
    any_change = False

    # 1. Renderer-defined config fields. Mirror settings_update("device-<id>").
    schema_fields = _config_fields_from_schema(device.config_schema)
    if schema_fields and device.config_topic is not None:
        values = _values_from_form(schema_fields)
        ok, err = device.validate_config(values)
        if not ok:
            flash(f"Invalid {device.name} config: {err}", "error")
        else:
            settings_store.update_for_namespace("devices", instance_id, values, schema_fields)
            transport = _transport()
            try:
                transport.publish(
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
        renderers_registry = _renderers()
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
                    coerced[name] = _coerce_form_value(field, raw_values[name])
            if coerced:
                settings_store.update_for_namespace("renderers", clone_id, coerced, dev_fields)
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
            renderers=_renderers(),
            data_root=_device_data_root(),
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

    # 3. Quiet-hours override. The inputs always submit on the combined
    # form (toggle, start, end). Empty start+end with toggle off clears
    # the block; the service helper handles that.
    if "quiet_hours_enabled" in form or "quiet_hours_start" in form or "quiet_hours_end" in form:
        qh_result = device_service.update_instance_quiet_hours(
            devices=devices_registry,
            renderers=_renderers(),
            data_root=_device_data_root(),
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
        _rebuild_transport_fn()()
    if ok_messages:
        flash(f"{device.name}: {', '.join(ok_messages)}.", "ok")
    return redirect_to


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
    the app currently has loaded.

    Resolves the same way ``app.main._rebuild_transport`` does:
      * external broker → ``host``/``port``/creds from the broker section
      * built-in broker → loopback + ``embedded_port`` (and the embedded
        creds when they're set)

    Used to bail with "no host configured" whenever the built-in broker
    was enabled, because the user typically leaves the external ``host``
    field blank in that mode — which made the button useless for the
    most common single-machine setup."""
    raw = _settings().get_section("broker")
    host = str(raw.get("host") or "").strip()
    port = int(raw.get("port") or 1883)
    username = raw.get("username") or None
    password = raw.get("password_secret") or None
    embedded_enabled = bool(raw.get("embedded_enabled"))
    if not host:
        if not embedded_enabled:
            flash(
                "Broker test: no host configured and built-in broker is off.",
                "error",
            )
            return redirect(url_for("auth.settings_area", area="server"))
        # Mirror app.main's "connect to ourselves on loopback" logic: the
        # embedded bind may be 0.0.0.0 for clients on the LAN, but that's
        # not a connectable address — use 127.0.0.1.
        host = "127.0.0.1"
        port = int(raw.get("embedded_port") or 1883)
        embedded_user = str(raw.get("embedded_username") or "").strip() or None
        embedded_pass = raw.get("embedded_password_secret") or None
        if embedded_user and not username:
            username = embedded_user
            password = embedded_pass
    config = BrokerConfig(
        host=host,
        port=port,
        username=username,
        password=password,
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
        target = (
            f"built-in broker on {host}:{port}"
            if embedded_enabled and host == "127.0.0.1"
            else f"{host}:{config.port}"
        )
        flash(f"Broker test ok: connected to {target} and published.", "ok")
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
            "meta": {"Network IP": detect_local_ip()},
        }
    )

    broker_raw = settings_store.get_section("broker")
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

    enabled_map = settings_store.get_section("renderers_enabled")
    for renderer in _renderers().all():
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
                + "Picture-quality settings (dither / saturation / contrast) "
                + "are per-device — set them under Settings → Devices."
            ).strip()
        sections.append(
            {
                "id": sid,
                "kind": "renderer",
                "title": f"Renderer: {renderer.name}",
                "blurb": blurb,
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
        # Picture-quality (dither / saturation / contrast) lives on the
        # clone renderer keyed ``<base_id>__<device_id>`` — one clone
        # per renderer the device's kind consumes. Surface each clone's
        # device_setting-flagged fields as a "Picture quality" subsection;
        # the template renders them inside the combined form with the
        # name pattern ``<clone_id>:<field_name>`` so the save handler
        # can route each value back to the right clone's namespace.
        picture_quality: list[dict[str, Any]] = []
        if is_instance:
            for clone in _renderers().for_device(device.id):
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
                        "state": settings_store.get_for_runtime("renderers", clone.id, dev_fields),
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
                "state": (
                    settings_store.get_for_runtime("devices", device.id, fields) if fields else {}
                ),
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
                        devices=_devices(),
                        pages=current_app.config["PAGE_STORE"],
                        schedules=current_app.config["SCHEDULE_STORE"],
                    )
                    if is_instance
                    else []
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
    transport = current_app.config.get("MQTT_TRANSPORT")
    auto = getattr(transport, "client_id", "") or ""
    if not auto:
        return BROKER_FIELDS
    return [
        {**f, "placeholder": f"{auto} (auto)"} if f.get("name") == "client_id" else f
        for f in BROKER_FIELDS
    ]


def _apply_broker_change() -> None:
    """Hot-swap the MQTT transport on broker setting changes."""
    rebuild = current_app.config.get("REBUILD_TRANSPORT")
    if callable(rebuild):
        rebuild()


def register(app: Flask) -> None:
    app.register_blueprint(bp)
