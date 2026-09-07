"""Bespoke sub-byte indexed BMP writer (app.bmp_writer).

Packs a palette-mode image at the smallest standard bit depth (1/4/8) that
fits its palette. These check the chosen depth, that it stays uncompressed
BI_RGB, that it round-trips through Pillow to the same pixels (so it's a valid
BMP any reader accepts), and that it's smaller than Pillow's fixed 8-bit BMP.
"""

from __future__ import annotations

import io

from PIL import Image

from app.bmp_writer import pack_indexed_bmp


def _p_image(
    w: int, h: int, palette: list[tuple[int, int, int]], indices: list[int]
) -> Image.Image:
    img = Image.new("P", (w, h))
    flat: list[int] = []
    for c in palette:
        flat += list(c)
    flat += [0] * (768 - len(flat))
    img.putpalette(flat)
    img.putdata(indices)
    return img


def _bpp(data: bytes) -> int:
    return int.from_bytes(data[28:30], "little")


def _compression(data: bytes) -> int:
    return int.from_bytes(data[30:34], "little")


def test_mono_packs_1bpp_and_roundtrips() -> None:
    img = _p_image(
        8, 3, [(0, 0, 0), (255, 255, 255)], [(x + y) % 2 for y in range(3) for x in range(8)]
    )
    data = pack_indexed_bmp(img)
    assert data[:2] == b"BM"
    assert _bpp(data) == 1 and _compression(data) == 0
    out = Image.open(io.BytesIO(data)).convert("RGB")
    assert out.size == (8, 3)
    assert list(out.getdata()) == list(img.convert("RGB").getdata())


def test_four_colours_pack_4bpp_and_roundtrip() -> None:
    pal = [(0, 0, 0), (255, 255, 255), (255, 0, 0), (255, 255, 0)]
    # Odd width exercises the 4-bit nibble padding + the 4-byte row stride.
    img = _p_image(9, 5, pal, [i % 4 for i in range(9 * 5)])
    data = pack_indexed_bmp(img)
    assert _bpp(data) == 4 and _compression(data) == 0
    out = Image.open(io.BytesIO(data)).convert("RGB")
    assert out.size == (9, 5)
    assert list(out.getdata()) == list(img.convert("RGB").getdata())


def test_smaller_than_pillow_8bit() -> None:
    pal = [(0, 0, 0), (255, 255, 255), (255, 0, 0)]  # tri-colour -> 4bpp
    img = _p_image(64, 64, pal, [i % 3 for i in range(64 * 64)])
    bespoke = pack_indexed_bmp(img)
    pill = io.BytesIO()
    img.save(pill, format="BMP")  # Pillow writes P as 8bpp
    assert len(bespoke) < len(pill.getvalue())
    # ...and still decodes to the same image.
    assert list(Image.open(io.BytesIO(bespoke)).convert("RGB").getdata()) == list(
        img.convert("RGB").getdata()
    )


def test_compacts_to_used_colours() -> None:
    # A 6-entry palette image that only touches 2 colours packs at 1bpp.
    pal = [(0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    img = _p_image(16, 4, pal, [(0 if i % 2 else 2) for i in range(16 * 4)])  # only indices 0 and 2
    data = pack_indexed_bmp(img)
    assert _bpp(data) == 1
    assert list(Image.open(io.BytesIO(data)).convert("RGB").getdata()) == list(
        img.convert("RGB").getdata()
    )


def test_rgb_passthrough_falls_back_to_24bit() -> None:
    img = Image.new("RGB", (10, 8), (12, 34, 56))
    data = pack_indexed_bmp(img)
    assert data[:2] == b"BM"
    out = Image.open(io.BytesIO(data)).convert("RGB")
    assert out.size == (10, 8) and out.getpixel((0, 0)) == (12, 34, 56)


def _clr_used(data: bytes) -> int:
    return int.from_bytes(data[46:50], "little")


def _pixel_offset(data: bytes) -> int:
    return int.from_bytes(data[10:14], "little")


def test_fixed_palette_is_written_whole_whatever_the_frame_uses() -> None:
    # Discussion #277: a tri-colour frame with no red on it must still ship
    # as a 4-bpp file with the full 3-entry table, so a client that sized its
    # bitmap from an earlier frame's header can reuse it for this one.
    pal = [(0, 0, 0), (255, 255, 255), (255, 0, 0)]
    img = _p_image(16, 4, pal, [i % 2 for i in range(16 * 4)])  # black + white only
    data = pack_indexed_bmp(img, palette=pal)
    assert _bpp(data) == 4 and _compression(data) == 0
    assert _clr_used(data) == 3
    # Exactly three BGRA entries between the headers and the pixel data.
    assert _pixel_offset(data) == 14 + 40 + 3 * 4
    assert data[54:66] == bytes((0, 0, 0, 0, 255, 255, 255, 0, 0, 0, 255, 0))
    out = Image.open(io.BytesIO(data))
    assert list(out.convert("RGB").getdata()) == list(img.convert("RGB").getdata())
    assert out.getpalette()[:9] == [0, 0, 0, 255, 255, 255, 255, 0, 0]


def test_fixed_palette_keeps_index_order() -> None:
    # Indices are taken as-is against the given palette (no compaction).
    pal = [(0, 0, 0), (255, 255, 255), (255, 0, 0)]
    img = _p_image(4, 1, pal, [2, 2, 0, 1])
    data = pack_indexed_bmp(img, palette=pal)
    out = Image.open(io.BytesIO(data))
    assert list(out.getdata()) == [2, 2, 0, 1]


def test_fixed_mono_palette_packs_1bpp() -> None:
    pal = [(0, 0, 0), (255, 255, 255)]
    img = _p_image(8, 1, pal, [1] * 8)  # all white
    data = pack_indexed_bmp(img, palette=pal)
    assert _bpp(data) == 1 and _clr_used(data) == 2


def test_fixed_palette_rejects_out_of_range_index() -> None:
    import pytest

    pal = [(0, 0, 0), (255, 255, 255)]
    img = _p_image(2, 1, [*pal, (255, 0, 0)], [0, 2])
    with pytest.raises(ValueError):
        pack_indexed_bmp(img, palette=pal)
