"""news_wikipedia_otd, Wikipedia 'On this day' for today's date."""

from __future__ import annotations

import contextlib
import json
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CACHE_TTL_S = 6 * 3600
HTTP_TIMEOUT_S = 12
USER_AGENT = "tesserae/0.1 (+news_wikipedia_otd)"


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    kind = options.get("type", "events")
    if kind not in ("events", "births", "deaths", "holidays"):
        kind = "events"
    max_items = max(1, int(options.get("max_items") or 6))

    today = datetime.now(UTC)
    mm = f"{today.month:02d}"
    dd = f"{today.day:02d}"

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"otd_{mm}_{dd}_{kind}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    url = f"https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/{kind}/{mm}/{dd}"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "items": []}

    raw_items = payload.get(kind) or payload.get("events") or []
    items = []
    for r in raw_items[:max_items]:
        # Each entry: { text, year, pages: [{normalizedtitle, thumbnail, ...}] }
        first_page = (r.get("pages") or [{}])[0]
        thumb = (first_page.get("thumbnail") or {}).get("source") or ""
        items.append(
            {
                "year": r.get("year"),
                "text": r.get("text") or "",
                "page": first_page.get("normalizedtitle") or "",
                "thumb": thumb,
            }
        )

    result = {
        "kind": kind,
        "date": f"{today.day} {today.strftime('%B')}",
        "items": items,
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result
