"""sky_moon — current moon phase + upcoming major phases.

Phase + illumination are computed locally from the synodic-month length
anchored to a known new-moon epoch (2000-01-06 18:14 UTC). Accuracy is
better than ±0.5 day for centuries either side of the epoch — plenty
for a "what does the moon look like tonight" widget.

Moonrise / moonset comes from Open-Meteo when lat/lon are set; failure
to fetch is non-fatal (the rest of the card still renders).
"""

from __future__ import annotations

import contextlib
import json
import math
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

CACHE_TTL_S = 3600  # phase moves slowly; 1h is plenty
HTTP_TIMEOUT_S = 12
USER_AGENT = "tesserae/0.1 (+sky_moon)"

# Known new moon: 2000-01-06 18:14 UTC. Synodic month: ~29.5305881 days.
_NEW_MOON_EPOCH = datetime(2000, 1, 6, 18, 14, tzinfo=UTC)
_SYNODIC_DAYS = 29.5305881


def _phase_age_days(now: datetime) -> float:
    """Age in days since the last new moon (0..synodic)."""
    delta = (now - _NEW_MOON_EPOCH).total_seconds() / 86400.0
    return delta % _SYNODIC_DAYS


def _illumination(age: float) -> float:
    """Fraction of disc illuminated, 0..1.
    Cosine of phase angle, mapped 0=new, 1=full."""
    phase_angle = 2 * math.pi * age / _SYNODIC_DAYS
    return (1 - math.cos(phase_angle)) / 2


def _phase_name(age: float) -> str:
    """Common name for a phase age in days."""
    # Boundaries at ~3.7 day intervals between the 8 standard phases.
    if age < 1.0 or age > _SYNODIC_DAYS - 1.0:
        return "New Moon"
    if age < 6.4:
        return "Waxing Crescent"
    if age < 8.4:
        return "First Quarter"
    if age < 13.8:
        return "Waxing Gibbous"
    if age < 15.8:
        return "Full Moon"
    if age < 21.1:
        return "Waning Gibbous"
    if age < 23.1:
        return "Last Quarter"
    return "Waning Crescent"


def _next_phase(now: datetime, target_fraction: float) -> datetime:
    """Find the next datetime where the moon age / synodic = target_fraction.
    target_fraction ∈ {0, 0.25, 0.5, 0.75}."""
    current = _phase_age_days(now) / _SYNODIC_DAYS
    delta = (target_fraction - current) % 1.0
    if delta == 0:
        delta = 1.0
    return now + timedelta(days=delta * _SYNODIC_DAYS)


def _fetch_moonrise(lat: float, lon: float) -> tuple[str | None, str | None]:
    """Return (moonrise_iso, moonset_iso) for today, or (None, None) on
    failure. Open-Meteo's daily endpoint serves these without auth."""
    if lat == 0 and lon == 0:
        return None, None
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=moonrise,moonset&timezone=auto&forecast_days=1"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None, None
    daily = payload.get("daily") or {}
    moonrise = (daily.get("moonrise") or [None])[0]
    moonset = (daily.get("moonset") or [None])[0]
    return moonrise, moonset


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    lat = float(options.get("latitude") or 0.0)
    lon = float(options.get("longitude") or 0.0)
    label = (options.get("label") or "").strip()

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / f"moon_{lat:.2f}_{lon:.2f}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    now = datetime.now(UTC)
    age = _phase_age_days(now)
    fraction = age / _SYNODIC_DAYS  # 0..1 around the synodic cycle
    illum = _illumination(age)
    waxing = age < _SYNODIC_DAYS / 2
    name = _phase_name(age)

    moonrise, moonset = _fetch_moonrise(lat, lon)

    result = {
        "label": label,
        "lat": lat,
        "phase_name": name,
        "age_days": round(age, 1),
        "fraction": round(fraction, 4),  # client uses this to draw the disc
        "illumination": round(illum * 100, 1),  # %
        "waxing": waxing,
        "next_new": _next_phase(now, 0).isoformat(),
        "next_first_quarter": _next_phase(now, 0.25).isoformat(),
        "next_full": _next_phase(now, 0.5).isoformat(),
        "next_last_quarter": _next_phase(now, 0.75).isoformat(),
        "moonrise": moonrise,
        "moonset": moonset,
        "fetched_at": int(time.time()),
    }
    with contextlib.suppress(OSError):
        cache.write_text(json.dumps(result))
    return result
