"""Per-device telemetry derived from heartbeats.

The transport's MQTT heartbeat callback writes raw status (battery,
rssi, ip) into ``app.config["DEVICE_STATUS_CACHE"]`` and stops there.
This module owns the *derived* telemetry that the smart-sync scheduler
will later consult: when each device last woke, how long it sleeps,
when it's predicted to wake next, and a confidence counter that says
how much to trust the prediction.

Persistence is one JSON file under ``data/core/device_telemetry.json``
keyed by device id. Tiny — six fields per device — but persisted so a
server restart doesn't reset the confidence counter and force every
device back through the warm-up window.

Step 1 of the smart-sync feature (issue #10):
- Track + persist telemetry on every heartbeat.
- Compute ``predicted_next_wake_at`` and the confidence counter.
- DO NOT act on the prediction yet; the scheduler hook is step 2.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Wake within this many seconds of the prediction counts as on-time;
# anything outside resets the confidence counter. ESP32 deep-sleep
# wake jitter is typically < 5s on a healthy panel, so 60s is generous
# and tolerates network-level latency on the heartbeat round-trip.
ON_TIME_TOLERANCE_S: float = 60.0

# Above this confidence value the scheduler (step 2) will trust the
# prediction enough to JIT-render at the predicted wake time. Below it,
# fall back to the page's fixed cadence.
CONFIDENCE_TRUSTED_AT: int = 3

# Cap confidence so a long-running device doesn't sit on a huge value
# that takes many missed wakes to drain. Cap small enough that a real
# fault (sleep-cycle changed firmware-side) flushes the trust in a
# couple of missed predictions.
CONFIDENCE_MAX: int = 10

# Defensive sanity check when the firmware publishes both
# ``sleep_until`` (absolute wake timestamp) and ``next_sleep_s``
# (relative seconds until sleep). They should always agree, since
# they come from the same firmware. When they DON'T agree, the
# usual cause is a stale clock at the moment ``sleep_until`` was
# computed (e.g. NTP hadn't synced when the firmware grabbed
# ``time(nullptr)``) or ``sleep_until`` being a leftover value from
# the previous wake cycle. In that case ``next_sleep_s`` is the
# more trustworthy field, it's a duration so it can't carry clock
# skew. The threshold is generous enough to accept normal jitter
# (wifi connect time, MQTT publish latency) but small enough that a
# really bad ``sleep_until`` value is caught and ignored.
SLEEP_UNTIL_VS_NEXT_SLEEP_MISMATCH_S: float = 30.0

# Some firmwares send two heartbeats per wake cycle, e.g. one on connect
# (battery + rssi for the dashboard) and one just before deep-sleep
# (with the sleep_until timing). Without debouncing, the second
# heartbeat looks like a wake event that arrived ~0s after the first,
# which sets offset ≈ -sleep_cycle and resets confidence every cycle.
# Heartbeats arriving within this window of the previous one are
# treated as the same wake event: the entry's timestamps + interval
# are refreshed (the second beat usually carries more accurate sleep
# info) but the confidence counter doesn't move.
WAKE_DEBOUNCE_S: float = 10.0


@dataclass
class DeviceTelemetry:
    """Per-device derived state used by the smart-sync scheduler.

    Every field is optional / has a safe default so a brand-new device
    starts with ``confidence = 0`` (not trusted) and accumulates state
    as heartbeats arrive."""

    device_id: str
    last_heartbeat_at: float | None = None
    last_sleep_interval_s: int | None = None
    predicted_next_wake_at: float | None = None
    consecutive_on_time_wakes: int = 0
    last_wake_offset_s: int | None = None  # actual - predicted, signed

    @property
    def is_trusted(self) -> bool:
        return self.consecutive_on_time_wakes >= CONFIDENCE_TRUSTED_AT

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _State:
    devices: dict[str, DeviceTelemetry] = field(default_factory=dict)


class TelemetryStore:
    """Thread-safe persisted store of per-device telemetry.

    The ``record_heartbeat`` method is the only mutating entry point;
    callers pass the device id + the parsed heartbeat dict and an
    optional ``configured_sleep_s`` fallback (from the device kind's
    saved config, used when the firmware doesn't publish
    ``sleep_until`` / ``next_sleep_s`` itself).

    Reads (``get``, ``all``) are lock-free copies so the scheduler can
    consult predictions without contending with heartbeat writes.
    """

    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self._lock = threading.Lock()
        self._state = _State()
        self._load()

    # -- public read API -------------------------------------------------

    def get(self, device_id: str) -> DeviceTelemetry | None:
        with self._lock:
            entry = self._state.devices.get(device_id)
            if entry is None:
                return None
            return DeviceTelemetry(**entry.as_dict())

    def all(self) -> dict[str, DeviceTelemetry]:
        with self._lock:
            return {
                device_id: DeviceTelemetry(**entry.as_dict())
                for device_id, entry in self._state.devices.items()
            }

    # -- mutating entry point --------------------------------------------

    def record_heartbeat(
        self,
        device_id: str,
        *,
        received_at: float,
        parsed: dict[str, Any],
        configured_sleep_s: int | None,
    ) -> DeviceTelemetry:
        """Update the telemetry for ``device_id`` after a new heartbeat.

        Reads three optional firmware-published fields in priority order:

        - ``sleep_until``: unix timestamp the device says it will wake.
          Most accurate; bypasses any clock-skew math on our side.
        - ``next_sleep_s``: seconds of sleep the device is about to
          enter. We add to ``received_at`` to derive the wake time.
        - Neither present → fall back to ``configured_sleep_s`` (the
          per-kind config the user set in the admin UI).

        Returns the updated entry."""
        sleep_until = _coerce_float(parsed.get("sleep_until"))
        next_sleep_s = _coerce_int(parsed.get("next_sleep_s"))

        with self._lock:
            prev = self._state.devices.get(device_id) or DeviceTelemetry(device_id=device_id)

            # Debounce: heartbeats arriving within WAKE_DEBOUNCE_S of
            # the previous one are treated as belonging to the same
            # wake cycle (firmwares that send a "connected" beat and
            # a "going to sleep" beat). Refresh timestamps + interval
            # from the newer beat but leave confidence + offset alone
            # so a double-fire doesn't reset trust.
            is_same_wake = (
                prev.last_heartbeat_at is not None
                and (received_at - prev.last_heartbeat_at) < WAKE_DEBOUNCE_S
            )

            # Confidence update: compare this heartbeat's actual arrival
            # against the previous prediction. A trusted prediction +
            # on-time arrival bumps confidence; a miss resets it.
            # Skipped on a same-wake debounce; the second beat carries
            # info about the upcoming sleep, not a new wake.
            consecutive = prev.consecutive_on_time_wakes
            offset_s: int | None = prev.last_wake_offset_s
            if not is_same_wake and prev.predicted_next_wake_at is not None:
                offset_s = round(received_at - prev.predicted_next_wake_at)
                if abs(offset_s) <= ON_TIME_TOLERANCE_S:
                    consecutive = min(consecutive + 1, CONFIDENCE_MAX)
                else:
                    if consecutive > 0:
                        logger.info(
                            "telemetry: %s wake missed prediction by %ds; "
                            "resetting confidence (was %d)",
                            device_id,
                            offset_s,
                            consecutive,
                        )
                    consecutive = 0

            # Resolve the sleep interval that will be used for the next
            # prediction. Firmware-published value wins; configured
            # interval is the fallback when the firmware is silent.
            #
            # Defensive: when both ``sleep_until`` AND ``next_sleep_s``
            # are present, they're meant to encode the same wake event
            # in two forms. If they disagree by more than
            # ``SLEEP_UNTIL_VS_NEXT_SLEEP_MISMATCH_S``, the absolute
            # timestamp is almost certainly carrying clock skew (NTP
            # not synced when computed, or a stale value from a
            # previous wake) and the relative seconds is the
            # trustworthy field. Drop ``sleep_until`` in that case and
            # fall through to the ``next_sleep_s`` branch.
            if (
                sleep_until is not None
                and next_sleep_s is not None
                and abs((sleep_until - received_at) - next_sleep_s)
                > SLEEP_UNTIL_VS_NEXT_SLEEP_MISMATCH_S
            ):
                logger.warning(
                    "telemetry: %s sleep_until disagrees with next_sleep_s "
                    "(sleep_until=>%ds vs next_sleep_s=%ds); using next_sleep_s. "
                    "Likely an unsynced clock at sleep_until computation in firmware.",
                    device_id,
                    round(sleep_until - received_at),
                    next_sleep_s,
                )
                sleep_until = None

            effective_interval: int | None
            predicted_wake: float | None
            if sleep_until is not None:
                # Wake time is published directly; derive an effective
                # interval for storage so the admin UI can show it.
                effective_interval = max(0, round(sleep_until - received_at))
                predicted_wake = sleep_until
            elif next_sleep_s is not None:
                effective_interval = next_sleep_s
                predicted_wake = received_at + next_sleep_s
            elif configured_sleep_s is not None:
                effective_interval = configured_sleep_s
                predicted_wake = received_at + configured_sleep_s
            else:
                # No clue about sleep timing; keep the previous interval
                # (if any) for display, drop the prediction.
                effective_interval = prev.last_sleep_interval_s
                predicted_wake = None

            entry = DeviceTelemetry(
                device_id=device_id,
                last_heartbeat_at=received_at,
                last_sleep_interval_s=effective_interval,
                predicted_next_wake_at=predicted_wake,
                consecutive_on_time_wakes=consecutive,
                last_wake_offset_s=offset_s,
            )
            self._state.devices[device_id] = entry
            self._flush_locked()
            return DeviceTelemetry(**entry.as_dict())

    def forget(self, device_id: str) -> None:
        """Drop a device's telemetry. Called when the user deletes
        the device instance from the admin UI so a future device with
        the same id (rare but possible) doesn't inherit stale state."""
        with self._lock:
            if self._state.devices.pop(device_id, None) is not None:
                self._flush_locked()

    # -- persistence -----------------------------------------------------

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as err:
            logger.warning("telemetry: failed to load %s: %s", self._state_path, err)
            return
        if not isinstance(data, dict):
            logger.warning(
                "telemetry: expected object at top of %s, got %s",
                self._state_path,
                type(data).__name__,
            )
            return
        devices_raw = data.get("devices")
        if not isinstance(devices_raw, dict):
            return
        for device_id, raw_entry in devices_raw.items():
            if not isinstance(raw_entry, dict):
                continue
            try:
                entry = DeviceTelemetry(
                    device_id=str(device_id),
                    last_heartbeat_at=_coerce_float(raw_entry.get("last_heartbeat_at")),
                    last_sleep_interval_s=_coerce_int(raw_entry.get("last_sleep_interval_s")),
                    predicted_next_wake_at=_coerce_float(raw_entry.get("predicted_next_wake_at")),
                    consecutive_on_time_wakes=int(raw_entry.get("consecutive_on_time_wakes") or 0),
                    last_wake_offset_s=_coerce_int(raw_entry.get("last_wake_offset_s")),
                )
            except (TypeError, ValueError) as err:
                logger.warning(
                    "telemetry: skipping malformed entry for %s: %s",
                    device_id,
                    err,
                )
                continue
            self._state.devices[entry.device_id] = entry

    def _flush_locked(self) -> None:
        """Caller holds ``self._lock``. Writes to a ``.part`` then
        atomic-renames so a crash mid-write never leaves a corrupt
        file."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "devices": {
                device_id: entry.as_dict() for device_id, entry in self._state.devices.items()
            },
        }
        tmp = self._state_path.with_suffix(self._state_path.suffix + ".part")
        try:
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self._state_path)
        except OSError as err:
            logger.warning("telemetry: failed to persist %s: %s", self._state_path, err)


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
