#!/usr/bin/env python3
"""Classify image changes between two site-wide asset indexes.

Catches BOTH brand-new images and *updated* (replaced) images. On Shopify an
updated image gets a new version stamp (?v=) and/or filename, so it is detected
as a new URL for an already-seen base image.

    python3 find_changed_images.py <baseline asset-index.json> <new asset-index.json>

Image identity:
  base   = filename with the size-variant suffix (_360x, _1500x600, ...) and the
           query string removed  -> identifies the underlying picture
  version= the ?v= stamp         -> identifies the content revision

Reports:
  NEW image     : a base filename never seen before  -> a code image added
  UPDATED image : a known base filename with a new ?v= version -> image replaced
  (size-only variants and re-seen versions are ignored as non-changes)

Every NEW or UPDATED url should be fetched and read — an image-based code lives
in one of them.
"""
import sys, re, json

IMG = re.compile(r'\.(png|jpe?g|gif|webp|avif)(\?|$)', re.I)


def parse(url):
    m = re.search(r'[?&]v=([^&]+)', url)
    version = m.group(1) if m else ''
    path = re.sub(r'[?#].*$', '', url)
    # strip a trailing Shopify size-variant token: _123x, _123x456
    base = re.sub(r'_\d+x(\d+)?(?=\.[A-Za-z0-9]+$)', '', path)
    return base, version, path


def index(assets):
    """base -> set(versions), plus base -> a sample full url per version."""
    bases = {}
    sample = {}
    for u in assets:
        if not IMG.search(u):
            continue
        base, version, _ = parse(u)
        bases.setdefault(base, set()).add(version)
        sample[(base, version)] = u
    return bases, sample


def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    old = json.load(open(sys.argv[1]))
    new = json.load(open(sys.argv[2]))
    ob, _ = index(old)
    nb, nsample = index(new)

    new_imgs, updated_imgs = [], []
    for base, versions in sorted(nb.items()):
        if base not in ob:
            for v in sorted(versions):
                new_imgs.append(nsample[(base, v)])
        else:
            fresh = versions - ob[base]
            for v in sorted(fresh):
                updated_imgs.append(nsample[(base, v)])

    print(f"baseline images: {len(ob)} base files | new index: {len(nb)} base files")
    print(f"NEW images: {len(new_imgs)} | UPDATED images: {len(updated_imgs)}\n")
    if new_imgs:
        print("=== NEW images (brand-new picture — fetch & read) ===")
        for u in new_imgs:
            print(" ", u)
    if updated_imgs:
        print("\n=== UPDATED images (existing picture replaced — fetch & read) ===")
        for u in updated_imgs:
            print(" ", u)
    if not new_imgs and not updated_imgs:
        print("No new or updated images.")


if __name__ == '__main__':
    main()
