"""pi_client: the unified Raspberry Pi kind.

Loader picks it up, status JSON round-trips, config is validated, and the
frame format follows the panel the client reports: a Spectra 6 Impression
that advertises ``spectra_6`` gets the packed ``.bin``, anything else PNG.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import device_loader
from app.device_loader import discover
from app.main import REPO_ROOT, create_app


@pytest.fixture
def pi_client(tmp_path: Path) -> Any:
    registry = discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    d = registry.get("pi_client")
    assert d is not None
    return d


def test_manifest_fields(pi_client: Any) -> None:
    assert pi_client.name == "Raspberry Pi client"
    # PNG first: the baseline every Inky panel can paint.
    assert pi_client.renderer_ids == ["pi_png", "pi_bin"]
    assert pi_client.status_topic == "tesserae/pi/status"
    schema = pi_client.config_schema or {}
    for key in ("sleep_interval_s", "always_on", "awake_poll_s", "button_wake_s", "touch_enabled"):
        assert key in schema
    # Mains-powered: stays awake unless the owner says otherwise.
    assert schema["always_on"]["default"] is True


def test_parse_status(pi_client: Any) -> None:
    assert pi_client.parse_status(b'{"state": "idle"}') == {"state": "idle"}
    assert pi_client.parse_status(b"") == {"raw": ""}
    assert pi_client.parse_status(b"hello") == {"raw": "hello"}


@pytest.mark.parametrize(
    "payload",
    [
        {"sleep_interval_s": 900},
        {
            "sleep_interval_s": 60,
            "always_on": True,
            "awake_poll_s": 15,
            "button_wake_s": 10,
            "touch_enabled": False,
        },
    ],
)
def test_validate_config_accepts(pi_client: Any, payload: dict[str, Any]) -> None:
    assert pi_client.validate_config(payload) == (True, None)


@pytest.mark.parametrize(
    ("payload", "needle"),
    [
        ({}, "missing"),
        ({"sleep_interval_s": 4}, "sleep_interval_s"),
        ({"sleep_interval_s": 7 * 86400 + 1}, "sleep_interval_s"),
        ({"sleep_interval_s": "fifteen"}, "integer"),
        ({"sleep_interval_s": 60, "awake_poll_s": 301}, "awake_poll_s"),
        ({"sleep_interval_s": 60, "awake_poll_s": True}, "awake_poll_s"),
        ({"sleep_interval_s": 60, "button_wake_s": 61}, "button_wake_s"),
        ({"sleep_interval_s": 60, "always_on": "yes"}, "always_on"),
        ({"sleep_interval_s": 60, "touch_enabled": 1}, "touch_enabled"),
    ],
)
def test_validate_config_rejects(pi_client: Any, payload: dict[str, Any], needle: str) -> None:
    ok, err = pi_client.validate_config(payload)
    assert not ok and err is not None and needle in err


def test_spectra_6_panels_get_the_packed_bin(pi_client: Any) -> None:
    pick = device_loader.effective_renderer_pick
    # The server stores the canonical gamut: spectra_6 is waveshare_e6.
    assert pick(pi_client.manifest, None, ["waveshare_e6"]) == "pi_bin"
    # Nothing advertised, or another gamut: the PNG baseline (None = primary).
    assert pick(pi_client.manifest, None, None) is None
    assert pick(pi_client.manifest, None, ["inky_7colour"]) is None


# --- end to end over the REST pairing flow -----------------------------------


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _register(app: Flask, device_id: str, **extra: Any) -> None:
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code},
        json={"device_id": device_id, "kind": "pi_client", "panel_w": 800, "panel_h": 480, **extra},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)


@pytest.mark.parametrize(
    ("gamuts", "renderer"),
    [(["spectra_6"], "pi_bin"), (None, "pi_png"), (["acep_7colour"], "pi_png")],
)
def test_registering_picks_the_frame_path_from_the_panel(
    app: Flask, gamuts: list[str] | None, renderer: str
) -> None:
    extra = {"gamuts": gamuts} if gamuts is not None else {}
    _register(app, "kitchen", **extra)
    dev = app.config["DEVICE_REGISTRY"].get("kitchen")
    assert dev is not None and dev.kind_of == "pi_client"
    (rid,) = dev.renderer_ids
    assert rid.split("__", 1)[0] == renderer
