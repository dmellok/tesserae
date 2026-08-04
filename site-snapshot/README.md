# Site snapshot & diff — jamiekay.co.nz

Purpose: catch content hidden *somewhere* on the site when an update lands —
specifically **image-based codes** — by comparing a **baseline captured now**
against a **fresh capture taken after the update**.

- **Baseline captured:** 2026-08-04 (UTC) from `https://jamiekay.co.nz/`
- **Site type:** Shopify (Pipeline theme), server-rendered HTML — the raw HTML
  is the authoritative content; no JS rendering needed to see it.
- **Confirmed:** the hidden codes will be **images**. Detection therefore keys
  on **new/changed image URLs**. Shopify versions every asset URL (a unique
  filename or `?v=` stamp), so a code-image added in the update is a URL that
  does not exist anywhere in this baseline — that is what flags it.

## What's stored here

```
site-snapshot/
  baseline/                 SINGLE-PAGE homepage detail (byte-level)
    index.html              raw homepage HTML
    http-headers.txt        response headers at capture time
    manifest.json           sha256 of the homepage + all 298 referenced assets
    assets/                 homepage CSS/JS/SVG text assets, byte-for-byte (52 files)
  crawl/                    FULL-SITE baseline (all 2,785 pages)
    pages.tar.xz            every page's raw HTML, xz cross-file-dedup (29 MB)
    pages-manifest.jsonl    url -> {file, bytes, sha256, status} for all 2,785 pages
    asset-index.json        every asset URL referenced site-wide: 95,652 urls
                            (95,565 images) -> the "have I seen this image?" oracle
    urls.txt                the 2,785 page URLs (from sitemap.xml) to re-crawl
  tools/
    crawler.py              resumable, throttled full-site crawler
    build_asset_index.py    rebuild asset-index.json from crawled pages
    crawl_diff.py           diff a fresh full-site crawl vs. this baseline
    mirror.py               single-page capture (homepage-style)
    diff.py                 single-page diff
```

**Image bytes are intentionally not stored** (~95k images, many GB). They are
tracked by URL in `asset-index.json`; any code-image in the update is a new URL,
which is then fetched and read at diff time. This is reliable because Shopify
never reuses a URL for changed image content.

## When the update lands — how to diff the whole site

```bash
cd site-snapshot/crawl
mkdir -p new && cp urls.txt new/urls.txt

# 1. re-crawl every page (same URL list, same crawler)
( cd new && python3 ../../tools/crawler.py )

# 2. rebuild the asset index for the fresh crawl
python3 ../tools/build_asset_index.py new/pages_raw new/asset-index.json

# 3a. page-level diff: which pages changed, with line diffs + new asset refs
python3 ../tools/crawl_diff.py \
    --baseline-manifest pages-manifest.jsonl \
    --baseline-archive  pages.tar.xz \
    --new-manifest      new/manifest.jsonl \
    --new-pages         new/pages_raw

# 3b. site-wide NEW IMAGES (the code candidates): urls present now but not before
python3 - <<'PY'
import json
old=set(json.load(open('asset-index.json')))
new=set(json.load(open('new/asset-index.json')))
imgs=[u for u in sorted(new-old)
      if any(e in u.lower() for e in ('.png','.jpg','.jpeg','.webp','.gif','.avif'))]
print(f"{len(imgs)} new image URLs to fetch & read:")
for u in imgs: print(" ", u)
PY
```

Then **fetch each new image URL and read the code off it** (visually or via
OCR). Because the codes are images, step 3b is the primary detector; step 3a is
the backup that also catches any accompanying text/markup changes and pins down
which page each new image lives on.

## Notes / caveats

- Responsive image URLs use a `{width}` placeholder; the tools pin it to `1500`
  so baseline and re-capture reference identical URLs.
- The crawl covers every URL in the store's `sitemap.xml` (products,
  collections, pages, blogs). If codes could appear on a URL not in the sitemap
  (e.g. a hidden/unlinked page), add it to `urls.txt` before re-crawling.
- Shopify/Cloudflare set per-request cookies and rotate a few analytics tokens;
  these can appear as harmless noise in page line-diffs. A real code-image is a
  new URL in `asset-index.json`, not a rotating token.
- Full-site crawl stats: 2,785 pages, 0 failures, ~4.7 GB raw HTML compressed to
  a 29 MB archive via cross-file dedup.
