"""First-run setup wizard.

A guided four-step flow that takes a fresh install from "password set" to
"a dashboard on a panel": welcome → broker → device → dashboard. It's a
thin orchestration layer — each step reuses the real services
(``device_service`` for instances, the push manager, the settings store),
so nothing here duplicates business logic.

The wizard is the landing page until ``app.onboarded`` is set (on Finish
or Skip). After that ``/`` goes to the normal Send page. Every step has a
Skip, so it guides without trapping anyone.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

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
    url_for,
)
from werkzeug.wrappers import Response

from app import device_service
from app.device_loader import DeviceRegistry
from app.discovery import DiscoveryCache
from app.layouts import LAYOUTS_BY_SLUG, to_panel_pixels
from app.network import detect_local_ip
from app.panel import (
    PANEL_PRESET_CHOICES,
    PANEL_PRESETS,
    panel_overrides_from_form,
    resolve_panel_for_page,
    resolve_settings_panel,
)
from app.push import PushManager
from app.state.page_store import Cell, Page, PageStore
from app.state.settings_store import SettingsStore

bp = Blueprint("onboarding", __name__, url_prefix="/onboarding")

STEPS: tuple[str, ...] = ("welcome", "broker", "device", "dashboard", "telemetry")
STEP_LABELS: dict[str, str] = {
    "welcome": "Welcome",
    "broker": "Broker",
    "device": "Device",
    "dashboard": "Dashboard",
    "telemetry": "Help out",
}
# Widget the starter dashboard drops into its single cell — a clock needs
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
    # device registered, or a dashboard created).
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
    if step == "broker":
        broker = _settings().get_section("broker")
        ctx["broker"] = broker
        port = int(broker.get("embedded_port") or 1883)
        ctx["builtin_port"] = port
        # Address clients point at when using the built-in broker. detect_
        # local_ip routes a UDP socket to find this host's LAN IP (no
        # packets sent); honours TESSERAE_HOST_IP.
        ctx["builtin_url"] = f"mqtt://{detect_local_ip()}:{port}"
    elif step == "device":
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


@bp.post("/broker")
def save_broker() -> Response:
    """Enable the built-in broker, or point the transport at an external
    one, then rebuild so the connection takes effect immediately."""
    form = request.form
    if form.get("use_builtin"):
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
        flash("That device is no longer announcing itself — add it by hand.", "error")
        return redirect(url_for("onboarding.step", step="device"))
    overrides: dict[str, Any] = {}
    if entry.panel_w is not None:
        overrides["w"] = entry.panel_w
    if entry.panel_h is not None:
        overrides["h"] = entry.panel_h
    result = device_service.create_instance(
        devices=_devices(),
        renderers=_renderers(),
        data_root=_data_root(),
        instance_id=discovered_id,
        kind_id=entry.kind,
        panel_overrides=overrides,
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
    flash("Starter dashboard created — push it to see it on your panel.", "ok")
    return redirect(url_for("onboarding.step", step="dashboard"))


@bp.post("/dashboard/<page_id>/push")
def push_starter(page_id: str) -> Response:
    result = _push().push(page_id)
    if result.status == "sent":
        flash("Pushed — your panel should paint shortly.", "ok")
    else:
        flash(f"Push {result.status}: {result.error or '(no detail)'}", "error")
    return redirect(url_for("onboarding.step", step="dashboard"))


@bp.post("/skip")
def skip() -> Response:
    mark_onboarded(_settings())
    flash("Setup skipped — you can configure everything under Settings.", "ok")
    return redirect(url_for("send.index"))


@bp.post("/finish")
def finish() -> Response:
    settings = _settings()
    # The telemetry consent comes from the last onboarding step. A
    # checkbox only POSTs its name when ticked, so absence means the
    # user explicitly unchecked it. Persist either choice so it survives
    # restarts and the answer is recorded the moment the user makes it.
    was_telemetry_on = bool(settings.get_section("app").get("telemetry_enabled", False))
    now_on = "telemetry_enabled" in request.form
    settings.patch_section("app", {"telemetry_enabled": now_on})
    mark_onboarded(settings)
    # Apply the consent live + fire app.started immediately so a fresh
    # install reports in without waiting for the next process restart.
    telemetry = current_app.config.get("TELEMETRY")
    if telemetry is not None and now_on != was_telemetry_on:
        telemetry.set_enabled(now_on)
        if now_on:
            telemetry.test_send()  # silent — onboarding shouldn't get loud
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
