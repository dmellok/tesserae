"""Advance a Meural's current dashboard item using the Lineups render path."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from flask import Flask
from PIL import Image

from app.device_loader import Device
from app.frame_log import _note


def advance(app: Flask, device: Device, latest: dict[str, Any]) -> bool:
    from app import preview_cache
    from app.composer import _preview_timezone_id
    from app.deck_routes import _item_render
    from app.push import DELIVERED_STATUSES
    from app.step_items import find_item, step_choices, step_draw_count

    page = app.config["PAGE_STORE"].get(str(latest.get("page_id") or ""))
    choices = step_choices(page, device.id) if page is not None else None
    key = ""
    if choices is not None and choices.mode == "draw" and step_draw_count(page, device.id) != 1:
        key = f"draw:{uuid.uuid4().hex[:12]}:1"
    elif choices is not None and len(choices.items) > 1:
        digest = str(latest.get("composition_digest") or "")
        with Image.open(Path(app.config["RENDERS_DIR"]) / f"{digest}.png") as image:
            shown = json.loads(image.info.get("tesserae_items", "{}"))
        candidates = shown.get(choices.cell_id, shown.get("*", []))
        current = next(
            (
                i
                for i, item in enumerate(choices.items)
                if item.key in candidates or f"#{item.index}" in candidates
            ),
            -1,
        )
        key = choices.items[(current + 1) % len(choices.items)].key
    if key and page is not None:
        chosen = find_item(page, key, device.id)
        render = _item_render(page, device.id, chosen) if chosen is not None else None
        if render is None:
            return False
        _note(app, device.id, "note", "Swipe on the frame: next item")
        preview_cache.render_now(
            base_url=app.config["BASE_URL_FN"](),
            page_id=page.id,
            width=render.width,
            height=render.height,
            cache_path=render.path,
            pool=app.config.get("BROWSER_POOL"),
            timezone_id=_preview_timezone_id(),
            query=render.query,
        )
        result = app.config["PUSH_MANAGER"].republish_composition(
            render.path.read_bytes(), page_id=page.id, device_ids={device.id}
        )
        return bool(result.status in DELIVERED_STATUSES)
    buttons = app.config.get("BUTTON_SERVICE")
    if buttons is None:
        return False
    _note(app, device.id, "note", "Swipe on the frame: next dashboard")
    result = buttons.step(
        device_id=device.id,
        forward=True,
        source="meural_swipe",
        lock_timeout_s=20.0,
        settle_max_s=45.0,
    )
    return bool(result.pushed_page_id and not result.error)
