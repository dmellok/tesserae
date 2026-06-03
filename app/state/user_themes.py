"""File-backed user theme store.

Built-in themes ship in ``plugins/themes_core/plugin.json`` and are
immutable. User themes live alongside in ``data/plugins/themes_core/user.json``
and are editable through the /themes builder page.

The PluginRegistry loads both — see ``app.plugin_loader`` — and presents
them as one flat ``themes`` dict so widgets / pages / cells don't care
where a theme came from.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Every theme palette MUST declare these tokens. The composer injects them
# as --theme-* CSS variables on each cell; widgets count on every key
# being present, so we validate here rather than at every cell paint.
PALETTE_TOKENS: tuple[str, ...] = (
    "bg",
    "surface",
    "surface2",
    "fg",
    "fgSoft",
    "muted",
    "accent",
    "accent2",
    "accent3",
    "accentSoft",
    "divider",
    "danger",
    "warn",
    "ok",
    "info",
)

VALID_MODES: frozenset[str] = frozenset({"light", "dark"})


@dataclass
class UserTheme:
    id: str
    name: str
    mode: str  # "light" | "dark"
    palette: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> UserTheme:
        return cls(
            id=str(raw["id"]),
            name=str(raw["name"]),
            mode=str(raw.get("mode") or "light"),
            palette={k: str(v) for k, v in (raw.get("palette") or {}).items()},
        )

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "mode": self.mode, "palette": dict(self.palette)}


class UserThemeStore:
    """JSON-backed list of user themes. Atomic-rename flush, same pattern
    as PageStore / ScheduleStore."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _load_raw(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        return [d for d in data if isinstance(d, dict)]

    def _save_raw(self, raw: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)

    def load(self) -> list[UserTheme]:
        with self._lock:
            out: list[UserTheme] = []
            for raw in self._load_raw():
                try:
                    out.append(UserTheme.from_raw(raw))
                except (KeyError, TypeError, ValueError):
                    continue
            return out

    def get(self, theme_id: str) -> UserTheme | None:
        for t in self.load():
            if t.id == theme_id:
                return t
        return None

    def upsert(self, theme: UserTheme) -> None:
        with self._lock:
            raw = self._load_raw()
            for i, existing in enumerate(raw):
                if existing.get("id") == theme.id:
                    raw[i] = theme.to_dict()
                    break
            else:
                raw.append(theme.to_dict())
            self._save_raw(raw)

    def delete(self, theme_id: str) -> bool:
        with self._lock:
            raw = self._load_raw()
            kept = [r for r in raw if r.get("id") != theme_id]
            if len(kept) == len(raw):
                return False
            self._save_raw(kept)
            return True


def validate_palette(palette: dict[str, str]) -> str | None:
    """Return a human-readable error if the palette is malformed, None
    if ok. Used by the /themes form before save."""
    missing = [t for t in PALETTE_TOKENS if t not in palette]
    if missing:
        return f"missing tokens: {', '.join(missing)}"
    for token, value in palette.items():
        if not isinstance(value, str) or not value.startswith("#") or len(value) not in (4, 7):
            return f"{token}={value!r} is not a #hex colour"
    return None


def slug_id(name: str) -> str:
    """Derive a stable id from a free-text name. Stripped + lowercased +
    non-alphanumerics → '_'."""
    import re

    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "theme"
