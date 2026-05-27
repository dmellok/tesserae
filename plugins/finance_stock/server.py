"""finance_stock — single stock via Yahoo Finance's public chart endpoint.

Yahoo's /v8/finance/chart endpoint isn't officially documented but has
been stable for many years and is what most "free stock API" libraries
reach for. No key required.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 120
HTTP_TIMEOUT_S = 12
USER_AGENT = "Mozilla/5.0 (compatible; tesserae/0.1)"
SAFE = re.compile(r"^[A-Z0-9.^=-]{1,10}$")
RANGE_INTERVAL = {
    "1d":  "5m",
    "5d":  "15m",
    "1mo": "30m",
    "3mo": "1d",
    "1y":  "1d",
}


def fetch(options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]) -> dict[str, Any]:
    del settings
    symbol = (options.get("symbol") or "AAPL").strip().upper()
    if not SAFE.match(symbol):
        return {"error": "Set a valid Yahoo Finance ticker.", "price": None}
    rng = options.get("range", "1d")
    if rng not in RANGE_INTERVAL:
        rng = "1d"
    interval = RANGE_INTERVAL[rng]

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"yf_{symbol.replace('.', '_').replace('^', '_').replace('=', '_')}_{rng}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(symbol)
        + f"?range={rng}&interval={interval}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "price": None}

    results = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not results:
        err = (((payload.get("chart") or {}).get("error") or {}).get("description")) or "Unknown ticker"
        return {"error": str(err), "price": None}

    meta = results.get("meta") or {}
    indicators = (results.get("indicators") or {}).get("quote") or [{}]
    closes_raw = indicators[0].get("close") or []
    series = [round(float(v), 4) for v in closes_raw if v is not None]

    price = meta.get("regularMarketPrice")
    prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
    change_pct = ((price - prev_close) / prev_close * 100.0) if (price and prev_close) else None

    result = {
        "symbol":     symbol,
        "name":       meta.get("shortName") or meta.get("longName") or symbol,
        "currency":   meta.get("currency") or "USD",
        "exchange":   meta.get("exchangeName") or "",
        "price":      price,
        "prev_close": prev_close,
        "change_pct": change_pct,
        "range":      rng,
        "series":     series,
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result))
    return result
