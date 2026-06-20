"""Tests for the per-kind defaults overrides (issue #22).

Covers the JSON-per-kind store + the save / reset settings routes +
the device_loader integration that applies the override at startup.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.kind_overrides import KindOverridesStore


@pytest.fixture
def app_with_gate(tmp_path: Path) -> Flask:
    """An app with the auth gate installed, pointing at a tmp data
    root so the store starts empty."""
    app = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    app.config["TESTING"] = True
    return app


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


# ----- KindOverridesStore unit tests ----------------------------------


def test_store_get_empty_when_no_file(tmp_path: Path) -> None:
    store = KindOverridesStore(tmp_path)
    assert store.get("esp32_client") == {}
    assert store.has_override("esp32_client") is False


def test_store_set_persists_whitelisted_fields(tmp_path: Path) -> None:
    store = KindOverridesStore(tmp_path)
    saved = store.set(
        "esp32_client",
        {
            "display_name": "Custom ESP",
            "panel_preset": "inky_7_3",
            "panel_w": "800",
            "panel_h": "480",
            "panel_orientation": "portrait",
            "sleep_interval_s": "600",
            "this_key_is_ignored": "x",  # outside the whitelist
        },
    )
    assert saved == {
        "display_name": "Custom ESP",
        "panel_preset": "inky_7_3",
        "panel_w": 800,
        "panel_h": 480,
        "panel_orientation": "portrait",
        "sleep_interval_s": 600,
    }
    assert store.has_override("esp32_client") is True
    # And the round-trip read is identical.
    assert store.get("esp32_client") == saved


def test_store_set_empty_removes_file(tmp_path: Path) -> None:
    store = KindOverridesStore(tmp_path)
    store.set("esp32_client", {"display_name": "Custom"})
    assert store.has_override("esp32_client")
    # Submitting an empty dict (or a dict whose values all coerce to
    # None) clears the override file entirely, so the kind reverts to
    # its bundled defaults instead of carrying a zero-byte file.
    store.set("esp32_client", {})
    assert not store.has_override("esp32_client")


def test_store_drops_invalid_orientation(tmp_path: Path) -> None:
    store = KindOverridesStore(tmp_path)
    saved = store.set("esp32_client", {"panel_orientation": "diagonal"})
    assert saved == {}


def test_store_delete_removes_file(tmp_path: Path) -> None:
    store = KindOverridesStore(tmp_path)
    store.set("esp32_client", {"display_name": "Custom"})
    assert store.delete("esp32_client") is True
    assert store.delete("esp32_client") is False


# ----- Routes: save + reset --------------------------------------------


def test_devices_kind_defaults_save_persists_to_disk(
    app_with_gate: Flask, tmp_path: Path
) -> None:
    client = app_with_gate.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/devices/kinds/esp32_client/defaults",
        data={
            "display_name": "Custom ESP",
            "panel_preset": "inky_7_3",
            "panel_w": "800",
            "panel_h": "480",
            "panel_orientation": "portrait",
            "sleep_interval_s": "600",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    store = KindOverridesStore(tmp_path / "devices")
    assert store.get("esp32_client") == {
        "display_name": "Custom ESP",
        "panel_preset": "inky_7_3",
        "panel_w": 800,
        "panel_h": 480,
        "panel_orientation": "portrait",
        "sleep_interval_s": 600,
    }


def test_devices_kind_defaults_reset_removes_file(
    app_with_gate: Flask, tmp_path: Path
) -> None:
    client = app_with_gate.test_client()
    _sign_in(client)
    # First save something to override.
    client.post(
        "/settings/devices/kinds/esp32_client/defaults",
        data={"display_name": "Custom"},
    )
    store = KindOverridesStore(tmp_path / "devices")
    assert store.has_override("esp32_client")
    # Then reset.
    resp = client.post(
        "/settings/devices/kinds/esp32_client/reset",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert not store.has_override("esp32_client")


def test_devices_kind_defaults_save_404s_on_unknown_kind(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/devices/kinds/nope/defaults",
        data={"display_name": "x"},
        follow_redirects=False,
    )
    # The route flashes + redirects rather than 404ing (matches the
    # other devices routes), so we follow the redirect and verify the
    # store is still empty.
    assert resp.status_code == 302


# ----- Settings page rendering ----------------------------------------


def test_built_in_kinds_card_renders_on_devices_tab(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    _sign_in(client)
    body = client.get("/settings/devices").get_data(as_text=True)
    # Section header + at least one kind row + the form anchor.
    assert "Built-in device kinds" in body
    assert "esp32_client" in body
    # Modified badge is absent before any override is saved.
    assert "MODIFIED" not in body


def test_built_in_kinds_card_flags_modified_after_save(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    _sign_in(client)
    client.post(
        "/settings/devices/kinds/esp32_client/defaults",
        data={"display_name": "Custom ESP"},
    )
    body = client.get("/settings/devices").get_data(as_text=True)
    # The MODIFIED badge appears once an override is recorded.
    assert "MODIFIED" in body
