"""Install identifier: a random UUID generated on first startup and
persisted at ``data/core/install_id.json``.

Widgets that declare ``needs_install_id`` or ``needs_scoped_id`` in their
plugin manifest receive the value (or a widget-scoped derivation) in the
render context, so features that require a stable per-install identity
(the planned tamagotchi pet, dashboard traveler, guestbook, etc.) have
something to key against without Tesserae ever storing anything tied to
the operator's real identity.

The UUID is a random string with no connection to the operator, their
hardware, IP address, HA account, or any external service. Users can
regenerate it from Settings, at the cost of losing whatever per-install
state widgets have accumulated (a pet's history, a traveler's home
waypoint, etc.).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path


def install_id_path(data_root: Path) -> Path:
    """Where the install-id file lives inside ``data_root``."""
    return data_root / "core" / "install_id.json"


def load_or_create(data_root: Path) -> str:
    """Return the current install id.

    Generates and persists a fresh v4 UUID on first startup, or when the
    file is missing or unreadable. The file is created with the parent
    ``core/`` directory as needed, so a fresh checkout (or an upgrade
    from a version that predates the install-id file) does the right
    thing on first run.
    """
    path = install_id_path(data_root)
    existing = _read_existing(path)
    if existing is not None:
        return existing
    return _generate_and_write(path)


def regenerate(data_root: Path) -> str:
    """Force a new install id and persist it, replacing any prior value.

    Callers surfacing this in a UI should warn the user that the
    regeneration resets any per-install state on the widget side (a pet's
    identity, a traveler's home waypoint, a guestbook signer, etc.).
    """
    path = install_id_path(data_root)
    return _generate_and_write(path)


def read_metadata(data_root: Path) -> dict[str, str] | None:
    """Return the on-disk metadata (``id``, ``created_at``, ``note``) or
    ``None`` when the file is missing. Used by the Settings UI to show
    the current identifier plus when it was minted."""
    path = install_id_path(data_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        return None
    if not _is_valid_uuid(data["id"]):
        return None
    return {
        "id": str(data["id"]),
        "created_at": str(data.get("created_at") or ""),
        "note": str(data.get("note") or ""),
    }


def scoped_id(install_id: str, scope: str) -> str:
    """Return a widget-scoped derivation of ``install_id`` for ``scope``.

    Different widgets pass different ``scope`` values (typically their
    plugin id), so each gets a distinct derived identifier that can't be
    correlated with any other widget's identifier by an external service.
    Widgets that legitimately need cross-widget correlation (the shared
    world features: pet, traveler, guestbook, postcards) request the raw
    install id via their manifest instead; everything else defaults to
    this scoped variant.

    Returns a 32-character hex string (128 bits, plenty of entropy).
    """
    digest = hashlib.sha256(f"{install_id}:{scope}".encode()).hexdigest()
    return digest[:32]


def _read_existing(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    existing = data.get("id")
    if isinstance(existing, str) and _is_valid_uuid(existing):
        return existing
    return None


def _generate_and_write(path: Path) -> str:
    new_id = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": new_id,
        "created_at": _now_iso(),
        "note": (
            "Random UUID persisted on first startup. Regenerable from"
            " Settings -> Privacy. Not tied to your identity, hardware, or"
            " Tesserae account."
        ),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return new_id


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_valid_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
    except (ValueError, TypeError):
        return False
    return True
