"""Every button/touch action has to be declared in more than one place,
and nothing fails loudly when one of them is missed.

Adding ``webhook_refresh`` (#242) missed two: the touch editor's action
picker carries its own hardcoded list rather than reading the registry,
and ``SIDE_EFFECTING_ACTIONS`` is a separate literal set. The second miss
was a security hole, since that set is what stops raw widget markup
aiming an HTTP request.

These tests fail when a new action is registered without a decision being
recorded for each surface. The fix for a failure is never to widen the
allowlist reflexively; it's to decide what the new action should do on
that surface and say so.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.button_actions import registered_actions
from app.touch_regions import SIDE_EFFECTING_ACTIONS
from app.touch_spec import classify_action

REPO_ROOT = Path(__file__).resolve().parent.parent
TOUCH_EDITOR_JS = REPO_ROOT / "static" / "touch_interaction.js"

# Registered actions deliberately absent from the touch editor's picker.
# ``fetch_latest`` re-downloads the frame the server already has; it's a
# recovery lever for a physical button on a panel showing a stale image,
# and offering it as a tap target invites taps that visibly do nothing.
_NOT_IN_TOUCH_EDITOR = frozenset({"fetch_latest"})

# Actions that are NOT side-effecting, i.e. safe to accept from raw widget
# markup. Everything here only moves the panel between things the operator
# already configured. Anything that reaches outside Tesserae belongs in
# ``SIDE_EFFECTING_ACTIONS`` instead.
_KNOWN_SAFE = frozenset(
    {
        "refresh",
        "fetch_latest",
        "rotate_next",
        "rotate_prev",
        "step",
        "page",
    }
)


def _touch_editor_action_ids() -> set[str]:
    """Pull the ``id`` values out of the ``var ACTIONS = [...]`` literal."""
    source = TOUCH_EDITOR_JS.read_text(encoding="utf-8")
    match = re.search(r"var ACTIONS = \[(.*?)\];", source, re.DOTALL)
    assert match, "couldn't find the ACTIONS array in touch_interaction.js"
    return set(re.findall(r'id:\s*"([^"]*)"', match.group(1)))


def test_every_registered_action_is_offered_in_the_touch_editor() -> None:
    """The picker is hardcoded, so a new action is invisible in the UI
    until someone remembers to add it there too (#242)."""
    offered = _touch_editor_action_ids()
    expected = set(registered_actions()) - _NOT_IN_TOUCH_EDITOR
    missing = sorted(expected - offered)
    assert not missing, (
        f"registered but not selectable in the touch editor: {missing}. "
        f"Add them to ACTIONS in {TOUCH_EDITOR_JS.name}, or to "
        f"_NOT_IN_TOUCH_EDITOR here with a reason."
    )


def test_touch_editor_offers_nothing_unregistered() -> None:
    """The other direction: a picker entry that dispatch would reject is
    a dead option that fails only once a user picks it."""
    offered = _touch_editor_action_ids()
    # "" is the "None" entry; "ha" is structured (a dict spec), dispatched
    # outside the string-action registry.
    known = set(registered_actions()) | {"", "ha"}
    unknown = sorted(offered - known)
    assert not unknown, f"offered in the touch editor but not dispatchable: {unknown}"


def test_every_registered_action_is_classified_for_the_provenance_gate() -> None:
    """The gate is an allowlist by omission: an action absent from
    ``SIDE_EFFECTING_ACTIONS`` is silently treated as safe for raw markup
    to aim. A new action must be an explicit decision, not a default."""
    unclassified = sorted(set(registered_actions()) - SIDE_EFFECTING_ACTIONS - _KNOWN_SAFE)
    assert not unclassified, (
        f"unclassified for the provenance gate: {unclassified}. If the action reaches "
        f"outside Tesserae, add it to SIDE_EFFECTING_ACTIONS in app/touch_regions.py. "
        f"If it only moves the panel between configured things, add it to _KNOWN_SAFE here."
    )


def test_the_two_classifications_do_not_overlap() -> None:
    """A stale entry in the test's own safe-list would quietly cancel a
    real gate entry, so treat any overlap as a bug in this file."""
    overlap = sorted(SIDE_EFFECTING_ACTIONS & _KNOWN_SAFE)
    assert not overlap, f"listed as both side-effecting and safe: {overlap}"


def test_every_registered_action_has_a_feedback_classification() -> None:
    """Unclassified specs fall through to ``nav``, so a new action shows
    navigation affordance on tap regardless of what it does (#242)."""
    for name in registered_actions():
        spec = f"{name}:x" if name in {"step", "page", "webhook", "webhook_refresh"} else name
        _, kind = classify_action(spec)
        assert kind != "nav" or name in {
            "rotate_next",
            "rotate_prev",
            "step",
            "page",
        }, f"{name!r} falls through to the 'nav' feedback default; classify it in app/touch_spec.py"
