"""Catalog screenshot emitter for Tesserae widgets.

Writes a widget's faithful-render screenshot set, ``lg.png`` (1200x800) plus
``extra-1..N.png`` from a preset list, by driving a running Tesserae
instance's MCP ``render.png`` endpoint. That endpoint IS Studio's faithful /
Playwright render path, so this reuses it verbatim: no second renderer, no
browser in this process. It just needs a Tesserae server to talk to (loopback
by default, the same one Studio uses).

    python -m app.screenshots <id> --out screenshots/<id>/ --lg \\
        --extra presets.json

``presets.json`` is a JSON list of ``{"name": str, "options": {...}}``; each
entry may also carry ``size`` (xs|sm|md|lg, default lg) or explicit ``w`` &
``h``. Entry *i* is written as ``extra-<i>.png`` rendered with those cell
options, so configured / generative states come out reproducibly (a widget
honours any seed / day it reads from its options).

Connection defaults to ``TESSERAE_URL`` (http://127.0.0.1:8765) and, when the
server isn't on loopback, ``TESSERAE_MCP_TOKEN`` for the bearer header; both
are overridable with ``--url`` / ``--token``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE = os.environ.get("TESSERAE_URL", "http://127.0.0.1:8765").rstrip("/")
DEFAULT_TOKEN = os.environ.get("TESSERAE_MCP_TOKEN", "").strip()


def _render(
    base: str,
    token: str,
    widget_id: str,
    *,
    size: str | None = None,
    w: int | None = None,
    h: int | None = None,
    options: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> bytes:
    """Fetch one faithful-render PNG from the running server's render.png
    endpoint. Explicit ``w,h`` win over ``size`` (matching the endpoint)."""
    params: dict[str, str] = {}
    if w is not None and h is not None:
        params["w"], params["h"] = str(w), str(h)
    elif size:
        params["size"] = size
    if options:
        params["opts"] = json.dumps(options, separators=(",", ":"))
    url = f"{base}/api/mcp/widgets/{urllib.parse.quote(widget_id)}/render.png"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return bytes(resp.read())
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace").strip()
        raise SystemExit(f"render failed ({err.code}) for {widget_id!r}: {detail}") from err
    except urllib.error.URLError as err:
        raise SystemExit(
            f"cannot reach Tesserae at {base} ({err.reason}). Is the server running?"
        ) from err


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.screenshots",
        description="Emit a widget's catalog screenshot set via the faithful renderer.",
    )
    parser.add_argument("widget_id", help="widget id (as in plugins/<id>/ and the catalog)")
    parser.add_argument("--out", required=True, help="output directory (created if absent)")
    parser.add_argument("--lg", action="store_true", help="write lg.png (1200x800)")
    parser.add_argument("--extra", help="path to a presets JSON list ({name, options})")
    parser.add_argument(
        "--url", default=DEFAULT_BASE, help=f"server base URL (default {DEFAULT_BASE})"
    )
    parser.add_argument(
        "--token", default=DEFAULT_TOKEN, help="MCP bearer token (loopback needs none)"
    )
    args = parser.parse_args(argv)

    if not args.lg and not args.extra:
        parser.error("nothing to do: pass --lg and/or --extra")

    base = args.url.rstrip("/")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    if args.lg:
        png = _render(base, args.token, args.widget_id, size="lg")
        (out / "lg.png").write_bytes(png)
        written.append("lg.png")

    if args.extra:
        try:
            presets = json.loads(Path(args.extra).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise SystemExit(f"could not read presets file {args.extra!r}: {err}") from err
        if not isinstance(presets, list):
            raise SystemExit("--extra file must be a JSON list of {name, options} objects")
        for i, preset in enumerate(presets, start=1):
            if not isinstance(preset, dict):
                raise SystemExit(f"preset {i} must be an object, got {type(preset).__name__}")
            options = preset.get("options") if isinstance(preset.get("options"), dict) else {}
            png = _render(
                base,
                args.token,
                args.widget_id,
                size=str(preset.get("size", "lg")),
                w=preset.get("w"),
                h=preset.get("h"),
                options=options,
            )
            name = f"extra-{i}.png"
            (out / name).write_bytes(png)
            written.append(name)

    print(f"wrote {len(written)} screenshot(s) to {out}/: {', '.join(written)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
