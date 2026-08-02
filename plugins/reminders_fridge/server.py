"""Fridge grocery-list widget: renders the Companion personal-data snapshot.

Read-only. The iOS Companion publishes a ``reminders.fridge`` snapshot to the
server (see app/companion_api.py, contract 0.7); this widget's ``fetch`` reads
the latest one from ``PERSONAL_DATA_STORE`` and shapes it for client.js. No
admin page: the data's authority is the phone. When no snapshot exists (source
disabled, never published, or expired) the widget shows a calm empty state.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any

from flask import current_app

STALE_SECONDS = 86_400  # matches PERSONAL_DATA_STALE_SECONDS in companion_api
_SOURCE_ID = "reminders.fridge"


def _ago_label(generated_epoch: float | None, now: float) -> str:
    if not isinstance(generated_epoch, (int, float)):
        return ""
    diff = max(0.0, now - generated_epoch)
    if diff < 90:
        return "just now"
    if diff < 3600:
        return f"{int(diff // 60)}m ago"
    if diff < 86_400:
        return f"{int(diff // 3600)}h ago"
    return f"{int(diff // 86_400)}d ago"


def _due(due_str: Any, today: date) -> tuple[str, bool]:
    """Return ``(label, urgent)`` for an item's due date. Urgent = due today or
    overdue. Empty label when there's no date."""
    if not isinstance(due_str, str) or not due_str:
        return "", False
    try:
        due = datetime.strptime(due_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return "", False
    delta = (due - today).days
    if delta < 0:
        return "Overdue", True
    if delta == 0:
        return "Today", True
    if delta == 1:
        return "Tmrw", False
    if delta < 7:
        return due.strftime("%a"), False
    return due.strftime("%b ") + str(due.day), False


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    title = (options.get("title") or "").strip() or "Fridge"
    max_items = int(options.get("max_items") or 0)
    accent = str(options.get("accent") or "accent-1")

    store = current_app.config.get("PERSONAL_DATA_STORE")
    rec = store.get(_SOURCE_ID) if store is not None else None
    now = time.time()

    base: dict[str, Any] = {"title": title, "accent": accent, "items": [], "count": 0, "shown": 0}

    if not isinstance(rec, dict):
        return {**base, "state": "empty", "empty": True, "updated_label": ""}

    generated = rec.get("generated_epoch")
    expires = rec.get("expires_epoch")
    raw_snapshot = rec.get("snapshot")
    snapshot: dict[str, Any] = raw_snapshot if isinstance(raw_snapshot, dict) else {}

    if isinstance(expires, (int, float)) and now >= expires:
        state = "expired"
    elif isinstance(generated, (int, float)) and now >= generated + STALE_SECONDS:
        state = "stale"
    else:
        state = "fresh"

    raw_items = (snapshot.get("data") or {}).get("items") or []
    today = datetime.now().date()
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        due_label, urgent = _due(raw.get("due_date"), today)
        items.append(
            {
                "title": str(raw.get("title") or ""),
                "high": raw.get("priority") == "high",
                "due": due_label,
                "urgent": urgent,
            }
        )
    count = len(items)
    shown = items[:max_items] if max_items > 0 else items

    return {
        **base,
        "items": shown,
        "count": count,
        "shown": len(shown),
        "state": state,
        "updated_label": _ago_label(generated, now),
        "empty": count == 0 and state != "expired",
    }
