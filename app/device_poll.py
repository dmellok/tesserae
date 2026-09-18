"""Shared next-poll decision for HTTP-polled e-paper clients.

Two request paths ask the same question and want the same answer:

* ``app.rest_api`` — the v1 REST client protocol. ``POST
  /api/v1/device/<id>/status`` echoes ``next_poll_s``.
* ``app.trmnl_api`` — the TRMNL BYOS protocol. ``GET /api/display``
  echoes ``refresh_rate``.

The answer: the device's configured wake interval is the ceiling (the
staleness the operator signed up for, and the only cover for the update
causes that can't be projected — manual Send, webhooks, HA events).
Within that, the soonest *known* change pulls the wake earlier so the
client lands on the new frame; wake alignment reshapes the ceiling onto a
wall-clock grid; and a device inside a quiet window it asked to sleep
through is told to sleep until the window reopens.

Historically this lived in ``app.rest_api`` and only the REST path had
it; ``app.trmnl_api`` echoed the static configured value. This module is
that logic, lifted out so both paths stay in lockstep.

Everything here needs a Flask **app** context (it reads ``SCHEDULER``,
``SETTINGS_STORE``, ``PUSH_MANAGER`` and ``DEVICE_TELEMETRY`` off
``current_app.config``) but no request context. Every external
dependency is wrapped so a fault degrades to the configured interval
rather than stranding a device.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from flask import current_app

from app.device_loader import Device
from app.device_service import AWAKE_POLL_MIN_S, awake_poll_interval_s
from app.state.settings_store import SettingsStore

logger = logging.getLogger(__name__)

# Seconds to add to a projected content change before telling the client to
# poll. The render is a browser compose plus a quantize, so a client polling
# at exactly that moment races it and collects the *previous* frame. A margin
# costs nothing (the device is asleep for it) and turns a guaranteed miss
# into a hit. ``/frame`` also re-renders on demand once a declared change has
# passed, so this only needs to cover the render itself.
CONTENT_POLL_MARGIN_S: int = 20

# Seconds to add to a *scheduler-projected* change. The scheduler does not
# fire at the projected instant: it wakes every ``tick_seconds`` (30 s, on a
# phase set by when the process started), fires everything due in ascending
# priority order, and each fire renders in turn, so a lineup due at 06:00:00
# lands anywhere up to ~40 s later on a busy tick. A device told to come back
# at 06:00:20 polled before the frame existed, collected yesterday's, and
# slept its whole configured interval on it. The margin has to clear a full
# tick plus the renders that share it.
_SCHEDULER_TICK_S: int = 30
_PROJECTED_POLL_MARGIN_S: int = _SCHEDULER_TICK_S + 2 * CONTENT_POLL_MARGIN_S

# Never ask a client to poll faster than this, however close the next change
# is. Itself clamped by the configured interval below, so a deliberately
# hot-polling panel (interval < this) isn't slowed down.
MIN_CONTENT_POLL_S: int = 5

# Certainties worth waking for. ``estimated`` events are the engine's own
# guess at an unanchored cadence, so waking early for one trades a real wake
# for a maybe; the configured interval is the better answer there.
_WAKE_WORTHY_CERTAINTIES = frozenset({"scheduled", "conditional"})

# Sleep-through quiet hours (#299): wake this long after the window opens so
# the first poll lands on the far side of it even if the clocks disagree by a
# few seconds, and never ask for more than this in one go (TRMNL firmware's
# own ceiling is seven days; the v1 REST firmware's is similar).
_QUIET_SLEEP_MARGIN_S: int = 30
_QUIET_SLEEP_MAX_S: int = 6 * 24 * 3600


def _settings() -> SettingsStore:
    return current_app.config["SETTINGS_STORE"]  # type: ignore[no-any-return]


def device_awake_poll_s(device: Device) -> int | None:
    """This device's always-on poll cadence, or ``None`` when it deep-sleeps.

    Read live from settings on every heartbeat, so changing the cadence in
    Settings takes effect on the device's next poll with no reboot."""
    section = _settings().get_section("devices") or {}
    stored = section.get(device.id) if isinstance(section, dict) else None
    return awake_poll_interval_s(stored)


def _projected_poll_s(device: Device, configured_s: int) -> int | None:
    """Seconds until the device's next *projected content change*, plus the
    render margin, or ``None`` when there's nothing to project.

    The projection engine already answers this for the Companion API and
    the scheduler (``scheduler.upcoming_for_device``); this reads the same
    answer onto the poll decision.

    Only ever returns something *sooner* than ``configured_s``; the caller
    keeps that as the ceiling. Manual Send, webhooks, Home Assistant events
    and data-change refreshes have no schedule to project, so a device that
    slept past its configured interval would go blind to all of them.
    """
    scheduler = current_app.config.get("SCHEDULER")
    if scheduler is None:
        return None

    from datetime import UTC, datetime

    from app.device_upcoming import MAX_HOURS
    from app.quiet_hours import resolve_quiet_hours

    now = datetime.now(UTC)
    # Anything past the configured interval gets capped to it anyway, so
    # there's no point walking the record set further than that.
    hours = max(1, min(MAX_HOURS, -(-configured_s // 3600)))
    quiet_window = resolve_quiet_hours(_settings().get_section("app") or {}, device)
    events = scheduler.upcoming_for_device(
        device.id,
        now=now,
        hours=hours,
        limit=4,
        quiet_window=quiet_window,
    )
    for event in events:
        if event.certainty not in _WAKE_WORTHY_CERTAINTIES:
            continue
        delta = (event.scheduled_at - now).total_seconds()
        # A record whose target has passed but which has not fired yet is
        # projected at "now" (the next tick fires it). It reaches here a
        # fraction of a second in the past, because ``scheduled_at`` is
        # truncated to whole seconds, and used to be skipped as stale, so a
        # device that polled a moment before the tick was told to sleep its
        # whole interval on the old frame. Anything overdue by less than a
        # tick is imminent: poll again after the margin.
        if delta < -_SCHEDULER_TICK_S:
            continue
        return max(0, int(delta)) + _PROJECTED_POLL_MARGIN_S
    return None


def _widget_change_poll_s(device: Device) -> int | None:
    """Seconds until a widget on this device said its own output goes
    stale, plus the render margin (#243).

    Schedules and rotation steps are what ``project_upcoming`` can see. A
    meeting ending, a bin going out, a countdown hitting zero are none of
    those: only the widget knows, and only once it has fetched. The
    composer records the soonest hint per device on every render.
    """
    from app import widget_next_change

    app = current_app._get_current_object()  # type: ignore[attr-defined]
    ts = widget_next_change.peek(app, device.id)
    if ts is None:
        return None
    delta = ts - time.time()
    if delta < 0:
        return None
    return int(delta) + CONTENT_POLL_MARGIN_S


def _wake_alignment_for(device: Device) -> Any:
    """The device's resolved wake alignment, or ``None`` when off.

    Always-on panels are excluded: they aren't on the sleep grid at all,
    so aligning their poll cadence would only add jitter to a device
    that is already continuously reachable."""
    from app import wake_alignment

    if device_awake_poll_s(device) is not None:
        return None
    section = _settings().get_section("devices") or {}
    stored = section.get(device.id) if isinstance(section, dict) else None
    return wake_alignment.alignment_from_stored(stored)


def _aligned_wake_epoch(device: Device, alignment: Any, configured: int) -> float | None:
    """Epoch of the next aligned wake for ``device``, or ``None``.

    The lead comes from telemetry's measured wake-to-checkin EWMA so the
    paint (not the radio) lands on the grid; zero until the first aligned
    cycle has been observed. Quiet hours resolve the same way the
    projection path resolves them, so grid points inside the window are
    skipped rather than waking a panel automation won't repaint."""
    from app import wake_alignment
    from app.quiet_hours import resolve_quiet_hours
    from app.tz_resolve import app_timezone

    lead = 0
    telemetry = current_app.config.get("DEVICE_TELEMETRY")
    if telemetry is not None:
        try:
            entry = telemetry.get(device.id)
            if entry is not None and entry.wake_lead_ewma_s is not None:
                lead = round(entry.wake_lead_ewma_s)
        except Exception:
            lead = 0
    quiet = resolve_quiet_hours(_settings().get_section("app") or {}, device)
    return wake_alignment.next_aligned_wake_epoch(
        alignment,
        now=time.time(),
        tz=app_timezone(),
        interval_s=configured,
        quiet=quiet,
        lead_s=lead,
    )


def _quiet_sleep_through_s(device: Device) -> int | None:
    """Seconds until this device's quiet window *ends*, when it is inside
    the window now and its effective quiet-hours layer asked it to sleep
    through. ``None`` otherwise, and for always-on panels: those never
    sleep, so the saving does not exist and holding their polls would only
    delay a manual push."""
    if device_awake_poll_s(device) is not None:
        return None
    from datetime import UTC, datetime

    from app.quiet_hours import quiet_ends_at, resolve_quiet_hours
    from app.tz_resolve import app_timezone

    window = resolve_quiet_hours(_settings().get_section("app") or {}, device)
    if window is None or not window.sleep_through:
        return None
    now = datetime.now(UTC)
    ends = quiet_ends_at(window, now, app_timezone())
    if ends is None or ends <= now:
        return None
    return min(int((ends - now).total_seconds()) + _QUIET_SLEEP_MARGIN_S, _QUIET_SLEEP_MAX_S)


def next_poll_decision(device: Device, *, configured_s: int) -> tuple[int, int | None]:
    """How many seconds until the client should poll again, plus the
    absolute wake instant (epoch) when wake alignment issued one.

    ``configured_s`` is the caller's per-device wake interval — the
    ceiling. ``app.rest_api`` passes its ``sleep_interval_s`` resolution,
    ``app.trmnl_api`` its ``refresh_rate_s`` resolution. An always-on
    device overrides it with its awake cadence, since a sleep interval
    says nothing about when a device that never sleeps comes back, and a
    sleeping device showing a page that declares its own interval
    overrides it with that (#144) — the content is what knows how often
    it changes. Both are resolved here rather than in each caller, so the
    two wire protocols answer the same way.

    Within the ceiling, the soonest known change (the scheduler's
    projection of schedules + rotation steps, and a widget's own
    declaration of when its data turns over, #243) pulls the wake
    earlier so the client lands on the new frame. Wake alignment
    reshapes the ceiling: ``interval`` mode makes it "seconds to the
    next wall-clock grid point" (projected pulls still apply); ``times``
    mode wakes only at the listed moments (nothing pulls it earlier). A
    device inside a quiet window it asked to sleep through is stretched
    to the window's end (#299).

    The second element is ``None`` unless alignment or the sleep-through
    stretch issued an absolute instant; when set it is the same instant
    as the first element as an epoch, so capable firmware can sleep to a
    wall-clock target and shed timer drift. Any fault in a dependency
    degrades to the configured interval.
    """
    result, wake_at = _decision_inner(device, configured_s)
    try:
        through = _quiet_sleep_through_s(device)
    except Exception:
        logger.exception("device_poll: quiet-hours sleep-through failed for device=%s", device.id)
        through = None
    if through is not None and through > result:
        return through, int(time.time()) + through
    return result, wake_at


def next_poll_s(device: Device, *, configured_s: int) -> int:
    """Relative-only view of :func:`next_poll_decision`, for callers whose
    wire format carries only a duration (TRMNL ``refresh_rate``)."""
    return next_poll_decision(device, configured_s=configured_s)[0]


def _decision_inner(device: Device, configured_s: int) -> tuple[int, int | None]:
    from app import wake_alignment

    now = time.time()
    awake = device_awake_poll_s(device)
    configured = awake if awake is not None else configured_s
    if awake is None:
        # The page on the glass may declare its own wake interval, and when it
        # does it replaces the device's (#144). Only for a device that sleeps:
        # an always-on panel is not on the sleep grid at all, so a page's
        # cadence says nothing about when it comes back, and honouring one
        # there would slow the manual Send that always-on mode exists for.
        from app import page_cadence

        try:
            declared = page_cadence.page_sleep_interval_s(device)
        except Exception:
            logger.exception("device_poll: page cadence failed for device=%s", device.id)
            declared = None
        if declared is not None:
            configured = declared

    aligned_epoch: float | None = None
    alignment = None
    try:
        alignment = _wake_alignment_for(device)
        if alignment is not None:
            aligned_epoch = _aligned_wake_epoch(device, alignment, configured)
    except Exception:
        logger.exception("device_poll: wake alignment failed for device=%s", device.id)
        alignment, aligned_epoch = None, None

    if alignment is not None and aligned_epoch is not None:
        aligned_delta = max(1, round(aligned_epoch - now))
        if alignment.mode == wake_alignment.MODE_TIMES:
            return aligned_delta, int(now) + aligned_delta
        configured = aligned_delta

    def _wake_at(result: int) -> int | None:
        if aligned_epoch is None:
            return None
        return int(now) + result

    candidates: list[int] = []
    try:
        projected = _projected_poll_s(device, configured)
    except Exception:
        logger.exception("device_poll: next-poll projection failed for device=%s", device.id)
        projected = None
    if projected is not None:
        candidates.append(projected)
    try:
        declared = _widget_change_poll_s(device)
    except Exception:
        logger.exception("device_poll: next-poll widget hint failed for device=%s", device.id)
        declared = None
    if declared is not None:
        candidates.append(declared)
    if not candidates:
        return configured, _wake_at(configured)
    # Ceiling: the configured interval. Floor: MIN_CONTENT_POLL_S, itself
    # capped by the configured interval so a hot-polling panel keeps its
    # cadence. An always-on panel floors at the awake minimum instead: the
    # content floor exists to stop a sleeping device spinning its radio up
    # for a change it could have waited for, and a device that never sleeps
    # is already associated.
    floor = AWAKE_POLL_MIN_S if awake is not None else MIN_CONTENT_POLL_S
    result = max(min(min(candidates), configured), min(configured, floor))
    return result, _wake_at(result)
