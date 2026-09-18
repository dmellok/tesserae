"""Repaint floor enforcement on the frame-delivery path (#250).

A hardware profile's ``refresh_floor_s`` says how fast the *glass* can be
repainted. It is not a poll cadence: a poll is a conditional GET that
answers 304 while the frame is unchanged, and a 304 never reaches the
panel. Clamping the ask to the repaint limit (the field's only historical
enforcement point, in ``app.rest_api._configured_poll_s``) made an always-on
E1003 with a 5 s cadence poll once a minute, so a manual Send took up to a
minute to appear. Removing that clamp in v0.332.0 fixed the poll and left
the repaint side of the field unguarded: every profile that declares a
floor states a constraint the server does not apply.

The limit belongs here, on the path that decides to hand a device a new
frame. The rule this module implements:

* A new frame that would land inside the floor is **held, not dropped**.
  The device keeps painting what it already holds and collects the new
  frame on the first poll after the floor expires.
* Because the frame is only ever held — never discarded — a newer render
  arriving during the hold simply replaces the held one, and the panel
  lands on the latest content when it does repaint. That falls out of
  serving ``latest_render_for`` rather than a queue.
* The wait is timed from the last frame *handed over*, which is the
  closest the server gets to when the glass last moved.

Three cases deliberately do not hold, because holding them would trade a
guarantee the server cannot prove for an outage it can:

* No declared floor. Most device kinds have none.
* No recorded handover. A device whose last frame predates
  ``last_served_at`` (or that has never been served) gives nothing to
  measure from, and an unmeasurable floor is not a reason to withhold a
  frame.
* No delivery-side bookkeeping at all (``push_mgr`` absent, as in tests
  that stub it out).
"""

from __future__ import annotations

import math
import time
from typing import Any

from app.device_loader import Device


def panel_floor_s(device: Device) -> int | None:
    """This device's declared repaint floor in seconds, or ``None``.

    Hardware profiles carry ``refresh_floor_s`` at the manifest root
    (``app.hardware_catalog._derive_manifest`` copies it there); device
    kinds without a profile have no floor at all.
    """
    raw = (device.manifest or {}).get("refresh_floor_s")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if raw > 0 else None


def hold_remaining_s(device: Device, push_mgr: Any, *, now: float | None = None) -> int | None:
    """Whole seconds left before this panel may be repainted, or ``None``
    when a new frame may be handed over right now.

    Rounded up, so a caller that turns this into a next-poll hint asks the
    device back on the far side of the floor rather than a fraction of a
    second short of it and having to wait a whole cycle more.
    """
    floor = panel_floor_s(device)
    if floor is None or push_mgr is None:
        return None
    reader = getattr(push_mgr, "last_served_render_for", None)
    if not callable(reader):
        return None
    try:
        served = reader(device.id)
    except Exception:
        return None
    if not isinstance(served, dict):
        return None
    served_at = served.get("served_at")
    if not isinstance(served_at, (int, float)):
        return None
    elapsed = (time.time() if now is None else now) - float(served_at)
    # A clock that went backwards (NTP step, container restart) would
    # otherwise hold the panel for up to a floor's worth of nonsense.
    if elapsed < 0:
        return None
    remaining = float(floor) - elapsed
    if remaining <= 0:
        return None
    return max(1, math.ceil(remaining))
