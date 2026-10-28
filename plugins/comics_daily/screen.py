"""What the comics page shows on a screen of a given size.

The page draws the Far Side's panels in a row and each strip at the cell's
full width, never cutting one off (client.js measures and fits). On a big
portrait panel everything fits. On a small or landscape screen it can't, and
a strip drawn smaller than the screen's width would be too small to read, so
the comics take turns instead: each render shows the next ones that fit
whole, and the following render moves on (at most once per HOLD_S, so a
send prepared ahead of time and its follow-ups show the same comics).

``plan()`` decides from the cell's size and the pictures' sizes, estimating
the heights the way client.js draws them; client.js still measures, so an
estimate a little off only changes how the gap is spread.

How many share the screen is the operator's choice (the "On small screens"
setting): as many as fit, or one at a time.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

# As client.js: the content width the gaps are set for, the narrowest a Far
# Side panel gets before the row drops one, the spare pixels kept free.
REF_WIDTH = 1408
MIN_PANEL_PX = 260
SAFETY_PX = 16
HOLD_S = 4 * 60

# The screens the admin page previews: name, width, height.
SCREENS: tuple[tuple[str, int, int], ...] = (
    ("32-inch portrait", 1440, 2560),
    ("13-inch", 1600, 1200),
    ("7.5-inch", 800, 480),
)


def _clamp(low: float, value: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class Item:
    """One row the page can show: the Far Side's panels or one strip."""

    kind: str  # "farside" | "strip"
    sizes: tuple[tuple[int, int], ...]  # (w, h) of each picture
    captions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    """Which items show (indexes into the list given), and whether they take
    turns: ``rotating`` with the window starting at ``start``."""

    show: tuple[int, ...]
    rotating: bool
    total: int

    @property
    def turns(self) -> int:
        """How many renders it takes to show every item once."""
        return math.ceil(self.total / max(1, len(self.show))) if self.rotating else 1


def heights(
    cell_w: float, items: Sequence[Item], far_side_count: int, captions: bool
) -> list[float]:
    """Each item's height at ``cell_w``, as client.js draws it."""
    pad = _clamp(4, 0.011 * cell_w, 24)
    width = max(1.0, cell_w - 2 * pad)
    out = []
    for item in items:
        if item.kind == "farside":
            want = min(far_side_count, len(item.sizes))
            cols = max(1, min(want, int(width // MIN_PANEL_PX)))
            gap = _clamp(4, 0.0055 * cell_w, 12)
            panel_pad = _clamp(4, 0.0105 * cell_w, 20)
            side = _clamp(2, 0.0035 * cell_w, 8)
            img_w = (width - gap * (cols - 1)) / cols - 2 * side
            tallest = max((img_w * h / w for w, h in item.sizes[:cols] if w > 0), default=0)
            caption = 0.0
            texts = [c for c in item.captions[:cols] if c] if captions else []
            if texts:
                font = _clamp(11, 0.012 * cell_w, 26)
                per_line = max(1, int(img_w / (font * 0.52)))
                lines = max(math.ceil(len(c) / per_line) for c in texts)
                caption = panel_pad + lines * font * 1.3
            out.append(tallest + 2 * panel_pad + caption)
        else:
            w, h = item.sizes[0]
            out.append(width * h / w if w > 0 else 0.0)
    return out


def available_height(cell_w: float, cell_h: float, note: bool) -> float:
    pad = _clamp(4, 0.011 * cell_w, 24)
    note_h = (_clamp(2, 0.005 * cell_w, 10) + _clamp(10, 0.0095 * cell_w, 20) * 1.2) if note else 0
    return cell_h - 2 * pad - note_h - SAFETY_PX * min(1.0, cell_w / REF_WIDTH)


def plan(
    cell_w: float | None,
    cell_h: float | None,
    items: Sequence[Item],
    *,
    far_side_count: int,
    captions: bool,
    note: bool,
    gap_min: float,
    one_at_a_time: bool,
    start: int = 0,
) -> Plan:
    """What to show on a ``cell_w`` x ``cell_h`` cell, from ``start`` (the
    placement's turn). Everything when it all fits (or the size is unknown);
    else the items from ``start`` on that fit whole (at least one: a single
    item too tall is drawn smaller, never cut), or one, taking turns."""
    total = len(items)
    if not total or not cell_w or not cell_h or cell_w <= 0 or cell_h <= 0:
        return Plan(tuple(range(total)), False, total)
    hs = heights(cell_w, items, far_side_count, captions)
    room = available_height(cell_w, cell_h, note)
    gap = gap_min * min(1.0, (cell_w - 2 * _clamp(4, 0.011 * cell_w, 24)) / REF_WIDTH)
    if sum(hs) + gap * (total - 1) <= room:
        return Plan(tuple(range(total)), False, total)
    start %= total
    show = [start]
    used = hs[start]
    if not one_at_a_time:
        for step in range(1, total):
            index = (start + step) % total
            if used + gap + hs[index] > room:
                break
            show.append(index)
            used += gap + hs[index]
    return Plan(tuple(show), True, total)


def turns(
    cell_w: float | None,
    cell_h: float | None,
    items: Sequence[Item],
    **kwargs: object,
) -> list[Plan]:
    """Every turn a placement goes through from the top, one plan per
    refresh, until each item has shown once (one plan when all fit)."""
    out: list[Plan] = []
    start = 0
    while len(out) < max(1, len(items)):
        chosen = plan(cell_w, cell_h, items, start=start, **kwargs)  # type: ignore[arg-type]
        out.append(chosen)
        if not chosen.rotating:
            break
        start += len(chosen.show)
        if start >= len(items):
            break
    return out
