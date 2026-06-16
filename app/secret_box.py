"""Wrap / unwrap helpers for at-rest encryption of manifest-declared
``secret: true`` setting fields (HA tokens, plugin API keys, etc.).

Background. ``SettingsStore`` already segregates secret-flagged fields
on disk by suffixing the key with ``_secret`` so a quick ``grep -i
secret data/core/settings.json`` makes every sensitive value visually
obvious. That convention is about visibility, not protection; the values
themselves were stored in plaintext. This module adds a thin
encryption layer so the on-disk values are wrapped with AES-GCM and
only decrypt when a process holding the key reads them through
``SettingsStore.get_for_runtime``.

Threat model. This protects backups, repo accidents, and shoulder-surf
scenarios where the filesystem leaves the host. It does **not** protect
against a host-root attacker (they can read the env or the settings
file the key derives from), and it does not replace a sandbox for
plugins, which still see plaintext at fetch time.

Wire format. A wrapped value is the ASCII string ``enc:v1:<token>``
where ``<token>`` is the urlsafe base64 of ``nonce(12) || ciphertext
|| tag(16)``. The ``v1`` is a literal version tag, future algo
upgrades append ``v2`` etc. and the unwrap path dispatches on it.

Key resolution.
  1. ``TESSERAE_SECRET_KEY`` env var. 64 hex chars (32 bytes). For real
     installs the user puts this in their docker-compose env. Stable
     across restarts because they wrote it down.
  2. Fallback: HKDF-SHA256 over ``settings.app.session_secret_secret``
     with the info string ``b"tesserae.secret_box.v1"`` so the
     derivation is distinct from the Flask signing key it bootstrapped
     from. This makes a fresh ``git clone && python -m app.main`` work
     without ceremony, and the wrapped values are still meaningful
     because the session secret persists across restarts. The fallback
     is logged at info on first use so the operator can choose to
     promote to an env-pinned key later.

Backwards compatibility. ``unwrap`` accepts a plaintext input (no
``enc:`` prefix) and returns it unchanged. Existing on-disk values
keep working until the next save, which wraps them. Migration is
opportunistic; there is no separate walker.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ENV_KEY: str = "TESSERAE_SECRET_KEY"
WIRE_PREFIX: str = "enc:v1:"
NONCE_BYTES: int = 12
KEY_BYTES: int = 32
HKDF_INFO: bytes = b"tesserae.secret_box.v1"

logger = logging.getLogger(__name__)


class SecretBoxError(ValueError):
    """Raised when a wrapped value can't be decrypted, the prefix is
    valid but the tag check failed, the key is wrong, or the payload
    was truncated. Callers in the settings path treat this as fatal
    (a misconfigured key is not silently recoverable; we surface it
    so the user can fix it instead of getting empty secrets)."""


class SecretBox:
    """Holds a 32-byte AES-GCM key plus the wrap / unwrap helpers.

    Construct directly with raw key bytes, or via the classmethods
    ``from_env`` / ``from_session_secret`` which apply the resolution
    rules described in the module docstring.
    """

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError(f"SecretBox key must be {KEY_BYTES} bytes, got {len(key)}")
        self._aead = AESGCM(key)

    # -- factories ----------------------------------------------------------

    @classmethod
    def from_env(cls) -> SecretBox | None:
        """Return a box keyed off ``TESSERAE_SECRET_KEY`` or ``None``
        when the env var isn't set / is malformed. Callers decide
        whether to log + fall back to ``from_session_secret`` or
        treat absence as a hard error."""
        raw = os.environ.get(ENV_KEY, "").strip()
        if not raw:
            return None
        try:
            key = bytes.fromhex(raw)
        except ValueError:
            logger.warning(
                "%s is set but isn't valid hex; falling back to session-derived key",
                ENV_KEY,
            )
            return None
        if len(key) != KEY_BYTES:
            logger.warning(
                "%s decoded to %d bytes (expected %d); falling back to session-derived key",
                ENV_KEY,
                len(key),
                KEY_BYTES,
            )
            return None
        return cls(key)

    @classmethod
    def from_session_secret(cls, session_secret: bytes) -> SecretBox:
        """Derive an encryption key from the Flask session secret via
        HKDF-SHA256. Distinct ``info`` string keeps the derived key
        cryptographically separated from the signing key it came from
        (compromise of one does not imply compromise of the other)."""
        if len(session_secret) < 16:
            # Session secrets are 32 bytes when this app generates them
            # (see app.auth.secret_key); a short one means the operator
            # supplied a manual override that's too weak to derive a
            # stable encryption key from. Refuse rather than build a
            # box that's effectively a low-entropy nonce.
            raise ValueError(
                "session secret is too short to derive a SecretBox key; "
                "set TESSERAE_SECRET_KEY explicitly"
            )
        kdf = HKDF(
            algorithm=hashes.SHA256(),
            length=KEY_BYTES,
            salt=None,
            info=HKDF_INFO,
        )
        return cls(kdf.derive(session_secret))

    @classmethod
    def resolve(cls, session_secret: bytes) -> SecretBox:
        """Apply the documented precedence: env first, session-secret
        derivation second. Logs the fallback at info on first use so
        the operator can promote to an explicit env key later."""
        from_env = cls.from_env()
        if from_env is not None:
            logger.info("SecretBox using %s", ENV_KEY)
            return from_env
        logger.info(
            "SecretBox using key derived from session secret; set %s for a stable, "
            "operator-owned key",
            ENV_KEY,
        )
        return cls.from_session_secret(session_secret)

    # -- wrap / unwrap -----------------------------------------------------

    def wrap(self, plaintext: str) -> str:
        """Return ``"enc:v1:<token>"`` for the given UTF-8 string. The
        empty string round-trips through wrap/unwrap as the empty
        string (still encrypted, no leak); callers that want an unset
        sentinel should treat absence separately from emptiness."""
        nonce = secrets.token_bytes(NONCE_BYTES)
        ciphertext = self._aead.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
        payload = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
        return WIRE_PREFIX + payload

    def unwrap(self, token: str) -> str:
        """Decrypt a wrapped value. Plaintext input (no ``enc:`` prefix)
        is returned unchanged so existing on-disk values keep working
        until the next save migrates them.

        Raises ``SecretBoxError`` only when the input *claims* to be
        wrapped (carries the prefix) but the tag check fails. Plain
        input always succeeds; the caller's read path doesn't see a
        difference."""
        if not is_wrapped(token):
            return token
        body = token[len(WIRE_PREFIX) :]
        try:
            blob = base64.urlsafe_b64decode(body.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise SecretBoxError(f"wrapped value not valid base64: {exc}") from exc
        if len(blob) <= NONCE_BYTES:
            raise SecretBoxError("wrapped value too short to contain nonce + ciphertext")
        nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
        try:
            plain = self._aead.decrypt(nonce, ciphertext, associated_data=None)
        except Exception as exc:
            raise SecretBoxError(f"AES-GCM authentication failed: {exc}") from exc
        return plain.decode("utf-8")


def is_wrapped(value: object) -> bool:
    """True when ``value`` looks like an encrypted token. Used by
    SettingsStore on save to avoid double-wrapping and on load to
    decide whether unwrap will actually do work."""
    return isinstance(value, str) and value.startswith(WIRE_PREFIX)
