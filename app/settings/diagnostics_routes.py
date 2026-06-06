"""Settings → diagnostics: one-shot tests that verify config without
touching the running transport.

* ``test_broker``, opens a fresh MQTT connection with the currently-
  saved broker settings and publishes a no-op probe. Mirrors the
  built-in-vs-external resolution logic in ``app.main._rebuild_transport``
  so the button works the same whether the user runs the built-in
  broker or points at Mosquitto.
* ``test_push``, generates a small synthetic PNG and runs it through
  ``PushManager.push_image``, exercising every loaded renderer end-to-
  end + the event-log path without needing a saved dashboard.
"""

from __future__ import annotations

import contextlib
import io

from flask import flash, redirect, url_for
from PIL import Image, ImageDraw
from werkzeug.wrappers import Response

from app.transport import BrokerConfig, MqttTransport

from ._shared import bp, push_manager, settings_store


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
    field blank in that mode, which made the button useless for the
    most common single-machine setup."""
    raw = settings_store().get_section("broker")
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
        # not a connectable address, use 127.0.0.1.
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
    panel = settings_store().get_section("app")
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
    result = push_manager().push_image(buf.getvalue(), source_label="diagnostics_test")
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
