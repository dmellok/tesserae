"""Panel orientation calibration.

The user can't always reason about which way their panel is physically
mounted. This generates a test card, a 2×2 grid numbered 1-4 with a
``TOP`` arrow, that gets pushed through the device's real renderer, so
what lands on the glass is the true result of the current settings.

The user reports which number ended up in the panel's top-left corner;
``target_orientation`` maps that to the display orientation that puts the
card upright. Because the four orientations are exactly 90° apart, one
answer is enough. A wrong guess (if a renderer turns the opposite way
than assumed) lands 180° off, recoverable with a single flip, which the
confirm re-push + the manual orientation dropdown both cover.

Digits are drawn as seven-segment glyphs so the card needs no font files
(none ship with the app, real rendering is HTML/Chromium).

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

# The four display orientations, ordered so that stepping the index by +1
# is assumed to turn the on-panel image +90° clockwise (landscape →
# portrait → landscape-flipped → portrait-flipped).
ORIENTATION_CYCLE: tuple[str, str, str, str] = (
    "landscape",
    "portrait",
    "landscape_flipped",
    "portrait_flipped",
)

# Card layout: 1=TL, 2=TR, 3=BL, 4=BR. Reading the corners clockwise from
# top-left gives 1,2,4,3. If the panel shows the card turned k quarter-
# turns clockwise, the number that ends up in the physical top-left is:
#   k=0 → 1,  k=1 → 3,  k=2 → 4,  k=3 → 2
# Invert that to recover k from the reported top-left number.
_TOP_LEFT_TO_QUARTERS: dict[int, int] = {1: 0, 3: 1, 4: 2, 2: 3}


def is_portrait(orientation: str) -> bool:
    return orientation.startswith("portrait")


def target_orientation(pushed: str, top_left_number: int) -> str:
    """Given the orientation the card was pushed with and which number
    the user sees in the panel's top-left, return the orientation that
    makes the card upright (1 in the top-left, arrow up)."""
    pushed = pushed if pushed in ORIENTATION_CYCLE else "landscape"
    quarters = _TOP_LEFT_TO_QUARTERS.get(top_left_number, 0)
    i = ORIENTATION_CYCLE.index(pushed)
    # The image is turned `quarters` CW from upright; step the orientation
    # by (4 - quarters) to add the complementary turn back to zero.
    return ORIENTATION_CYCLE[(i + (4 - quarters)) % 4]


# Seven-segment definitions for digits 1-4. Each entry lists the lit
# segments (a top, b top-right, c bottom-right, d bottom, e bottom-left,
# f top-left, g middle).
_SEGMENTS: dict[int, frozenset[str]] = {
    1: frozenset("bc"),
    2: frozenset("abged"),
    3: frozenset("abgcd"),
    4: frozenset("fgbc"),
}


def _draw_digit(
    draw: ImageDraw.ImageDraw,
    digit: int,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int],
) -> None:
    """Draw a seven-segment ``digit`` filling ``box`` = (x, y, w, h)."""
    x, y, w, h = box
    t = max(4, min(w, h) // 8)  # segment thickness
    mid_y = y + h / 2
    seg_h = (h - 3 * t) / 2  # height of a vertical segment
    lit = _SEGMENTS.get(digit, frozenset())
    rects: dict[str, tuple[float, float, float, float]] = {
        "a": (x + t, y, w - 2 * t, t),
        "g": (x + t, mid_y - t / 2, w - 2 * t, t),
        "d": (x + t, y + h - t, w - 2 * t, t),
        "f": (x, y + t, t, seg_h),
        "b": (x + w - t, y + t, t, seg_h),
        "e": (x, mid_y + t / 2, t, seg_h),
        "c": (x + w - t, mid_y + t / 2, t, seg_h),
    }
    for name, (rx, ry, rw, rh) in rects.items():
        if name in lit:
            draw.rectangle((rx, ry, rx + rw, ry + rh), fill=color)


def build_calibration_card(w: int, h: int) -> bytes:
    """Render the calibration card as PNG bytes at ``w`` × ``h``.

    A 2×2 grid numbered 1-4 (TL/TR/BL/BR), each in a distinct Spectra-6
    colour, plus a filled ``TOP`` arrow on the top edge so the user can
    read both the corner and which way is up."""
    w = max(2, w)
    h = max(2, h)
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    black = (0, 0, 0)
    # Quadrant accent colours (Spectra 6 palette: red, green, blue, black).
    quad_color = {
        1: (200, 30, 30),
        2: (30, 150, 60),
        3: (40, 80, 200),
        4: (20, 20, 20),
    }

    # Grid lines.
    line = max(2, min(w, h) // 200)
    draw.rectangle((0, 0, w - 1, h - 1), outline=black, width=line)
    draw.line((w // 2, 0, w // 2, h), fill=black, width=line)
    draw.line((0, h // 2, w, h // 2), fill=black, width=line)

    # Digit boxes, inset within each quadrant.
    qw, qh = w // 2, h // 2
    dw, dh = int(qw * 0.4), int(qh * 0.5)
    insets = {
        1: (qw // 2 - dw // 2, qh // 2 - dh // 2),
        2: (qw + qw // 2 - dw // 2, qh // 2 - dh // 2),
        3: (qw // 2 - dw // 2, qh + qh // 2 - dh // 2),
        4: (qw + qw // 2 - dw // 2, qh + qh // 2 - dh // 2),
    }
    for digit, (ix, iy) in insets.items():
        _draw_digit(draw, digit, (ix, iy, dw, dh), quad_color[digit])

    # "TOP" arrow, a filled triangle pointing up, centred on the top edge.
    aw = max(20, w // 12)
    ah = max(16, h // 16)
    cx = w // 2
    top = max(line + 4, h // 40)
    draw.polygon(
        [(cx, top), (cx - aw // 2, top + ah), (cx + aw // 2, top + ah)],
        fill=black,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
