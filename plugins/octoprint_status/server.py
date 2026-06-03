"""octoprint_status — live print monitor for an OctoPrint instance.

Pulls two endpoints from the OctoPrint REST API (auth via ``X-Api-Key``):

    GET /api/printer?exclude=sd   →  temperatures + state flags
    GET /api/job                  →  current job file, progress, times

and normalises them into the shape the four client variants paint from:

    {
      "label": str,
      "time": "HH:MM",                       # server wall-clock at fetch
      "state": {"text": str, "tone": str},   # tone ∈ printing | paused |
                                             #   complete | error | offline | idle
      "job": {                               # None when nothing is loaded
        "name": str,
        "completion": float|None,            # 0-100 %
        "elapsed": int|None,                 # seconds printed
        "remaining": int|None,               # seconds left (OctoPrint estimate)
        "eta": "HH:MM"|None                  # now + remaining
      } | None,
      "temps": {                             # actual / target °C, None if absent
        "tool": {"actual": float|None, "target": float|None},
        "bed":  {"actual": float|None, "target": float|None}
      }
    }

A printer that is powered down or has no serial link makes OctoPrint
answer ``/api/printer`` with 409 — that is a normal *offline* state, not
an error, so we surface ``tone: "offline"`` and still render the card.
Only genuine configuration problems (missing URL / key) return
``{"error": ...}`` for the client's error shell.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.plugin_http import fetch_json

HTTP_TIMEOUT_S = 8
USER_AGENT = "tesserae/0.1 (+octoprint_status)"


def _f(value: Any) -> float | None:
    """Coerce a temperature/percentage to float, dropping HA-style
    sentinels and non-numerics to ``None`` so the client renders an
    em-dash rather than a phantom 0."""
    if value in (None, "", "null"):
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _secs(value: Any) -> int | None:
    if value in (None, "", "null"):
        return None
    try:
        n = round(float(value))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _eta(remaining: int | None) -> str | None:
    """Wall-clock time the print is projected to finish. Local time —
    server and panel share a TZ on a single-household install."""
    if not remaining or remaining <= 0:
        return None
    return (datetime.now() + timedelta(seconds=remaining)).strftime("%H:%M")


def _tone(flags: dict[str, Any], completion: float | None) -> str:
    """Collapse OctoPrint's state-flag matrix to one design tone. Order
    matters: error wins over everything, then the active print states,
    then 'finished' (operational + 100%), then plain ready."""
    if not flags:
        return "idle"
    if flags.get("error") or flags.get("closedOrError"):
        return "error"
    if flags.get("printing") or flags.get("resuming"):
        return "printing"
    if flags.get("paused") or flags.get("pausing"):
        return "paused"
    if flags.get("operational") and completion is not None and completion >= 100:
        return "complete"
    return "idle"


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del ctx
    base = str(settings.get("base_url", "")).strip().rstrip("/")
    # secret:true stores under "<name>_secret"; accept the bare name too
    # so a plaintext override in settings.json still works.
    key = str(settings.get("api_key_secret") or settings.get("api_key") or "").strip()
    label = options.get("label") or "OctoPrint"
    now = datetime.now().strftime("%H:%M")

    if not base:
        return {"error": "Set the OctoPrint URL in plugin settings."}
    if not key:
        return {"error": "Set the OctoPrint API key in plugin settings."}

    headers = {"X-Api-Key": key, "User-Agent": USER_AGENT}

    # --- printer state + temperatures -------------------------------
    # 409 (printer not operational) and connection errors both mean the
    # machine is offline from OctoPrint's POV — render the offline card.
    reachable = True
    printer: dict[str, Any] = {}
    try:
        printer = (
            fetch_json(base + "/api/printer?exclude=sd", headers=headers, timeout=HTTP_TIMEOUT_S)
            or {}
        )
    except Exception:
        reachable = False

    temps_raw = (printer.get("temperature") or {}) if isinstance(printer, dict) else {}
    tool = temps_raw.get("tool0") or {}
    bed = temps_raw.get("bed") or {}
    temps = {
        "tool": {"actual": _f(tool.get("actual")), "target": _f(tool.get("target"))},
        "bed": {"actual": _f(bed.get("actual")), "target": _f(bed.get("target"))},
    }

    # --- current job ------------------------------------------------
    job_raw: dict[str, Any] = {}
    if reachable:
        try:
            job_raw = fetch_json(base + "/api/job", headers=headers, timeout=HTTP_TIMEOUT_S) or {}
        except Exception:
            job_raw = {}

    progress = job_raw.get("progress") or {}
    job_meta = job_raw.get("job") or {}
    file_meta = job_meta.get("file") or {}
    completion = _f(progress.get("completion"))
    remaining = _secs(progress.get("printTimeLeft"))
    name = file_meta.get("display") or file_meta.get("name")

    job: dict[str, Any] | None = None
    if name:
        job = {
            "name": str(name),
            "completion": completion,
            "elapsed": _secs(progress.get("printTime")),
            "remaining": remaining,
            "eta": _eta(remaining),
        }

    # --- state tone -------------------------------------------------
    if not reachable:
        state = {"text": "Offline", "tone": "offline"}
    else:
        st = printer.get("state") or {}
        flags = st.get("flags") or {}
        tone = _tone(flags, completion)
        state = {"text": str(st.get("text") or job_raw.get("state") or "Ready"), "tone": tone}

    return {
        "label": label,
        "time": now,
        "state": state,
        "job": job,
        "temps": temps,
    }
