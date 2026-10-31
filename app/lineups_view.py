"""View-model for the Lineups page (``/decks``), redesign round 7.

The page draws every display as a header (what is on screen, what comes
next, a day bar) over its lineups, sorted by when each one plays next. This
module builds that whole picture from plain inputs so it can be unit-tested
without Flask, the stores or a clock:

* **Inputs** (``*Input`` dataclasses): one :class:`DisplayInput` per display,
  carrying its lineups (:class:`LineupInput`), what its glass holds, how it can
  be reached (:class:`app.next_update.Reach`) and its quiet hours.
  :mod:`app.deck_routes` fills them from the deck store and the live state.
* **Builder**: :func:`build_view` turns those into a :class:`LineupsView`.
  Everything (states, the day bar, the week grid, step flags, the header's
  next update) comes from one schedule model, a list of blocks in *week
  minutes* (Monday 00:00 = 0), so no two parts of the page can disagree.
  This mirrors the approved mockup's ``build()`` / ``dayPieces()``.
* **Output** (``*View`` dataclasses): JSON-serialisable via ``to_dict()``;
  the template and ``GET /decks/view.json`` render from the same dict.

:func:`sample_view_model` returns the mockup's demo data (Wednesday 18:12, a
connected Visionect frame in sync and a sleeping kitchen display with a
queued step) run through the real builder, for the frontend and the tests.

Timeline additions keep ``schema_version: 1`` and every existing field.
Each ``DisplayView.week[day].segments`` hold includes ``carried`` (a
``ScreenView`` thumbnail with dashboard ID, name, URL, aspect and dimensions,
or null), ``from_lineup_id``, ``from_prev_day``, ``from_day_label`` (the
originating block's weekday when carried across midnight, otherwise null),
``predicted`` (the clipped stretch starts after now) and ``is_current``.
These describe the schedule's last frame; future holds are predictions.
Window/push segments include ``steps``: ordered advance occurrences with
``index`` (zero-based lineup step), ``dashboard_id``, ``name``, ``thumb_url``,
``aspect``, ``at_min`` (minute on that weekday), ``at_label`` (``clock``),
``current`` and ``next``. Repeated rotations repeat their steps. Only the
first 60 occurrences are included; ``steps_total`` counts all occurrences.
The same scheduler-parity block points drive these times and the day bar.
Timeline thumbnail metadata is resolved once per dashboard per display build and
reused across all seven days. Holds have empty steps and a zero total.
Successful empty scheduler projections never fall back to model delivery
times; model-only next runs are explicitly labelled as scheduled.

Lineup kinds map onto the deck model as follows (see ``deck_model.Deck``):

``cycle``     ``advance`` timer/both on the ``cycle`` trigger: a window from
              ``advance_anchor`` to ``advance_end_at`` (midnight when unset),
              stepping through the pages by their dwell.
``interval``  timer on the ``interval`` trigger: fires every
              ``advance_interval_minutes`` inside an optional window (which may
              wrap past midnight; unset means all day).
``daily``     timer on the ``daily`` trigger: one push at ``advance_fires_at``;
              the frame is then held until the next lineup.
``manual``    a by-hand deck: no window, nothing scheduled.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Any, Literal

from app.next_update import (
    MODE_CONNECTED,
    MODE_SLEEPS,
    Hold,
    Reach,
    _checkin_at_or_after,
    compute_next_update,
    pause_hold_at,
)
from app.quiet_hours import QuietHoursWindow, is_in_window

DAY_MIN = 1440
WEEK_MIN = 7 * DAY_MIN

DAY_ABBR: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_LETTER: tuple[str, ...] = ("M", "T", "W", "T", "F", "S", "S")
DAY_PLURAL: tuple[str, ...] = (
    "Mondays",
    "Tuesdays",
    "Wednesdays",
    "Thursdays",
    "Fridays",
    "Saturdays",
    "Sundays",
)
ALL_DAYS: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)

LineupKind = Literal["cycle", "interval", "daily", "manual"]
LineupStatus = Literal["live", "later_today", "not_today", "off"]
SegmentKind = Literal["window", "hold", "push"]
PillKind = Literal["now", "queued", "next"]
PrimaryAction = Literal["skip", "play", "push"]


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemInfo:
    """One item inside a multi-item dashboard (a gallery photo, a quote)."""

    index: int
    label: str
    thumb_url: str = ""


@dataclass(frozen=True)
class DashboardInfo:
    """A dashboard (page) as the Lineups page shows it. ``width``/``height``
    are the size it renders at (0 = unknown, fall back to the display's).
    ``item_count`` > 1 marks a dashboard that shows one of N items per
    visit; ``items`` lists them when known. ``composition_digest`` pins the
    last-sent picture used by the thumbnail and the step's Push now action."""

    id: str
    name: str
    thumb_url: str = ""
    width: int = 0
    height: int = 0
    item_count: int = 1
    items: tuple[ItemInfo, ...] = ()
    composition_digest: str | None = None
    missing: bool = False  # a lineup step still points at a deleted dashboard
    #: displays it is bound to, and "Name (WxH)" for each, for the off-display flag
    bound_ids: tuple[str, ...] = ()
    bound_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepInput:
    dashboard_id: str
    dwell_minutes: int = 0
    conditional: bool = False


@dataclass(frozen=True)
class RuntimeStep:
    """The scheduler's own idea of a live cycle lineup's step (force-step
    overrides and conditions can move it off the plain schedule). Epochs."""

    step_index: int
    since: float | None = None
    next_at: float | None = None


@dataclass(frozen=True)
class LineupUrls:
    edit: str = ""
    toggle: str = ""
    delete: str = ""
    fire: str = ""
    play: str = ""
    push_step: str = ""
    window: str = ""
    days: str = ""
    bind: str = ""


@dataclass(frozen=True)
class LineupInput:
    """One lineup (deck). ``start``/``end`` are ``HH:MM``; see the module
    docstring for what they mean per ``kind``. ``interval_minutes`` is the
    cadence of an ``interval`` lineup (ignored otherwise)."""

    id: str
    name: str
    kind: LineupKind
    steps: tuple[StepInput, ...]
    enabled: bool = True
    days: tuple[int, ...] = ALL_DAYS
    start: str | None = None
    end: str | None = None
    interval_minutes: int = 0
    runtime: RuntimeStep | None = None
    playing: bool = False  # a by-hand deck the display is on right now
    urls: LineupUrls = field(default_factory=LineupUrls)
    priority: int = 0  # wins a tie with another lineup due at the same moment
    last_fired: float | None = None  # epoch of an interval lineup's last fire
    #: the lineup names its display(s), so a step bound elsewhere cannot show
    explicit_display: bool = False


@dataclass(frozen=True)
class PauseInput:
    """Quiet hours (quiet hours) in day minutes; ``days`` are the weekdays
    the window starts on, ``all_day`` weekdays quiet from midnight to
    midnight. ``start > end`` wraps past midnight."""

    start_min: int
    end_min: int
    days: tuple[int, ...] = ALL_DAYS
    all_day: tuple[int, ...] = ()


@dataclass(frozen=True)
class OnScreenInput:
    dashboard_id: str
    since: float | None = None  # epoch the frame went up / was fetched
    verb: str = "sent"


@dataclass(frozen=True)
class FrameInput:
    """The frame on a display that no dashboard explains (sent by hand, an
    upload, a webpage): ``label`` names it (``a frame sent by hand``),
    ``thumb_url`` is its immutable thumbnail (empty when the image is gone)
    and ``at`` the epoch it was served or sent."""

    label: str
    thumb_url: str = ""
    at: float | None = None


@dataclass(frozen=True)
class PublishedInput:
    """The frame the server last published for the display (its latest
    render): what a sleeping display picks up at its next check-in."""

    dashboard_id: str
    at: float | None = None  # epoch it was published


@dataclass(frozen=True)
class UpcomingInput:
    """One update the scheduler projects for the display
    (``app.device_upcoming.UpcomingEvent``): when, from which lineup, and
    which dashboard (``None`` when conditions leave it open)."""

    at: float
    lineup_id: str | None = None
    dashboard_id: str | None = None


@dataclass(frozen=True)
class DisplayInput:
    id: str
    name: str
    width: int
    height: int
    reach: Reach
    lineups: tuple[LineupInput, ...]
    icon: str = ""
    spec: str = ""
    pause: PauseInput | None = None
    on_screen: OnScreenInput | None = None
    hold: Hold | None = None  # an active pause / automation pause
    # The scheduler's own projection, soonest first. ``None`` (tests, the
    # sample) falls back to the schedule model in this module.
    upcoming: tuple[UpcomingInput, ...] | None = None
    # What was last published to it. When known, "queued" means published but
    # not on the glass yet; ``None`` falls back to the schedule model.
    published: PublishedInput | None = None
    # True when ``published`` is authoritative even as ``None`` (the server
    # knows nothing was published), so nothing is reported queued.
    published_known: bool = False
    # The display's settings page (the gear next to its name).
    settings_url: str = ""
    gamut: str = "color"
    grey_levels: int = 0
    dither: str | None = None
    # What is on the glass when no dashboard names it; see ``FrameInput``.
    frame: FrameInput | None = None
    # True when the server knows nothing was ever rendered for the display.
    nothing_sent: bool = False
    # Epoch of the display's last heartbeat, and whether that is recent for
    # its wake interval (so it is not "waiting for check-in").
    last_seen: float | None = None
    heard: bool = False
    # True when a render exists that a pull/REST display has not fetched yet
    # (its latest digest differs from the last one served, or none was served).
    pending: bool = False


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


class _Dict:
    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)  # type: ignore[call-overload,no-any-return]


@dataclass
class NowView(_Dict):
    epoch: float
    iso: str
    weekday: int
    minute: int
    hhmm: str
    tz: str


@dataclass
class LinkView(_Dict):
    """How the display is reached. ``mode`` is ``connected`` / ``sleeps`` /
    ``unknown``; ``label`` is the header tag (``Online``, ``Wakes every 20
    min``)."""

    mode: str
    connected: bool
    live: bool
    label: str
    title: str
    checkin_minutes: int | None
    next_checkin: str | None


@dataclass
class PauseView(_Dict):
    start: str
    end: str
    label: str
    title: str
    start_min: int
    end_min: int
    days: list[bool]
    all_day: list[bool]


@dataclass
class ItemView(_Dict):
    index: int
    label: str
    thumb_url: str


@dataclass
class ScreenView(_Dict):
    """A dashboard as a thumbnail: on screen, queued or scheduled."""

    dashboard_id: str
    name: str
    thumb_url: str
    width: int
    height: int
    aspect: str  # CSS aspect-ratio, "1440/2560"
    orientation: Literal["p", "l"]
    since: str | None = None  # HH:MM it went up
    missing: bool = False


@dataclass
class FrameView(_Dict):
    """The header's stand-in for a frame no dashboard explains."""

    label: str
    thumb_url: str
    since: str | None
    title: str


@dataclass
class HeaderNoteView(_Dict):
    """What the header thumb says about delivery: the placeholder that stands
    in for a missing image (``state`` queued / nothing / unavailable /
    delivering / none) or the corner badge on a shown image (``queued``).
    ``title`` is the full sentence (tooltip and aria-label)."""

    state: str
    label: str
    sub: str
    title: str


@dataclass
class ScheduledView(_Dict):
    lineup_id: str
    lineup_name: str
    step_index: int
    steps_total: int
    dashboard_id: str
    name: str
    since: str
    holding: bool


@dataclass
class QueuedView(_Dict):
    """The scheduled step a sleeping display has not picked up yet."""

    dashboard_id: str
    name: str
    sent_at: str
    lands: str  # "~18:18"
    lands_epoch: float | None


@dataclass
class NextUpdateView(_Dict):
    """The header's next update: what lands, when and why.

    ``at_label`` is what the display gets it (``18:30`` connected, ``~18:18``
    at a check-in); ``text`` the honest sentence; ``from_label``/``pct``/
    ``in_label`` the progress from the previous event; ``then_text`` the one
    after, for a queued step."""

    dashboard_id: str
    name: str
    lineup_id: str | None
    at_epoch: float | None
    at_label: str
    push_at_label: str
    reason: str
    text: str
    warn: bool
    held: bool
    from_label: str
    pct: int
    in_label: str
    then_text: str | None


@dataclass
class NextLineView(_Dict):
    """The quiet line under "Showing X": ``before`` + bold ``at`` + ``after``."""

    before: str
    at: str
    after: str
    title: str


@dataclass
class TickView(_Dict):
    minute: int  # minute of the day
    pct: float  # position inside the segment, 0..100
    next: bool
    label: str = ""  # ``minute`` as the page reads it, see ``clock``
    # Only on the next tick: whole minutes and seconds until it fires, and the
    # "Update in ..." flag text (empty when it is a day or more away).
    in_min: int | None = None
    in_sec: int | None = None
    in_label: str = ""


@dataclass
class PointView(_Dict):
    """An advance on the day (lanes in the timeline view)."""

    minute: int
    step_index: int
    dashboard_id: str
    role: Literal["", "now", "waiting", "next"]


@dataclass
class SegmentStepView(_Dict):
    """One advance occurrence in a timeline block, including repeat visits."""

    index: int
    dashboard_id: str
    name: str
    thumb_url: str
    aspect: str
    at_min: int
    at_label: str
    current: bool
    next: bool


@dataclass
class SegmentView(_Dict):
    lineup_id: str
    lineup_name: str
    kind: SegmentKind
    start_min: int
    end_min: int
    left_pct: float
    width_pct: float
    live: bool
    planned: bool  # a window on a day other than today
    title: str
    label: str
    ticks: list[TickView]
    points: list[PointView]
    carried: ScreenView | None = None
    from_lineup_id: str | None = None
    from_prev_day: bool = False
    from_day_label: str | None = None
    predicted: bool = False
    is_current: bool = False
    steps: list[SegmentStepView] = field(default_factory=list)
    steps_total: int = 0


@dataclass
class PauseSegmentView(_Dict):
    start_min: int
    end_min: int
    left_pct: float
    width_pct: float
    title: str
    # Where this stretch lies under a lineup's own block (a lineup that runs
    # all day has no hold to show it): ``[{left_pct, width_pct}]``, drawn as a
    # hatched edge along the block so quiet hours are still visible.
    over: list[dict[str, float]] = field(default_factory=list)


@dataclass
class StartLabelView(_Dict):
    """A block's start time under the day bar, at the block's left edge."""

    minute: int
    pct: float
    label: str  # clock(minute): the page's clock style
    n: int  # label length: the scale font is monospace, so its width in ch
    hide: str  # "h0 h2": bar-width tiers (SCALE_TIERS) where it is dropped


@dataclass
class DayBarView(_Dict):
    weekday: int
    label: str  # "Wed"
    relative: str  # "today", "tomorrow", "yesterday", "in 3 days"
    is_today: bool
    now_min: int | None
    now_pct: float | None
    segments: list[SegmentView]
    pause: list[PauseSegmentView]
    aria_label: str
    absent: list[str]  # lineups that do not run that day: "Breakfast, runs Tue + Wed"
    now_label: str | None = None  # ``now_min`` as the page reads it, see ``clock``
    # Block start labels instead of the 00-24 axis; None (axis) for 1-2 blocks.
    starts: list[StartLabelView] | None = None


@dataclass
class WindowView(_Dict):
    start: str
    end: str | None  # None for a single push ("at 22:00")
    label: str  # "17:00–20:00" / "at 22:00"
    start_min: int
    end_min: int
    is_point: bool
    wraps: bool
    overlaps_pause: bool
    pause_title: str | None
    editable: bool


@dataclass
class ProgressView(_Dict):
    refreshes_today: int
    window_pct: int | None
    ends_in_minutes: int | None
    ends_in_label: str | None  # "1h 48m"
    starts_in_minutes: int | None
    starts_in_label: str | None  # "3h 48m"
    title: str


@dataclass
class NextRunView(_Dict):
    epoch: float
    week_minute: int
    hhmm: str
    weekday: int
    day_label: str  # "today", "tomorrow", "Sat"
    main: str  # "today 22:00", "Thu 06:30"
    sub: str  # "in 3 h 48 min", "tomorrow", "in 3 days"
    text: str  # "next Thu 06:30"
    lands: str  # when it reaches the display: "06:30", "~06:38"
    held: bool
    note: str


@dataclass
class PillView(_Dict):
    kind: PillKind
    label: str
    title: str


@dataclass
class StepView(_Dict):
    index: int
    position: str  # "3/6" or "only"
    dashboard_id: str
    name: str
    thumb_url: str
    width: int
    height: int
    aspect: str
    orientation: Literal["p", "l"]
    dwell_minutes: int
    conditional: bool
    item_count: int
    items: list[ItemView]
    current: bool  # the step the schedule says is up now
    next: bool
    queued: bool
    on_screen: bool
    pill: PillView | None
    separator: Literal["arrow", "loop", ""]
    push_title: str
    composition_digest: str | None = None
    missing: bool = False  # its dashboard was deleted; the scheduler cannot show it
    #: its dashboard is bound only to other displays, so this one never shows it
    off_display: bool = False
    off_display_note: str = ""  # "Bound to Frame (480×800)"


@dataclass
class LiveNowView(_Dict):
    """The current step, shown under the progress bar of a folded live row."""

    scheduled_name: str
    on_screen_name: str | None
    in_sync: bool
    lands: str | None


@dataclass
class ActionsView(_Dict):
    primary: PrimaryAction
    primary_label: str
    edit_url: str
    toggle_url: str
    delete_url: str
    fire_url: str
    play_url: str
    push_step_url: str
    window_url: str
    days_url: str
    can_push_step: bool
    bind_url: str = ""


@dataclass
class LineupView(_Dict):
    id: str
    name: str
    display_id: str
    kind: LineupKind
    badge: str  # "Rotation", "Daily", "Interval", "By hand"
    mode_label: str  # "Rotation", "Daily push", "Interval", "By hand"
    enabled: bool
    status: LineupStatus
    status_label: str  # "Live", "Later today", "Tue–Fri", "Weekends", "Off"
    holding: bool  # its last frame is held on the panel right now
    days: list[bool]
    today_index: int
    days_label: str
    days_text: str  # "every day" / "Mon, Tue, Wed"
    window: WindowView | None
    step_count: int
    step_minutes: int | None  # None when the steps' dwells differ
    step_label: str  # "30m"
    cycle_minutes: int
    cycle_label: str  # "3h"
    refreshes_today: int
    progress: ProgressView | None
    next_run: NextRunView | None
    live_now: LiveNowView | None
    folded: bool
    steps: list[StepView]
    actions: ActionsView
    sort_minutes: int  # minutes until it plays next (live = 0)
    warnings: list[str] = field(default_factory=list)
    display_removed: bool = False
    missing_count: int = 0  # steps whose dashboard was deleted; not in step_count


@dataclass
class DisplayView(_Dict):
    id: str
    name: str
    icon: str
    orientation: Literal["p", "l"]
    width: int
    height: int
    size_label: str  # "1440×2560"
    spec: str
    link: LinkView
    pause: PauseView | None
    refreshes_today: int
    on_screen: ScreenView | None
    scheduled: ScheduledView | None
    in_sync: bool
    queued: QueuedView | None
    next_update: NextUpdateView | None
    next_line: NextLineView
    show_title: str
    push_lands: str  # "lands now" / "lands ~18:18"
    selected_day: int
    day: DayBarView
    week: list[DayBarView]
    lineups: list[LineupView]
    # False when the server cannot tell what the glass holds (a sleeping
    # display that has not fetched the latest frame, nothing sent yet).
    on_screen_known: bool = True
    show_suffix: str = ""
    status_title: str = ""
    settings_url: str = ""
    gamut: str = "color"
    grey_levels: int = 0
    dither: str | None = None
    # With no dashboard on screen: the frame that is (``frame``), or the
    # honest "Nothing sent yet" (``nothing_sent``), or ``show_note`` tells
    # whether the display is quiet (", waiting for check-in") or was heard.
    frame: FrameView | None = None
    nothing_sent: bool = False
    show_note: str = ""
    # Additive: the dashed placeholder text when the header has no image, and
    # the "Queued" corner badge when a newer frame waits behind the shown one.
    placeholder: HeaderNoteView | None = None
    queued_badge: HeaderNoteView | None = None


@dataclass
class LineupsView(_Dict):
    now: NowView
    selected_day: int
    fold_default: bool
    lineup_count: int
    displays: list[DisplayView]
    unassigned: list[LineupView]
    schema_version: int = 1  # bumped only for a breaking change to this shape
    # The clock style every label here uses (``h12`` / ``h23``, see ``clock``);
    # the page's redraw formats with it too.
    time_cycle: str = "h23"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


_HHMM_RE = re.compile(r"([0-9]{1,2}):([0-9]{2})", re.ASCII)


def pause_label(p: PauseInput) -> str:
    """``23:00–06:30``; ``All day`` (or ``All day Sat, Sun``) for pause
    hours that are only whole days, which would otherwise read 00:00–00:00."""
    if not p.days and p.all_day:
        days = sorted(set(p.all_day))
        return "All day" if len(days) == 7 else "All day " + ", ".join(DAY_ABBR[d] for d in days)
    return clock_range(p.start_min, p.end_min)


def parse_hhmm(value: str | None) -> int | None:
    """``"06:30"`` -> 390; ``"24:00"`` -> 1440; anything else -> None.
    ASCII digits only (``str.isdigit`` also takes other scripts' digits)."""
    if not value or not isinstance(value, str):
        return None
    match = _HHMM_RE.fullmatch(value.strip())
    if match is None:
        return None
    h, m = int(match.group(1)), int(match.group(2))
    if h == 24 and m == 0:
        return DAY_MIN
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def hhmm(minutes: int) -> str:
    m = minutes % DAY_MIN
    return f"{m // 60:02d}:{m % 60:02d}"


def end_hhmm(minutes: int) -> str:
    """A window end: ``24:00`` rather than ``00:00`` at midnight."""
    return "24:00" if minutes > 0 and minutes % DAY_MIN == 0 else hhmm(minutes)


# The page's clock style (``app.locale_resolve.time_cycle``): ``h23`` reads
# ``18:30``, ``h12`` reads ``6:30 PM``. :func:`build_view` sets it for its
# build; labels read it through :func:`clock`. ``hhmm`` stays the canonical
# 24-hour data form (inputs, edit payloads, ``*.start`` / ``*.end``).
_TIME_CYCLE: ContextVar[str] = ContextVar("lineups_time_cycle", default="h23")


@contextmanager
def using_time_cycle(cycle: str) -> Iterator[None]:
    """Labels built inside read on ``cycle`` (``h12`` / ``h23``)."""
    token = _TIME_CYCLE.set("h12" if cycle == "h12" else "h23")
    try:
        yield
    finally:
        _TIME_CYCLE.reset(token)


def clock(minutes: int, *, end: bool = False, cycle: str | None = None) -> str:
    """A time label in the page's clock style. ``h23``: ``06:30``, with
    ``end`` a midnight end reads ``24:00``. ``h12``: ``6:30 AM``, ``11 PM``
    (whole hours drop ``:00``), noon ``12 PM``, midnight ``12 AM`` at either
    end, a narrow no-break space before AM/PM. Mirrors ``clock`` in
    static/lineups_render.js; change both together."""
    if (cycle or _TIME_CYCLE.get()) != "h12":
        return end_hhmm(minutes) if end else hhmm(minutes)
    h, m = divmod(minutes % DAY_MIN, 60)
    return f"{h % 12 or 12}{f':{m:02d}' if m else ''}\u202f{'AM' if h < 12 else 'PM'}"


def update_in_label(minutes: int) -> str:
    """The flag over the next advance: "Update in 5 min". Mirrors
    updateInLabel() in static/lineups_render.js; change both together. Empty
    from a day on, where it would only be noise."""
    if minutes >= DAY_MIN:
        return ""
    if minutes < 1:
        return "Update in under a minute"
    h, m = divmod(minutes, 60)
    if not h:
        return f"Update in {m} min"
    return f"Update in {h} h" + (f" {m} min" if m else "")


def clock_range(start: int, end: int) -> str:
    """``06:30–08:00`` / ``6:30 AM–8 AM``."""
    return f"{clock(start)}–{clock(end, end=True)}"


def rel_long(minutes: int) -> str:
    """``1 h 48 min`` / ``18 min`` / ``3 h``."""
    minutes = max(0, int(minutes))
    h, m = divmod(minutes, 60)
    if h:
        return f"{h} h" + (f" {m} min" if m else "")
    return f"{m} min"


def rel_short(minutes: int) -> str:
    """``1h 48m`` / ``18m`` / ``3h``."""
    return rel_long(minutes).replace(" min", "m").replace(" h", "h")


def dur_short(minutes: int) -> str:
    """Step / cycle chips: ``30m``, ``1h``, ``1h30``."""
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60}h"
    if minutes > 60:
        return f"{minutes // 60}h{minutes % 60:02d}"
    return f"{minutes}m"


def days_label(days: Sequence[int]) -> str:
    """The tag a not-today row wears: ``Daily``, ``Weekends``, ``Weekdays``,
    ``Mondays``, a run ``Tue–Fri``, else ``Mon Wed``."""
    ds = sorted(set(days))
    if len(ds) == 7:
        return "Daily"
    if not ds:
        return "No days"
    if ds == [5, 6]:
        return "Weekends"
    if ds == [0, 1, 2, 3, 4]:
        return "Weekdays"
    if len(ds) == 1:
        return DAY_PLURAL[ds[0]]
    if all(b == a + 1 for a, b in itertools.pairwise(ds)):
        return f"{DAY_ABBR[ds[0]]}–{DAY_ABBR[ds[-1]]}"
    return " ".join(DAY_ABBR[d] for d in ds)


def day_relative(day: int, today: int) -> str:
    diff = (day - today) % 7
    if diff == 0:
        return "today"
    if diff == 1:
        return "tomorrow"
    if diff == 6:
        return "yesterday"
    return f"in {diff} days"


def _aspect(w: int, h: int) -> tuple[str, Literal["p", "l"]]:
    if w <= 0 or h <= 0:
        return "16/10", "l"
    return f"{w}/{h}", ("p" if h > w else "l")


def _pct(minutes: float) -> float:
    return round(minutes / DAY_MIN * 100, 4)


# Bar widths, in the scale font's ch, at which start labels are laid out;
# CSS container queries pick the set for the bar's real width, no JS needed.
SCALE_TIERS = (*range(36, 97, 4), 108, 120)
_LABEL_GAP = 0.5  # ch between two labels


def start_labels(segments: Sequence[SegmentView]) -> list[StartLabelView] | None:
    """Start labels for a day bar with more than two blocks (windows and
    pushes; holds are filler). At each tier width the longest block, then the
    earliest, keeps its label; one that would touch a kept label is dropped
    there. A label too close to the end slides left to stay on the bar.
    static/lineups_render.js startLabels is the twin."""
    blocks = [s for s in segments if s.kind != "hold"]
    if len(blocks) <= 2:
        return None
    longest: dict[int, int] = {}
    for b in blocks:
        longest[b.start_min] = max(longest.get(b.start_min, 0), b.end_min - b.start_min)
    order = sorted(longest, key=lambda m: (-longest[m], m))
    text = {m: clock(m) for m in longest}
    hide: dict[int, list[str]] = {m: [] for m in longest}
    for i, w in enumerate(SCALE_TIERS):
        kept: list[tuple[float, float]] = []
        for m in order:
            n = len(text[m])
            left = min(m / DAY_MIN * w, w - n)
            if all(left >= b + _LABEL_GAP or left + n + _LABEL_GAP <= a for a, b in kept):
                kept.append((left, left + n))
            else:
                hide[m].append(f"h{i}")
    return [
        StartLabelView(m, _pct(m), text[m], len(text[m]), " ".join(hide[m]))
        for m in sorted(longest)
    ]


# ---------------------------------------------------------------------------
# Schedule model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Point:
    t: int  # week minute
    i: int  # step index


@dataclass
class _Block:
    lineup: LineupInput
    s: int
    e: int
    pts: list[_Point]
    push: bool
    hold: bool = False
    last: int = 0  # holds: the step index left on the panel
    after: _Block | None = None
    next: _Block | None = None


def _dwells(lineup: LineupInput) -> list[int]:
    return [max(1, s.dwell_minutes or 1) for s in lineup.steps]


def window_minutes(lineup: LineupInput) -> tuple[int, int] | None:
    """``(start, end)`` in day minutes, for showing the window: ``end`` may
    exceed 1440 for an interval window that wraps past midnight and equals
    ``start`` for a single push. ``None`` for a lineup with no time window
    (by hand). What actually runs is :func:`day_spans`."""
    if lineup.kind == "manual":
        return None
    if lineup.kind == "daily":
        s = parse_hhmm(lineup.start)
        return None if s is None else (s, s)
    if lineup.kind == "cycle":
        s = parse_hhmm(lineup.start) or 0
        e = parse_hhmm(lineup.end)
        # The engine treats before-anchor as dormant: an end at or before
        # the start stops at midnight (``_cycle_fires_on``).
        if e is None or e <= s:
            e = DAY_MIN
        return (s, e)
    s0 = parse_hhmm(lineup.start)
    e0 = parse_hhmm(lineup.end)
    if s0 is None and e0 is None:
        return (0, DAY_MIN)
    s = s0 if s0 is not None else 0
    e = DAY_MIN if e0 is None else e0
    return (s, e if e >= s else e + DAY_MIN)


def day_spans(lineup: LineupInput) -> list[tuple[int, int]]:
    """The ``[start, end)`` spans, in minutes of one calendar day, a lineup
    runs on each of its days. Mirrors the scheduler's gates: every weekday is
    judged on its own clock, so a Monday-only ``22:00``-``06:00`` interval
    window runs Monday 00:00-06:00 and Monday 22:00-24:00 and nothing on
    Tuesday (``device_upcoming._window_contains``); an interval window's end
    minute is inclusive; a cycle stops at its end (or midnight)."""
    if lineup.kind == "manual":
        return []
    if lineup.kind == "daily":
        s = parse_hhmm(lineup.start)
        return [] if s is None or s >= DAY_MIN else [(s, s)]
    if lineup.kind == "cycle":
        win = window_minutes(lineup)
        return [win] if win is not None else []
    s0 = parse_hhmm(lineup.start)
    e0 = parse_hhmm(lineup.end)
    if s0 is None and e0 is None:
        return [(0, DAY_MIN)]
    s = s0 if s0 is not None else 0
    if e0 is None:
        return [(s, DAY_MIN)]
    e = min(DAY_MIN, e0 + 1)
    if s <= e0:
        return [(s, e)]
    return [(0, e), (s, DAY_MIN)]


def _blocks_for(lineup: LineupInput, last_fired: int | None = None) -> list[_Block]:
    """The lineup's runs in week minutes (Monday 00:00 = 0). ``last_fired``
    (a week minute) re-anchors an interval lineup's fires on its cooldown:
    the scheduler fires an interval ``interval`` minutes after the last
    actual fire, not on a grid from the window start."""
    spans = day_spans(lineup)
    if not spans or not lineup.steps:
        return []
    n = len(lineup.steps)
    dwell = _dwells(lineup)
    out: list[_Block] = []
    for day in sorted(set(lineup.days)):
        for ws, we in spans:
            s = day * DAY_MIN + ws
            e = day * DAY_MIN + we
            pts: list[_Point] = []
            if lineup.kind == "daily":
                pts.append(_Point(s, 0))
            elif lineup.kind == "interval":
                step = max(1, lineup.interval_minutes or 1)
                t = s
                if last_fired is not None and s <= last_fired < e:
                    t = last_fired - ((last_fired - s) // step) * step
                while t < e and len(pts) < 2000:
                    pts.append(_Point(t, 0))
                    t += step
            else:
                t, i = s, 0
                while t < e and len(pts) < 2000:
                    pts.append(_Point(t, i % n))
                    t += dwell[i % n]
                    i += 1
            out.append(_Block(lineup, s, e, pts, push=lineup.kind == "daily"))
    return out


@dataclass
class _Model:
    blocks: list[_Block]
    holds: list[_Block]
    live_block: _Block | None
    live_hold: _Block | None


def _within(x: _Block, now_abs: int) -> bool:
    return (x.s <= now_abs < x.e) or (x.s <= now_abs + WEEK_MIN < x.e)


def _rank(lineup: LineupInput) -> int:
    """The scheduler's kind rank on a tie (``device_upcoming._coalesce``):
    a timed record over a cycle."""
    return 0 if lineup.kind == "cycle" else 2


def _last_fire_at(block: _Block, now_abs: int) -> int:
    off = 0 if block.s <= now_abs else -WEEK_MIN
    fired = [p.t + off for p in block.pts if p.t + off <= now_abs]
    return max(fired) if fired else block.s + off


def _block_key(b: _Block) -> tuple[int, int, str]:
    return (b.s, b.e, b.lineup.id)


def _build_model(lineups: Sequence[LineupInput], clk: _Clock | None = None) -> _Model:
    now_abs = clk.now_abs if clk is not None else 0
    blocks: list[_Block] = []
    for lineup in lineups:
        if not lineup.enabled:
            continue
        last = (
            clk.week_minute(lineup.last_fired)
            if clk is not None and lineup.last_fired is not None
            else None
        )
        blocks.extend(_blocks_for(lineup, last))
    blocks.sort(key=_block_key)
    holds: list[_Block] = []
    for i, b in enumerate(blocks):
        nx = blocks[(i + 1) % len(blocks)]
        ns = nx.s if i + 1 < len(blocks) else nx.s + WEEK_MIN
        if ns > b.e and b.pts:
            holds.append(
                _Block(
                    b.lineup,
                    b.e,
                    ns,
                    [],
                    push=False,
                    hold=True,
                    last=b.pts[-1].i,
                    after=b,
                    next=nx,
                )
            )
    # With overlapping windows the panel shows whichever fired last; a tie
    # goes the way the scheduler's tick does (priority, then kind, then id).
    live = [b for b in blocks if b.pts and _within(b, now_abs)]
    live_block = (
        max(
            live,
            key=lambda b: (
                _last_fire_at(b, now_abs),
                b.lineup.priority,
                _rank(b.lineup),
                b.lineup.id,
            ),
        )
        if live
        else None
    )
    live_hold = None if live_block else next((h for h in holds if _within(h, now_abs)), None)
    return _Model(blocks, holds, live_block, live_hold)


def _rel_now(t: int, now_abs: int) -> int:
    """A week minute as an offset from now, in [now - week, now + week)."""
    return t if t >= now_abs - WEEK_MIN // 2 else t + WEEK_MIN


@dataclass(frozen=True)
class _Scheduled:
    lineup: LineupInput
    index: int
    at: int  # week minute (may be < now or relative)
    hold: bool


def _live_step(block: _Block, now_abs: int) -> tuple[int, int, int | None]:
    """``(step index, since, next advance)`` for a live window, in week
    minutes relative to ``now_abs`` (the window may have started last week)."""
    off = 0 if block.s <= now_abs < block.e else -WEEK_MIN
    cur = block.pts[0]
    nxt: int | None = None
    for p in block.pts:
        if p.t + off <= now_abs:
            cur = p
        else:
            nxt = p.t + off
            break
    return cur.i, cur.t + off, nxt


def _scheduled_now(model: _Model, now_abs: int) -> _Scheduled | None:
    lb = model.live_block
    if lb is not None and lb.pts:
        idx, since, _ = _live_step(lb, now_abs)
        return _Scheduled(lb.lineup, idx, since, hold=False)
    lh = model.live_hold
    if lh is not None:
        since = lh.s if lh.s <= now_abs else lh.s - WEEK_MIN
        return _Scheduled(lh.lineup, lh.last, since, hold=True)
    return None


def _next_start(blocks: Sequence[_Block], now_abs: int) -> int | None:
    fut = sorted(b.s if b.s > now_abs else b.s + WEEK_MIN for b in blocks)
    return fut[0] if fut else None


def _next_fire(model: _Model, after: int) -> tuple[int, _Block, _Point] | None:
    """The first fire strictly after week minute ``after``; on a tie the
    record the scheduler's tick leaves on the panel (highest priority, then
    kind, then id)."""
    found: list[tuple[int, _Block, _Point]] = []
    for b in model.blocks:
        for off in (0, WEEK_MIN):
            p = next((p for p in b.pts if p.t + off > after), None)
            if p is not None:
                found.append((p.t + off, b, p))
                break
    if not found:
        return None
    first = min(t for t, _, _ in found)
    tied = [f for f in found if f[0] == first]
    return max(tied, key=lambda f: (f[1].lineup.priority, _rank(f[1].lineup), f[1].lineup.id))


def _last_push(model: _Model, now_abs: int) -> tuple[int, _Block] | None:
    """The most recent daily push at or before now (``(week minute, block)``,
    the minute possibly last week); on a tie the scheduler's winner."""
    found: list[tuple[int, _Block]] = []
    for b in model.blocks:
        if b.push:
            t = b.s if b.s <= now_abs else b.s - WEEK_MIN
            found.append((t, b))
    if not found:
        return None
    return max(
        found, key=lambda f: (f[0], f[1].lineup.priority, _rank(f[1].lineup), f[1].lineup.id)
    )


#: A Monday, so ``_REF_MONDAY + week minutes`` has the right weekday for the
#: quiet-hours predicates, which take a wall-clock datetime.
_REF_MONDAY = datetime(2024, 1, 1)


def _quiet_window(pause: PauseInput) -> QuietHoursWindow:
    a, b = pause.start_min % DAY_MIN, pause.end_min % DAY_MIN
    return QuietHoursWindow(
        time(a // 60, a % 60),
        time(b // 60, b % 60),
        frozenset(pause.days),
        frozenset(pause.all_day),
    )


def _pause_parts(pause: PauseInput | None, day: int) -> list[tuple[int, int]]:
    """Pause-hour spans on weekday ``day``, ``[start, end)`` in day minutes.
    The same rule as ``quiet_hours.is_in_window`` (a test holds the two
    together): each weekday on its own clock, end minute included."""
    if pause is None:
        return []
    if day in pause.all_day:
        return [(0, DAY_MIN)]
    if day not in pause.days:
        return []
    a, b = pause.start_min % DAY_MIN, pause.end_min % DAY_MIN
    if a <= b:
        return [(a, min(DAY_MIN, b + 1))]
    return [(0, b + 1), (a, DAY_MIN)]


def _pause_display_parts(pause: PauseInput | None, day: int) -> list[tuple[int, int]]:
    """Quiet hours as the page *draws and flags* them: :func:`_pause_parts`
    with the end treated as exclusive, so a pause "23:00–06:30" ends at 06:30
    on the bar and a lineup starting at 06:30 touches it without a one-minute
    sliver or a pause flag.

    Display only. The scheduler includes the end minute (a 06:30 push under
    that pause is held to 06:31), and everything that predicts delivery
    (``in_pause``, holds, next-run times) keeps that inclusive rule."""
    if pause is None:
        return []
    end_plus = pause.end_min % DAY_MIN + 1
    out: list[tuple[int, int]] = []
    for s, e in _pause_parts(pause, day):
        if day not in pause.all_day and e == end_plus:
            e -= 1  # drop the inclusive end minute
        if e > s:
            out.append((s, e))
    return out


def in_pause(pause: PauseInput | None, week_minute: int) -> bool:
    """Whether week minute ``week_minute`` is in quiet hours, by
    ``quiet_hours.is_in_window`` itself."""
    if pause is None:
        return False
    moment = _REF_MONDAY + timedelta(minutes=week_minute % WEEK_MIN)
    return is_in_window(_quiet_window(pause), moment, None)


def window_overlaps_pause(pause: PauseInput | None, lineup: LineupInput) -> bool:
    """Whether any of the lineup's runs (or its push) really falls in pause
    hours: an overlap of at least a full minute against the display spans
    (:func:`_pause_display_parts`), so merely starting on the minute pause
    hours end is not flagged."""
    if pause is None:
        return False
    for b in _blocks_for(lineup):
        day, base = (b.s // DAY_MIN) % 7, (b.s // DAY_MIN) * DAY_MIN
        parts = [(base + ps, base + pe) for ps, pe in _pause_display_parts(pause, day)]
        if b.push:
            if any(ps <= b.s < pe for ps, pe in parts):
                return True
        elif any(b.s < pe and b.e > ps for ps, pe in parts):
            return True
    return False


# ---------------------------------------------------------------------------
# Clash detection (inline edits)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Clash:
    other: LineupInput
    day: int

    def message_for_window(self) -> str:
        return f"Overlaps {self.other.name} on {DAY_ABBR[self.day]} ({window_label(self.other)})."

    def message_for_day(self) -> str:
        return f"{DAY_ABBR[self.day]} clashes with {self.other.name} ({window_label(self.other)})."


def window_label(lineup: LineupInput) -> str:
    win = window_minutes(lineup)
    if win is None:
        return "by hand"
    s, e = win
    if lineup.kind == "daily":
        return f"at {clock(s)}"
    return clock_range(s, e)


def _overlap(a: _Block, b: _Block) -> bool:
    ae = a.e if a.e > a.s else a.s + 1
    be = b.e if b.e > b.s else b.s + 1
    return a.s < be and ae > b.s


def find_clashes(candidate: LineupInput, others: Sequence[LineupInput]) -> list[Clash]:
    """Every (other lineup, day) where another enabled lineup meets
    ``candidate``'s runs. Each weekday is judged on its own, as the
    scheduler does (see :func:`day_spans`)."""
    out: list[Clash] = []
    seen: set[tuple[str, int]] = set()
    mine = _blocks_for(candidate)
    for other in others:
        if other.id == candidate.id or not other.enabled:
            continue
        for b in _blocks_for(other):
            for a in mine:
                key = (other.id, (a.s // DAY_MIN) % 7)
                if key not in seen and _overlap(a, b):
                    seen.add(key)
                    out.append(Clash(other, key[1]))
    return out


def find_clash(candidate: LineupInput, others: Sequence[LineupInput]) -> Clash | None:
    """The first of :func:`find_clashes`, or ``None``."""
    found = find_clashes(candidate, others)
    return found[0] if found else None


def clash_keys(
    lineups: Sequence[LineupInput], shares: Callable[[str, str], bool] | None = None
) -> set[tuple[str, str, int]]:
    """Every clashing pair in a whole snapshot as ``(id, id, weekday)`` with
    the ids sorted. ``shares(a, b)`` says whether two lineups play on a
    common display (default: all do). Comparing the sets for the snapshot
    before and after a batch of edits finds exactly the clashes the batch
    introduces, however many lineups moved."""
    blocks = {lu.id: _blocks_for(lu) for lu in lineups if lu.enabled and lu.kind != "manual"}
    ids = sorted(blocks)
    out: set[tuple[str, str, int]] = set()
    for i, a_id in enumerate(ids):
        for b_id in ids[i + 1 :]:
            if shares is not None and not shares(a_id, b_id):
                continue
            for a in blocks[a_id]:
                for b in blocks[b_id]:
                    if _overlap(a, b):
                        out.add((a_id, b_id, (a.s // DAY_MIN) % 7))
    return out


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


@dataclass
class _Clock:
    now: datetime
    tz: tzinfo
    origin: datetime  # Monday 00:00 local of the current week
    now_abs: int
    epoch: float

    def epoch_of(self, week_minute: int) -> float:
        """The instant of a week-minute wall time. A wall time the clocks
        pass twice (DST rollback) resolves to its first pass, except a
        future one whose first pass is already behind us: that takes the
        second, so a coming run is never placed in the past."""
        naive = self.origin.replace(tzinfo=None) + timedelta(minutes=week_minute)
        first = naive.replace(tzinfo=self.tz, fold=0).timestamp()
        if week_minute > self.now_abs and first < self.epoch:
            second = naive.replace(tzinfo=self.tz, fold=1).timestamp()
            if second > first:
                return second
        return first

    def week_minute(self, epoch: float) -> int:
        local = datetime.fromtimestamp(epoch, tz=self.tz).replace(tzinfo=None)
        delta = local - self.origin.replace(tzinfo=None)
        return math.floor(delta.total_seconds() / 60)

    def time_of(self, epoch: float) -> str:
        """``18:24`` (or ``6:24 PM``, see :func:`clock`)."""
        local = datetime.fromtimestamp(epoch, tz=self.tz)
        return clock(local.hour * 60 + local.minute)

    def clock(self, epoch: float) -> str:
        """``18:24`` today, ``Thu 06:30`` another day."""
        local = datetime.fromtimestamp(epoch, tz=self.tz)
        if local.date() == self.now.date():
            return self.time_of(epoch)
        return f"{DAY_ABBR[local.weekday()]} {self.time_of(epoch)}"


def _make_clock(now: datetime) -> _Clock:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    tz = now.tzinfo
    assert tz is not None
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    origin = midnight - timedelta(days=now.weekday())
    now_abs = now.weekday() * DAY_MIN + now.hour * 60 + now.minute
    return _Clock(now, tz, origin, now_abs, now.timestamp())


@dataclass
class _Lands:
    epoch: float | None
    label: str  # "18:30" connected / "~18:38" at a check-in
    held: bool
    text: str
    reason: str


def _lands(d: DisplayInput, clk: _Clock, push_abs: int) -> _Lands:
    """:func:`_lands_at` for a week-minute push time from the model."""
    return _lands_at(d, clk, clk.epoch_of(push_abs))


def _lands_at(d: DisplayInput, clk: _Clock, push_epoch: float) -> _Lands:
    """When a push at ``push_epoch`` reaches the display: held to the end of
    quiet hours (or an active pause), then delivered right away (connected)
    or at the first check-in at or after it (sleeps), via
    :func:`compute_next_update`. Works on the exact instant throughout; only
    the pause-hours test reads its wall clock."""
    hold = d.hold
    if hold is not None and hold.until is not None and hold.until <= push_epoch:
        hold = None  # an active pause that is over by then does not hold it
    if d.pause is not None:
        # The same pause-hours rule as ``DeviceTiming.view``, on the real
        # instant (DST included).
        pause_hold = pause_hold_at(_quiet_window(d.pause), max(push_epoch, clk.epoch), clk.tz)
        if pause_hold is not None and (
            hold is None
            or pause_hold.until is None
            or (hold.until is not None and hold.until < pause_hold.until)
        ):
            hold = pause_hold
    nu = compute_next_update(push_epoch, d.reach, now=clk.epoch, hold=hold)
    held = nu.held_until is not None or nu.held_indefinitely
    if nu.at is None:
        why = hold.reason if hold is not None else "the automation pause"
        return _Lands(None, "", held, f"Held by {why}.", "held")
    approx = nu.mode == MODE_SLEEPS and nu.checkin_at is not None
    label = ("~" if approx else "") + clk.clock(nu.at)
    if nu.held_until is not None and d.pause is not None and nu.held_reason == "quiet hours":
        p = d.pause
        text = f"Held until {clk.clock(nu.held_until)} by Quiet hours ({pause_label(p)})."
    elif nu.held_until is not None:
        text = f"Held until {clk.clock(nu.held_until)} by {nu.held_reason}."
    elif d.reach.mode == MODE_CONNECTED:
        text = (
            "Frame is connected, so it lands right away."
            if d.reach.live
            else "Display stays awake, so it lands on its next poll."
        )
    elif d.reach.mode == MODE_SLEEPS:
        text = "Display sleeps; it lands at the next check-in."
    else:
        text = "Lands when the display next asks for a frame."
    reason = "held" if held else nu.mode
    return _Lands(nu.at, label, held, text, reason)


def _header_notes(
    d: DisplayInput,
    clk: _Clock,
    on_screen: ScreenView | None,
    queued: QueuedView | None,
    connected: bool,
    in_sync: bool,
) -> tuple[HeaderNoteView | None, HeaderNoteView | None]:
    """``(placeholder, badge)`` for the header thumb.

    A frame waits when the display is a sleeper with a render it has not
    fetched (``d.pending``) or the schedule's step has not landed
    (``queued``). With an image to show the wait is a corner badge on it;
    without one the dashed box says what is going on."""
    waiting = not connected and (d.pending or queued is not None)
    if (
        connected
        and d.published is not None
        and not in_sync
        and (on_screen is None or not on_screen.thumb_url)
    ):
        # A live display handed a frame whose picture is not ready yet.
        return (
            HeaderNoteView(
                "delivering", "Delivering", "", "A frame is being delivered to the display now."
            ),
            None,
        )
    has_image = on_screen is not None or (d.frame is not None and bool(d.frame.thumb_url))
    when = ""
    if waiting:
        if queued is not None and queued.lands:
            when = queued.lands
        else:
            when = _lands_at(d, clk, clk.epoch).label
    sentence = (
        f"A new frame is queued. The frame picks it up at its next check-in {when}."
        if when
        else "A new frame is queued. The frame picks it up when it next checks in."
    )
    if has_image:
        if not waiting:
            return None, None
        return None, HeaderNoteView("queued", "Queued", "", sentence)
    if waiting:
        sub = (
            f"Frame picks it up at its next check-in {when}"
            if when
            else "when the frame next checks in"
        )
        return HeaderNoteView("queued", "Queued", sub, sentence), None
    if d.frame is not None:
        return (
            HeaderNoteView(
                "unavailable",
                "Image unavailable",
                "",
                "The frame on this display is known, but its image file is gone.",
            ),
            None,
        )
    if d.nothing_sent:
        return (
            HeaderNoteView(
                "nothing", "Nothing sent yet", "", "Nothing has been sent to this display yet."
            ),
            None,
        )
    return (
        HeaderNoteView("none", "No image yet", "", "No image is known for this display yet."),
        None,
    )


def _link_view(d: DisplayInput, clk: _Clock) -> LinkView:
    r = d.reach
    gap = round(r.gap_s / 60) if r.gap_s else None
    nxt = _checkin_at_or_after(r, clk.epoch) if r.mode == MODE_SLEEPS else None
    if r.mode == MODE_CONNECTED:
        label = "Online"
        title = (
            "Holds an open connection; changes land right away"
            if r.live
            else "Stays awake and polls; changes land on its next poll"
        )
    elif r.mode == MODE_SLEEPS:
        # The same wake time as the "next at" lines (reach.next_checkin_at is
        # the telemetry prediction, which is the device's own sleep_until when
        # it states one). A cadence is only named when it is a stable one.
        if r.next_checkin_at is not None and r.next_checkin_at > clk.epoch:
            at = clk.clock(r.next_checkin_at)
            same_day = at == clk.time_of(r.next_checkin_at)
            label = f"Wakes at {at}" if same_day else f"Wakes {at}"
            title = f"Sleeping; it wakes {'at' if same_day else 'on'} {at} and changes land then"
        elif gap and r.cadence_stable:
            label = f"Wakes every {gap} min"
            title = "Sleeps between check-ins; changes land when it wakes"
        else:
            label = "Sleeps"
            title = "Sleeps between check-ins; its next wake time is not known yet"
    elif d.heard:
        label = "Checked in"
        title = "Heard from recently; how often it checks in is not known yet"
    else:
        label = "Offline"
        title = "Nothing known about when it checks in"
    return LinkView(
        mode=r.mode,
        connected=r.mode == MODE_CONNECTED,
        live=r.live,
        label=label,
        title=title,
        checkin_minutes=gap,
        next_checkin=clk.clock(nxt) if nxt is not None else None,
    )


def _pause_view(p: PauseInput | None) -> PauseView | None:
    if p is None:
        return None
    label = pause_label(p)
    return PauseView(
        start=hhmm(p.start_min),
        end=end_hhmm(p.end_min),
        label=label,
        title=f"Quiet hours: nothing is pushed {label}",
        start_min=p.start_min,
        end_min=p.end_min,
        days=[i in p.days for i in range(7)],
        all_day=[i in p.all_day for i in range(7)],
    )


def _screen(
    dash: DashboardInfo | None, dashboard_id: str, d: DisplayInput, since: str | None = None
) -> ScreenView:
    w = dash.width if dash and dash.width > 0 else d.width
    h = dash.height if dash and dash.height > 0 else d.height
    aspect, orient = _aspect(w, h)
    return ScreenView(
        dashboard_id=dashboard_id,
        name=MISSING_NAME if dash and dash.missing else dash.name if dash else dashboard_id,
        thumb_url=dash.thumb_url if dash else "",
        width=w,
        height=h,
        aspect=aspect,
        orientation=orient,
        since=since,
        missing=bool(dash and dash.missing),
    )


def _day_pieces(model: _Model, day: int) -> list[tuple[_Block, int, int, bool]]:
    """Blocks and holds clipped to weekday ``day``: ``(block, s, e, push)``
    in day minutes."""
    a, z = day * DAY_MIN, (day + 1) * DAY_MIN
    out: list[tuple[_Block, int, int, bool]] = []
    for x in [*model.blocks, *model.holds]:
        for off in (0, WEEK_MIN, -WEEK_MIN):
            s = x.s + off
            e = max(x.e + off, s)
            if x.push and a <= s < z:
                out.append((x, s - a, s - a, True))
            elif not x.push and e > a and s < z and e > s:
                out.append((x, max(s, a) - a, min(e, z) - a, False))
    out.sort(key=lambda p: (p[1], p[0].hold))
    return out


MISSING_NAME = "Missing dashboard"
MISSING_TITLE = "This dashboard was deleted; remove it from the lineup"


def _name(dashboards: Mapping[str, DashboardInfo], dashboard_id: str) -> str:
    dash = dashboards.get(dashboard_id)
    if dash and dash.missing:
        return MISSING_NAME
    return dash.name if dash else dashboard_id


def _next_name(dashboards: Mapping[str, DashboardInfo], dashboard_id: str) -> str:
    """A step's name in 'next' copy. The scheduler still tries a deleted
    dashboard when its turn comes, fails to push it and leaves the previous
    frame up, so say that rather than name it like a real dashboard."""
    dash = dashboards.get(dashboard_id)
    if dash and dash.missing:
        return f"{MISSING_NAME} (can't be shown)"
    return _name(dashboards, dashboard_id)


def _day_bar(
    d: DisplayInput,
    model: _Model,
    day: int,
    clk: _Clock,
    dashboards: Mapping[str, DashboardInfo],
    roles: Mapping[tuple[str, int], str],
    next_tick: int | None,
    screens: Mapping[str, ScreenView],
) -> DayBarView:
    today = clk.now_abs // DAY_MIN
    is_today = day == today
    segs: list[SegmentView] = []
    for x, s, e, push in _day_pieces(model, day):
        lineup = x.lineup
        live_now = is_today and (x is model.live_block or x is model.live_hold)
        carried: ScreenView | None = None
        from_prev_day = False
        from_day_label: str | None = None
        if x.hold:
            last = lineup.steps[x.last].dashboard_id if lineup.steps else ""
            carried = screens.get(last)
            if x.after is not None:
                # Find this clipped piece's occurrence, including Sunday-to-
                # Monday and holds spanning several days without a new run.
                offset = (day * DAY_MIN + s - x.s) // WEEK_MIN * WEEK_MIN
                source_day = (x.after.s + offset) // DAY_MIN
                from_prev_day = source_day < day
                if from_prev_day:
                    from_day_label = DAY_ABBR[source_day % 7]
            nxt = x.next.lineup.name if x.next else ""
            title = f"{lineup.name}: keeps {_name(dashboards, last)} on the panel until {nxt}"
            kind: SegmentKind = "hold"
        elif push:
            title = f"{lineup.name} at {clock(s)}"
            kind = "push"
        else:
            title = f"{lineup.name} {window_label(lineup)}"
            kind = "window"
        ticks: list[TickView] = []
        points: list[PointView] = []
        if not x.hold:
            base = day * DAY_MIN
            for off in (0, WEEK_MIN, -WEEK_MIN):
                for p in x.pts:
                    m = p.t + off - base
                    if not (s <= m <= e) or (m == e and not push):
                        continue
                    abs_t = p.t + off
                    role = roles.get((lineup.id, abs_t), "") if is_today else ""
                    points.append(
                        PointView(
                            minute=m,
                            step_index=p.i,
                            dashboard_id=lineup.steps[p.i].dashboard_id,
                            role=role,  # type: ignore[arg-type]
                        )
                    )
                    if not push and s < m < e:
                        is_next = live_now and next_tick is not None and abs_t == next_tick
                        in_sec = (
                            max(0, int(clk.epoch_of(abs_t % WEEK_MIN) - clk.epoch))
                            if is_next
                            else None
                        )
                        in_min = in_sec // 60 if in_sec is not None else None
                        ticks.append(
                            TickView(
                                minute=m,
                                pct=round((m - s) / (e - s) * 100, 4),
                                next=is_next,
                                label=clock(m),
                                in_min=in_min,
                                in_sec=in_sec,
                                in_label=update_in_label(in_min) if in_min is not None else "",
                            )
                        )
        steps: list[SegmentStepView] = []
        for point in points[:60]:
            screen = screens[point.dashboard_id]
            steps.append(
                SegmentStepView(
                    index=point.step_index,
                    dashboard_id=point.dashboard_id,
                    name=screen.name,
                    thumb_url=screen.thumb_url,
                    aspect=screen.aspect,
                    at_min=point.minute,
                    at_label=clock(point.minute),
                    current=point.role in ("now", "waiting"),
                    next=point.role == "next",
                )
            )
        segs.append(
            SegmentView(
                lineup_id=lineup.id,
                lineup_name=lineup.name,
                kind=kind,
                start_min=s,
                end_min=e,
                left_pct=_pct(s),
                width_pct=0.4 if push else _pct(e - s),
                live=live_now,
                planned=not is_today and not x.hold,
                title=title,
                label="" if x.hold else lineup.name,
                ticks=ticks,
                points=points,
                carried=carried,
                from_lineup_id=lineup.id if x.hold else None,
                from_prev_day=from_prev_day,
                from_day_label=from_day_label,
                predicted=x.hold and day * DAY_MIN + s > clk.now_abs,
                is_current=live_now,
                steps=steps,
                steps_total=len(points),
            )
        )
    pause_segs: list[PauseSegmentView] = []
    if d.pause is not None:
        pz = d.pause
        ptitle = f"Quiet hours {pause_label(pz)}: nothing is pushed"
        for s, e in _pause_display_parts(pz, day):
            over: list[dict[str, float]] = []
            for sg in segs:
                if sg.kind != "window":
                    continue
                lo, hi = max(s, sg.start_min), min(e, sg.end_min)
                if hi > lo:
                    over.append({"left_pct": _pct(lo), "width_pct": _pct(hi - lo)})
            pause_segs.append(PauseSegmentView(s, e, _pct(s), _pct(e - s), ptitle, over))
    now_min = clk.now_abs % DAY_MIN if is_today else None
    names = [f"{sg.lineup_name} {clock(sg.start_min)}" for sg in segs if sg.kind != "hold"]
    present = {sg.lineup_id for sg in segs}
    absent = [
        f"{lu.name}, runs {' + '.join(DAY_ABBR[x] for x in sorted(lu.days))}"
        for lu in d.lineups
        if lu.enabled and lu.kind != "manual" and lu.id not in present
    ]
    return DayBarView(
        weekday=day,
        label=DAY_ABBR[day],
        relative=day_relative(day, today),
        is_today=is_today,
        now_min=now_min,
        now_pct=_pct(now_min) if now_min is not None else None,
        segments=segs,
        pause=pause_segs,
        aria_label=f"{DAY_ABBR[day]}: " + (", ".join(names) or "no lineups"),
        absent=absent,
        now_label=clock(now_min) if now_min is not None else None,
        starts=start_labels(segs),
    )


_BADGE: dict[str, tuple[str, str]] = {
    "cycle": ("Rotation", "Rotation"),
    "interval": ("Interval", "Interval"),
    "daily": ("Daily", "Daily push"),
    "manual": ("By hand", "By hand"),
}


def _step_meta(lineup: LineupInput) -> tuple[int | None, str, int, str]:
    if lineup.kind == "interval":
        step = max(1, lineup.interval_minutes or 1)
        return step, dur_short(step), step, dur_short(step)
    if lineup.kind != "cycle":
        return None, "", 0, ""
    dw = _dwells(lineup)
    cycle = sum(dw)
    if not dw:
        return None, "", 0, ""
    if len(set(dw)) == 1:
        return dw[0], dur_short(dw[0]), cycle, dur_short(cycle)
    return None, f"{dur_short(min(dw))}–{dur_short(max(dw))}", cycle, dur_short(cycle)


def build_display(
    d: DisplayInput,
    clk: _Clock,
    dashboards: Mapping[str, DashboardInfo],
    selected_day: int,
    fold_default: bool,
) -> DisplayView:
    now_abs = clk.now_abs
    model = _build_model(d.lineups, clk)
    lb = model.live_block
    lineup_names = {lu.id: lu.name for lu in d.lineups}

    # -- what the schedule says is up, and what the glass holds ------------
    sched = _scheduled_now(model, now_abs)
    runtime = lb.lineup.runtime if lb is not None else None
    next_adv: int | None = None
    if lb is not None:
        _, _, next_adv = _live_step(lb, now_abs)
    if sched is not None and runtime is not None and lb is not None and lb.lineup.steps:
        idx = runtime.step_index % len(lb.lineup.steps)
        since = clk.week_minute(runtime.since) if runtime.since is not None else sched.at
        sched = _Scheduled(sched.lineup, idx, since, hold=False)
        if runtime.next_at is not None:
            next_adv = clk.week_minute(runtime.next_at)

    # A daily push that fired after the live window's latest step owns the
    # panel (a noon override inside an hourly rotation); windows alone miss
    # it because a push has no duration.
    lp = _last_push(model, now_abs)
    if lp is not None and sched is not None and not sched.hold:
        t, pb = lp
        mine = (sched.lineup.priority, _rank(sched.lineup), sched.lineup.id)
        theirs = (pb.lineup.priority, _rank(pb.lineup), pb.lineup.id)
        if t > sched.at or (t == sched.at and theirs > mine):
            sched = _Scheduled(pb.lineup, 0, t, hold=True)

    sched_dash = sched.lineup.steps[sched.index].dashboard_id if sched else None
    connected = d.reach.mode == MODE_CONNECTED
    pub = d.published
    on_id: str | None = d.on_screen.dashboard_id if d.on_screen else None
    on_since: str | None = None
    if d.on_screen is not None and d.on_screen.since is not None:
        on_since = clk.clock(d.on_screen.since)
    if on_id is None and connected:
        # A connected display shows what was sent (or, failing that, what the
        # schedule says) right away.
        if pub is not None:
            on_id = pub.dashboard_id
            on_since = clk.clock(pub.at) if pub.at is not None else None
        elif sched_dash is not None and sched is not None and not d.published_known:
            on_id = sched_dash
            on_since = clock(sched.at)
    on_known = on_id is not None

    # Queued = published but not on the glass yet. With the publish state
    # known that is the whole truth; without it, the scheduled step stands in.
    queued: QueuedView | None = None
    queued_push: float | None = None
    queued_lineup: str | None = None
    if pub is not None or d.published_known:
        in_sync = on_id is not None and (pub is None or on_id == pub.dashboard_id)
        if pub is not None and not in_sync and not connected:
            queued_push = pub.at if pub.at is not None else clk.epoch
            if sched is not None and sched_dash == pub.dashboard_id:
                queued_lineup = sched.lineup.id
            lands = _lands_at(d, clk, max(queued_push, clk.epoch))
            queued = QueuedView(
                dashboard_id=pub.dashboard_id,
                name=_name(dashboards, pub.dashboard_id),
                sent_at=clk.time_of(queued_push),
                lands=lands.label,
                lands_epoch=lands.epoch,
            )
    else:
        # Unknown glass is never "in sync": a sleeping display that has not
        # fetched the scheduled frame is waiting for it.
        in_sync = sched_dash is None or (on_id is not None and on_id == sched_dash)
        if (
            not in_sync
            and not connected
            and sched is not None
            and sched_dash is not None
            and d.upcoming != ()
        ):
            queued_push = clk.epoch_of(sched.at)
            queued_lineup = sched.lineup.id
            lands = _lands_at(d, clk, max(queued_push, clk.epoch))
            queued = QueuedView(
                dashboard_id=sched_dash,
                name=_name(dashboards, sched_dash),
                sent_at=clock(sched.at),
                lands=lands.label,
                lands_epoch=lands.epoch,
            )

    # -- next update ---------------------------------------------------------
    # The scheduler's projection when there is one (it knows cooldowns,
    # overrides and which of several due records wins), else the model. Items
    # keep the exact epoch; week minutes are only for drawing the bar.
    Item = tuple[float, str | None, str | None]  # (epoch, dashboard, lineup)
    upcoming: Item | None = None
    following: Item | None = None  # strictly after now
    if d.upcoming is not None:
        for ev in d.upcoming:
            item = (ev.at, ev.dashboard_id, ev.lineup_id)
            if upcoming is None and (
                ev.at > clk.epoch or (ev.dashboard_id is not None and ev.dashboard_id != on_id)
            ):
                upcoming = item
            if following is None and ev.at > clk.epoch:
                following = item
    if d.upcoming is None:
        # No projection to ask (unavailable, or tests and the sample): the
        # schedule model stands in. A projection that ran and came back empty
        # is an answer, not a gap: the scheduler sees nothing it will deliver
        # in its horizon (a daily fire inside quiet hours is skipped, not
        # retried), so the header says so instead of promising a model time.
        nf = _next_fire(model, now_abs)
        if nf is not None:
            t, b, p = nf
            item = (clk.epoch_of(t), b.lineup.steps[p.i].dashboard_id, b.lineup.id)
            upcoming = upcoming or item
            following = following or item

    def label_of(item: Item) -> str:
        if item[1] is not None:
            return _next_name(dashboards, item[1])
        return lineup_names.get(item[2] or "", "") or "An update"

    next_update: NextUpdateView | None = None
    if queued is not None and queued_push is not None:
        lands = _lands_at(d, clk, max(queued_push, clk.epoch))
        gap = d.reach.gap_s
        if lands.epoch is not None and gap:
            from_epoch = lands.epoch - gap
            from_label = f"{clk.clock(from_epoch)} check-in"
        else:
            from_epoch = queued_push
            from_label = queued.sent_at
        then = None
        if following is not None:
            t_lands = _lands_at(d, clk, following[0])
            then = (
                f"Then {label_of(following)} at {clk.time_of(following[0])}, lands {t_lands.label}"
            )
        next_update = _next_update_view(
            clk,
            dashboard_id=queued.dashboard_id,
            name=queued.name,
            lineup_id=queued_lineup,
            push_epoch=queued_push,
            lands=lands,
            text=(
                f"Display sleeps between check-ins. {queued.name} went out "
                f"at {queued.sent_at} and lands at the next check-in."
            ),
            from_epoch=from_epoch,
            from_label=from_label,
            then=then,
        )
    elif upcoming is not None:
        lands = _lands_at(d, clk, upcoming[0])
        if sched is not None:
            from_epoch, from_label = clk.epoch_of(sched.at), clock(sched.at)
        elif d.on_screen is not None and d.on_screen.since is not None:
            from_epoch, from_label = d.on_screen.since, clk.clock(d.on_screen.since)
        else:
            from_epoch, from_label = clk.epoch, clock(now_abs)
        next_update = _next_update_view(
            clk,
            dashboard_id=upcoming[1] or "",
            name=label_of(upcoming),
            lineup_id=upcoming[2],
            push_epoch=upcoming[0],
            lands=lands,
            text=lands.text,
            from_epoch=from_epoch,
            from_label=from_label,
            then=None,
        )

    on_screen = _screen(dashboards.get(on_id), on_id, d, on_since) if on_id is not None else None
    scheduled_view: ScheduledView | None = None
    if sched is not None and sched_dash is not None:
        scheduled_view = ScheduledView(
            lineup_id=sched.lineup.id,
            lineup_name=sched.lineup.name,
            step_index=sched.index,
            steps_total=len(sched.lineup.steps),
            dashboard_id=sched_dash,
            name=_name(dashboards, sched_dash),
            since=clock(sched.at),
            holding=sched.hold,
        )

    # Header copy -------------------------------------------------------------
    if sched is not None and sched_dash is not None:
        n = len(sched.lineup.steps)
        if in_sync:
            tail = f", on screen since {on_since}" if on_since else ""
        elif on_id is None:
            tail = f". Scheduled since {clock(sched.at)}; not known yet what the display shows"
        else:
            tail = (
                f". Scheduled since {clock(sched.at)}; {_name(dashboards, on_id)} "
                f"on screen since {on_since or '?'}"
            )
        show_title = f"{sched.lineup.name}, step {sched.index + 1} of {n}{tail}"
    else:
        show_title = f"On screen since {on_since}" if on_since else "Nothing known on screen"
    frame_view: FrameView | None = None
    show_note = ""
    nothing_sent = False
    if on_screen is None:
        if d.frame is not None:
            frame_since = clk.clock(d.frame.at) if d.frame.at is not None else None
            title = d.frame.label[:1].upper() + d.frame.label[1:]
            frame_view = FrameView(
                label=d.frame.label,
                thumb_url=d.frame.thumb_url,
                since=frame_since,
                title=f"{title}, on screen since {frame_since}" if frame_since else title,
            )
            show_title = frame_view.title
        elif d.nothing_sent:
            nothing_sent = True
            show_title = "Nothing has been sent to this display yet"
        elif connected:
            pass
        elif d.heard:
            show_note = (
                f", checked in {clk.clock(d.last_seen)}"
                if d.last_seen is not None
                else ", checked in recently"
            )
        else:
            show_note = ", waiting for check-in"
    placeholder, queued_badge = _header_notes(d, clk, on_screen, queued, connected, in_sync)
    next_title = (
        next_update.text + (f" {next_update.then_text}." if next_update.then_text else "")
        if next_update
        else ""
    )
    if next_update is None:
        next_line = NextLineView("No update scheduled", "", "", next_title)
    elif queued is not None:
        next_line = NextLineView(
            f"{queued.name} next, lands", queued.lands, "when it wakes", next_title
        )
    elif not in_sync and sched_dash is not None and pub is None and not d.published_known:
        next_line = NextLineView(
            f"{_next_name(dashboards, sched_dash)} next, lands",
            next_update.at_label,
            "",
            next_title,
        )
    else:
        next_line = NextLineView(
            f"{next_update.name} next at", next_update.at_label, "", next_title
        )

    if connected:
        push_lands = "lands now"
    else:
        lands_now = _lands_at(d, clk, clk.epoch)
        push_lands = f"lands {lands_now.label}" if lands_now.label else "lands later"

    # -- step roles on the live block (for the lanes) --------------------------
    roles: dict[tuple[str, int], str] = {}
    if lb is not None and sched is not None and sched.lineup is lb.lineup:
        roles[(lb.lineup.id, sched.at if sched.at >= 0 else sched.at + WEEK_MIN)] = (
            "waiting" if queued and queued.dashboard_id == sched_dash else "now"
        )
        if next_adv is not None:
            roles[(lb.lineup.id, next_adv % WEEK_MIN)] = "next"

    # -- lineups ---------------------------------------------------------------
    views: list[LineupView] = []
    for lineup in d.lineups:
        views.append(
            _lineup_view(
                d,
                lineup,
                model,
                clk,
                dashboards,
                sched=sched,
                next_adv=next_adv,
                on_id=on_id,
                queued=queued,
                fold_default=fold_default,
            )
        )
    views.sort(key=_lineup_sort_key)

    refreshes = sum(v.refreshes_today for v in views)
    w, h = d.width, d.height
    _, orient = _aspect(w, h)
    next_tick = next_adv % WEEK_MIN if next_adv is not None else None
    dashboard_ids = {step.dashboard_id for lineup in d.lineups for step in lineup.steps}
    screens = {pid: _screen(dashboards.get(pid), pid, d) for pid in dashboard_ids}
    week = [_day_bar(d, model, i, clk, dashboards, roles, next_tick, screens) for i in range(7)]
    return DisplayView(
        gamut=d.gamut,
        grey_levels=d.grey_levels,
        dither=d.dither,
        id=d.id,
        name=d.name,
        settings_url=d.settings_url,
        icon=d.icon or ("portrait" if orient == "p" else "landscape"),
        orientation=orient,
        width=w,
        height=h,
        size_label=f"{w}×{h}" if w and h else "",
        spec=d.spec or (f"{w}×{h}" if w and h else ""),
        link=_link_view(d, clk),
        pause=_pause_view(d.pause),
        refreshes_today=refreshes,
        on_screen=on_screen,
        scheduled=scheduled_view,
        in_sync=in_sync,
        queued=queued,
        next_update=next_update,
        next_line=next_line,
        show_title=show_title,
        push_lands=push_lands,
        selected_day=selected_day,
        day=week[selected_day % 7],
        week=week,
        lineups=views,
        on_screen_known=on_known,
        frame=frame_view,
        nothing_sent=nothing_sent,
        show_note=show_note,
        placeholder=placeholder,
        queued_badge=queued_badge,
    )


def _next_update_view(
    clk: _Clock,
    *,
    dashboard_id: str,
    name: str,
    lineup_id: str | None,
    push_epoch: float,
    lands: _Lands,
    text: str,
    from_epoch: float,
    from_label: str,
    then: str | None,
) -> NextUpdateView:
    at = lands.epoch
    pct = 0
    in_label = ""
    if at is not None:
        span = at - from_epoch
        if span > 0:
            pct = round(min(100.0, max(0.0, (clk.epoch - from_epoch) / span * 100)))
        mins = max(0, math.ceil((at - clk.epoch) / 60))
        in_label = f"in {'~' if lands.label.startswith('~') else ''}{rel_long(mins)}"
    return NextUpdateView(
        dashboard_id=dashboard_id,
        name=name,
        lineup_id=lineup_id,
        at_epoch=at,
        at_label=lands.label,
        push_at_label=clk.time_of(push_epoch),
        reason=lands.reason,
        text=text,
        warn=lands.held or lands.label.startswith("~"),
        held=lands.held,
        from_label=from_label,
        pct=pct,
        in_label=in_label,
        then_text=then,
    )


def _lineup_sort_key(v: LineupView) -> tuple[int, int, str]:
    if v.status == "off":
        return (2, 0, v.name.lower())
    live = v.status == "live" or v.holding
    return (0 if live else 1, v.sort_minutes, v.name.lower())


def _lineup_view(
    d: DisplayInput,
    lineup: LineupInput,
    model: _Model,
    clk: _Clock,
    dashboards: Mapping[str, DashboardInfo],
    *,
    sched: _Scheduled | None,
    next_adv: int | None,
    on_id: str | None,
    queued: QueuedView | None,
    fold_default: bool,
) -> LineupView:
    now_abs = clk.now_abs
    today = now_abs // DAY_MIN
    mine = [b for b in model.blocks if b.lineup is lineup]
    # Its own run in progress: with overlapping lineups more than one can be
    # running, though only the scheduler's winner holds the panel.
    lb = next((b for b in mine if b.pts and _within(b, now_abs)), None)
    live = lb is not None
    holding = model.live_hold is not None and model.live_hold.lineup is lineup
    later = any(now_abs < b.s < (today + 1) * DAY_MIN for b in mine)
    refreshes = sum(len(b.pts) for b in mine if b.s // DAY_MIN == today)
    badge, mode_label = _BADGE[lineup.kind]
    days_sorted = sorted(set(lineup.days))

    status: LineupStatus
    if not lineup.enabled:
        status, status_label = "off", "Off"
    elif live or (lineup.kind == "manual" and lineup.playing):
        status, status_label = "live", "Live"
    elif later:
        status, status_label = "later_today", "Later today"
    elif lineup.kind == "manual":
        status, status_label = "not_today", "By hand"
    else:
        status, status_label = "not_today", days_label(days_sorted)

    # -- window ------------------------------------------------------------
    win = window_minutes(lineup)
    window: WindowView | None = None
    if win is not None:
        s, e = win
        point = lineup.kind == "daily"
        overlaps = window_overlaps_pause(d.pause, lineup)
        p = d.pause
        window = WindowView(
            start=hhmm(s),
            end=None if point else end_hhmm(e),
            label=f"at {clock(s)}" if point else clock_range(s, e),
            start_min=s,
            end_min=e,
            is_point=point,
            wraps=e > DAY_MIN,
            overlaps_pause=overlaps,
            pause_title=(
                f"Part of this window falls in Quiet hours ({pause_label(p)}); "
                "nothing is pushed then"
                if overlaps and p is not None
                else None
            ),
            editable=True,
        )

    # -- next run ------------------------------------------------------------
    next_abs = _next_start(mine, now_abs)
    next_run: NextRunView | None = None
    if next_abs is not None and lineup.enabled:
        nday = (next_abs // DAY_MIN) % 7
        diff = next_abs // DAY_MIN - today
        if diff == 0:
            day_lbl = "today"
            main = f"today {clock(next_abs)}"
            sub = f"in {rel_long(next_abs - now_abs)}"
        elif diff == 1:
            day_lbl = "tomorrow"
            main = f"{DAY_ABBR[nday]} {clock(next_abs)}"
            sub = "tomorrow"
        else:
            day_lbl = DAY_ABBR[nday]
            main = f"{DAY_ABBR[nday]} {clock(next_abs)}"
            sub = f"in {diff} days"
        lands = _lands(d, clk, next_abs)
        schedule_only = d.upcoming == ()
        next_run = NextRunView(
            epoch=clk.epoch_of(next_abs),
            week_minute=next_abs,
            hhmm=hhmm(next_abs),
            weekday=nday,
            day_label=day_lbl,
            main=main,
            sub=sub,
            text=f"scheduled {main}" if schedule_only else f"next {main}",
            lands="" if schedule_only else lands.label,
            held=False if schedule_only else lands.held,
            note="Scheduled run only; no delivery projected." if schedule_only else lands.text,
        )

    # -- progress ----------------------------------------------------------
    progress: ProgressView | None = None
    if live and lb is not None:
        s_rel = lb.s if lb.s <= now_abs else lb.s - WEEK_MIN
        e_rel = lb.e if lb.s <= now_abs else lb.e - WEEK_MIN
        span = max(1, e_rel - s_rel)
        pct = round((now_abs - s_rel) / span * 100)
        ends = e_rel - now_abs
        hold = next((h for h in model.holds if h.after is lb), None)
        title = f"{refreshes} refreshes today. {pct}% through this window."
        if hold is not None and hold.next is not None and lineup.steps:
            last = lineup.steps[hold.last].dashboard_id
            title += (
                f" After {clock(lb.e % DAY_MIN or DAY_MIN, end=True)} {_name(dashboards, last)} "
                f"stays on the panel until {hold.next.lineup.name} at {clock(hold.e)}."
            )
        progress = ProgressView(
            refreshes_today=refreshes,
            window_pct=pct,
            ends_in_minutes=ends,
            ends_in_label=rel_short(ends),
            starts_in_minutes=None,
            starts_in_label=None,
            title=title,
        )
    elif status == "later_today" and next_abs is not None:
        progress = ProgressView(
            refreshes_today=refreshes,
            window_pct=None,
            ends_in_minutes=None,
            ends_in_label=None,
            starts_in_minutes=next_abs - now_abs,
            starts_in_label=rel_short(next_abs - now_abs),
            title=next_run.note if next_run else "",
        )

    # -- steps ---------------------------------------------------------------
    n = len(lineup.steps)
    intended = on_panel = nxt = -1
    next_at = ""
    if live and sched is not None and sched.lineup is lineup:
        intended = sched.index
        on_panel = next((i for i, s in enumerate(lineup.steps) if s.dashboard_id == on_id), -1)
        if next_adv is not None and lb is not None and next_adv < _rel_now(lb.e, now_abs):
            nxt = next(
                (p.i for p in lb.pts if p.t % WEEK_MIN == next_adv % WEEK_MIN),
                (intended + 1) % n,
            )
            next_at = clock(next_adv)
    steps: list[StepView] = []
    loops = lineup.kind == "cycle" and n > 1
    for i, st in enumerate(lineup.steps):
        dash = dashboards.get(st.dashboard_id)
        scr = _screen(dash, st.dashboard_id, d)
        pill: PillView | None = None
        if i == on_panel:
            pill = PillView("now", "Now", "On screen now")
        is_queued = (
            i == intended
            and intended != on_panel
            and queued is not None
            and queued.dashboard_id == st.dashboard_id
        )
        if is_queued and queued is not None:
            pill = PillView(
                "queued",
                f"Queued · {queued.lands}" if queued.lands else "Queued",
                "Sent; the display picks it up at its next check-in",
            )
        if i == nxt:
            pill = PillView("next", f"Next {next_at}", f"Next advance {next_at}")
        items = list(dash.items) if dash else []
        count = max(dash.item_count if dash else 1, len(items), 1)
        off_display = bool(
            lineup.explicit_display
            and dash
            and not dash.missing
            and dash.bound_ids
            and d.id not in dash.bound_ids
        )
        steps.append(
            StepView(
                index=i,
                position=f"{i + 1}/{n}" if n > 1 else "only",
                dashboard_id=st.dashboard_id,
                name=scr.name,
                thumb_url="" if off_display else scr.thumb_url,
                composition_digest=dash.composition_digest if dash else None,
                width=scr.width,
                height=scr.height,
                aspect=scr.aspect,
                orientation=scr.orientation,
                dwell_minutes=st.dwell_minutes,
                conditional=st.conditional,
                item_count=count,
                items=[ItemView(it.index, it.label, it.thumb_url) for it in items],
                current=i == intended,
                next=i == nxt,
                queued=is_queued,
                on_screen=i == on_panel,
                pill=pill,
                separator="arrow" if i < n - 1 else ("loop" if loops else ""),
                push_title=MISSING_TITLE if scr.missing else f"Push {scr.name} to {d.name} now",
                missing=scr.missing,
                off_display=off_display,
                off_display_note=(
                    "Bound to " + ", ".join(dash.bound_labels or dash.bound_ids)
                    if off_display and dash
                    else ""
                ),
            )
        )
    missing_count = sum(1 for sv in steps if sv.missing)

    live_now: LiveNowView | None = None
    if live and intended >= 0:
        sd = lineup.steps[intended].dashboard_id
        live_now = LiveNowView(
            scheduled_name=_name(dashboards, sd),
            on_screen_name=_name(dashboards, on_id) if on_id else None,
            in_sync=on_id is None or on_id == sd,
            lands=queued.lands if queued and queued.dashboard_id == sd else None,
        )

    step_min, step_lbl, cycle_min, cycle_lbl = _step_meta(lineup)
    folded = status in ("not_today", "off") or (fold_default and status == "later_today")
    if lineup.kind == "daily":
        primary: PrimaryAction = "push"
        primary_label = "Push now: send it to the display"
    elif live and lineup.kind == "cycle":
        primary, primary_label = "skip", "Advance to the next dashboard"
    else:
        primary, primary_label = "play", "Play now: start this lineup on the panel"
    u = lineup.urls
    sort_minutes = 0 if live else ((next_abs - now_abs) if next_abs is not None else WEEK_MIN)
    return LineupView(
        id=lineup.id,
        name=lineup.name,
        display_id=d.id,
        kind=lineup.kind,
        badge=badge,
        mode_label=mode_label,
        enabled=lineup.enabled,
        status=status,
        status_label=status_label,
        holding=holding,
        days=[i in lineup.days for i in range(7)],
        today_index=today,
        days_label=days_label(days_sorted),
        days_text="every day"
        if len(days_sorted) == 7
        else ", ".join(DAY_ABBR[x] for x in days_sorted),
        window=window,
        step_count=n - missing_count,
        missing_count=missing_count,
        step_minutes=step_min,
        step_label=step_lbl,
        cycle_minutes=cycle_min,
        cycle_label=cycle_lbl,
        refreshes_today=refreshes,
        progress=progress,
        next_run=next_run,
        live_now=live_now,
        folded=folded,
        steps=steps,
        actions=ActionsView(
            primary=primary,
            primary_label=primary_label,
            edit_url=u.edit,
            toggle_url=u.toggle,
            delete_url=u.delete,
            fire_url=u.fire,
            play_url=u.play,
            push_step_url=u.push_step,
            window_url=u.window,
            days_url=u.days,
            can_push_step=bool(lineup.steps),
            bind_url=u.bind,
        ),
        sort_minutes=sort_minutes,
    )


def resolve_day(raw: str | None, today: int) -> int:
    """The ``?day=`` parameter: a weekday ``0``-``6`` (``day=1`` is Tuesday),
    ``today``, ``tomorrow``, ``yesterday``, or an offset ``+1`` / ``-1``
    (send the plus encoded, ``day=%2B1``; ``?offset=1`` on the JSON route
    avoids it). An unencoded ``+1`` arrives decoded as `` 1`` and is read as
    the offset it meant, not as weekday 1. Anything else is today."""
    if raw is None:
        return today
    if raw[:1] == " " and raw.strip().isascii() and raw.strip().isdigit():
        raw = "+" + raw.strip()
    v = raw.strip().lower()
    if v in ("", "today"):
        return today
    if v == "tomorrow":
        return (today + 1) % 7
    if v == "yesterday":
        return (today - 1) % 7
    try:
        if v[0] in "+-":
            return (today + int(v)) % 7
        n = int(v)
    except ValueError:
        return today
    return n if 0 <= n <= 6 else today


def fold_by_default(displays: Sequence[DisplayInput]) -> bool:
    """More than one display has a multi-step lineup: later-today rows fold
    too, so the page reads as headers first."""
    busy = sum(1 for d in displays if any(len(lu.steps) > 1 for lu in d.lineups))
    return busy > 1


def build_view(
    displays: Sequence[DisplayInput],
    dashboards: Mapping[str, DashboardInfo],
    *,
    now: datetime,
    selected_day: int | None = None,
    unassigned: Sequence[LineupInput] = (),
    tz_name: str = "",
    time_cycle: str = "h23",
) -> LineupsView:
    """The whole Lineups page. ``now`` must be aware (local time); labels
    read on ``time_cycle`` (``h12`` / ``h23``, see :func:`clock`)."""
    clk = _make_clock(now)
    today = clk.now_abs // DAY_MIN
    day = today if selected_day is None else selected_day % 7
    fold = fold_by_default(displays)
    orphan = DisplayInput(
        id="",
        name="Not on a display yet",
        width=0,
        height=0,
        reach=Reach("unknown"),
        lineups=tuple(unassigned),
    )
    with using_time_cycle(time_cycle):
        views = [build_display(d, clk, dashboards, day, fold) for d in displays]
        orphans = build_display(orphan, clk, dashboards, day, fold).lineups if unassigned else []
        cycle = _TIME_CYCLE.get()
    count = sum(len(v.lineups) for v in views) + len(orphans)
    return LineupsView(
        now=NowView(
            epoch=clk.epoch,
            iso=clk.now.isoformat(),
            weekday=today,
            minute=clk.now_abs % DAY_MIN,
            hhmm=hhmm(clk.now_abs),
            tz=tz_name or (getattr(clk.tz, "key", "") or str(clk.tz)),
        ),
        selected_day=day,
        fold_default=fold,
        lineup_count=count,
        displays=views,
        unassigned=orphans,
        time_cycle=cycle,
    )


# ---------------------------------------------------------------------------
# Mockup sample
# ---------------------------------------------------------------------------

#: "Now" in the mockup: Wednesday 18:12 (2026-10-28 is a Wednesday).
SAMPLE_NOW = datetime(2026, 10, 28, 18, 12, tzinfo=UTC)

_SAMPLE_DASH: dict[str, str] = {
    "family": "Family",
    "newspaper": "Newspaper",
    "art": "Art",
    "ainews": "AI News",
    "quotes": "Quotes",
    "comics": "Comics",
    "nightsky": "Night sky",
    "weather": "Weather",
    "calendar": "Calendar",
    "recipes": "Recipes",
    "bus": "Bus times",
}
_SAMPLE_ITEMS: dict[str, int] = {"quotes": 20, "art": 12, "recipes": 8}


def sample_inputs() -> tuple[list[DisplayInput], dict[str, DashboardInfo]]:
    """The mockup's demo data as builder inputs."""

    def at(hh: int, mm: int) -> float:
        return SAMPLE_NOW.replace(hour=hh, minute=mm).timestamp()

    dashboards = {
        k: DashboardInfo(
            id=k,
            name=v,
            item_count=_SAMPLE_ITEMS.get(k, 1),
            items=tuple(
                ItemInfo(i, f"{v} {i + 1} of {_SAMPLE_ITEMS[k]}")
                for i in range(_SAMPLE_ITEMS.get(k, 0))
            ),
        )
        for k, v in _SAMPLE_DASH.items()
    }

    def cyc(
        id_: str,
        name: str,
        days: tuple[int, ...],
        start: str,
        end: str,
        step: int,
        steps: Sequence[str],
        cond: Sequence[str] = (),
    ) -> LineupInput:
        return LineupInput(
            id=id_,
            name=name,
            kind="cycle",
            days=days,
            start=start,
            end=end,
            steps=tuple(StepInput(s, step, s in cond) for s in steps),
            urls=_sample_urls(id_),
        )

    frame = DisplayInput(
        id="frame",
        settings_url="/settings/devices/frame",
        name="Visionect frame",
        gamut="grey16",
        grey_levels=16,
        width=1440,
        height=2560,
        icon="portrait",
        spec="32in portrait · 1440×2560",
        reach=Reach(MODE_CONNECTED, live=True),
        pause=PauseInput(23 * 60, 6 * 60 + 30),
        on_screen=OnScreenInput("family", at(18, 0)),
        published=PublishedInput("family", at(18, 0)),
        lineups=(
            cyc(
                "dinner",
                "Dinner (Mon–Wed)",
                (0, 1, 2),
                "17:00",
                "20:00",
                30,
                ["newspaper", "art", "family", "ainews", "quotes", "comics"],
                ["art", "comics"],
            ),
            LineupInput(
                id="night",
                name="Night",
                kind="daily",
                start="22:00",
                steps=(StepInput("nightsky"),),
                urls=_sample_urls("night"),
            ),
            cyc(
                "breakfast",
                "Breakfast",
                (1, 2, 3, 4),
                "06:30",
                "09:30",
                15,
                ["family", "newspaper", "quotes", "ainews"],
            ),
            cyc(
                "weekend",
                "Morning (weekend)",
                (5, 6),
                "08:00",
                "11:00",
                36,
                ["art", "comics", "quotes", "family", "newspaper"],
                ["comics"],
            ),
            cyc(
                "monday",
                "Morning (Mon)",
                (0,),
                "06:00",
                "09:00",
                60,
                ["family", "newspaper", "ainews"],
            ),
        ),
    )
    kitchen = DisplayInput(
        id="kitchen",
        settings_url="/settings/devices/kitchen",
        name="Kitchen display",
        width=800,
        height=480,
        icon="landscape",
        spec="7.5in · 800×480",
        reach=Reach(MODE_SLEEPS, next_checkin_at=at(18, 18), gap_s=1200),
        pause=PauseInput(22 * 60 + 30, 6 * 60),
        on_screen=OnScreenInput("bus", at(17, 58)),
        published=PublishedInput("weather", at(18, 0)),
        lineups=(
            cyc(
                "kitchen",
                "Kitchen all day",
                ALL_DAYS,
                "06:00",
                "22:30",
                30,
                ["weather", "calendar", "recipes", "bus"],
            ),
        ),
    )
    return [frame, kitchen], dashboards


def _sample_urls(lineup_id: str) -> LineupUrls:
    return LineupUrls(
        edit=f"/decks/{lineup_id}/edit",
        toggle=f"/decks/{lineup_id}/toggle",
        delete=f"/decks/{lineup_id}/delete",
        push_step=f"/decks/{lineup_id}/push-step",
        window=f"/decks/{lineup_id}/window",
        days=f"/decks/{lineup_id}/days",
    )


def sample_view_model(selected_day: int | None = None, time_cycle: str = "h23") -> LineupsView:
    """The mockup's demo page (Wednesday 18:12) through the real builder:
    a connected Visionect frame in sync showing Family with AI News next
    at 18:30, and a sleeping kitchen display showing Bus times with Weather
    queued to land ~18:18."""
    displays, dashboards = sample_inputs()
    return build_view(
        displays,
        dashboards,
        now=SAMPLE_NOW,
        selected_day=selected_day,
        tz_name="UTC",
        time_cycle=time_cycle,
    )


__all__ = [
    "ALL_DAYS",
    "DAY_ABBR",
    "DAY_LETTER",
    "DAY_MIN",
    "DAY_PLURAL",
    "SAMPLE_NOW",
    "SCALE_TIERS",
    "WEEK_MIN",
    "ActionsView",
    "Clash",
    "DashboardInfo",
    "DayBarView",
    "DisplayInput",
    "DisplayView",
    "FrameInput",
    "FrameView",
    "HeaderNoteView",
    "ItemInfo",
    "ItemView",
    "LineupInput",
    "LineupKind",
    "LineupStatus",
    "LineupUrls",
    "LineupView",
    "LineupsView",
    "LinkView",
    "LiveNowView",
    "NextLineView",
    "NextRunView",
    "NextUpdateView",
    "NowView",
    "OnScreenInput",
    "PauseInput",
    "PauseSegmentView",
    "PauseView",
    "PillKind",
    "PillView",
    "PointView",
    "PrimaryAction",
    "ProgressView",
    "PublishedInput",
    "QueuedView",
    "RuntimeStep",
    "ScheduledView",
    "ScreenView",
    "SegmentKind",
    "SegmentStepView",
    "SegmentView",
    "StartLabelView",
    "StepInput",
    "StepView",
    "TickView",
    "UpcomingInput",
    "WindowView",
    "build_display",
    "build_view",
    "clash_keys",
    "clock",
    "clock_range",
    "day_relative",
    "day_spans",
    "days_label",
    "dur_short",
    "end_hhmm",
    "find_clash",
    "find_clashes",
    "fold_by_default",
    "hhmm",
    "in_pause",
    "parse_hhmm",
    "pause_label",
    "rel_long",
    "rel_short",
    "resolve_day",
    "sample_inputs",
    "sample_view_model",
    "start_labels",
    "using_time_cycle",
    "window_label",
    "window_minutes",
    "window_overlaps_pause",
]
