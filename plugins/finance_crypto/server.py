"""finance_crypto — current price + 24h sparkline via CoinGecko."""

from __future__ import annotations

import contextlib
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_S = 120
HTTP_TIMEOUT_S = 12
USER_AGENT = "tesserae/0.1 (+finance_crypto)"
SAFE = re.compile(r"^[a-z0-9-]+$")


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    coin = (options.get("coin") or "bitcoin").strip().lower()
    vs = (options.get("vs") or "usd").strip().lower()
    if not SAFE.match(coin) or not SAFE.match(vs):
        return {"error": "Bad coin or quote currency.", "price": None}

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"crypto_{coin}_{vs}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    try:
        price = _get(
            f"https://api.coingecko.com/api/v3/simple/price"
            f"?ids={coin}&vs_currencies={vs}&include_24hr_change=true&include_market_cap=true"
        )
        chart = _get(
            f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart?vs_currency={vs}&days=1"
        )
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "price": None}

    pdata = price.get(coin) or {}
    if not pdata.get(vs):
        return {"error": f"Unknown coin '{coin}'.", "price": None}

    # market_chart returns [[timestamp_ms, price], ...] — slim to numbers
    # so client can sparkline without parsing.
    prices = chart.get("prices") or []
    series = [round(float(p[1]), 6) for p in prices]

    result = {
        "coin": coin,
        "vs": vs,
        "price": pdata.get(vs),
        "change_24h": pdata.get(f"{vs}_24h_change"),
        "market_cap": pdata.get(f"{vs}_market_cap"),
        "series": series,
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result
