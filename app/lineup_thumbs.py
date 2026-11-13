"""Small immutable snapshots for Lineups; rendering stays off request threads."""

from __future__ import annotations

import hashlib
import logging
import re
import tempfile
import threading
import urllib.parse
from pathlib import Path
from typing import Any

from flask import abort, current_app, request, send_file, url_for
from PIL import Image, features
from werkzeug.wrappers import Response

from app.state.page_store import Page

logger = logging.getLogger(__name__)
MAX_DIM = 360
#: The lightbox's larger rendition (``?lg=1`` on a thumbnail URL).
LARGE_DIM = 1200
_FORMAT = "WEBP" if features.check("webp") else "PNG"
_EXT = "webp" if _FORMAT == "WEBP" else "png"
_ENCODE_LOCK = threading.Lock()


def _paths(kind: str, key: str, dim: int = MAX_DIM) -> tuple[Path, Path]:
    core = Path(current_app.config["DATA_ROOT"]) / "core"
    directory = core / ("renders" if kind == "sent" else "previews")
    return directory / f"{key}.png", directory / ".thumbs" / f"{key}-lu-v2-{dim}.{_EXT}"


def page_compositions(pages: list[Page], device_id: str | None = None) -> dict[str, str]:
    """Latest successful composition digest per dashboard, one log query.
    ``device_id`` keeps only pushes that went to that display."""
    events = current_app.config.get("EVENT_LOG")
    try:
        latest = events.latest_pushes([p.id for p in pages], device_id) if events else {}
    except Exception:
        logger.debug("lineups: push lookup failed", exc_info=True)
        latest = {}
    out: dict[str, str] = {}
    for page in pages:
        row = latest.get(page.id)
        digest = (row.extra.get("composition_digest") or row.digest) if row else None
        if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{6,64}", digest):
            out[page.id] = digest
    return out


def sent_thumb_url(digest: object) -> str:
    """The immutable thumbnail URL for a retained frame in the renders dir
    (a composition or device preview), or ``""`` when it is not on disk."""
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{6,64}", digest):
        return ""
    source, cached = _paths("sent", digest)
    if not (source.is_file() or cached.is_file()):
        return ""
    return url_for("composer.lineup_thumbnail", kind="sent", key=digest, ext=_EXT)


def _draft_thumb(page: Page, dims: tuple[int, int], device_id: str = "") -> str:
    """The thumbnail URL of ``page`` drawn at ``dims`` (for ``device_id`` when
    given: the compose route then lays it out as for that display), or ``""``
    while it is queued to render."""
    from app import preview_cache
    from app.composer import PREVIEW_MAX_DIM, _preview_timezone_id, page_preview_token

    token = page_preview_token(page, dims)
    who = f"-d{hashlib.sha256(device_id.encode()).hexdigest()[:10]}" if device_id else ""
    key = f"{page.id}{who}__{token}"
    source, cached = _paths("draft", key)
    if source.is_file() or cached.is_file():
        return url_for("composer.lineup_thumbnail", kind="draft", key=key, ext=_EXT)
    preview_cache.submit(
        key=str(source),
        base_url=request.host_url.rstrip("/"),
        page_id=page.id,
        width=dims[0],
        height=dims[1],
        cache_path=source,
        thumb_max=PREVIEW_MAX_DIM,
        pool=current_app.config.get("BROWSER_POOL"),
        timezone_id=_preview_timezone_id(),
        query=f"&device_id={urllib.parse.quote(device_id)}" if device_id else "",
    )
    return ""


def shapes_differ(page: Page, devices: Any) -> bool:
    """True when the displays a dashboard is bound to are not all of one ratio,
    so its one shared thumbnail (the largest panel's) is the wrong shape on some."""
    from app.panel import device_panel

    ratios: list[float] = []
    for did in dict.fromkeys(page.device_ids):
        device = devices.devices.get(did) if devices is not None else None
        panel = device_panel(device) if device is not None else None
        if panel is not None and panel.w > 0 and panel.h > 0:
            ratios.append(panel.w / panel.h)
    return bool(ratios) and (max(ratios) - min(ratios)) > 0.01 * min(ratios)


def display_thumbs(
    device_id: str, dims: tuple[int, int], pages: list[Page]
) -> dict[str, tuple[str, str | None]]:
    """Each dashboard as one display shows it: ``{page id: (thumbnail URL,
    composition digest)}``. The picture last pushed to that display when it is
    kept, else the dashboard drawn at the display's own panel size (``dims``);
    the digest is ``None`` for a drawing. A dashboard shared by displays of
    different shapes thus gets each display's own ratio, not the largest
    panel's."""
    compositions = page_compositions(pages, device_id)
    out: dict[str, tuple[str, str | None]] = {}
    for page in pages:
        digest = compositions.get(page.id)
        url = sent_thumb_url(digest) if digest else ""
        if url:
            out[page.id] = (url, digest)
            continue
        try:
            out[page.id] = (_draft_thumb(page, dims, device_id), None)
        except Exception:
            logger.debug("lineups: preview unavailable for %s", page.id, exc_info=True)
            out[page.id] = ("", None)
    return out


def page_thumbs(pages: list[Page], compositions: dict[str, str] | None = None) -> dict[str, str]:
    """One batched log lookup per view, one URL per dashboard across all rows.

    ``compositions`` (from :func:`page_compositions`) lets the caller share
    one snapshot between the thumbnail and the step's Push now digest.
    Unpushed pages keep their initial placeholder until the existing serial,
    single-flight render queue publishes a snapshot. No time-based refresh:
    the next push or definition edit supplies a new version instead.
    """
    from app.composer import preview_dims

    if compositions is None:
        compositions = page_compositions(pages)
    out: dict[str, str] = {}
    for page in pages:
        out[page.id] = ""
        digest = compositions.get(page.id)
        if digest:
            source, cached = _paths("sent", digest)
            if source.is_file() or cached.is_file():
                out[page.id] = url_for(
                    "composer.lineup_thumbnail", kind="sent", key=digest, ext=_EXT
                )
                continue
        try:
            dims = preview_dims(
                page,
                current_app.config.get("DEVICE_REGISTRY"),
                current_app.config.get("SETTINGS_STORE"),
            )
            out[page.id] = _draft_thumb(page, dims)
        except Exception:
            logger.debug("lineups: preview unavailable for %s", page.id, exc_info=True)
    return out


def exact_fit(size: tuple[int, int], max_w: int, max_h: int) -> tuple[int, int]:
    """Fit ``size`` inside ``max_w`` x ``max_h`` (never enlarging) at the
    source's exact aspect ratio.

    A plain thumbnail rounds one side (3840x2160 at 360 wide is 360x203, not
    202.5), and a card sized by the display's true ratio then shows a
    sub-pixel strip of its background along the image: a thin light line on a
    dark photo. Stepping the long side down a few pixels finds a size whose
    other side is a whole number (352x198 for 16:9); without one within 8%
    the plain rounding is kept."""
    w, h = size
    if w <= 0 or h <= 0:
        return max(1, w), max(1, h)
    scale = min(max_w / w, max_h / h, 1.0)
    if w >= h:
        long_side, ratio = max(1, int(w * scale)), h / w
    else:
        long_side, ratio = max(1, int(h * scale)), w / h
    for candidate in range(long_side, max(0, int(long_side * 0.92)), -1):
        other = candidate * ratio
        if abs(other - round(other)) < 0.01 and round(other) >= 1:
            pair = (candidate, round(other))
            return pair if w >= h else (pair[1], pair[0])
    other = max(1, round(long_side * ratio))
    return (long_side, other) if w >= h else (other, long_side)


def exact_thumb_size(size: tuple[int, int], dim: int) -> tuple[int, int]:
    """:func:`exact_fit` inside a ``dim`` square, the Lineups card bound."""
    return exact_fit(size, dim, dim)


def serve(kind: str, key: str, ext: str, large: bool = False) -> Response:
    """Serve a version directly, without reading the page store or event log.
    ``large`` is the lightbox rendition (``LARGE_DIM``) rather than the card's."""
    dim = LARGE_DIM if large else MAX_DIM
    pattern = r"[0-9a-f]{6,64}" if kind == "sent" else r"[a-z0-9_][a-z0-9_-]*__[0-9a-f]{16}"
    if kind not in ("sent", "draft") or ext != _EXT or not re.fullmatch(pattern, key):
        abort(404)
    source, cached = _paths(kind, key, dim)
    if not cached.is_file():
        # Serialise first encodes; readers never see a partial file and each
        # variant is resized once even when several cards ask simultaneously.
        with _ENCODE_LOCK:
            if not cached.is_file():
                if not source.is_file():
                    abort(404)
                cached.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(source) as src:
                    im = src.resize(exact_thumb_size(src.size, dim), Image.Resampling.LANCZOS)
                    tmp_path: Path | None = None
                    try:
                        with tempfile.NamedTemporaryFile(
                            dir=cached.parent, suffix=".tmp", delete=False
                        ) as tmp:
                            tmp_path = Path(tmp.name)
                            im.convert("RGB").save(
                                tmp,
                                format=_FORMAT,
                                **(
                                    {"quality": 82 if large else 75, "method": 4}
                                    if _FORMAT == "WEBP"
                                    else {"optimize": True}
                                ),
                            )
                        # Closed before the rename: Windows refuses to move an open file.
                        tmp_path.replace(cached)
                    finally:
                        if tmp_path is not None:
                            tmp_path.unlink(missing_ok=True)
    response = send_file(
        cached,
        mimetype=f"image/{_EXT}",
        conditional=True,
        etag=f"lu-v2-{dim}-{kind}-{key}-{_EXT}",
        max_age=31536000,
    )
    # Authenticated images belong in the user's browser cache, not shared proxies.
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response
