"""Data for one device's battery chart card.

The Device batteries page renders one card per device, and the device page's
Power section embeds the same card for a single device. Both go through
:func:`build_battery_chart`, which returns everything the
``templates/_battery_chart.html`` macros need (see the docstring there) so no
caller re-derives series, wake dots, stats, gaps or whether predictions apply.

Wakes. A "wake" is a moment the device checked in. Every battery sample is a
heartbeat, so each one is a wake (heartbeats within the telemetry debounce of
each other are one wake). The steady heartbeat is deliberately not in the event
log (see ``transport_wiring.status_changed_meaningfully``), so the samples are
the wake timeline and the event log supplies the why.

Cause of a wake. :func:`attribute_wakes` combines two things:

* the device's own reason, from the heartbeat's event-log row (Visionect
  ``connect_reason``, a REST client's ``wake_reason`` / ``reset_reason``, an
  abnormal-reset report in ``diag.reset``). A reconnect (the previous connection
  ended badly) or a restart (reboot, power on, brownout, crash) always wins, and
  so does a button or touch wake;
* what the server did for that device just before: ``type="push"`` rows (and
  ``type="touch"`` rows) whose ``extra.device_ids`` name it. A push is claimed
  by the first wake at or after it, so a push made while the device slept is
  picked up by its next wake, not by every later one.

The result is a category (``scheduled``, ``lineup``, ``ha``, ``device``,
``manual``, ``webhook``, ``reconnect``, ``problem`` (a restart), ``other``), a
one-line cause, an optional detail line and the raw device reason. A wake right
after a push (within :data:`DIRECT_S`) takes the push's cause; a Visionect "by
server request" wake takes it up to :data:`SERVER_REQUEST_S` later, and with no
push to credit it is the sign checking in because Tesserae, which manages its
sleep and wake, asked: a scheduled check-in. Any other wake is a scheduled
check-in too, and when a push was waiting it says what it picked up. A reason
Tesserae has no mapping for is shown as the device reported it.

Gaps. Spans with no samples for much longer than the device's usual interval
are returned in ``gaps`` so the chart can draw them as "no readings" rather
than as a measured line; one that overlaps the device's quiet hours is labelled
asleep, the rest offline. Nothing is interpolated: the readings either side are
real, the prediction fits only real samples.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import bisect
import re
import statistics
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from itertools import pairwise
from typing import Any

from app import visionect
from app.battery_offset import apply_to_pct
from app.quiet_hours import QuietHoursWindow, is_in_window
from app.state.battery_history import BatteryHistory, BatteryRow, Prediction
from app.state.device_telemetry import WAKE_DEBOUNCE_S
from app.state.event_log import EventLog, EventRow

# At most this many dots per chart. The window is cut into this many equal time
# buckets and each keeps one wake, so a device on a 1-minute cadence costs the
# same as one on an hourly cadence.
MAX_WAKE_DOTS: int = 120
# An event-log row is written just after the sample of the same heartbeat; this
# is how far apart the two may be and still count as one wake.
MATCH_TOLERANCE_S: float = 120.0
DEFAULT_LABEL: str = "Scheduled check-in"
DROPPED_LABEL: str = "Reconnected after a dropped connection"
# Fields of a parsed heartbeat that name why the device woke or connected, in
# priority order. ``connect_reason`` is Visionect's; the others are what REST
# firmware sends (unknown fields ride along in ``parsed``).
REASON_KEYS: tuple[str, ...] = (
    "connect_reason",
    "wake_reason",
    "wakeup_reason",
    "wake_cause",
    "reset_reason",
    "boot_reason",
)
# Longest raw reason kept, so a garbage field can't bloat the page.
MAX_REASON_CHARS: int = 48

# -- causes --------------------------------------------------------------

#: Category id -> legend name, in legend / breakdown order.
CATEGORIES: dict[str, str] = {
    "scheduled": "Scheduled check-in",
    "lineup": "Lineup update",
    "ha": "Home Assistant",
    "device": "Button or touch",
    "manual": "Manual push",
    "webhook": "Webhook / API",
    "reconnect": "Reconnected",
    "problem": "Restarted or crashed",
    "other": "Other",
}
#: The same, as it reads in "34 check-ins, 6 lineup, 2 HA".
_SHORT: dict[str, str] = {
    "scheduled": "check-ins",
    "lineup": "lineup",
    "ha": "HA",
    "device": "button/touch",
    "manual": "manual",
    "webhook": "webhook",
    "reconnect": "reconnects",
    "problem": "restarts",
    "other": "other",
}

#: Push sources that mean "someone sent this by hand" (deck_routes keeps the
#: Lineups page's own copy of this set).
MANUAL_SOURCES: frozenset[str] = frozenset(
    {
        "page",
        "manual",
        "file",
        "url",
        "webpage",
        "note",
        "resend",
        "onboarding",
        "companion",
        "device_push_now",
    }
)
LINEUP_SOURCES: frozenset[str] = frozenset(
    {
        "deck",
        "deck_init",
        "rotation",
        "scheduler",
        "page_refresh",
        "follow-up",
        "widget_change",
        "reconcile",
    }
)
WEBHOOK_SOURCES: frozenset[str] = frozenset({"webhook", "api"})
#: Sources that never reach a panel (background renders): not a cause.
_NON_CAUSE_SOURCES: frozenset[str] = frozenset({"deck_warm", "album_warm"})
#: A press / tap counts as a cause whatever became of it; anything else only
#: when something was actually delivered.
_PRESS_SOURCES: frozenset[str] = frozenset({"button", "touch"})
_DELIVERED: frozenset[str] = frozenset({"sent", "ok"})

# A push is claimed by the first wake at or after it, allowing the push row to
# be written a little after the heartbeat that asked for it (a button press is
# logged while its wake is already open).
PUSH_AFTER_S: float = 30.0
#: Furthest back a sleeping device's wake still counts as picking a push up.
PICKUP_MAX_S: float = 86_400.0
#: A wake this soon after a push took that push's cause.
DIRECT_S: float = 90.0
#: A "by server request" connection is the answer to something the server did.
SERVER_REQUEST_S: float = 300.0

# Normalised reason token -> label. Tokens are lowercased with runs of spaces,
# dashes and slashes folded to "_". Visionect's names come from pyvisionect's
# CONNECT_REASON table; the ESP-IDF ones from its wake-cause / reset-reason names.
_LABELS: dict[str, str] = {
    **dict.fromkeys(
        (
            "heartbeat",
            "timer",
            "timer_wake",
            "rtc",
            "deepsleep",
            "deep_sleep",
            "scheduled",
            "wakeup",
            "wake",
        ),
        DEFAULT_LABEL,
    ),
    **dict.fromkeys(("unknown", "n/a", "na", "none", "undefined"), DEFAULT_LABEL),
    **dict.fromkeys(("button", "ext0", "ext1", "gpio", "gpio_wake", "ext_button"), "Button"),
    **dict.fromkeys(("touch", "touchpad"), "Touch"),
    **dict.fromkeys(("by_server_request", "server_request", "server"), "Server request"),
    "server_changes": "Server changes",
    **dict.fromkeys(("wifi", "wifi_reconnect", "wi_fi_reconnect", "reconnect"), "Wi-Fi reconnect"),
    # The previous connection ended badly (often the server dropping a stale
    # one, or a Wi-Fi hiccup) and the device came back.
    **dict.fromkeys(
        ("connection_error", "connection_driver_error", "protocol_error"), DROPPED_LABEL
    ),
    **dict.fromkeys(("poweron", "power_on", "power_on_reset", "por", "cold_boot"), "Power on"),
    **dict.fromkeys(("reboot", "restart", "sw", "software", "software_reset"), "Reboot"),
    "brownout": "Brownout",
    **dict.fromkeys(("panic", "exception", "crash"), "Crash"),
    **dict.fromkeys(("watchdog", "wdt", "int_wdt", "task_wdt"), "Watchdog reset"),
    "logs_full": "Logs full",
    "command_line": "Command line",
    "display_driver_failed": "Display driver failed",
}
_TOKEN_SEP = re.compile(r"[\s\-/]+")
_RECONNECT_LABELS: frozenset[str] = frozenset({"Wi-Fi reconnect", DROPPED_LABEL})
_PROBLEM_LABELS: frozenset[str] = frozenset(
    {"Reboot", "Power on", "Brownout", "Crash", "Watchdog reset", "Display driver failed"}
)
_DEVICE_LABELS: frozenset[str] = frozenset({"Button", "Touch"})
_SERVER_LABELS: frozenset[str] = frozenset({"Server request", "Server changes"})

# -- gaps ----------------------------------------------------------------

#: A span with no samples counts as a gap when it is longer than this many
#: times the device's usual interval, and at least ``GAP_MIN_S``.
GAP_FACTOR: float = 2.5
GAP_MIN_S: float = 20 * 60.0
#: With fewer sample intervals than this the median is not trusted.
GAP_MIN_INTERVALS: int = 4


@dataclass(frozen=True)
class WakeEvents:
    """The event-log rows one device's wakes are explained from.

    ``status``: the device's heartbeat rows (``type="device"``). ``pushes``:
    ``push`` / ``touch`` rows that targeted it, oldest first. ``links``:
    ``scheduler`` / ``rotation`` / ``deck`` rows, which name the schedule or
    lineup behind a push (shared by every device on the page)."""

    status: Sequence[EventRow] = ()
    pushes: Sequence[EventRow] = ()
    links: Sequence[EventRow] = field(default_factory=tuple)


def is_battery_predictable(device: Any) -> bool:
    """Whether drain / reaches-X predictions are shown for ``device``.

    A Visionect frame shows the chart, the percentage and the sample count only.
    ``None`` (no registry entry) is predictable."""
    return not visionect.is_visionect_device(device)


def reason_label(raw: str | None) -> str:
    """Short readable label for a raw wake / connect reason.

    Known reasons map through :data:`_LABELS`; an empty or uninformative one
    ("unknown", "n/a") is a scheduled check-in; anything else shows as
    reported."""
    text = (raw or "").strip()
    if not text:
        return DEFAULT_LABEL
    token = _TOKEN_SEP.sub("_", text.lower())
    return _LABELS.get(token, text[:MAX_REASON_CHARS])


def reason_class(label: str) -> str:
    """How a device reason label behaves in attribution: ``reconnect``,
    ``problem`` (a restart), ``device`` (button / touch), ``server`` (the server
    asked it to connect), ``scheduled`` or ``other`` (reported as is)."""
    if label in _RECONNECT_LABELS:
        return "reconnect"
    if label in _PROBLEM_LABELS:
        return "problem"
    if label in _DEVICE_LABELS:
        return "device"
    if label in _SERVER_LABELS:
        return "server"
    if label == DEFAULT_LABEL:
        return "scheduled"
    return "other"


def event_reason(extra: dict[str, Any]) -> str | None:
    """The wake / connect reason an event-log row names, or ``None``.

    Looks at the parsed heartbeat first (:data:`REASON_KEYS`), then at an
    abnormal-reset report (``diag.reset``: brownout, panic, watchdog)."""
    parsed = extra.get("parsed")
    if isinstance(parsed, dict):
        for key in REASON_KEYS:
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:MAX_REASON_CHARS]
    diag = extra.get("diag")
    if isinstance(diag, dict):
        reset = diag.get("reset")
        if isinstance(reset, str) and reset.strip():
            return reset.strip()[:MAX_REASON_CHARS]
    return None


# -- wakes ---------------------------------------------------------------


def extract_wakes(
    series: Sequence[dict[str, Any]], events: Iterable[EventRow]
) -> list[dict[str, Any]]:
    """Every wake in the window, oldest first, before attribution and thinning.

    ``series`` is the chart line (``{t_ms, pct}``, oldest first); each point is a
    wake, except that points within :data:`WAKE_DEBOUNCE_S` of the previous one
    are the same wake. ``events`` are the device's status-heartbeat rows; each one
    that names a reason sets the reason of the nearest wake within
    :data:`MATCH_TOLERANCE_S`, or becomes a wake of its own. Returns
    ``{t_ms, pct, reason_label, reason, ...}`` dicts (``reason`` is the raw text,
    ``None`` for a plain check-in). Empty when there is no line to put dots on."""
    if not series:
        return []
    wakes: list[dict[str, Any]] = []
    debounce_ms = WAKE_DEBOUNCE_S * 1000.0
    for p in series:
        if wakes and p["t_ms"] - wakes[-1]["t_ms"] < debounce_ms:
            continue
        wakes.append(
            {
                "t_ms": p["t_ms"],
                "pct": p["pct"],
                "reason_label": DEFAULT_LABEL,
                "reason": None,
            }
        )
    times = [w["t_ms"] for w in wakes]
    tol_ms = MATCH_TOLERANCE_S * 1000.0
    extras: list[dict[str, Any]] = []
    for event in events:
        raw = event_reason(event.extra)
        if raw is None:
            continue
        label = reason_label(raw)
        t_ms = int(event.timestamp * 1000)
        idx = _nearest(times, t_ms)
        if idx is not None and abs(times[idx] - t_ms) <= tol_ms:
            target = wakes[idx]
            # A routine label never overwrites a reason already matched.
            if label != DEFAULT_LABEL or target["reason"] is None:
                target.update(reason_label=label, reason=raw)
            continue
        extras.append(
            {
                "t_ms": t_ms,
                "pct": _interpolate(series, t_ms),
                "reason_label": label,
                "reason": raw,
            }
        )
    if extras:
        wakes.extend(extras)
        wakes.sort(key=lambda w: w["t_ms"])
    return wakes


def is_wake_cause(row: EventRow) -> bool:
    """Whether a push / touch row can be why a device woke or what it picked
    up: a press always, anything else only when a frame was delivered."""
    if row.type not in ("push", "touch") or row.source in _NON_CAUSE_SOURCES:
        return False
    if row.source in _PRESS_SOURCES or row.type == "touch":
        return True
    return row.status in _DELIVERED


def push_cause(
    row: EventRow,
    *,
    links: Sequence[EventRow] = (),
    names: Mapping[str, str] | None = None,
    device_id: str = "",
) -> tuple[str, str]:
    """``(category, one-line cause)`` for a push / touch row, such as
    ``("ha", "Home Assistant: Next")`` or ``("lineup", "Lineup update: Morning")``."""
    source = row.source
    extra = row.extra
    if row.type == "touch" or source == "touch":
        return "device", "Touch"
    if source == "button":
        name = _clean(extra.get("button"))
        return "device", f"Button: {name}" if name else "Button"
    if source == "home_assistant":
        name = _clean(extra.get("button"))
        return "ha", f"Home Assistant: {name}" if name else "Home Assistant"
    if source in MANUAL_SOURCES:
        return "manual", "Manual push"
    if source in LINEUP_SOURCES:
        name = _lineup_name(row, links, names or {}, device_id)
        return "lineup", f"Lineup update: {name}" if name else "Lineup update"
    if source in WEBHOOK_SOURCES:
        return "webhook", "Webhook / API"
    return "other", f"Push ({source})" if source else "Push"


def attribute_wakes(
    wakes: list[dict[str, Any]],
    pushes: Sequence[EventRow] = (),
    *,
    links: Sequence[EventRow] = (),
    names: Mapping[str, str] | None = None,
    device_id: str = "",
) -> list[dict[str, Any]]:
    """Give each wake a ``cat`` (category id), ``label`` (the cause line) and
    ``detail`` (a second line or ``None``); see the module docstring for the
    rules. Mutates and returns ``wakes``."""
    times = [w["t_ms"] for w in wakes]
    claimed: dict[int, list[EventRow]] = {}
    for row in pushes:
        if not is_wake_cause(row):
            continue
        idx = bisect.bisect_left(times, int((row.timestamp - PUSH_AFTER_S) * 1000))
        if idx >= len(times) or times[idx] / 1000.0 - row.timestamp > PICKUP_MAX_S:
            continue
        claimed.setdefault(idx, []).append(row)

    for i, wake in enumerate(wakes):
        reason_lbl: str = wake["reason_label"]
        cls = reason_class(reason_lbl)
        rows = claimed.get(i, [])
        latest = rows[-1] if rows else None
        lag_s = (wake["t_ms"] / 1000.0 - latest.timestamp) if latest is not None else 0.0
        cause = (
            push_cause(latest, links=links, names=names, device_id=device_id)
            if latest is not None
            else None
        )
        cat, label, detail = "scheduled", DEFAULT_LABEL, None
        if cls in ("problem", "reconnect"):
            cat, label = cls, reason_lbl
            detail = f"Picked up {cause[1]}" if cause else None
        elif cls == "device":
            cat, label = "device", reason_lbl
            if cause and cause[0] == "device" and ": " in cause[1]:
                label = cause[1]
        elif cls == "server":
            if cause and lag_s <= SERVER_REQUEST_S:
                cat, label = cause
            elif cause:
                detail = f"Picked up {cause[1]}"
        elif cls == "other":
            cat, label = "other", reason_lbl
        elif cause and lag_s <= DIRECT_S:
            cat, label = cause
        elif cause:
            detail = f"Picked up {cause[1]}"
        wake.update(cat=cat, label=label, detail=detail)
    return wakes


def thin_wakes(
    wakes: Sequence[dict[str, Any]],
    *,
    start_ms: int,
    end_ms: int,
    max_dots: int = MAX_WAKE_DOTS,
) -> list[dict[str, Any]]:
    """Cap ``wakes`` at ``max_dots`` by keeping one per equal time bucket.

    A bucket keeps its last wake, except that a notable one (any category other
    than a scheduled check-in) beats routine check-ins in the same bucket, so a
    Power on does not vanish behind the heartbeat that follows it. Fewer wakes
    than ``max_dots`` pass through untouched."""
    if len(wakes) <= max_dots or max_dots < 1:
        return list(wakes)
    span = max(end_ms - start_ms, 1)
    kept: dict[int, dict[str, Any]] = {}
    for wake in wakes:
        bucket = min(max((wake["t_ms"] - start_ms) * max_dots // span, 0), max_dots - 1)
        held = kept.get(bucket)
        if held is not None and held["cat"] != "scheduled" and wake["cat"] == "scheduled":
            continue
        kept[bucket] = wake
    return [kept[b] for b in sorted(kept)]


def wake_stats(
    wakes: Sequence[dict[str, Any]], *, window_days: float, now_ms: int
) -> dict[str, Any]:
    """Wakes per day and a breakdown by cause, over the unthinned ``wakes``.

    The rate is the wake count over the time the device has been reporting
    inside the window (first wake to now, at least an hour, at most the window),
    so a device added yesterday is not averaged over thirty days. ``per_day`` is
    ``None`` with fewer than two wakes. ``breakdown`` lists the categories seen,
    most common first; ``summary`` reads ``34 check-ins, 6 lineup, 2 HA``."""
    counts: dict[str, int] = {}
    for w in wakes:
        counts[w["cat"]] = counts.get(w["cat"], 0) + 1
    per_day: float | None = None
    if len(wakes) >= 2:
        span_days = min(window_days, max((now_ms - wakes[0]["t_ms"]) / 86_400_000.0, 1 / 24))
        per_day = round(len(wakes) / span_days, 1)
    order = {c: i for i, c in enumerate(CATEGORIES)}
    ranked = sorted(counts, key=lambda c: (-counts[c], order.get(c, 99)))
    breakdown = [
        {"cat": c, "name": CATEGORIES.get(c, c), "short": _short(c, counts[c]), "count": counts[c]}
        for c in ranked
    ]
    parts = [f"{b['count']} {b['short']}" for b in breakdown]
    # The tile shows the top three causes; the rest fold into "N more" (the
    # full list is the tile's tooltip).
    rest = sum(b["count"] for b in breakdown[3:])
    summary = ", ".join(parts[:3]) + (f", {rest} more" if rest else "")
    return {
        "total": len(wakes),
        "per_day": per_day,
        "breakdown": breakdown,
        "summary": summary,
        "summary_full": ", ".join(parts),
    }


def _short(cat: str, count: int) -> str:
    """The breakdown word for ``cat``: singular for one (``1 restart``)."""
    word = _SHORT.get(cat, cat)
    return word[:-1] if count == 1 and word.endswith("s") else word


# -- gaps ----------------------------------------------------------------


def expected_interval_s(
    series: Sequence[dict[str, Any]], hint_s: float | None = None
) -> float | None:
    """The device's usual time between samples: the median spacing of the
    samples (robust to sleeps and outages, which are a minority of intervals),
    or the configured interval when there are too few samples to trust it.
    When both exist the larger wins, so a hint set below what the device really
    does can't flag every interval as a gap."""
    diffs = [
        (b["t_ms"] - a["t_ms"]) / 1000.0
        for a, b in pairwise(series)
        if b["t_ms"] - a["t_ms"] >= WAKE_DEBOUNCE_S * 1000.0
    ]
    median: float | None = (
        float(statistics.median(diffs)) if len(diffs) >= GAP_MIN_INTERVALS else None
    )
    hint = hint_s if hint_s is not None and hint_s > 0 else None
    if median is None:
        return hint
    return max(median, hint) if hint is not None else median


def detect_gaps(
    series: Sequence[dict[str, Any]],
    *,
    now_ms: int,
    expected_s: float | None,
    quiet_window: QuietHoursWindow | None = None,
    tz: tzinfo | None = None,
) -> list[dict[str, Any]]:
    """Spans with no readings: ``{start_ms, end_ms, kind}`` with ``kind``
    ``asleep`` when the span overlaps the device's quiet hours, else
    ``offline``. A span is a gap when it is longer than :data:`GAP_FACTOR`
    times ``expected_s`` and at least :data:`GAP_MIN_S`. The silence between the
    last sample and ``now_ms`` counts too, so a device that has stopped
    reporting shows it."""
    if expected_s is None or not series:
        return []
    threshold_ms = max(expected_s * GAP_FACTOR, GAP_MIN_S) * 1000.0
    edges = [p["t_ms"] for p in series]
    edges.append(now_ms)
    gaps: list[dict[str, Any]] = []
    for start, end in pairwise(edges):
        if end - start <= threshold_ms:
            continue
        asleep = _overlaps_quiet(start, end, quiet_window, tz)
        gaps.append({"start_ms": start, "end_ms": end, "kind": "asleep" if asleep else "offline"})
    return gaps


def _overlaps_quiet(
    start_ms: int, end_ms: int, window: QuietHoursWindow | None, tz: tzinfo | None
) -> bool:
    """Whether the span's first minute or its midpoint falls in the window."""
    if window is None:
        return False
    for ms in (start_ms + 60_000, (start_ms + end_ms) // 2):
        if is_in_window(window, datetime.fromtimestamp(ms / 1000.0, tz), tz):
            return True
    return False


# -- the rest ------------------------------------------------------------


def prediction_dict(prediction: Prediction | None) -> dict[str, Any] | None:
    """The prediction fields the templates and JSON endpoint use."""
    if prediction is None:
        return None
    return {
        "slope_per_day": prediction.slope_per_day,
        "is_charging": prediction.is_charging,
        "charge_rate_per_day": prediction.charge_rate_per_day,
        "days_to_20pct": prediction.days_to_20pct,
        "days_to_empty": prediction.days_to_empty,
        "days_to_full": prediction.days_to_full,
        "samples": prediction.samples,
    }


def tone_for(pct: int | None) -> str:
    """``critical`` (10% or less), ``low`` (30% or less) or ``ok``."""
    if pct is not None and pct <= 10:
        return "critical"
    if pct is not None and pct <= 30:
        return "low"
    return "ok"


#: The per-chart range buttons: (hours, label). A chart's window need not be
#: one of these (the Batteries page also offers 3, 14 and 90 days); none is
#: pressed then.
RANGES: tuple[tuple[int, str], ...] = ((12, "12h"), (24, "24h"), (168, "7d"), (720, "30d"))
#: The longest window the JSON endpoint serves.
MAX_WINDOW_HOURS: int = 90 * 24


def window_label(hours: float) -> str:
    """``12 hours``, ``7 days``: a window as it reads in a sentence."""
    if hours >= 48 and hours % 24 == 0:
        return f"{int(hours // 24)} days"
    if hours == 24:
        return "24 hours"
    value = f"{hours:g}"
    return f"{value} hour" + ("" if hours == 1 else "s")


#: Event types the cause of a wake is read from, besides the device's own rows.
_CONTEXT_TYPES: tuple[str, ...] = ("push", "touch", "scheduler", "rotation", "deck")


def load_wake_events(
    event_log: EventLog | None,
    device_ids: Sequence[str],
    *,
    window_days: float | None = None,
    window_hours: float | None = None,
) -> dict[str, WakeEvents]:
    """The event-log rows behind every card's wakes, two queries for the page:
    the devices' heartbeat rows over the chart window, and the push / touch /
    schedule / lineup rows from a day before it (a sleeping device's first wake
    in the window may pick up an earlier push). Pass the entry for a device to
    :func:`build_battery_chart` as ``events``."""
    if event_log is None or not device_ids:
        return {}
    hours = window_hours if window_hours is not None else (window_days or 7) * 24
    since = time.time() - hours * 3600.0
    status = event_log.device_status_events(list(device_ids), since=since)
    context = event_log.context_events(_CONTEXT_TYPES, since=since - PICKUP_MAX_S)
    links: tuple[EventRow, ...] = tuple(
        row for kind in ("scheduler", "rotation", "deck") for row in context.get(kind, [])
    )
    pushes = [*context.get("push", []), *context.get("touch", [])]
    pushes.sort(key=lambda r: r.timestamp)
    out: dict[str, WakeEvents] = {}
    for device_id in device_ids:
        mine = [
            row
            for row in pushes
            if device_id in (row.extra.get("device_ids") or []) or row.target == device_id
        ]
        out[device_id] = WakeEvents(status=status.get(device_id, []), pushes=mine, links=links)
    return out


def app_chart_options(config: Mapping[str, Any], device: Any) -> dict[str, Any]:
    """The per-device keyword arguments of :func:`build_battery_chart` that come
    from the running app: quiet hours and timezone (to call a gap asleep), the
    configured check-in interval (to size gaps) and lineup names (to name a
    lineup update). Call inside a request; the device page's embed uses it the
    same way the Batteries page does."""
    from app.device_service import awake_poll_interval_s
    from app.quiet_hours import resolve_quiet_hours
    from app.tz_resolve import app_timezone

    settings = config.get("SETTINGS_STORE")
    app_settings: dict[str, Any] = settings.get_section("app") if settings is not None else {}
    device_settings: dict[str, Any] = {}
    if settings is not None and device is not None:
        device_settings = (settings.get_section("devices") or {}).get(device.id) or {}
    hint: float | None = None
    for raw in (awake_poll_interval_s(device_settings), device_settings.get("sleep_interval_s")):
        if isinstance(raw, int | float) and not isinstance(raw, bool) and raw > 0:
            hint = float(raw)
            break
    names: dict[str, str] = {}
    for key in ("DECK_STORE", "ROTATION_STORE"):
        store = config.get(key)
        if store is not None:
            names.update({item.id: item.name for item in store.all()})
    return {
        "quiet_window": resolve_quiet_hours(app_settings, device),
        "tz": app_timezone(),
        "interval_hint_s": hint,
        "lineup_names": names,
    }


def build_battery_chart(
    device_id: str,
    *,
    history: BatteryHistory,
    window_days: float | None = None,
    window_hours: float | None = None,
    name: str | None = None,
    device: Any = None,
    current: dict[str, Any] | None = None,
    mv_offset: int = 0,
    pct_offset: int = 0,
    event_log: EventLog | None = None,
    events: WakeEvents | None = None,
    lineup_names: Mapping[str, str] | None = None,
    quiet_window: QuietHoursWindow | None = None,
    tz: tzinfo | None = None,
    interval_hint_s: float | None = None,
) -> dict[str, Any]:
    """Everything one device's battery card needs.

    The window is ``window_hours`` when given, else ``window_days`` (default 7).
    ``events`` are the device's pre-fetched event-log rows (from
    :func:`load_wake_events`, two queries for a whole page); when ``None`` and an
    ``event_log`` is given, they are fetched here for this one device.
    ``app_chart_options`` supplies ``lineup_names``, ``quiet_window``, ``tz`` and
    ``interval_hint_s``. The series carries the **offset-adjusted** percent so
    the chart matches the dashboard's current-battery readout
    (``BatteryHistory.recent`` returns raw rows; the offset is layered here
    only).

    Returns::

        {
          "id": str, "name": str,
          "current": {...},            # live reading, as passed in
          "pct": int | None, "tone": "ok" | "low" | "critical",
          "window_hours": float, "window_days": float, "window_label": str,
          "ranges": [{"hours", "label", "pressed"}, ...],  # per-chart range buttons
          "samples": int,
          "series": [{"t_ms": int, "pct": int}, ...],            # oldest first
          "wakes": [{"t_ms", "pct", "cat", "label", "detail", "reason"}, ...],
                                       # thinned for drawing
          "wake_stats": {"total", "per_day", "breakdown": [...], "summary"},
                                       # from the unthinned wakes
          "legend": [{"cat", "name"}, ...],  # categories drawn, in legend order
          "gaps": [{"start_ms", "end_ms", "kind": "asleep" | "offline"}, ...],
          "predictions_apply": bool,   # False for a Visionect frame
          "prediction": {...} | None,  # always None when predictions don't apply
        }
    """
    hours: float = window_hours if window_hours is not None else (window_days or 7) * 24
    days = hours / 24
    rows: list[BatteryRow] = history.recent(device_id, window_days=days)
    series: list[dict[str, Any]] = []
    for row in rows:
        adj = apply_to_pct(row.pct, mv_offset, pct_offset, raw_mv=row.battery_mv)
        series.append(
            {"t_ms": int(row.timestamp * 1000), "pct": adj if adj is not None else row.pct}
        )
    if events is None:
        events = load_wake_events(event_log, [device_id], window_hours=hours).get(
            device_id, WakeEvents()
        )
    now_ms = int(time.time() * 1000)
    all_wakes = attribute_wakes(
        extract_wakes(series, events.status),
        events.pushes,
        links=events.links,
        names=lineup_names,
        device_id=device_id,
    )
    for wake in all_wakes:
        wake.pop("reason_label", None)
    stats = wake_stats(all_wakes, window_days=days, now_ms=now_ms)
    wakes = thin_wakes(all_wakes, start_ms=now_ms - int(hours * 3_600_000), end_ms=now_ms)
    drawn = {w["cat"] for w in wakes}
    applies = is_battery_predictable(device)
    pct = (current or {}).get("pct")
    return {
        "id": device_id,
        "name": name or device_id,
        "current": current or {},
        "pct": pct,
        "tone": tone_for(pct),
        "window_hours": hours,
        "window_days": int(days) if days == int(days) else round(days, 3),
        "window_label": window_label(hours),
        "ranges": [{"hours": h, "label": lbl, "pressed": h == hours} for h, lbl in RANGES],
        "samples": len(rows),
        "series": series,
        "wakes": wakes,
        "wake_stats": stats,
        "legend": [{"cat": c, "name": n} for c, n in CATEGORIES.items() if c in drawn],
        "gaps": detect_gaps(
            series,
            now_ms=now_ms,
            expected_s=expected_interval_s(series, interval_hint_s),
            quiet_window=quiet_window,
            tz=tz,
        ),
        "predictions_apply": applies,
        "prediction": (
            prediction_dict(history.predict(device_id, window_days=days)) if applies else None
        ),
    }


def render_card_parts(card: dict[str, Any]) -> dict[str, str]:
    """The card's server-rendered pieces that change with its range, as HTML
    for the page to swap in: ``tiles_html`` (the stat tiles), ``hint_html`` (the
    not-enough-samples note, or empty) and ``legend_html`` (the chart key, or
    empty). Rendered by the same macros as the first paint."""
    from flask import current_app

    macros: Any = current_app.jinja_env.get_template("_battery_chart.html").module
    return {
        "tiles_html": str(macros.battery_tiles(card)).strip(),
        "hint_html": str(macros.battery_hint(card)).strip(),
        "legend_html": str(macros.battery_legend(card)).strip(),
    }


def _clean(value: Any) -> str:
    return value.strip()[:40] if isinstance(value, str) else ""


def _lineup_name(
    row: EventRow, links: Sequence[EventRow], names: Mapping[str, str], device_id: str
) -> str:
    """The lineup, rotation or schedule behind a lineup-source push, or ``""``.

    A scheduler / rotation row links to the push it caused by
    ``extra.push_event_id``; a lineup step carries ``extra.deck_id`` when it was
    promoted from the warm cache, and otherwise is matched to the lineup's own
    ``deck`` row (same device and page, close in time)."""
    source = row.source
    if source in ("scheduler", "rotation"):
        for link in links:
            if link.type == source and link.extra.get("push_event_id") == row.id:
                key = "schedule_name" if source == "scheduler" else "rotation_name"
                return _clean(link.extra.get(key))
        return ""
    if source in ("deck", "deck_init"):
        deck_id = row.extra.get("deck_id")
        if isinstance(deck_id, str) and deck_id:
            return names.get(deck_id, "")
        for link in links:
            if (
                link.type == "deck"
                and link.extra.get("page_id") == row.target
                and device_id
                in (link.extra.get("timer_advance_device"), link.extra.get("home_return_device"))
                and abs(link.timestamp - row.timestamp) <= 60
            ):
                return names.get(link.target, "")
        return ""
    if source == "page_refresh":
        return "page refresh"
    if source == "follow-up":
        return "follow-up"
    return ""


def _nearest(times: Sequence[int], t_ms: int) -> int | None:
    """Index of the value in sorted ``times`` closest to ``t_ms``."""
    if not times:
        return None
    i = bisect.bisect_left(times, t_ms)
    if i == 0:
        return 0
    if i == len(times):
        return len(times) - 1
    return i if times[i] - t_ms < t_ms - times[i - 1] else i - 1


def _interpolate(series: Sequence[dict[str, Any]], t_ms: int) -> int:
    """The line's percent at ``t_ms``, clamped to the first / last sample."""
    times = [p["t_ms"] for p in series]
    i = bisect.bisect_left(times, t_ms)
    if i == 0:
        return int(series[0]["pct"])
    if i == len(series):
        return int(series[-1]["pct"])
    a, b = series[i - 1], series[i]
    span = b["t_ms"] - a["t_ms"]
    if span <= 0:
        return int(b["pct"])
    frac = (t_ms - a["t_ms"]) / span
    value: int = round(a["pct"] + (b["pct"] - a["pct"]) * frac)
    return value
