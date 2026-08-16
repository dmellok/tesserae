"""When (and whether) to ask an install to consider sponsoring.

Tesserae is free, self-hosted, and has no account system, so the only
honest moment to ask is one the operator has already earned: the install
has been running for a year, or it has painted a lot of frames. Both are
read from data that already exists locally, and neither is a headcount.
Panel count deliberately isn't a trigger: twelve secondhand panels is a
hobbyist with a soldering iron and three panels can be a business, so
fleet size can't tell the two apart.

Rules the rest of the app relies on:

* **Asked once.** Dismissal is permanent and silent. There is no "remind
  me later", because a promise to ask again is the nagware pattern this
  is trying not to be.
* **Dismissal lives in settings, not in the stats store.** Deleting the
  stats counters is a privacy control; having it resurrect the ask would
  punish the person who used it.
* **Never blocks anything.** The card is a card. Nothing is gated,
  delayed, degraded, or counted against a limit, and nothing about this
  is ever rendered to a panel: the display is the operator's wall, not a
  billboard.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from app.state.stats_store import FRAMES_BY_DEVICE, StatsStore

logger = logging.getLogger(__name__)

SETTINGS_SECTION = "app"
DISMISSED_KEY = "sponsor_prompt_dismissed"

# A year of running, or ten thousand frames painted, whichever lands
# first. At a 15-minute cadence one panel reaches the frame milestone in
# about three months, so a real fleet gets there earlier; the year is
# what catches the quiet single-panel install that has simply kept going.
MILESTONE_DAYS = 365
MILESTONE_FRAMES = 10_000


def _dismissed(settings: Any) -> bool:
    try:
        return bool(settings.get_section(SETTINGS_SECTION).get(DISMISSED_KEY))
    except Exception:
        # A settings store that can't be read is not permission to ask.
        return True


def dismiss(settings: Any) -> None:
    settings.patch_section(SETTINGS_SECTION, {DISMISSED_KEY: True})


def install_age_days(created_at: str, *, now: float | None = None) -> int:
    """Days since the install id was minted, or 0 when unparseable.

    The age comes from the install id rather than the stats store because
    the counters started when the stats feature shipped: reading them for
    age would make every existing install look new."""
    if not created_at:
        return 0
    try:
        minted = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return 0
    seconds = (now if now is not None else time.time()) - minted.timestamp()
    return max(0, int(seconds // 86_400))


def state(
    *,
    settings: Any,
    stats: StatsStore | None,
    install_created_at: str,
    now: float | None = None,
) -> dict[str, Any] | None:
    """The card's contents, or ``None`` when it shouldn't appear.

    ``reason`` names which milestone fired so the copy can lead with the
    number the operator actually earned rather than a generic appeal."""
    if stats is None or _dismissed(settings):
        return None
    frames = stats.total(FRAMES_BY_DEVICE)
    days = install_age_days(install_created_at, now=now)
    if days >= MILESTONE_DAYS:
        reason = "years"
    elif frames >= MILESTONE_FRAMES:
        reason = "frames"
    else:
        return None
    return {
        "reason": reason,
        "frames": frames,
        "days": days,
        "years": max(1, days // MILESTONE_DAYS),
        "counting_since": stats.since(),
    }
