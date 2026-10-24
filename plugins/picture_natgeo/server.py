"""picture_natgeo, National Geographic's Photo of the Day.

The source is National Geographic TV Canada's Photo of the Day RSS feed
(``feeds.feedburner.com/natgeotv/ca/featured/POD``). Each item carries a
date title ("OCTOBER 24, 2026"), the article link, and a description
with a 300x169 ``ThumbL`` image and the caption, which ends with the show
the frame comes from ("... Image from Lion.").

That thumbnail is far too small to fill a cell, so the full frame is read
off the article page, where it sits in ``<a id="POD<day>"><img src=...>``
(2400x1350, up to ~1.3 MB). The page is scraped rather than the id
guessed: the full image's id has so far been the thumbnail's minus one,
but nothing promises that. If the page can't be read the thumbnail is
used, a soft picture beats an empty cell.

The full frame is downloaded once per photo, downscaled to
``MAX_EDGE_PX`` on its long side, and served from this plugin's own
``/photos/`` route, so the renderer loads a few hundred kB over loopback
instead of a multi-megabyte remote file it may give up on mid-load, and
an offline host keeps showing the last photo it fetched.

The feed answer is cached for 10 minutes in data_dir, the same pattern as
weather_now; the photo files are kept for the newest ``KEEP_PHOTOS``.
"""

from __future__ import annotations

import contextlib
import html
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from flask import Blueprint, abort, current_app, send_from_directory

from app.plugin_http import decode_body

PLUGIN_ID = "picture_natgeo"
FEED_URL = "https://feeds.feedburner.com/natgeotv/ca/featured/POD"
USER_AGENT = f"tesserae/0.1 (+{PLUGIN_ID})"
CACHE_TTL_S = 600
# Short-fail so a slow upstream can't blow the composer's per-widget
# hydration budget (see weather_now/server.py). Three requests at most on
# a cold cache: feed, article page, image.
HTTP_TIMEOUT_S = 5
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_EDGE_PX = 1600
JPEG_QUALITY = 85
KEEP_PHOTOS = 7

_IMG_SRC_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SOURCE_RE = re.compile(r"\s*Image from\s+(.+?)\.?\s*$", re.IGNORECASE)
_PHOTO_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.jpg$")


# ----------------------------------------------------------------------
# Upstream
# ----------------------------------------------------------------------


def _get(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        body = decode_body(resp)
    if len(body) > MAX_IMAGE_BYTES:
        raise ValueError("response too large")
    return body


def _plain_text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", fragment)).split())


def _parse_date(title: str) -> str:
    """'OCTOBER 24, 2026' -> '2026-10-24', or '' when it doesn't parse."""
    try:
        return datetime.strptime(title.strip().title(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return ""


def _parse_feed(body: bytes) -> dict[str, Any] | None:
    """The newest item, or None when the feed has none we can use."""
    root = ET.fromstring(body)
    for item in root.iter("item"):
        description = item.findtext("description") or ""
        match = _IMG_SRC_RE.search(description)
        if not match:
            continue
        caption = _plain_text(description)
        source = ""
        found = _SOURCE_RE.search(caption)
        if found:
            source = found.group(1).strip()
            caption = caption[: found.start()].strip()
        title = (item.findtext("title") or "").strip()
        return {
            "date": _parse_date(title),
            "title": title,
            "link": (item.findtext("link") or "").strip(),
            "thumb": html.unescape(match.group(1)),
            "caption": caption,
            "source": source,
        }
    return None


def _article_url(link: str) -> str:
    """The feed links http://natgeotv.com/..., which redirects to
    https://www.natgeotv.com/...; go straight there."""
    parts = urllib.parse.urlsplit(link)
    if parts.hostname in ("natgeotv.com", "www.natgeotv.com"):
        return urllib.parse.urlunsplit(("https", "www.natgeotv.com", parts.path, "", ""))
    return link


def _full_image_url(page: str, iso_date: str) -> str | None:
    """The day's full-size frame on the article page: the <img> inside
    ``<a id="POD<day>">``. The page lists the whole month, hence the day."""
    day = iso_date[-2:].lstrip("0") if iso_date else ""
    if not day:
        return None
    match = re.search(
        rf"id=[\"']POD{day}[\"'][^>]*>\s*<img[^>]+src=[\"']([^\"']+)[\"']",
        page,
        re.IGNORECASE,
    )
    return html.unescape(match.group(1)) if match else None


# ----------------------------------------------------------------------
# Local photo store
# ----------------------------------------------------------------------


def _photos_dir(data_dir: Path) -> Path:
    path = data_dir / "photos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _photo_name(image_url: str) -> str:
    """A stable, path-safe file name for a remote image URL."""
    stem = Path(urllib.parse.urlsplit(image_url).path).stem
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-") or "photo"
    return f"{safe}.jpg"


def _store_photo(image_url: str, data_dir: Path) -> str:
    """Download ``image_url`` once, downscale it, return the file name."""
    from PIL import Image

    photos = _photos_dir(data_dir)
    name = _photo_name(image_url)
    target = photos / name
    if target.is_file():
        return name

    body = _get(image_url)
    with Image.open(io.BytesIO(body)) as source:
        img = source.convert("RGB")
    img.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX), Image.Resampling.LANCZOS)
    tmp = target.with_suffix(".tmp")
    img.save(tmp, "JPEG", quality=JPEG_QUALITY, optimize=True)
    tmp.replace(target)

    stale = sorted(photos.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in stale[KEEP_PHOTOS:]:
        with contextlib.suppress(OSError):
            old.unlink()
    return name


def _photo_route(name: str) -> str:
    return f"/plugins/{PLUGIN_ID}/photos/{name}"


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def _cached(path: Path) -> dict[str, Any] | None:
    if not path.exists() or time.time() - path.stat().st_mtime >= CACHE_TTL_S:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del options, settings

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / "pod.json"
    cached = _cached(cache_path)
    if cached is not None:
        return cached

    try:
        entry = _parse_feed(_get(FEED_URL))
    except urllib.error.HTTPError as err:
        with contextlib.suppress(Exception):
            err.close()
        return {"error": f"National Geographic's feed answered {err.code}. Try again later."}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"error": "Couldn't reach National Geographic right now."}
    except ET.ParseError:
        return {"error": "National Geographic's feed couldn't be read."}
    except Exception:
        return {"error": "Couldn't load the Photo of the Day right now."}

    if entry is None:
        return {"error": "No Photo of the Day in National Geographic's feed."}

    # Best effort from here on: any failure falls back to the thumbnail.
    image_url = entry["thumb"]
    with contextlib.suppress(Exception):
        page = _get(_article_url(entry["link"])).decode("utf-8", errors="ignore")
        image_url = _full_image_url(page, entry["date"]) or image_url
    url = image_url
    with contextlib.suppress(Exception):
        url = _photo_route(_store_photo(image_url, data_dir))

    result = {
        "url": url,
        "date": entry["date"],
        "title": entry["title"],
        "caption": entry["caption"],
        "source": entry["source"],
        "link": _article_url(entry["link"]),
        "fetched_at": int(time.time()),
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result


# ----------------------------------------------------------------------
# Photo serving
# ----------------------------------------------------------------------

# Read-only, declared safe for a loopback render with no session (see
# app/auth.py): the widget's <img src> points here. Nothing that writes,
# deletes, or reaches the network belongs in this list.
RENDER_SAFE_ENDPOINTS: tuple[str, ...] = (f"{PLUGIN_ID}_photos.serve_photo",)


def blueprint() -> Blueprint:
    bp = Blueprint(f"{PLUGIN_ID}_photos", __name__)

    @bp.get("/photos/<name>")
    def serve_photo(name: str) -> Any:
        if not _PHOTO_NAME_RE.match(name):
            abort(404)
        plugin = current_app.config["PLUGIN_REGISTRY"].get(PLUGIN_ID)
        if plugin is None:
            abort(404)
        response = send_from_directory(_photos_dir(Path(plugin.data_dir)), name)
        # A photo's file name is derived from its upstream id, so its bytes
        # never change under the same URL.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    return bp
