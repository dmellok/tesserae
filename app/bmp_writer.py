"""Bespoke sub-byte indexed BMP writer for the CircuitPython BMP path.

Pillow saves palette-mode ("P") images as 8-bit BMP regardless of how few
colours they use, so a 3-colour tri-colour frame goes out 8x larger than it
needs to. The CircuitPython BMP format exists precisely for memory-constrained
clients, where wire size matters, so this packs an indexed image at the
smallest *standard* BMP bit depth that fits its palette:

* <= 2 colours -> 1 bpp   (mono panels: 8x smaller than Pillow)
* <= 16 colours -> 4 bpp  (tri-colour / 4-grey / Spectra 6 / 7-colour ACeP: 2x)
* otherwise -> 8 bpp

Output is uncompressed ``BI_RGB``, bottom-up, MSB-first within a byte, with a
4-byte-aligned stride and a full ``2**bpp`` colour table, i.e. the exact shape
``adafruit_imageload``'s indexed-BMP reader unpacks (its loop is generic over
``color_depth = 8 // pixels_per_byte``). 1 / 4 / 8 bpp are also what Pillow can
read back, so the output round-trips for tests and stays portable to other
clients. The palette is derived from the colours actually present, so the BMP
is self-describing (the client reads its own colour table).
"""

from __future__ import annotations

import io
import struct

import numpy as np
from PIL import Image


def pack_indexed_bmp(img: Image.Image) -> bytes:
    """Pack a palette-mode image as a minimal-bit-depth uncompressed BMP.

    Non-``P`` images fall back to Pillow's BMP writer (e.g. the rgb24/rgb16
    full-colour passthrough, which isn't indexed)."""
    if img.mode != "P":
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="BMP")
        return buf.getvalue()

    w, h = img.size
    src_palette = list(img.getpalette() or [])
    src_palette += [0] * (768 - len(src_palette))
    idx = np.frombuffer(img.tobytes(), dtype=np.uint8).reshape(h, w)

    # Compact the palette to just the colours actually used, so a frame that
    # touches 3 of a 6-colour gamut still packs at the smaller depth.
    used = np.unique(idx)
    palette = [(src_palette[i * 3], src_palette[i * 3 + 1], src_palette[i * 3 + 2]) for i in used]
    colors = max(1, len(palette))
    bpp = 1 if colors <= 2 else 4 if colors <= 16 else 8

    # Remap the original indices to compact 0..colors-1 indices.
    remap = np.zeros(256, dtype=np.uint8)
    for new_i, old_i in enumerate(used):
        remap[int(old_i)] = new_i
    gidx = remap[idx]

    stride = ((w * bpp + 31) // 32) * 4  # 4-byte aligned row size
    pixels = bytearray()
    for y in range(h - 1, -1, -1):  # BMP scanlines are bottom-up
        pixels += _pack_row(gidx[y], bpp, stride)

    table_entries = 1 << bpp
    color_table = bytearray()
    for i in range(table_entries):
        r, g, b = palette[i] if i < len(palette) else (0, 0, 0)
        color_table += bytes((b, g, r, 0))  # BMP colour table is BGRA

    pixel_offset = 14 + 40 + len(color_table)
    file_size = pixel_offset + len(pixels)
    file_header = b"BM" + struct.pack("<IHHI", file_size, 0, 0, pixel_offset)
    info_header = struct.pack(
        "<IiiHHIIiiII",
        40,  # BITMAPINFOHEADER size
        w,
        h,  # positive height -> bottom-up
        1,  # planes
        bpp,
        0,  # BI_RGB (uncompressed)
        len(pixels),
        2835,  # 72 DPI, x
        2835,  # 72 DPI, y
        table_entries,
        0,  # important colours (0 = all)
    )
    return bytes(file_header) + info_header + bytes(color_table) + bytes(pixels)


def _pack_row(vals: np.ndarray, bpp: int, stride: int) -> bytes:
    """Pack one row of palette indices at ``bpp`` bits, MSB-first, padded to
    ``stride`` bytes."""
    vals = np.ascontiguousarray(vals, dtype=np.uint8)
    if bpp == 8:
        raw = vals.tobytes()
    elif bpp == 4:
        if vals.shape[0] % 2:
            vals = np.concatenate([vals, np.zeros(1, dtype=np.uint8)])
        packed = ((vals[0::2] & 0x0F) << 4) | (vals[1::2] & 0x0F)
        raw = packed.astype(np.uint8).tobytes()
    else:  # bpp == 1
        pad = (-vals.shape[0]) % 8
        bits = np.concatenate([vals & 1, np.zeros(pad, dtype=np.uint8)]) if pad else (vals & 1)
        raw = np.packbits(bits).tobytes()  # MSB-first
    if len(raw) < stride:
        raw += b"\x00" * (stride - len(raw))
    return raw
