"""Run each widget's ``clamp_check.mjs`` self-check under pytest.

The calendar widgets ship plain-node assertion scripts covering the client-side
clamping helpers — ``clampScale``, ``clampToDay``, ``computeRange`` and friends.
Their own headers say ``Run: node tests/clamp_check.mjs``, and nothing did:
no CI step, no pytest bridge, no ``package.json`` test script referenced them.

That is a coverage hole with a history. ``calendar_day``'s header records it:
the cases "previously covered by test_smoke.py's
test_scale_sliders_clamp_to_bounds" moved out of pytest when the clamp moved
from ``server.py`` to ``client.js``, and the assertions went with them. They
have been unrun since — free to drift from ``client.js`` with nothing to
notice (#214).

This is the "fold them into the Python smoke test via a subprocess call" option
from that issue: the scripts stay where they are, next to the code they assert
on, and the existing pytest run executes them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKS = sorted(REPO_ROOT.glob("plugins/*/tests/clamp_check.mjs"))


def _node() -> str | None:
    return shutil.which("node")


def test_every_clamp_check_is_discovered() -> None:
    """A widget that grows a self-check is picked up without editing this file.

    Also guards the reverse: if the glob ever silently matches nothing, the
    parametrised test below would vacuously "pass" by having no cases, which is
    exactly the invisible-drift failure this file exists to close.
    """
    assert CHECKS, "no plugins/*/tests/clamp_check.mjs found — has the layout moved?"


@pytest.mark.parametrize("script", CHECKS, ids=lambda p: p.parent.parent.name)
def test_clamp_check_passes(script: Path) -> None:
    node = _node()
    if node is None:
        # Skipping locally is fine — plenty of contributors work on the Python
        # side without node. Skipping in CI is not: it would restore the exact
        # silence this test removes, so fail there instead. GitHub's
        # ubuntu-latest image ships node, so this only fires on a real
        # regression in the runner image.
        if os.environ.get("CI"):
            pytest.fail("node is not on PATH in CI, so the widget self-checks did not run")
        pytest.skip("node is not installed; widget self-checks skipped locally")

    result = subprocess.run(
        [node, str(script)],
        capture_output=True,
        text=True,
        cwd=script.parent,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"{script.relative_to(REPO_ROOT)} failed:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
