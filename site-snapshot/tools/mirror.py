#!/usr/bin/env python3
"""Mirror a page + its referenced assets and build a sha256 manifest.

Usage:
    python3 mirror.py <url> <out_dir>

Produces, inside <out_dir>:
    index.html      raw HTML of the page
    http-headers.txt response headers
    assets/         every referenced css/js/svg/image, fetched byte-for-byte
    manifest.json   { url: {file, bytes, sha256, status} } for the page + all assets

The manifest is what makes diffing reliable: re-run this later and compare
manifests to see exactly which assets changed, were added, or removed.

Shopify note: responsive image URLs contain a literal "{width}" placeholder.
We substitute a FIXED width (1500) so the baseline and any later capture request
the identical URL and stay byte-comparable.
"""
import re, os, sys, json, hashlib, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

FIXED_WIDTH = "1500"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36"}


def base_origin(url):
    m = re.match(r"(https?://[^/]+)", url)
    return m.group(1) if m else ""


def fetch_url(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(), dict(r.headers)


def extract_assets(html, origin):
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

    def norm(u):
        u = u.strip().replace('{width}', FIXED_WIDTH)
        if u.startswith('//'):
            return 'https:' + u
        if u.startswith('http://') or u.startswith('https://'):
            return u
        if u.startswith('/'):
            return origin + u
        return None

    out = set()
    for u in urls:
        n = norm(u)
        if n and re.search(r'\.(png|jpe?g|gif|webp|svg|avif|css|js)(\?|$)', n, re.I):
            out.add(n)
    return sorted(out)


def safe_name(url):
    h = hashlib.sha1(url.encode()).hexdigest()[:10]
    base = re.sub(r'[?#].*', '', url).rstrip('/').split('/')[-1] or 'index'
    base = re.sub(r'[^A-Za-z0-9._-]', '_', base)[:80]
    return f"{h}__{base}"


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    url, out = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.join(out, 'assets'), exist_ok=True)

    page, headers = fetch_url(url)
    with open(os.path.join(out, 'index.html'), 'wb') as f:
        f.write(page)
    with open(os.path.join(out, 'http-headers.txt'), 'w') as f:
        for k, v in headers.items():
            f.write(f"{k}: {v}\n")

    html = page.decode('utf-8', 'replace')
    assets = extract_assets(html, base_origin(url))

    manifest = {'__PAGE__:index.html': {
        'file': 'index.html', 'bytes': len(page),
        'sha256': hashlib.sha256(page).hexdigest(), 'status': 200}}

    def grab(u):
        try:
            data, _ = fetch_url(u)
            fn = safe_name(u)
            with open(os.path.join(out, 'assets', fn), 'wb') as f:
                f.write(data)
            return u, {'file': fn, 'bytes': len(data),
                       'sha256': hashlib.sha256(data).hexdigest(), 'status': 200}
        except urllib.error.HTTPError as e:
            return u, {'file': None, 'status': e.code, 'error': str(e)}
        except Exception as e:
            return u, {'file': None, 'status': None, 'error': str(e)}

    with ThreadPoolExecutor(max_workers=6) as ex:
        for u, meta in ex.map(grab, assets):
            manifest[u] = meta

    with open(os.path.join(out, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    ok = sum(1 for v in manifest.values() if v.get('status') == 200)
    print(f"page + {len(assets)} assets; {ok} ok, {len(manifest)-ok} failed -> {out}")


if __name__ == '__main__':
    main()
