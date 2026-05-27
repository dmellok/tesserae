"""finance_currency — single FX pair via frankfurter.app (ECB-backed, free)."""

from __future__ import annotations

import contextlib
import json
import re
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

CACHE_TTL_S = 6 * 3600  # ECB rates update once a day
HTTP_TIMEOUT_S = 10
USER_AGENT = "tesserae/0.1 (+finance_currency)"
CODE = re.compile(r"^[A-Z]{3}$")


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch(options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]) -> dict[str, Any]:
    del settings
    base = (options.get("base") or "AUD").strip().upper()
    quote = (options.get("quote") or "USD").strip().upper()
    if not (CODE.match(base) and CODE.match(quote)):
        return {"error": "Use 3-letter ISO currency codes (e.g. USD, EUR, AUD).", "rate": None}

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"fx_{base}_{quote}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    end = datetime.now(UTC).date()
    start = end - timedelta(days=30)
    try:
        # /latest gives today's reference rate; range gives the 30-day series.
        latest = _get(f"https://api.frankfurter.app/latest?from={base}&to={quote}")
        series = _get(f"https://api.frankfurter.app/{start}..{end}?from={base}&to={quote}")
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "rate": None}

    rate = (latest.get("rates") or {}).get(quote)
    if rate is None:
        return {"error": f"Unknown pair {base}/{quote}.", "rate": None}

    rates_by_day = series.get("rates") or {}
    # Sort by date, take the .to-currency value.
    pairs = sorted(rates_by_day.items())
    points = [round(d.get(quote) or 0, 6) for _, d in pairs]
    first = points[0] if points else None
    change_pct = ((rate - first) / first * 100.0) if first else None

    result = {
        "base":   base,
        "quote":  quote,
        "rate":   rate,
        "series": points,
        "as_of":  latest.get("date"),
        "change_30d": change_pct,
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result))
    return result
