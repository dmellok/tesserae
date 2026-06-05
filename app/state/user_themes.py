"""File-backed user-themes store.

User themes live at ``data/themes/user.json`` as a flat list. Each
entry carries the 20 Spectra colour tokens (3 surfaces + 3 text + 1
edge + 6 accents × 2 (base + soft) + 1 on-accent), the chosen mode
(``light`` / ``dark``), the optional font-family, and the user's
display name.

The browse page enumerates them through :func:`UserThemeStore.list_all`
which returns ``Theme`` records compatible with the bundled-theme
registry so both render through the same template path. The CSS for
each user theme is emitted at ``GET /themes/user.css`` by
:func:`emit_css` — that route is the only place that walks the token
dict, so any token-list change ripples through one function instead
of N templates.

The store is process-local and synchronous (single-user appliance);
no DB, no migrations. Atomic write via tmp + ``Path.replace`` so a
crash mid-save can't corrupt the file.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, ClassVar

from app.state.theme_registry import Theme

# Slug prefix for every user theme id. Picked so user themes can never
# shadow a bundled id (``light`` / ``dark`` / ``base16-*``) by accident
# — the form-side slug sanitizer adds this prefix after stripping it
# if the user re-entered it.
USER_THEME_PREFIX = "user-"

# Slug rule: lowercase alphanumeric + hyphen, 2–48 chars after the
# ``user-`` prefix. Forbids leading / trailing hyphens to keep the
# resulting CSS selector legible. The 2-char floor means the picker's
# tagline column has room to render something sensible alongside.
_SLUG_MAX = 48
_SLUG_RE = re.compile(rf"^[a-z0-9][a-z0-9-]{{0,{_SLUG_MAX - 2}}}[a-z0-9]$")
_SLUG_SAFE_NAME = re.compile(r"[^a-z0-9]+")

# Default mode → on-accent colour. Used for a brand-new user theme when
# the user hasn't picked anything yet; the builder UI seeds the form
# with these so a save without further edits still produces a usable
# theme.
DEFAULT_ON_ACCENT_BY_MODE: dict[str, str] = {
    "light": "#FFFFFF",
    "dark": "#141310",
}


@dataclass
class UserTheme:
    """A single user-saved theme.

    Field names match the Spectra CSS token names with the leading
    ``--`` stripped, except ``font_family`` which becomes
    ``--font-family`` at CSS-emission time. Accent pairs are flattened
    into the dataclass for an easy form round-trip; the CSS emitter
    re-pairs them on output.

    ``mode`` is informational only — it doesn't change the cascade,
    but the builder seeds different defaults for light vs dark and the
    browse page can filter on it later.
    """

    id: str
    name: str
    mode: str = "light"  # "light" | "dark"
    font_family: str | None = None

    bg: str = "#FFFFFF"
    surface: str = "#F7F5F0"
    surface_sunken: str = "#E1DDD2"

    text_primary: str = "#1B1A16"
    text_secondary: str = "#4D4A42"
    text_muted: str = "#837F73"

    edge: str = "#B6B1A4"

    accent_1: str = "#A84B2A"
    accent_1_soft: str = "#E9D6CC"
    accent_2: str = "#9A7414"
    accent_2_soft: str = "#EAE0C4"
    accent_3: str = "#4F6F36"
    accent_3_soft: str = "#D9E2C9"
    accent_4: str = "#256E6B"
    accent_4_soft: str = "#CCE0DD"
    accent_5: str = "#3F5A88"
    accent_5_soft: str = "#D2DCEA"
    accent_6: str = "#7E4068"
    accent_6_soft: str = "#E5D2DF"

    on_accent: str = "#F7F5F0"

    # UI preference: when true, the builder auto-derives every
    # ``accent_*_soft`` from its base accent + bg whenever the user
    # edits either, and shows the soft inputs as read-only. Persisted
    # so the preference survives page reloads. Doesn't affect the CSS
    # output — the soft fields still emit normally; this flag only
    # changes how the builder treats them.
    auto_soft_tints: bool = False

    # The colour-token field names in the order they appear on disk and
    # in the CSS output. Excludes the metadata trio (id / name / mode /
    # font_family). Kept as a ClassVar so dataclass treats it as
    # class-level (no per-instance field, no spurious constructor arg).
    TOKEN_FIELDS: ClassVar[tuple[str, ...]] = (
        "bg",
        "surface",
        "surface_sunken",
        "text_primary",
        "text_secondary",
        "text_muted",
        "edge",
        "accent_1",
        "accent_1_soft",
        "accent_2",
        "accent_2_soft",
        "accent_3",
        "accent_3_soft",
        "accent_4",
        "accent_4_soft",
        "accent_5",
        "accent_5_soft",
        "accent_6",
        "accent_6_soft",
        "on_accent",
    )

    def to_registry_theme(self) -> Theme:
        """Adapt to a :class:`Theme` so the browse page + picker treat
        user themes identically to bundled ones."""
        return Theme(id=self.id, name=self.name, family="user", user=True)

    def to_disk(self) -> dict[str, Any]:
        """Shape for JSON persistence."""
        return asdict(self)

    def emit_css(self) -> str:
        """Render this theme as one ``[data-theme="user-<id>"] { ... }``
        block. CSS variable names are derived from field names by
        replacing ``_`` with ``-`` and prefixing ``--``."""
        lines = [f'[data-theme="{self.id}"]{{']
        if self.font_family:
            # Quote the user-supplied font family verbatim — Spectra's
            # other themes use a comma-separated stack starting with the
            # specific family, so the user is responsible for that shape.
            lines.append(f"  --font-family: {self.font_family};")
        for key in self.TOKEN_FIELDS:
            css_name = "--" + key.replace("_", "-")
            value = getattr(self, key)
            lines.append(f"  {css_name}: {value};")
        # Icon always derives from text-primary in the Spectra cascade,
        # so we don't ask the user to set it; emit the derivation.
        lines.append("  --icon: var(--text-primary);")
        lines.append("}")
        return "\n".join(lines)


def slugify_name(name: str) -> str:
    """Turn a free-form display name into a slug suitable for the
    ``user-<slug>`` id. Empty / all-symbol names degrade to ``theme``.

    Capped at :data:`_SLUG_MAX` chars to keep CSS selectors, URL
    paths, and dropdown labels readable. A trailing hyphen left by
    the truncation gets stripped — slugs always end on an
    alphanumeric so the matching ``valid_slug`` regex accepts them.
    """
    slug = _SLUG_SAFE_NAME.sub("-", name.lower()).strip("-")
    if len(slug) > _SLUG_MAX:
        slug = slug[:_SLUG_MAX].rstrip("-")
    return slug or "theme"


def valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug))


class UserThemeStore:
    """Thread-safe JSON store. Operations: list / get / save / delete.

    The store is keyed by full id (``user-<slug>``); the caller is
    responsible for prefixing user input. See
    :func:`unique_id_for` for the canonical "give me a non-colliding id
    based on this name" helper used by the create route.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._themes: list[UserTheme] = []
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        with self._lock:
            self._themes = []
            if not self._path.exists():
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                # Corrupt file: refuse to silently lose data. Keep the
                # in-memory list empty so the rest of the app boots,
                # but leave the file alone so the user can repair it.
                return
            if not isinstance(raw, list):
                return
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                try:
                    self._themes.append(self._from_dict(entry))
                except (KeyError, TypeError, ValueError):
                    continue

    @staticmethod
    def _from_dict(raw: dict[str, Any]) -> UserTheme:
        """Construct a :class:`UserTheme` from a JSON dict, only pulling
        recognised field names. Forward-compatible: unknown keys are
        ignored so a future schema bump on disk doesn't crash older
        code paths."""
        known = {f for f in UserTheme.__dataclass_fields__ if f != "TOKEN_FIELDS"}
        kwargs = {k: raw[k] for k in known if k in raw}
        return UserTheme(**kwargs)

    def _flush(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            payload = json.dumps([t.to_disk() for t in self._themes], indent=2) + "\n"
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self._path)

    def list_all(self) -> list[UserTheme]:
        """Snapshot the current themes (caller-mutable copy)."""
        with self._lock:
            return list(self._themes)

    def list_as_registry_themes(self) -> list[Theme]:
        """Adapter for the registry/browse-page surface. Order matches
        on-disk order so a user's curated arrangement isn't reshuffled."""
        return [t.to_registry_theme() for t in self.list_all()]

    def get(self, theme_id: str) -> UserTheme | None:
        with self._lock:
            for t in self._themes:
                if t.id == theme_id:
                    return t
            return None

    def save(self, theme: UserTheme) -> None:
        """Insert or replace by id. Atomic flush after the in-memory
        update so a write failure leaves the on-disk file at its
        pre-call state."""
        if not theme.id.startswith(USER_THEME_PREFIX):
            raise ValueError(f"user theme id must start with {USER_THEME_PREFIX!r}")
        with self._lock:
            for i, existing in enumerate(self._themes):
                if existing.id == theme.id:
                    self._themes[i] = theme
                    self._flush()
                    return
            self._themes.append(theme)
            self._flush()

    def delete(self, theme_id: str) -> bool:
        with self._lock:
            for i, t in enumerate(self._themes):
                if t.id == theme_id:
                    self._themes.pop(i)
                    self._flush()
                    return True
            return False

    def unique_id_for(self, name: str, reserved: Iterable[str] = ()) -> str:
        """Given a free-form display name, return a non-colliding
        ``user-<slug>`` id. Adds a numeric suffix when the desired slug
        is taken, in-store or in ``reserved`` (bundled ids the caller
        passes in to keep user themes from shadowing them)."""
        base = slugify_name(name)
        taken = {t.id for t in self.list_all()}
        taken.update(reserved)
        candidate = f"{USER_THEME_PREFIX}{base}"
        if candidate not in taken:
            return candidate
        n = 2
        while True:
            attempt = f"{USER_THEME_PREFIX}{base}-{n}"
            if attempt not in taken:
                return attempt
            n += 1


def emit_css(store: UserThemeStore) -> str:
    """Render every user theme as a single ``user.css`` payload. The
    `/themes/user.css` route serves this directly; ``compose.html`` and
    the themes browse page both load it so the cascade matches what's
    on disk without a Python round-trip per render."""
    blocks = [t.emit_css() for t in store.list_all()]
    if not blocks:
        # Empty file is fine — browsers handle 200 + empty body cleanly
        # and the conditional <link> tag in templates can stay
        # unconditional this way.
        return "/* No user themes saved. */\n"
    header = "/* User themes. Generated; edit via Settings → Themes → Builder. */\n"
    return header + "\n\n".join(blocks) + "\n"
