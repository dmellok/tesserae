"""File-backed settings store.

One JSON file at ``data/core/settings.json`` holds every persisted setting
the app cares about — segmented into named sections:

* ``app``     — base_url, session secret, anything app-wide
* ``auth``    — password hash + salt (PBKDF2-HMAC-SHA256)
* ``broker``  — MQTT host/port/credentials
* ``plugins.<id>`` — per-plugin settings declared in plugin.json's ``settings``
* ``renderers.<id>`` — per-renderer settings declared in renderer.json
* ``devices.<id>``   — per-device settings (M5)

Secret handling: when a setting field declares ``secret: true`` in its
manifest, the value is stored on disk under ``<name>_secret`` instead of
``<name>``. That way a quick ``grep -i secret data/core/settings.json``
makes every sensitive value visually obvious. The store exposes two reads:

* ``get_for_runtime(...)`` returns real values keyed by the manifest name
  (drops the ``_secret`` suffix). Used by the push pipeline, plugin
  ``fetch()``, etc.
* ``get_for_admin(...)`` returns the same but with secret values masked.
  Used when shipping settings back to the editor / settings page.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SECRET_MASK = "********"


def _disk_key(field_name: str, *, secret: bool) -> str:
    """Translate manifest field name to disk key. Secret fields gain the
    ``_secret`` suffix; non-secret fields keep their name as-is."""
    return f"{field_name}_secret" if secret else field_name


class SettingsStore:
    """Thread-safe, file-backed settings store.

    Atomicity: every save rewrites the whole file via tmp + rename. The file
    is small (kilobytes) and writes are infrequent — no journaling needed.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text())
        if not isinstance(raw, dict):
            raise ValueError(f"{self._path} must contain a JSON object")
        self._data = raw

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True))
        tmp.replace(self._path)

    # -- generic section access -------------------------------------------

    def get_section(self, section: str) -> dict[str, Any]:
        """Return the raw, on-disk contents of a section (with ``_secret``
        keys intact). Most callers want ``get_for_runtime`` / ``get_for_admin``
        instead — this is the raw access used by tests and the auth module."""
        with self._lock:
            section_data = self._data.get(section, {})
            return dict(section_data) if isinstance(section_data, dict) else {}

    def update_section(self, section: str, values: dict[str, Any]) -> None:
        """Replace the entire section with ``values``. The auth + app
        sections use this; per-plugin / per-renderer flows go through
        ``update_for_namespace`` so they get the secret-rename treatment."""
        with self._lock:
            self._data[section] = dict(values)
            self._flush()

    def patch_section(self, section: str, values: dict[str, Any]) -> None:
        """Merge ``values`` into the existing section. Existing keys not
        present in ``values`` are preserved."""
        with self._lock:
            existing = self._data.get(section, {})
            if not isinstance(existing, dict):
                existing = {}
            existing.update(values)
            self._data[section] = existing
            self._flush()

    # -- manifest-aware access (plugins / renderers / devices) -------------

    def get_for_runtime(
        self,
        namespace: str,
        item_id: str,
        manifest_settings: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        """Return real values keyed by manifest field name.

        Looks up ``self._data[namespace][item_id]``, applies defaults from
        ``manifest_settings``, and strips the ``_secret`` suffix from any
        secret-flagged field so callers see them under their declared name.
        """
        on_disk = self._get_item(namespace, item_id)
        out: dict[str, Any] = {}
        for field in manifest_settings:
            name = str(field["name"])
            is_secret = bool(field.get("secret"))
            disk = _disk_key(name, secret=is_secret)
            if disk in on_disk:
                out[name] = on_disk[disk]
            elif "default" in field:
                out[name] = field["default"]
        return out

    def get_for_admin(
        self,
        namespace: str,
        item_id: str,
        manifest_settings: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        """Like ``get_for_runtime`` but secret values are replaced with
        ``SECRET_MASK`` if a value is present (or left absent if not). Use
        this when shipping settings back over the wire to the UI."""
        on_disk = self._get_item(namespace, item_id)
        out: dict[str, Any] = {}
        for field in manifest_settings:
            name = str(field["name"])
            is_secret = bool(field.get("secret"))
            disk = _disk_key(name, secret=is_secret)
            if disk in on_disk:
                out[name] = SECRET_MASK if is_secret else on_disk[disk]
            elif "default" in field:
                out[name] = field["default"]
        return out

    def update_for_namespace(
        self,
        namespace: str,
        item_id: str,
        values: dict[str, Any],
        manifest_settings: Iterable[dict[str, Any]],
    ) -> None:
        """Persist ``values`` (keyed by manifest field name) into
        ``namespace.item_id``, applying the secret-rename convention.

        Quietly drops any incoming key that isn't declared in
        ``manifest_settings`` — the UI can post extras (CSRF token, etc.)
        without polluting on-disk state.

        Secret fields whose incoming value is ``SECRET_MASK`` are kept as
        their existing on-disk value (the UI displays masks, so a re-submit
        without changes shouldn't blow the secret away)."""
        field_index = {str(f["name"]): f for f in manifest_settings}
        with self._lock:
            ns = self._data.setdefault(namespace, {})
            if not isinstance(ns, dict):
                ns = {}
                self._data[namespace] = ns
            existing = ns.get(item_id, {})
            if not isinstance(existing, dict):
                existing = {}
            merged: dict[str, Any] = dict(existing)
            for name, value in values.items():
                field = field_index.get(name)
                if field is None:
                    continue
                is_secret = bool(field.get("secret"))
                disk = _disk_key(name, secret=is_secret)
                if is_secret and value == SECRET_MASK:
                    # User left the masked value alone — don't overwrite.
                    continue
                merged[disk] = value
            ns[item_id] = merged
            self._flush()

    def _get_item(self, namespace: str, item_id: str) -> dict[str, Any]:
        with self._lock:
            ns = self._data.get(namespace, {})
            if not isinstance(ns, dict):
                return {}
            item = ns.get(item_id, {})
            return dict(item) if isinstance(item, dict) else {}
