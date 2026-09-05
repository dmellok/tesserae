"""Build the :class:`app.ha_discovery.HaHooks` bag from a running app.

The discovery module only knows the transport, the push manager and the
page store. Everything else it exposes to Home Assistant (lineup
switches, the automation pause, quiet-hours toggles, wake interval,
firmware update, text notifications) needs app internals that live in
``app.config``. This module is the one place that knows where those
live, so ``ha_discovery`` stays free of Flask and testable with fakes.

Each hook mirrors an existing web route one-for-one (the Lineups toggle,
the device card's config save, the Firmware page's queue button, the
quiet-hours override form) so an HA command and a click in the admin UI
leave the same state behind.
"""

from __future__ import annotations

import io
import json
import logging
import textwrap
from typing import Any

from flask import Flask

from app.ha_discovery import HaHooks

logger = logging.getLogger(__name__)


def _invalidate_deck(app: Flask, deck: Any) -> None:
    """Same as ``deck_routes._invalidate``: drop warmed frames + nav
    position for the deck's devices so a toggled / rebound lineup doesn't
    leave a stale frame or a dangling position behind."""
    push = app.config.get("PUSH_MANAGER")
    nav = app.config.get("DECK_NAV_STORE")
    for device_id in getattr(deck, "device_ids", []) or []:
        if push is not None and hasattr(push, "clear_deck_cache"):
            with _quiet("deck cache clear"):
                push.clear_deck_cache(device_id)
        if nav is not None:
            with _quiet("deck nav clear"):
                nav.clear(device_id)


class _quiet:
    """Context manager: log-and-swallow, for best-effort side effects."""

    def __init__(self, what: str) -> None:
        self._what = what

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc is not None:
            logger.exception("HA hooks: %s failed", self._what)
        return True


def _set_device_quiet_override(app: Flask, device_id: str, enabled: bool) -> str | None:
    """Flip a display's quiet-hours override. Enabling without stored
    times copies the app-level window so the switch does something
    visible; disabling keeps the times so a later ON restores them."""
    from app import device_service

    devices = app.config["DEVICE_REGISTRY"]
    device = devices.get(device_id)
    if device is None:
        return f"Unknown device {device_id!r}."
    manifest = getattr(device, "manifest", None) or {}
    stored = manifest.get("quiet_hours") if isinstance(manifest, dict) else None
    start = str((stored or {}).get("start") or "")
    end = str((stored or {}).get("end") or "")
    if enabled and not (start and end):
        app_section = app.config["SETTINGS_STORE"].get_section("app") or {}
        start = str(app_section.get("quiet_hours_start") or "22:00")
        end = str(app_section.get("quiet_hours_end") or "07:00")
    result = device_service.update_instance_quiet_hours(
        devices=devices,
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id=device_id,
        enabled=enabled,
        start=start,
        end=end,
    )
    return None if result.ok else (result.error or "Couldn't save quiet hours.")


def _device_config(app: Flask, device_id: str) -> dict[str, Any]:
    from app.device_service import device_config_doc

    device = app.config["DEVICE_REGISTRY"].get(device_id)
    if device is None:
        return {}
    return device_config_doc(app.config["SETTINGS_STORE"], device)


def _set_device_config(app: Flask, device_id: str, values: dict[str, Any]) -> str | None:
    """Patch a display's config the way the device card's save does:
    validate the merged document, persist it, and deliver it (MQTT
    ``config_topic`` publish, relay mailbox, or wait for the next REST
    poll). Also re-derives the wake prediction on an interval change."""
    from app.settings._shared import config_fields_from_schema

    devices = app.config["DEVICE_REGISTRY"]
    device = devices.get(device_id)
    if device is None or device.kind_of is None:
        return f"Unknown device {device_id!r}."
    merged = _device_config(app, device_id)
    merged.update(values)
    fields = config_fields_from_schema(device.config_schema or {})
    ok, err = device.validate_config(merged)
    if not ok:
        return f"Invalid config: {err}"
    store = app.config["SETTINGS_STORE"]
    store.update_for_namespace("devices", device_id, merged, fields)
    if device.transport == "relay":
        publisher = app.config.get("RELAY_PUBLISHER")
        if publisher is not None:
            with _quiet("relay config change"):
                publisher.on_config_change(device_id)
    elif device.transport != "rest" and device.config_topic:
        transport = app.config.get("MQTT_TRANSPORT")
        if transport is not None:
            try:
                transport.publish(
                    device.config_topic,
                    json.dumps(merged).encode("utf-8"),
                    qos=1,
                    retain=True,
                )
            except RuntimeError as exc:
                return f"Config saved, publish failed: {exc}"
    telemetry = app.config.get("DEVICE_TELEMETRY")
    interval = values.get("sleep_interval_s")
    if telemetry is not None and isinstance(interval, int):
        with _quiet("wake reprojection"):
            telemetry.reproject(device_id, interval)
    return None


def _expected_interval(app: Flask, device_id: str) -> int | None:
    """Seconds between heartbeats for a display: the awake poll cadence
    when it stays on, else the stored / default sleep interval."""
    from app.device_service import awake_poll_interval_s

    device = app.config["DEVICE_REGISTRY"].get(device_id)
    if device is None:
        return None
    section = app.config["SETTINGS_STORE"].get_section("devices") or {}
    stored = section.get(device_id) if isinstance(section, dict) else None
    awake = awake_poll_interval_s(stored)
    if awake is not None:
        return awake
    if isinstance(stored, dict) and isinstance(stored.get("sleep_interval_s"), int):
        return int(stored["sleep_interval_s"])
    schema = device.config_schema or {}
    spec = schema.get("sleep_interval_s") if isinstance(schema, dict) else None
    if isinstance(spec, dict) and isinstance(spec.get("default"), int):
        return int(spec["default"])
    return None


def _ota_capable(app: Flask, device_id: str) -> bool:
    status = (app.config.get("DEVICE_STATUS") or {}).get(device_id) or {}
    if status.get("ota_schema") is not None:
        return True
    facts_store = app.config.get("DEVICE_FACTS")
    facts = facts_store.get(device_id) if facts_store is not None else None
    return bool(facts and facts.get("ota_schema") is not None)


def _firmware_state(app: Flask, device_id: str) -> dict[str, Any] | None:
    """What HA's ``update`` entity needs: installed vs latest version,
    whether an update is mid-flight, and where the release notes are."""
    from app import firmware_check as fw_check
    from app.online import online_enabled
    from app.ota.release import is_newer

    device = app.config["DEVICE_REGISTRY"].get(device_id)
    if device is None or device.kind_of is None:
        return None
    capable = _ota_capable(app, device_id)
    status = (app.config.get("DEVICE_STATUS") or {}).get(device_id) or {}
    parsed = status.get("parsed") or {}
    installed = parsed.get("fw_version")
    facts_store = app.config.get("DEVICE_FACTS")
    facts = (facts_store.get(device_id) if facts_store is not None else None) or {}
    if installed in (None, "") and facts.get("fw_version"):
        installed = facts["fw_version"]
    installed_str = str(installed) if installed not in (None, "") else None

    latest: str | None = None
    release_url: str | None = None
    kind_id = str(device.kind_of)
    release_store = app.config.get("OTA_RELEASE")
    rel = release_store.get(kind_id) if release_store is not None else None
    if isinstance(rel, dict) and rel.get("fw_version"):
        latest = str(rel["fw_version"])
    settings = app.config.get("SETTINGS_STORE")
    if settings is not None and online_enabled(settings):
        info = fw_check.latest_for_kind(kind_id, current=installed_str or "")
        if info is not None and info.version:
            if latest is None or is_newer(info.version, latest):
                latest = info.version
            release_url = info.url or None
    report = status.get("ota")
    phase = report.get("phase") if isinstance(report, dict) else None
    in_progress = phase not in (None, "confirmed", "failed", "rolled_back")
    return {
        "capable": capable,
        "installed_version": installed_str,
        "latest_version": latest,
        "in_progress": in_progress,
        "release_url": release_url,
    }


def _firmware_install(app: Flask, device_id: str) -> str | None:
    """Queue one display for its kind's newest release, the same path as
    the Firmware page's per-device Update button."""
    from app.online import online_enabled
    from app.ota import auto as ota_auto

    devices = app.config["DEVICE_REGISTRY"]
    device = devices.get(device_id)
    if device is None or device.kind_of is None:
        return f"Unknown device {device_id!r}."
    if not _ota_capable(app, device_id):
        return f"{device.display_name} has not advertised OTA support."
    store = app.config.get("OTA_RELEASE")
    if store is None:
        return "Firmware releases are not available on this install."
    kind_id = str(device.kind_of)
    settings = app.config["SETTINGS_STORE"]
    rel = store.get(kind_id)
    if online_enabled(settings):
        carry = sorted(ota_auto.auto_update_ids(settings, devices, kind_id))
        rel, err = ota_auto.freshen_release(store, kind_id, rel, carryover=carry)
        if err is not None:
            logger.warning("HA hooks: firmware freshen failed for %s: %s", kind_id, err)
    if rel is None:
        return "No firmware release to offer yet; import a descriptor on the Firmware page."
    state = rel.get("state")
    if state == "promoted":
        queued = {d.id for d in devices.all() if d.kind_of == kind_id and _ota_capable(app, d.id)}
    elif state == "paused":
        queued = set()
    else:
        queued = set(rel.get("canary_device_ids") or [])
    queued.add(device_id)
    store.set_target(
        kind_id,
        rel["descriptor"],
        fw_version=str(rel["fw_version"]),
        canary_device_ids=sorted(queued),
    )
    event_log = app.config.get("EVENT_LOG")
    if event_log is not None:
        with _quiet("event log"):
            event_log.record(
                type="ota",
                source="home_assistant",
                target=kind_id,
                status="ok",
                extra={"action": "queue", "device_id": device_id, "fw_version": rel["fw_version"]},
            )
    return None


def render_notice(message: str, width: int, height: int) -> bytes:
    """A plain black-on-white text card sized to the panel, as PNG bytes.

    Deliberately simple: large wrapped text, centred. It goes through the
    normal image push so each renderer fits, quantises and packs it for
    its own panel; a notification should read from across the room, not
    win a design award."""
    from PIL import Image, ImageDraw, ImageFont

    width = max(64, int(width))
    height = max(64, int(height))
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    margin = max(12, width // 20)
    size = max(18, min(width, height) // 9)
    text = message.strip() or " "
    while True:
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:  # pragma: no cover - Pillow < 10.1
            font = ImageFont.load_default()
        # Wrap by measured width: a character budget derived from the font's
        # average advance keeps long words from spilling past the margin.
        avg = max(1.0, draw.textlength("abcdefghijklmnopqrstuvwxyz", font=font) / 26)
        budget = max(8, int((width - 2 * margin) / avg))
        lines = [
            wrapped
            for paragraph in text.splitlines() or [text]
            for wrapped in (textwrap.wrap(paragraph, budget) or [""])
        ]
        line_h = int(size * 1.25)
        block_h = line_h * len(lines)
        if block_h <= height - 2 * margin or size <= 14:
            break
        size = max(14, int(size * 0.85))
    y = max(margin, (height - block_h) // 2)
    for line in lines:
        w = draw.textlength(line, font=font)
        draw.text(((width - w) / 2, y), line, fill="black", font=font)
        y += line_h
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _notify(app: Flask, device_id: str | None, message: str) -> str | None:
    """Render ``message`` onto one display (or every display) through the
    image push path, so it lands like any other frame and shows up in
    History tagged ``home_assistant``."""
    push = app.config.get("PUSH_MANAGER")
    devices = app.config["DEVICE_REGISTRY"]
    if push is None:
        return "Push pipeline not ready."
    targets = (
        [devices.get(device_id)]
        if device_id is not None
        else [d for d in devices.all() if d.kind_of is not None and d.panel is not None]
    )
    errors: list[str] = []
    sent = 0
    for device in targets:
        if device is None:
            errors.append(f"Unknown device {device_id!r}.")
            continue
        panel = device.panel or {}
        try:
            w, h = int(panel.get("w") or 800), int(panel.get("h") or 480)
        except (TypeError, ValueError):
            w, h = 800, 480
        try:
            png = render_notice(message, w, h)
            result = push.push_image(
                png,
                source_label="Home Assistant notification",
                device_id=device.id,
                fit="fit",
                source="home_assistant",
            )
        except Exception as exc:
            logger.exception("HA hooks: notify render/push failed for %s", device.id)
            errors.append(f"{device.display_name}: {exc}")
            continue
        if result.status == "failed":
            errors.append(f"{device.display_name}: {result.error or 'push failed'}")
        else:
            sent += 1
    if errors and not sent:
        return "; ".join(errors)[:240]
    return None


def build_ha_hooks(app: Flask) -> HaHooks:
    """Assemble the hooks from ``app.config``. Anything not wired yet on
    this install (no OTA store, no scheduler in tests) is simply left
    ``None`` and the matching entities stay unpublished."""
    resolve_tz = app.config.get("RESOLVE_TIMEZONE")
    return HaHooks(
        deck_store=app.config.get("DECK_STORE"),
        deck_nav_store=app.config.get("DECK_NAV_STORE"),
        settings_store=app.config.get("SETTINGS_STORE"),
        scheduler=app.config.get("SCHEDULER"),
        rotation_state_store=app.config.get("DEVICE_ROTATION_STATE_STORE"),
        telemetry=app.config.get("DEVICE_TELEMETRY"),
        button_service=app.config.get("BUTTON_SERVICE"),
        timezone_fn=resolve_tz if callable(resolve_tz) else None,
        invalidate_deck_fn=lambda deck: _invalidate_deck(app, deck),
        set_device_quiet_override_fn=lambda device_id, enabled: _set_device_quiet_override(
            app, device_id, enabled
        ),
        device_config_fn=lambda device_id: _device_config(app, device_id),
        set_device_config_fn=lambda device_id, values: _set_device_config(app, device_id, values),
        firmware_state_fn=(
            (lambda device_id: _firmware_state(app, device_id))
            if app.config.get("OTA_RELEASE") is not None
            else None
        ),
        firmware_install_fn=(
            (lambda device_id: _firmware_install(app, device_id))
            if app.config.get("OTA_RELEASE") is not None
            else None
        ),
        notify_fn=lambda device_id, message: _notify(app, device_id, message),
        expected_interval_fn=lambda device_id: _expected_interval(app, device_id),
    )
