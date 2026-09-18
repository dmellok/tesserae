"""Note pushes for the Send page, plus the paste-detection rule it shares
with the browser.

A note is a few lines of text set in a panel theme: the first line is the
headline, the rest is the body. It renders through the same headless
Chromium path a webpage push uses (:func:`app.renderer.render_to_png`),
so the panel's colour handling, font stack and dithering are whatever
every other push gets. The page is a self-contained ``data:`` URL: the
chosen theme's Spectra variables are inlined, so nothing has to be
fetched from the app while rendering.

:func:`detect_kind` is the server-side twin of the rule in
``static/pages/send.js``. Both must agree, or the page would post to an
endpoint the server would classify differently.
"""

from __future__ import annotations

import base64
import html
import re
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urlsplit

from app.renderer import BrowserPool, RenderRequest, render_to_png
from app.state.theme_registry import parse_theme_blocks

SendKind = Literal["image", "webpage", "note", ""]

#: Path suffixes that mark an http(s) link as a picture rather than a page.
IMAGE_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic"}
)

NOTE_SIZES: Final[tuple[str, ...]] = ("large", "medium", "small")
NOTE_ALIGNS: Final[tuple[str, ...]] = ("left", "center", "right")
#: The theme a note falls back to when the form says "panel default".
DEFAULT_NOTE_THEME: Final[str] = "light"

_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


def detect_kind(text: str) -> SendKind:
    """Classify pasted text the way the Send page does.

    A single http(s) URL whose path ends in an image extension is an
    ``image``; any other single http(s) URL is a ``webpage``; anything
    else non-empty is a ``note``. Blank input is ``""``."""
    stripped = text.strip()
    if not stripped:
        return ""
    if "\n" not in stripped and _URL_RE.match(stripped):
        last = urlsplit(stripped).path.rsplit("/", 1)[-1].lower()
        suffix = "." + last.rsplit(".", 1)[-1] if "." in last else ""
        return "image" if suffix in IMAGE_EXTENSIONS else "webpage"
    return "note"


def normalise_size(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    return value if value in NOTE_SIZES else NOTE_SIZES[0]


def normalise_align(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if value == "centre":
        value = "center"
    return value if value in NOTE_ALIGNS else "center"


def split_note(text: str) -> tuple[str, str]:
    """``(headline, body)``: the first non-blank line and the rest, with
    surrounding blank lines trimmed from the body."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return "", ""
    headline = lines[0].strip()
    body = "\n".join(lines[1:]).strip("\n")
    return headline, body


def note_label(text: str, *, limit: int = 60) -> str:
    """The History target for a note: its headline, shortened."""
    headline, _ = split_note(text)
    headline = headline or "Note"
    return headline if len(headline) <= limit else headline[: limit - 1].rstrip() + "…"


@lru_cache(maxsize=1)
def _bundled_theme_blocks() -> dict[str, dict[str, str]]:
    css = Path(__file__).resolve().parent.parent / "static" / "style" / "spectra-tokens.css"
    if not css.is_file():
        return {}
    return parse_theme_blocks(css.read_text(encoding="utf-8"))


def theme_variables(theme_id: str | None, extra_css: str = "") -> dict[str, str]:
    """The Spectra variable block for ``theme_id``.

    Bundled themes come from ``spectra-tokens.css``; ``extra_css`` is the
    user + community theme CSS the app serves at ``/themes/*.css``, so a
    saved or installed theme resolves the same way a dashboard's would.
    An unknown or empty id falls back to :data:`DEFAULT_NOTE_THEME`."""
    wanted = (theme_id or "").strip() or DEFAULT_NOTE_THEME
    blocks = dict(_bundled_theme_blocks())
    if extra_css:
        blocks.update(parse_theme_blocks(extra_css))
    chosen = blocks.get(wanted) or blocks.get(DEFAULT_NOTE_THEME) or {}
    return dict(chosen)


_SIZE_CSS: Final[dict[str, tuple[str, str]]] = {
    # (headline, body) in vmin so the same page reads right at every panel size.
    "large": ("13vmin", "5.4vmin"),
    "medium": ("9.5vmin", "4.2vmin"),
    "small": ("6.5vmin", "3.2vmin"),
}
_ALIGN_CSS: Final[dict[str, tuple[str, str]]] = {
    "left": ("flex-start", "left"),
    "center": ("center", "center"),
    "right": ("flex-end", "right"),
}


def note_html(
    text: str,
    *,
    size: str = "large",
    align: str = "center",
    theme_vars: dict[str, str] | None = None,
) -> str:
    """A complete HTML document for the note, ready to screenshot or to
    show in the Send page's live preview iframe."""
    headline, body = split_note(text)
    head_fs, body_fs = _SIZE_CSS[normalise_size(size)]
    items, text_align = _ALIGN_CSS[normalise_align(align)]
    vars_css = "".join(f"{k}:{v};" for k, v in (theme_vars or {}).items())
    body_html = f'<div class="note-body">{html.escape(body)}</div>' if body else ""
    rule_html = '<div class="note-rule"></div>' if body else ""
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>Note</title>'
        "<style>"
        f":root{{{vars_css}}}"
        "html,body{margin:0;width:100%;height:100%;}"
        "body{box-sizing:border-box;display:flex;align-items:center;"
        f"justify-content:{items};padding:7vmin 8vmin;"
        "background:var(--bg,#fff);color:var(--text-primary,#000);"
        'font-family:var(--font-family,"Helvetica Neue",Helvetica,Arial,sans-serif);'
        f"text-align:{text_align};}}"
        f".note{{display:flex;flex-direction:column;align-items:{items};gap:3vmin;"
        "max-width:100%;min-width:0;}"
        f".note-head{{font-size:{head_fs};font-weight:800;line-height:1.05;"
        "letter-spacing:-0.02em;overflow-wrap:anywhere;}"
        ".note-rule{width:14vmin;height:0.9vmin;background:var(--accent-4,currentColor);}"
        f".note-body{{font-size:{body_fs};font-weight:500;line-height:1.3;"
        "color:var(--text-secondary,inherit);white-space:pre-wrap;overflow-wrap:anywhere;}"
        "</style></head><body>"
        f'<div class="note"><div class="note-head">{html.escape(headline)}</div>'
        f"{rule_html}{body_html}</div></body></html>"
    )


def render_note_png(
    text: str,
    *,
    w: int,
    h: int,
    size: str = "large",
    align: str = "center",
    theme_vars: dict[str, str] | None = None,
    pool: BrowserPool | None = None,
) -> bytes:
    """Screenshot the note at ``w × h`` and return the PNG bytes."""
    page = note_html(text, size=size, align=align, theme_vars=theme_vars)
    encoded = base64.b64encode(page.encode("utf-8")).decode("ascii")
    return render_to_png(
        RenderRequest(
            url=f"data:text/html;charset=utf-8;base64,{encoded}",
            viewport_w=w,
            viewport_h=h,
            wait_until="load",
            is_composer=False,
        ),
        pool=pool,
    )
