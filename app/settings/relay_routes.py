"""Settings -> Cloud relay: link the install and pair remote panels.

The remote-panel feature (docs/install/remote-panel.md) needs three operator
actions: register this install with a relay, add a remote panel (which mints a
pairing code to enter on the panel), and revoke a panel. The heavy lifting lives
in app/relay_pairing.py + app/relay_client.py; these routes are thin wrappers
that flash the outcome and redirect back to the page.
"""

from __future__ import annotations

from typing import Any

from flask import current_app, flash, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from app import device_service
from app.relay_client import RelayError
from app.relay_config import DEFAULT_BASE_URL, base_url, build_client, is_linked, relay_config
from app.relay_pairing import mint_remote_panel_code, register_this_install

from ._shared import bp, devices, renderers, settings_store


def _relay_devices() -> list[Any]:
    return [d for d in devices().devices.values() if d.transport == "relay"]


def _panel_kinds() -> list[Any]:
    """Device *kinds* (not instances) a remote panel can be created from."""
    return [d for d in devices().devices.values() if d.kind_of is None]


@bp.get("/settings/relay")
def relay_index() -> str:
    cfg = relay_config(settings_store())
    return render_template(
        "settings_relay.html",
        active="relay",
        linked=is_linked(cfg),
        base_url=base_url(cfg),
        default_base_url=DEFAULT_BASE_URL,
        allow_local=bool(cfg.get("allow_local")),
        install_id=cfg.get("install_id") or "",
        relay_devices=_relay_devices(),
        panel_kinds=_panel_kinds(),
        # A freshly minted pairing code is passed through the redirect so the
        # operator can read it off the page (it isn't stored server-side).
        new_code=request.args.get("code", ""),
        new_code_device=request.args.get("device", ""),
    )


@bp.post("/settings/relay/register")
def relay_register() -> Response:
    base = (request.form.get("base_url") or "").strip() or DEFAULT_BASE_URL
    allow_local = request.form.get("allow_local") == "1"
    try:
        register_this_install(settings_store(), base=base, allow_local=allow_local)
        flash("Install registered with the relay.", "ok")
    except RelayError as exc:
        flash(f"Couldn't register with the relay: {exc}", "error")
    return redirect(url_for("auth.relay_index"))


@bp.post("/settings/relay/add-panel")
def relay_add_panel() -> Response:
    device_id = (request.form.get("device_id") or "").strip().lower()
    kind = (request.form.get("kind") or "").strip()
    name = (request.form.get("name") or "").strip()
    panel: dict[str, Any] = {}
    try:
        if request.form.get("panel_w") and request.form.get("panel_h"):
            panel = {"w": int(request.form["panel_w"]), "h": int(request.form["panel_h"])}
    except ValueError:
        flash("Panel width/height must be numbers.", "error")
        return redirect(url_for("auth.relay_index"))
    if not device_id or not kind:
        flash("Pick a device id and kind for the remote panel.", "error")
        return redirect(url_for("auth.relay_index"))
    try:
        code, _expires = mint_remote_panel_code(
            settings_store(), device_id=device_id, kind=kind, name=name, panel=panel or None
        )
    except RelayError as exc:
        flash(f"Couldn't create a pairing code: {exc}", "error")
        return redirect(url_for("auth.relay_index"))
    return redirect(url_for("auth.relay_index", code=code, device=device_id))


@bp.post("/settings/relay/revoke")
def relay_revoke() -> Response:
    device_id = (request.form.get("device_id") or "").strip().lower()
    if not device_id:
        flash("No device given.", "error")
        return redirect(url_for("auth.relay_index"))
    # Drop the relay mailbox first so the panel stops receiving frames, then
    # delete the local instance. A relay error is non-fatal: still remove locally.
    client = build_client(relay_config(settings_store()))
    if client is not None:
        try:
            client.revoke_device(device_id)
        except RelayError as exc:
            current_app.logger.warning("relay revoke %s: %s", device_id, exc)
    device_service.delete_instance(
        devices=devices(),
        renderers=renderers(),
        instance_id=device_id,
    )
    flash(f"Removed remote panel {device_id}.", "ok")
    return redirect(url_for("auth.relay_index"))
