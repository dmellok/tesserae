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
