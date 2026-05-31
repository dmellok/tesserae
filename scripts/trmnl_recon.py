"""Recon HTTP server for a TRMNL-compatible client.

A throwaway server that pretends to be a TRMNL backend so we can watch
exactly what a TRMNL client (Kindle jailbreak, official device, anything
that implements the BYOS protocol) sends when it tries to phone home.
Logs every request to stdout with method, path, headers, and body, then
returns minimal valid-shaped responses so the client keeps polling
instead of giving up after the first 404.

We use it to discover:

* What headers the client actually sends (``ID``, ``Access-Token``,
  ``Width``, ``Height``, battery / RSSI / FW fields).
* What the request cadence looks like for ``/api/display`` polling.
* Whether the client honours ``refresh_rate`` we hand back, and how
  it interprets ``reset_firmware`` / ``update_firmware`` / others.
* What the ``/api/log/`` POST body looks like in practice — the BYOS
  docs sketch it but each implementation has its own quirks.

Not part of the runtime app. Stays under ``scripts/`` because it is a
development-time tool the maintainer runs by hand.

Usage
-----

Standard library only — no venv or pip needed. Runs anywhere with
Python 3.11+::

    python3 scripts/trmnl_recon.py
    python3 scripts/trmnl_recon.py --port 8765 --host 0.0.0.0

Then point the TRMNL client at ``http://<your-dev-ip>:8765/`` and watch
the logs. The default 0.0.0.0 bind makes the server reachable from any
device on the LAN.

Hello images are generated on demand at any panel dimensions —
``/img/hello-<W>x<H>.png`` returns a 1-bit greyscale (or 8-bit if
``--bit-depth 8`` is set) PNG with a thick border, a corner-to-corner
diagonal X, centre crosshairs, and L-shaped corner brackets so a
successful end-to-end fetch is unmistakable on any panel.
"""

from __future__ import annotations

import argparse
import http.server
import json
import struct
import sys
import zlib
from datetime import datetime


class ReconHandler(http.server.BaseHTTPRequestHandler):
    # Quiet the default per-request line — we print our own richer log
    # block in ``_log_request`` below.
    def log_message(self, format: str, *args: object) -> None:
        return

    # ---- request logging --------------------------------------------------

    def _log_request(self, body: bytes = b"") -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"\n=== {ts}  {self.command} {self.path}")
        for header in self.headers:
            print(f"  {header}: {self.headers[header]}")
        if body:
            print(f"  --- body ({len(body)} bytes) ---")
            try:
                print(f"  {body.decode('utf-8')}")
            except UnicodeDecodeError:
                # Some clients POST form-encoded blobs that aren't valid
                # UTF-8 — fall back to a hex dump of the first 256 bytes
                # so we can still eyeball the shape.
                print(f"  <binary> {body[:256].hex()}")
        sys.stdout.flush()

    # ---- response helpers -------------------------------------------------

    def _json(self, code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _png(self, width: int, height: int) -> None:
        body = _size_matched_png(width, height)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _host(self) -> str:
        return (
            self.headers.get("Host")
            or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        )

    # ---- routing ----------------------------------------------------------

    def do_GET(self) -> None:
        self._log_request()
        if self.path in ("/api/setup/", "/api/setup"):
            # First-pair flow. Some clients call this on first boot to
            # exchange MAC → token; the KOReader plugin skips it entirely
            # and goes straight to /api/display. We respond anyway in
            # case a different client implementation lands here.
            w, h = self._requested_panel_dims()
            self._json(
                200,
                {
                    "status": 200,
                    "api_key": "recon-token-please-replace",
                    "friendly_id": "kindle-recon",
                    "image_url": f"http://{self._host()}/img/hello-{w}x{h}.png",
                    "filename": f"hello-{w}x{h}.png",
                },
            )
        elif self.path == "/api/display":
            # Steady-state poll. The client tells us what panel size it
            # wants via ``png-width`` / ``png-height`` headers; build
            # the image URL around that so a size-matched PNG comes back.
            w, h = self._requested_panel_dims()
            self._json(
                200,
                {
                    "status": 0,
                    "image_url": f"http://{self._host()}/img/hello-{w}x{h}.png",
                    "filename": f"hello-{w}x{h}.png",
                    "refresh_rate": 60,
                    "reset_firmware": False,
                    "update_firmware": False,
                    "firmware_url": "",
                    "special_function": "none",
                },
            )
        elif self.path.startswith("/img/hello-") and self.path.endswith(".png"):
            # /img/hello-<W>x<H>.png — parse the dims and generate.
            stem = self.path[len("/img/hello-") : -len(".png")]
            try:
                w_s, h_s = stem.split("x", 1)
                w = max(1, min(int(w_s), 4096))
                h = max(1, min(int(h_s), 4096))
            except (ValueError, IndexError):
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._png(w, h)
        elif self.path == "/img/hello.png":
            # Legacy URL — clients that ignore ``image_url`` and probe
            # this path directly get the stock 800×480 TRMNL panel size.
            self._png(800, 480)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def _requested_panel_dims(self) -> tuple[int, int]:
        """Pull the client's requested PNG dims from headers.

        KOReader-side clients send ``png-width`` / ``png-height``; native
        TRMNL devices send ``Width`` / ``Height``. Fall through both
        spellings and default to TRMNL's stock 800×480 when nothing
        useful is supplied (lets curl smoke-test the endpoints without
        the headers)."""
        for w_key, h_key in (("png-width", "png-height"), ("Width", "Height")):
            raw_w = self.headers.get(w_key)
            raw_h = self.headers.get(h_key)
            if raw_w and raw_h:
                try:
                    return max(1, int(raw_w)), max(1, int(raw_h))
                except ValueError:
                    pass
        return 800, 480

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length > 0 else b""
        self._log_request(body)
        if self.path.startswith("/api/log"):
            self._json(200, {"status": 200})
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()


# ---- size-matched PNG generation -----------------------------------
# Some clients (KOReader plugin) tell the server the dims they want
# via ``png-width`` / ``png-height`` headers and reject PNGs that
# don't match. Generate at any size on demand. Stdlib-only (struct +
# zlib) because we deliberately don't depend on Pillow — the recon
# script is supposed to run on any vanilla Python 3 install.

_PNG_CACHE: dict[tuple[int, int, int], bytes] = {}

# 1-bit (PNG spec) by default. Flip to 8 with ``--bit-depth 8`` if a
# client garbles the 1-bit output (some PNG decoders assume 8-bit
# greyscale regardless of the IHDR bit depth).
_BIT_DEPTH: int = 1


def _size_matched_png(width: int, height: int) -> bytes:
    """Build a greyscale PNG at exactly the requested dims.

    Visible content is a thick border, a diagonal X across the whole
    panel, centre crosshairs, and L-shaped corner brackets — enough
    visual structure that a successful end-to-end fetch is unmistakable
    on any panel size. Cached per (w,h) so repeated polls skip the
    zlib pass.

    Bit depth is controlled by the global ``_BIT_DEPTH``: 1-bit by
    default (smallest, what spec-compliant readers expect), 8-bit when
    the client garbles 1-bit output. Some readers — including some
    KOReader builds — assume 8-bit greyscale regardless of the IHDR
    bit depth and produce diagonal-noise garbage for 1-bit PNGs."""
    key = (width, height, _BIT_DEPTH)
    cached = _PNG_CACHE.get(key)
    if cached is not None:
        return cached

    # We build the image as a flat (width × height) byte buffer where
    # 0 = black, 1 = white, then pack to 1 bit per pixel at the end.
    # That keeps the drawing helpers obvious — bit-fiddling per pixel
    # would obscure the geometry.
    pixels = bytearray(b"\xff" * (width * height))

    def set_px(x: int, y: int) -> None:
        if 0 <= x < width and 0 <= y < height:
            pixels[y * width + x] = 0

    def hline(x1: int, x2: int, y: int) -> None:
        if not (0 <= y < height):
            return
        a, b = min(x1, x2), max(x1, x2)
        a, b = max(0, a), min(width - 1, b)
        off = y * width
        for x in range(a, b + 1):
            pixels[off + x] = 0

    def vline(x: int, y1: int, y2: int) -> None:
        if not (0 <= x < width):
            return
        a, b = min(y1, y2), max(y1, y2)
        for y in range(max(0, a), min(height - 1, b) + 1):
            pixels[y * width + x] = 0

    def thick_line(x1: int, y1: int, x2: int, y2: int, t: int = 3) -> None:
        """Bresenham with a square thickness — good enough for a recon
        marker."""
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy
        x, y = x1, y1
        half = t // 2
        while True:
            for ox in range(-half, half + 1):
                for oy in range(-half, half + 1):
                    set_px(x + ox, y + oy)
            if x == x2 and y == y2:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy

    # Border thickness scales with the smaller dim so it stays
    # proportional on tiny + giant panels.
    border = max(4, min(width, height) // 60)
    for y in range(border):
        hline(0, width - 1, y)
        hline(0, width - 1, height - 1 - y)
    for x in range(border):
        vline(x, 0, height - 1)
        vline(width - 1 - x, 0, height - 1)

    # Full diagonal X across the panel.
    thick_line(0, 0, width - 1, height - 1, t=3)
    thick_line(width - 1, 0, 0, height - 1, t=3)

    # Centre crosshair (1px thick is plenty against the white field).
    cx, cy = width // 2, height // 2
    hline(0, width - 1, cy)
    vline(cx, 0, height - 1)

    # Corner brackets — L-shapes inset from the border so they read
    # as framing marks. Length is 1/8 of the smaller dim.
    bracket = max(20, min(width, height) // 8)
    bt = max(2, border // 2)  # bracket thickness
    inset = border + 4
    for off in range(bt):
        # Top-left
        hline(inset, inset + bracket, inset + off)
        vline(inset + off, inset, inset + bracket)
        # Top-right
        hline(width - 1 - inset - bracket, width - 1 - inset, inset + off)
        vline(width - 1 - inset - off, inset, inset + bracket)
        # Bottom-left
        hline(inset, inset + bracket, height - 1 - inset - off)
        vline(inset + off, height - 1 - inset - bracket, height - 1 - inset)
        # Bottom-right
        hline(width - 1 - inset - bracket, width - 1 - inset, height - 1 - inset - off)
        vline(width - 1 - inset - off, height - 1 - inset - bracket, height - 1 - inset)

    # Pack the byte buffer into PNG-format scanlines.
    raw = bytearray()
    if _BIT_DEPTH == 1:
        # 1-bit: ceil(width/8) bytes per row, MSB first.
        row_bytes = (width + 7) // 8
        for y in range(height):
            raw.append(0)  # filter: None
            row = bytearray(b"\x00" * row_bytes)
            off = y * width
            for x in range(width):
                if pixels[off + x]:  # white → bit set
                    row[x >> 3] |= 1 << (7 - (x & 7))
            raw.extend(row)
    else:
        # 8-bit: one byte per pixel. 0 = black, 255 = white. No
        # packing, no MSB/LSB ambiguity — the bulletproof option when
        # a client's PNG decoder mishandles 1-bit.
        for y in range(height):
            raw.append(0)  # filter: None
            off = y * width
            for x in range(width):
                raw.append(0xFF if pixels[off + x] else 0x00)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    signature = b"\x89PNG\r\n\x1a\n"
    # IHDR: width, height, bit-depth, colour-type=0 (greyscale),
    # compression=0, filter=0, interlace=0.
    ihdr = struct.pack(">IIBBBBB", width, height, _BIT_DEPTH, 0, 0, 0, 0)
    idat = zlib.compress(bytes(raw), level=9)
    iend = b""

    png = signature + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", iend)
    _PNG_CACHE[key] = png
    return png


def main() -> None:
    p = argparse.ArgumentParser(description="TRMNL recon HTTP server")
    p.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    p.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    p.add_argument(
        "--bit-depth",
        type=int,
        choices=[1, 8],
        default=1,
        help="Generated PNG bit depth (default: 1). Use 8 if the client garbles 1-bit output.",
    )
    args = p.parse_args()

    global _BIT_DEPTH
    _BIT_DEPTH = args.bit_depth
    _PNG_CACHE.clear()

    addr = (args.host, args.port)
    httpd = http.server.HTTPServer(addr, ReconHandler)
    print(f"TRMNL recon listening on http://{args.host}:{args.port}/")
    print("Point your TRMNL client at this address.")
    print("All requests will be logged below. Ctrl+C to stop.\n", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nrecon: shutting down", flush=True)
        httpd.server_close()


if __name__ == "__main__":
    main()
