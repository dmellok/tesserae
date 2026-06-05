"""User-themes store: persistence, id rules, CSS emission, atomic write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.state.user_themes import (
    USER_THEME_PREFIX,
    UserTheme,
    UserThemeStore,
    emit_css,
    slugify_name,
    valid_slug,
)

# -- slug helpers ------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Sunset", "sunset"),
        ("Cozy Cabin", "cozy-cabin"),
        ("  Trim me  ", "trim-me"),
        ("Spaces -- hyphens", "spaces-hyphens"),
        ("Mixed CASE", "mixed-case"),
        ("Unicode 🎨", "unicode"),
        ("", "theme"),
        ("---", "theme"),
    ],
)
def test_slugify_name_handles_user_input(name: str, expected: str) -> None:
    assert slugify_name(name) == expected


def test_slugify_name_caps_long_input_to_48_chars() -> None:
    """A 200-char display name would otherwise produce a 200-char slug
    that 1) doesn't survive the ``valid_slug`` regex and 2) bloats CSS
    selectors, URL paths, and dropdown labels. Cap the slug at 48
    chars and strip any trailing hyphen the truncation might leave."""
    huge = "X" * 200
    slug = slugify_name(huge)
    assert len(slug) <= 48
    assert slug.endswith("x")  # truncation didn't leave a hyphen tail


def test_slugify_name_truncated_slug_is_still_valid() -> None:
    """The capped slug must still pass ``valid_slug`` so the create
    route doesn't reject themes generated from long names."""
    name = "supercalifragilisticexpialidocious " * 6
    slug = slugify_name(name)
    assert valid_slug(slug)


@pytest.mark.parametrize(
    "slug, ok",
    [
        ("sunset", True),
        ("cozy-cabin", True),
        ("a1", True),
        ("ab", True),
        ("a", False),  # too short
        ("-bad", False),  # leading hyphen
        ("bad-", False),  # trailing hyphen
        ("UPPER", False),
        ("has spaces", False),
        ("emoji-🎨", False),
        ("a" * 50, False),  # too long
    ],
)
def test_valid_slug_enforces_id_shape(slug: str, ok: bool) -> None:
    assert valid_slug(slug) is ok


# -- store CRUD --------------------------------------------------------


def test_empty_store_lists_nothing(tmp_path: Path) -> None:
    store = UserThemeStore(tmp_path / "user.json")
    assert store.list_all() == []
    # No file is written until something is saved — saves the user's
    # data/ directory from a redundant empty file.
    assert not (tmp_path / "user.json").exists()


def test_save_roundtrips_theme(tmp_path: Path) -> None:
    store = UserThemeStore(tmp_path / "user.json")
    theme = UserTheme(id="user-sunset", name="Sunset")
    store.save(theme)
    # Reload via a fresh store to confirm persistence, not just memory.
    fresh = UserThemeStore(tmp_path / "user.json")
    assert [t.id for t in fresh.list_all()] == ["user-sunset"]
    assert fresh.get("user-sunset") == theme


def test_save_replaces_by_id(tmp_path: Path) -> None:
    store = UserThemeStore(tmp_path / "user.json")
    store.save(UserTheme(id="user-x", name="One", mode="light"))
    store.save(UserTheme(id="user-x", name="One renamed", mode="dark"))
    assert len(store.list_all()) == 1
    assert store.get("user-x").name == "One renamed"
    assert store.get("user-x").mode == "dark"


def test_save_rejects_non_prefixed_id(tmp_path: Path) -> None:
    """Anything without the ``user-`` prefix could collide with a
    bundled theme. The store enforces the prefix so a programmer error
    in a route can't slip an unprefixed id onto disk."""
    store = UserThemeStore(tmp_path / "user.json")
    with pytest.raises(ValueError, match="must start with"):
        store.save(UserTheme(id="light", name="Hijack"))


def test_delete_removes_and_reports(tmp_path: Path) -> None:
    store = UserThemeStore(tmp_path / "user.json")
    store.save(UserTheme(id="user-a", name="A"))
    store.save(UserTheme(id="user-b", name="B"))
    assert store.delete("user-a") is True
    assert store.delete("user-a") is False  # already gone
    assert [t.id for t in store.list_all()] == ["user-b"]


def test_unique_id_for_avoids_in_store_collision(tmp_path: Path) -> None:
    store = UserThemeStore(tmp_path / "user.json")
    store.save(UserTheme(id="user-sunset", name="Sunset"))
    assert store.unique_id_for("Sunset") == "user-sunset-2"
    store.save(UserTheme(id="user-sunset-2", name="Sunset 2"))
    assert store.unique_id_for("Sunset") == "user-sunset-3"


def test_unique_id_for_avoids_reserved_ids(tmp_path: Path) -> None:
    """The create-route passes bundled ids as ``reserved`` so a user
    can't accidentally produce ``user-light`` and later confuse
    themselves by remembering ``light`` was the bundled name."""
    store = UserThemeStore(tmp_path / "user.json")
    assert store.unique_id_for("Custom", reserved={"user-custom"}) == "user-custom-2"


# -- on-disk format ----------------------------------------------------


def test_corrupt_file_yields_empty_store(tmp_path: Path) -> None:
    """A user editing user.json by hand and shipping invalid JSON
    shouldn't crash boot. The store loads what it can and leaves the
    file alone so the user can fix it without losing entries that
    were never readable to begin with."""
    bad = tmp_path / "user.json"
    bad.write_text("{ this isn't JSON", encoding="utf-8")
    store = UserThemeStore(bad)
    assert store.list_all() == []
    # File contents untouched — no destructive auto-repair.
    assert bad.read_text(encoding="utf-8") == "{ this isn't JSON"


def test_on_disk_drops_token_fields_classvar(tmp_path: Path) -> None:
    """``TOKEN_FIELDS`` is a class-level tuple, not an instance field —
    it must never appear in the JSON payload (would just bloat the
    file and confuse manual editors)."""
    store = UserThemeStore(tmp_path / "user.json")
    store.save(UserTheme(id="user-x", name="X"))
    raw = json.loads((tmp_path / "user.json").read_text(encoding="utf-8"))
    assert raw and "TOKEN_FIELDS" not in raw[0]


def test_extra_keys_on_disk_are_ignored(tmp_path: Path) -> None:
    """Forward-compat: a future field added to UserTheme means older
    Tesserae instances reading the file should still load known data
    instead of crashing."""
    path = tmp_path / "user.json"
    path.write_text(
        json.dumps([{"id": "user-x", "name": "X", "future_field": "ignored"}]),
        encoding="utf-8",
    )
    store = UserThemeStore(path)
    assert [t.id for t in store.list_all()] == ["user-x"]


# -- CSS emission ------------------------------------------------------


def test_emit_css_empty_store_returns_placeholder(tmp_path: Path) -> None:
    store = UserThemeStore(tmp_path / "user.json")
    css = emit_css(store)
    assert "No user themes" in css


def test_emit_css_renders_data_theme_block_per_user_theme(tmp_path: Path) -> None:
    store = UserThemeStore(tmp_path / "user.json")
    store.save(
        UserTheme(
            id="user-sunset",
            name="Sunset",
            bg="#FFE6CC",
            text_primary="#3A1A00",
        )
    )
    css = emit_css(store)
    assert '[data-theme="user-sunset"]' in css
    assert "--bg: #FFE6CC;" in css
    assert "--text-primary: #3A1A00;" in css
    # Every theme emits the --icon derivation so widgets reading
    # var(--icon) keep painting.
    assert "--icon: var(--text-primary);" in css


def test_emit_css_includes_font_family_when_set(tmp_path: Path) -> None:
    store = UserThemeStore(tmp_path / "user.json")
    store.save(
        UserTheme(
            id="user-x",
            name="X",
            font_family='"Inter", system-ui, sans-serif',
        )
    )
    css = emit_css(store)
    assert '--font-family: "Inter", system-ui, sans-serif;' in css


def test_emit_css_omits_font_family_when_blank(tmp_path: Path) -> None:
    store = UserThemeStore(tmp_path / "user.json")
    store.save(UserTheme(id="user-x", name="X", font_family=None))
    css = emit_css(store)
    # Token blocks should still contain the colour entries but no
    # --font-family line, so widgets fall back to the bundled
    # cascade's default.
    assert "--font-family" not in css


def test_emit_css_order_matches_save_order(tmp_path: Path) -> None:
    """The browse page lists user themes in save order, so the CSS
    cascade has to follow suit — otherwise an early save could clobber
    a later one through specificity surprises (currently unlikely
    since each theme block is selector-uniquely keyed, but worth
    keeping deterministic)."""
    store = UserThemeStore(tmp_path / "user.json")
    store.save(UserTheme(id="user-first", name="First"))
    store.save(UserTheme(id="user-second", name="Second"))
    css = emit_css(store)
    assert css.index('"user-first"') < css.index('"user-second"')


# -- registry-theme adapter -------------------------------------------


def test_to_registry_theme_marks_user_family(tmp_path: Path) -> None:
    """The browse page filters by family. Pin that user-saved themes
    land in the ``user`` bucket so the chip count + grouping in the
    template light up correctly."""
    t = UserTheme(id=f"{USER_THEME_PREFIX}x", name="X").to_registry_theme()
    assert t.family == "user"
    assert t.user is True


# -- auto_soft_tints field --------------------------------------------


def test_auto_soft_tints_defaults_false(tmp_path: Path) -> None:
    """New themes have the auto-soft toggle off so the user opts in
    rather than getting derived values they didn't ask for."""
    t = UserTheme(id=f"{USER_THEME_PREFIX}x", name="X")
    assert t.auto_soft_tints is False


def test_auto_soft_tints_round_trips_through_store(tmp_path: Path) -> None:
    store = UserThemeStore(tmp_path / "user.json")
    store.save(UserTheme(id=f"{USER_THEME_PREFIX}x", name="X", auto_soft_tints=True))
    fresh = UserThemeStore(tmp_path / "user.json")
    saved = fresh.get(f"{USER_THEME_PREFIX}x")
    assert saved is not None
    assert saved.auto_soft_tints is True


def test_emit_css_does_not_include_auto_soft_tints_flag(tmp_path: Path) -> None:
    """``auto_soft_tints`` is a builder preference, not a CSS variable
    — the emitter must skip it so it doesn't leak into the stylesheet
    as a meaningless ``--auto-soft-tints`` rule."""
    store = UserThemeStore(tmp_path / "user.json")
    store.save(UserTheme(id=f"{USER_THEME_PREFIX}x", name="X", auto_soft_tints=True))
    css = emit_css(store)
    assert "auto-soft" not in css.lower()
