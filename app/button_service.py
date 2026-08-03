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
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.button_actions import (
    DEFAULT_BUTTON_MAP,
    DEFAULT_DEBOUNCE_SECONDS,
    ActionContext,
    ActionResult,
    ButtonActionError,
    dispatch,
    parse_action_spec,
)
from app.device_loader import DeviceRegistry
from app.push import PushManager, PushResult
from app.scheduler import RotationSource
from app.state.deck_model import Deck
from app.state.deck_nav_store import DeckNavStore
from app.state.deck_store import DeckStore
from app.state.device_rotation_state_model import DeviceRotationState
from app.state.device_rotation_state_store import DeviceRotationStateStore
from app.state.event_log import EventLog
from app.state.page_store import PageStore
from app.state.rotation_model import Rotation
from app.state.settings_store import SettingsStore
from app.touch_regions import (
    classify_stroke,
    hit_test,
    is_side_effecting,
    resolve_gesture_action,
    slide_declaration,
    slide_value,
    substitute_value,
)

log = logging.getLogger(__name__)


def _handles_swipe(region: dict[str, Any] | None, gesture: str) -> bool:
    """True when ``region`` declares an action for this swipe direction.
    Used by the swipe end-point fallback so a swipe is only re-homed onto a
    region that actually handles it (issue #49). Sliders aren't matched here
    (they're a press-on interaction, not a swipe target)."""
    return region is not None and resolve_gesture_action(region, gesture) is not None


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

# Home Assistant service calls fired from a touch action run
# synchronously (the wake's response must report dispatched vs failed),
# so keep the ceiling tight. Overridable via
# ``settings.app.touch_ha_timeout_s``.
_HA_TIMEOUT_SECONDS = 5.0

# Post-action reconcile debounce: after an HA action, the current page
# re-renders in the background so the panel catches up with the new
# state; a burst of taps coalesces into one render this many seconds
# after the last action. Patch-capable devices (overlay schema >= 2)
# get the short window (the reconcile lands as partial-refresh rects,
# so eagerness is cheap); everything else gets the longer one, because
# its reconcile is a full push -> full e-ink repaint. Overridable via
# ``settings.app.touch_patch_debounce_s`` /
# ``settings.app.touch_repush_debounce_s``.
_PATCH_DEBOUNCE_SECONDS = 0.4
_REPUSH_DEBOUNCE_SECONDS = 3.0


def _spec_label(spec: str | dict[str, Any] | None) -> str | None:
    """A loggable string form of an action spec (dict specs serialise)."""
    if spec is None:
        return None
    return spec if isinstance(spec, str) else json.dumps(spec, sort_keys=True)


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
    force_download: bool = False

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


@dataclass(frozen=True)
class TouchStroke:
    """A raw touch stroke as reported by the firmware, in the served
    frame's pixel space. A tap is a stroke whose end point equals (or
    sits within the tap radius of) its start point; clients that only
    track a single point send the same coordinates for both."""

    x0: int
    y0: int
    x1: int
    y1: int
    duration_ms: int | None = None

    def to_log(self) -> dict[str, int | None]:
        return {
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class TouchHandleResult:
    """Outcome of a touch stroke.

    ``outcome`` is one of ``deduped`` / ``no_frame`` / ``stale`` /
    ``no_target`` / ``blocked`` (guard-chain exits; ``blocked`` = a
    side-effecting action from raw markup, refused by the provenance
    gate) or ``dispatched`` / ``noop`` / ``webhook_dispatched`` /
    ``ha_dispatched`` / ``ha_failed`` / ``error`` (the action ran,
    mirroring the button statuses). ``gesture`` includes ``slide`` for
    slider regions, whose 0-100 ``value`` is what the stroke resolved
    to. ``base`` carries the rotation envelope the REST layer
    serialises, exactly as a button press would."""

    outcome: str
    gesture: str | None
    base: ButtonHandleResult
    magnitude: int = 0
    action_spec: str | None = None
    value: int | None = None


class ButtonService:
    def __init__(
        self,
        *,
        rotation_store: RotationSource,
        state_store: DeviceRotationStateStore,
        settings_store: SettingsStore,
        page_store: PageStore,
        push_manager: PushManager | Callable[[], PushManager | None] | None,
        event_log: EventLog | None = None,
        clock: Callable[[], datetime] | None = None,
        deck_store: DeckStore | None = None,
        deck_nav_store: DeckNavStore | None = None,
        devices: DeviceRegistry | None = None,
        device_status: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._rotations = rotation_store
        self._state = state_store
        self._settings = settings_store
        self._pages = page_store
        # Optional Decks wiring: when both are present, a button that maps to a
        # deck-graph link on the device's current page navigates the deck
        # (promoting a pre-warmed frame) instead of the rotation.
        self._decks = deck_store
        self._deck_nav = deck_nav_store
        # Only needed to normalise a touch stroke (frame pixels) against a
        # deck zone (normalised 0..1); button navigation doesn't use it.
        self._devices = devices
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
        # Live device-status cache getter (heartbeat capabilities). The
        # post-action reconcile reads the sticky ``overlay`` capability
        # from it to pick the patch path over a full re-push.
        self._device_status = device_status
        # Post-action reconcile debounce state: device_id -> monotonic
        # time of its last HA action. Presence of a key means a worker
        # thread is already draining that device.
        self._reconcile_lock = threading.Lock()
        self._reconcile_last: dict[str, float] = {}

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
                result=result_dedup,
                origin="button",
                origin_extra={"button": button, "button_event_id": event_id},
                status="deduped",
            )
            return result_dedup

        # Deck navigation intercept: if the device is on a deck and this button
        # is a graph link on its current page, navigate the deck (promoting the
        # pre-warmed frame) and short-circuit. A button that isn't a deck link
        # falls through to the normal rotation / button_map path, so a device
        # can both deck-navigate and keep a "refresh" button.
        deck_target = self._try_deck_button(device_id, button)
        if deck_target is not None:
            return self._deck_navigate(
                device_id=device_id,
                deck=deck_target[0],
                target_page=deck_target[1],
                trigger_label=button,
                event_id=event_id,
                state=state,
                now=now,
                origin_extra={
                    "button": button,
                    "button_event_id": event_id,
                    "deck_id": deck_target[0].id,
                },
            )

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
                result=result_unmapped,
                origin="button",
                origin_extra={"button": button, "button_event_id": event_id},
                status="unmapped",
            )
            return result_unmapped

        # Dispatch. Malformed spec / unknown action -> log + no-op.
        return self._dispatch_spec(
            device_id=device_id,
            spec=spec,
            ctx=ctx,
            rotation=rotation,
            state=state,
            now=now,
            current_step_index=current_step_index,
            manual=_manual,
            trigger_label=button,
            event_id=event_id,
            origin="button",
            origin_extra={"button": button, "button_event_id": event_id},
        )

    # ---- deck navigation ---------------------------------------------

    def _bound_deck(self, device_id: str) -> Deck | None:
        """The enabled deck bound to a device (first when several), or None."""
        if self._decks is None or self._deck_nav is None:
            return None
        decks = self._decks.for_device(device_id)
        return decks[0] if decks else None

    def _deck_current_page(self, deck: Deck, device_id: str) -> str:
        """The page the device is on within ``deck``; its entry page if new."""
        assert self._deck_nav is not None
        return self._deck_nav.current_page(device_id, deck.id) or deck.resolved_entry_page_id

    def _try_deck_button(self, device_id: str, button: str) -> tuple[Deck, str] | None:
        """``(deck, target_page)`` when ``button`` navigates the device's
        current deck page: an explicit graph link, or the default
        ``left``/``right`` = previous/next in deck order (wrapping) when
        the graph is silent. The default keeps a graph-less deck (the
        management-page flow) navigable and mirrors the link table the
        sync manifest ships to cache-capable devices, so server-side and
        device-local navigation agree. None for non-nav buttons (fall
        through to rotation / button map)."""
        deck = self._bound_deck(device_id)
        if deck is None:
            return None
        current = self._deck_current_page(deck, device_id)
        target = deck.resolve_button(current, button)
        if target is None and button in ("left", "right"):
            from app.deck_sync import default_neighbours

            neighbours = default_neighbours(deck, current)
            if neighbours is not None:
                target = neighbours[0] if button == "left" else neighbours[1]
        return (deck, target) if target is not None else None

    def _panel_dims(self, device_id: str) -> tuple[int, int] | None:
        """The device's (width, height) in the frame's pixel space, used to
        normalise a touch point against a deck zone. None when unknown."""
        if self._devices is None:
            return None
        device = self._devices.get(device_id)
        if device is None:
            return None
        panel = device.panel or {}
        try:
            w, h = int(panel.get("w") or 0), int(panel.get("h") or 0)
        except (TypeError, ValueError):
            return None
        return (w, h) if w > 0 and h > 0 else None

    def _reconcile_deck_frame(self, device_id: str, frame_digest: str) -> dict[str, Any] | None:
        """When ``frame_digest`` matches a deck-cached render for this
        device, the panel is showing that frame via local (SD) nav:
        promote it into the live slot, record the nav position, and
        return the render info so the caller hit-tests the right
        composition. None when the digest matches nothing we know."""
        if not frame_digest or self._decks is None:
            return None
        deck = self._bound_deck(device_id)
        pusher = self._push_getter()
        if deck is None or pusher is None:
            return None
        render_for = getattr(pusher, "deck_render_for", None)
        promoter = getattr(pusher, "promote_deck_page", None)
        if not (callable(render_for) and callable(promoter)):
            return None
        latest_fn = getattr(pusher, "latest_render_for", None)
        latest = latest_fn(device_id) if callable(latest_fn) else None
        for page in deck.pages:
            info = render_for(device_id, page.page_id)
            if info is not None and str(info.get("digest") or "") == frame_digest:
                # RECENCY GUARD: hit-test against the frame the finger
                # actually touched either way, but only promote it into
                # the live slot when it isn't older than what's pending
                # there; otherwise a tap on a stale frame would silently
                # revert a fresh push awaiting delivery.
                try:
                    older = latest is not None and float(info.get("timestamp") or 0) < float(
                        latest.get("timestamp") or 0
                    )
                except (TypeError, ValueError):
                    older = False
                if not older:
                    promoter(device_id, page.page_id)
                if self._deck_nav is not None:
                    self._deck_nav.set(device_id, deck.id, page.page_id)
                log.info(
                    "touch reconcile: device=%s showing deck frame %s (page=%s, promoted=%s)",
                    device_id,
                    frame_digest,
                    page.page_id,
                    not older,
                )
                return dict(info)
        return None

    def _try_deck_touch(self, device_id: str, stroke: TouchStroke) -> tuple[Deck, str] | None:
        """``(deck, target_page)`` when the tap point lands in a deck zone on
        the device's current deck page, else None (fall through to markup
        touch regions)."""
        deck = self._bound_deck(device_id)
        if deck is None:
            return None
        dims = self._panel_dims(device_id)
        if dims is None:
            return None
        w, h = dims
        target = deck.resolve_zone(
            self._deck_current_page(deck, device_id), stroke.x0 / w, stroke.y0 / h
        )
        return (deck, target) if target is not None else None

    def _deck_navigate(
        self,
        *,
        device_id: str,
        deck: Deck,
        target_page: str,
        trigger_label: str,
        event_id: int | None,
        state: DeviceRotationState,
        now: datetime,
        origin_extra: dict[str, Any],
    ) -> ButtonHandleResult:
        """Move the device to ``target_page`` within ``deck``: promote the
        pre-warmed frame when there is one (instant), else render on the fly.
        Records nav position + dedup state either way."""
        pusher = self._push_getter()
        push_result: PushResult | None = None
        pushed_page_id: str | None = None
        if pusher is not None:
            pushed_page_id = target_page
            promoter = getattr(pusher, "promote_deck_page", None)
            if not (callable(promoter) and promoter(device_id, target_page)):
                # No warmed frame ready: render on the fly (Phase 4's refresh
                # keeps decks warm so this is the cold-start / miss path).
                push_result = pusher.push(
                    target_page,
                    device_ids={device_id},
                    respect_quiet_hours=False,
                    source="deck",
                )
        failed = push_result is not None and push_result.status == "failed"
        if self._deck_nav is not None:
            self._deck_nav.set(device_id, deck.id, target_page)
        # Dedup bookkeeping so a retry of the same event doesn't double-navigate.
        self._state.upsert(
            state.model_copy(
                update={
                    "last_button": trigger_label,
                    "last_button_event_id": event_id,
                    "last_button_at": now,
                }
            )
        )
        result = ButtonHandleResult(
            device_id=device_id,
            dedup=False,
            unmapped=False,
            action_spec=f"deck:{deck.id}:{target_page}",
            action_description=f"deck {deck.id!r} -> {target_page}",
            rotation_id=None,
            step_index=0,
            step_page_id=None,
            step_count=0,
            manual_override=False,
            override_until=None,
            pushed_page_id=None if failed else pushed_page_id,
            push_result=push_result,
        )
        self._emit_history_row(
            result=result,
            origin="deck",
            origin_extra=origin_extra,
            status="failed" if failed else "dispatched",
        )
        return result

    def _deck_touch_navigate(
        self, *, device_id: str, deck: Deck, target_page: str, event_id: int | None
    ) -> TouchHandleResult:
        """A deck-zone tap: dedup, navigate, wrap as a touch result."""
        now = self._clock()
        state = self._state.get(device_id) or DeviceRotationState(device_id=device_id)
        if self._is_duplicate(state, button="touch", event_id=event_id, now=now):
            return TouchHandleResult(outcome="deduped", gesture=None, base=self.snapshot(device_id))
        base = self._deck_navigate(
            device_id=device_id,
            deck=deck,
            target_page=target_page,
            trigger_label="touch",
            event_id=event_id,
            state=state,
            now=now,
            origin_extra={"touch_event_id": event_id, "deck_id": deck.id, "target": target_page},
        )
        return TouchHandleResult(
            outcome="dispatched" if base.pushed_page_id is not None else "error",
            gesture="tap",
            base=base,
            action_spec=base.action_spec,
        )

    def handle_touch(
        self,
        *,
        device_id: str,
        stroke: TouchStroke,
        frame_digest: str,
        event_id: int | None = None,
    ) -> TouchHandleResult:
        """Resolve + dispatch a touch stroke (see ``_handle_touch``),
        then speculatively prewarm the rotation's adjacent pages so a
        follow-up tap's synchronous push skips its Playwright capture
        (issue #49 linger). The prewarm runs on a daemon thread after the
        result is computed, so it never adds latency to this wake."""
        # Deck navigation intercept: a tap that lands in a deck zone on the
        # device's current deck page navigates the deck (promoting the warmed
        # frame). Taps outside every zone fall through to the markup touch
        # regions (widget actions) below.
        deck_touch = self._try_deck_touch(device_id, stroke)
        if deck_touch is not None:
            return self._deck_touch_navigate(
                device_id=device_id,
                deck=deck_touch[0],
                target_page=deck_touch[1],
                event_id=event_id,
            )
        result = self._handle_touch(
            device_id=device_id,
            stroke=stroke,
            frame_digest=frame_digest,
            event_id=event_id,
        )
        # Any live-session stroke qualifies (even a no_target tap means a
        # user is interacting with the current frame); the guard exits
        # mean there's no current-frame session to speculate for.
        if result.outcome not in ("deduped", "no_frame", "stale"):
            self._spawn_prewarm(device_id)
        return result

    def handle_region_report(
        self,
        *,
        device_id: str,
        region_id: str,
        gesture: str,
        frame_digest: str,
        value: int | None = None,
        event_id: int | None = None,
        stroke: TouchStroke | None = None,
    ) -> TouchHandleResult:
        """Protocol v2 dispatch: the device hit-tested locally and
        reports a manifest region id + gesture instead of coordinates.
        The server validates (the id must mint from the digest's own
        sidecar; the gesture must match the entry it names) and then
        dispatches through the same machinery as a coordinate stroke.
        Guard-chain outcomes mirror ``_handle_touch``: ``deduped`` /
        ``no_frame`` / ``stale`` / ``no_target``."""
        now = self._clock()
        state = self._state.get(device_id) or DeviceRotationState(device_id=device_id)
        diag = stroke or TouchStroke(x0=0, y0=0, x1=0, y1=0)

        if (
            event_id is not None
            and state.last_button_event_id is not None
            and event_id == state.last_button_event_id
        ):
            result = TouchHandleResult(
                outcome="deduped", gesture=None, base=self.snapshot(device_id)
            )
            self._emit_touch_row(result, stroke=diag, event_id=event_id)
            return result

        pusher = self._push_getter()
        latest = pusher.latest_render_for(device_id) if pusher is not None else None
        if latest is None:
            result = TouchHandleResult(
                outcome="no_frame", gesture=None, base=self.snapshot(device_id)
            )
            self._emit_touch_row(result, stroke=diag, event_id=event_id)
            return result
        if frame_digest != str(latest.get("digest") or ""):
            reconciled = self._reconcile_deck_frame(device_id, frame_digest)
            if reconciled is not None:
                latest = reconciled
            elif self._layout_unchanged(device_id, frame_digest, latest):
                # Protocol v2 staleness is anchored to LAYOUT, not pixels:
                # the region id fully identifies the action, and the frame
                # the finger touched differs from the live one only in
                # pixels (a clock tick or sensor re-render raced the tap).
                # Dispatch against the current sidecar instead of dropping
                # every tap on a dashboard that re-renders each 30 s.
                log.info(
                    "touch region report: frame %s superseded but layout "
                    "unchanged; dispatching (device=%s id=%s)",
                    frame_digest,
                    device_id,
                    region_id,
                )
            else:
                result = TouchHandleResult(
                    outcome="stale", gesture=None, base=self.snapshot(device_id)
                )
                self._emit_touch_row(result, stroke=diag, event_id=event_id)
                return result

        regions = (
            pusher.touch_regions_for(str(latest.get("composition_digest") or ""))
            if pusher is not None
            else []
        )
        panel = self._device_panel(device_id)
        from app.manifest import resolve_region_action

        resolved = resolve_region_action(regions, region_id, panel)
        if resolved is None or resolved[1] != gesture:
            log.info(
                "touch region report unresolved: device=%s id=%s gesture=%s",
                device_id,
                region_id,
                gesture,
            )
            result = TouchHandleResult(
                outcome="no_target", gesture=gesture, base=self.snapshot(device_id)
            )
            self._emit_touch_row(result, stroke=diag, event_id=event_id)
            return result
        region, _declared_gesture, spec = resolved
        magnitude = 0
        slide_val: int | None = None
        if gesture == "slide":
            slide_val = max(0, min(100, int(value or 0)))
            magnitude = slide_val
            spec = substitute_value(spec, slide_val)
        # Defense in depth: the manifest builder already refuses
        # markup-origin side effects, but the dispatch gate stays.
        if is_side_effecting(spec) and region.get("origin") != "config":
            result = TouchHandleResult(
                outcome="blocked",
                gesture=gesture,
                magnitude=magnitude,
                action_spec=_spec_label(spec),
                base=self.snapshot(device_id),
            )
            self._emit_touch_row(result, stroke=diag, event_id=event_id)
            return result

        origin_extra: dict[str, Any] = {
            "region_id": region_id,
            "touch_event_id": event_id,
            "gesture": gesture,
            "magnitude": magnitude,
            "value": slide_val,
        }
        if isinstance(spec, dict):
            result = self._dispatch_structured(
                device_id=device_id,
                action=spec,
                gesture=gesture,
                magnitude=magnitude,
                value=slide_val,
                stroke=diag,
                event_id=event_id,
                region_box={k: region[k] for k in ("x", "y", "w", "h") if k in region},
                state=state,
                now=now,
            )
            self._spawn_prewarm(device_id)
            return result

        rotation = self._resolve_rotation(device_id)
        current_step_index, manual = self._effective_step_index(rotation, state)
        ctx = ActionContext(
            device_id=device_id,
            current_step_index=current_step_index,
            rotation_step_count=len(rotation.steps) if rotation is not None else 0,
            rotation_id=rotation.id if rotation is not None else None,
            known_page_ids=frozenset(p.id for p in self._pages.list()),
        )
        base = self._dispatch_spec(
            device_id=device_id,
            spec=spec,
            ctx=ctx,
            rotation=rotation,
            state=state,
            now=now,
            current_step_index=current_step_index,
            manual=manual,
            trigger_label="touch",
            event_id=event_id,
            origin="touch",
            origin_extra=origin_extra,
        )
        if base.action_spec is not None and base.unmapped:
            outcome = "error"
        elif base.pushed_page_id is not None:
            outcome = "dispatched"
        elif base.force_download:
            outcome = "fetched"
        elif spec.startswith("webhook"):
            outcome = "webhook_dispatched"
        else:
            outcome = "noop"
        self._spawn_prewarm(device_id)
        return TouchHandleResult(
            outcome=outcome,
            gesture=gesture,
            magnitude=magnitude,
            action_spec=spec,
            value=slide_val,
            base=base,
        )

    def dispatch_touch_spec(
        self,
        *,
        device_id: str,
        spec: str | dict[str, Any],
        value: int | None = None,
        event_id: int | None = None,
        region_box: dict[str, Any] | None = None,
    ) -> TouchHandleResult:
        """Dispatch a resolved touch-v3 primitive action (device-owned touch).

        The firmware hit-tested a primitive locally and reported it; the endpoint
        resolved it to an action spec. This runs that spec through the same
        dispatch machinery as a coordinate touch, with ``{value}`` substitution
        for sliders, but the HA path passes ``reconcile=False``: the device
        already drew its own feedback and dashboard data arrives via the values
        channel, so the old post-action re-render/frame-patch is skipped.
        Primitives are authored in the canvas editor (config origin), so the
        markup-origin side-effect gate does not apply."""
        now = self._clock()
        state = self._state.get(device_id) or DeviceRotationState(device_id=device_id)
        diag = TouchStroke(x0=0, y0=0, x1=0, y1=0)
        if (
            event_id is not None
            and state.last_button_event_id is not None
            and event_id == state.last_button_event_id
        ):
            result = TouchHandleResult(
                outcome="deduped", gesture=None, base=self.snapshot(device_id)
            )
            self._emit_touch_row(result, stroke=diag, event_id=event_id)
            return result

        magnitude = int(value) if value is not None else 0
        if isinstance(spec, str) and value is not None:
            spec = substitute_value(spec, value)

        if isinstance(spec, dict):
            result = self._dispatch_structured(
                device_id=device_id,
                action=spec,
                gesture="tap",
                magnitude=magnitude,
                value=value,
                stroke=diag,
                event_id=event_id,
                region_box=region_box or {},
                state=state,
                now=now,
                reconcile=False,
            )
            self._spawn_prewarm(device_id)
            return result

        rotation = self._resolve_rotation(device_id)
        current_step_index, manual = self._effective_step_index(rotation, state)
        ctx = ActionContext(
            device_id=device_id,
            current_step_index=current_step_index,
            rotation_step_count=len(rotation.steps) if rotation is not None else 0,
            rotation_id=rotation.id if rotation is not None else None,
            known_page_ids=frozenset(p.id for p in self._pages.list()),
        )
        base = self._dispatch_spec(
            device_id=device_id,
            spec=spec,
            ctx=ctx,
            rotation=rotation,
            state=state,
            now=now,
            current_step_index=current_step_index,
            manual=manual,
            trigger_label="touch",
            event_id=event_id,
            origin="touch",
            origin_extra={"touch_event_id": event_id, "value": value},
        )
        if base.action_spec is not None and base.unmapped:
            outcome = "error"
        elif base.pushed_page_id is not None:
            outcome = "dispatched"
        elif base.force_download:
            outcome = "fetched"
        elif spec.startswith("webhook"):
            outcome = "webhook_dispatched"
        else:
            outcome = "noop"
        self._spawn_prewarm(device_id)
        return TouchHandleResult(
            outcome=outcome,
            gesture="tap",
            magnitude=magnitude,
            action_spec=spec,
            value=value,
            base=base,
        )

    def _spawn_prewarm(self, device_id: str) -> None:
        """Fire ``_prewarm_adjacent`` on a daemon thread. Split out so
        tests can run it synchronously."""
        threading.Thread(
            target=self._prewarm_adjacent,
            args=(device_id,),
            name="tesserae-touch-prewarm",
            daemon=True,
        ).start()

    def _prewarm_adjacent(self, device_id: str) -> None:
        """Prewarm the compositions a linger session will most likely ask
        for next: the rotation steps either side of the device's current
        step (prev/next swipe targets). Best-effort by design."""
        try:
            pusher = self._push_getter()
            prewarm = getattr(pusher, "prewarm_page", None)
            if not callable(prewarm):
                return
            rotation = self._resolve_rotation(device_id)
            if rotation is None or len(rotation.steps) < 2:
                return
            state = self._state.get(device_id) or DeviceRotationState(device_id=device_id)
            step_index, _ = self._effective_step_index(rotation, state)
            count = len(rotation.steps)
            candidates: list[str] = []
            for offset in (1, -1):
                page_id = rotation.steps[(step_index + offset) % count].page_id
                if page_id and page_id not in candidates:
                    candidates.append(page_id)
            for page_id in candidates:
                prewarm(page_id, device_id=device_id)
        except Exception:
            log.exception("touch prewarm failed for device=%s", device_id)

    def _handle_touch(
        self,
        *,
        device_id: str,
        stroke: TouchStroke,
        frame_digest: str,
        event_id: int | None = None,
    ) -> TouchHandleResult:
        """Resolve a touch stroke against the frame the device is showing
        and dispatch the region's action (issue #49).

        The guard chain, in order:

        * **dedup**: monotonic ``event_id`` shared with the button
          counter (one wake-event counter per device). No time-window
          fallback, two quick intentional taps are legitimate.
        * **no_frame**: nothing rendered for this device yet.
        * **stale**: ``frame_digest`` (the artifact digest the firmware
          holds as its ETag) doesn't match the current frame. The stroke
          landed on content that has since been replaced; drop it, the
          device repaints and the user re-taps on current content.
        * **no_target**: no region handles this gesture. No-op, not an error.

        Coordinates are in the served frame's pixel space (the frame as
        downloaded, before any device-side rotation/mirror). A tap hit-tests
        on its point; a swipe hit-tests on its *start* point and, if that
        doesn't land on a region declaring the swipe direction, falls back to
        the *end* point, so a stroke that starts just outside a small zone
        and moves into it still triggers."""
        now = self._clock()
        state = self._state.get(device_id) or DeviceRotationState(device_id=device_id)

        # Equality-only dedup, same rationale as ``_is_duplicate``: a
        # retry resends the SAME id, while a lower id means the RTC
        # counter restarted on a power cycle or the offline queue is
        # replaying a stroke, both of which must dispatch.
        if (
            event_id is not None
            and state.last_button_event_id is not None
            and event_id == state.last_button_event_id
        ):
            log.info(
                "touch dedup: device=%s event_id=%s (last=%s)",
                device_id,
                event_id,
                state.last_button_event_id,
            )
            base = self.snapshot(device_id)
            result = TouchHandleResult(outcome="deduped", gesture=None, base=base)
            self._emit_touch_row(result, stroke=stroke, event_id=event_id)
            return result

        pusher = self._push_getter()
        latest = pusher.latest_render_for(device_id) if pusher is not None else None
        if latest is None:
            result = TouchHandleResult(
                outcome="no_frame", gesture=None, base=self.snapshot(device_id)
            )
            self._emit_touch_row(result, stroke=stroke, event_id=event_id)
            return result
        if frame_digest != str(latest.get("digest") or ""):
            # Deck local nav (SD cache): the device may legitimately be
            # showing a deck-cached frame the server never served via
            # /frame. If the echoed digest matches one, that frame IS
            # current: promote it into the live slot (aligning ETag
            # polling and future strokes) and hit-test against it,
            # instead of dropping the user's tap as stale.
            reconciled = self._reconcile_deck_frame(device_id, frame_digest)
            if reconciled is not None:
                latest = reconciled
            else:
                log.info(
                    "touch stale: device=%s stroke_digest=%s current=%s",
                    device_id,
                    frame_digest,
                    latest.get("digest"),
                )
                result = TouchHandleResult(
                    outcome="stale", gesture=None, base=self.snapshot(device_id)
                )
                self._emit_touch_row(result, stroke=stroke, event_id=event_id)
                return result

        gesture, magnitude = classify_stroke(stroke.x0, stroke.y0, stroke.x1, stroke.y1)
        regions = (
            pusher.touch_regions_for(str(latest.get("composition_digest") or ""))
            if pusher is not None
            else []
        )
        region = hit_test(regions, stroke.x0, stroke.y0)
        # Swipe forgiveness (issue #49): a swipe often starts a hair outside a
        # small zone and moves into it (or the firmware's first sample lands
        # just outside the box). If the start point didn't land on a region
        # that declares this swipe direction, retry at the END point so the
        # stroke still triggers. Taps keep strict start-point hit-testing, and
        # sliders (which you press ON) are unaffected: this only rescues a
        # swipe onto a region that explicitly handles that direction.
        if gesture.startswith("swipe_") and not _handles_swipe(region, gesture):
            end_region = hit_test(regions, stroke.x1, stroke.y1)
            if _handles_swipe(end_region, gesture):
                region = end_region
        spec: str | dict[str, Any] | None = None
        slide_val: int | None = None
        if region is not None:
            slide = slide_declaration(region)
            if slide is not None:
                # A slider region absorbs every gesture: the stroke's end
                # point (== start point for a tap) sets an absolute 0-100
                # value along the axis, substituted into the action as
                # ``{value}``. Press-drag-lift to a level, or tap the bar
                # where you want it.
                gesture = "slide"
                slide_val = slide_value(region, str(slide["axis"]), stroke.x1, stroke.y1)
                magnitude = slide_val
                spec = substitute_value(slide["action"], slide_val)
            else:
                spec = resolve_gesture_action(region, gesture)
        if spec is None:
            result = TouchHandleResult(
                outcome="no_target",
                gesture=gesture,
                magnitude=magnitude,
                base=self.snapshot(device_id),
            )
            self._emit_touch_row(result, stroke=stroke, event_id=event_id)
            return result

        # Provenance gate (issue #49 phase 2): side-effecting actions
        # (webhook / ha) fire only when the region's action came from
        # validated config (editor / MCP fields, a code element's named
        # actions map), never from raw widget markup, so a third-party
        # widget can't aim a webhook by annotating its own HTML.
        if region is not None and is_side_effecting(spec) and region.get("origin") != "config":
            log.warning(
                "touch action blocked (markup origin): device=%s spec=%s",
                device_id,
                spec,
            )
            result = TouchHandleResult(
                outcome="blocked",
                gesture=gesture,
                magnitude=magnitude,
                action_spec=_spec_label(spec),
                base=self.snapshot(device_id),
            )
            self._emit_touch_row(result, stroke=stroke, event_id=event_id)
            return result

        region_box: dict[str, Any] | None = (
            {k: region[k] for k in ("x", "y", "w", "h") if k in region}
            if region is not None
            else None
        )

        # Structured (dict) actions: currently the Home Assistant service
        # call. Fired synchronously (the wake's response reports the
        # outcome); the repaint that shows the new HA state happens via
        # the debounced background reconcile, never inside this wake.
        if isinstance(spec, dict):
            return self._dispatch_structured(
                device_id=device_id,
                action=spec,
                gesture=gesture,
                magnitude=magnitude,
                value=slide_val,
                stroke=stroke,
                event_id=event_id,
                region_box=region_box,
                state=state,
                now=now,
            )

        rotation = self._resolve_rotation(device_id)
        current_step_index, manual = self._effective_step_index(rotation, state)
        ctx = ActionContext(
            device_id=device_id,
            current_step_index=current_step_index,
            rotation_step_count=len(rotation.steps) if rotation is not None else 0,
            rotation_id=rotation.id if rotation is not None else None,
            known_page_ids=frozenset(p.id for p in self._pages.list()),
        )
        base = self._dispatch_spec(
            device_id=device_id,
            spec=spec,
            ctx=ctx,
            rotation=rotation,
            state=state,
            now=now,
            current_step_index=current_step_index,
            manual=manual,
            trigger_label="touch",
            event_id=event_id,
            origin="touch",
            origin_extra={
                "touch": stroke.to_log(),
                "touch_event_id": event_id,
                "gesture": gesture,
                "magnitude": magnitude,
                "value": slide_val,
                "region": region_box,
            },
        )
        if base.action_spec is not None and base.unmapped:
            outcome = "error"
        elif base.pushed_page_id is not None:
            outcome = "dispatched"
        elif base.force_download:
            outcome = "fetched"
        elif spec.startswith("webhook"):
            outcome = "webhook_dispatched"
        else:
            outcome = "noop"
        # ``_dispatch_spec`` already emitted the history row.
        return TouchHandleResult(
            outcome=outcome,
            gesture=gesture,
            magnitude=magnitude,
            action_spec=spec,
            value=slide_val,
            base=base,
        )

    def _dispatch_structured(
        self,
        *,
        device_id: str,
        action: dict[str, Any],
        gesture: str,
        magnitude: int,
        value: int | None,
        stroke: TouchStroke,
        event_id: int | None,
        region_box: dict[str, Any] | None,
        state: DeviceRotationState,
        now: datetime,
        reconcile: bool = True,
    ) -> TouchHandleResult:
        """Dispatch a structured (dict) touch action. Currently the Home
        Assistant service call: ``{"action": "ha", "domain", "service",
        "data"}``. The call itself is synchronous (this wake's response
        reports dispatched vs failed); the repaint showing the new HA
        state is handed to the debounced background reconcile so the
        wake returns immediately and the digitizer stays live."""
        label = _spec_label(action)
        name = str(action.get("action") or "")
        outcome = "error"
        error: str | None = None
        pushed_page_id: str | None = None
        if name != "ha":
            error = f"unknown structured action {name!r}"
            log.warning("touch structured action unsupported: device=%s %s", device_id, label)
        else:
            domain = str(action.get("domain") or "").strip()
            service = str(action.get("service") or "").strip()
            raw_data = action.get("data")
            data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
            if not domain or not service:
                error = "ha action needs 'domain' and 'service'"
            else:
                try:
                    self._call_ha(domain, service, data)
                    outcome = "ha_dispatched"
                except Exception as exc:
                    outcome = "ha_failed"
                    error = f"{type(exc).__name__}: {exc}"
                    log.warning(
                        "touch ha call failed: device=%s %s.%s: %s",
                        device_id,
                        domain,
                        service,
                        exc,
                    )
        # Record the wake fingerprint for event-id dedup, same as the
        # string path does inside _dispatch_spec.
        self._state.upsert(
            state.model_copy(
                update={
                    "last_button": "touch",
                    "last_button_event_id": event_id,
                    "last_button_at": now,
                }
            )
        )
        push_result: PushResult | None = None
        if outcome == "ha_dispatched" and reconcile:
            # Catch the panel up with the new HA state OFF this wake: a
            # debounced background reconcile re-renders whatever the
            # device is showing (rotation step, deck page, or the last
            # directly-pushed page) and delivers the change either as
            # partial-refresh patch rects (overlay schema >= 2) or as a
            # normal push. The old synchronous re-push blocked the wake
            # for the full render + download + e-ink flash, locking
            # touch for ~10 s per action on the big panels.
            self._schedule_ha_reconcile(device_id)
        snapshot = self.snapshot(device_id)
        base = ButtonHandleResult(
            device_id=device_id,
            dedup=False,
            unmapped=False,
            action_spec=label,
            action_description=(
                f"ha: {action.get('domain')}.{action.get('service')}" if name == "ha" else label
            ),
            rotation_id=snapshot.rotation_id,
            step_index=snapshot.step_index,
            step_page_id=snapshot.step_page_id,
            step_count=snapshot.step_count,
            manual_override=snapshot.manual_override,
            override_until=snapshot.override_until,
            pushed_page_id=pushed_page_id,
            push_result=push_result,
        )
        result = TouchHandleResult(
            outcome=outcome,
            gesture=gesture,
            magnitude=magnitude,
            action_spec=label,
            value=value,
            base=base,
        )
        self._emit_history_row(
            result=base,
            origin="touch",
            origin_extra={
                "touch": stroke.to_log(),
                "touch_event_id": event_id,
                "gesture": gesture,
                "magnitude": magnitude,
                "value": value,
                "region": region_box,
            },
            status=outcome,
            error=error,
        )
        return result

    def _call_ha(self, domain: str, service: str, data: dict[str, Any]) -> None:
        """Fire a Home Assistant service call through the ha_core plugin
        (shared base URL / token / TLS policy). Split out so tests can
        stub the HA transport without an app context."""
        from flask import current_app

        registry = current_app.config.get("PLUGIN_REGISTRY")
        plugin = registry.get("ha_core") if registry is not None else None
        mod = getattr(plugin, "server_module", None) if plugin is not None else None
        if mod is None or not hasattr(mod, "call_service"):
            raise RuntimeError("ha_core plugin unavailable")
        # Plain service call (no ``return_response``): an actuator like
        # light.turn_on doesn't support returning a payload, and HA 400s the
        # request if asked to. call_service_with_response is only for the
        # read-style services that do (todo.get_items et al).
        mod.call_service(domain, service, data=data, timeout=int(self._ha_timeout_seconds()))

    def _ha_timeout_seconds(self) -> float:
        try:
            value = self._settings.get_section("app").get("touch_ha_timeout_s")
        except Exception:
            value = None
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return _HA_TIMEOUT_SECONDS

    # ---- post-action reconcile ---------------------------------------

    def _schedule_ha_reconcile(self, device_id: str) -> None:
        """Note an HA action and make sure a reconcile worker is
        draining this device. Every call re-arms the debounce window, so
        a burst of taps produces one render after the burst ends."""
        with self._reconcile_lock:
            already_running = device_id in self._reconcile_last
            self._reconcile_last[device_id] = time.monotonic()
        if not already_running:
            self._spawn_reconcile(device_id)

    def _spawn_reconcile(self, device_id: str) -> None:
        """Fire ``_reconcile_worker`` on a daemon thread. Split out so
        tests can run it synchronously (same seam as ``_spawn_prewarm``)."""
        threading.Thread(
            target=self._reconcile_worker,
            args=(device_id,),
            name="tesserae-touch-reconcile",
            daemon=True,
        ).start()

    def _reconcile_worker(self, device_id: str) -> None:
        """Debounce loop: wait until the device has been quiet for the
        debounce window, reconcile once, and re-run if more actions
        arrived while rendering. Exits (dropping the pending key) only
        when a reconcile completes with no newer action recorded."""
        try:
            while True:
                debounce = self._reconcile_debounce_seconds(device_id)
                while True:
                    with self._reconcile_lock:
                        last = self._reconcile_last.get(device_id, 0.0)
                    remaining = last + debounce - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(remaining)
                self._run_reconcile(device_id)
                with self._reconcile_lock:
                    if self._reconcile_last.get(device_id, last) == last:
                        self._reconcile_last.pop(device_id, None)
                        return
        except Exception:
            log.exception("touch reconcile worker failed: device=%s", device_id)
            with self._reconcile_lock:
                self._reconcile_last.pop(device_id, None)

    def _run_reconcile(self, device_id: str) -> None:
        """One reconcile pass: re-render the page the device is showing
        and deliver the difference. Patch-capable devices (overlay
        schema >= 2) get partial-refresh rects staged for their next
        ``/frame/data`` poll; everything else gets a normal async push
        (full frame on the next poll / publish)."""
        pusher = self._push_getter()
        if pusher is None:
            return
        page_id = self._reconcile_page_id(device_id)
        if page_id is None:
            log.info(
                "touch reconcile: no page resolved for device=%s (no rotation, "
                "deck, or last-pushed page); frame left as-is",
                device_id,
            )
            return
        if self._patch_capable(device_id):
            reconcile = getattr(pusher, "reconcile_via_patches", None)
            if callable(reconcile):
                outcome = reconcile(device_id, page_id, panel=self._device_panel(device_id))
                log.info(
                    "touch reconcile: device=%s page=%s outcome=%s",
                    device_id,
                    page_id,
                    outcome,
                )
                if outcome != "failed":
                    return
        try:
            pusher.push(
                page_id,
                device_ids={device_id},
                respect_quiet_hours=False,
                source="touch",
            )
        except Exception:
            log.exception("touch reconcile push failed: device=%s page=%s", device_id, page_id)

    def _reconcile_page_id(self, device_id: str) -> str | None:
        """The page the device is showing right now: its rotation's
        current step, else its deck's current page, else the page of its
        last-pushed frame. None when nothing resolves (image push, no
        frame yet); the periodic schedule catches those up."""
        rotation = self._resolve_rotation(device_id)
        if rotation is not None and rotation.steps:
            state = self._state.get(device_id)
            step_index, _ = self._effective_step_index(rotation, state)
            page_id = rotation.steps[step_index].page_id
            if page_id:
                return page_id
        deck = self._bound_deck(device_id)
        if deck is not None:
            deck_page = self._deck_current_page(deck, device_id)
            if deck_page:
                return deck_page
        pusher = self._push_getter()
        latest_fn = getattr(pusher, "latest_render_for", None) if pusher is not None else None
        latest = latest_fn(device_id) if callable(latest_fn) else None
        candidate = latest.get("page_id") if isinstance(latest, dict) else None
        return candidate if isinstance(candidate, str) and candidate else None

    def _layout_unchanged(
        self, device_id: str, reported_digest: str, latest: dict[str, Any]
    ) -> bool:
        """True when the frame a region report names renders the same
        interaction layout as the live frame: identical composition, or
        (composition differs, e.g. a value's text width shifted) an
        identical untrimmed region-id set. The digest lineage comes from
        the push manager's last ~10 retired frames; an unresolvable
        digest is genuinely stale."""
        pusher = self._push_getter()
        comp_fn = getattr(pusher, "composition_for_digest", None) if pusher is not None else None
        old_comp = comp_fn(device_id, reported_digest) if callable(comp_fn) else None
        cur_comp = str(latest.get("composition_digest") or "")
        if not old_comp or not cur_comp:
            return False
        if old_comp == cur_comp:
            return True
        regions_fn = getattr(pusher, "touch_regions_for", None)
        if not callable(regions_fn):
            return False
        from app.manifest import region_ids_for

        panel = self._device_panel(device_id)
        old_ids = region_ids_for(regions_fn(old_comp), panel)
        cur_ids = region_ids_for(regions_fn(cur_comp), panel)
        return old_ids is not None and bool(old_ids) and old_ids == cur_ids

    def _device_panel(self, device_id: str) -> dict[str, Any]:
        """The device's panel block (composition + native dims), the
        transform inputs the patch diff needs. Empty when unknown."""
        if self._devices is None:
            return {}
        device = self._devices.get(device_id)
        panel = getattr(device, "panel", None) if device is not None else None
        return dict(panel) if isinstance(panel, dict) else {}

    def _patch_capable(self, device_id: str) -> bool:
        """True when the device's sticky heartbeat capability advertises
        overlay schema >= 2 OR protocol v2 (fb-rect patch application is
        part of both contracts, and a v2 firmware need not keep sending
        the v1 overlay advert)."""
        if self._device_status is None:
            return False
        try:
            status = (self._device_status() or {}).get(device_id)
        except Exception:
            return False
        if not isinstance(status, dict):
            return False
        cap = status.get("overlay")
        schema = cap.get("schema") if isinstance(cap, dict) else None
        if isinstance(schema, int) and not isinstance(schema, bool) and schema >= 2:
            return True
        proto = status.get("proto")
        v = proto.get("v") if isinstance(proto, dict) else None
        return isinstance(v, int) and not isinstance(v, bool) and v >= 2

    def _reconcile_debounce_seconds(self, device_id: str) -> float:
        if self._patch_capable(device_id):
            key, default = "touch_patch_debounce_s", _PATCH_DEBOUNCE_SECONDS
        else:
            key, default = "touch_repush_debounce_s", _REPUSH_DEBOUNCE_SECONDS
        try:
            value = self._settings.get_section("app").get(key)
        except Exception:
            value = None
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
        return default

    def _dispatch_spec(
        self,
        *,
        device_id: str,
        spec: str,
        ctx: ActionContext,
        rotation: Rotation | None,
        state: DeviceRotationState,
        now: datetime,
        current_step_index: int,
        manual: bool,
        trigger_label: str,
        event_id: int | None,
        origin: str,
        origin_extra: dict[str, object],
    ) -> ButtonHandleResult:
        """Shared dispatch tail for button presses and touch strokes:
        run the action, persist rotation state, push the resolved page
        so the same wake's frame reflects the new state, fire webhooks
        async, and emit the history row. ``origin`` is the event-log
        source (``button`` / ``touch``); ``origin_extra`` carries the
        origin-specific fields for the history row and webhook payload."""
        try:
            result: ActionResult = dispatch(spec, ctx)
        except ButtonActionError as exc:
            log.warning(
                "%s action error: device=%s trigger=%s spec=%s: %s",
                origin,
                device_id,
                trigger_label,
                spec,
                exc,
            )
            new_state = state.model_copy(
                update={
                    "last_button": trigger_label,
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
                manual_override=manual,
                override_until=state.override_until if manual else None,
                pushed_page_id=None,
                push_result=None,
            )
            self._emit_history_row(
                result=result_error,
                origin=origin,
                origin_extra=origin_extra,
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
                "last_button": trigger_label,
                "last_button_event_id": event_id,
                "last_button_at": now,
            }
        )
        self._state.upsert(new_state)

        # Push the resolved page so the returned frame reflects the
        # new state on this same wake. If we can't push (missing
        # PushManager, quiet hours, lock contention), leave the state
        # updated so the next timer wake catches up.
        # Resolve the action name once; the refresh fallback below and the
        # webhook dispatch further down both need it.
        try:
            action_name, action_arg = parse_action_spec(spec)
        except ButtonActionError:
            action_name, action_arg = ("", None)

        pushed_page_id: str | None = None
        push_result: PushResult | None = None
        pusher = self._push_getter()
        if pusher is not None:
            if result.target_page_id is not None:
                pushed_page_id = result.target_page_id
            elif rotation is not None and (rotation_changed or result.force_refresh):
                pushed_page_id = rotation.steps[result.new_step_index].page_id
            elif action_name == "refresh" and result.force_refresh:
                # No rotation, deck, or target page drove this wake. Honour a
                # bare refresh by re-rendering whatever dashboard is currently
                # on the device (issue #146): the authoritative "what's on the
                # glass" is the page whose frame we last pushed here.
                latest_for = getattr(pusher, "latest_render_for", None)
                rec = latest_for(device_id) if callable(latest_for) else None
                candidate = rec.get("page_id") if isinstance(rec, dict) else None
                if isinstance(candidate, str) and candidate:
                    pushed_page_id = candidate
            if pushed_page_id is not None:
                try:
                    push_result = pusher.push(
                        pushed_page_id,
                        device_ids={device_id},
                        respect_quiet_hours=False,
                        source=origin,
                    )
                except Exception:
                    log.exception(
                        "%s push failed: device=%s page=%s spec=%s",
                        origin,
                        device_id,
                        pushed_page_id,
                        spec,
                    )

        # webhook:<url> action: fire a POST asynchronously so /frame
        # doesn't block on external endpoints. ``dispatch`` has already
        # validated the URL shape (http(s), non-empty), so we just
        # extract the arg and hand it off to the daemon thread.
        if action_name == "webhook" and action_arg:
            payload: dict[str, object] = {
                "device_id": device_id,
                **origin_extra,
                "action_spec": spec,
                "timestamp": now.isoformat(),
                "rotation_id": rotation.id if rotation is not None else None,
                "step_index": new_state.step_index if rotation is not None else None,
                "step_page_id": (
                    rotation.steps[new_state.step_index].page_id if rotation is not None else None
                ),
            }
            self._fire_webhook_async(action_arg, payload)

        # ``fetch_latest`` serves an artefact that already exists, so there is
        # no PushResult carrying the composition thumbnail. Snapshot the
        # current composition digest into the button event instead. The event's
        # top-level ``digest`` deliberately stays None: History treats that as
        # the resend contract, while this value is preview-only.
        preview_composition_digest: str | None = None
        if result.force_download and pusher is not None:
            latest_render_for = getattr(pusher, "latest_render_for", None)
            if callable(latest_render_for):
                latest = latest_render_for(device_id)
                if isinstance(latest, dict):
                    candidate = latest.get("composition_digest")
                    if isinstance(candidate, str) and candidate:
                        preview_composition_digest = candidate

        step_page_id = (
            rotation.steps[result.new_step_index].page_id if rotation is not None else None
        )
        log.info(
            "%s event: device=%s trigger=%s spec=%s -> step=%d page=%s%s",
            origin,
            device_id,
            trigger_label,
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
            force_download=result.force_download,
        )
        # Status distinguishes the outcomes admins care about: an actual
        # push, a fire-and-forget webhook (no push), or a rotate/refresh
        # that resolved to "nothing to do" (rare edge case, but worth
        # showing so the user sees the press wasn't lost).
        if action_name == "webhook":
            status = "webhook_dispatched"
        elif result.force_download:
            status = "fetched"
        elif pushed_page_id is not None:
            status = "dispatched"
        else:
            status = "noop"
        self._emit_history_row(
            result=result_ok,
            origin=origin,
            origin_extra=origin_extra,
            status=status,
            composition_digest=preview_composition_digest,
        )
        return result_ok

    # ---- internals ---------------------------------------------------

    def _emit_history_row(
        self,
        *,
        result: ButtonHandleResult,
        origin: str,
        origin_extra: dict[str, object],
        status: str,
        error: str | None = None,
        composition_digest: str | None = None,
    ) -> None:
        """Append a row to the event log so the History page has the
        button / touch feed as a first-class surface. Non-fatal on
        absence: constructed without an event log (tests, offline
        paths) skips silently. The push that some actions trigger
        already logs its own row via ``PushManager``; this row is the
        "user pressed / tapped" event alongside that "server pushed the
        page" event, so a busy device produces two rows per
        state-changing wake. Non-push branches (fetch, dedup, unmapped,
        error, webhook, noop) get one row that captures the whole outcome.

        Button rows use ``type="push"`` so the existing ``/history``
        route (which filters on that type) picks them up. Touch rows use
        ``type="touch"`` (issue #49) so they get their own category +
        chip on the Events page, carrying the full stroke / gesture /
        region / action payload for diagnostics, including the misses
        (no_target / stale / blocked), which are the ones worth seeing.
        ``digest`` is None so the row's Resend button stays disabled.
        ``fetch_latest`` may carry a preview-only ``composition_digest`` in
        ``extra`` so History can render the exact existing frame without
        changing that resend contract. ``origin`` becomes the row's source
        (``button`` / ``touch``); ``origin_extra`` carries the origin-specific
        fields (button name vs stroke + gesture)."""
        if self._event_log is None:
            return
        try:
            self._event_log.record(
                type="touch" if origin == "touch" else "push",
                source=origin,
                target=result.device_id,
                status=status,
                digest=None,
                error=error,
                duration_s=0.0,
                extra={
                    **origin_extra,
                    "device_ids": [result.device_id],
                    "action_spec": result.action_spec,
                    "action_description": result.action_description,
                    "rotation_id": result.rotation_id,
                    "step_index": result.step_index if result.rotation_id else None,
                    "step_page_id": result.step_page_id,
                    "pushed_page_id": result.pushed_page_id,
                    "manual_override": result.manual_override,
                    **(
                        {"composition_digest": composition_digest}
                        if composition_digest is not None
                        else {}
                    ),
                },
            )
        except Exception:
            log.exception(
                "%s event log write failed: device=%s status=%s",
                origin,
                result.device_id,
                status,
            )

    def _emit_touch_row(
        self,
        result: TouchHandleResult,
        *,
        stroke: TouchStroke,
        event_id: int | None,
    ) -> None:
        """History row for the guard-chain exits (deduped / no_frame /
        stale / no_target / blocked). Dispatched strokes are logged by
        ``_dispatch_spec`` with the full region + action context."""
        extra: dict[str, object] = {
            "touch": stroke.to_log(),
            "touch_event_id": event_id,
            "gesture": result.gesture,
            "magnitude": result.magnitude,
        }
        if result.outcome == "blocked":
            extra["blocked_action_spec"] = result.action_spec
        self._emit_history_row(
            result=result.base,
            origin="touch",
            origin_extra=extra,
            status=result.outcome,
        )

    def _resolve_rotation(self, device_id: str) -> Rotation | None:
        """Pick the highest-priority enabled rotation this device is
        bound to. Ties broken by earliest id lexicographically for
        determinism."""
        matches = [
            r
            for r in self._rotations.all()
            if r.enabled and r.steps and self._rotation_targets_device(r, device_id)
        ]
        if not matches:
            return None
        matches.sort(key=lambda r: (-r.priority, r.id))
        return matches[0]

    def _rotation_targets_device(self, rotation: Rotation, device_id: str) -> bool:
        """Whether a rotation drives this device. An explicit ``device_ids``
        wins; an empty one (the default the Rotations UI writes) falls
        through to the step pages' own device bindings, matching how the
        scheduler fires a rotation, it pushes the step page and lets the
        page's bindings route it. Without this, no UI-created rotation ever
        matched here, so physical buttons and touch swipes never advanced
        one."""
        if rotation.device_ids:
            return device_id in rotation.device_ids
        pages_by_id = {p.id: p for p in self._pages.list()}
        for step in rotation.steps:
            page = pages_by_id.get(step.page_id)
            # An unbound page (no device_ids) shows on every device, same
            # rule the timetable / composer use.
            if page is not None and (not page.device_ids or device_id in page.device_ids):
                return True
        return False

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

    def reset_event_counter(self, device_id: str) -> None:
        """Forget the device's wake-event dedup state (monotonic counter
        high-water mark + the same-button time-window fallback).

        Called on the re-pair lifecycle paths (/register on an existing
        id, /discover MAC claim): those are the moments a firmware's
        counter most obviously restarts (reflash, wipe). Defensive
        hygiene alongside the equality-only dedup rule (which already
        tolerates restarts): it also clears the same-button time-window
        fallback so a re-paired device starts from a clean slate."""
        state = self._state.get(device_id)
        if state is None:
            return
        if (
            state.last_button_event_id is None
            and state.last_button is None
            and state.last_button_at is None
        ):
            return
        self._state.upsert(
            state.model_copy(
                update={
                    "last_button_event_id": None,
                    "last_button": None,
                    "last_button_at": None,
                }
            )
        )
        log.info("button dedup reset: device=%s (re-pair)", device_id)

    def _is_duplicate(
        self,
        state: DeviceRotationState,
        *,
        button: str,
        event_id: int | None,
        now: datetime,
    ) -> bool:
        # Preferred path: the wake-event id from the firmware. A retry
        # resends the SAME id, so equality is the only duplicate signal.
        # A LOWER id is not a duplicate: the counter is RTC-backed on
        # the ESP32 boards and restarts at 0 on any power cycle (battery
        # pull, crash, reflash) without the device re-pairing, and the
        # offline touch queue can replay older-id strokes after a WiFi
        # outage; both must dispatch. (Pre-v0.187.2 this was ``<=`` and
        # a power-cycled panel had every event swallowed until re-pair.)
        if event_id is not None and state.last_button_event_id is not None:
            return event_id == state.last_button_event_id
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
