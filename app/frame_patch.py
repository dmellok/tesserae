"""Post-action frame patches (hybrid render mode, schema 2).

After a touch action mutates external state (a Home Assistant service
call), the server re-renders the page headless and diffs the new wire
framebuffer against the frame the device is showing. The changed areas
ship as a small patch document: rects in wire-framebuffer pixel space
plus one concatenated blob of row data in the frame's own packing, so
the firmware memcpys each rect into its stored framebuffer and
partial-refreshes it. Sub-second visual truth with no full e-ink flash
and no full-frame download; the periodic full paint still reconciles
ghosting.

Both artifacts must be raw packed framebuffers (the .bin renderer
family): no header, row-major, ``width * bpp / 8`` bytes per row. The
pixel encoding is opaque here; a patch is byte-identical to the same
region of the new frame, so the firmware never interprets pixels.
Rect x/w land on byte boundaries by construction (byte columns map back
to whole pixels for every bpp in 1/2/4/8).

mypy --strict does not apply here; shapes mirror app.overlay_sync.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Caps mirror the overlay-spec discipline: the server never ships more
# than the firmware budget, and anything past a cap is dropped loudly
# (here: the caller falls back to a normal full push). More rects than
# this and the per-rect refresh overhead approaches a full repaint.
MAX_PATCH_RECTS = 12
# Patch payload budget. Past ~20% of the E1003's 1.3 MB frame a full
# download + repaint costs about the same, so patching stops paying.
MAX_PATCH_BYTES = 262144

# Diff granularity: change detection clusters on tiles of this many
# frame rows by this many packed bytes (32 px at 4bpp). Coarse enough
# that a widget's scattered anti-aliasing edges fuse into one rect,
# fine enough that two separate light tiles stay two rects.
_TILE_ROWS = 32
_TILE_BYTES = 16


def infer_bpp(data_len: int, width: int, height: int) -> int | None:
    """Bits per pixel of a raw packed framebuffer, from its byte size
    against the panel dims. None when the size matches no headerless
    packing we know (a PNG artifact, a truncated file, wrong dims)."""
    if width <= 0 or height <= 0 or data_len <= 0:
        return None
    bits = data_len * 8
    px = width * height
    if bits % px:
        return None
    bpp = bits // px
    return bpp if bpp in (1, 2, 4, 8) else None


def _components(grid: object) -> list[tuple[int, int, int, int]]:
    """4-connected components of a boolean tile grid, as inclusive
    bounding boxes ``(row0, col0, row1, col1)``. Plain BFS; the grid is
    tiny (a 1404x936-byte frame is a 44x59 grid)."""
    import numpy as np

    g = np.asarray(grid)
    seen = np.zeros_like(g, dtype=bool)
    boxes: list[tuple[int, int, int, int]] = []
    rows, cols = g.shape
    for r0 in range(rows):
        for c0 in range(cols):
            if not g[r0, c0] or seen[r0, c0]:
                continue
            stack = [(r0, c0)]
            seen[r0, c0] = True
            rmin = rmax = r0
            cmin = cmax = c0
            while stack:
                r, c = stack.pop()
                rmin, rmax = min(rmin, r), max(rmax, r)
                cmin, cmax = min(cmin, c), max(cmax, c)
                for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                    if 0 <= nr < rows and 0 <= nc < cols and g[nr, nc] and not seen[nr, nc]:
                        seen[nr, nc] = True
                        stack.append((nr, nc))
            boxes.append((rmin, cmin, rmax, cmax))
    return boxes


def _merge_to_cap(
    rects: list[tuple[int, int, int, int]], cap: int
) -> list[tuple[int, int, int, int]]:
    """Greedily merge byte-space rects ``(y, x, h, w)`` until at most
    ``cap`` remain, always fusing the pair whose union grows least."""

    def union(
        a: tuple[int, int, int, int], b: tuple[int, int, int, int]
    ) -> tuple[int, int, int, int]:
        y = min(a[0], b[0])
        x = min(a[1], b[1])
        y1 = max(a[0] + a[2], b[0] + b[2])
        x1 = max(a[1] + a[3], b[1] + b[3])
        return (y, x, y1 - y, x1 - x)

    out = list(rects)
    while len(out) > cap:
        best = None
        best_cost = None
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                u = union(out[i], out[j])
                cost = u[2] * u[3] - out[i][2] * out[i][3] - out[j][2] * out[j][3]
                if best_cost is None or cost < best_cost:
                    best_cost = cost
                    best = (i, j, u)
        assert best is not None
        i, j, u = best
        out[j : j + 1] = []
        out[i : i + 1] = [u]
    return out


def diff_rects(
    old: bytes,
    new: bytes,
    *,
    width: int,
    height: int,
    max_rects: int = MAX_PATCH_RECTS,
) -> list[tuple[int, int, int, int]] | None:
    """Changed regions between two same-size packed framebuffers, as
    wire-space pixel rects ``(x, y, w, h)`` with x/w on byte boundaries.
    Empty list = identical buffers. None = the buffers aren't a packed
    framebuffer for these dims (caller falls back to a full push)."""
    import numpy as np

    bpp = infer_bpp(len(old), width, height)
    if bpp is None or len(old) != len(new):
        return None
    stride = width * bpp // 8
    a = np.frombuffer(old, dtype=np.uint8).reshape(height, stride)
    b = np.frombuffer(new, dtype=np.uint8).reshape(height, stride)
    neq = a != b
    if not neq.any():
        return []

    # Coarse tile grid of "anything changed in here", then connected
    # components so each cluster of change becomes one candidate rect.
    counts = np.add.reduceat(neq, np.arange(0, height, _TILE_ROWS), axis=0)
    counts = np.add.reduceat(counts, np.arange(0, stride, _TILE_BYTES), axis=1)
    boxes = _components(counts > 0)

    px_per_byte = 8 // bpp
    byte_rects: list[tuple[int, int, int, int]] = []
    for rmin, cmin, rmax, cmax in boxes:
        y0 = rmin * _TILE_ROWS
        y1 = min(height, (rmax + 1) * _TILE_ROWS)
        x0 = cmin * _TILE_BYTES
        x1 = min(stride, (cmax + 1) * _TILE_BYTES)
        # Tighten the tile bbox to the true changed extents inside it.
        sub = neq[y0:y1, x0:x1]
        rows = np.flatnonzero(sub.any(axis=1))
        cols = np.flatnonzero(sub.any(axis=0))
        byte_rects.append(
            (
                y0 + int(rows[0]),
                x0 + int(cols[0]),
                int(rows[-1] - rows[0]) + 1,
                int(cols[-1] - cols[0]) + 1,
            )
        )
    byte_rects = _merge_to_cap(byte_rects, max_rects)
    return [(x * px_per_byte, y, w * px_per_byte, h) for (y, x, h, w) in byte_rects]


def build_patch_blob(
    new: bytes,
    rects: list[tuple[int, int, int, int]],
    *,
    width: int,
    height: int,
) -> tuple[bytes, list[dict[str, int]]] | None:
    """The patch payload for pixel rects out of the new framebuffer: one
    concatenated blob of per-rect row data (each rect's rows contiguous,
    ``w * bpp / 8`` bytes per row, same packing as the frame file) plus
    the rect entries ``{x, y, w, h, offset, len}`` indexing into it."""
    import numpy as np

    bpp = infer_bpp(len(new), width, height)
    if bpp is None:
        return None
    stride = width * bpp // 8
    arr = np.frombuffer(new, dtype=np.uint8).reshape(height, stride)
    parts: list[bytes] = []
    entries: list[dict[str, int]] = []
    offset = 0
    for x, y, w, h in rects:
        bx = x * bpp // 8
        bw = w * bpp // 8
        if bx * 8 != x * bpp or bw * 8 != w * bpp:
            return None  # rect not byte-aligned; diff_rects never emits these
        chunk = arr[y : y + h, bx : bx + bw].tobytes()
        entries.append({"x": x, "y": y, "w": w, "h": h, "offset": offset, "len": len(chunk)})
        parts.append(chunk)
        offset += len(chunk)
    return b"".join(parts), entries
