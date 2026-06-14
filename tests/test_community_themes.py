"""Community-themes store: directory walker + CSS concat.

The marketplace install path is the only writer; this module just
reads. So the tests here exercise the read side against synthetic
on-disk layouts to pin the rules:

* Valid two-file folder → parsed entry.
* Missing theme.json or theme.css → skipped (warning, not raised).
* theme.json id ≠ folder name → skipped (mismatch would shadow a
  different theme).
* Bad JSON / wrong types → skipped.
* Unknown family → falls back to ``community`` family bucket.
* Empty store → ``emit_css`` returns a stable comment string.
* ``emit_css`` concatenates only readable themes; an unreadable
  theme.css logs and drops out without breaking the rest.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.state.community_themes import CommunityThemeStore, emit_css


def _drop_theme(root: Path, theme_id: str, *, manifest: dict, css: str) -> None:
    folder = root / theme_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "theme.css").write_text(css, encoding="utf-8")


def test_store_lists_valid_themes(tmp_path: Path) -> None:
    """Two well-formed theme folders → both surface in list_all,
    sorted by folder name so the picker order is stable across runs."""
    root = tmp_path / "themes-community"
    _drop_theme(
        root,
        "aqua",
        manifest={"id": "aqua", "name": "Aqua", "family": "light", "tagline": "ocean blues"},
        css='[data-theme="aqua"]{ --bg: #0ee; }',
    )
    _drop_theme(
        root,
        "midnight",
        manifest={"id": "midnight", "name": "Midnight", "family": "dark"},
        css='[data-theme="midnight"]{ --bg: #000; }',
    )
    out = CommunityThemeStore(root).list_all()
    assert [t.id for t in out] == ["aqua", "midnight"]
    assert out[0].name == "Aqua"
    assert out[0].tagline == "ocean blues"
    assert out[1].tagline is None


def test_store_skips_folder_with_missing_files(tmp_path: Path) -> None:
    """A theme folder that's missing either file is silently skipped —
    a broken half-install shouldn't kill the whole picker."""
    root = tmp_path / "themes-community"
    # Missing theme.css
    (root / "no-css").mkdir(parents=True)
    (root / "no-css" / "theme.json").write_text('{"id":"no-css","name":"X","family":"light"}')
    # Missing theme.json
    (root / "no-manifest").mkdir(parents=True)
    (root / "no-manifest" / "theme.css").write_text('[data-theme="no-manifest"]{}')

    out = CommunityThemeStore(root).list_all()
    assert out == []


def test_store_skips_id_mismatch(tmp_path: Path) -> None:
    """The manifest id MUST match the folder name. A mismatch means
    the [data-theme="<id>"] CSS block won't line up with the registered
    Theme id, so we refuse to load it."""
    root = tmp_path / "themes-community"
    _drop_theme(
        root,
        "ocean",  # folder name
        manifest={"id": "OCEAN-typo", "name": "Ocean", "family": "light"},
        css='[data-theme="OCEAN-typo"]{}',
    )
    assert CommunityThemeStore(root).list_all() == []


def test_unknown_family_falls_back_to_community(tmp_path: Path) -> None:
    """Theme manifests can declare ``family``; anything outside the
    bundled set falls back to ``community`` so the picker still groups it."""
    root = tmp_path / "themes-community"
    _drop_theme(
        root,
        "weird",
        manifest={"id": "weird", "name": "Weird", "family": "made-up"},
        css='[data-theme="weird"]{}',
    )
    out = CommunityThemeStore(root).list_all()
    assert len(out) == 1
    assert out[0].family == "community"


def test_emit_css_empty_store_returns_stable_comment(tmp_path: Path) -> None:
    """No themes installed ⇒ a one-line comment, so the unconditional
    <link> tag in compose.html / themes.html stays valid."""
    body = emit_css(CommunityThemeStore(tmp_path / "themes-community"))
    assert "No community themes installed" in body


def test_emit_css_concatenates_installed_themes(tmp_path: Path) -> None:
    """Every readable theme.css ends up in the concatenated body, each
    preceded by a comment header so the source of each block is
    visible if a maintainer ever has to read the served CSS."""
    root = tmp_path / "themes-community"
    _drop_theme(
        root,
        "aqua",
        manifest={"id": "aqua", "name": "Aqua", "family": "light"},
        css='[data-theme="aqua"]{ --bg: #0ee; }',
    )
    _drop_theme(
        root,
        "midnight",
        manifest={"id": "midnight", "name": "Midnight", "family": "dark"},
        css='[data-theme="midnight"]{ --bg: #000; }',
    )
    body = emit_css(CommunityThemeStore(root))
    assert '[data-theme="aqua"]' in body
    assert '[data-theme="midnight"]' in body
    assert "/* aqua */" in body
    assert "/* midnight */" in body


def test_get_finds_theme_by_id(tmp_path: Path) -> None:
    """``get`` is a fast path that skips the full directory walk; it
    must return the same shape ``list_all`` would have for that entry."""
    root = tmp_path / "themes-community"
    _drop_theme(
        root,
        "aqua",
        manifest={"id": "aqua", "name": "Aqua", "family": "light"},
        css='[data-theme="aqua"]{}',
    )
    store = CommunityThemeStore(root)
    found = store.get("aqua")
    assert found is not None and found.id == "aqua"
    assert store.get("ghost") is None
    # Refuses ids that don't match the id pattern (defence in depth: a
    # caller passing `../` should never reach the filesystem walk).
    assert store.get("../etc/passwd") is None


def test_to_registry_theme_carries_community_flag(tmp_path: Path) -> None:
    """The Theme dataclass version that goes into ``build_registry``
    has ``community=True`` so the browse UI can render the
    Duplicate-to-edit pattern for these themes."""
    root = tmp_path / "themes-community"
    _drop_theme(
        root,
        "aqua",
        manifest={"id": "aqua", "name": "Aqua", "family": "light"},
        css='[data-theme="aqua"]{}',
    )
    [community_theme] = CommunityThemeStore(root).list_all()
    registry_theme = community_theme.to_registry_theme()
    assert registry_theme.community is True
    assert registry_theme.user is False
    assert registry_theme.family == "light"
