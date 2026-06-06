"""picture_unsplash, random photo from Unsplash, full bleed.

Ported from inky-dash's unsplash plugin. Cache key hashes the filter
options so two cells with the same filters share an API call (saving
quota) while two with different filters stay independent. A short TTL
(30s) deduplicates the burst of fetches inside a single push pipeline
(composer + screenshot + paint) without making the rotation cadence
feel stuck.

Honours Unsplash's "trigger download" requirement: after picking a
photo, we GET the photo's links.download_location so the photographer's
stats reflect the use. Best-effort; never fails the render.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API_BASE = "https://api.unsplash.com"
CACHE_TTL_S = 30
USER_AGENT = "tesserae/0.1 (+picture_unsplash)"


def _cache_key(options: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "q": (options.get("query") or "").strip().lower(),
            "c": (options.get("collections") or "").strip(),
            "u": (options.get("username") or "").strip().lower(),
            "o": options.get("orientation", "any"),
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha1(payload).hexdigest()[:12]


def _request(url: str, headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))  # type: ignore[no-any-return]


def _track_download(download_endpoint: str, headers: dict[str, str]) -> None:
    with contextlib.suppress(Exception):
        req = urllib.request.Request(download_endpoint, headers=headers)
        with urllib.request.urlopen(req, timeout=5):
            pass


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    access_key = (settings.get("access_key") or "").strip()
    if not access_key:
        return {
            "error": "Set your Unsplash Access Key in Settings → Plugins → Unsplash.",
            "url": None,
        }

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / f"unsplash_{_cache_key(options)}.json"
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        except (json.JSONDecodeError, OSError):
            pass

    params: dict[str, str] = {}
    query = (options.get("query") or "").strip()
    if query:
        params["query"] = query
    collections = (options.get("collections") or "").strip()
    if collections:
        params["collections"] = collections
    username = (options.get("username") or "").strip()
    if username:
        params["username"] = username
    orientation = options.get("orientation", "any")
    if orientation in {"landscape", "portrait", "squarish"}:
        params["orientation"] = orientation
    params["content_filter"] = "high"

    url = f"{API_BASE}/photos/random"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "Authorization": f"Client-ID {access_key}",
        "Accept-Version": "v1",
        "User-Agent": USER_AGENT,
    }

    try:
        photo = _request(url, headers)
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")[:200]
        return {"error": f"HTTP {err.code}: {body}", "url": None}
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "url": None}

    # /random returns a list when count>1 is requested; with no count
    # it's a single object. Be defensive in case the API surfaces a
    # one-element list anyway.
    if isinstance(photo, list):
        if not photo:
            return {"error": "Unsplash returned no photos for those filters.", "url": None}
        photo = photo[0]

    urls = photo.get("urls") or {}
    image_url = urls.get("regular") or urls.get("full") or urls.get("raw")
    if not image_url:
        return {"error": "Unsplash response had no image URL.", "url": None}

    download_endpoint = (photo.get("links") or {}).get("download_location")
    if download_endpoint:
        _track_download(download_endpoint, headers)

    user = photo.get("user") or {}
    result = {
        "url": image_url,
        "alt": (photo.get("alt_description") or photo.get("description") or "").strip(),
        "credit_name": (user.get("name") or "").strip(),
        "credit_username": (user.get("username") or "").strip(),
        "html_link": (photo.get("links") or {}).get("html", ""),
        "fetched_at": int(time.time()),
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result
