"""Editor layout templates.

A layout is a named list of (x_frac, y_frac, w_frac, h_frac) tuples — cell
positions expressed as 0..1 fractions of the panel. Applying a layout
scales the fractions to the page's panel dimensions and replaces the
cell positions.

The fraction-based representation means the same layout works at any
panel size; the editor just multiplies by panel.w / panel.h on apply.

When applying a layout to an existing page, the editor preserves the
first N cells' plugin + options where N = min(existing, new) — so picking
a different layout doesn't blow away your widget assignments. Extra
cells (existing > new) are dropped; extra slots (new > existing) are
added as unassigned (plugin=None) cells the user fills in.
"""

from __future__ import annotations

from dataclasses import dataclass

# (x_frac, y_frac, w_frac, h_frac) — all in [0, 1]. The layout's cells
# should tile the panel without gaps (the page-level matting gap is
# applied separately by the composer).
LayoutCell = tuple[float, float, float, float]


@dataclass(frozen=True)
class Layout:
    slug: str
    name: str
    cells: tuple[LayoutCell, ...]


LAYOUTS: tuple[Layout, ...] = (
    Layout("1_cell", "1 cell", ((0.0, 0.0, 1.0, 1.0),)),
    Layout(
        "2_columns",
        "2 columns",
        (
            (0.0, 0.0, 0.5, 1.0),
            (0.5, 0.0, 0.5, 1.0),
        ),
    ),
    Layout(
        "2_rows",
        "2 rows",
        (
            (0.0, 0.0, 1.0, 0.5),
            (0.0, 0.5, 1.0, 0.5),
        ),
    ),
    Layout(
        "3_rows",
        "3 rows",
        (
            (0.0, 0.0, 1.0, 1 / 3),
            (0.0, 1 / 3, 1.0, 1 / 3),
            (0.0, 2 / 3, 1.0, 1 / 3),
        ),
    ),
    Layout(
        "2x2_grid",
        "2x2 grid",
        (
            (0.0, 0.0, 0.5, 0.5),
            (0.5, 0.0, 0.5, 0.5),
            (0.0, 0.5, 0.5, 0.5),
            (0.5, 0.5, 0.5, 0.5),
        ),
    ),
    Layout(
        "hero_top",
        "Hero top",
        (
            (0.0, 0.0, 1.0, 0.55),
            (0.0, 0.55, 0.5, 0.45),
            (0.5, 0.55, 0.5, 0.45),
        ),
    ),
    Layout(
        "hero_bottom",
        "Hero bottom",
        (
            (0.0, 0.0, 0.5, 0.45),
            (0.5, 0.0, 0.5, 0.45),
            (0.0, 0.45, 1.0, 0.55),
        ),
    ),
    Layout(
        "hero_left",
        "Hero left",
        (
            (0.0, 0.0, 0.6, 1.0),
            (0.6, 0.0, 0.4, 0.5),
            (0.6, 0.5, 0.4, 0.5),
        ),
    ),
    Layout(
        "hero_right",
        "Hero right",
        (
            (0.0, 0.0, 0.4, 0.5),
            (0.0, 0.5, 0.4, 0.5),
            (0.4, 0.0, 0.6, 1.0),
        ),
    ),
    Layout(
        "hero_sandwich",
        "Hero sandwich",
        (
            (0.0, 0.0, 0.5, 0.25),
            (0.5, 0.0, 0.5, 0.25),
            (0.0, 0.25, 1.0, 0.5),
            (0.0, 0.75, 0.5, 0.25),
            (0.5, 0.75, 0.5, 0.25),
        ),
    ),
)


LAYOUTS_BY_SLUG: dict[str, Layout] = {layout.slug: layout for layout in LAYOUTS}


def to_panel_pixels(layout: Layout, panel_w: int, panel_h: int) -> list[tuple[int, int, int, int]]:
    """Project a layout's fraction cells onto a panel grid. Last cell in
    each axis snaps to the panel edge so rounding errors don't leave a
    one-pixel gap or overlap. Returns (x, y, w, h) tuples."""
    out: list[tuple[int, int, int, int]] = []
    for x_frac, y_frac, w_frac, h_frac in layout.cells:
        x = round(x_frac * panel_w)
        y = round(y_frac * panel_h)
        # Snap right + bottom edges to the panel grid when the layout
        # cell extends to the edge (frac sums to 1). Otherwise size from
        # the fraction.
        right_frac = x_frac + w_frac
        bottom_frac = y_frac + h_frac
        right = panel_w if abs(right_frac - 1.0) < 1e-6 else round(right_frac * panel_w)
        bottom = panel_h if abs(bottom_frac - 1.0) < 1e-6 else round(bottom_frac * panel_h)
        w = max(1, right - x)
        h = max(1, bottom - y)
        out.append((x, y, w, h))
    return out


def detect_layout(
    cells: list[tuple[int, int, int, int]], panel_w: int, panel_h: int
) -> Layout | None:
    """Reverse: given a list of (x, y, w, h) cells, find the layout that
    matches (within rounding tolerance). Used by the editor to highlight
    the currently-applied layout card.

    Returns None when no built-in layout matches the cell geometry."""
    if not cells:
        return None
    for layout in LAYOUTS:
        if len(layout.cells) != len(cells):
            continue
        target = to_panel_pixels(layout, panel_w, panel_h)
        # 4px tolerance on each side — accommodates the page-level
        # matting gap, which the composer applies as inset padding.
        if all(
            abs(a[0] - b[0]) <= 4
            and abs(a[1] - b[1]) <= 4
            and abs(a[2] - b[2]) <= 4
            and abs(a[3] - b[3]) <= 4
            for a, b in zip(sorted(target), sorted(cells), strict=False)
        ):
            return layout
    return None
