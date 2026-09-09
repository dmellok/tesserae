"""Server-side webfont cache for canvas pages and code elements.

The render sandbox has no network and a ``font-src data:`` policy, and the
headless renderer never fetches from a third party, so the only fonts a
dashboard could use were the ~39 families vendored in ``plugins/fonts_core``.
This module opens that set up without loosening either rule: a font is fetched
ONCE at author time (a Google Fonts family, or a direct ``.woff2`` / ``.ttf`` /
``.otf`` URL), stored under ``data/core/fonts/<slug>/`` with a small manifest,
and from then on served from the local origin exactly like a bundled font:

* ``/page-fonts/<slug>/<file>`` serves the files for the canvas / grid page
  CSS (``@font-face`` with a same-origin URL, fetched over loopback while the
  renderer composes a page, same as ``/page-assets``);
* ``/fonts/face/<slug>.css`` (composer) inlines the same faces as ``data:``
  URLs for the code element sandbox, which is what autolibs injects when an
  element's CSS names the family;
* the appearance catalog lists cached families next to the bundled ones, so a
  page's ``font`` field can name one.

Nothing here runs at render time. A render with a missing cache entry simply
has no ``@font-face`` for that family and the browser walks the author's
fallback stack, which is what happened before this module existed.

Remote fetches go through :mod:`app.net_guard` (http(s) only, no loopback or
private hosts, redirects re-validated, size capped). Google's CSS endpoint is
asked with a modern browser User-Agent so it answers with woff2 faces split
per subset; only the requested subsets (``latin`` by default) are kept, which
is what keeps a CJK family like Shippori Mincho at a few hundred KB instead of
several MB.

mypy --strict applies, see pyproject.toml.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.net_guard import fetch_bytes

_GOOGLE_CSS = "https://fonts.googleapis.com/css2"
# Google serves woff2 (and per-subset faces) only to browsers it recognises;
# the guard's default UA gets TTF for the whole family instead.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_MAX_CSS_BYTES: int = 512 * 1024
_MAX_FACE_BYTES: int = 4 * 1024 * 1024  # a single face; CJK woff2 slices are ~100 KB
_MAX_FACES: int = 24  # weights x styles x subsets, keeps a runaway request bounded

_SLUG_RE = re.compile(r"^[a-z0-9_]+$")
_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_STYLES: tuple[str, ...] = ("normal", "italic")
_DEFAULT_WEIGHTS: tuple[int, ...] = (400, 700)
_DEFAULT_SUBSETS: tuple[str, ...] = ("latin",)

# Magic bytes -> file extension. The bytes are only ever served back as fonts,
# never executed, so this is about keeping the folder to real font files (not
# HTML a hostile endpoint might hand back with a font URL).
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"wOF2", "woff2"),
    (b"wOFF", "woff"),
    (b"\x00\x01\x00\x00", "ttf"),
    (b"true", "ttf"),
    (b"OTTO", "otf"),
)
_FORMAT_BY_EXT: dict[str, str] = {
    "woff2": "woff2",
    "woff": "woff",
    "ttf": "truetype",
    "otf": "opentype",
}

_BLOCK_RE = re.compile(r"/\*\s*([\w-]+)\s*\*/\s*@font-face\s*\{(.*?)\}", re.S)
_STYLE_RE = re.compile(r"font-style:\s*(\w+)")
_WEIGHT_RE = re.compile(r"font-weight:\s*(\d+)")
_SRC_RE = re.compile(r"url\(([^)]+)\)\s*format\(['\"]?(\w+)['\"]?\)")
_RANGE_RE = re.compile(r"unicode-range:\s*([^;]+);")


class FontCacheError(ValueError):
    """A cache request was refused (bad family, not a font, nothing to keep, …)."""


@dataclass
class Face:
    weight: int
    style: str
    file: str
    format: str
    bytes: int
    subset: str = "latin"
    unicode_range: str = ""


@dataclass
class CachedFont:
    family: str
    slug: str
    source: dict[str, Any]
    faces: list[Face] = field(default_factory=list)

    @property
    def weights(self) -> list[int]:
        return sorted({f.weight for f in self.faces})

    @property
    def styles(self) -> list[str]:
        return sorted({f.style for f in self.faces})

    def record(self) -> dict[str, Any]:
        """The catalog / API shape."""
        return {
            "id": self.slug,
            "name": self.family,
            "source": "cached",
            "origin": self.source,
            "weights": self.weights,
            "styles": self.styles,
            "subsets": sorted({f.subset for f in self.faces}),
            "files": [f.file for f in self.faces],
            "bytes": sum(f.bytes for f in self.faces),
        }


# -- paths ---------------------------------------------------------------


def fonts_root(data_root: Path) -> Path:
    return Path(data_root) / "core" / "fonts"


def slugify(family: str) -> str:
    """``"Shippori Mincho"`` -> ``"shippori_mincho"``. The slug is the font's id
    in the appearance catalog and its folder name, so it is kept to a safe
    charset; a family that reduces to nothing is refused."""
    slug = re.sub(r"[^a-z0-9]+", "_", family.strip().lower()).strip("_")
    if not slug or not _SLUG_RE.match(slug):
        raise FontCacheError(f"invalid font family {family!r}")
    return slug


def font_dir(data_root: Path, slug: str) -> Path:
    if not _SLUG_RE.match(slug or ""):
        raise FontCacheError(f"invalid font id {slug!r}")
    return fonts_root(data_root) / slug


def local_url(slug: str, file: str) -> str:
    return f"/page-fonts/{slug}/{file}"


# -- manifest ------------------------------------------------------------


def _manifest_path(data_root: Path, slug: str) -> Path:
    return font_dir(data_root, slug) / "manifest.json"


def _load(data_root: Path, slug: str) -> CachedFont | None:
    path = _manifest_path(data_root, slug)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    faces: list[Face] = []
    for item in raw.get("faces") or []:
        if not isinstance(item, dict):
            continue
        try:
            faces.append(
                Face(
                    weight=int(item["weight"]),
                    style=str(item.get("style") or "normal"),
                    file=str(item["file"]),
                    format=str(item.get("format") or "woff2"),
                    bytes=int(item.get("bytes") or 0),
                    subset=str(item.get("subset") or "latin"),
                    unicode_range=str(item.get("unicode_range") or ""),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return CachedFont(
        family=str(raw.get("family") or slug),
        slug=slug,
        source=dict(raw.get("source") or {}),
        faces=faces,
    )


def _save(data_root: Path, font: CachedFont) -> None:
    directory = font_dir(data_root, font.slug)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "family": font.family,
        "slug": font.slug,
        "source": font.source,
        "faces": [asdict(f) for f in font.faces],
    }
    _manifest_path(data_root, font.slug).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def get(data_root: Path, font_id: str) -> CachedFont | None:
    """Look a cached font up by slug or by exact family name (either is what
    an author writes: the catalog id, or the name from their CSS)."""
    if not font_id:
        return None
    try:
        slug = font_id if _SLUG_RE.match(font_id) else slugify(font_id)
    except FontCacheError:
        return None
    font = _load(data_root, slug)
    if font is not None and font.faces:
        return font
    return None


def list_fonts(data_root: Path) -> list[CachedFont]:
    root = fonts_root(data_root)
    if not root.is_dir():
        return []
    out: list[CachedFont] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and _SLUG_RE.match(child.name):
            font = _load(data_root, child.name)
            if font is not None and font.faces:
                out.append(font)
    return out


def delete_font(data_root: Path, font_id: str) -> bool:
    font = get(data_root, font_id)
    if font is None:
        return False
    shutil.rmtree(font_dir(data_root, font.slug), ignore_errors=True)
    return True


# -- fetching ------------------------------------------------------------


def _sniff(data: bytes) -> str:
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    raise FontCacheError("not a font file (expected woff2, woff, ttf or otf)")


def _face_name(weight: int, style: str, subset: str, ext: str) -> str:
    name = f"{weight}{'i' if style == 'italic' else ''}"
    if subset != "latin":
        name += f"_{re.sub(r'[^a-z0-9]+', '_', subset.lower())}"
    return f"{name}.{ext}"


def _put_face(font: CachedFont, face: Face) -> None:
    """Replace any face with the same weight / style / subset, keep the rest."""
    font.faces = [
        f
        for f in font.faces
        if not (f.weight == face.weight and f.style == face.style and f.subset == face.subset)
    ]
    font.faces.append(face)
    font.faces.sort(key=lambda f: (f.subset != "latin", f.subset, f.weight, f.style))


def _fetch_face(
    data_root: Path,
    font: CachedFont,
    url: str,
    weight: int,
    style: str,
    subset: str,
    unicode_range: str = "",
) -> Face:
    try:
        raw, _ = fetch_bytes(url, headers={"User-Agent": _BROWSER_UA}, max_bytes=_MAX_FACE_BYTES)
    except Exception as err:
        raise FontCacheError(f"fetch failed: {type(err).__name__}: {err}") from err
    ext = _sniff(raw)
    file = _face_name(weight, style, subset, ext)
    directory = font_dir(data_root, font.slug)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / file).write_bytes(raw)
    return Face(
        weight=weight,
        style=style,
        file=file,
        format=_FORMAT_BY_EXT[ext],
        bytes=len(raw),
        subset=subset,
        unicode_range=unicode_range.strip(),
    )


def _normalise_weights(weights: Any) -> list[int]:
    if weights is None:
        return list(_DEFAULT_WEIGHTS)
    out: set[int] = set()
    for w in weights if isinstance(weights, (list, tuple, set)) else [weights]:
        try:
            n = int(w)
        except (TypeError, ValueError) as err:
            raise FontCacheError(f"invalid weight {w!r}") from err
        if n < 100 or n > 900:
            raise FontCacheError(f"weight {n} out of range (100-900)")
        out.add(n)
    if not out:
        raise FontCacheError("no weights requested")
    return sorted(out)


def _normalise_styles(styles: Any) -> list[str]:
    if styles is None:
        return ["normal"]
    out: list[str] = []
    for s in styles if isinstance(styles, (list, tuple, set)) else [styles]:
        name = str(s).strip().lower()
        if name not in _STYLES:
            raise FontCacheError(f"invalid style {s!r} (normal or italic)")
        if name not in out:
            out.append(name)
    return out or ["normal"]


def _normalise_subsets(subsets: Any) -> list[str]:
    if subsets is None:
        return list(_DEFAULT_SUBSETS)
    out: list[str] = []
    for s in subsets if isinstance(subsets, (list, tuple, set)) else [subsets]:
        name = str(s).strip().lower()
        if not re.match(r"^[a-z0-9-]+$", name):
            raise FontCacheError(f"invalid subset {s!r}")
        if name not in out:
            out.append(name)
    return out or list(_DEFAULT_SUBSETS)


def google_css_url(family: str, weights: list[int], styles: list[str]) -> str:
    """The css2 request for a family at the given weights / styles. Google wants
    the ``ital,wght`` tuples sorted, ital first."""
    tuples = sorted((1 if s == "italic" else 0, w) for s in styles for w in weights)
    spec = ";".join(f"{i},{w}" for i, w in tuples)
    return f"{_GOOGLE_CSS}?family={quote(family)}:ital,wght@{spec}&display=block"


def parse_google_css(css: str) -> list[dict[str, str]]:
    """Every ``@font-face`` block in a css2 response as ``{subset, style,
    weight, url, format, unicode_range}``. Google labels each block with a
    ``/* latin */``-style comment naming its subset."""
    out: list[dict[str, str]] = []
    for m in _BLOCK_RE.finditer(css):
        subset, body = m.group(1), m.group(2)
        style = _STYLE_RE.search(body)
        weight = _WEIGHT_RE.search(body)
        src = _SRC_RE.search(body)
        rng = _RANGE_RE.search(body)
        if not (weight and src):
            continue
        out.append(
            {
                "subset": subset.lower(),
                "style": (style.group(1).lower() if style else "normal"),
                "weight": weight.group(1),
                "url": src.group(1).strip("'\" "),
                "format": src.group(2).lower(),
                "unicode_range": rng.group(1).strip() if rng else "",
            }
        )
    return out


def cache_google_font(
    data_root: Path,
    family: str,
    *,
    weights: Any = None,
    styles: Any = None,
    subsets: Any = None,
) -> CachedFont:
    """Fetch a Google Fonts family once and store the requested faces.

    Only blocks for the requested subsets are kept (``latin`` by default), so
    a family that Google splits into dozens of unicode-range slices stays
    small. A family Google doesn't know, or one with none of the requested
    subsets, is refused with the subsets it does have."""
    family = family.strip()
    slug = slugify(family)
    want_weights = _normalise_weights(weights)
    want_styles = _normalise_styles(styles)
    want_subsets = _normalise_subsets(subsets)
    url = google_css_url(family, want_weights, want_styles)
    try:
        raw, _ = fetch_bytes(url, headers={"User-Agent": _BROWSER_UA}, max_bytes=_MAX_CSS_BYTES)
    except Exception as err:
        raise FontCacheError(
            f"Google Fonts has no family {family!r} at those weights ({type(err).__name__}: {err})"
        ) from err
    blocks = parse_google_css(raw.decode("utf-8", "replace"))
    if not blocks:
        raise FontCacheError(f"Google Fonts returned no faces for {family!r}")
    keep = [b for b in blocks if b["subset"] in want_subsets]
    if not keep:
        have = sorted({b["subset"] for b in blocks})
        raise FontCacheError(
            f"{family!r} has no {', '.join(want_subsets)} subset; available: {', '.join(have)}"
        )
    if len(keep) > _MAX_FACES:
        raise FontCacheError(f"too many faces ({len(keep)}); narrow the weights, styles or subsets")

    font = _load(data_root, slug) or CachedFont(family=family, slug=slug, source={})
    font.family = family
    font.source = {
        "kind": "google",
        "css": url,
        "weights": want_weights,
        "styles": want_styles,
        "subsets": want_subsets,
    }
    for b in keep:
        face = _fetch_face(
            data_root,
            font,
            b["url"],
            int(b["weight"]),
            b["style"],
            b["subset"],
            b["unicode_range"],
        )
        _put_face(font, face)
    _save(data_root, font)
    return font


def cache_font_url(
    data_root: Path,
    family: str,
    url: str,
    *,
    weight: Any = 400,
    style: Any = "normal",
) -> CachedFont:
    """Fetch one font file from a direct URL and store it as a face of
    ``family`` (a self-hosted woff2, a GitHub release asset, …). Repeat per
    weight / style; the same weight and style replaces the earlier file."""
    family = family.strip()
    slug = slugify(family)
    w = _normalise_weights([weight])[0]
    s = _normalise_styles([style])[0]
    if not url.strip():
        raise FontCacheError("provide a font file url")
    font = _load(data_root, slug) or CachedFont(family=family, slug=slug, source={})
    font.family = family
    face = _fetch_face(data_root, font, url.strip(), w, s, "latin")
    _put_face(font, face)
    font.source = {"kind": "url", "last_url": url.strip()}
    _save(data_root, font)
    return font


# -- CSS -----------------------------------------------------------------


def _rule(font: CachedFont, face: Face, src: str) -> str:
    rng = f" unicode-range: {face.unicode_range};" if face.unicode_range else ""
    return (
        f"@font-face {{ font-family: '{font.family}'; font-style: {face.style}; "
        f"font-weight: {face.weight}; src: url({src}) format('{face.format}'); "
        f"font-display: block;{rng} }}"
    )


def font_face_css(font: CachedFont, url_for_file: Callable[[str, str], str] = local_url) -> str:
    """``@font-face`` rules pointing at the served files (page / editor CSS)."""
    return "\n".join(_rule(font, f, f"'{url_for_file(font.slug, f.file)}'") for f in font.faces)


def font_face_css_datauri(data_root: Path, font: CachedFont) -> str:
    """The same rules with each file embedded as a ``data:`` URL, for the code
    element sandbox (``font-src data:`` only). Faces whose file is missing on
    disk are skipped rather than emitted broken."""
    rules: list[str] = []
    mime = {
        "woff2": "font/woff2",
        "woff": "font/woff",
        "truetype": "font/ttf",
        "opentype": "font/otf",
    }
    directory = font_dir(data_root, font.slug)
    for face in font.faces:
        if not _FILE_RE.match(face.file):
            continue
        try:
            data = (directory / face.file).read_bytes()
        except OSError:
            continue
        b64 = base64.b64encode(data).decode("ascii")
        rules.append(_rule(font, face, f"data:{mime.get(face.format, 'font/woff2')};base64,{b64}"))
    return "\n".join(rules)


def all_font_face_css(data_root: Path) -> str:
    """Rules for every cached family, appended to a page's bundled font CSS so
    the canvas / grid page ``font`` field and per-cell fonts can name one."""
    return "\n".join(font_face_css(f) for f in list_fonts(data_root))


def mtime(data_root: Path, slug: str) -> float:
    """Manifest mtime, the cache key for anything derived from a font's files."""
    try:
        return _manifest_path(data_root, slug).stat().st_mtime
    except (OSError, FontCacheError):
        return 0.0
