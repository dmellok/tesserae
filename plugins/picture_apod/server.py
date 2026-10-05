"""picture_apod, NASA Astronomy Picture of the Day.

Ported from inky-dash's apod plugin, then moved to science.nasa.gov.

On 2026-09-29 APOD moved from apod.nasa.gov to science.nasa.gov/apod and
the api.nasa.gov ``planetary/apod`` endpoint this widget used stopped
serving real entries (it answers every date with the NASA logo). It now
reads the replacement, the WordPress REST route
``science.nasa.gov/wp-json/wp/v2/apod-basic``: no API key, no quota, so
the old ``api_key`` setting is gone (a stored value is ignored).

The new payload differs from the old one in ways that matter here:

* ``url`` is the HTML article permalink, not an image. The picture is
  ``hdurl``, an ``assets.science.nasa.gov`` link with its own resize query.
* ``credit`` / ``copyright`` are HTML fragments (links, sometimes an
  "Image Credit:" prefix in a ``<strong>``), so they're flattened to text.
* The listing route returns newest first, so one ``?per_page=N`` call
  replaces the old day-by-day walk back past video entries.

The image is requested at most 1200px on its long side through the asset
host's resizer: some ``hdurl`` files are several megabytes and the
renderer only waits a few seconds for the network to go idle. Older
entries point at ``/content/dam/``, which ignores the resize query, so
those are rewritten to the equivalent ``/dynamicimage/assets/`` path.
"""

from __future__ import annotations

import contextlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

from app.plugin_http import fetch_json

CACHE_TTL_S = 60 * 60
LOOKBACK_ENTRIES = 14
MAX_EDGE_PX = 1200
API_URL = "https://science.nasa.gov/wp-json/wp/v2/apod-basic"
ASSET_HOST = "assets.science.nasa.gov"
USER_AGENT = "tesserae/0.1 (+picture_apod)"

_TAG_RE = re.compile(r"<[^>]+>")
_CREDIT_PREFIX_RE = re.compile(r"^\s*(image\s+)?credit\s*(&\s*copyright)?\s*:\s*", re.IGNORECASE)
_IMG_SRC_RE = re.compile(r"<img[^>]+src=\"([^\"]+)\"", re.IGNORECASE)


def _plain_text(fragment: str | None) -> str:
    """Flatten an HTML fragment to one line of text."""
    text = html.unescape(_TAG_RE.sub("", fragment or ""))
    return " ".join(text.split())


def _credit(entry: dict[str, Any]) -> str:
    text = _plain_text(entry.get("copyright") or entry.get("credit"))
    return _CREDIT_PREFIX_RE.sub("", text).strip()


def _sized_image_url(raw: str) -> str:
    """Point an asset-host image at its resizer, capped at MAX_EDGE_PX."""
    parts = urllib.parse.urlsplit(raw)
    if parts.hostname != ASSET_HOST:
        return raw
    path = parts.path
    if path.startswith("/content/dam/"):
        path = "/dynamicimage/assets/" + path[len("/content/dam/") :]
    if not path.startswith("/dynamicimage/"):
        return raw
    query = urllib.parse.urlencode({"w": MAX_EDGE_PX, "h": MAX_EDGE_PX, "fit": "clip"})
    return urllib.parse.urlunsplit((parts.scheme or "https", parts.netloc, path, query, ""))


def _pick_image_url(entry: dict[str, Any]) -> str | None:
    """The entry's still image. For a video entry ``hdurl`` is a snapshot
    frame, which the caller only falls back to when no image is in reach."""
    raw: str | None = entry.get("hdurl")
    if not raw:
        match = _IMG_SRC_RE.search(entry.get("basic_html") or "")
        raw = html.unescape(match.group(1)) if match else None
    return _sized_image_url(raw) if raw else None


def _choose(entries: list[dict[str, Any]]) -> tuple[dict[str, Any], str] | None:
    still: tuple[dict[str, Any], str] | None = None
    for entry in entries:
        url = _pick_image_url(entry)
        if not url:
            continue
        if entry.get("media_type") == "image":
            return entry, url
        if still is None:
            still = (entry, url)
    return still


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del options, settings

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / "apod.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        except (json.JSONDecodeError, OSError):
            pass

    url = f"{API_URL}?per_page={LOOKBACK_ENTRIES}"
    try:
        entries = fetch_json(url, headers={"User-Agent": USER_AGENT})
    except urllib.error.HTTPError as err:
        with contextlib.suppress(Exception):
            err.close()
        return {"error": f"HTTP {err.code}: {err.reason}", "url": None}
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "url": None}

    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return {"error": "Unexpected response from science.nasa.gov.", "url": None}

    picked = _choose([e for e in entries if isinstance(e, dict)])
    if picked is None:
        return {"error": f"No APOD image in the last {LOOKBACK_ENTRIES} entries.", "url": None}
    entry, image_url = picked

    result = {
        "url": image_url,
        "title": _plain_text(entry.get("title")),
        "date": entry.get("date", ""),
        "copyright": _credit(entry),
        "alt": _plain_text(entry.get("alt")),
        "permalink": entry.get("permalink", ""),
        "fetched_at": int(time.time()),
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result
