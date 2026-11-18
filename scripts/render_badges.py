"""Draw the "Works with Tesserae" badges.

Four badges, for manufacturers and compatibility listings: a stacked and a
single-line layout, each for light and dark grounds. They reuse the server
mark from ``static/brand/icon.svg`` and set the text in Inter, the wordmark's
typeface, from the fonts bundled in ``plugins/fonts_core``.

The text is shaped with HarfBuzz (so it is kerned) and written as outlines,
which means the SVGs carry no font dependency and look the same wherever they
are embedded. Each SVG is also rasterised at 3x with headless Chromium
(Playwright, which the test suite already uses) for places that want a PNG.

Run when the mark, the colours or the wording change, and commit the output.
fontTools, brotli (for woff2) and uharfbuzz are not project dependencies, so
ask uv for them:

  uv run --with fonttools --with brotli --with uharfbuzz \
      python scripts/render_badges.py
"""

from __future__ import annotations

import io
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = REPO_ROOT / "plugins" / "fonts_core" / "static"
OUT_DIR = REPO_ROOT / "docs" / "assets" / "brand"

LABEL = "Works with Tesserae"
INK, RED, PAPER, BONE = "#1C1B19", "#D0493C", "#FFFDF8", "#EDE9DF"
PNG_SCALE = 3


def _num(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


class Face:
    """One bundled font, ready to turn kerned text into an SVG path."""

    def __init__(self, path: Path) -> None:
        # HarfBuzz reads plain sfnt, so unwrap the woff2 first.
        packed = TTFont(path)
        packed.flavor = None
        buf = io.BytesIO()
        packed.save(buf)
        data = buf.getvalue()
        font = TTFont(io.BytesIO(data))
        self._glyphs = font.getGlyphSet()
        self._order = font.getGlyphOrder()
        self._upem: int = font["head"].unitsPerEm
        self._cap: float = getattr(font["OS/2"], "sCapHeight", 0) or self._upem * 0.727
        self._hb = hb.Font(hb.Face(data))

    def cap_height(self, size: float) -> float:
        return self._cap / self._upem * size

    def run(
        self, text: str, size: float, x: float, baseline: float, tracking: float = 0.0
    ) -> tuple[str, float]:
        """Set ``text`` at ``size`` px from ``x`` on ``baseline``. ``tracking``
        is extra letter spacing in ems. Returns the path data and the x where
        the text ends."""
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self._hb, buf, {"kern": True, "liga": False})
        scale = size / self._upem
        pen = SVGPathPen(self._glyphs, ntos=_num)
        cursor = x
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
            # Font units are y-up; SVG is y-down.
            place = (
                scale,
                0,
                0,
                -scale,
                cursor + pos.x_offset * scale,
                baseline - pos.y_offset * scale,
            )
            self._glyphs[self._order[info.codepoint]].draw(TransformPen(pen, place))
            cursor += pos.x_advance * scale + tracking * size
        return pen.getCommands(), cursor - tracking * size


def tile(x: float, y: float, size: float, *, hairline: bool) -> str:
    """The server mark from ``static/brand/icon.svg``. On a dark ground the
    ink tile gets the same hairline the on-dark wordmark uses."""
    parts = [
        f'<g transform="translate({_num(x)} {_num(y)}) scale({size / 256:.5f})">',
        f'<rect width="256" height="256" rx="72" fill="{INK}"/>',
    ]
    if hairline:
        parts.append(
            '<rect x="3" y="3" width="250" height="250" rx="69" fill="none" '
            f'stroke="{BONE}" stroke-opacity="0.3" stroke-width="6"/>'
        )
    parts.append(f'<path d="M128 55H174A27 27 0 0 1 201 82V128H128Z" fill="{RED}"/>')
    parts.append(f'<path d="M55 128H128V201H82A27 27 0 0 1 55 174Z" fill="{PAPER}"/></g>')
    return "".join(parts)


def frame(width: float, height: int, radius: int, body: str, *, dark: bool) -> str:
    ground, edge = (INK, BONE) if dark else (PAPER, INK)
    w = round(width)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {height}" '
        f'width="{w}" height="{height}" role="img" aria-label="{LABEL}">'
        f"<title>{LABEL}</title>"
        f'<rect x="1" y="1" width="{w - 2}" height="{height - 2}" rx="{radius}" '
        f'fill="{ground}" stroke="{edge}" stroke-opacity="{0.3 if dark else 0.18}" '
        f'stroke-width="2"/>{body}</svg>\n'
    )


def stacked(regular: Face, bold: Face, *, dark: bool) -> str:
    """Small caps line over the name, beside a 64 px tile. 96 px tall."""
    height, pad, icon = 96, 16, 64
    small, big, gap = 12.0, 31.0, 10.0
    ink = BONE if dark else INK
    x = pad + icon + 16
    # Centre the two lines as one block against the tile.
    block = regular.cap_height(small) + gap + bold.cap_height(big)
    top = (height - block) / 2
    line1, end1 = regular.run(
        "WORKS WITH", small, x + 0.5, top + regular.cap_height(small), tracking=0.14
    )
    line2, end2 = bold.run("Tesserae", big, x - 1, top + block, tracking=-0.012)
    body = (
        tile(pad, pad, icon, hairline=dark)
        + f'<path fill="{ink}" fill-opacity="0.72" d="{line1}"/>'
        + f'<path fill="{ink}" d="{line2}"/>'
    )
    return frame(max(end1, end2) + pad + 8, height, 22, body, dark=dark)


def inline(regular: Face, bold: Face, *, dark: bool) -> str:
    """One line beside a 32 px tile. 48 px tall, for tables and footers."""
    height, pad, icon, size = 48, 8, 32, 17.0
    ink = BONE if dark else INK
    baseline = height / 2 + bold.cap_height(size) / 2
    lead, lead_end = regular.run("Works with ", size, pad + icon + 10, baseline)
    name, end = bold.run("Tesserae", size, lead_end, baseline, tracking=-0.008)
    body = (
        tile(pad, pad, icon, hairline=dark)
        + f'<path fill="{ink}" fill-opacity="0.78" d="{lead}"/>'
        + f'<path fill="{ink}" d="{name}"/>'
    )
    return frame(end + pad + 8, height, 14, body, dark=dark)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    regular = Face(FONTS_DIR / "inter" / "400.woff2")
    bold = Face(FONTS_DIR / "inter" / "700.woff2")

    written: list[Path] = []
    for name, draw in (("stacked", stacked), ("inline", inline)):
        for dark in (False, True):
            suffix = "-on-dark" if dark else ""
            out = OUT_DIR / f"works-with-tesserae-{name}{suffix}.svg"
            out.write_text(draw(regular, bold, dark=dark), encoding="utf-8")
            written.append(out)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=PNG_SCALE)
        for svg in written:
            page.set_content(
                '<html><body style="margin:0;background:transparent">'
                f"{svg.read_text(encoding='utf-8')}</body></html>"
            )
            page.locator("svg").screenshot(path=str(svg.with_suffix(".png")), omit_background=True)
        browser.close()

    print(f"Wrote {len(written)} badges (SVG + PNG) into {OUT_DIR.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
