"""Community themes installed from the marketplace.

A community theme is the minimum useful tarball: one ``theme.json``
manifest + one ``theme.css`` block. The marketplace install path
unwraps the tarball, validates the two-file shape and the CSS
selector, then copies the folder to
``<data_root>/themes/community/<id>/``. This store walks that directory
on every call (the dir is the index, no separate JSON file to drift)
and exposes the parsed entries to:

* the theme registry, for picker + browse-page listing, and
* the ``GET /themes/community.css`` endpoint, which concatenates every
  installed theme's CSS so the cascade matches what's on disk.

Read-only: edits go through duplicate-to-user-theme, exactly like
bundled themes. Uninstall is just ``rmtree`` of the per-theme folder
(the marketplace handles that path).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from app.state.theme_registry import Theme, ThemeFamily

logger = logging.getLogger(__name__)

# Anything inside this folder is considered an installed community
# theme. The marketplace owns writes; this store only reads.
_MANIFEST_NAME = "theme.json"
_CSS_NAME = "theme.css"

# Same id pattern as plugin / catalog ids elsewhere; lets CSS selectors,
# URLs, and dropdown labels share the same shape without escaping.
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

# Valid family values; anything outside this set falls back to
# ``community`` so the picker still groups it sensibly.
_KNOWN_FAMILIES: frozenset[ThemeFamily] = frozenset(
    {"light", "dark", "movement", "vivid", "gradient"}
)


@dataclass(frozen=True)
class CommunityTheme:
    """Parsed view of one installed community theme. The CSS file is
    served verbatim through the ``/themes/community.css`` route; the
    manifest fields feed the registry / picker so contributors don't
    have to repeat themselves in two places."""

    id: str
    name: str
    family: ThemeFamily
    tagline: str | None
    css_path: Path

    def to_registry_theme(self) -> Theme:
        return Theme(
            id=self.id,
            name=self.name,
            family=self.family,
            tagline=self.tagline,
            community=True,
        )


class CommunityThemeStore:
    """Read-only directory walker. Thread-safety: the listing operation
    re-reads the directory each call (cheap; a few stat calls + one JSON
    parse per theme). Marketplace install / uninstall mutates the
    directory under its own lock, so a concurrent ``list_all`` may see
    a partial state for a brief window — acceptable for an installer
    that runs from a single Flask request thread anyway."""

    def __init__(self, root: Path) -> None:
        self._root = root
        # Cheap mutex against concurrent listing while a CI tool / test
        # mutates the same dir.
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self._root

    def list_all(self) -> list[CommunityTheme]:
        """Walk ``<root>/<theme_id>/theme.json`` for every installed
        theme. Invalid manifests are skipped with a warning so a single
        malformed entry doesn't break the picker for everyone else."""
        with self._lock:
            if not self._root.is_dir():
                return []
            out: list[CommunityTheme] = []
            for entry in sorted(self._root.iterdir()):
                if not entry.is_dir():
                    continue
                theme = self._read_one(entry)
                if theme is not None:
                    out.append(theme)
            return out

    def get(self, theme_id: str) -> CommunityTheme | None:
        """Fast path for "do we have this id?" without listing the
        whole directory. Same validation as ``list_all``."""
        if not _ID_RE.match(theme_id):
            return None
        return self._read_one(self._root / theme_id)

    def _read_one(self, folder: Path) -> CommunityTheme | None:
        manifest_path = folder / _MANIFEST_NAME
        css_path = folder / _CSS_NAME
        if not manifest_path.is_file() or not css_path.is_file():
            return None
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            logger.warning("community_themes: bad manifest %s: %s", manifest_path, err)
            return None
        if not isinstance(raw, dict):
            return None
        # ``id`` MUST match the folder name; otherwise the CSS selector
        # would point at one id while the registry advertises a
        # different one. We do not silently rewrite either.
        declared_id = raw.get("id")
        if not isinstance(declared_id, str) or declared_id != folder.name:
            logger.warning(
                "community_themes: id mismatch in %s (declared %r, folder %r)",
                manifest_path,
                declared_id,
                folder.name,
            )
            return None
        if not _ID_RE.match(declared_id):
            return None
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        family_raw = raw.get("family") or "community"
        family: ThemeFamily = (
            family_raw if family_raw in _KNOWN_FAMILIES else "community"  # type: ignore[assignment]
        )
        tagline = raw.get("tagline")
        if tagline is not None and not isinstance(tagline, str):
            tagline = None
        return CommunityTheme(
            id=declared_id,
            name=name.strip(),
            family=family,
            tagline=tagline.strip() if isinstance(tagline, str) else None,
            css_path=css_path,
        )


def emit_css(store: CommunityThemeStore) -> str:
    """Concatenate every installed community theme's ``theme.css`` for
    the ``/themes/community.css`` endpoint. We serve the raw file
    bytes (no normalisation, no minification) so the contributor's
    intent reaches the browser unchanged; the install path is what
    enforced the selector shape, so the read side stays trivial.

    Empty store ⇒ a small comment string. The conditional ``<link>``
    tag would be more code than just letting 0-byte CSS through, and
    the cascade tolerates empty stylesheets cleanly."""
    themes = store.list_all()
    if not themes:
        return "/* No community themes installed. */\n"
    chunks: list[str] = []
    for theme in themes:
        try:
            block = theme.css_path.read_text(encoding="utf-8")
        except OSError as err:
            logger.warning("community_themes: could not read %s: %s", theme.css_path, err)
            continue
        chunks.append(f"/* {theme.id} */\n{block.strip()}\n")
    if not chunks:
        return "/* No readable community themes. */\n"
    header = "/* Community themes installed from the marketplace. Generated. */\n"
    return header + "\n".join(chunks)
