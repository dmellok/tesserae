"""Disk-backed store for user-authored palette profiles.

Each profile lives in its own ``data/palette_profiles/<slug>.json``
file, mirroring the shape of :mod:`app.state.user_themes` (the
themes browser has the same "bundled read-only + user editable"
structure). No index file: the directory listing IS the index, so
saving / deleting a profile doesn't require touching a second file.

The store is intentionally simple and file-scoped rather than sitting
in :mod:`app.state.settings_store` because profiles are chunky JSON
documents users trade around: the "export" flow is just downloading
the raw file, and "import" is parsing a POST body against the schema.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.palette_profiles.schema import PaletteProfile, profile_from_dict

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class PaletteProfileStore:
    """List / load / save / delete user-authored profiles under
    ``data/palette_profiles/``. Constructor takes the data root so tests
    can point at a tmp directory."""

    def __init__(self, data_root: Path) -> None:
        self.dir = Path(data_root) / "palette_profiles"

    def list_all(self) -> list[PaletteProfile]:
        """Every user profile on disk, ordered by ``saved_at`` desc so
        the picker shows the most recently edited first. Files that
        fail to parse are dropped silently rather than crashing the
        picker; log-worthy but not fatal."""
        if not self.dir.exists():
            return []
        out: list[PaletteProfile] = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            slug_from_stem = path.stem
            if not raw.get("slug"):
                raw["slug"] = slug_from_stem
            out.append(profile_from_dict(raw))
        out.sort(key=lambda p: p.saved_at or "", reverse=True)
        return out

    def load(self, slug: str) -> PaletteProfile | None:
        """Load one profile by slug. Returns ``None`` when the file is
        missing or malformed rather than raising, so callers can fall
        through to the bundled table without a try/except."""
        if not _SLUG_RE.match(slug):
            return None
        path = self.dir / f"{slug}.json"
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        raw.setdefault("slug", slug)
        return profile_from_dict(raw)

    def save(self, profile: PaletteProfile) -> PaletteProfile:
        """Write ``profile`` to disk. Rejects bundled slugs and slugs
        that fail the safety regex; returns the (possibly-normalised)
        profile so callers can pick up any changes without a re-read."""
        if profile.bundled:
            raise ValueError("cannot save over a bundled preset; use save-as-new")
        if not _SLUG_RE.match(profile.slug):
            raise ValueError(f"invalid slug {profile.slug!r}")
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{profile.slug}.json"
        path.write_text(
            json.dumps(profile.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return profile

    def delete(self, slug: str) -> bool:
        """Delete a user profile. Returns True when it existed, False
        when it didn't. Bundled slugs are refused."""
        if not _SLUG_RE.match(slug):
            return False
        path = self.dir / f"{slug}.json"
        if not path.exists():
            return False
        path.unlink()
        return True

    def slug_available(self, slug: str) -> bool:
        """Sanity check for the "Save as new" flow before writing."""
        if not _SLUG_RE.match(slug):
            return False
        return not (self.dir / f"{slug}.json").exists()


def slugify(name: str) -> str:
    """Deterministic ``name`` -> ``slug`` for the Save-as flow. Non-
    alphanumeric runs collapse to single hyphens; leading digits get a
    ``p-`` prefix so the slug regex accepts them; length caps at 64."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    if not cleaned:
        return "profile"
    if cleaned[0].isdigit():
        cleaned = f"p-{cleaned}"
    return cleaned[:64]
