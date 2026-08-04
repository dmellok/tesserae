#!/usr/bin/env python3
"""Cheap change-detector for jamiekay.co.nz — the front line of monitoring.

Instead of re-crawling 2,785 pages every time, this fetches only the sitemaps
(+ the homepage) and compares against a small baseline. It fires whenever the
update could have landed; a full crawl_diff is only needed once it does.

    python3 watch_check.py baseline <out.json>     # capture baseline (once)
    python3 watch_check.py check    <baseline.json> # detect changes

`check` prints signals and exits 0 with "CHANGES DETECTED" in output if any of
these moved since baseline:
  * new or removed page URLs (products/collections/pages/blogs)
  * changed <lastmod> on collection/page URLs (product lastmods bump en masse,
    so they are ignored — product image changes are caught via image:loc)
  * new product primary-image URLs (<image:loc>)
  * new image URLs on the homepage (catches banner/theme code images)

When it reports changes, run a full crawl + tools/crawl_diff.py, then fetch and
read any brand-new image URL — that's where an image-based code lives.
"""
import sys, re, json, html, urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36"}
ROOT = "https://jamiekay.co.nz/sitemap.xml"


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode("utf-8", "replace")


def sub_sitemaps(root_xml):
    return [html.unescape(u) for u in re.findall(r'<loc>(.*?\.xml[^<]*)</loc>', root_xml)]


def collect():
    root = get(ROOT)
    subs = sub_sitemaps(root)
    pages = {}          # url -> lastmod ('' if none)
    prod_images = set()  # <image:loc> urls
    with ThreadPoolExecutor(max_workers=6) as ex:
        xmls = list(ex.map(lambda u: (u, get(u)), subs))
    for u, x in xmls:
        for block in re.findall(r'<url>(.*?)</url>', x, re.S):
            loc = re.search(r'(?<!image:)<loc>(.*?)</loc>', block)
            if not loc:
                continue
            page = html.unescape(loc.group(1).strip())
            lm = re.search(r'<lastmod>(.*?)</lastmod>', block)
            pages[page] = lm.group(1).strip() if lm else ''
            for im in re.findall(r'<image:loc>(.*?)</image:loc>', block):
                prod_images.add(html.unescape(im.strip()))
    # homepage image set
    home = get("https://jamiekay.co.nz/")
    home_imgs = set()
    for m in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', home):
        if re.search(r'\.(png|jpe?g|gif|webp|avif)(\?|$)', m, re.I):
            home_imgs.add(m.replace('{width}', '1500'))
    for m in re.findall(r'srcset\s*=\s*["\']([^"\']+)["\']', home):
        for part in m.split(','):
            u2 = part.strip().split(' ')[0]
            if u2 and re.search(r'\.(png|jpe?g|gif|webp|avif)', u2, re.I):
                home_imgs.add(u2.replace('{width}', '1500'))
    return {"pages": pages, "prod_images": sorted(prod_images),
            "home_images": sorted(home_imgs)}


def is_collection_or_page(u):
    return '/collections/' in u or '/pages/' in u or '/blogs/' in u


def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    mode, path = sys.argv[1], sys.argv[2]
    if mode == "baseline":
        data = collect()
        json.dump(data, open(path, "w"), indent=0, sort_keys=True)
        print(f"baseline: {len(data['pages'])} pages, "
              f"{len(data['prod_images'])} product images, "
              f"{len(data['home_images'])} homepage images -> {path}")
        return

    base = json.load(open(path))
    now = collect()
    bp, npg = base["pages"], now["pages"]
    new_pages = sorted(set(npg) - set(bp))
    gone_pages = sorted(set(bp) - set(npg))
    changed_lm = sorted(u for u in set(bp) & set(npg)
                        if is_collection_or_page(u) and bp[u] != npg[u])
    new_prod_imgs = sorted(set(now["prod_images"]) - set(base["prod_images"]))
    new_home_imgs = sorted(set(now["home_images"]) - set(base["home_images"]))

    changed = bool(new_pages or gone_pages or changed_lm or new_prod_imgs or new_home_imgs)
    print("CHANGES DETECTED" if changed else "no changes")
    def show(t, items, cap=50):
        if items:
            print(f"\n{t} ({len(items)}):")
            for x in items[:cap]:
                print("  ", x)
            if len(items) > cap:
                print(f"   ... (+{len(items)-cap} more)")
    show("NEW PAGES", new_pages)
    show("REMOVED PAGES", gone_pages)
    show("COLLECTION/PAGE lastmod changed", changed_lm)
    show("NEW product primary images", new_prod_imgs)
    show("NEW homepage images (fetch & read — code candidates)", new_home_imgs)
    sys.exit(0)


if __name__ == '__main__':
    main()
