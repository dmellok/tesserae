"""Enforce the semantic token layer.

Widgets must paint from the ``--c-*`` semantic tokens (defined in
``static/style/widget-bauhaus.css``), never the raw ``--theme-*`` colour
primitives. This keeps the categorical-vs-status distinction intact and
means a theme/gamut retune only has to touch the primitives.

The ``--theme-font`` / ``--theme-font-mono`` typography tokens are not part
of the colour layer and are allowed.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO_ROOT / "plugins"

# Matches `var(--theme-<name>` — captures <name> up to the first non-alpha.
_PRIMITIVE = re.compile(r"var\(--theme-([a-zA-Z]+)")
_ALLOWED = {"font"}  # --theme-font and --theme-font-mono both capture "font"


def _assets() -> list[Path]:
    return sorted(
        p
        for p in PLUGINS_DIR.rglob("client.*")
        if p.suffix in {".css", ".js"} and "/tests/" not in str(p)
    )


def test_widgets_use_semantic_tokens_not_primitives() -> None:
    offenders: list[str] = []
    for path in _assets():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name in _PRIMITIVE.findall(line):
                if name not in _ALLOWED:
                    rel = path.relative_to(REPO_ROOT)
                    offenders.append(f"  {rel}:{lineno}: var(--theme-{name}) — use a --c-* token")
    assert not offenders, (
        "widgets must reference the --c-* semantic layer, not raw --theme-* "
        "colour primitives:\n" + "\n".join(offenders)
    )
