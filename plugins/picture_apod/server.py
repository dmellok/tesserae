"""picture_apod — NASA Astronomy Picture of the Day.

Ported from inky-dash's apod plugin. Walks back day-by-day if today's
entry is a video, capped at LOOKBACK_DAYS so a long video streak can't
trigger a request storm. Cached for an hour so the composer's repeated
render hits within a single push pipeline don't each go upstream.
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

CACHE_TTL_S = 60 * 60
LOOKBACK_DAYS = 14
DEMO_KEY = "DEMO_KEY"
USER_AGENT = "tesserae/0.1 (+picture_apod)"


def _api_request(api_key: str, when: date | None = None) -> dict[str, Any]:
    params = {"api_key": api_key, "thumbs": "true"}
    if when is not None:
        params["date"] = when.isoformat()
    url = "https://api.nasa.gov/planetary/apod?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _pick_image_url(entry: dict[str, Any]) -> str | None:
    if entry.get("media_type") != "image":
        return None
    url: str | None = entry.get("hdurl") or entry.get("url")
    return url


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del options
    api_key = (settings.get("api_key") or DEMO_KEY).strip() or DEMO_KEY

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / "apod.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text())  # type: ignore[no-any-return]
        except (json.JSONDecodeError, OSError):
            pass

    today = datetime.now(UTC).date()
    chosen: dict[str, Any] | None = None
    last_err: str | None = None

    for offset in range(LOOKBACK_DAYS):
        when: date | None = today - timedelta(days=offset) if offset > 0 else None
        try:
            entry = _api_request(api_key, when)
        except urllib.error.HTTPError as err:
            last_err = f"HTTP {err.code}: {err.reason}"
            break
        except Exception as err:
            last_err = f"{type(err).__name__}: {err}"
            break

        url = _pick_image_url(entry)
        if url:
            chosen = entry
            chosen["_image_url"] = url
            break

    if chosen is None:
        return {
            "error": last_err or f"No APOD image found in the last {LOOKBACK_DAYS} days.",
            "url": None,
        }

    result = {
        "url":        chosen["_image_url"],
        "title":      chosen.get("title", ""),
        "date":       chosen.get("date", ""),
        "copyright":  (chosen.get("copyright") or "").strip(),
        "fetched_at": int(time.time()),
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result))
    return result
