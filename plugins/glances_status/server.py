"""glances_status — system stats from a Glances REST instance.

Hits Glances' v4 ``/api/4/all`` endpoint (with a v3 fallback so older
installs keep working), normalises the relevant bits into the shape
the client paints from:

    {
      "label": str,
      "time": "HH:MM",           # server wall-clock at fetch
      "hostname": str | None,
      "cpu": float | None,       # 0-100, total CPU utilisation
      "mem": float | None,       # 0-100, RAM utilisation
      "disk": {                  # rootfs (mount point "/") if present,
        "percent": float,        #   else the highest-usage filesystem
        "mnt_point": str,
      } | None,
      "load": float | None,      # 1-minute load average
      "uptime": int | None,      # seconds (parsed from string or numeric)
      "state": {"text": str, "tone": str},  # tone ∈ ok|warn|danger|offline
    }

Tone heuristic collapses three thresholds into one designed colour:
any of CPU > 90 / RAM > 90 / disk > 95 → danger; CPU > 70 / RAM > 75 /
disk > 85 → warn; otherwise ok. An unreachable Glances becomes the
``offline`` tone — same fall-through pattern as octoprint_status so a
powered-down box renders as a card, not an error.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime
from typing import Any

from app.plugin_http import fetch_json

HTTP_TIMEOUT_S = 6
USER_AGENT = "tesserae/0.1 (+glances_status)"

# Glances 4.x is the current API; some self-hosted installs are still on
# 3.x. Try the newer one first.
_API_PATHS: tuple[str, ...] = ("/api/4/all", "/api/3/all")

# Uptime strings look like "1 day, 4:23:11" or "4:23:11" or "23:11"
# depending on Glances version + locale. Parse defensively; numeric
# uptime (a float of seconds) also appears in some 4.x builds.
_UPTIME_RE = re.compile(r"^(?:(?P<days>\d+)\s*day[s]?,\s*)?(?:(?P<h>\d+):)?(?P<m>\d+):(?P<s>\d+)$")


def _f(value: Any) -> float | None:
    """Coerce to float; reject None / sentinels / non-numerics."""
    if value in (None, "", "null"):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n


def _uptime_seconds(value: Any) -> int | None:
    """Parse Glances' uptime into seconds. Accepts either a numeric
    (already seconds) or the ``"1 day, 4:23:11"`` shape Glances v3+
    emits by default."""
    if isinstance(value, int | float):
        return int(value) if value > 0 else None
    if not isinstance(value, str) or not value.strip():
        return None
    m = _UPTIME_RE.match(value.strip())
    if not m:
        return None
    days = int(m.group("days") or 0)
    h = int(m.group("h") or 0)
    mins = int(m.group("m") or 0)
    secs = int(m.group("s") or 0)
    return days * 86400 + h * 3600 + mins * 60 + secs


def _pick_disk(filesystems: Any) -> dict[str, Any] | None:
    """Pick the most useful filesystem from Glances' ``fs`` list.

    Prefer rootfs (``/``) — that's the metric most users actually care
    about. Fall back to the highest-usage filesystem so a Docker host
    where ``/`` is read-only still surfaces a meaningful number.
    """
    if not isinstance(filesystems, list) or not filesystems:
        return None
    rooted: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    for entry in filesystems:
        if not isinstance(entry, dict):
            continue
        if entry.get("mnt_point") == "/":
            rooted.append(entry)
        else:
            others.append(entry)
    if rooted:
        target = rooted[0]
    elif others:
        # Highest percent wins; pick the first if percents are absent.
        others.sort(key=lambda e: _f(e.get("percent")) or 0, reverse=True)
        target = others[0]
    else:
        return None
    pct = _f(target.get("percent"))
    if pct is None:
        return None
    return {"percent": round(pct, 1), "mnt_point": str(target.get("mnt_point") or "")}


def _tone(cpu: float | None, mem: float | None, disk_pct: float | None) -> str:
    """One-tone summary for the state pill. Any single dimension
    crossing its danger threshold wins. Order: danger > warn > ok."""
    danger = (
        (cpu is not None and cpu > 90)
        or (mem is not None and mem > 90)
        or (disk_pct is not None and disk_pct > 95)
    )
    if danger:
        return "danger"
    warn = (
        (cpu is not None and cpu > 70)
        or (mem is not None and mem > 75)
        or (disk_pct is not None and disk_pct > 85)
    )
    if warn:
        return "warn"
    return "ok"


def _tone_text(tone: str) -> str:
    return {"ok": "OK", "warn": "Warning", "danger": "Critical", "offline": "Offline"}.get(
        tone, "OK"
    )


def _fetch_all(base: str, headers: dict[str, str]) -> dict[str, Any] | None:
    """Try API v4 first, fall back to v3. Returns the decoded payload
    on the first endpoint that yields a dict, or None if neither
    answered cleanly. Network / decode failures bubble to the caller
    via the standard ``fetch_json`` exceptions; this only handles the
    happy path + the v4-not-available case."""
    for path in _API_PATHS:
        try:
            payload = fetch_json(base + path, headers=headers, timeout=HTTP_TIMEOUT_S)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del ctx
    base = str(settings.get("base_url", "")).strip().rstrip("/")
    label = (options.get("label") or "").strip()
    now = datetime.now().strftime("%H:%M")

    if not base:
        return {"error": "Set the Glances URL in plugin settings."}

    headers: dict[str, str] = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    username = str(settings.get("username") or "").strip()
    password = str(settings.get("password_secret") or settings.get("password") or "").strip()
    if username:
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {token}"

    payload = _fetch_all(base, headers)
    if payload is None:
        # Unreachable / non-JSON / 401 — surface as an offline card,
        # not an error shell. The user gets visual continuity on the
        # panel when the host briefly drops; persistent outage is
        # obvious from the Offline pill.
        return {
            "label": label or "Server",
            "time": now,
            "hostname": None,
            "cpu": None,
            "mem": None,
            "disk": None,
            "load": None,
            "uptime": None,
            "state": {"text": _tone_text("offline"), "tone": "offline"},
        }

    cpu = _f((payload.get("cpu") or {}).get("total"))
    mem = _f((payload.get("mem") or {}).get("percent"))
    disk = _pick_disk(payload.get("fs"))
    load = _f((payload.get("load") or {}).get("min1"))
    uptime = _uptime_seconds(payload.get("uptime"))
    hostname = str((payload.get("system") or {}).get("hostname") or "") or None

    disk_pct = disk["percent"] if disk else None
    tone = _tone(cpu, mem, disk_pct)

    return {
        "label": label or hostname or "Server",
        "time": now,
        "hostname": hostname,
        "cpu": cpu,
        "mem": mem,
        "disk": disk,
        "load": load,
        "uptime": uptime,
        "state": {"text": _tone_text(tone), "tone": tone},
    }
