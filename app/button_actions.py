"""Button action registry.

A physical button press on an ESP32 device arrives at ``/frame`` and
``/status`` with a button name (``left`` / ``right`` / ``refresh`` /
etc.). The button name is looked up in the device's ``button_map`` to
resolve an *action spec*, which this module then dispatches to a
callable that mutates rotation state and / or issues a push.

Action spec grammar (string form as stored in ``button_map``):

    "<action>"                (parameterless, e.g. "rotate_next")
    "<action>:<arg>"          (parameterised, e.g. "page:morning_briefing")

The colon-separated form keeps the config surface flat and JSON-
friendly. When there's no ``:`` the whole string is the action name
and ``arg`` is ``None``. Actions that need an argument raise
``ButtonActionError`` when the arg is missing or malformed; the
button handler treats that as an unmapped button and returns the
current frame without state change (per the spec).

Registered actions are pure functions of an immutable ``ActionContext``
plus their ``arg``. They return an ``ActionResult`` describing:

* whether the manual rotation position changed (``new_step_index``),
* whether the returned frame should be a specific page instead of the
  rotation's resolved page (``target_page_id``),
* whether the caller should force a re-render even if the resolved
  page is unchanged (``force_refresh``),
* whether the current frame response should bypass ``If-None-Match``
  and serve the latest already-rendered artefact (``force_download``),
* a short log-friendly description of what happened.

Adding a new action is one function + one registry entry. Custom
extensions can register at import time.

mypy --strict applies to this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


class ButtonActionError(ValueError):
    """Raised for malformed / missing action args. Callers treat this
    the same as an unmapped button: log + no-op + return current frame."""


@dataclass(frozen=True)
class ActionContext:
    """Everything an action needs to make a decision.

    ``rotation`` is ``None`` when the device isn't bound to any
    rotation. Rotation-manipulating actions (``rotate_prev``,
    ``rotate_next``, ``step:<i>``) become no-ops in that case; page /
    webhook actions still work since they don't depend on rotation
    state."""

    device_id: str
    current_step_index: int
    rotation_step_count: int
    rotation_id: str | None
    # Set of known page ids so ``page:<id>`` can validate before
    # returning a target_page_id the caller can't render.
    known_page_ids: frozenset[str]


@dataclass(frozen=True)
class ActionResult:
    """What the action decided.

    * ``new_step_index``: new rotation position (defaults to the input
      current index, i.e. no change). Only meaningful when the device
      is bound to a rotation.
    * ``target_page_id``: force the caller to push this specific page
      on this wake, bypassing the rotation's resolved page. ``None``
      means "use the rotation's step".
    * ``force_refresh``: re-render even if the resolved page hasn't
      changed. ``refresh`` is the canonical case; ``page:<id>`` also
      sets this when the target matches the currently-pushed page.
    * ``force_download``: serve the latest already-rendered artefact
      with a full ``200`` response even when the client's ETag matches.
      This does not render, push, move the rotation, or set an override.
    * ``description``: short human-readable label, used in event log
      + admin surfaces.
    """

    new_step_index: int
    target_page_id: str | None
    force_refresh: bool
    description: str
    force_download: bool = False


ActionFn = Callable[[ActionContext, str | None], ActionResult]

# Populated by ``register`` below. Keep a module-level dict so tests
# and third-party plugins can extend it.
_REGISTRY: dict[str, ActionFn] = {}


def register(action_name: str, fn: ActionFn) -> None:
    """Add an action to the registry. Overwrites any existing entry so
    tests + plugins can rebind if they need to."""
    _REGISTRY[action_name] = fn


def registered_actions() -> tuple[str, ...]:
    """Snapshot of registered action names (for admin UI + validation)."""
    return tuple(sorted(_REGISTRY))


def parse_action_spec(spec: str) -> tuple[str, str | None]:
    """Split ``<action>[:<arg>]`` into ``(action, arg_or_None)``.

    Splits on the FIRST colon so URL-style args like
    ``webhook:https://example.com/x`` keep their scheme intact.
    """
    if not isinstance(spec, str) or not spec:
        raise ButtonActionError(f"empty action spec: {spec!r}")
    head, sep, tail = spec.partition(":")
    if not head:
        raise ButtonActionError(f"malformed action spec: {spec!r}")
    return head, (tail if sep else None)


def dispatch(spec: str, ctx: ActionContext) -> ActionResult:
    """Look up ``spec`` in the registry and execute it. Raises
    ``ButtonActionError`` when the action is unknown or its arg is
    invalid."""
    action_name, arg = parse_action_spec(spec)
    fn = _REGISTRY.get(action_name)
    if fn is None:
        raise ButtonActionError(f"unknown action: {action_name!r}")
    return fn(ctx, arg)


# ---- built-in actions ---------------------------------------------


def _rotate_prev(ctx: ActionContext, _arg: str | None) -> ActionResult:
    if ctx.rotation_id is None or ctx.rotation_step_count == 0:
        return ActionResult(
            new_step_index=ctx.current_step_index,
            target_page_id=None,
            force_refresh=True,
            description="rotate_prev no-op (no rotation bound)",
        )
    new_idx = (ctx.current_step_index - 1) % ctx.rotation_step_count
    return ActionResult(
        new_step_index=new_idx,
        target_page_id=None,
        force_refresh=False,
        description=f"rotate_prev -> step {new_idx}",
    )


def _rotate_next(ctx: ActionContext, _arg: str | None) -> ActionResult:
    if ctx.rotation_id is None or ctx.rotation_step_count == 0:
        return ActionResult(
            new_step_index=ctx.current_step_index,
            target_page_id=None,
            force_refresh=True,
            description="rotate_next no-op (no rotation bound)",
        )
    new_idx = (ctx.current_step_index + 1) % ctx.rotation_step_count
    return ActionResult(
        new_step_index=new_idx,
        target_page_id=None,
        force_refresh=False,
        description=f"rotate_next -> step {new_idx}",
    )


def _refresh(ctx: ActionContext, _arg: str | None) -> ActionResult:
    return ActionResult(
        new_step_index=ctx.current_step_index,
        target_page_id=None,
        force_refresh=True,
        description="refresh (force re-render of current step)",
    )


def _fetch_latest(ctx: ActionContext, _arg: str | None) -> ActionResult:
    """Re-serve the latest rendered frame without producing a new one."""
    return ActionResult(
        new_step_index=ctx.current_step_index,
        target_page_id=None,
        force_refresh=False,
        description="fetch_latest (download latest rendered frame)",
        force_download=True,
    )


def _step(ctx: ActionContext, arg: str | None) -> ActionResult:
    if arg is None:
        raise ButtonActionError("step action requires an index arg, e.g. 'step:2'")
    try:
        target = int(arg)
    except ValueError as exc:
        raise ButtonActionError(f"step arg must be an integer: {arg!r}") from exc
    if ctx.rotation_id is None or ctx.rotation_step_count == 0:
        return ActionResult(
            new_step_index=ctx.current_step_index,
            target_page_id=None,
            force_refresh=True,
            description=f"step:{target} no-op (no rotation bound)",
        )
    if not (0 <= target < ctx.rotation_step_count):
        raise ButtonActionError(f"step arg {target} out of range [0, {ctx.rotation_step_count})")
    return ActionResult(
        new_step_index=target,
        target_page_id=None,
        force_refresh=False,
        description=f"step -> {target}",
    )


def _page(ctx: ActionContext, arg: str | None) -> ActionResult:
    """Push a specific page to this device on this wake.

    Doesn't touch rotation state: this is a shortcut, not a rotation
    manipulation. A later timer wake resumes whatever the scheduler
    would otherwise choose. If the page id isn't known, treat as
    malformed and let the caller no-op with a warning."""
    if arg is None or not arg:
        raise ButtonActionError("page action requires a page id, e.g. 'page:morning'")
    if arg not in ctx.known_page_ids:
        raise ButtonActionError(f"unknown page id: {arg!r}")
    return ActionResult(
        new_step_index=ctx.current_step_index,
        target_page_id=arg,
        force_refresh=True,
        description=f"page -> {arg}",
    )


def _validate_webhook_url(action_name: str, arg: str | None) -> str:
    """Shared arg check for the webhook actions. Validating here means a
    ``button_map`` holding a bad URL is rejected at dispatch time rather
    than silently failing on a daemon thread at fire time."""
    if arg is None or not arg:
        raise ButtonActionError(
            f"{action_name} action requires a URL, e.g. '{action_name}:https://…'"
        )
    if not (arg.startswith("http://") or arg.startswith("https://")):
        raise ButtonActionError(f"webhook URL must be http(s): {arg!r}")
    return arg


def _webhook(_ctx: ActionContext, arg: str | None) -> ActionResult:
    """Stub for the webhook action.

    The real implementation lives in the caller (the button service)
    because it needs the HTTP client and the current request context.
    Here we only validate the arg shape.
    """
    url = _validate_webhook_url("webhook", arg)
    return ActionResult(
        new_step_index=_ctx.current_step_index,
        target_page_id=None,
        force_refresh=False,
        description=f"webhook -> {url}",
    )


def _webhook_refresh(_ctx: ActionContext, arg: str | None) -> ActionResult:
    """``webhook`` plus a delayed re-render of whatever the panel is
    showing (#242).

    For buttons whose webhook mutates the state the dashboard reads: book
    a meeting room, toggle a door, file a ticket. Plain ``webhook`` is
    fire-and-forget and leaves the panel showing pre-action state until
    its next wake, which on a deep-sleeping panel can be a long time.

    ``force_refresh`` stays False deliberately. That would re-render
    inside this wake, and the POST is fire-and-forget with no ack, so the
    render would very likely read the receiver's *pre-action* state and
    repaint the same frame with more conviction. The button service
    instead schedules a delayed background reconcile, whose delay is
    configurable because only the operator knows how long their receiver
    takes to commit.
    """
    url = _validate_webhook_url("webhook_refresh", arg)
    return ActionResult(
        new_step_index=_ctx.current_step_index,
        target_page_id=None,
        force_refresh=False,
        description=f"webhook_refresh -> {url}",
    )


def _room_book(_ctx: ActionContext, arg: str | None) -> ActionResult:
    """``room_book:<room_id>`` books the room in its own calendar (#90).

    Like ``webhook_refresh``, ``force_refresh`` stays False: the write
    goes out and the panel is repainted by the delayed reconcile, because
    re-rendering inside this wake would race the calendar server and
    repaint the room's *pre-booking* state.

    The real work lives in the button service, which has the room store
    and the HTTP client; here we only validate the shape so a bad
    ``button_map`` is rejected at dispatch time rather than at fire time.
    """
    if not arg or not arg.strip():
        raise ButtonActionError("room_book action requires a room id, e.g. 'room_book:kestrel'")
    return ActionResult(
        new_step_index=_ctx.current_step_index,
        target_page_id=None,
        force_refresh=False,
        description=f"room_book -> {arg.strip()}",
    )


# Register built-ins at import time so ``dispatch`` sees them from
# the first call. Third-party plugins can ``register(...)`` later.
register("rotate_prev", _rotate_prev)
register("rotate_next", _rotate_next)
register("refresh", _refresh)
register("fetch_latest", _fetch_latest)
register("step", _step)
register("page", _page)
register("webhook", _webhook)
register("webhook_refresh", _webhook_refresh)
register("room_book", _room_book)


# ---- config defaults ----------------------------------------------

DEFAULT_BUTTON_MAP: dict[str, str] = {
    "left": "rotate_prev",
    "right": "rotate_next",
    "refresh": "refresh",
}

DEFAULT_DEBOUNCE_SECONDS: float = 3.0
