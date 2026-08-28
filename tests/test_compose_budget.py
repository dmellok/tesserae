"""The webpage widget's iframe wait must fit inside the renderer's
compose-signal budget.

The widget holds the composer's ``__tesseraeComposed`` signal for up to
``settle + IFRAME_LOAD_GRACE_MS``; if the renderer's wait is shorter, the
screenshot fires mid-settle and captures the iframe blank (the intermittent
"webpage cell is empty" report). The three numbers live in three files
(plugin.json, client.js, renderer.py), so this test pins the cross-file
invariant instead of trusting comments to stay true.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.renderer import _COMPOSE_SIGNAL_TIMEOUT_MS

_WEBPAGE = Path(__file__).resolve().parent.parent / "plugins" / "webpage"


def _js_const(source: str, name: str) -> int:
    m = re.search(rf"const {name} = (\d+);", source)
    assert m, f"{name} not found in webpage client.js"
    return int(m.group(1))


def test_compose_wait_outlasts_webpage_worst_case() -> None:
    manifest = json.loads((_WEBPAGE / "plugin.json").read_text(encoding="utf-8"))
    settle = next(o for o in manifest["cell_options"] if o["name"] == "settle_seconds")
    client = (_WEBPAGE / "client.js").read_text(encoding="utf-8")

    grace_ms = _js_const(client, "IFRAME_LOAD_GRACE_MS")
    max_settle_ms = _js_const(client, "MAX_SETTLE_MS")

    # client.js clamps to the same ceiling the option schema advertises.
    assert max_settle_ms == settle["max"] * 1000

    # Worst-case widget wait stays under the renderer's compose budget,
    # with margin for the compose page's own goto + other cells' mounts.
    worst_case_ms = max_settle_ms + grace_ms
    assert worst_case_ms + 2_000 <= _COMPOSE_SIGNAL_TIMEOUT_MS
