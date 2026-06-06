"""picture_apple_album, public iCloud Shared Album rotation.

Uses Apple's *undocumented* but long-stable shared-album endpoints:
  POST {base}/webstream      → manifest of photos in the album
  POST {base}/webasseturls   → time-limited signed URLs for specific
                               photo GUIDs (keyed by derivative checksum)

The initial base URL is derived from the token's character set
(``p{NN}-sharedstreams.icloud.com``). Apple sometimes returns HTTP 330
with an ``X-Apple-MMe-Host`` redirect when the album lives on a
different partition; we follow it once and cache the resolved host with
the manifest.

Two caches in data_dir:
  manifest_<token>.json  , webstream payload (TTL 6h; album metadata
                            rarely changes)
  asset_<guid>.json      , signed URLs for one photo (TTL 50 min;
                            Apple's URLs expire at ~1 h)

References (reverse-engineered, MIT):
  https://github.com/ghostops/ICloud-Shared-Album
  https://github.com/bertrandom/icloud-shared-album-to-flickr
"""

from __future__ import annotations

import contextlib
import json
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
HTTP_TIMEOUT_S = 15
MANIFEST_TTL_S = 6 * 3600
ASSET_TTL_S = 50 * 60
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_4) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/56.0.2924.87 Safari/537.36"
)
REQ_HEADERS = {
    "Origin": "https://www.icloud.com",
    "Accept-Language": "en-US,en;q=0.8",
    "User-Agent": USER_AGENT,
    "Content-Type": "text/plain",
    "Accept": "*/*",
    "Referer": "https://www.icloud.com/sharedalbum/",
    "Connection": "keep-alive",
}
TOKEN_RE = re.compile(r"^[A-Za-z0-9;]+$")


# ----- token parsing + URL derivation --------------------------------


def _parse_token(raw: str) -> str:
    """Extract the album token from either the full share link
    (https://www.icloud.com/sharedalbum/#B0xxxxxx) or a bare token."""
    s = (raw or "").strip()
    if "#" in s:
        s = s.split("#", 1)[1]
    s = s.strip().strip("/")
    return s


def _base62_to_int(s: str) -> int:
    n = 0
    for c in s:
        idx = BASE62.find(c)
        if idx < 0:
            raise ValueError(f"non-base62 char {c!r} in token")
        n = n * 62 + idx
    return n


def _partition_for(token: str) -> int:
    """Mirror ghostops/ICloud-Shared-Album's partition derivation:
    tokens starting with 'A' use 1 char, everything else uses 2."""
    if not token:
        raise ValueError("empty token")
    if token[0] == "A":
        return _base62_to_int(token[1:2])
    return _base62_to_int(token[1:3])


def _initial_base_url(token: str) -> str:
    n = _partition_for(token)
    prefix = f"0{n}" if n < 10 else str(n)
    return f"https://p{prefix}-sharedstreams.icloud.com/{token}/sharedstreams/"


# ----- HTTP helpers --------------------------------------------------


def _post_json(url: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=REQ_HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as err:
        # Apple's 330 redirect arrives here. Read the body to find the
        # new host; the caller handles re-dispatching.
        raw = err.read().decode("utf-8", errors="replace")
        try:
            payload: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"_raw": raw}
        return err.code, payload


def _resolve_base_url(token: str) -> tuple[str, dict[str, Any]]:
    """Resolve the working base URL for ``token`` by attempting a
    webstream POST and following Apple's 330 redirect if it comes back.
    Returns (base_url, first_webstream_response) so the caller doesn't
    need a second HTTP round-trip."""
    base = _initial_base_url(token)
    status, body = _post_json(base + "webstream", {"streamCtag": None})
    if status == 330:
        host = body.get("X-Apple-MMe-Host")
        if not host:
            raise RuntimeError("330 redirect with no X-Apple-MMe-Host")
        base = f"https://{host}/{token}/sharedstreams/"
        status, body = _post_json(base + "webstream", {"streamCtag": None})
    if status >= 400 or "photos" not in body:
        raise RuntimeError(f"webstream failed: HTTP {status} {body!r}"[:200])
    return base, body


# ----- manifest cache ------------------------------------------------


def _safe_cache_key(token: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", token)[:40]


def _read_cache(path: Path, ttl: int) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime >= ttl:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(payload), encoding="utf-8")


def _load_manifest(token: str, data_dir: Path) -> dict[str, Any]:
    cache_path = data_dir / f"manifest_{_safe_cache_key(token)}.json"
    cached = _read_cache(cache_path, MANIFEST_TTL_S)
    if cached is not None:
        return cached
    base, body = _resolve_base_url(token)
    photos = body.get("photos") or []
    slim_photos = []
    for p in photos:
        derivatives = p.get("derivatives") or {}
        slim_derivs = []
        for size_key, d in derivatives.items():
            if not isinstance(d, dict) or not d.get("checksum"):
                continue
            slim_derivs.append(
                {
                    "size_key": size_key,
                    "checksum": d["checksum"],
                    "width": int(d.get("width") or 0),
                    "height": int(d.get("height") or 0),
                    "fileSize": int(d.get("fileSize") or 0),
                }
            )
        if not slim_derivs:
            continue
        slim_photos.append(
            {
                "guid": p.get("photoGuid"),
                "width": int(p.get("width") or 0),
                "height": int(p.get("height") or 0),
                "dateCreated": p.get("dateCreated"),
                "derivatives": slim_derivs,
            }
        )
    manifest = {
        "base": base,
        "stream": body.get("streamName") or "",
        "owner": f"{body.get('userFirstName') or ''} {body.get('userLastName') or ''}".strip(),
        "photos": slim_photos,
        "fetched_at": int(time.time()),
    }
    _write_cache(cache_path, manifest)
    return manifest


def _asset_url_for(base: str, token: str, photo: dict[str, Any], data_dir: Path) -> str | None:
    """Resolve the signed URL for a photo's largest derivative.
    Caches per-photo for ASSET_TTL_S so a render burst doesn't
    repeatedly hit /webasseturls."""
    guid = photo.get("guid")
    if not guid:
        return None
    cache_path = data_dir / f"asset_{_safe_cache_key(token)}_{_safe_cache_key(guid)}.json"
    cached = _read_cache(cache_path, ASSET_TTL_S)
    if cached and cached.get("url"):
        return str(cached["url"])

    status, body = _post_json(base + "webasseturls", {"photoGuids": [guid]})
    if status >= 400:
        return None
    items = body.get("items") or {}
    # Pick the largest derivative we know about (highest fileSize); look
    # up its checksum in the items map for the signed URL.
    derivs = sorted(
        photo.get("derivatives") or [],
        key=lambda d: int(d.get("fileSize") or 0),
        reverse=True,
    )
    for d in derivs:
        item = items.get(d["checksum"])
        if not item:
            continue
        location = item.get("url_location")
        path = item.get("url_path")
        if not location or not path:
            continue
        url = f"https://{location}{path}"
        _write_cache(cache_path, {"url": url})
        return url
    return None


# ----- orientation filter -------------------------------------------


def _orientation_of(p: dict[str, Any]) -> str:
    w = int(p.get("width") or 0)
    h = int(p.get("height") or 0)
    if w == 0 or h == 0:
        return "any"
    ratio = w / h
    if abs(ratio - 1.0) <= 0.05:
        return "square"
    return "landscape" if ratio > 1 else "portrait"


def _filter_orientation(photos: list[dict[str, Any]], wanted: str) -> list[dict[str, Any]]:
    if wanted == "any":
        return photos
    return [p for p in photos if _orientation_of(p) == wanted]


# ----- plugin contract ----------------------------------------------


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    token = _parse_token(options.get("album") or "")
    if not token:
        return {
            "error": "Paste the share link from a public iCloud Shared Album "
            "(https://www.icloud.com/sharedalbum/#B0…). Enable "
            "'Public Website' on the album in Photos to get the link.",
            "url": None,
        }
    if not TOKEN_RE.match(token):
        return {"error": f"Bad album token: {token[:24]}…", "url": None}

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)

    try:
        manifest = _load_manifest(token, data_dir)
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}"[:200], "url": None}

    photos = manifest.get("photos") or []
    if not photos:
        return {
            "error": "Album is empty (or the token isn't a published shared album).",
            "url": None,
        }

    orientation = (options.get("orientation") or "any").lower()
    filtered = _filter_orientation(photos, orientation)
    if not filtered:
        return {"error": f"No {orientation} photos in this album.", "url": None}

    mode = options.get("mode", "random")
    if mode == "sequential":
        suffix = f"_{orientation}" if orientation != "any" else ""
        idx_file = data_dir / f".seq_idx_{_safe_cache_key(token)}{suffix}"
        try:
            current = int(idx_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            current = -1
        next_idx = (current + 1) % len(filtered)
        with contextlib.suppress(OSError):
            idx_file.write_text(str(next_idx), encoding="utf-8")
        photo = filtered[next_idx]
    else:
        photo = random.choice(filtered)

    url = _asset_url_for(manifest["base"], token, photo, data_dir)
    if not url:
        return {"error": "Could not resolve a signed URL for the picked photo.", "url": None}

    return {
        "url": url,
        "guid": photo.get("guid"),
        "date": photo.get("dateCreated"),
        "stream": manifest.get("stream"),
        "owner": manifest.get("owner"),
        "count": len(filtered),
    }
