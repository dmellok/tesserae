"""Pydantic model for per-device rotation state.

The rotation model (`Rotation` in `rotation_model.py`) describes an
ordered cycle of pages tied to wall-clock time. That's enough for
scheduled panels that only ever wake on a timer. Physical button
presses need an extra piece of state: the manually-selected step
index for a specific device, plus a "hold" window that suppresses
the time-based scheduler for a while after the press so the display
doesn't get yanked back a minute later.

This module owns that state and nothing else. It's read by the
button handler (to compute a new step index after a press), by the
`/frame` handler (to decide whether to serve the manual step or the
time-based one), and written by the button dispatcher (to record the
new position + the debounce fingerprint).

One record per device_id. Devices with no manual overrides ever
recorded have no entry, and the reader falls back to the time-based
step. Two failure modes are represented explicitly:

* ``rotation_id is None`` records that a button was seen but the
  device isn't bound to any rotation. Kept so we can log + dedup
  even in the no-op case.
* ``override_until`` is a UTC timestamp; readers compare against
  ``datetime.now(tz=UTC)``. Once past, the manual position stops
  taking precedence and the time-based scheduler resumes.

mypy --strict applies via re-export through ``app.state``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeviceRotationState(BaseModel):
    """One device's manual rotation position + button dedup state."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1)

    # The rotation the manual position points into. ``None`` means the
    # device isn't bound to any rotation (button events are dedup'd +
    # logged, but no state change).
    rotation_id: str | None = None

    # 0-indexed position in the resolved rotation's ``steps`` list.
    # Undefined semantics when ``rotation_id is None``.
    step_index: int = Field(default=0, ge=0)

    # UTC timestamp; while ``datetime.now(UTC) < override_until`` the
    # manual position takes precedence over the time-based scheduler.
    # ``None`` means "never overridden", i.e. always fall through to
    # scheduler.
    override_until: datetime | None = None

    # Debounce fingerprints for the last processed button event.
    # ``last_button_event_id`` is the monotonic counter the firmware
    # sends; incoming events with an id ``<= last_button_event_id``
    # are treated as retries and no-op'd. When the firmware doesn't
    # send an event id (legacy or bug), the fallback compares the
    # incoming button + timestamp against these fields within a
    # settings-configured window (default 3s).
    last_button: str | None = None
    last_button_event_id: int | None = None
    last_button_at: datetime | None = None
