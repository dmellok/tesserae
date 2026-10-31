"""Whether a manual push reaches a display now, or waits for it.

A push to a display holding a live connection (a Visionect sign, an MQTT
panel, a frame set to stay awake) lands right away. A sleeping battery frame
(a REST client: a reTerminal, an ESP32) only fetches it at its next check-in,
or when someone presses its wake button. Without saying so, a push to a
sleeping frame looks like nothing happened.

``delivery_notice`` answers that for one display, in plain words, and every
push surface (Send, Lineups "Push now", the device page, decks, the companion
API, MCP, webhooks) shows the answer next to its usual confirmation:
HTML flows flash it (:func:`flash_delivery`), JSON replies carry it as an
additive ``delivery`` list (:func:`delivery_notices`).

It reads the same reach (:func:`app.next_update.reach_for_device`) the
"Next update" line on the Devices and Lineups pages reads, so the two agree.
Nothing here changes how a push is delivered: no forced wake.

The wording is built by :func:`notice_for`, which is pure (plain numbers and a
``Reach``) so it can be tested without an app.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, datetime, tzinfo
from typing import Any

from app.next_update import MODE_CONNECTED, MODE_SLEEPS, Reach, _checkin_at_or_after

LIVE = "live"
QUEUED = "queued"
UNKNOWN = "unknown"

REASON_SLEEPING = "sleeping"
REASON_QUIET_HOURS = "quiet_hours"
REASON_OFFLINE = "offline"
REASON_NO_STATUS = "no_status"

LIVE_TEXT = "Delivering now"

# A predicted check-in this far in the past means the frame missed it: it is
# offline (out of battery, out of Wi-Fi range), not merely about to arrive.
_OVERDUE_MIN_S = 120.0
# An awake frame that has not polled for this many of its poll intervals (and
# at least this long) is not awake any more.
_AWAKE_STALE_POLLS = 3
_AWAKE_STALE_MIN_S = 300.0
# A flash lists at most this many displays, then counts the rest.
_FLASH_MAX_LINES = 4


def _time_label(epoch: float, *, tz: tzinfo | None, now: float, cycle: str) -> str:
    """``3:48 PM`` / ``15:48`` today, ``Tue 7 AM`` another day, in the app's
    clock style (:func:`app.clock_text.epoch_label`)."""
    from app.clock_text import epoch_label

    return epoch_label(epoch, tz=tz, now=now, cycle=cycle)


def _age_label(seconds: float) -> str:
    """``7 min`` / ``2 h`` / ``3 days``: how long since the frame was heard."""
    minutes = max(1, int(seconds // 60))
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h"
    return f"{hours // 24} days"


def notice_for(
    *,
    device_id: str,
    name: str,
    reach: Reach,
    now: float,
    tz: tzinfo | None = None,
    cycle: str = "h23",
    transport: str = "",
    last_heartbeat_at: float | None = None,
    quiet_until: float | None = None,
    in_quiet_hours: bool = False,
    awake_poll_s: float | None = None,
    wake_button: bool = True,
) -> dict[str, Any]:
    """What a push to this display does right now, in plain words.

    Returns ``{device_id, name, mode, expected_at, reason, text, short}``:

    * ``mode``: ``live`` (it lands now), ``queued`` (it waits for the frame,
      ``expected_at`` is the predicted check-in when one is known) or
      ``unknown`` (a pull frame whose cadence isn't known yet).
    * ``reason``: ``sleeping``, ``quiet_hours``, ``offline`` (missed its
      check-in, or a sign that isn't connected), ``no_status`` (never checked
      in) or ``None``.
    * ``text``: the sentence a toast shows; ``short``: the few words a button
      confirmation shows (``Queued · picks up ~3:48 PM``).

    ``in_quiet_hours`` / ``quiet_until``: the display is inside its quiet
    window (a sleeping frame sleeps through it), until that epoch (``None``
    when it doesn't end within the week). ``awake_poll_s``: the poll cadence
    of a frame set to stay awake, used to tell a stale one. ``wake_button``:
    the display has a button that wakes it."""

    def when(epoch: float) -> str:
        return _time_label(epoch, tz=tz, now=now, cycle=cycle)

    def out(
        mode: str, text: str, short: str, *, reason: str | None = None, at: float | None = None
    ) -> dict[str, Any]:
        return {
            "device_id": device_id,
            "name": name,
            "mode": mode,
            "expected_at": at,
            "reason": reason,
            "text": text,
            "short": short,
        }

    wake = ", or press its wake button to get it now" if wake_button else ""
    heard_ago = None if last_heartbeat_at is None else max(0.0, now - last_heartbeat_at)

    if reach.mode == MODE_CONNECTED:
        if not reach.live and awake_poll_s and heard_ago is not None:
            stale_after = max(_AWAKE_STALE_MIN_S, _AWAKE_STALE_POLLS * awake_poll_s)
            if heard_ago > stale_after:
                return out(
                    QUEUED,
                    f"{name} hasn't checked in for {_age_label(heard_ago)}: "
                    f"it will pick this up when it next checks in{wake}.",
                    f"Queued · last seen {_age_label(heard_ago)} ago",
                    reason=REASON_OFFLINE,
                )
        return out(LIVE, LIVE_TEXT, "Pushed · lands now")

    if reach.mode == MODE_SLEEPS:
        due = reach.next_checkin_at
        if due is None:
            # A Visionect sign that isn't holding its connection.
            return out(
                QUEUED,
                f"{name} isn't connected: it will pick this up when it reconnects.",
                "Queued · waiting for it to reconnect",
                reason=REASON_OFFLINE,
            )
        grace = max(_OVERDUE_MIN_S, 0.1 * (reach.gap_s or 0.0))
        if due < now - grace:
            silent = f"hasn't checked in for {_age_label(heard_ago)}" if heard_ago else "is late"
            return out(
                QUEUED,
                f"{name} {silent} (it was due at {when(due)}): "
                f"it will pick this up when it next checks in{wake}.",
                "Queued · check-in overdue",
                reason=REASON_OFFLINE,
            )
        # The first check-in after any quiet hours (the frame sleeps through
        # them): the same pickup the Lineups header and the Settings queued
        # line show (:func:`app.next_update.compute_next_update`).
        floor = now
        if in_quiet_hours and quiet_until is not None:
            floor = max(now, quiet_until + 60)
        checkin = _checkin_at_or_after(reach, floor) or due
        if in_quiet_hours:
            until = f" until {when(quiet_until)}" if quiet_until is not None else ""
            return out(
                QUEUED,
                f"{name} is in quiet hours{until}: it will pick this up at its next "
                f"check-in (~{when(checkin)}){wake}.",
                f"Queued · picks up ~{when(checkin)}",
                reason=REASON_QUIET_HOURS,
                at=checkin,
            )
        return out(
            QUEUED,
            f"{name} is asleep: it will pick this up at its next check-in "
            f"(~{when(checkin)}){wake}.",
            f"Queued · picks up ~{when(checkin)}",
            reason=REASON_SLEEPING,
            at=checkin,
        )

    # Nothing known about when it can receive. An MQTT panel is subscribed
    # and takes a publish the moment it lands; a pull frame takes it when it
    # next polls, whenever that is.
    if transport == "mqtt":
        return out(LIVE, LIVE_TEXT, "Pushed · lands now")
    if last_heartbeat_at is None:
        return out(
            UNKNOWN,
            f"{name} hasn't checked in yet: it will pick this up the first time it does.",
            "Queued · picks up at its first check-in",
            reason=REASON_NO_STATUS,
        )
    if in_quiet_hours:
        until = f" until {when(quiet_until)}" if quiet_until is not None else ""
        return out(
            UNKNOWN,
            f"{name} is in quiet hours{until}: it will pick this up the next time "
            f"it checks in{wake}.",
            "Queued · picks up at its next check-in",
            reason=REASON_QUIET_HOURS,
        )
    return out(
        UNKNOWN,
        f"{name} will pick this up the next time it checks in{wake}.",
        "Queued · picks up at its next check-in",
    )


# -- reading the app ------------------------------------------------------


def _clock_cycle() -> str:
    from app.clock_text import app_cycle

    return app_cycle()


def delivery_notice(
    device: Any, now: float | None = None, *, cycle: str | None = None
) -> dict[str, Any]:
    """:func:`notice_for` for a registered ``device``, read from the app's
    stores (live connection state, telemetry, quiet hours, settings).
    Needs an app context."""
    from flask import current_app

    from app.device_loader import has_buttons
    from app.device_service import awake_poll_interval_s
    from app.next_update import _tz, reach_for_device
    from app.quiet_hours import is_in_window, quiet_ends_at, resolve_quiet_hours

    now = time.time() if now is None else now
    config = current_app.config
    tz = _tz()
    reach = reach_for_device(device, now=now)
    entry = None
    telemetry = config.get("DEVICE_TELEMETRY")
    if telemetry is not None:
        try:
            entry = telemetry.get(device.id)
        except Exception:
            entry = None
    settings = config.get("SETTINGS_STORE")
    stored: Any = None
    app_section: dict[str, Any] = {}
    if settings is not None:
        section = settings.get_section("devices") or {}
        stored = section.get(device.id) if isinstance(section, dict) else None
        app_section = settings.get_section("app") or {}
    in_quiet = False
    quiet_until: float | None = None
    window = resolve_quiet_hours(app_section, device)
    if window is not None:
        now_dt = datetime.fromtimestamp(now, UTC)
        if is_in_window(window, now_dt, tz):
            in_quiet = True
            ends = quiet_ends_at(window, now_dt, tz)
            # The first minute outside is a minute past the window's end
            # (the end minute is inclusive); say the end the user set.
            quiet_until = ends.timestamp() - 60 if ends is not None else None
    heard = getattr(entry, "last_heartbeat_at", None)
    return notice_for(
        device_id=device.id,
        name=str(getattr(device, "display_name", None) or device.id),
        reach=reach,
        now=now,
        tz=tz,
        cycle=cycle or _clock_cycle(),
        transport=str(getattr(device, "transport", "") or ""),
        last_heartbeat_at=float(heard) if isinstance(heard, (int, float)) else None,
        quiet_until=quiet_until,
        in_quiet_hours=in_quiet,
        awake_poll_s=awake_poll_interval_s(stored),
        wake_button=has_buttons(device),
    )


def delivery_notices(
    device_ids: Iterable[str], *, now: float | None = None
) -> list[dict[str, Any]]:
    """One notice per known display in ``device_ids`` (order kept, repeats
    and unknown ids dropped). A display whose notice can't be built is left
    out: this decorates a push reply and must never break it."""
    from flask import current_app

    registry = current_app.config.get("DEVICE_REGISTRY")
    devices = getattr(registry, "devices", None)
    if not isinstance(devices, dict):
        return []
    now = time.time() if now is None else now
    try:
        cycle = _clock_cycle()
    except Exception:
        cycle = "h23"
    seen: set[str] = set()
    notices: list[dict[str, Any]] = []
    for did in device_ids:
        if not isinstance(did, str) or did in seen or did not in devices:
            continue
        seen.add(did)
        try:
            notices.append(delivery_notice(devices[did], now, cycle=cycle))
        except Exception:
            current_app.logger.debug("push_delivery: no notice for %s", did, exc_info=True)
    return notices


def waiting(notices: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The notices for displays that won't get the push right away."""
    return [n for n in notices if n.get("mode") != LIVE]


def flash_text(notices: Iterable[dict[str, Any]]) -> str:
    """The queued flash's message: one line per display that waits, the rest
    counted once there are many. Empty when every display is live."""
    lines = [str(n["text"]) for n in waiting(notices)]
    if len(lines) > _FLASH_MAX_LINES:
        rest = len(lines) - (_FLASH_MAX_LINES - 1)
        lines = lines[: _FLASH_MAX_LINES - 1]
        lines.append(f"{rest} more displays pick this up at their next check-in.")
    return "\n".join(lines)


def flash_delivery(device_ids: Iterable[str], *, now: float | None = None) -> list[dict[str, Any]]:
    """Flash the "queued" notice for the push just sent to ``device_ids``
    (nothing when they all take it right away). Returns the notices."""
    from flask import flash

    notices = delivery_notices(device_ids, now=now)
    text = flash_text(notices)
    if text:
        flash(text, "queued")
    return notices


def page_targets(page_id: str) -> list[str]:
    """The displays a push of saved page ``page_id`` fans out to: its bound
    displays, else (when unbound broadcast is on) every registered one."""
    from flask import current_app

    store = current_app.config.get("PAGE_STORE")
    page = None
    if store is not None:
        try:
            page = store.get(page_id)
        except Exception:
            page = None
    bound = [str(d) for d in (getattr(page, "device_ids", None) or [])]
    if bound:
        return bound
    from app.push import _unbound_broadcast_enabled

    if page is None or not _unbound_broadcast_enabled(current_app.config.get("SETTINGS_STORE")):
        return []
    recipients = getattr(current_app.config.get("PUSH_MANAGER"), "_broadcast_recipients", None)
    try:
        found = recipients() if callable(recipients) else None
    except Exception:
        found = None
    return sorted(d for d in found if isinstance(d, str)) if isinstance(found, set) else []
