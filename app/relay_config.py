"""Home-side relay configuration, read from the ``relay`` settings section.

One install links to one relay. The section holds the relay base URL, the
install identity minted at registration, and the install's X25519 private key
used to complete pairings. Secrets live here alongside the rest of the app
settings (same store as the MQTT/webhook credentials).

Shape of the ``relay`` section (secrets use the ``_secret`` suffix so admin
views redact them and a wired SecretBox encrypts them at rest, matching
``app.session_secret_secret`` / ``broker.password_secret``)::

    {
      "enabled": bool,
      "base_url": "https://relay.tesserae.ink",
      "install_id": "<opaque>",
      "publisher_token_secret": "<secret>",
      "install_privkey_secret": "<base64url X25519 private, secret>",
      "allow_local": bool,       # dev: point at a wrangler-dev / LAN relay
      "pending_pairings": { "<code>": { device_id, kind, panel, name, ... } }
    }

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

from typing import Any

from app.relay_client import RelayClient

RELAY_SECTION: str = "relay"
DEFAULT_BASE_URL: str = "https://relay.tesserae.ink"


def relay_config(settings: Any) -> dict[str, Any]:
    """The ``relay`` settings section as a plain dict (empty when unset)."""
    try:
        section = settings.get_section(RELAY_SECTION)
    except Exception:
        return {}
    return section if isinstance(section, dict) else {}


def _str(cfg: dict[str, Any], key: str) -> str:
    value = cfg.get(key)
    return value if isinstance(value, str) else ""


def is_linked(cfg: dict[str, Any]) -> bool:
    """True when the install has registered (has an id + publisher token), so
    the publisher and pairing poller have something to talk to."""
    return bool(
        cfg.get("enabled") and _str(cfg, "install_id") and _str(cfg, "publisher_token_secret")
    )


def base_url(cfg: dict[str, Any]) -> str:
    raw = cfg.get("base_url")
    return raw.strip() if isinstance(raw, str) and raw.strip() else DEFAULT_BASE_URL


def install_privkey(cfg: dict[str, Any]) -> str:
    """The install's base64url X25519 private key (for completing pairings),
    or an empty string when unset."""
    return _str(cfg, "install_privkey_secret")


def build_client(cfg: dict[str, Any]) -> RelayClient | None:
    """A :class:`RelayClient` for the linked install, or ``None`` when the
    install isn't linked yet."""
    if not is_linked(cfg):
        return None
    return RelayClient(
        base_url=base_url(cfg),
        install_id=_str(cfg, "install_id"),
        publisher_token=_str(cfg, "publisher_token_secret"),
        allow_local=bool(cfg.get("allow_local")),
    )
