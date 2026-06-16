"""SettingsStore: secret rename on disk, masking for admin reads, real
values for runtime reads, defaults from manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.state.settings_store import SECRET_MASK, SettingsStore

WIDGET_FIELDS = [
    {"name": "base_url", "type": "string", "label": "URL", "default": "http://x"},
    {"name": "api_key", "type": "string", "label": "Key", "secret": True},
    {"name": "show_debug", "type": "boolean", "label": "Debug", "default": False},
]


def test_secret_renamed_on_disk(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    store.update_for_namespace(
        "plugins",
        "weather",
        {"base_url": "http://api.example", "api_key": "xyzzy", "show_debug": True},
        WIDGET_FIELDS,
    )
    on_disk = json.loads((tmp_path / "s.json").read_text())
    assert on_disk["plugins"]["weather"] == {
        "base_url": "http://api.example",
        "api_key_secret": "xyzzy",
        "show_debug": True,
    }


def test_get_for_runtime_strips_secret_suffix(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    store.update_for_namespace(
        "plugins",
        "weather",
        {"api_key": "xyzzy"},
        WIDGET_FIELDS,
    )
    runtime = store.get_for_runtime("plugins", "weather", WIDGET_FIELDS)
    assert runtime["api_key"] == "xyzzy"
    # Defaults from the manifest fill in the gaps.
    assert runtime["base_url"] == "http://x"
    assert runtime["show_debug"] is False


def test_get_for_admin_masks_secrets(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    store.update_for_namespace(
        "plugins",
        "weather",
        {"api_key": "xyzzy", "show_debug": True},
        WIDGET_FIELDS,
    )
    admin = store.get_for_admin("plugins", "weather", WIDGET_FIELDS)
    assert admin["api_key"] == SECRET_MASK
    assert admin["show_debug"] is True


def test_resubmit_masked_secret_keeps_existing_value(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    store.update_for_namespace("plugins", "weather", {"api_key": "original"}, WIDGET_FIELDS)
    # The UI displays the mask; if the user re-submits without editing it,
    # the on-disk secret must NOT be replaced with the mask.
    store.update_for_namespace(
        "plugins", "weather", {"api_key": SECRET_MASK, "show_debug": True}, WIDGET_FIELDS
    )
    runtime = store.get_for_runtime("plugins", "weather", WIDGET_FIELDS)
    assert runtime["api_key"] == "original"
    assert runtime["show_debug"] is True


def test_update_ignores_keys_not_in_manifest(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    store.update_for_namespace(
        "plugins",
        "weather",
        {"api_key": "k", "rogue_field": "junk"},
        WIDGET_FIELDS,
    )
    on_disk = json.loads((tmp_path / "s.json").read_text())
    assert "rogue_field" not in on_disk["plugins"]["weather"]


def test_section_round_trip(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    store.update_section("broker", {"host": "h", "port": 1883})
    store.patch_section("broker", {"port": 8883, "username": "u"})
    assert store.get_section("broker") == {"host": "h", "port": 8883, "username": "u"}


def test_persistence_across_instances(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    a = SettingsStore(p)
    a.update_section("app", {"base_url": "http://lan/"})
    b = SettingsStore(p)
    assert b.get_section("app") == {"base_url": "http://lan/"}


# -- v0.49 at-rest encryption ------------------------------------------


def _box() -> Any:
    """Helper: a fresh SecretBox with a random key. ``Any`` return type
    keeps the import local so the rest of the file doesn't depend on
    cryptography at collect time."""
    import secrets as _s

    from app.secret_box import SecretBox

    return SecretBox(_s.token_bytes(32))


def test_secret_field_is_encrypted_on_disk(tmp_path: Path) -> None:
    """With a SecretBox wired, the on-disk ``_secret`` value carries
    the ``enc:v1:`` prefix instead of the raw plaintext. The runtime
    read transparently decrypts."""
    from app.secret_box import WIRE_PREFIX

    box = _box()
    store = SettingsStore(tmp_path / "s.json", secret_box=box)
    store.update_for_namespace("plugins", "weather", {"api_key": "hass-token-xyz"}, WIDGET_FIELDS)
    on_disk = json.loads((tmp_path / "s.json").read_text())
    stored = on_disk["plugins"]["weather"]["api_key_secret"]
    assert stored.startswith(WIRE_PREFIX)
    assert "hass-token-xyz" not in stored  # the literal must not appear
    runtime = store.get_for_runtime("plugins", "weather", WIDGET_FIELDS)
    assert runtime["api_key"] == "hass-token-xyz"


def test_legacy_plaintext_secret_still_reads(tmp_path: Path) -> None:
    """Backwards compat: a file written by an older version (plaintext
    under ``_secret`` suffix) decrypts cleanly because the unwrap path
    is a no-op for non-prefixed input. No migration step required."""
    p = tmp_path / "s.json"
    # Hand-write a legacy plaintext payload.
    p.write_text(json.dumps({"plugins": {"weather": {"api_key_secret": "legacy-plain"}}}))
    store = SettingsStore(p, secret_box=_box())
    runtime = store.get_for_runtime("plugins", "weather", WIDGET_FIELDS)
    assert runtime["api_key"] == "legacy-plain"


def test_legacy_plaintext_gets_migrated_on_next_save(tmp_path: Path) -> None:
    """When the user touches a settings page that re-saves a secret
    field, the value gets opportunistically wrapped. No background
    walker; this is the migration."""
    from app.secret_box import WIRE_PREFIX

    p = tmp_path / "s.json"
    p.write_text(json.dumps({"plugins": {"weather": {"api_key_secret": "legacy-plain"}}}))
    store = SettingsStore(p, secret_box=_box())
    # Simulate the user updating an adjacent field; the existing
    # secret tags along as part of the merge.
    store.update_for_namespace(
        "plugins", "weather", {"api_key": "new-token", "show_debug": True}, WIDGET_FIELDS
    )
    on_disk = json.loads(p.read_text())
    assert on_disk["plugins"]["weather"]["api_key_secret"].startswith(WIRE_PREFIX)


def test_admin_view_still_masks_under_encryption(tmp_path: Path) -> None:
    """The masking convention used by the settings UI continues to
    work; the operator sees ``********`` and the ciphertext never
    crosses into the response body."""
    store = SettingsStore(tmp_path / "s.json", secret_box=_box())
    store.update_for_namespace("plugins", "weather", {"api_key": "hass-token"}, WIDGET_FIELDS)
    admin = store.get_for_admin("plugins", "weather", WIDGET_FIELDS)
    assert admin["api_key"] == SECRET_MASK


def test_get_section_unwraps_nested_secret_fields(tmp_path: Path) -> None:
    """Plugin server modules (e.g. ha_core) read their own settings
    via ``get_section("plugins")`` rather than the manifest-aware
    runtime path. The section getter must transparently unwrap any
    ``_secret``-suffixed string it finds at any depth so those plugins
    keep seeing plaintext."""
    box = _box()
    store = SettingsStore(tmp_path / "s.json", secret_box=box)
    store.update_for_namespace("plugins", "ha_core", {"api_key": "hass-token"}, WIDGET_FIELDS)
    section = store.get_section("plugins")
    # Plugin keeps reading ``token_secret``-style keys directly; they
    # come back as plaintext, not ``enc:v1:...``.
    assert section["ha_core"]["api_key_secret"] == "hass-token"


def test_no_box_keeps_plaintext_behaviour(tmp_path: Path) -> None:
    """Without a wired box (tests, legacy installs), the store
    behaves exactly as before: plaintext on disk, plaintext on read.
    No accidental encryption requirement on the test path."""
    store = SettingsStore(tmp_path / "s.json")  # no secret_box
    store.update_for_namespace("plugins", "weather", {"api_key": "plain"}, WIDGET_FIELDS)
    on_disk = json.loads((tmp_path / "s.json").read_text())
    assert on_disk["plugins"]["weather"]["api_key_secret"] == "plain"


def test_wrong_key_surfaces_error_rather_than_empty_token(tmp_path: Path) -> None:
    """A misconfigured SECRET_KEY (e.g. operator rotated env var without
    re-saving secrets) should fail loudly when a secret is read, not
    silently return an empty string the plugin then 401s with."""
    import secrets as _s

    from app.secret_box import SecretBox, SecretBoxError

    p = tmp_path / "s.json"
    box_a = SecretBox(_s.token_bytes(32))
    store_a = SettingsStore(p, secret_box=box_a)
    store_a.update_for_namespace("plugins", "weather", {"api_key": "hass-token"}, WIDGET_FIELDS)
    # Reload with a different key, as if the operator changed
    # TESSERAE_SECRET_KEY without resaving.
    box_b = SecretBox(_s.token_bytes(32))
    store_b = SettingsStore(p, secret_box=box_b)
    with pytest.raises(SecretBoxError):
        store_b.get_for_runtime("plugins", "weather", WIDGET_FIELDS)
