#!/usr/bin/env python3
"""Full-site diff: compare a fresh crawl against the committed baseline crawl.

Usage:
    # 1. re-crawl the whole site when the update lands:
    cd site-snapshot/crawl && cp ../../<path>/urls.txt . 2>/dev/null
    python3 ../tools/crawler.py            # writes pages_raw/ + manifest.jsonl

    # 2. diff fresh manifest vs baseline manifest, and line-diff changed pages:
    python3 ../tools/crawl_diff.py \
        --baseline-manifest pages-manifest.jsonl \
        --baseline-archive  pages.tar.xz \
        --new-manifest      <fresh>/manifest.jsonl \
        --new-pages         <fresh>/pages_raw

Reports every page whose content hash changed / was added / removed, then for
each changed page shows a unified line diff (old from the archive, new from the
fresh crawl) and the set of asset URLs that appeared/disappeared -- which is
where a hidden string, including one on a new image, will show up.
"""
import sys, os, json, argparse, tarfile, difflib, re, tempfile

def load_manifest(path):
    m = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("status") == 200 and r.get("sha256"):
                m[r["url"]] = r
    return m

def assets_in(html):
    urls = set()
    for x in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', html):
        if re.search(r'\.(png|jpe?g|gif|webp|svg|avif|css|js)(\?|$)', x, re.I):
            urls.add(x.replace('{width}', '1500'))
    for m in re.findall(r'srcset\s*=\s*["\']([^"\']+)["\']', html):
        for part in m.split(','):
            u = part.strip().split(' ')[0]
            if u:
                urls.add(u.replace('{width}', '1500'))
    return urls

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline-manifest", required=True)
    ap.add_argument("--baseline-archive", required=True)
    ap.add_argument("--new-manifest", required=True)
    ap.add_argument("--new-pages", required=True)
    ap.add_argument("--max-diff-lines", type=int, default=200)
    a = ap.parse_args()

    base = load_manifest(a.baseline_manifest)
    new = load_manifest(a.new_manifest)

    added = sorted(set(new) - set(base))
    removed = sorted(set(base) - set(new))
    changed = sorted(u for u in set(base) & set(new)
                     if base[u]["sha256"] != new[u]["sha256"])

    print("=" * 70)
    print(f"PAGES: {len(base)} baseline / {len(new)} new")
    print(f"  changed: {len(changed)}   added: {len(added)}   removed: {len(removed)}")
    print("=" * 70)
    for u in added:
        print("  [ADDED PAGE] ", u)
    for u in removed:
        print("  [REMOVED PAGE]", u)

    if not changed and not added:
        print("\nNo content changes detected across the whole site.")
        return

    # open baseline archive once, map filename -> member
    tf = tarfile.open(a.baseline_archive, "r:xz")
    members = {os.path.basename(m.name): m for m in tf.getmembers() if m.isfile()}

    def base_html(url):
        fn = base[url]["file"]
        m = members.get(fn) or members.get("./" + fn)
        if not m:
            return None
        return tf.extractfile(m).read().decode("utf-8", "replace")

    def new_html(url):
        fp = os.path.join(a.new_pages, new[url]["file"])
        if not os.path.exists(fp):
            return None
        return open(fp, encoding="utf-8", errors="replace").read()

    print("\n" + "=" * 70)
    print("CHANGED PAGES — line diffs + asset-reference changes")
    print("=" * 70)
    for u in changed:
        ob, nb = base_html(u), new_html(u)
        print("\n" + "#" * 70 + f"\n# {u}\n" + "#" * 70)
        if ob is None or nb is None:
            print("  (could not load one side; hashes differ)")
            continue
        d = list(difflib.unified_diff(ob.splitlines(), nb.splitlines(),
                                      "baseline", "new", lineterm=""))
        for line in d[:a.max_diff_lines]:
            print(line)
        if len(d) > a.max_diff_lines:
            print(f"... ({len(d)-a.max_diff_lines} more diff lines)")
        aold, anew = assets_in(ob), assets_in(nb)
        newassets = sorted(anew - aold)
        if newassets:
            print("\n  >>> NEW ASSET REFERENCES (fetch & inspect — string may be here):")
            for x in newassets:
                print("     ", x)
    tf.close()

if __name__ == '__main__':
    main()
