#!/usr/bin/env python3
"""Resumable, throttled full-site crawler.

Fetches every URL in urls.txt, stores raw HTML under pages_raw/, and appends a
record to manifest.jsonl: {url, file, bytes, sha256, status}. Resumable: URLs
already present in manifest.jsonl (status 200) are skipped. Backs off on rate
limiting / connection resets so it stays under the proxy limiter.

Set CB=1 in the environment on a re-crawl to cache-bust (see below), so a stale
cached page cannot hide a real change.
"""
import os, sys, json, time, hashlib, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36"}

# Cache-busting: set CB=1 to defeat Shopify page_cache / any CDN edge cache on a
# re-crawl, so a stale copy can't masquerade as "no change". Each request gets a
# unique ?cb= param (fresh cache key) plus no-cache headers; the manifest is
# still keyed by the CLEAN url so it compares against the baseline.
CACHE_BUST = os.environ.get("CB") == "1"
if CACHE_BUST:
    UA = dict(UA, **{"Cache-Control": "no-cache", "Pragma": "no-cache"})

def bust(url):
    if not CACHE_BUST:
        return url
    token = os.urandom(8).hex()
    return url + ("&" if "?" in url else "?") + "cb=" + token
OUT = "pages_raw"
MANIFEST = "manifest.jsonl"
PROGRESS = "progress.txt"
MAX_RETRIES = 6
WORKERS = 5

os.makedirs(OUT, exist_ok=True)
lock = threading.Lock()
counter = {"done": 0, "ok": 0, "fail": 0}


def safe_name(url):
    h = hashlib.sha1(url.encode()).hexdigest()[:12]
    slug = url.split("://", 1)[-1].strip("/").replace("/", "_")
    slug = "".join(c if c.isalnum() or c in "._-" else "_" for c in slug)[:90] or "root"
    return f"{h}__{slug}.html"


def load_done():
    done = set()
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("status") == 200:
                        done.add(r["url"])
                except Exception:
                    pass
    return done


def fetch(url):
    delay = 2.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(bust(url), headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read(), r.status
        except urllib.error.HTTPError as e:
            if e.code in (429, 430, 503, 502, 500) and attempt < MAX_RETRIES:
                time.sleep(delay); delay = min(delay * 2, 30); continue
            return None, e.code
        except Exception:
            if attempt < MAX_RETRIES:
                time.sleep(delay); delay = min(delay * 2, 30); continue
            return None, "ERR"
    return None, "ERR"


def handle(url):
    data, status = fetch(url)
    rec = {"url": url, "status": status if isinstance(status, int) else None}
    if data is not None and status == 200:
        fn = safe_name(url)
        with open(os.path.join(OUT, fn), "wb") as f:
            f.write(data)
        rec.update(file=fn, bytes=len(data),
                   sha256=hashlib.sha256(data).hexdigest())
    with lock:
        with open(MANIFEST, "a") as f:
            f.write(json.dumps(rec) + "\n")
        counter["done"] += 1
        if rec.get("sha256"):
            counter["ok"] += 1
        else:
            counter["fail"] += 1
        if counter["done"] % 25 == 0 or counter["done"] == TOTAL:
            with open(PROGRESS, "w") as p:
                p.write(f"{counter['done']}/{TOTAL} done | ok={counter['ok']} fail={counter['fail']}\n")
    return rec


urls = [u.strip() for u in open("urls.txt") if u.strip()]
done = load_done()
todo = [u for u in urls if u not in done]
TOTAL = len(urls)
counter["done"] = len(done)
counter["ok"] = len(done)
with open(PROGRESS, "w") as p:
    p.write(f"start: {len(done)} already done, {len(todo)} to fetch (total {TOTAL})\n")

with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    list(ex.map(handle, todo))

with open(PROGRESS, "a") as p:
    p.write(f"COMPLETE: {counter['done']}/{TOTAL} ok={counter['ok']} fail={counter['fail']}\n")
print(f"COMPLETE ok={counter['ok']} fail={counter['fail']}")
