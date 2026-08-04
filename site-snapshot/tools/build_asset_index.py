#!/usr/bin/env python3
"""Build a site-wide index of every asset URL referenced by any crawled page.

Usage:
    python3 build_asset_index.py <pages_raw_dir> <out.json>

Output JSON: { asset_url: [count, first_page_file] } for every css/js/svg/image
URL referenced anywhere on the site. Lets the diff spot a newly-added image
site-wide even if you only glance at the asset index.

Shopify note: {width} placeholders are normalized to 1500 so the index is
stable across captures (matches mirror.py).
"""
import os, sys, re, json, glob

def norm(u):
    u = u.strip().replace('{width}', '1500')
    if u.startswith('//'):
        return 'https:' + u
    if u.startswith('http://') or u.startswith('https://'):
        return u
    if u.startswith('/'):
        return 'https://jamiekay.co.nz' + u
    return None

def extract(html):
    urls = set()
    for m in re.findall(r'(?:src|href|content)\s*=\s*["\']([^"\']+)["\']', html):
        urls.add(m)
    for m in re.findall(r'srcset\s*=\s*["\']([^"\']+)["\']', html):
        for part in m.split(','):
            u = part.strip().split(' ')[0]
            if u:
                urls.add(u)
    for _q, u in re.findall(r'url\((["\']?)([^)\'"]+)\1\)', html):
        urls.add(u)
    out = set()
    for u in urls:
        n = norm(u)
        if n and re.search(r'\.(png|jpe?g|gif|webp|svg|avif|css|js)(\?|$)', n, re.I):
            out.add(n)
    return out

def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    src, out = sys.argv[1], sys.argv[2]
    idx = {}
    files = glob.glob(os.path.join(src, '*.html'))
    for fp in files:
        html = open(fp, encoding='utf-8', errors='replace').read()
        base = os.path.basename(fp)
        for u in extract(html):
            if u in idx:
                idx[u][0] += 1
            else:
                idx[u] = [1, base]
    json.dump(idx, open(out, 'w'), indent=0, sort_keys=True)
    imgs = sum(1 for u in idx if re.search(r'\.(png|jpe?g|gif|webp|avif)(\?|$)', u, re.I))
    print(f"{len(files)} pages -> {len(idx)} unique asset urls ({imgs} images)")

if __name__ == '__main__':
    main()
