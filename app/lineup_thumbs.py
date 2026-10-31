"""Small immutable snapshots for Lineups; rendering stays off request threads."""

from __future__ import annotations

import logging
import re
import tempfile
import threading
from pathlib import Path

from flask import abort, current_app, request, send_file, url_for
from PIL import Image, features
from werkzeug.wrappers import Response

from app.state.page_store import Page

logger = logging.getLogger(__name__)
MAX_DIM = 360
_FORMAT = "WEBP" if features.check("webp") else "PNG"
_EXT = "webp" if _FORMAT == "WEBP" else "png"
_ENCODE_LOCK = threading.Lock()


def _paths(kind: str, key: str) -> tuple[Path, Path]:
    core = Path(current_app.config["DATA_ROOT"]) / "core"
    directory = core / ("renders" if kind == "sent" else "previews")
    return directory / f"{key}.png", directory / ".thumbs" / f"{key}-lu-v1-{MAX_DIM}.{_EXT}"


def page_compositions(pages: list[Page]) -> dict[str, str]:
    """Latest successful composition digest per dashboard, one log query."""
    events = current_app.config.get("EVENT_LOG")
    try:
        latest = events.latest_pushes([p.id for p in pages]) if events else {}
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


def page_thumbs(pages: list[Page], compositions: dict[str, str] | None = None) -> dict[str, str]:
    """One batched log lookup per view, one URL per dashboard across all rows.

    ``compositions`` (from :func:`page_compositions`) lets the caller share
    one snapshot between the thumbnail and the step's Push now digest.
    Unpushed pages keep their initial placeholder until the existing serial,
    single-flight render queue publishes a snapshot. No time-based refresh:
    the next push or definition edit supplies a new version instead.
    """
    from app import preview_cache
    from app.composer import PREVIEW_MAX_DIM, _preview_timezone_id, page_preview_token, preview_dims

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
            token = page_preview_token(page, dims)
            key = f"{page.id}__{token}"
            source, cached = _paths("draft", key)
            if source.is_file() or cached.is_file():
                out[page.id] = url_for("composer.lineup_thumbnail", kind="draft", key=key, ext=_EXT)
            else:
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
                )
        except Exception:
            logger.debug("lineups: preview unavailable for %s", page.id, exc_info=True)
    return out


def serve(kind: str, key: str, ext: str) -> Response:
    """Serve a version directly, without reading the page store or event log."""
    pattern = r"[0-9a-f]{6,64}" if kind == "sent" else r"[a-z0-9_][a-z0-9_-]*__[0-9a-f]{16}"
    if kind not in ("sent", "draft") or ext != _EXT or not re.fullmatch(pattern, key):
        abort(404)
    source, cached = _paths(kind, key)
    if not cached.is_file():
        # Serialise first encodes; readers never see a partial file and each
        # variant is resized once even when several cards ask simultaneously.
        with _ENCODE_LOCK:
            if not cached.is_file():
                if not source.is_file():
                    abort(404)
                cached.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(source) as im:
                    im.thumbnail((MAX_DIM, MAX_DIM), Image.Resampling.LANCZOS)
                    with tempfile.NamedTemporaryFile(
                        dir=cached.parent, suffix=".tmp", delete=False
                    ) as tmp:
                        try:
                            im.convert("RGB").save(
                                tmp,
                                format=_FORMAT,
                                **(
                                    {"quality": 75, "method": 4}
                                    if _FORMAT == "WEBP"
                                    else {"optimize": True}
                                ),
                            )
                            Path(tmp.name).replace(cached)
                        finally:
                            Path(tmp.name).unlink(missing_ok=True)
    response = send_file(
        cached,
        mimetype=f"image/{_EXT}",
        conditional=True,
        etag=f"lu-v1-{MAX_DIM}-{kind}-{key}-{_EXT}",
        max_age=31536000,
    )
    # Authenticated images belong in the user's browser cache, not shared proxies.
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response
