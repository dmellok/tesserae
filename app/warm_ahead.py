"""Warm-ahead: a page's widgets run before a scheduled send, so slow work
(an AI painting in flight, say) is finished when the real render happens
and the panel paints the finished page once.

A widget opts in through its manifest::

    "render": {"warm_ahead_s": 150}

That number is only the first guess. Each warm polls the page until it stops
asking for a refresh (the ``live_refresh_s`` hint, see ``composer.refresh_hint_s``)
and learns from it: how many pieces of work the page said were pending at the
start (``live_pending``) against how long it took to settle gives seconds per
item; a page that says nothing about pending work is learned as a whole. The
next lead is the typical number of items for that page times the seconds per
item, plus the page's own base time, with a margin, so a page that paints
three aircraft at fifteen seconds each is warmed about a minute ahead and one
that paints ten gets three. A warm that runs out of lead before the page
settles asks for more next time.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# The lead a page may ask for, after learning.
LEAD_MIN_S = 30
LEAD_MAX_S = 900
# How long a warm waits between polls of the page: the page's own hint, kept
# within this window so a 2 s hint does not hammer the widgets' upstreams.
POLL_MIN_S = 10
POLL_MAX_S = 30
# Lead = settle time seen x MARGIN + MARGIN_S.
MARGIN = 1.3
MARGIN_S = 20
# Weight of the newest observation in each running estimate.
LEARN = 0.3
# Seconds per item until a page has been seen to settle with items pending.
PER_ITEM_DEFAULT_S = 20.0


def page_warm_ahead_s(page: Any, registry: Any) -> int:
    """The longest ``render.warm_ahead_s`` among the widgets on ``page`` (grid
    cells and canvas widgets alike), clamped to the lead window; 0 when none
    asks."""
    if registry is None:
        return 0

    def wants(plugin_id: Any) -> int:
        if not plugin_id:
            return 0
        plugin = registry.get(str(plugin_id))
        if plugin is None:
            return 0
        try:
            return int((plugin.manifest.get("render") or {}).get("warm_ahead_s") or 0)
        except (TypeError, ValueError, AttributeError):
            return 0

    best = 0
    for cell in getattr(page, "cells", []) or []:
        best = max(best, wants(getattr(cell, "plugin", None)))
    canvas = getattr(page, "canvas", None)
    for el in (getattr(canvas, "els", None) or []) if canvas is not None else []:
        best = max(best, wants(getattr(el, "widget", None)))
        for src in getattr(el, "sources", None) or []:
            best = max(best, wants(getattr(src, "key", None)))
    return min(LEAD_MAX_S, max(LEAD_MIN_S, best)) if best > 0 else 0


class WarmAhead:
    """Starts warms for fires that are within a page's lead, one per page and
    fire, and learns each page's settle time from them.

    ``warm(page_id)`` runs the page's widgets as a push would and returns the
    refresh hint the composed page carries (0 once nothing on it is waiting)
    and how many pieces of work it says are pending. ``clock`` and ``sleep``
    are injectable for tests; ``threaded=False`` runs a warm inline."""

    def __init__(
        self,
        *,
        warm: Callable[[str], tuple[int, int]],
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        threaded: bool = True,
        name: Callable[[str], str] | None = None,
        eligible: Callable[[str], bool] | None = None,
    ) -> None:
        self._warm = warm
        self._eligible = eligible or (lambda _page_id: True)
        self._name = name
        self._clock = clock
        self._sleep = sleep
        self._threaded = threaded
        self._lock = threading.Lock()
        # page_id -> what has been learned: seconds per pending item, the
        # typical number of items at a send, the page's base settle time
        # (with nothing pending), and the last settle time seen
        self._learned: dict[str, dict[str, float]] = {}
        # "page_id|fire_ts" -> when its warm started, so one fire warms once
        self._started: dict[str, float] = {}

    # -- what the scheduler asks ------------------------------------------

    def lead_s(self, page_id: str, default_s: int) -> int:
        """How far ahead of a fire to start warming ``page_id``: the manifest's
        number until the page has been seen to settle, then that time with a
        margin."""
        with self._lock:
            seen = self._learned.get(page_id)
        if seen is None:
            return min(LEAD_MAX_S, max(LEAD_MIN_S, int(default_s)))
        expected = seen["base_s"] + seen["items"] * seen["per_item_s"]
        return min(LEAD_MAX_S, max(LEAD_MIN_S, int(expected * MARGIN + MARGIN_S)))

    def consider(self, page_id: str, fire_at: float, default_s: int, now: float) -> bool:
        """Start a warm for the fire of ``page_id`` at ``fire_at`` (epoch
        seconds) when it is within the lead and has not been warmed. True when
        a warm was started."""
        lead = self.lead_s(page_id, default_s)
        if not (0 < fire_at - now <= lead) or not self._eligible(page_id):
            return False
        key = f"{page_id}|{int(fire_at)}"
        with self._lock:
            if key in self._started:
                return False
            self._started[key] = now
            stale = [k for k, t in self._started.items() if now - t > LEAD_MAX_S * 2]
            for k in stale:
                del self._started[k]
        logger.info(
            "warm ahead: %s fires in %.0fs, warming now (lead %ds%s)",
            page_id,
            fire_at - now,
            lead,
            ""
            if page_id not in self._learned
            else f", settled in {self._learned[page_id]['settle_s']:.0f}s last time",
        )
        if self._threaded:
            threading.Thread(
                target=self._run,
                args=(page_id, fire_at, now),
                name="tesserae-warm-ahead",
                daemon=True,
            ).start()
        else:
            self._run(page_id, fire_at, now)
        return True

    def settle_s(self, page_id: str) -> float | None:
        """The seconds ``page_id`` took to settle at its last warm; None before any."""
        with self._lock:
            seen = self._learned.get(page_id)
        return None if seen is None else seen["settle_s"]

    def learned(self, page_id: str) -> dict[str, float] | None:
        """What is known about ``page_id``: ``per_item_s``, ``items`` (typical
        pending at a send), ``base_s`` and ``settle_s``; None before any warm."""
        with self._lock:
            seen = self._learned.get(page_id)
        return dict(seen) if seen else None

    def status(self) -> dict[str, dict[str, float]]:
        with self._lock:
            pages = list(self._learned)
        out: dict[str, dict[str, float]] = {}
        for page_id in pages:
            seen = self.learned(page_id)
            if seen:
                out[page_id] = {
                    **{k: round(v, 1) for k, v in seen.items()},
                    "lead_s": self.lead_s(page_id, 0),
                }
        return out

    # -- one warm ----------------------------------------------------------

    def _run(self, page_id: str, fire_at: float, started: float) -> None:
        settled = False
        polls = 0
        items = 0
        from app import jobs

        name = (self._name(page_id) if self._name else "") or page_id
        key = f"warm:{page_id}"
        title = f"Running {name} ahead of its send"
        try:
            while True:
                if not self._eligible(page_id):
                    # Leave this fire eligible if the display comes back
                    # before it is due. A cancelled warm teaches us nothing.
                    with self._lock:
                        self._started.pop(f"{page_id}|{int(fire_at)}", None)
                    return
                jobs.report(
                    "scheduler",
                    key,
                    title,
                    detail="running the page" if not polls else f"{items} pending; looking again",
                    label="Warm-ahead",
                )
                try:
                    hint, pending = self._warm(page_id)
                    hint, pending = int(hint or 0), int(pending or 0)
                except Exception:
                    logger.exception("warm ahead: %s failed", page_id)
                    break
                polls += 1
                if polls == 1:
                    items = pending
                now = self._clock()
                if hint <= 0:
                    settled = True
                    break
                if now + POLL_MIN_S >= fire_at:
                    break
                jobs.report(
                    "scheduler",
                    key,
                    title,
                    detail=f"{pending} still painting, {max(0.0, fire_at - now):.0f} s to the send",
                    label="Warm-ahead",
                )
                self._sleep(float(min(POLL_MAX_S, max(POLL_MIN_S, hint), max(1, fire_at - now))))
        finally:
            jobs.finish("scheduler", key, "settled" if settled else "sent as it was")
        took = max(0.0, self._clock() - started)
        if not settled:
            # Out of lead (or a failure) before the page settled: next time
            # ask for half as much again on top of what was tried.
            took = max(took, fire_at - started) * 1.5
        self._learn(page_id, items, took, settled)
        learned = self.lead_s(page_id, 0)
        logger.info(
            "warm ahead: %s %s after %d poll(s) in %.0fs with %d item(s) pending at the start; next lead %ds",
            page_id,
            "settled" if settled else "did not settle",
            polls,
            took,
            items,
            learned,
        )
        if learned >= LEAD_MAX_S:
            logger.warning(
                "warm ahead: %s takes longer than the longest lead (%ds); it will not finish before its sends",
                page_id,
                LEAD_MAX_S,
            )

    def _learn(self, page_id: str, items: int, took: float, settled: bool) -> None:
        """Fold one warm into the page's estimates: with items pending, the
        time beyond the page's base is seconds per item; with none, it is the
        base itself. The typical item count moves towards what was seen."""

        def ema(old: float | None, new: float) -> float:
            return new if old is None else old * (1 - LEARN) + new * LEARN

        with self._lock:
            seen = self._learned.get(page_id)
            base = seen["base_s"] if seen else None
            per_item = seen["per_item_s"] if seen else None
            typical = seen["items"] if seen else None
            if items > 0:
                base_now = base if base is not None else min(took, 10.0)
                per_item = ema(per_item, max(1.0, (took - base_now) / items))
                base = base_now
            else:
                base = ema(base, took)
            self._learned[page_id] = {
                "per_item_s": per_item if per_item is not None else PER_ITEM_DEFAULT_S,
                "items": ema(typical, float(items)),
                "base_s": base,
                "settle_s": took,
            }
