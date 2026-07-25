"""Frame patch diffing (hybrid render mode, schema 2): bpp inference,
changed-rect extraction with byte-aligned coordinates, the merge-to-cap
behaviour, and blob packing offsets."""

from __future__ import annotations

import numpy as np

from app.frame_patch import build_patch_blob, diff_rects, infer_bpp

W, H = 256, 64  # 4bpp -> 128-byte stride, spans several 16-byte tiles


def _frame(fill: int = 0x00) -> np.ndarray:
    return np.full((H, W * 4 // 8), fill, dtype=np.uint8)


def test_infer_bpp_matches_packings() -> None:
    assert infer_bpp(W * H // 2, W, H) == 4
    assert infer_bpp(W * H // 8, W, H) == 1
    assert infer_bpp(W * H // 4, W, H) == 2
    assert infer_bpp(W * H, W, H) == 8
    assert infer_bpp(W * H // 2 + 1, W, H) is None  # not headerless-packed
    assert infer_bpp(0, W, H) is None


def test_identical_buffers_diff_to_empty() -> None:
    a = _frame().tobytes()
    assert diff_rects(a, a, width=W, height=H) == []


def test_size_mismatch_returns_none() -> None:
    a = _frame().tobytes()
    assert diff_rects(a, a[:-1], width=W, height=H) is None
    assert diff_rects(a[:-1], a[:-1], width=W, height=H) is None


def test_two_distant_changes_become_two_tight_rects() -> None:
    old = _frame()
    new = old.copy()
    new[2:6, 3:7] = 0xFF  # rows 2-5, byte cols 3-6
    new[40:46, 100:111] = 0xFF  # rows 40-45, byte cols 100-110
    rects = diff_rects(old.tobytes(), new.tobytes(), width=W, height=H)
    assert rects is not None
    # 4bpp -> 2 px per byte; x/w land on byte boundaries.
    assert sorted(rects, key=lambda r: r[1]) == [
        (6, 2, 8, 4),
        (200, 40, 22, 6),
    ]


def test_merge_cap_fuses_rects_into_bounding_union() -> None:
    old = _frame()
    new = old.copy()
    new[2:6, 3:7] = 0xFF
    new[40:46, 100:111] = 0xFF
    rects = diff_rects(old.tobytes(), new.tobytes(), width=W, height=H, max_rects=1)
    assert rects == [(6, 2, 216, 44)]


def test_blob_packs_rect_rows_with_offsets() -> None:
    old = _frame()
    new = old.copy()
    new[2:6, 3:7] = 0xAB
    new[40:46, 100:111] = 0xCD
    rects = diff_rects(old.tobytes(), new.tobytes(), width=W, height=H)
    assert rects is not None
    built = build_patch_blob(new.tobytes(), rects, width=W, height=H)
    assert built is not None
    blob, entries = built
    assert len(entries) == 2
    offset = 0
    for entry in sorted(entries, key=lambda e: e["offset"]):
        assert entry["offset"] == offset
        assert entry["len"] == entry["h"] * entry["w"] * 4 // 8
        bx, bw = entry["x"] * 4 // 8, entry["w"] * 4 // 8
        expected = new[entry["y"] : entry["y"] + entry["h"], bx : bx + bw].tobytes()
        assert blob[entry["offset"] : entry["offset"] + entry["len"]] == expected
        offset += entry["len"]
    assert len(blob) == offset


def test_blob_rejects_unaligned_rect() -> None:
    new = _frame().tobytes()
    # x=1 at 4bpp is mid-byte; diff_rects never emits this, the packer refuses it.
    assert build_patch_blob(new, [(1, 0, 4, 4)], width=W, height=H) is None


def test_1bpp_alignment_uses_eight_pixel_columns() -> None:
    stride = W // 8
    old = np.zeros((H, stride), dtype=np.uint8)
    new = old.copy()
    new[10:12, 5:7] = 0xFF  # byte cols 5-6 -> px 40..55
    rects = diff_rects(old.tobytes(), new.tobytes(), width=W, height=H)
    assert rects == [(40, 10, 16, 2)]


# -- composition-space diff (pre-dither) ----------------------------------


def _png(arr: np.ndarray) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def test_composition_diff_is_tight_under_global_dither_noise() -> None:
    """The reason this function exists: a wire-space byte diff of two
    error-diffusion renders is nearly global, but the composition diff
    of the same change is one tight rect."""
    from app.frame_patch import diff_composition_rects

    old = np.full((H, W, 3), 255, dtype=np.uint8)
    new = old.copy()
    new[40:46, 200:222] = 0
    rects = diff_composition_rects(_png(old), _png(new), expected_w=W, expected_h=H)
    assert rects == [(200, 40, 22, 6)]


def test_composition_diff_identical_and_mismatched() -> None:
    from app.frame_patch import diff_composition_rects

    base = np.full((H, W, 3), 255, dtype=np.uint8)
    assert diff_composition_rects(_png(base), _png(base)) == []
    assert diff_composition_rects(_png(base), _png(base), expected_w=W + 2, expected_h=H) is None
    other = np.full((H // 2, W, 3), 255, dtype=np.uint8)
    assert diff_composition_rects(_png(base), _png(other)) is None
    assert diff_composition_rects(b"not a png", _png(base)) is None


def test_align_rect_pads_and_snaps_to_byte_columns() -> None:
    from app.frame_patch import align_rect

    # 4bpp -> 2 px/byte: pad 2 then snap outward to even columns.
    assert align_rect((5, 5, 10, 10), width=W, height=H, bpp=4) == (2, 3, 16, 14)
    # 1bpp -> 8 px/byte columns.
    assert align_rect((9, 0, 4, 4), width=W, height=H, bpp=1) == (0, 0, 16, 6)
    # Clamps at the panel edge and never exceeds it.
    x, y, w, h = align_rect((W - 3, H - 3, 3, 3), width=W, height=H, bpp=4)
    assert x + w <= W and y + h <= H
    assert align_rect((0, H, 4, 4), width=W, height=H, bpp=4) is None
