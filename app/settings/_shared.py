"""Shared blueprint object + helpers used across the split settings
routes.

The ``auth`` blueprint is constructed exactly once here and re-imported
by every route module, keeping the blueprint name stable means every
``url_for("auth.xxx")`` reference across the templates keeps working
after the split. ``register()`` (in ``app.settings.__init__``) imports
each route module to trigger its decorators, then registers ``bp``
with the Flask app.

Everything in this module is module-private to ``app.settings`` -
external callers should use the symbols re-exported from
``app.settings_routes`` instead so the split stays an implementation
detail.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, flash, redirect, request, url_for
from werkzeug.wrappers import Response

from app import updater as _updater_mod
from app.device_loader import Device, DeviceRegistry
from app.discovery import DiscoveredDevice, DiscoveryCache
from app.plugin_loader import PluginRegistry
from app.push import PushManager
from app.renderer_loader import RendererRegistry
from app.state.event_log import EventLog
from app.state.settings_store import SettingsStore
from app.transport import MqttTransport

# How fresh a heartbeat has to be to read as "ok" in the UI. Past this it
# decays through "warn" (2x) into "stale". Tuned for the typical Pi client
# 60s heartbeat, esp32_client wakes far less often but the broker-retained
# last value is still informative.
STATUS_FRESH_S: int = 90
STATUS_WARN_S: int = 5 * 60

# Blueprint name is "auth" for backward compatibility, 71+ templates
# reference url_for("auth.xxx"). Renaming would be a mass rename across
# every settings / onboarding / devices template; the split keeps the
# endpoint surface stable.
bp = Blueprint("auth", __name__)


# Sub-page taxonomy. The 'app' and 'broker' sections live together under
# 'server' because they're both server-config; renderers / devices /
# plugins each get their own page since their lists grow independently.
AREAS: tuple[tuple[str, str], ...] = (
    ("server", "Server"),
    ("renderers", "Renderers"),
    ("devices", "Devices"),
    ("plugins", "Plugins"),
    ("system", "System"),
)

# Map area → section kinds that belong on that page. The "system" page
# is hand-built (updates + backups), no manifest-driven sections.
# Same for "about", which renders fixed cards (project meta, community /
# sponsor) and never iterates the manifest sections list.
AREA_KINDS: dict[str, set[str]] = {
    "server": {"app", "panel", "broker"},
    "renderers": {"renderer"},
    "devices": {"device"},
    "plugins": {"plugin"},
    "system": set(),
    "about": set(),
}


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


# -- registry accessors -------------------------------------------------
# Each thin wrapper exists so the route modules can stay decoupled from
# the Flask config-dict key names, change a key in one place if it ever
# needs to move.


def settings_store() -> SettingsStore:
    return current_app.config["SETTINGS_STORE"]  # type: ignore[no-any-return]


def plugins() -> PluginRegistry:
    return current_app.config["PLUGIN_REGISTRY"]  # type: ignore[no-any-return]


def renderers() -> RendererRegistry:
    return current_app.config["RENDERER_REGISTRY"]  # type: ignore[no-any-return]


def devices() -> DeviceRegistry:
    return current_app.config["DEVICE_REGISTRY"]  # type: ignore[no-any-return]


def device_status() -> dict[str, dict[str, Any]]:
    return current_app.config["DEVICE_STATUS"]  # type: ignore[no-any-return]


def transport() -> MqttTransport:
    return current_app.config["MQTT_TRANSPORT"]  # type: ignore[no-any-return]


def push_manager() -> PushManager:
    return current_app.config["PUSH_MANAGER"]  # type: ignore[no-any-return]


def events() -> EventLog:
    return current_app.config["EVENT_LOG"]  # type: ignore[no-any-return]


def updater() -> _updater_mod.Updater:
    return current_app.config["UPDATER"]  # type: ignore[no-any-return]


def data_root() -> Path:
    return current_app.config["DATA_ROOT"]  # type: ignore[no-any-return]


def device_data_root() -> Path:
    return current_app.config["DEVICE_DATA_ROOT"]  # type: ignore[no-any-return]


def rebuild_transport_fn() -> Callable[[], None]:
    return current_app.config["REBUILD_TRANSPORT"]  # type: ignore[no-any-return]


def discovery_cache() -> DiscoveryCache:
    return current_app.config["DISCOVERY_CACHE"]  # type: ignore[no-any-return]


def device_kinds() -> list[Device]:
    """Built-in device kinds (the manifests under ``devices/``)."""
    return [d for d in devices().all() if d.kind_of is None]


def _kind_protocol(kind: Device) -> str:
    """The wire protocol a kind speaks. Canonical rule lives in
    :func:`app.device_service.kind_protocol` (shared with the
    register-time kind auto-heal); thin alias for existing callers."""
    from app.device_service import kind_protocol

    return kind_protocol(kind)


def variant_options(kind_id: str | None, declared_gamut: str | None) -> list[dict[str, Any]]:
    """Sibling kinds that share ``kind_id``'s wire protocol, for the
    discovered-device variant picker (issue #82).

    Two 4" Pimoroni Inky panels illustrate the need: the current Spectra 6
    (``pimoroni_inky_4``, 600x400) and the legacy 7-colour ACeP
    (``pimoroni_inky_4_acep``, 640x400) share the pi_bin protocol but
    differ in gamut and dims. A pi_bin client can't tell Tesserae which
    variant it is, so the legacy panel registers as Spectra 6 with the
    wrong resolution.

    Returns [] when the kind is unknown or is the only kind on its
    protocol (the template falls back to the bare Register button).

    When the heartbeat DID declare a gamut, the sibling whose panel
    matches it is marked ``selected`` so one-click register lands on the
    right variant even before the picker is touched, the auto path that
    kicks in once the client firmware learns to declare its gamut."""
    if not kind_id:
        return []
    base = devices().get(kind_id)
    if base is None or base.kind_of is not None:
        return []
    protocol = _kind_protocol(base)
    siblings = [k for k in device_kinds() if _kind_protocol(k) == protocol]
    if len(siblings) < 2:
        return []

    from app.quantizer import canonicalise_gamut

    want_gamut = (
        canonicalise_gamut(declared_gamut.strip())
        if isinstance(declared_gamut, str) and declared_gamut.strip()
        else None
    )
    options: list[dict[str, Any]] = []
    matched = False
    for k in siblings:
        panel = k.panel or {}
        raw_gamut = panel.get("gamut")
        canon = (
            canonicalise_gamut(str(raw_gamut)) if isinstance(raw_gamut, str) and raw_gamut else None
        )
        select_by_gamut = want_gamut is not None and canon == want_gamut and not matched
        matched = matched or select_by_gamut
        options.append(
            {
                "id": k.id,
                "name": k.name,
                "w": panel.get("w"),
                "h": panel.get("h"),
                "gamut": canon,
                "selected": select_by_gamut,
            }
        )
    # No gamut match (or none declared): default the select to the kind
    # the heartbeat actually named.
    if not matched:
        for opt in options:
            opt["selected"] = opt["id"] == kind_id
    options.sort(key=lambda o: (not o["selected"], str(o["name"])))
    return options


# -- form / field helpers -----------------------------------------------


def log_auth(action: str, status: str, error: str | None = None) -> None:
    """Record an auth event. Target is always 'session' since we have one
    shared admin login, no per-user concept."""
    events().record(
        type="auth",
        source=action,
        target="session",
        status=status,
        error=error,
        extra={"remote_addr": request.remote_addr or "(unknown)"},
    )


def config_fields_from_schema(schema: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
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


def safe_next(target: str | None) -> str:
    """Bound the post-login redirect to in-app paths so we can't be used as
    an open redirector. Anything not starting with ``/`` (or starting with
    ``//`` which would be a protocol-relative URL) falls back to /settings."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return url_for("auth.settings")
    return target


def coerce_form_value(field: dict[str, Any], raw: str | None) -> Any:
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
    if ftype == "location_search":
        # v0.69.6 (issue #52 items 5 + 6): the location picker submits a
        # JSON-encoded dict in a hidden input. Parse it into a real dict
        # here so the SettingsStore round-trips a proper structure, not
        # a stringified blob. Blank / malformed input clears the picker.
        if raw is None or raw == "":
            return {}
        import contextlib
        import json

        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        out_loc: dict[str, Any] = {}
        for key in ("name", "country", "admin1"):
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                out_loc[key] = val.strip()
        for key in ("latitude", "longitude"):
            val = parsed.get(key)
            if val is None:
                continue
            with contextlib.suppress(TypeError, ValueError):
                out_loc[key] = float(val)
        return out_loc
    return raw if raw is not None else ""


def values_from_form(fields: list[dict[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in fields:
        name = str(field["name"])
        if field.get("type") in ("boolean", "switch"):
            # Unchecked checkboxes are absent from the form, present ones
            # send "on", bare presence is what we use.
            values[name] = field["name"] in request.form
        else:
            values[name] = coerce_form_value(field, request.form.get(name))
    return values


def render_for_admin(namespace: str, item_id: str, fields: list[dict[str, Any]]) -> dict[str, Any]:
    return settings_store().get_for_admin(namespace, item_id, fields)


# -- redirect helpers ---------------------------------------------------


def area_for_section_kind(section_kind: str) -> str:
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


def redirect_to_section(section_kind: str) -> Response:
    return redirect(
        url_for(
            "auth.settings_area",
            area=area_for_section_kind(section_kind),
            _anchor=section_kind,
        )
    )


def system_redirect() -> Response:
    return redirect(url_for("auth.settings_area", area="system"))


def refuse_in_dev() -> Response | None:
    if current_app.debug:
        flash(
            "Updates and restores only run on the production (waitress) server, "
            "the --dev reloader owns restarts.",
            "error",
        )
        return system_redirect()
    return None


def refuse_in_container() -> Response | None:
    """Refuse in-app self-updates when running inside the official Docker
    image. A ``git pull`` against a layered filesystem would lose the next
    image rebuild, so users upgrade via ``docker compose pull`` (or by
    bumping the HA add-on) instead. The Settings → System tab shows that
    hint when ``TESSERAE_IN_DOCKER=1`` is set. Gated server-side too so a
    hand-crafted POST can't sneak through.

    Note: backup restore and data import are *not* gated by this; they
    only touch the persistent ``data/`` volume (which survives container
    upgrades) and the post-restore ``os.execv`` cleanly replaces the
    container's PID 1 in place. Only the code-tree-mutating routes
    (``update/apply``, ``update/rollback``) need the docker refusal."""
    if os.environ.get("TESSERAE_IN_DOCKER"):
        flash(
            "Updates aren't supported in the Docker image, "
            "use `docker compose pull && docker compose up -d` to upgrade.",
            "error",
        )
        return system_redirect()
    return None


# -- display helpers ----------------------------------------------------


def orientation_label(orientation: str) -> str:
    return _ORIENTATION_DEGREES.get(orientation, orientation)


def format_relative(seconds: float) -> str:
    if seconds < 5:
        return "just now"
    if seconds < 60:
        return f"{int(seconds)} s ago"
    if seconds < 3600:
        return f"{int(seconds / 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)} h ago"
    return f"{int(seconds / 86400)} d ago"


def format_discovered(items: list[DiscoveredDevice]) -> list[dict[str, Any]]:
    """Shape DiscoveredDevice records for the template, flatten to plain
    dicts so Jinja doesn't trip over @property access, and pre-compute
    the relative-time string."""
    now = time.time()
    out: list[dict[str, Any]] = []
    for d in items:
        out.append(
            {
                "id": d.id,
                "kind": d.kind,
                "name": d.name,
                "panel_w": d.panel_w,
                "panel_h": d.panel_h,
                "gamut": d.gamut,
                "fw_version": d.fw_version,
                "ip": d.ip,
                "relative": format_relative(max(0.0, now - d.received_at)),
                "parsed": d.parsed,
                "variants": variant_options(d.kind, d.gamut),
            }
        )
    return out
