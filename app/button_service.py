"""Button dispatch service.

Ties together the pieces the REST handler shouldn't have to know about:

* resolve the device's active rotation (highest-priority enabled
  rotation whose ``device_ids`` includes this device);
* load per-device rotation state (or default it);
* look up the button in the device's ``button_map`` with fallback to
  the app-global ``button_map`` and finally the hardcoded default
  (``left`` -> rotate_prev, ``right`` -> rotate_next,
  ``refresh`` -> refresh);
* dedup incoming events against the persisted
  ``last_button_event_id`` (or a same-button-within-N-seconds
  fallback when the firmware doesn't send an id);
* dispatch through ``button_actions.dispatch`` to compute a new step
  index / target page / refresh flag;
* update persistent state (new step, override_until, dedup
  fingerprints);
* trigger a push on the resolved page so the returned frame reflects
  the new state on this same wake.

Returns a ``ButtonHandleResult`` the REST handler can encode into the
``rotation`` block on ``/frame`` and ``/status`` responses.

mypy --strict applies to this module.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.button_actions import (
    DEFAULT_BUTTON_MAP,
    DEFAULT_DEBOUNCE_SECONDS,
    ActionContext,
    ActionResult,
    ButtonActionError,
    dispatch,
    parse_action_spec,
)
from app.push import PushManager, PushResult
from app.state.device_rotation_state_model import DeviceRotationState
from app.state.device_rotation_state_store import DeviceRotationStateStore
from app.state.event_log import EventLog
from app.state.page_store import PageStore
from app.state.rotation_model import Rotation
from app.state.rotation_store import RotationStore
from app.state.settings_store import SettingsStore

log = logging.getLogger(__name__)


# Cap on how long a manual override sticks when we can't compute the
# rotation's next daily anchor (no rotation bound, or a degenerate
# anchor value). One hour is long enough to prevent a "scheduler yanks
# it back" surprise but short enough that an abandoned device doesn't
# get stuck forever. Overridable by ``settings.app.button_hold_seconds``.
_FALLBACK_HOLD_SECONDS = 3600

# Webhook fires happen in a daemon thread so the /frame response
# returns immediately (battery devices care). This is fire-and-forget:
# failures are logged and dropped, no retries. Timeout is short enough
# that a stuck external endpoint doesn't strand threads. Overridable
# via ``settings.app.button_webhook_timeout_s``.
_WEBHOOK_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class ButtonHandleResult:
    """What the REST handler needs to encode into the response.

    Attributes carry both the *decision* (dedup vs unmapped vs
    dispatched) and the *state* that dispatch resolved to. When the
    device is bound to no rotation at all, ``rotation_id`` is None
    and downstream serialisation omits the whole rotation envelope.
    """

    device_id: str
    dedup: bool  # True if the event was a retry / debounced no-op
    unmapped: bool  # True if the button name wasn't in any button_map
    action_spec: str | None  # resolved spec from button_map
    action_description: str | None
    rotation_id: str | None
    step_index: int
    step_page_id: str | None
    step_count: int
    manual_override: bool
    override_until: datetime | None
    pushed_page_id: str | None
    push_result: PushResult | None

    def to_envelope(self) -> dict[str, str | int | bool | None]:
        """Serialisable subset for the ``rotation`` block on ``/frame``
        and ``/status`` responses. Omits internal fields like
        ``push_result`` which are useful for logging but not the
        firmware."""
        return {
            "rotation_id": self.rotation_id,
            "step_index": self.step_index if self.rotation_id else None,
            "step_page_id": self.step_page_id,
            "step_count": self.step_count if self.rotation_id else None,
            "manual_override": self.manual_override,
            "override_until": (self.override_until.isoformat() if self.override_until else None),
        }


class ButtonService:
    def __init__(
        self,
        *,
        rotation_store: RotationStore,
        state_store: DeviceRotationStateStore,
        settings_store: SettingsStore,
        page_store: PageStore,
        push_manager: PushManager | Callable[[], PushManager | None] | None,
        event_log: EventLog | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._rotations = rotation_store
        self._state = state_store
        self._settings = settings_store
        self._pages = page_store
        # Accept either a live PushManager (or any duck-type with a
        # ``.push`` method, useful for tests) or a zero-arg callable
        # that returns one on each call (production, where the
        # transport rebuild swaps the manager and a held reference
        # would go stale). Normalise to a getter.
        resolved: PushManager | None
        if push_manager is None:
            resolved = None
            self._push_getter: Callable[[], PushManager | None] = lambda: resolved
        elif hasattr(push_manager, "push"):
            resolved = push_manager  # type: ignore[assignment]
            self._push_getter = lambda: resolved
        else:
            self._push_getter = push_manager
        self._event_log = event_log
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    # ---- public API --------------------------------------------------

    def snapshot(self, device_id: str) -> ButtonHandleResult:
        """Read-only view of the current rotation position for a
        device. Used to attach the rotation envelope to a ``/frame``
        response when no button was pressed on this wake."""
        rotation = self._resolve_rotation(device_id)
        state = self._state.get(device_id)
        step_index, manual = self._effective_step_index(rotation, state)
        step_page_id = rotation.steps[step_index].page_id if rotation is not None else None
        return ButtonHandleResult(
            device_id=device_id,
            dedup=False,
            unmapped=False,
            action_spec=None,
            action_description=None,
            rotation_id=rotation.id if rotation is not None else None,
            step_index=step_index,
            step_page_id=step_page_id,
            step_count=len(rotation.steps) if rotation is not None else 0,
            manual_override=manual,
            override_until=state.override_until if (state is not None and manual) else None,
            pushed_page_id=None,
            push_result=None,
        )

    def handle_button(
        self,
        *,
        device_id: str,
        button: str,
        event_id: int | None,
    ) -> ButtonHandleResult:
        """Full path: resolve, dedup, dispatch, persist, push."""
        now = self._clock()
        rotation = self._resolve_rotation(device_id)
        state = self._state.get(device_id) or DeviceRotationState(device_id=device_id)

        # Dedup: monotonic id wins; fall back to same-button-within-N.
        if self._is_duplicate(state, button=button, event_id=event_id, now=now):
            log.info(
                "button dedup: device=%s button=%s event_id=%s (last=%s)",
                device_id,
                button,
                event_id,
                state.last_button_event_id,
            )
            step_index, manual = self._effective_step_index(rotation, state)
            step_page_id = rotation.steps[step_index].page_id if rotation is not None else None
            result_dedup = ButtonHandleResult(
                device_id=device_id,
                dedup=True,
                unmapped=False,
                action_spec=None,
                action_description="duplicate; ignored",
                rotation_id=rotation.id if rotation is not None else None,
                step_index=step_index,
                step_page_id=step_page_id,
                step_count=len(rotation.steps) if rotation is not None else 0,
                manual_override=manual,
                override_until=state.override_until if manual else None,
                pushed_page_id=None,
                push_result=None,
            )
            self._emit_history_row(
                result=result_dedup, button=button, event_id=event_id, status="deduped"
            )
            return result_dedup

        # Resolve action spec through per-device -> global -> default.
        spec = self._resolve_action_spec(device_id, button)

        current_step_index, _manual = self._effective_step_index(rotation, state)
        ctx = ActionContext(
            device_id=device_id,
            current_step_index=current_step_index,
            rotation_step_count=len(rotation.steps) if rotation is not None else 0,
            rotation_id=rotation.id if rotation is not None else None,
            known_page_ids=frozenset(p.id for p in self._pages.list()),
        )

        # Unmapped button -> log + no-op, but still record the event
        # for dedup so a retry doesn't accidentally advance later.
        if spec is None:
            log.warning(
                "button unmapped: device=%s button=%s (no per-device or global map)",
                device_id,
                button,
            )
            new_state = state.model_copy(
                update={
                    "last_button": button,
                    "last_button_event_id": event_id,
                    "last_button_at": now,
                }
            )
            self._state.upsert(new_state)
            step_page_id = (
                rotation.steps[current_step_index].page_id if rotation is not None else None
            )
            result_unmapped = ButtonHandleResult(
                device_id=device_id,
                dedup=False,
                unmapped=True,
                action_spec=None,
                action_description=f"unmapped button {button!r}",
                rotation_id=rotation.id if rotation is not None else None,
                step_index=current_step_index,
                step_page_id=step_page_id,
                step_count=len(rotation.steps) if rotation is not None else 0,
                manual_override=_manual,
                override_until=state.override_until if _manual else None,
                pushed_page_id=None,
                push_result=None,
            )
            self._emit_history_row(
                result=result_unmapped, button=button, event_id=event_id, status="unmapped"
            )
            return result_unmapped

        # Dispatch. Malformed spec / unknown action -> log + no-op.
        try:
            result: ActionResult = dispatch(spec, ctx)
        except ButtonActionError as exc:
            log.warning(
                "button action error: device=%s button=%s spec=%s: %s",
                device_id,
                button,
                spec,
                exc,
            )
            new_state = state.model_copy(
                update={
                    "last_button": button,
                    "last_button_event_id": event_id,
                    "last_button_at": now,
                }
            )
            self._state.upsert(new_state)
            step_page_id = (
                rotation.steps[current_step_index].page_id if rotation is not None else None
            )
            result_error = ButtonHandleResult(
                device_id=device_id,
                dedup=False,
                unmapped=True,
                action_spec=spec,
                action_description=f"error: {exc}",
                rotation_id=rotation.id if rotation is not None else None,
                step_index=current_step_index,
                step_page_id=step_page_id,
                step_count=len(rotation.steps) if rotation is not None else 0,
                manual_override=_manual,
                override_until=state.override_until if _manual else None,
                pushed_page_id=None,
                push_result=None,
            )
            self._emit_history_row(
                result=result_error,
                button=button,
                event_id=event_id,
                status="error",
                error=str(exc),
            )
            return result_error

        # Compute override_until only if the action actually changed
        # the rotation position (or was a rotation-manipulating action
        # in general). page:<id> shortcuts don't set an override; the
        # scheduler resumes on the next timer wake.
        rotation_changed = rotation is not None and result.new_step_index != current_step_index
        wants_hold = (
            rotation is not None
            and result.target_page_id is None
            and (rotation_changed or result.force_refresh)
        )
        override_until = (
            self._compute_hold_until(rotation, now) if wants_hold else state.override_until
        )

        new_state = state.model_copy(
            update={
                "rotation_id": rotation.id if rotation is not None else None,
                "step_index": result.new_step_index if rotation is not None else state.step_index,
                "override_until": override_until,
                "last_button": button,
                "last_button_event_id": event_id,
                "last_button_at": now,
            }
        )
        self._state.upsert(new_state)

        # Push the resolved page so the returned frame reflects the
        # new state on this same wake. If we can't push (missing
        # PushManager, quiet hours, lock contention), leave the state
        # updated so the next timer wake catches up.
        pushed_page_id: str | None = None
        push_result: PushResult | None = None
        pusher = self._push_getter()
        if pusher is not None:
            if result.target_page_id is not None:
                pushed_page_id = result.target_page_id
            elif rotation is not None and (rotation_changed or result.force_refresh):
                pushed_page_id = rotation.steps[result.new_step_index].page_id
            if pushed_page_id is not None:
                try:
                    push_result = pusher.push(
                        pushed_page_id,
                        device_ids={device_id},
                        respect_quiet_hours=False,
                        source="button",
                    )
                except Exception:
                    log.exception(
                        "button push failed: device=%s page=%s spec=%s",
                        device_id,
                        pushed_page_id,
                        spec,
                    )

        # webhook:<url> action: fire a POST asynchronously so /frame
        # doesn't block on external endpoints. ``dispatch`` has already
        # validated the URL shape (http(s), non-empty), so we just
        # extract the arg and hand it off to the daemon thread.
        try:
            action_name, action_arg = parse_action_spec(spec)
        except ButtonActionError:
            action_name, action_arg = ("", None)
        if action_name == "webhook" and action_arg:
            payload: dict[str, object] = {
                "device_id": device_id,
                "button": button,
                "button_event_id": event_id,
                "action_spec": spec,
                "timestamp": now.isoformat(),
                "rotation_id": rotation.id if rotation is not None else None,
                "step_index": new_state.step_index if rotation is not None else None,
                "step_page_id": (
                    rotation.steps[new_state.step_index].page_id if rotation is not None else None
                ),
            }
            self._fire_webhook_async(action_arg, payload)

        step_page_id = (
            rotation.steps[result.new_step_index].page_id if rotation is not None else None
        )
        log.info(
            "button event: device=%s button=%s spec=%s -> step=%d page=%s%s",
            device_id,
            button,
            spec,
            result.new_step_index if rotation is not None else -1,
            step_page_id,
            f" (target={result.target_page_id})" if result.target_page_id else "",
        )
        result_ok = ButtonHandleResult(
            device_id=device_id,
            dedup=False,
            unmapped=False,
            action_spec=spec,
            action_description=result.description,
            rotation_id=rotation.id if rotation is not None else None,
            step_index=new_state.step_index,
            step_page_id=step_page_id,
            step_count=len(rotation.steps) if rotation is not None else 0,
            manual_override=override_until is not None and override_until > now,
            override_until=override_until,
            pushed_page_id=pushed_page_id,
            push_result=push_result,
        )
        # Status distinguishes the outcomes admins care about: an actual
        # push, a fire-and-forget webhook (no push), or a rotate/refresh
        # that resolved to "nothing to do" (rare edge case, but worth
        # showing so the user sees the press wasn't lost).
        if action_name == "webhook":
            status = "webhook_dispatched"
        elif pushed_page_id is not None:
            status = "dispatched"
        else:
            status = "noop"
        self._emit_history_row(result=result_ok, button=button, event_id=event_id, status=status)
        return result_ok

    # ---- internals ---------------------------------------------------

    def _emit_history_row(
        self,
        *,
        result: ButtonHandleResult,
        button: str,
        event_id: int | None,
        status: str,
        error: str | None = None,
    ) -> None:
        """Append a row to the event log so the History page has the
        button feed as a first-class surface. Non-fatal on absence:
        constructed without an event log (tests, offline paths) skips
        silently. The push that some actions trigger already logs its
        own row via ``PushManager``; this row is the "user pressed the
        button" event alongside that "server pushed the page" event,
        so a busy device produces two rows per state-changing wake.
        Non-push branches (dedup, unmapped, error, webhook, noop) get
        one row that captures the whole outcome.

        Uses ``type="push"`` so the existing ``/history`` route (which
        filters on that type) picks the row up without a schema
        change. ``digest`` is None so the row's Resend button stays
        disabled, and no thumbnail is rendered."""
        if self._event_log is None:
            return
        try:
            self._event_log.record(
                type="push",
                source="button",
                target=result.device_id,
                status=status,
                digest=None,
                error=error,
                duration_s=0.0,
                extra={
                    "button": button,
                    "button_event_id": event_id,
                    "action_spec": result.action_spec,
                    "action_description": result.action_description,
                    "rotation_id": result.rotation_id,
                    "step_index": result.step_index if result.rotation_id else None,
                    "step_page_id": result.step_page_id,
                    "pushed_page_id": result.pushed_page_id,
                    "manual_override": result.manual_override,
                },
            )
        except Exception:
            log.exception(
                "button event log write failed: device=%s button=%s status=%s",
                result.device_id,
                button,
                status,
            )

    def _resolve_rotation(self, device_id: str) -> Rotation | None:
        """Pick the highest-priority enabled rotation this device is
        bound to. Ties broken by earliest id lexicographically for
        determinism."""
        matches = [
            r for r in self._rotations.all() if r.enabled and device_id in r.device_ids and r.steps
        ]
        if not matches:
            return None
        matches.sort(key=lambda r: (-r.priority, r.id))
        return matches[0]

    def _effective_step_index(
        self,
        rotation: Rotation | None,
        state: DeviceRotationState | None,
    ) -> tuple[int, bool]:
        """Return ``(step_index, manual_override_active)``.

        The ``/frame`` handler uses this to decide whether the frame
        being served is the manual step or the time-based one. The
        button dispatcher uses it as the input to the action's
        ``current_step_index``.
        """
        if rotation is None or not rotation.steps:
            return 0, False
        if state is None:
            return 0, False
        if state.rotation_id != rotation.id:
            # Manual state points at a different rotation (rebinding,
            # deletion). Ignore the override; fall through to
            # time-based / index 0.
            return 0, False
        if state.override_until is None:
            return 0, False
        if self._clock() >= state.override_until:
            return 0, False
        # Clamp to the current rotation's step count in case the
        # rotation was edited and shrank since the override.
        idx = max(0, min(state.step_index, len(rotation.steps) - 1))
        return idx, True

    def _is_duplicate(
        self,
        state: DeviceRotationState,
        *,
        button: str,
        event_id: int | None,
        now: datetime,
    ) -> bool:
        # Preferred path: monotonic id from the firmware. Retries send
        # the same id; a duplicate is anything <= the last processed.
        if event_id is not None and state.last_button_event_id is not None:
            return event_id <= state.last_button_event_id
        # Fallback for firmwares that don't send an id: same button
        # within the configured window (default 3s).
        if state.last_button == button and state.last_button_at is not None:
            window = timedelta(seconds=self._debounce_seconds())
            if now - state.last_button_at <= window:
                return True
        return False

    def _debounce_seconds(self) -> float:
        try:
            value = self._settings.get_section("app").get("button_debounce_s")
        except Exception:
            value = None
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
        return DEFAULT_DEBOUNCE_SECONDS

    def _resolve_action_spec(self, device_id: str, button: str) -> str | None:
        """Per-device button_map, then app-global, then hardcoded
        default. Returns None if the button isn't mapped anywhere."""
        try:
            devices_section = self._settings.get_section("devices") or {}
            device_map = devices_section.get(device_id, {}).get("button_map")
            if isinstance(device_map, dict) and button in device_map:
                value = device_map[button]
                if isinstance(value, str) and value:
                    return value
        except Exception:
            pass
        try:
            global_map = self._settings.get_section("app").get("button_map")
            if isinstance(global_map, dict) and button in global_map:
                value = global_map[button]
                if isinstance(value, str) and value:
                    return value
        except Exception:
            pass
        default = DEFAULT_BUTTON_MAP.get(button)
        return default

    def _webhook_timeout_seconds(self) -> float:
        try:
            value = self._settings.get_section("app").get("button_webhook_timeout_s")
        except Exception:
            value = None
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return _WEBHOOK_TIMEOUT_SECONDS

    def _fire_webhook_async(self, url: str, payload: dict[str, object]) -> None:
        """Fire the POST in a daemon thread so /frame returns without
        waiting on the external endpoint. Failures are logged and
        dropped; there is no retry (the caller can rebind the button
        to something else if the endpoint is unhealthy)."""
        timeout = self._webhook_timeout_seconds()

        def _fire() -> None:
            try:
                body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "tesserae-button-webhook/1",
                    },
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    log.info(
                        "button webhook fired: url=%s status=%s device=%s button=%s",
                        url,
                        resp.status,
                        payload.get("device_id"),
                        payload.get("button"),
                    )
            except Exception as exc:
                log.warning(
                    "button webhook failed: url=%s device=%s button=%s error=%s",
                    url,
                    payload.get("device_id"),
                    payload.get("button"),
                    exc,
                )

        thread = threading.Thread(target=_fire, daemon=True, name="button-webhook")
        thread.start()

    def _compute_hold_until(self, rotation: Rotation | None, now: datetime) -> datetime:
        """When should the manual override expire?

        Default is the rotation's next daily anchor: pressing a button
        holds the display through the rest of the day, then the
        scheduler resumes at midnight (or whatever the rotation's
        ``anchor`` is). Rotations without a usable anchor and devices
        with no rotation bound fall back to a fixed window from
        settings (``app.button_hold_seconds``, default 3600s).
        """
        try:
            override = self._settings.get_section("app").get("button_hold_seconds")
        except Exception:
            override = None
        if isinstance(override, (int, float)) and override > 0:
            return now + timedelta(seconds=float(override))
        if rotation is None:
            return now + timedelta(seconds=_FALLBACK_HOLD_SECONDS)
        # Rotation anchors are local-time "HH:MM". Without threading a
        # tz through here, use UTC "next anchor" as a conservative
        # approximation: it's always <= 24h out. The scheduler already
        # snaps to the local anchor separately, so a slight offset in
        # the hold expiry just gives the user a hair more manual time
        # than requested, never less.
        try:
            hh, mm = (int(x) for x in rotation.anchor.split(":", 1))
        except Exception:
            return now + timedelta(seconds=_FALLBACK_HOLD_SECONDS)
        anchor_today = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if anchor_today <= now:
            anchor_today = anchor_today + timedelta(days=1)
        return anchor_today
