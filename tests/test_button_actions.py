"""Unit tests for the button action registry and built-in actions."""

from __future__ import annotations

import pytest

from app.button_actions import (
    DEFAULT_BUTTON_MAP,
    ActionContext,
    ActionResult,
    ButtonActionError,
    dispatch,
    parse_action_spec,
    register,
    registered_actions,
)


def _ctx(
    *,
    device_id: str = "dev",
    current_step_index: int = 0,
    rotation_step_count: int = 3,
    rotation_id: str | None = "rot",
    known_page_ids: frozenset[str] = frozenset({"morning", "afternoon"}),
) -> ActionContext:
    return ActionContext(
        device_id=device_id,
        current_step_index=current_step_index,
        rotation_step_count=rotation_step_count,
        rotation_id=rotation_id,
        known_page_ids=known_page_ids,
    )


# -- spec parsing --------------------------------------------------


def test_parse_action_spec_bare_action() -> None:
    assert parse_action_spec("rotate_next") == ("rotate_next", None)


def test_parse_action_spec_parameterised() -> None:
    assert parse_action_spec("page:morning") == ("page", "morning")


def test_parse_action_spec_url_arg_preserves_scheme() -> None:
    """URL args keep their ``https://`` intact; we split on the FIRST
    colon so the colon in the URL doesn't get eaten."""
    assert parse_action_spec("webhook:https://x.example/hook") == (
        "webhook",
        "https://x.example/hook",
    )


def test_parse_action_spec_empty_raises() -> None:
    with pytest.raises(ButtonActionError):
        parse_action_spec("")


def test_parse_action_spec_bare_colon_raises() -> None:
    with pytest.raises(ButtonActionError):
        parse_action_spec(":arg")


# -- rotate_prev / rotate_next -------------------------------------


def test_rotate_next_advances_by_one() -> None:
    result = dispatch("rotate_next", _ctx(current_step_index=0, rotation_step_count=3))
    assert result.new_step_index == 1
    assert result.target_page_id is None
    assert result.force_refresh is False


def test_rotate_next_wraps_at_end() -> None:
    result = dispatch("rotate_next", _ctx(current_step_index=2, rotation_step_count=3))
    assert result.new_step_index == 0


def test_rotate_prev_decrements_by_one() -> None:
    result = dispatch("rotate_prev", _ctx(current_step_index=2, rotation_step_count=3))
    assert result.new_step_index == 1


def test_rotate_prev_wraps_at_start() -> None:
    result = dispatch("rotate_prev", _ctx(current_step_index=0, rotation_step_count=3))
    assert result.new_step_index == 2


def test_rotate_next_no_rotation_bound_is_noop_refresh() -> None:
    """No rotation to advance -> keep the current position and force a
    refresh so the frame gets re-served (empty rotation degenerates to
    a refresh, which is a sensible fallback)."""
    result = dispatch("rotate_next", _ctx(rotation_id=None, rotation_step_count=0))
    assert result.new_step_index == 0
    assert result.force_refresh is True


# -- refresh --------------------------------------------------------


def test_refresh_keeps_step_and_forces_refresh() -> None:
    result = dispatch("refresh", _ctx(current_step_index=1))
    assert result.new_step_index == 1
    assert result.force_refresh is True
    assert result.target_page_id is None


# -- fetch_latest ---------------------------------------------------


def test_fetch_latest_only_forces_frame_download() -> None:
    result = dispatch("fetch_latest", _ctx(current_step_index=1))
    assert result.new_step_index == 1
    assert result.target_page_id is None
    assert result.force_refresh is False
    assert result.force_download is True


# -- step:<i> -------------------------------------------------------


def test_step_jumps_to_specific_index() -> None:
    result = dispatch("step:2", _ctx(current_step_index=0, rotation_step_count=4))
    assert result.new_step_index == 2


def test_step_missing_arg_raises() -> None:
    with pytest.raises(ButtonActionError):
        dispatch("step", _ctx())


def test_step_out_of_range_raises() -> None:
    with pytest.raises(ButtonActionError):
        dispatch("step:9", _ctx(rotation_step_count=3))


def test_step_non_integer_raises() -> None:
    with pytest.raises(ButtonActionError):
        dispatch("step:abc", _ctx())


def test_step_no_rotation_is_noop() -> None:
    result = dispatch("step:2", _ctx(rotation_id=None, rotation_step_count=0))
    assert result.new_step_index == 0
    assert result.force_refresh is True


# -- page:<id> -----------------------------------------------------


def test_page_pushes_target_without_moving_rotation() -> None:
    """The ``page:<id>`` shortcut sets a target_page_id but doesn't
    touch the rotation position: it's a shortcut, not a rotation
    manipulation, so a later timer wake resumes the scheduled step."""
    result = dispatch("page:morning", _ctx(current_step_index=1))
    assert result.new_step_index == 1
    assert result.target_page_id == "morning"
    assert result.force_refresh is True


def test_page_unknown_id_raises() -> None:
    """Guardrail: reject page ids the caller doesn't know about so we
    don't push a phantom render request."""
    with pytest.raises(ButtonActionError):
        dispatch("page:nope", _ctx())


def test_page_missing_arg_raises() -> None:
    with pytest.raises(ButtonActionError):
        dispatch("page", _ctx())


# -- webhook:<url> -------------------------------------------------


def test_webhook_valid_url_passes() -> None:
    result = dispatch("webhook:https://example.com/x", _ctx())
    assert result.force_refresh is False


def test_webhook_missing_url_raises() -> None:
    with pytest.raises(ButtonActionError):
        dispatch("webhook", _ctx())


def test_webhook_non_http_scheme_raises() -> None:
    with pytest.raises(ButtonActionError):
        dispatch("webhook:ftp://example.com/x", _ctx())


def test_webhook_refresh_keeps_the_url_intact() -> None:
    """Splits on the first colon, so the scheme survives."""
    result = dispatch("webhook_refresh:https://example.com/x?a=1", _ctx())
    assert "https://example.com/x?a=1" in result.description


def test_webhook_refresh_does_not_force_a_same_wake_render() -> None:
    """#242 reconciles on a delay instead. Rendering inside the wake would
    read the receiver's pre-action state, since the POST has no ack."""
    result = dispatch("webhook_refresh:https://example.com/x", _ctx())
    assert result.force_refresh is False
    assert result.force_download is False
    assert result.target_page_id is None


def test_webhook_refresh_validates_the_url_like_webhook() -> None:
    with pytest.raises(ButtonActionError):
        dispatch("webhook_refresh:ftp://example.com/x", _ctx())
    with pytest.raises(ButtonActionError):
        dispatch("webhook_refresh", _ctx())


def test_webhook_refresh_error_names_its_own_action() -> None:
    """The message is what an operator sees when their button_map is
    wrong, so it shouldn't tell them to fix a different action."""
    with pytest.raises(ButtonActionError) as excinfo:
        dispatch("webhook_refresh", _ctx())
    assert "webhook_refresh:https://" in str(excinfo.value)


# -- unknown actions -----------------------------------------------


def test_unknown_action_raises() -> None:
    with pytest.raises(ButtonActionError):
        dispatch("bogus_action", _ctx())


# -- registry extensibility ----------------------------------------


def test_register_overwrites_existing_action() -> None:
    """Third-party plugins can rebind actions. Tests use this to verify
    that the registry is mutable at runtime."""

    def _custom(ctx: ActionContext, _arg: str | None) -> ActionResult:
        return ActionResult(
            new_step_index=99,
            target_page_id=None,
            force_refresh=False,
            description="custom",
        )

    original = dispatch("refresh", _ctx()).description
    register("refresh", _custom)
    try:
        assert dispatch("refresh", _ctx()).new_step_index == 99
    finally:
        # Re-register the built-in so subsequent tests aren't affected.
        from app.button_actions import _refresh  # type: ignore[attr-defined]

        register("refresh", _refresh)
    assert original.startswith("refresh")


def test_registered_actions_includes_defaults() -> None:
    names = set(registered_actions())
    assert {
        "rotate_prev",
        "rotate_next",
        "refresh",
        "fetch_latest",
        "step",
        "page",
        "webhook",
        "webhook_refresh",
    } <= names


# -- default button map --------------------------------------------


def test_default_button_map_covers_conventional_names() -> None:
    assert DEFAULT_BUTTON_MAP["left"] == "rotate_prev"
    assert DEFAULT_BUTTON_MAP["right"] == "rotate_next"
    assert DEFAULT_BUTTON_MAP["refresh"] == "refresh"


# -- provenance gate (#242) ------------------------------------------


def test_webhook_refresh_is_side_effecting() -> None:
    """Widget markup must not be able to aim a POST. ``webhook_refresh``
    carries the same URL as ``webhook``, so it needs the same gate;
    omitting it would let any widget's HTML fire arbitrary requests."""
    from app.touch_regions import SIDE_EFFECTING_ACTIONS, is_side_effecting

    assert "webhook_refresh" in SIDE_EFFECTING_ACTIONS
    assert is_side_effecting("webhook_refresh:https://example.com/x") is True


def test_webhook_refresh_from_markup_origin_is_blocked() -> None:
    """The gate is origin-sensitive: config-declared is fine, markup is not."""
    from app.manifest import _dispatchable  # type: ignore[attr-defined]

    spec = "webhook_refresh:https://example.com/x"
    assert _dispatchable({"origin": "config"}, spec) is True
    assert _dispatchable({"origin": "markup"}, spec) is False
