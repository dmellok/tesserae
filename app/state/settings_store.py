"""File-backed settings store.

One JSON file at ``data/core/settings.json`` holds every persisted setting
the app cares about, segmented into named sections:

* ``app``    , base_url, session secret, anything app-wide
* ``auth``   , password hash + salt (PBKDF2-HMAC-SHA256)
* ``broker`` , MQTT host/port/credentials
* ``plugins.<id>``, per-plugin settings declared in plugin.json's ``settings``
* ``renderers.<id>``, per-renderer settings declared in renderer.json
* ``devices.<id>``  , per-device settings (M5)

Secret handling: when a setting field declares ``secret: true`` in its
manifest, the value is stored on disk under ``<name>_secret`` instead of
``<name>``. That way a quick ``grep -i secret data/core/settings.json``
makes every sensitive value visually obvious. The store exposes two reads:

* ``get_for_runtime(...)`` returns real values keyed by the manifest name
  (drops the ``_secret`` suffix). Used by the push pipeline, plugin
  ``fetch()``, etc.
* ``get_for_admin(...)`` returns the same but with secret values masked.
  Used when shipping settings back to the editor / settings page.

Encryption at rest (v0.49). When a ``SecretBox`` is wired into the store,
manifest-declared secret values are AES-GCM-wrapped on the way to disk
and unwrapped on the way back out, transparent to consumers. Legacy
plaintext values keep reading (the unwrap path is a no-op for non-prefixed
input) and get migrated to ciphertext the next time the field is saved.
The bootstrap secrets (``app.session_secret_secret``, ``auth.password_hash_secret``,
``broker.password_secret``) bypass this path: they're read with
``get_section`` directly, so they stay in their existing form.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.secret_box import SecretBox, SecretBoxError, is_wrapped

SECRET_MASK = "********"

logger = logging.getLogger(__name__)


def _disk_key(field_name: str, *, secret: bool) -> str:
    """Translate manifest field name to disk key. Secret fields gain the
    ``_secret`` suffix; non-secret fields keep their name as-is."""
    return f"{field_name}_secret" if secret else field_name


class SettingsStore:
    """Thread-safe, file-backed settings store.

    Atomicity: every save rewrites the whole file via tmp + rename. The file
    is small (kilobytes) and writes are infrequent, no journaling needed.
    """

    def __init__(self, path: Path, *, secret_box: SecretBox | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._secret_box = secret_box
        self._load()

    def set_secret_box(self, secret_box: SecretBox) -> None:
        """Inject a SecretBox after construction. Used by the app
        factory, which can only resolve the key once the session
        secret has been read out of the store. Subsequent reads /
        writes apply the wrap/unwrap treatment immediately, and the
        next save migrates any pre-existing plaintext secrets to
        ciphertext opportunistically (no separate walker)."""
        self._secret_box = secret_box

    def _maybe_unwrap(self, value: Any) -> Any:
        """Decrypt a stored secret if it's wrapped and we have a box;
        return as-is otherwise. Wrong-key / corrupt-payload errors
        propagate so the operator notices instead of silently getting
        an empty token at fetch time."""
        if self._secret_box is None or not is_wrapped(value):
            return value
        try:
            return self._secret_box.unwrap(str(value))
        except SecretBoxError:
            logger.exception("failed to unwrap secret in settings; refusing to fall through")
            raise

    def _maybe_wrap(self, value: Any) -> Any:
        """Encrypt a secret on its way to disk. No-op when no box is
        wired or when the value is non-string (e.g. an old config that
        snuck a non-string into a secret field, leave it alone rather
        than crash the save) or when it's already wrapped."""
        if self._secret_box is None or not isinstance(value, str) or is_wrapped(value):
            return value
        return self._secret_box.wrap(value)

    def _unwrap_tree(self, value: Any) -> Any:
        """Recursively unwrap ``_secret``-suffixed string values inside
        a dict tree. Used by ``get_section`` so plugins that read their
        own state directly (rather than going through the manifest-aware
        ``get_for_runtime``) still see plaintext. Non-dict / non-string
        nodes pass through unchanged; the wrap/unwrap pair is symmetric
        with the ``_disk_key`` suffix convention so we only touch the
        keys we'd have written ciphertext into.

        Per-value tolerance for decryption failures (v0.64.25, issue
        [#29](https://github.com/dmellok/tesserae/issues/29)): a single
        stale ciphertext anywhere in the section, typical culprit is
        ``TESSERAE_SECRET_KEY`` changing across container restarts, or
        a data-volume restore with a mismatched
        ``session_secret_secret``, used to cascade-fail the whole
        ``get_section()`` call. RealGandy hit this with the HA token
        re-entered cleanly but another plugin's older
        ``*_secret`` still wrapped under the old key, the first
        broken value blew up the read before any other plugin's
        fresh value could be seen. Now individual failures log +
        replace with empty string so each broken value surfaces
        independently downstream (the entity picker's "re-enter the
        secret" sentinel from v0.64.24, etc.). The manifest-aware
        ``get_for_runtime`` path stays strict so a caller that
        explicitly asked for a specific secret still gets a loud
        error instead of an empty string the plugin then 401s with."""
        if isinstance(value, dict):
            out: dict[Any, Any] = {}
            for k, v in value.items():
                if isinstance(k, str) and k.endswith("_secret") and isinstance(v, str):
                    try:
                        out[k] = self._maybe_unwrap(v)
                    except SecretBoxError:
                        logger.warning(
                            "settings: stored secret at key %r can't be decrypted "
                            "with the current SecretBox key; replacing with empty "
                            "string so the rest of the section reads. Re-enter the "
                            "secret through the relevant Settings page to repair.",
                            k,
                        )
                        out[k] = ""
                else:
                    out[k] = self._unwrap_tree(v)
            return out
        return value

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{self._path} must contain a JSON object")
        self._data = raw

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)

    # -- generic section access -------------------------------------------

    def get_section(self, section: str) -> dict[str, Any]:
        """Return the on-disk contents of a section (with ``_secret`` keys
        intact). Most callers want ``get_for_runtime`` / ``get_for_admin``
        instead; this is the raw-ish access used by the auth module and
        by plugin server modules that pull their own state without a
        manifest in hand.

        When a SecretBox is wired, ``_secret``-suffixed string values at
        any depth are unwrapped on the way out. Bootstrap secrets like
        ``app.session_secret_secret`` are stored unencrypted (they're key
        material themselves) and pass through unchanged because the
        unwrap path is a no-op for non-prefixed input.
        """
        with self._lock:
            section_data = self._data.get(section, {})
            if not isinstance(section_data, dict):
                return {}
            unwrapped = self._unwrap_tree(section_data)
            assert isinstance(unwrapped, dict)  # type narrowing for mypy
            return unwrapped

    def unreadable_secrets(self, section: str, item_id: str | None = None) -> set[str]:
        """Disk keys under ``section`` (or ``section.item_id``) whose stored
        ciphertext will not decrypt with the current key.

        The read paths deliberately turn an undecryptable secret into an
        empty string so one stale value can't take a whole section down with
        it. That keeps the app running and makes the value falsy everywhere,
        which is the safe outcome, but it leaves the admin UI unable to tell
        "never set" from "set, and unreadable since the key changed" -- and
        those need opposite things from the operator. This answers that
        question on demand rather than by tracking state, so it cannot go
        stale relative to what is actually on disk.

        Empty when no SecretBox is wired (nothing is encrypted, so nothing
        can fail to decrypt).
        """
        if self._secret_box is None:
            return set()
        item = (
            self._get_item(section, item_id) if item_id is not None else self.raw_section(section)
        )
        bad: set[str] = set()
        for key, value in item.items():
            if not (isinstance(key, str) and key.endswith("_secret")):
                continue
            if not isinstance(value, str) or not is_wrapped(value):
                continue
            try:
                self._maybe_unwrap(value)
            except SecretBoxError:
                bad.add(key)
        return bad

    def raw_section(self, section: str) -> dict[str, Any]:
        """A section exactly as stored, secrets still wrapped. Only for
        callers that need to reason about the ciphertext itself."""
        with self._lock:
            data = self._data.get(section, {})
            return dict(data) if isinstance(data, dict) else {}

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
                value = on_disk[disk]
                out[name] = self._maybe_unwrap(value) if is_secret else value
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
        unreadable = self.unreadable_secrets(namespace, item_id)
        out: dict[str, Any] = {}
        for field in manifest_settings:
            name = str(field["name"])
            is_secret = bool(field.get("secret"))
            disk = _disk_key(name, secret=is_secret)
            if disk in unreadable:
                # Stored, but the key that encrypted it is gone. Masking it
                # would tell the operator a working secret is saved while
                # every runtime read gets an empty string, which is exactly
                # the confusion this avoids: show it as absent, and let the
                # form say why.
                out[name] = ""
            elif disk in on_disk:
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
        ``manifest_settings``, the UI can post extras (CSRF token, etc.)
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
                    # User left the masked value alone, don't overwrite.
                    continue
                merged[disk] = self._maybe_wrap(value) if is_secret else value
            ns[item_id] = merged
            self._flush()

    def _get_item(self, namespace: str, item_id: str) -> dict[str, Any]:
        with self._lock:
            ns = self._data.get(namespace, {})
            if not isinstance(ns, dict):
                return {}
            item = ns.get(item_id, {})
            return dict(item) if isinstance(item, dict) else {}
