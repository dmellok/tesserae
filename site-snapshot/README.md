# Site snapshot & diff — jamiekay.co.nz

Purpose: catch a string hidden *somewhere* on the site (including inside an
image) when an update lands, by comparing a **baseline captured now** against a
**fresh capture taken after the update**.

- **Baseline captured:** 2026-08-04 (UTC) from `https://jamiekay.co.nz/`
- **Site type:** Shopify (Pipeline theme), server-rendered HTML — the raw HTML
  is the authoritative content; no JS rendering needed to see the copy.

## What's stored here

```
site-snapshot/
  baseline/
    index.html          raw homepage HTML (the primary baseline)
    http-headers.txt    response headers at capture time
    manifest.json       sha256 of the page + all 298 referenced assets  <-- detection backbone
    assets/             the CSS/JS/SVG text assets, byte-for-byte (52 files)
  tools/
    mirror.py           re-capture the site identically
    diff.py             diff a fresh capture against this baseline
```

**Images are not committed here** (109 MB). Their sha256 hashes *are* in
`manifest.json`, which is all that's needed to detect *which* image changed;
the changed/new image is then re-fetched from the live site to read it. A full
byte-level snapshot including every image was provided separately as a tarball.

## When the update lands — how to diff

```bash
# 1. capture a fresh snapshot
python3 site-snapshot/tools/mirror.py https://jamiekay.co.nz/ /tmp/new-capture

# 2. diff it against the baseline
python3 site-snapshot/tools/diff.py site-snapshot/baseline /tmp/new-capture
```

`diff.py` reports:
1. **HTML line diff** — surfaces any hidden text: new copy, HTML comments,
   `data-*` attributes, `meta` tags, `alt` text, `display:none` blocks, etc.
2. **Asset changes by hash** — added / removed / changed URLs.
3. **Text-asset (CSS/JS/SVG) line diffs** — a string tucked into a stylesheet
   comment or script shows up here.
4. **Images to inspect** — every changed or newly-added picture, to fetch and
   read (visually or via OCR). Because Shopify versions asset URLs with `?v=`,
   an updated image changes both its URL in the HTML and its bytes, so it is
   reliably flagged.

## Notes / caveats

- Responsive image URLs use a `{width}` placeholder; `mirror.py` pins it to
  `1500` so baseline and re-capture request the identical URL.
- This baseline covers the **homepage** (the page the Facebook link lands on).
  Point `mirror.py` at other paths to baseline more pages if needed.
- Shopify/Cloudflare set per-request cookies and rotate a few analytics tokens;
  those may appear as harmless noise in the HTML diff. The hidden string will be
  a real content change, not a rotating token.
