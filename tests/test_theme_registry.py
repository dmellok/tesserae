"""Theme registry: in-lockstep with the Spectra CSS + picker contract.

The registry is the single source of truth that drives both the
/themes browse page and the page editor's theme picker. The CSS
cascade is the source of truth for what data-theme actually paints.
This file pins the invariant that the two never drift: every theme
the registry advertises must have a CSS block, and every CSS block
must have a registry entry.
"""

from __future__ import annotations

import re

from app.main import REPO_ROOT
from app.state.theme_registry import (
    BUNDLED_THEMES,
    FAMILY_ORDER,
    Theme,
    build_registry,
    picker_options,
    themes_by_family,
)

# Match a real CSS rule selector — ``[data-theme="..."]`` immediately
# followed by ``{`` (allowing whitespace + the optional ``:root,`` group).
# Avoids picking up example values inside comments like the one at the
# top of spectra-tokens.css.
_CSS_THEME_RE = re.compile(r'\[data-theme="([^"]+)"\]\s*(?:,\s*\[data-theme="[^"]+"\]\s*)*\{')
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_SPECTRA_FILES = ("spectra-tokens.css", "spectra-base16.css")


def _css_theme_ids() -> set[str]:
    """Every ``data-theme="..."`` selector that opens a real CSS rule
    in the Spectra stylesheets. Comments are stripped first so example
    placeholders like ``[data-theme="name"]{ ... }`` in the file header
    don't show up as orphans. The browse page paints from these rules,
    so any registry entry without a matching one will show an unstyled
    card and any rule without a registry entry will be unreachable
    from the picker."""
    style_dir = REPO_ROOT / "static" / "style"
    ids: set[str] = set()
    for fname in _SPECTRA_FILES:
        text = (style_dir / fname).read_text(encoding="utf-8")
        stripped = _CSS_COMMENT_RE.sub("", text)
        ids.update(_CSS_THEME_RE.findall(stripped))
    return ids


def test_every_registered_theme_has_a_css_block() -> None:
    """A registry entry with no matching ``[data-theme=...]`` rule
    would render an unstyled (and broken-looking) card. Catch that on
    every commit so the catalogue can never advertise vapourware."""
    css_ids = _css_theme_ids()
    missing = [t.id for t in BUNDLED_THEMES if t.id not in css_ids]
    assert not missing, f"registry has themes with no CSS block: {missing!r}"


def test_every_css_block_has_a_registry_entry() -> None:
    """The reverse: a Spectra CSS block that no registry entry knows
    about is unreachable from the picker. Either add a Theme(...) row
    to BUNDLED_THEMES or drop the orphan CSS rule."""
    registry_ids = {t.id for t in BUNDLED_THEMES}
    css_ids = _css_theme_ids()
    orphaned = sorted(css_ids - registry_ids)
    assert not orphaned, f"CSS blocks with no registry entry: {orphaned!r}"


def test_theme_ids_are_unique() -> None:
    ids = [t.id for t in BUNDLED_THEMES]
    assert len(ids) == len(set(ids))


def test_picker_label_includes_tagline_when_set() -> None:
    t = Theme(id="t", name="Light", family="light", tagline="warm paper")
    assert t.picker_label() == "Light · warm paper"


def test_picker_label_falls_back_to_name_when_no_tagline() -> None:
    t = Theme(id="t", name="Dracula", family="base16")
    assert t.picker_label() == "Dracula"


def test_picker_options_shape_matches_select_field_contract() -> None:
    """The ``select_field`` macro expects ``[{value, label, group}]``;
    pin the shape so a future "let's drop group" refactor of the
    registry surfaces here instead of producing a silently flat picker."""
    options = picker_options(list(BUNDLED_THEMES))
    assert options, "expected at least one bundled theme"
    sample = options[0]
    assert set(sample.keys()) == {"value", "label", "group"}


def test_picker_options_preserve_registry_order() -> None:
    """Order matters: the page editor's dropdown is meant to land the
    safe defaults (Light, Dark) at the top. A future sort-by-name in
    picker_options would silently change every user's first option."""
    options = picker_options(list(BUNDLED_THEMES))
    assert options[0]["value"] == BUNDLED_THEMES[0].id
    assert options[-1]["value"] == BUNDLED_THEMES[-1].id


def test_themes_by_family_groups_in_declared_family_order() -> None:
    grouped = themes_by_family(list(BUNDLED_THEMES))
    # Each declared family bucket exists even if empty (the browse
    # page conditionally renders only non-empty ones, but the dict
    # must have every key so an iteration over FAMILY_ORDER doesn't
    # KeyError).
    for family in FAMILY_ORDER:
        assert family in grouped


def test_build_registry_appends_user_themes_after_bundled() -> None:
    """User themes always trail bundled ones so the picker's "safe
    default at the top" property holds even after a user adds 50
    custom themes."""
    user = [Theme(id="user-foo", name="Foo", family="user", user=True)]
    out = build_registry(user_themes=user)
    assert out[: len(BUNDLED_THEMES)] == list(BUNDLED_THEMES)
    assert out[-1] == user[0]


def test_default_themes_css_file_exists() -> None:
    """Trivial existence guard so the registry isn't pointing at a
    stylesheet that's been moved or renamed."""
    for fname in _SPECTRA_FILES:
        assert (REPO_ROOT / "static" / "style" / fname).is_file()
