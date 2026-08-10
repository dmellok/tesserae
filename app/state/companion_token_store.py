"""Companion credential store for the community iOS client.

The Companion API (``/api/app/v1``) is a separate trust boundary from the
firmware device API. A companion client pairs once, with a short-lived
single-use code the operator generates in the admin UI, and receives a
long-lived per-client bearer token in exchange. This store holds those
tokens.

Design constraints, deliberately distinct from firmware device tokens
(``device.access_token``) and the global webhook credential:

* **Dedicated registry.** A companion token can never be minted from a
  firmware pairing code and vice-versa. The two credential purposes are
  kept apart so one can't stand in for the other.
* **Hashed at rest.** Only ``sha256(token)`` is persisted, never the
  plaintext. The plaintext is returned exactly once, at pairing time, and
  is unrecoverable afterwards (the app keeps it in the Keychain). A leaked
  ``companion_tokens.json`` yields no usable credentials.
* **Scoped.** Each record carries an explicit ``scopes`` list. Pairing
  grants one fixed Companion role; anything beyond it is an optional scope
  the operator grants per client from Settings, which takes effect on the
  next request without re-pairing (the bearer doesn't change).
* **Independently revocable.** Every client shows up in the admin UI by
  name + last use and can be revoked on its own. Revocation is a tombstone
  (``revoked_at`` set) rather than a delete so an audit trail survives.
* **Constant-time lookup.** Presented tokens are hashed and compared with
  ``secrets.compare_digest`` against every live hash so a timing side
  channel can't distinguish a near-miss from a miss.

Persistence mirrors the settings/page stores: one JSON file, whole-file
atomic rewrite via tmp + rename, coarse lock (reads + writes are cheap,
no I/O in the hot path beyond the flush).

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The scopes a pairing grants. One "Companion" role today, issued whole;
# the field is stored per-token so narrower sets can be issued later.
#
# A token paired before a scope existed simply doesn't carry it, and the
# routes guarding that scope refuse it. That's survivable while every new
# scope is something the app couldn't call anyway, and it's why authoring
# (``lineups:write``) is NOT here: granting the ability to rewrite
# household scheduling to every previously-paired client is the one case
# where silence is the wrong default, so it lands as an explicit per-token
# grant instead (#207).
COMPANION_SCOPES: tuple[str, ...] = (
    "devices:read",
    "dashboards:read",
    "push:write",
    "media:write",
    "personal_data:write",
    "lineups:read",
    "lineups:control",
)

# Scopes an operator can grant to an existing pairing from Settings, on top
# of the set above. Each one is here because it shouldn't arrive by default:
# rewriting household scheduling is a different ask from pushing a picture,
# and a client paired months ago shouldn't silently acquire it because the
# server upgraded (#207).
OPTIONAL_SCOPES: tuple[str, ...] = ("lineups:write",)

# Every scope this server recognises. A record carrying anything else was
# either hand-edited or written by a newer build; it's dropped on load
# rather than kept, so a typo can't sit in the file looking like a grant.
KNOWN_SCOPES: frozenset[str] = frozenset(COMPANION_SCOPES) | frozenset(OPTIONAL_SCOPES)

# How stale ``last_used_at`` may get before a fresh authenticated request
# rewrites it. The admin UI only needs "last use" at minute granularity;
# throttling the write keeps a chatty client (the app polls a job every
# couple of seconds) from rewriting the whole file on every request.
_LAST_USED_THROTTLE_S = 60.0


def _now_iso() -> str:
    """Current UTC time as an RFC 3339 / ISO-8601 ``Z`` string, matching
    the ``format: date-time`` fields in the contract."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class CompanionToken:
    """One paired companion client. The plaintext token is never stored
    here, only its hash. ``client`` mirrors the ``PairingRequest.client``
    shape (name / platform / app_version / installation_id)."""

    token_id: str
    token_hash: str
    scopes: list[str]
    client: dict[str, Any]
    created_at: str
    last_used_at: str | None = None
    revoked_at: str | None = None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def name(self) -> str:
        raw = self.client.get("name")
        return str(raw) if isinstance(raw, str) and raw.strip() else self.token_id

    def public_dict(self) -> dict[str, Any]:
        """Admin-facing view: everything except the token hash. Safe to
        render in the Settings UI / return from an admin listing."""
        return {
            "token_id": self.token_id,
            "scopes": list(self.scopes),
            "client": dict(self.client),
            "name": self.name,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "revoked_at": self.revoked_at,
        }

    def _persist_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "token_hash": self.token_hash,
            "scopes": list(self.scopes),
            "client": dict(self.client),
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "revoked_at": self.revoked_at,
        }

    @classmethod
    def _from_persist(cls, raw: dict[str, Any]) -> CompanionToken | None:
        try:
            token_id = str(raw["token_id"])
            token_hash = str(raw["token_hash"])
        except (KeyError, TypeError):
            return None
        # Drop anything this build doesn't recognise. A scope check is a
        # membership test, so an unknown string sat in the file looking like
        # a grant while granting nothing; better it never loads.
        scopes = [str(s) for s in raw.get("scopes", []) if isinstance(s, str) and s in KNOWN_SCOPES]
        client = raw.get("client")
        return cls(
            token_id=token_id,
            token_hash=token_hash,
            scopes=scopes,
            client=dict(client) if isinstance(client, dict) else {},
            created_at=str(raw.get("created_at") or _now_iso()),
            last_used_at=(str(raw["last_used_at"]) if raw.get("last_used_at") else None),
            revoked_at=(str(raw["revoked_at"]) if raw.get("revoked_at") else None),
        )


class CompanionTokenStore:
    """Thread-safe, file-backed store of companion client credentials."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._tokens: dict[str, CompanionToken] = {}
        self._load()

    # -- issue / lookup / revoke ------------------------------------------

    def issue(
        self,
        *,
        client: dict[str, Any],
        scopes: tuple[str, ...] | list[str] = COMPANION_SCOPES,
    ) -> tuple[str, CompanionToken]:
        """Mint a fresh credential. Returns ``(plaintext_token, record)``.

        The plaintext is returned once and never stored; only its hash is
        persisted. ``client`` is the ``PairingRequest.client`` block from
        the pairing exchange."""
        plaintext = f"tc_live_{secrets.token_urlsafe(32)}"
        record = CompanionToken(
            token_id=f"ct_{secrets.token_hex(10)}",
            token_hash=_hash_token(plaintext),
            scopes=list(scopes),
            client=dict(client),
            created_at=_now_iso(),
        )
        with self._lock:
            self._tokens[record.token_id] = record
            self._flush()
        return plaintext, record

    def lookup(self, token: str) -> CompanionToken | None:
        """Resolve a presented bearer token to its live record, or None.

        Hashes the input and constant-time compares against every stored
        hash so a timing leak can't reveal which tokens exist. Revoked
        records never match. A successful lookup refreshes ``last_used_at``
        (throttled) so the admin UI can show each client's last use."""
        if not token:
            return None
        presented = _hash_token(token)
        matched: CompanionToken | None = None
        with self._lock:
            for record in self._tokens.values():
                if record.revoked:
                    continue
                # Finish the loop even after a hit so the comparison count
                # doesn't depend on which record matched.
                if secrets.compare_digest(record.token_hash, presented):
                    matched = record
            if matched is not None:
                self._touch_locked(matched)
        return matched

    def revoke(self, token_id: str) -> bool:
        """Tombstone a credential by id. Returns True if a live record was
        revoked, False if it was unknown or already revoked."""
        with self._lock:
            record = self._tokens.get(token_id)
            if record is None or record.revoked:
                return False
            record.revoked_at = _now_iso()
            self._flush()
            return True

    def revoke_token(self, token: str) -> bool:
        """Revoke by presented plaintext token (the ``DELETE /session``
        path, where the client presents its own bearer)."""
        if not token:
            return False
        presented = _hash_token(token)
        with self._lock:
            for record in self._tokens.values():
                if record.revoked:
                    continue
                if secrets.compare_digest(record.token_hash, presented):
                    record.revoked_at = _now_iso()
                    self._flush()
                    return True
        return False

    def set_optional_scope(self, token_id: str, scope: str, *, granted: bool) -> bool:
        """Grant or withdraw one optional scope on a live pairing.

        Takes effect on the client's next request; the bearer is unchanged,
        so the operator can hand an app a new ability without making the
        user re-pair, and take it back the same way. Only scopes in
        ``OPTIONAL_SCOPES`` move: the pairing set isn't editable here,
        because withdrawing it would leave a working app failing in ways
        the operator didn't intend. Returns whether anything changed."""
        if scope not in OPTIONAL_SCOPES:
            return False
        with self._lock:
            record = self._tokens.get(token_id)
            if record is None or record.revoked:
                return False
            has = scope in record.scopes
            if has == granted:
                return False
            if granted:
                record.scopes.append(scope)
            else:
                record.scopes.remove(scope)
            self._flush()
            return True

    def list_active(self) -> list[CompanionToken]:
        """Live (non-revoked) credentials, newest first. Backs the admin
        UI's paired-clients list."""
        with self._lock:
            live = [r for r in self._tokens.values() if not r.revoked]
        live.sort(key=lambda r: r.created_at, reverse=True)
        return live

    # -- internals ---------------------------------------------------------

    def _touch_locked(self, record: CompanionToken) -> None:
        """Refresh ``last_used_at`` if it's gone stale. Caller holds the
        lock. Throttled so a polling client doesn't rewrite the file on
        every request."""
        now = time.time()
        prev = _iso_to_epoch(record.last_used_at)
        if prev is not None and (now - prev) < _LAST_USED_THROTTLE_S:
            return
        record.last_used_at = _now_iso()
        self._flush()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt or unreadable file must not take the server down at
            # boot. Start empty; the operator can re-pair. (Deliberately
            # not overwriting the file here, a transient read error
            # shouldn't nuke real credentials.)
            return
        entries = raw.get("tokens") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            return
        for item in entries:
            if not isinstance(item, dict):
                continue
            record = CompanionToken._from_persist(item)
            if record is not None:
                self._tokens[record.token_id] = record

    def _flush(self) -> None:
        """Whole-file atomic rewrite. Caller holds the lock."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tokens": [r._persist_dict() for r in self._tokens.values()]}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)


def _iso_to_epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None
