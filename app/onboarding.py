"""First-run setup wizard.

A guided four-step flow that takes a fresh install from "password set" to
"a dashboard on a panel": welcome → broker → device → dashboard. It's a
thin orchestration layer, each step reuses the real services
(``device_service`` for instances, the push manager, the settings store),
so nothing here duplicates business logic.

The wizard is the landing page until ``app.onboarded`` is set (on Finish
or Skip). After that ``/`` goes to the normal Send page. Every step has a
Skip, so it guides without trapping anyone.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from flask import (
    Blueprint,
    Flask,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.wrappers import Response

from app import device_service
from app.device_loader import DeviceRegistry
from app.discovery import DiscoveryCache
from app.layouts import LAYOUTS_BY_SLUG, to_panel_pixels
from app.network import detect_local_ip, docker_bridge_ip_warning
from app.panel import (
    PANEL_PRESET_CHOICES,
    PANEL_PRESETS,
    panel_overrides_from_form,
    resolve_panel_for_page,
    resolve_settings_panel,
)
from app.push import PushManager
from app.settings.field_defs import _TZ_CHOICES
from app.state.page_store import Cell, Page, PageStore
from app.state.settings_store import SettingsStore
from app.tz_resolve import _resolve_iana_timezone

bp = Blueprint("onboarding", __name__, url_prefix="/onboarding")

STEPS: tuple[str, ...] = ("welcome", "timezone", "broker", "device", "share", "dashboard")
STEP_LABELS: dict[str, str] = {
    "welcome": "Welcome",
    # The online-features opt-in. Placed just before the dashboard step so the
    # consent screen always lands inside the linear wizard: the dashboard step
    # hands off to the editor (Edit / freeform), which would otherwise let
    # someone leave the flow before this was ever shown. Off unless the user
    # says yes here.
    "share": "Help out",
    # Timezone slots in right after welcome because it's foundational:
    # the scheduler interprets every fire time against it. Surfaced
    # during onboarding so users on Docker / bare metal explicitly
    # pick instead of inheriting whatever the host happens to be
    # (often UTC).
    "timezone": "Timezone",
    # The route step id is still "broker" for URL stability (see
    # save_broker), but the user-facing label now reflects the v0.52.2
    # reframe to a transport choice. REST users don't touch a broker
    # at all.
    "broker": "Transport",
    "device": "Device",
    "dashboard": "Dashboard",
}
# Widget the starter dashboard drops into its single cell, a clock needs
# no API keys or config, so it paints on the very first push.
STARTER_PLUGIN = "clock_analog"


# -- app.config accessors ----------------------------------------------


def _settings() -> SettingsStore:
    return current_app.config["SETTINGS_STORE"]  # type: ignore[no-any-return]


def _devices() -> DeviceRegistry:
    return current_app.config["DEVICE_REGISTRY"]  # type: ignore[no-any-return]


def _renderers() -> Any:
    return current_app.config["RENDERER_REGISTRY"]


def _discovery() -> DiscoveryCache:
    return current_app.config["DISCOVERY_CACHE"]  # type: ignore[no-any-return]


def _pages() -> PageStore:
    return current_app.config["PAGE_STORE"]  # type: ignore[no-any-return]


def _push() -> PushManager:
    return current_app.config["PUSH_MANAGER"]  # type: ignore[no-any-return]


def _data_root() -> Any:
    return current_app.config["DEVICE_DATA_ROOT"]


def _rebuild_transport() -> None:
    current_app.config["REBUILD_TRANSPORT"]()


# -- onboarded flag -----------------------------------------------------


def is_onboarded(settings: SettingsStore) -> bool:
    if settings.get_section("app").get("onboarded"):
        return True
    # Treat an already-configured install as onboarded so the wizard never
    # ambushes someone who set things up before it existed (broker set, a
    # device registered, dashboard created, OR the user picked REST via
    # the v0.52 transport-choice screen).
    app_section = settings.get_section("app")
    if app_section.get("default_transport"):
        # default_transport persisted = user has been through the v0.52
        # transport step at least once, treat the install as onboarded.
        return True
    broker = settings.get_section("broker")
    if broker.get("host") or broker.get("embedded_enabled"):
        return True
    try:
        if any(d.kind_of is not None for d in current_app.config["DEVICE_REGISTRY"].all()):
            return True
        if current_app.config["PAGE_STORE"].list():
            return True
    except (KeyError, RuntimeError):
        pass
    return False


def mark_onboarded(settings: SettingsStore) -> None:
    settings.patch_section("app", {"onboarded": True})


# -- step completion (for the progress bar + smart resume) --------------


def _broker_done() -> bool:
    """The transport step (still called 'broker' for URL stability) is
    considered done in three cases:
    * The user picked the REST transport (no broker setup needed).
    * The user set an external broker host.
    * The user enabled the built-in broker.
    """
    app_section = _settings().get_section("app")
    if app_section.get("default_transport") == "rest":
        return True
    broker = _settings().get_section("broker")
    return bool(broker.get("host")) or bool(broker.get("embedded_enabled"))


def _device_done() -> bool:
    return any(d.kind_of is not None for d in _devices().all())


def _dashboard_done() -> bool:
    return len(_pages().list()) > 0


_DONE = {
    "broker": _broker_done,
    "device": _device_done,
    "dashboard": _dashboard_done,
}


def _progress(current: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in STEPS:
        done = _DONE[key]() if key in _DONE else False
        out.append({"key": key, "label": STEP_LABELS[key], "done": done, "current": key == current})
    return out


# -- routes -------------------------------------------------------------


@bp.get("")
def index() -> Response:
    return redirect(url_for("onboarding.step", step="welcome"))


@bp.get("/<step>")
def step(step: str) -> Response | str:
    if step not in STEPS:
        return redirect(url_for("onboarding.step", step="welcome"))
    ctx: dict[str, Any] = {
        "step": step,
        "steps": _progress(step),
        "step_index": STEPS.index(step),
        "step_count": len(STEPS),
    }
    if step == "timezone":
        app_section = _settings().get_section("app")
        stored = str(app_section.get("timezone") or "system")
        # Auto-detect from the host so the picker lands pre-populated
        # with a sensible default. The user can keep it (one click) or
        # change it. ``_resolve_iana_timezone`` returns "" when nothing
        # resolves — we fall back to "UTC" so the picker still has a
        # value selected (better than blank).
        detected = _resolve_iana_timezone("system") or "UTC"
        ctx["timezone_stored"] = stored
        ctx["timezone_detected"] = detected
        ctx["timezone_choices"] = _TZ_CHOICES
        # ``selected_value`` is what the picker pre-selects. Prefer the
        # user's existing settings.app.timezone if they're revisiting
        # the step (e.g. clicked Back) so we don't blow away an
        # explicit earlier choice.
        if stored and stored.lower() != "system":
            ctx["timezone_selected"] = stored
        else:
            ctx["timezone_selected"] = detected
    elif step == "broker":
        broker = _settings().get_section("broker")
        ctx["broker"] = broker
        # v0.52 transport-choice radio. Default to REST for fresh
        # installs (no broker setup needed); preserve the user's
        # earlier choice if they're revisiting the step.
        app_section = _settings().get_section("app")
        ctx["default_transport"] = str(app_section.get("default_transport") or "rest")
        port = int(broker.get("embedded_port") or 1883)
        ctx["builtin_port"] = port
        # Address clients point at when using the built-in broker. detect_
        # local_ip routes a UDP socket to find this host's LAN IP (no
        # packets sent); honours TESSERAE_HOST_IP.
        ctx["builtin_url"] = f"mqtt://{detect_local_ip()}:{port}"
        # Under the Docker image with bridge networking, ``detect_local_ip``
        # returns the container's bridge address (172.18.0.x or similar)
        # , useless for LAN panels. Flag it so the wizard can show a hint
        # about ``TESSERAE_HOST_IP`` / host networking.
        ctx["builtin_url_is_bridge"] = docker_bridge_ip_warning()
        # Hide the built-in broker option under HA. The bundled Mosquitto
        # add-on already owns port 1883 on the host; offering a second
        # broker there just confuses users about which devices connect
        # where. The template uses this to suppress the "use built-in"
        # card and pre-fill the external host with core-mosquitto.
        ctx["ha_ingress"] = bool(current_app.config.get("HA_INGRESS_MODE"))
        if ctx["ha_ingress"] and not broker.get("host"):
            ctx["broker"] = {**broker, "host": "core-mosquitto", "port": 1883}
    elif step == "device":
        app_section = _settings().get_section("app")
        ctx["default_transport"] = str(app_section.get("default_transport") or "rest")
        # When the user picked REST on the previous step, offer to
        # issue a pairing code right here so a freshly-flashed firmware
        # can register against this server without the user backtracking
        # to Settings -> Devices.
        if ctx["default_transport"] == "rest":
            store = current_app.config.get("PAIRING_STORE")
            if store is not None:
                ctx["pairing_codes"] = [
                    {
                        "code": p.code,
                        "expires_at": p.expires_at,
                        "note": p.note,
                        "seconds_left": max(0, int(p.expires_at - time.time())),
                    }
                    for p in store.list_pending()
                ]
            else:
                ctx["pairing_codes"] = []
            ctx["pairing_reveal"] = session.pop("_rest_pairing_reveal", None)
        # ``panel`` rides along on each kind so the form's preset
        # selector can auto-sync to the kind's declared size when the
        # user picks a kind, and so the resulting w/h match Settings →
        # Devices' panel UI exactly.
        ctx["device_kinds"] = [
            {"id": d.id, "name": d.name, "panel": d.panel}
            for d in _devices().all()
            if d.kind_of is None
        ]
        ctx["instances"] = [
            {"id": d.id, "name": d.name} for d in _devices().all() if d.kind_of is not None
        ]
        ctx["discovered"] = [
            {"id": e.id, "kind": e.kind, "panel_w": e.panel_w, "panel_h": e.panel_h}
            for e in _discovery().all()
            if e.id not in _devices().devices
        ]
        # Baseline signature for the client-side poller (auto-refresh).
        ctx["discovered_sig"] = ",".join(
            sorted(f"{d['id']}:{d['kind'] or ''}" for d in ctx["discovered"])
        )
        # Panel-preset chrome (mirrors Settings → Devices' add form).
        ctx["panel_preset_choices"] = PANEL_PRESET_CHOICES
        ctx["panel_presets"] = PANEL_PRESETS
    elif step == "dashboard":
        ctx["pages"] = [{"id": p.id, "name": p.name} for p in _pages().list()]
    return render_template("onboarding.html", **ctx)


@bp.post("/timezone")
def save_timezone() -> Response:
    """Persist the picked timezone to ``settings.app.timezone`` and
    advance to the broker step.

    Validates against ``zoneinfo.available_timezones()`` so a hand-
    typed bogus value (or a stale ``_TZ_CHOICES`` cache) can't slip
    a non-IANA string into settings. Unknown values fall through to
    ``"system"`` (host auto-detect at scheduler-time) which is the
    least-surprising default.
    """
    import zoneinfo

    raw = (request.form.get("timezone") or "").strip()
    if raw and raw != "system" and raw not in zoneinfo.available_timezones():
        flash(f"Unknown timezone {raw!r}; falling back to system.", "warn")
        raw = "system"
    _settings().patch_section("app", {"timezone": raw or "system"})

    flash("Timezone saved.", "ok")
    return redirect(url_for("onboarding.step", step="broker"))


@bp.post("/broker")
def save_broker() -> Response:
    """Enable the built-in broker, or point the transport at an external
    one, then rebuild so the connection takes effect immediately.

    v0.52 added a transport-choice radio above the broker form. When the
    user picks ``rest`` (the default for new installs), the broker setup
    is skipped entirely: persist ``app.default_transport = "rest"`` and
    advance to the device step. The broker section stays unconfigured
    until the user explicitly opts back into MQTT later."""
    form = request.form

    # Transport choice. Defaults to ``rest`` for fresh installs so the
    # new-user path skips broker setup altogether.
    chosen_transport = (form.get("transport") or "rest").strip().lower()
    if chosen_transport not in ("rest", "mqtt"):
        chosen_transport = "rest"
    _settings().patch_section("app", {"default_transport": chosen_transport})

    if chosen_transport == "rest":
        # No broker needed. Devices pair via /api/v1/device/register and
        # poll /api/v1/device/<id>/frame. Skip the broker-rebuild
        # entirely so an unconfigured broker doesn't churn the
        # transport thread.
        flash("Transport: REST. No broker needed; pair devices on the next step.", "ok")
        return redirect(url_for("onboarding.step", step="device"))

    # Under HA the built-in broker is intentionally not offered (see the
    # broker step template + transport_wiring.HA_INGRESS_MODE guard).
    # Treat any stale ``use_builtin`` post as "external broker" so a
    # cached page can't sneak it back in.
    ha_ingress = bool(current_app.config.get("HA_INGRESS_MODE"))
    if form.get("use_builtin") and not ha_ingress:
        # Bind all interfaces so LAN clients (Pi / ESP32) can reach it;
        # the transport self-connects over loopback (see _rebuild_transport).
        patch: dict[str, Any] = {
            "embedded_enabled": True,
            "host": "",
            "embedded_port": 1883,
            "embedded_bind": "0.0.0.0",
            "embedded_username": (form.get("builtin_username") or "").strip(),
        }
        bpw = form.get("builtin_password") or ""
        if bpw:
            patch["embedded_password_secret"] = bpw
        _settings().patch_section("broker", patch)
    else:
        host = (form.get("host") or "").strip()
        if not host:
            flash("Enter a broker host, or choose the built-in broker.", "error")
            return redirect(url_for("onboarding.step", step="broker"))
        patch = {
            "embedded_enabled": False,
            "host": host,
            "port": _int(form.get("port"), 1883),
            "username": (form.get("username") or "").strip(),
        }
        pw = form.get("password") or ""
        if pw:
            patch["password_secret"] = pw
        _settings().patch_section("broker", patch)
    _rebuild_transport()
    flash("Broker saved.", "ok")
    return redirect(url_for("onboarding.step", step="device"))


@bp.post("/device")
def add_device() -> Response:
    """Register a device instance by hand (the manual fallback when
    nothing has been auto-discovered yet)."""
    form = request.form
    result = device_service.create_instance(
        devices=_devices(),
        renderers=_renderers(),
        data_root=_data_root(),
        instance_id=form.get("id") or "",
        kind_id=(form.get("kind") or "").strip(),
        name=form.get("name") or "",
        panel_overrides=panel_overrides_from_form(form),
        orientation=form.get("panel_orientation"),
    )
    if not result.ok or result.device is None:
        flash(result.error or "Could not add device.", "error")
    else:
        _rebuild_transport()
        flash(f"Added {result.device.name!r}.", "ok")
    return redirect(url_for("onboarding.step", step="device"))


@bp.post("/device/<discovered_id>/register")
def register_discovered(discovered_id: str) -> Response:
    """One-click register a device the broker already heard from."""
    entry = _discovery().get(discovered_id)
    if entry is None or not entry.kind:
        flash("That device is no longer announcing itself, add it by hand.", "error")
        return redirect(url_for("onboarding.step", step="device"))
    # Parity with app.settings.devices_routes.devices_register_discovered
    # (issue #82). An explicit variant pick is authoritative over the
    # heartbeat's dims / gamut (which we can't trust to identify the
    # panel); without one, honour the declared dims + gamut.
    explicit_kind = (request.form.get("kind") or "").strip()
    kind_id = explicit_kind or entry.kind
    user_picked_variant = bool(explicit_kind) and explicit_kind != (entry.kind or "")
    # Guard against firmware reporting panel dims as zero; mirrors the
    # check in app.settings.devices_routes.devices_register_discovered.
    overrides: dict[str, Any] = {}
    if not user_picked_variant:
        if entry.panel_w is not None and entry.panel_w > 0:
            overrides["w"] = entry.panel_w
        if entry.panel_h is not None and entry.panel_h > 0:
            overrides["h"] = entry.panel_h
        if entry.gamut:
            from app.quantizer import canonicalise_gamut

            overrides["gamut"] = canonicalise_gamut(entry.gamut)
    # Same geometry resolution as the Settings → Devices register button
    # (issue #200): keep the announce's dims and derive an orientation that
    # agrees with them, rather than letting the kind's default orientation
    # rewrite them on the next save.
    geometry, reported_orientation = device_service.panel_geometry_from_report(
        w=overrides.get("w"), h=overrides.get("h"), rotation=entry.rotation
    )
    overrides.update(geometry)
    renderers = _renderers()
    kind = _devices().get(kind_id)
    renderer_id = device_service.renderer_id_for_format(renderers, kind, entry.wire_format)
    result = device_service.create_instance(
        devices=_devices(),
        renderers=renderers,
        data_root=_data_root(),
        instance_id=discovered_id,
        kind_id=kind_id,
        panel_overrides=overrides,
        orientation=reported_orientation,
        renderer_id=renderer_id,
    )
    if not result.ok or result.device is None:
        flash(result.error or "Could not register device.", "error")
    else:
        _discovery().forget(discovered_id)
        _rebuild_transport()
        flash(f"Registered {result.device.name!r}.", "ok")
    return redirect(url_for("onboarding.step", step="device"))


@bp.post("/dashboard")
def create_starter() -> Response:
    """Build a ready-to-paint starter dashboard (a single clock cell),
    bound to the first registered device so it sizes to that panel."""
    page = _build_starter_page(_pages(), _settings(), _devices())
    _pages().save(page)
    flash("Starter dashboard created, push it to see it on your panel.", "ok")
    return redirect(url_for("onboarding.step", step="dashboard"))


@bp.post("/dashboard/<page_id>/push")
def push_starter(page_id: str) -> Response:
    result = _push().push(page_id)
    if result.status == "sent":
        flash("Pushed, your panel should paint shortly.", "ok")
    else:
        flash(f"Push {result.status}: {result.error or '(no detail)'}", "error")
    return redirect(url_for("onboarding.step", step="dashboard"))


@bp.post("/share")
def save_share() -> Response:
    """Record the online-features opt-in choice, then advance to the final
    dashboard step.

    Yes turns on update checks, firmware indicators, marketplace install
    counts, and the anonymous heartbeat; No keeps the install fully offline.
    Changeable later in Settings -> System. Setup itself is finished on the
    dashboard step, so this only records the choice and moves on."""
    enabled = request.form.get("online_features") in ("1", "true", "on")
    _settings().patch_section("app", {"online_features": enabled})
    if enabled:
        flash("Thank you, genuinely. You're now one of the installs I get to build for.", "ok")
    else:
        flash(
            "No problem at all. Tesserae will not contact api.tesserae.ink; "
            "turn it on anytime in Settings -> System -> Online features.",
            "ok",
        )
    return redirect(url_for("onboarding.step", step="dashboard"))


@bp.post("/skip")
def skip() -> Response:
    mark_onboarded(_settings())
    flash("Setup skipped, you can configure everything under Settings.", "ok")
    return redirect(url_for("send.index"))


@bp.post("/finish")
def finish() -> Response:
    settings = _settings()
    mark_onboarded(settings)
    flash("You're all set.", "ok")
    return redirect(url_for("send.index"))


# -- helpers ------------------------------------------------------------


def _int(raw: str | None, default: int) -> int:
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _build_starter_page(pages: PageStore, settings: SettingsStore, devices: DeviceRegistry) -> Page:
    """A one-cell clock dashboard bound to the first registered instance
    (so it inherits that device's panel size + routing). Falls back to the
    virtual panel when no device is registered yet."""
    instance = next((d for d in devices.all() if d.kind_of is not None), None)
    device_id = instance.id if instance is not None else None

    taken = {p.id for p in pages.list()}
    page_id = "welcome"
    n = 2
    while page_id in taken:
        page_id = f"welcome-{n}"
        n += 1

    page = Page(
        id=page_id,
        name="Welcome",
        device_ids=[device_id] if device_id else [],
        cells=[],
    )
    panel = (
        resolve_panel_for_page(page, devices, settings)
        if device_id
        else resolve_settings_panel(settings)
    )
    layout = LAYOUTS_BY_SLUG["1_cell"]
    cells = [
        Cell(id=uuid.uuid4().hex[:8], plugin=STARTER_PLUGIN, x=x, y=y, w=w, h=h)
        for x, y, w, h in to_panel_pixels(layout, panel.w, panel.h)
    ]
    return page.model_copy(update={"cells": cells})


def register(app: Flask) -> None:
    app.register_blueprint(bp)
