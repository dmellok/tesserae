"""Device-reported OTA state (contract: ``docs/ota/contract.md``, "State reporting").

A device that speaks OTA extends the ``ota`` capability object in its heartbeat
body with report fields naming where it is in the update lifecycle. The server
records the latest report on the device's live status (a Devices-card chip) and
event-logs the transitions, so a canary's ``confirmed`` / ``rolled_back`` is
observable without reading serial logs. The report is advisory: the device's own
first-boot checks remain the acceptance gate.
"""

from __future__ import annotations

import json
from typing import Any

# Lifecycle phases (contract "Phases" table). ``idle`` is the steady state and
# carries no report, so it's deliberately absent here: an idle / capability-only
# heartbeat leaves the last real report standing rather than clearing the chip.
IN_PROGRESS_PHASES = ("downloading", "validating", "pending_confirm")
TERMINAL_PHASES = ("confirmed", "rejected", "failed", "rolled_back")
KNOWN_PHASES = IN_PROGRESS_PHASES + TERMINAL_PHASES

# Phases that represent a failed outcome, logged at error severity.
FAILURE_PHASES = ("failed", "rolled_back")

# Report fields whose change marks a new lifecycle event worth logging.
_IDENTITY_FIELDS = ("phase", "reason", "target_fw", "attempt_id")

_DETAIL_CAP = 200


def parse_report(payload: bytes | str | dict[str, Any]) -> dict[str, Any] | None:
    """The OTA report from a heartbeat body, or None.

    Returns None for a body with no ``ota`` object, a capability-only
    ``{"schema": 1}``, an ``idle`` phase, or an unrecognised phase. String
    fields are stripped; ``detail`` is capped. The returned dict always has a
    known non-idle ``phase``."""
    if isinstance(payload, (bytes, bytearray, str)):
        try:
            body = json.loads(payload)
        except (ValueError, TypeError):
            return None
    else:
        body = payload
    if not isinstance(body, dict):
        return None
    ota = body.get("ota")
    if not isinstance(ota, dict):
        return None
    phase = ota.get("phase")
    if not isinstance(phase, str) or phase.strip() not in KNOWN_PHASES:
        return None
    report: dict[str, Any] = {"phase": phase.strip()}
    for key in ("reason", "target_fw", "attempt_id"):
        val = ota.get(key)
        if isinstance(val, str) and val.strip():
            report[key] = val.strip()
    detail = ota.get("detail")
    if isinstance(detail, str) and detail.strip():
        report["detail"] = detail.strip()[:_DETAIL_CAP]
    return report


def report_changed(prev: dict[str, Any] | None, cur: dict[str, Any]) -> bool:
    """True when ``cur`` is a new lifecycle event vs the last stored report,
    i.e. any identity field (phase / reason / target_fw / attempt_id) differs.
    A device re-sending the same terminal report every heartbeat is unchanged,
    so it logs once (``received_at`` is not an identity field)."""
    if prev is None:
        return True
    return any(prev.get(k) != cur.get(k) for k in _IDENTITY_FIELDS)


def is_failure(report: dict[str, Any]) -> bool:
    """Whether the report names a failed outcome (event-logged at error)."""
    return report.get("phase") in FAILURE_PHASES
