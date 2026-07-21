"""Per-dashboard image asset catalog.

Each canvas dashboard gets its own folder under
``data/core/page_assets/<page_id>/`` where images it uses are cached, so a
code element can reference a stable local copy instead of hotlinking a remote
URL on every render (which breaks when the upstream is down, leaks a request to
a third party each refresh, and can't be reproduced offline). The folder is
deleted with the dashboard, so assets never outlive the page that owns them.

Remote images are fetched through :mod:`app.net_guard`, so caching an image URL
carries the same SSRF protection as a URL data source: http(s) only, no
loopback / private hosts, redirects re-validated, size capped.

mypy --strict applies, see pyproject.toml.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from app.net_guard import fetch_bytes

# Image content types we'll store, mapped to the extension we save under. The
# fetched bytes are only ever served back with this type, never executed, so
# the allowlist is about keeping the folder to real images (not HTML/JS a
# hostile endpoint might return with an image URL).
_EXT_BY_TYPE: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/bmp": "bmp",
    "image/avif": "avif",
}
_MAX_IMAGE_BYTES: int = 10 * 1024 * 1024  # 10 MiB
_PAGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class AssetError(ValueError):
    """A cache/upload was refused (bad page id, non-image, too big, …)."""


def _safe_page_id(page_id: str) -> str:
    if not page_id or not _PAGE_ID_RE.match(page_id):
        raise AssetError(f"invalid page id {page_id!r}")
    return page_id


def assets_root(data_root: Path) -> Path:
    return Path(data_root) / "core" / "page_assets"


def assets_dir(data_root: Path, page_id: str) -> Path:
    return assets_root(data_root) / _safe_page_id(page_id)


def local_url(page_id: str, name: str) -> str:
    return f"/page-assets/{_safe_page_id(page_id)}/{name}"


def _store(data_root: Path, page_id: str, data: bytes, ext: str) -> dict[str, str | int]:
    """Content-address ``data`` into the page's folder under ``<sha>.<ext>`` and
    return its record. Same bytes -> same name, so re-caching is idempotent."""
    directory = assets_dir(data_root, page_id)
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()[:16]
    name = f"{digest}.{ext}"
    path = directory / name
    if not path.exists():
        path.write_bytes(data)
    return {"name": name, "url": local_url(page_id, name), "bytes": len(data)}


def cache_url(
    data_root: Path, page_id: str, url: str, *, headers: dict[str, str] | None = None
) -> dict[str, str | int]:
    """Fetch a remote image and store it in the page's folder. Returns
    ``{name, url, bytes}``. Raises :class:`AssetError` for a non-image response
    or a URL the SSRF guard refuses (the guard's own error message is kept)."""
    _safe_page_id(page_id)
    try:
        raw, content_type = fetch_bytes(url, headers=headers, max_bytes=_MAX_IMAGE_BYTES)
    except Exception as err:
        raise AssetError(f"fetch failed: {type(err).__name__}: {err}") from err
    ext = _EXT_BY_TYPE.get(content_type)
    if ext is None:
        raise AssetError(f"not a supported image (content-type {content_type or 'unknown'!r})")
    return _store(data_root, page_id, raw, ext)


def save_bytes(
    data_root: Path, page_id: str, data: bytes, content_type: str
) -> dict[str, str | int]:
    """Store already-fetched image bytes (an upload) in the page's folder."""
    ext = _EXT_BY_TYPE.get(content_type.split(";", 1)[0].strip().lower())
    if ext is None:
        raise AssetError(f"not a supported image (content-type {content_type or 'unknown'!r})")
    if len(data) > _MAX_IMAGE_BYTES:
        raise AssetError(f"image exceeds {_MAX_IMAGE_BYTES} byte cap")
    return _store(data_root, page_id, data, ext)


def list_assets(data_root: Path, page_id: str) -> list[dict[str, str | int]]:
    directory = assets_dir(data_root, page_id)
    if not directory.is_dir():
        return []
    out: list[dict[str, str | int]] = []
    for path in sorted(directory.iterdir()):
        if path.is_file():
            out.append(
                {
                    "name": path.name,
                    "url": local_url(page_id, path.name),
                    "bytes": path.stat().st_size,
                }
            )
    return out


def delete_asset(data_root: Path, page_id: str, name: str) -> bool:
    if not _NAME_RE.match(name):
        raise AssetError(f"invalid asset name {name!r}")
    path = assets_dir(data_root, page_id) / name
    if path.is_file():
        path.unlink()
        return True
    return False


def delete_all(data_root: Path, page_id: str) -> None:
    """Remove a dashboard's whole asset folder. Called when the page is deleted
    so cached images never outlive their dashboard. Best-effort and idempotent:
    a missing folder is a no-op."""
    try:
        directory = assets_dir(data_root, page_id)
    except AssetError:
        return
    shutil.rmtree(directory, ignore_errors=True)
