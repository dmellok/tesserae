#!/usr/bin/env python3
"""Diff a fresh capture of the site against the committed baseline.

Usage:
    # 1. capture a new snapshot when the update lands:
    python3 tools/mirror.py https://jamiekay.co.nz/ /tmp/new-capture

    # 2. diff it against the baseline:
    python3 tools/diff.py site-snapshot/baseline /tmp/new-capture

Reports:
    * HTML line diff (surfaces any hidden text, comments, data-* attrs, meta tags)
    * text-asset (css/js/svg) line diffs for files present in both
    * ASSET CHANGES by content hash: added / removed / changed URLs
    * a short list of changed/new IMAGE urls to fetch and eyeball/OCR

Everything the manifest flags as a changed or new image is where a hidden
string could be living in a picture -- fetch those URLs and inspect them.
"""
import sys, os, json, difflib, re


def load(path):
    with open(os.path.join(path, 'manifest.json')) as f:
        return json.load(f)


def is_image(url):
    return bool(re.search(r'\.(png|jpe?g|gif|webp|avif)(\?|$)', url, re.I))


def is_text(url):
    return bool(re.search(r'\.(css|js|svg)(\?|$)', url, re.I))


def read(path, sub):
    p = os.path.join(path, sub)
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8', errors='replace') as f:
        return f.read().splitlines()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    base, new = sys.argv[1], sys.argv[2]
    mb, mn = load(base), load(new)

    print("=" * 70)
    print("1) HTML LINE DIFF (index.html)")
    print("=" * 70)
    a = read(base, 'index.html') or []
    b = read(new, 'index.html') or []
    diff = list(difflib.unified_diff(a, b, 'baseline/index.html',
                                     'new/index.html', lineterm=''))
    if diff:
        print("\n".join(diff))
    else:
        print("(no change in raw HTML)")

    print("\n" + "=" * 70)
    print("2) ASSET CHANGES BY CONTENT HASH")
    print("=" * 70)
    hb = {u: v.get('sha256') for u, v in mb.items() if v.get('status') == 200}
    hn = {u: v.get('sha256') for u, v in mn.items() if v.get('status') == 200}
    added = sorted(set(hn) - set(hb))
    removed = sorted(set(hb) - set(hn))
    changed = sorted(u for u in set(hb) & set(hn) if hb[u] != hn[u])

    def show(title, items):
        print(f"\n-- {title} ({len(items)}) --")
        for u in items:
            tag = "IMG" if is_image(u) else ("TXT" if is_text(u) else "   ")
            print(f"  [{tag}] {u}")

    show("ADDED urls", added)
    show("REMOVED urls", removed)
    show("CHANGED content (same url)", changed)

    print("\n" + "=" * 70)
    print("3) TEXT-ASSET LINE DIFFS (css/js/svg present in both)")
    print("=" * 70)
    any_txt = False
    for u in changed:
        if not is_text(u):
            continue
        fb = mb[u].get('file')
        fn = mn[u].get('file')
        la = read(base, os.path.join('assets', fb)) if fb else None
        lb = read(new, os.path.join('assets', fn)) if fn else None
        if la is None or lb is None:
            continue
        d = list(difflib.unified_diff(la, lb, u, u, lineterm=''))
        if d:
            any_txt = True
            print("\n" + "-" * 60 + f"\n{u}\n" + "-" * 60)
            print("\n".join(d[:400]))
    if not any_txt:
        print("(no changed text assets, or none stored locally)")

    print("\n" + "=" * 70)
    print("4) IMAGES TO INSPECT (changed or newly added pictures)")
    print("=" * 70)
    imgs = [u for u in changed if is_image(u)] + [u for u in added if is_image(u)]
    if imgs:
        print("Fetch and eyeball / OCR each -- a hidden string may live here:")
        for u in imgs:
            print("  " + u)
    else:
        print("(no image changes detected)")


if __name__ == '__main__':
    main()
