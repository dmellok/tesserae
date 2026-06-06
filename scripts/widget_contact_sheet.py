"""widget_contact_sheet.py, drive Playwright across every widget × variant
× size, saving a PNG per frame under ``screenshots/`` so a human (or an
agent) can scan for layout regressions in one pass.

Usage::

    .venv/bin/python scripts/widget_contact_sheet.py \\
        --base http://localhost:8765 \\
        --password 'your-dev-password' \\
        --sizes md           # default; pass "all" for xs/sm/md/lg
        --out screenshots

The script reuses the dev gallery's ``/_test/render?plugin=…&size=…&
variant=…&sample=1`` endpoint so it matches what the gallery shows.
Auth is via a one-shot POST to /login; the resulting session cookie
flows through every render request via the Playwright context.

The directory is gitignored, file names encode the frame:

    {plugin_id}__{variant}__{size}.png
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from playwright.sync_api import sync_playwright

# Cell dims must match SIZE_DIMENSIONS in app/composer.py, we set the
# Playwright viewport to the cell dims so the screenshot is pixel-exact.
SIZES: dict[str, tuple[int, int]] = {
    "xs": (180, 180),
    "sm": (380, 240),
    "md": (640, 400),
    "lg": (1200, 800),
}


def login(base: str, password: str) -> str:
    """Log in via the /login form and return the session cookie value."""
    s = requests.Session()
    # Fetch the form first so we get any CSRF token + initial session.
    s.get(f"{base}/login", timeout=10)
    resp = s.post(
        f"{base}/login",
        data={"password": password},
        allow_redirects=False,
        timeout=10,
    )
    if resp.status_code not in (302, 303):
        raise SystemExit(
            f"Login failed: HTTP {resp.status_code}. "
            "Check the password (or whether the dev server requires one)."
        )
    cookies = s.cookies.get_dict()
    if "session" not in cookies:
        raise SystemExit(
            f"Login succeeded ({resp.status_code}) but no 'session' cookie "
            f"came back. Got: {list(cookies.keys())}"
        )
    return cookies["session"]


def list_widgets(base: str, session_cookie: str) -> list[dict]:
    """Hit the gallery route's underlying registry and pull every widget's
    id, supported sizes, and variants. We do this by parsing the widget
    list out of the gallery HTML, it already exposes everything we need
    in data-* attrs and select options."""
    headers = {"Cookie": f"session={session_cookie}"}
    r = requests.get(f"{base}/_test/widgets", headers=headers, timeout=15)
    r.raise_for_status()
    html = r.text

    # Walk the gallery DOM cheap-and-cheerful via string ops. Each widget
    # card has ``data-widget-id="..."``; the per-card variant <select>
    # carries ``data-widget-variant data-widget-id="..."`` with one
    # <option value="..."> per direction. No bs4 dep, keep it light.
    widgets: list[dict] = []
    import re

    # Pull widget ids + supported sizes from the section markup.
    card_iter = re.finditer(
        r'<section class="widget"[^>]*data-widget-id="([^"]+)"[^>]*>'
        r"(?P<body>.+?)</section>",
        html,
        re.DOTALL,
    )
    for m in card_iter:
        wid = m.group(1)
        body = m.group("body")
        sizes = re.findall(r'data-widget-size[^>]*data-native-w="(\d+)"', body)
        # Map dims back to size keys.
        size_keys: list[str] = []
        for w_str in sizes:
            w = int(w_str)
            for sk, (sw, _sh) in SIZES.items():
                if sw == w:
                    size_keys.append(sk)
                    break
        # Variant <option value> list (if any).
        variants: list[str] = []
        sel_match = re.search(
            r'data-widget-variant[^>]*data-widget-id="'
            + re.escape(wid)
            + r'"[^>]*>(?P<sel>.+?)</select>',
            body,
            re.DOTALL,
        )
        if sel_match:
            variants = re.findall(r'<option value="([^"]+)"', sel_match.group("sel"))
        widgets.append({"id": wid, "sizes": size_keys, "variants": variants})
    return widgets


def render_url(base: str, plugin: str, size: str, variant: str) -> str:
    qs = {"plugin": plugin, "size": size, "sample": "1"}
    if variant:
        qs["variant"] = variant
    return f"{base}/_test/render?{urlencode(qs)}"


def run(
    base: str,
    password: str,
    out_dir: Path,
    size_filter: list[str] | None,
    headless: bool,
) -> None:
    print(f"[1/3] Logging in to {base} ...", flush=True)
    cookie = login(base, password)

    print("[2/3] Listing widgets from gallery ...", flush=True)
    widgets = list_widgets(base, cookie)
    if not widgets:
        raise SystemExit("No widgets found, is /_test/widgets reachable?")

    out_dir.mkdir(parents=True, exist_ok=True)

    # One big counted flat list so we can print progress as we go.
    frames: list[tuple[str, str, str]] = []
    for w in widgets:
        sizes = [s for s in (size_filter or w["sizes"]) if s in w["sizes"]]
        if not sizes:
            continue
        variants = w["variants"] or [""]
        for v in variants:
            for sk in sizes:
                frames.append((w["id"], v, sk))

    print(f"[3/3] Rendering {len(frames)} frames ...", flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1200, "height": 800},
            device_scale_factor=1,
        )
        # Drop the session cookie into the context so /_test/render
        # passes the auth gate. Domain is the loopback host.
        from urllib.parse import urlparse

        host = urlparse(base).hostname or "localhost"
        context.add_cookies(
            [
                {
                    "name": "session",
                    "value": cookie,
                    "domain": host,
                    "path": "/",
                    "httpOnly": True,
                    "sameSite": "Lax",
                }
            ]
        )

        manifest: list[dict] = []
        page = context.new_page()
        t0 = time.time()
        for i, (wid, variant, size) in enumerate(frames, 1):
            url = render_url(base, wid, size, variant)
            w, h = SIZES[size]
            page.set_viewport_size({"width": w, "height": h})
            try:
                page.goto(url, wait_until="networkidle", timeout=20_000)
                # Tiny settle so any tick/loop fires once. The widgets
                # are pure client-side at sample=1 so 200ms is plenty.
                page.wait_for_timeout(200)
                fname = f"{wid}__{variant or 'default'}__{size}.png"
                path = out_dir / fname
                page.screenshot(path=str(path), full_page=False, omit_background=False)
                manifest.append(
                    {
                        "plugin": wid,
                        "variant": variant,
                        "size": size,
                        "url": url,
                        "file": fname,
                        "ok": True,
                    }
                )
                print(f"  [{i}/{len(frames)}] {fname}", flush=True)
            except Exception as err:
                print(
                    f"  [{i}/{len(frames)}] FAILED {wid} {variant} {size}: {err}",
                    file=sys.stderr,
                    flush=True,
                )
                manifest.append(
                    {
                        "plugin": wid,
                        "variant": variant,
                        "size": size,
                        "url": url,
                        "file": None,
                        "ok": False,
                        "error": f"{type(err).__name__}: {err}",
                    }
                )
        page.close()
        context.close()
        browser.close()

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    elapsed = time.time() - t0
    ok_n = sum(1 for r in manifest if r["ok"])
    print(
        f"Done. {ok_n}/{len(manifest)} ok in {elapsed:.1f}s. Output: {out_dir.resolve()}",
        flush=True,
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:8765")
    p.add_argument("--password", required=True)
    p.add_argument("--out", type=Path, default=Path("screenshots"))
    p.add_argument(
        "--sizes",
        default="md",
        help="Comma list of size keys (xs,sm,md,lg) or 'all'. Default md.",
    )
    p.add_argument("--show", action="store_true", help="Run browser non-headless.")
    args = p.parse_args()

    sizes = None if args.sizes == "all" else args.sizes.split(",")
    run(args.base, args.password, args.out, sizes, headless=not args.show)


if __name__ == "__main__":
    main()
