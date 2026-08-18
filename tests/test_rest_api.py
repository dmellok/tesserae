"""REST API endpoint tests: frame fetch, status post, register, log.

Covers the wire contract Phase 1 ships:
- Bearer-token auth, including the URL-id mismatch case
- Frame: ETag, 304 on If-None-Match, 204 when no frame rendered yet
- Status: parse + merge, response piggybacks config + next_poll_s
- Register: pairing code flow, idempotent on existing device id,
  refuses bad codes / unknown kinds
- Log: persists into the EventLog with the right shape
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app


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


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _register_via_api(client, *, code: str, device_id: str, kind: str = "pico_bin_client"):
    """Drive the register endpoint to set up an instance with a token
    we can then reuse in subsequent tests. Returns the response."""
    return client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": device_id,
                "kind": kind,
                "panel_w": 1600,
                "panel_h": 1200,
                "fw_version": "0.1.0",
            }
        ),
    )


def _issue_pairing(app) -> str:
    return app.config["PAIRING_STORE"].issue(note="test").code


# -- register ----------------------------------------------------------


def test_register_with_dims_and_gamut_persists_panel_override(app: Flask) -> None:
    """v0.69.1 (issue #41): a generic CircuitPython register with
    ``panel_w`` + ``panel_h`` + ``gamut`` in the body overrides the
    kind's default panel block so the same generic kind can serve
    different-shape panels without a per-SKU manifest add."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "cp_lab",
                "kind": "circuitpython_generic",
                "panel_w": 400,
                "panel_h": 300,
                "gamut": "mono",
                "fw_version": "0.1.0",
            }
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    devices = app.config["DEVICE_REGISTRY"]
    instance = devices.get("cp_lab")
    assert instance is not None
    panel = instance.panel or {}
    assert panel.get("w") == 400
    assert panel.get("h") == 300
    assert panel.get("gamut") == "mono"


def test_register_aliases_semantic_gamut_to_canonical(app: Flask) -> None:
    """``spectra_6`` aliases to ``waveshare_e6`` so the .bin packer's
    lookup still finds a palette; ``acep_7colour`` aliases to
    ``inky_7colour`` the same way."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "cp_spectra",
                "kind": "circuitpython_generic",
                "panel_w": 800,
                "panel_h": 480,
                "gamut": "spectra_6",
                "fw_version": "0.1.0",
            }
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    devices = app.config["DEVICE_REGISTRY"]
    panel = (devices.get("cp_spectra").panel or {}) if devices.get("cp_spectra") else {}
    assert panel.get("gamut") == "waveshare_e6"


def test_register_rejects_bogus_gamut_and_falls_back_to_waveshare_e6(app: Flask) -> None:
    """Corrupt payloads (a gamut string that isn't in the allow-list)
    persist as ``waveshare_e6`` rather than stranding the device with a
    nonsense panel the .bin packer can't quantise against."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "cp_bogus",
                "kind": "circuitpython_generic",
                "panel_w": 800,
                "panel_h": 480,
                "gamut": "not-a-real-gamut",
                "fw_version": "0.1.0",
            }
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    devices = app.config["DEVICE_REGISTRY"]
    panel = (devices.get("cp_bogus").panel or {}) if devices.get("cp_bogus") else {}
    assert panel.get("gamut") == "waveshare_e6"


def test_register_accepts_rgb24_and_rgb16_as_declared(app: Flask) -> None:
    """Bernhard's suggestion from issue #41 (comment 4872979793):
    accept ``rgb24`` and ``rgb16`` for full-colour displays. Values
    persist verbatim (no alias to a .bin packer target since these
    aren't served by that path)."""
    client = app.test_client()
    _sign_in(client)
    for tag, gamut in (("rgb24", "rgb24"), ("rgb16", "rgb16")):
        code = _issue_pairing(app)
        resp = client.post(
            "/api/v1/device/register",
            headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
            data=json.dumps(
                {
                    "device_id": f"cp_{tag}",
                    "kind": "circuitpython_generic",
                    "panel_w": 320,
                    "panel_h": 240,
                    "gamut": gamut,
                    "fw_version": "0.1.0",
                }
            ),
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
    devices = app.config["DEVICE_REGISTRY"]
    assert (devices.get("cp_rgb24").panel or {}).get("gamut") == "rgb24"
    assert (devices.get("cp_rgb16").panel or {}).get("gamut") == "rgb16"


def test_register_with_valid_pairing_code_creates_device_and_returns_token(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)

    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["device_token"]
    assert isinstance(body["device_token"], str)
    assert body["reused_existing"] is False
    # Instance now lives in the registry.
    devices = app.config["DEVICE_REGISTRY"]
    instance = devices.get("bedroom_pico")
    assert instance is not None
    assert instance.kind_of == "pico_bin_client"
    assert instance.manifest.get("access_token") == body["device_token"]


def test_register_without_pairing_code_returns_400(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/api/v1/device/register",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"device_id": "x", "kind": "pico_bin_client"}),
    )
    assert resp.status_code == 400


def test_register_with_invalid_pairing_code_returns_403(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = _register_via_api(client, code="000000", device_id="bedroom_pico")
    assert resp.status_code == 403
    assert "invalid" in resp.get_json()["error"].lower()


def test_register_rejection_is_logged_server_side_without_the_code(
    app: Flask, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed pairing has to be debuggable from the server log, not
    only from the firmware's discarded HTTP body (issue #126). Rejections
    log at WARNING with the reason; the pairing code never appears."""
    client = app.test_client()
    _sign_in(client)
    with caplog.at_level(logging.WARNING, logger="app.rest_api"):
        resp = _register_via_api(client, code="135790", device_id="bedroom_pico")
    assert resp.status_code == 403
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("rejected" in r.getMessage() and "bedroom_pico" in r.getMessage() for r in warnings)
    # The pairing code must not be logged.
    assert all("135790" not in r.getMessage() for r in caplog.records)


def test_register_success_is_logged(app: Flask, caplog: pytest.LogCaptureFixture) -> None:
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    with caplog.at_level(logging.INFO, logger="app.rest_api"):
        resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    assert resp.status_code == 201
    assert any(
        "device=bedroom_pico" in r.getMessage() and "status=201" in r.getMessage()
        for r in caplog.records
    )


def test_register_with_unknown_kind_does_not_burn_pairing_code(app: Flask) -> None:
    """Validation order is load-bearing: pairing code is checked AFTER
    the kind. A typoed kind shouldn't lose the user their code."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="x", kind="nonexistent_kind")
    assert resp.status_code == 400
    # Code still consumable since unknown-kind validation happened first.
    resp2 = _register_via_api(client, code=code, device_id="bedroom_pico")
    assert resp2.status_code == 201


def test_register_is_idempotent_on_existing_device_id(app: Flask) -> None:
    """If the firmware retries register (maybe missed our response),
    second call returns the EXISTING token rather than creating a
    duplicate or failing."""
    client = app.test_client()
    _sign_in(client)
    first_code = _issue_pairing(app)
    second_code = _issue_pairing(app)

    resp1 = _register_via_api(client, code=first_code, device_id="bedroom_pico")
    token1 = resp1.get_json()["device_token"]

    resp2 = _register_via_api(client, code=second_code, device_id="bedroom_pico")
    assert resp2.status_code == 200
    body = resp2.get_json()
    assert body["reused_existing"] is True
    assert body["device_token"] == token1


def test_reregister_switches_wire_format_and_invalidates_render(app: Flask) -> None:
    """A device registered as png can switch to bmp by re-declaring the
    format on a later /register, no delete + re-create. The renderer flips
    and any stale render is invalidated so /frame won't keep serving the
    old-format frame."""
    client = app.test_client()
    _sign_in(client)
    devices = app.config["DEVICE_REGISTRY"]

    code = _issue_pairing(app)
    first = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": "cp_fmt", "kind": "circuitpython_generic", "panel_w": 400, "panel_h": 300}
        ),
    )
    assert first.status_code == 201
    assert devices.get("cp_fmt").renderer_ids == ["circuitpython_png__cp_fmt"]

    # Seed a stale render so we can prove it gets invalidated on switch.
    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders["cp_fmt"] = {"digest": "abc", "ext": "png", "filename": "abc.png"}

    second_code = _issue_pairing(app)
    switched = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": second_code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": "cp_fmt", "kind": "circuitpython_generic", "format": "bmp"}),
    )
    assert switched.status_code == 200
    assert switched.get_json()["reused_existing"] is True
    assert devices.get("cp_fmt").renderer_ids == ["circuitpython_bmp__cp_fmt"]
    # Stale png render dropped -> /frame will 204 until the next push.
    assert push_mgr.latest_render_for("cp_fmt") is None


def test_reregister_heals_generic_kind_to_declared_sku(app: Flask) -> None:
    """A device that first paired under the generic esp32_client kind
    later re-registers running a board build that declares its hardware
    SKU. The instance moves to the SKU kind (same wire protocol), keeps
    its token, and drops any stale render, so per-kind OTA rollouts see
    the device under the kind its firmware verifies descriptors
    against."""
    client = app.test_client()
    _sign_in(client)
    devices = app.config["DEVICE_REGISTRY"]

    code = _issue_pairing(app)
    first = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": "frame01", "kind": "esp32_client", "panel_w": 1200, "panel_h": 1600}
        ),
    )
    assert first.status_code == 201
    token = first.get_json()["device_token"]
    assert devices.get("frame01").kind_of == "esp32_client"

    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders["frame01"] = {"digest": "abc", "ext": "bin", "filename": "abc.bin"}

    second_code = _issue_pairing(app)
    healed = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": second_code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "frame01",
                "kind": "seeed_reterminal_e1004",
                "panel_w": 1200,
                "panel_h": 1600,
                "fw_version": "1.6.0",
            }
        ),
    )
    assert healed.status_code == 200
    body = healed.get_json()
    assert body["reused_existing"] is True
    assert body["device_token"] == token
    assert devices.get("frame01").kind_of == "seeed_reterminal_e1004"
    # Old-kind render dropped -> /frame will 204 until the next push.
    assert push_mgr.latest_render_for("frame01") is None


def test_reregister_ignores_cross_protocol_kind(app: Flask) -> None:
    """Kind healing only refines which board on the same wire protocol.
    A re-register declaring a kind on a different protocol keeps the
    stored kind rather than moving the device across contracts."""
    client = app.test_client()
    _sign_in(client)
    devices = app.config["DEVICE_REGISTRY"]

    code = _issue_pairing(app)
    first = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": "frame02", "kind": "esp32_client"}),
    )
    assert first.status_code == 201

    second_code = _issue_pairing(app)
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": second_code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": "frame02", "kind": "pico_bin_client"}),
    )
    assert resp.status_code == 200
    assert resp.get_json()["reused_existing"] is True
    assert devices.get("frame02").kind_of == "esp32_client"


def test_discover_mac_claim_heals_kind(app: Flask) -> None:
    """A re-flashed device whose NVS was wiped comes back through
    /discover with its MAC; the claim path heals a stale generic kind
    the same way /register does. This is the path the stale-kind case
    actually hits in the field (a healthy device never re-registers)."""
    client = app.test_client()
    _sign_in(client)
    devices = app.config["DEVICE_REGISTRY"]

    code = _issue_pairing(app)
    first = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "frame03",
                "kind": "esp32_client",
                "panel_w": 1200,
                "panel_h": 1600,
                "mac": "aa:bb:cc:dd:ee:03",
            }
        ),
    )
    assert first.status_code == 201

    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "frame03",
                "kind": "seeed_reterminal_e1004",
                "panel_w": 1200,
                "panel_h": 1600,
                "fw_version": "1.6.0",
                "mac": "aa:bb:cc:dd:ee:03",
            }
        ),
    )
    assert resp.status_code == 200
    assert resp.get_json()["registered"] is True
    assert devices.get("frame03").kind_of == "seeed_reterminal_e1004"


def test_heal_between_catalog_siblings_keeps_device_identity(app: Flask) -> None:
    """A reflash switches the same physical unit between catalog
    sibling kinds (E1001 mono <-> E1001 gray, XIAO mono <-> XIAO BWR).
    The heal must move the EXISTING row, not create a duplicate: same
    device id, same token, one registry entry, and per-device state
    keyed by the id (history, pages, nav) stays attached."""
    client = app.test_client()
    _sign_in(client)
    devices = app.config["DEVICE_REGISTRY"]

    for device_id, mac, first_kind, second_kind in (
        (
            "hall_e1001",
            "aa:bb:cc:dd:ee:11",
            "seeed_reterminal_e1001",
            "seeed_reterminal_e1001_gray",
        ),
        ("desk_xiao", "aa:bb:cc:dd:ee:12", "xiao_epaper_75", "xiao_epaper_75_bwr"),
    ):
        code = _issue_pairing(app)
        first = client.post(
            "/api/v1/device/register",
            headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
            data=json.dumps({"device_id": device_id, "kind": first_kind, "mac": mac}),
        )
        assert first.status_code == 201
        token = first.get_json()["device_token"]

        # Reflash: wiped NVS, device re-discovers by MAC declaring the
        # sibling kind.
        resp = client.post(
            "/api/v1/device/discover",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {"device_id": device_id, "kind": second_kind, "fw_version": "1.7.0", "mac": mac}
            ),
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["registered"] is True
        assert body["device_token"] == token  # identity kept, not re-minted

        healed = devices.get(device_id)
        assert healed is not None and healed.kind_of == second_kind
        assert healed.manifest.get("access_token") == token
        # One row, no duplicate instance under another id.
        instances = [d for d in devices.all() if d.kind_of is not None and d.id == device_id]
        assert len(instances) == 1

        # And back again (gray unit reflashed to mono firmware).
        back = client.post(
            "/api/v1/device/discover",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {"device_id": device_id, "kind": first_kind, "fw_version": "1.7.1", "mac": mac}
            ),
        )
        assert back.status_code == 200
        assert devices.get(device_id).kind_of == first_kind


def test_heal_tracks_the_declared_e1001_grayscale_variant(app: Flask) -> None:
    """The E1001's two grayscale kinds are indistinguishable on the wire and
    exist only to separate OTA lineages, so the lineage has to follow the
    build actually flashed. Nothing the device sends identifies the glass;
    the firmware's own declaration is the sole authority, and the heal must
    honour it in both directions. Pinning that here because the alternative
    (freezing the kind) silently offers a reflashed panel the other variant's
    image, which leaves it unable to refresh until it is re-flashed by USB."""
    client = app.test_client()
    _sign_in(client)
    devices = app.config["DEVICE_REGISTRY"]
    device_id, mac = "study_e1001", "aa:bb:cc:dd:ee:13"

    code = _issue_pairing(app)
    first = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": device_id, "kind": "seeed_reterminal_e1001_gray", "mac": mac}
        ),
    )
    assert first.status_code == 201
    token = first.get_json()["device_token"]

    for declared in (
        "seeed_reterminal_e1001_gray_legacy",  # reflashed to the legacy build
        "seeed_reterminal_e1001_gray",  # and back to the default build
    ):
        resp = client.post(
            "/api/v1/device/discover",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"device_id": device_id, "kind": declared, "mac": mac}),
        )
        assert resp.status_code == 200
        healed = devices.get(device_id)
        assert healed is not None and healed.kind_of == declared
        # Same row, same credential: only the lineage moved.
        assert healed.manifest.get("access_token") == token


# -- auth --------------------------------------------------------------


def test_frame_endpoint_without_token_returns_401(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/api/v1/device/bedroom_pico/frame")
    assert resp.status_code == 401


def test_frame_endpoint_with_invalid_token_returns_401(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get(
        "/api/v1/device/bedroom_pico/frame",
        headers={"Authorization": "Bearer wrongtoken"},
    )
    assert resp.status_code == 401


def test_frame_endpoint_with_token_for_other_device_returns_403(app: Flask) -> None:
    """Bearer token is valid AND the URL id resolves to a real registered
    device, but the token belongs to a DIFFERENT device. That's the only
    genuine wrong-device auth failure; return 403 with a deliberately
    vague message so the response doesn't leak which device the token
    belongs to."""
    client = app.test_client()
    _sign_in(client)
    # Register two devices so both ids resolve. The token from the first
    # against the URL of the second is the case we're checking.
    code_a = _issue_pairing(app)
    resp_a = _register_via_api(client, code=code_a, device_id="bedroom_pico")
    token_a = resp_a.get_json()["device_token"]
    code_b = _issue_pairing(app)
    _register_via_api(client, code=code_b, device_id="kitchen_pico")

    resp = client.get(
        "/api/v1/device/kitchen_pico/frame",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 403
    body = resp.get_json()
    # Vague message on purpose: don't leak which device the token owns.
    assert "not valid for this device" in body["error"]


def test_frame_endpoint_with_nonexistent_device_id_returns_404(app: Flask) -> None:
    """A URL id that isn't a registered device returns 404, not 403.

    Splitting 404 (id doesn't exist) from 403 (id exists but wrong
    device) saves the firmware author five minutes of "is it me or
    the server" when their URL template forgets to substitute the id.
    Device ids are admin-chosen, not attacker-guessable, so the
    resource-existence signal isn't a meaningful leak."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    resp = client.get(
        "/api/v1/device/nonexistent_device/frame",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["status"] == 404
    assert "no device" in body["error"].lower()


def test_register_writes_instance_file_into_device_data_root(app: Flask) -> None:
    """The manifest has to land in ``data/devices/`` where the loader
    scans, not the parent data root. Writing to the parent orphans it:
    it never loads on restart and blocks re-pair with a 400 (#127)."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    assert resp.status_code == 201

    device_data_root = Path(app.config["DEVICE_DATA_ROOT"])
    data_root = Path(app.config["DATA_ROOT"])
    assert (device_data_root / "bedroom_pico.json").is_file()
    # Not orphaned one level up in the parent data root.
    assert not (data_root / "bedroom_pico.json").exists()


def test_register_echoes_canonical_lowercased_device_id(app: Flask) -> None:
    """Register lowercases the id on write, so the success response has
    to echo the canonical id back (like /discover does) or a firmware
    that keeps its mixed-case id 404s on every frame fetch (#128)."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="Bedroom_Pico")
    assert resp.status_code == 201
    assert resp.get_json()["device_id"] == "bedroom_pico"


def test_frame_fetch_tolerates_mixed_case_device_id_in_url(app: Flask) -> None:
    """A device that paired with a mixed-case id (stored lowercased) and
    kept the mixed-case form in its URL template must still resolve, not
    404. Regression for #128: the id in the path is normalised the same
    way register normalises it on write."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="Bedroom_Pico")
    token = resp.get_json()["device_token"]

    # Mixed-case id in the URL, canonical instance stored as bedroom_pico.
    resp = client.get(
        "/api/v1/device/Bedroom_Pico/frame",
        headers={"Authorization": f"Bearer {token}"},
    )
    # No push yet, so 204 (or 304/200 if one lands) — anything but the
    # 404 the casing mismatch used to produce.
    assert resp.status_code != 404
    assert resp.status_code in (200, 204, 304)


def test_blueprint_404_returns_json_not_html(app: Flask) -> None:
    """A stray /api/v1/device/... URL that doesn't match any registered
    route must return the same {status, error} JSON envelope the
    authed 4xx paths use, not Flask's default HTML 404. Firmware
    clients don't render HTML; this is a real regression Bernhard hit
    while wiring his CircuitPython client (POST to /status missing
    the id landed on Flask's 404 page)."""
    client = app.test_client()
    resp = client.get("/api/v1/device/")  # no id, no route
    assert resp.status_code == 404
    assert resp.mimetype == "application/json", (
        f"expected JSON 404 body, got mimetype={resp.mimetype!r}"
    )
    body = resp.get_json()
    assert body["status"] == 404
    assert "error" in body


def test_blueprint_405_returns_json(app: Flask) -> None:
    """Same rationale as the 404 handler: a firmware POSTing to a
    GET-only route (or vice-versa) should get a JSON body it can
    decode, not an HTML page."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    # /frame is GET-only; POST returns 405.
    resp = client.post(
        "/api/v1/device/bedroom_pico/frame",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 405
    assert resp.mimetype == "application/json"
    body = resp.get_json()
    assert body["status"] == 405


def test_status_endpoint_accepts_x_tesserae_token_header(app: Flask) -> None:
    """Some embedded HTTP libs make custom Authorization headers
    awkward; the X-Tesserae-Token fallback covers that case."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    resp = client.post(
        "/api/v1/device/bedroom_pico/status",
        headers={"X-Tesserae-Token": token, "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 72}),
    )
    assert resp.status_code == 200


def test_status_server_time_is_integer_epoch(app: Flask) -> None:
    """server_time must be an int, not a float (#143): a MicroPython /
    CircuitPython client parses a JSON float into a float32, which rounds the
    current epoch to the nearest ~2 minutes. register() carries it too."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    reg = _register_via_api(client, code=code, device_id="bedroom_pico")
    assert isinstance(reg.get_json()["server_time"], int)
    token = reg.get_json()["device_token"]

    resp = client.post(
        "/api/v1/device/bedroom_pico/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 72}),
    )
    assert resp.status_code == 200
    assert isinstance(resp.get_json()["server_time"], int)


# -- frame -------------------------------------------------------------


def test_frame_returns_204_when_nothing_rendered(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    resp = client.get(
        "/api/v1/device/bedroom_pico/frame",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204


def test_frame_returns_url_and_etag_when_rendered(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    # Fake a render. PushManager's latest_renders is the map the
    # endpoint reads from.
    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders["bedroom_pico"] = {
        "digest": "abc123",
        "ext": "bin",
        "filename": "abc123.bin",
        "renderer_id": "pico_bin",
        "timestamp": time.time(),
        "composition_digest": "comp123",
        "preview_digest": "preview123",
    }

    resp = client.get(
        "/api/v1/device/bedroom_pico/frame",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["render_id"] == "abc123"
    assert body["format"] == "bin"
    # The render URL carries a short-lived signature (issue #151) so a
    # device on a public host can fetch it; the path is unchanged.
    assert "/renders/abc123.bin" in body["url"]
    assert "sig=" in body["url"]
    assert resp.headers["ETag"] == '"abc123"'
    served = push_mgr.last_served_render_for("bedroom_pico")
    assert served["digest"] == "abc123"
    assert served["preview_digest"] == "preview123"
    # Stamped on handover so the Companion timeline has a start for its
    # progress interval (#232); a bare digest can't say when.
    assert served["served_at"] is not None


def test_frame_carries_button_wake_for_button_kind(app: Flask) -> None:
    """A button-capable kind (esp32) gets ``button_wake_s`` on /frame: the
    schema default, then the stored per-device value once set (#123)."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="hall_esp", kind="esp32_client")
    token = resp.get_json()["device_token"]

    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders["hall_esp"] = {
        "digest": "e0e0",
        "ext": "bin",
        "filename": "e0e0.bin",
        "renderer_id": "esp32_bin",
        "timestamp": time.time(),
        "composition_digest": "comp_e0",
    }

    def _frame() -> dict:
        r = client.get(
            "/api/v1/device/hall_esp/frame",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        return r.get_json()

    # Schema default is 0 (deep-sleep immediately).
    assert _frame()["button_wake_s"] == 0

    # A stored per-device value overrides the default.
    store = app.config["SETTINGS_STORE"]
    section = store.get_section("devices") or {}
    section["hall_esp"] = {**section.get("hall_esp", {}), "button_wake_s": 5}
    store.update_section("devices", section)
    assert _frame()["button_wake_s"] == 5


def test_frame_omits_button_wake_for_non_button_kind(app: Flask) -> None:
    """A kind whose schema doesn't declare ``button_wake_s`` (pico) never
    carries the field, so the /frame envelope stays clean for it."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="desk_pico", kind="pico_bin_client")
    token = resp.get_json()["device_token"]

    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders["desk_pico"] = {
        "digest": "d1d1",
        "ext": "bin",
        "filename": "d1d1.bin",
        "renderer_id": "pico_bin",
        "timestamp": time.time(),
        "composition_digest": "comp_d1",
    }
    resp = client.get(
        "/api/v1/device/desk_pico/frame",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "button_wake_s" not in resp.get_json()


def _stage_ota(
    app: Flask, device_id: str, *, kind: str = "esp32_client", fw: str = "1.4.0", schema: int = 1
) -> dict:
    """Sign a descriptor with the test fixtures key and stage it for a device."""
    from app.ota import build_manifest, load_private_key, sign_manifest

    seed = bytes.fromhex(
        (REPO_ROOT / "tests" / "fixtures" / "ota" / "test_signing_key.hex").read_text().strip()
    )
    manifest = build_manifest(
        key_id="test-ed25519-1",
        device_kind=kind,
        fw_version=fw,
        image_url="https://cdn.example.test/app.bin",
        image=b"firmware-bytes",
    )
    descriptor = sign_manifest(manifest, load_private_key(seed))
    app.config["OTA_STAGING"].stage(
        device_id, descriptor, device_kind=kind, fw_version=fw, schema_version=schema
    )
    return descriptor


def _post_status(client, device_id: str, token: str, body: dict) -> Any:
    return client.post(
        f"/api/v1/device/{device_id}/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps(body),
    )


def _register_esp32(app: Flask, client, device_id: str) -> str:
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id=device_id, kind="esp32_client")
    return resp.get_json()["device_token"]


def test_status_delivers_staged_ota_when_device_is_capable(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "hall_esp")
    descriptor = _stage_ota(app, "hall_esp")

    resp = _post_status(client, "hall_esp", token, {"ota": {"schema": 1}, "battery_mv": 4000})
    assert resp.status_code == 200
    assert resp.get_json()["ota"] == descriptor


def test_status_omits_ota_when_device_does_not_advertise(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "hall_esp")
    _stage_ota(app, "hall_esp")

    resp = _post_status(client, "hall_esp", token, {"battery_mv": 4000})
    assert resp.status_code == 200
    assert "ota" not in resp.get_json()


def test_status_omits_ota_when_nothing_staged(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "hall_esp")

    resp = _post_status(client, "hall_esp", token, {"ota": {"schema": 1}})
    assert resp.status_code == 200
    assert "ota" not in resp.get_json()


def test_status_omits_ota_when_descriptor_schema_newer_than_device(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "hall_esp")
    _stage_ota(app, "hall_esp", schema=2)

    resp = _post_status(client, "hall_esp", token, {"ota": {"schema": 1}})
    assert resp.status_code == 200
    assert "ota" not in resp.get_json()


def test_status_omits_ota_when_staged_for_other_kind(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "hall_esp")
    # A descriptor mis-staged for a different kind must not reach this device.
    _stage_ota(app, "hall_esp", kind="pico_bin_client")

    resp = _post_status(client, "hall_esp", token, {"ota": {"schema": 1}})
    assert resp.status_code == 200
    assert "ota" not in resp.get_json()


def _release_descriptor(kind: str = "esp32_client", fw: str = "1.5.0") -> dict:
    from app.ota import build_manifest, load_private_key, sign_manifest

    seed = bytes.fromhex(
        (REPO_ROOT / "tests" / "fixtures" / "ota" / "test_signing_key.hex").read_text().strip()
    )
    manifest = build_manifest(
        key_id="test-ed25519-1",
        device_kind=kind,
        fw_version=fw,
        image_url="https://cdn.example.test/app.bin",
        image=b"firmware-bytes",
    )
    return sign_manifest(manifest, load_private_key(seed))


def test_status_offers_kind_release_to_canary_when_newer(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "hall_esp")
    desc = _release_descriptor(fw="1.5.0")
    app.config["OTA_RELEASE"].set_target(
        "esp32_client", desc, fw_version="1.5.0", canary_device_ids=["hall_esp"]
    )
    # Canary device, reports an older version, advertises OTA -> offered.
    resp = _post_status(client, "hall_esp", token, {"ota": {"schema": 1}, "fw_version": "1.4.0"})
    assert resp.status_code == 200
    assert resp.get_json()["ota"] == desc


def test_status_withholds_kind_release_from_non_canary_until_promoted(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "hall_esp")
    app.config["OTA_RELEASE"].set_target(
        "esp32_client",
        _release_descriptor(fw="1.5.0"),
        fw_version="1.5.0",
        canary_device_ids=["other"],
    )
    body = {"ota": {"schema": 1}, "fw_version": "1.4.0"}
    assert "ota" not in _post_status(client, "hall_esp", token, body).get_json()
    # After promote, the same device is offered it.
    app.config["OTA_RELEASE"].promote("esp32_client")
    assert "ota" in _post_status(client, "hall_esp", token, body).get_json()


def test_status_withholds_kind_release_when_not_newer(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "hall_esp")
    app.config["OTA_RELEASE"].set_target(
        "esp32_client",
        _release_descriptor(fw="1.5.0"),
        fw_version="1.5.0",
        canary_device_ids=["hall_esp"],
    )
    # Device already on 1.5.0 -> not offered.
    body = {"ota": {"schema": 1}, "fw_version": "1.5.0"}
    assert "ota" not in _post_status(client, "hall_esp", token, body).get_json()


def test_status_paused_release_offers_nothing(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "hall_esp")
    app.config["OTA_RELEASE"].set_target(
        "esp32_client",
        _release_descriptor(fw="1.5.0"),
        fw_version="1.5.0",
        canary_device_ids=["hall_esp"],
    )
    app.config["OTA_RELEASE"].pause("esp32_client")
    body = {"ota": {"schema": 1}, "fw_version": "1.4.0"}
    assert "ota" not in _post_status(client, "hall_esp", token, body).get_json()


def test_status_per_device_stage_wins_over_kind_release(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "hall_esp")
    staged = _stage_ota(app, "hall_esp", fw="1.6.0")
    app.config["OTA_RELEASE"].set_target(
        "esp32_client",
        _release_descriptor(fw="1.5.0"),
        fw_version="1.5.0",
        canary_device_ids=["hall_esp"],
    )
    # Explicit per-device stage takes precedence over the kind release.
    resp = _post_status(client, "hall_esp", token, {"ota": {"schema": 1}, "fw_version": "1.4.0"})
    assert resp.get_json()["ota"] == staged


def test_frame_response_carries_renderer_payload_fields(app: Flask) -> None:
    """REST /frame must return the renderer-specific fields its MQTT-
    subscribed cousins receive (rotate / scale / bg / saturation for
    pi_png, etc.), not just the REST-shape envelope. A real pi_png
    client logs ``payload missing 'rotate'`` and skips the paint
    otherwise."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="lounge_pi", kind="pi_png_client")
    token = resp.get_json()["device_token"]

    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders["lounge_pi"] = {
        "digest": "ab9dbd3be",
        "ext": "png",
        "filename": "ab9dbd3be.png",
        "renderer_id": "pi_png",
        "timestamp": time.time(),
        "composition_digest": "comp_ab9",
    }

    resp = client.get(
        "/api/v1/device/lounge_pi/frame",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    # REST-shape envelope still present.
    assert body["render_id"] == "ab9dbd3be"
    assert body["format"] == "png"
    # Renderer-specific fields (the v3-frozen pi_png payload) merged in.
    assert "rotate" in body, body
    assert "scale" in body
    assert "bg" in body
    assert "saturation" in body


def test_frame_returns_304_when_if_none_match_matches(app: Flask) -> None:
    """The save-the-firmware-a-fetch-and-paint path. A deep-sleep
    client whose last-seen ETag matches the current frame can skip
    everything except the status post."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders["bedroom_pico"] = {
        "digest": "abc123",
        "ext": "bin",
        "filename": "abc123.bin",
        "renderer_id": "pico_bin",
        "timestamp": time.time(),
        "composition_digest": "comp123",
        "preview_digest": "preview123",
    }

    resp = client.get(
        "/api/v1/device/bedroom_pico/frame",
        headers={
            "Authorization": f"Bearer {token}",
            "If-None-Match": '"abc123"',
        },
    )
    assert resp.status_code == 304
    assert resp.headers["ETag"] == '"abc123"'
    # Content-Location echoes the canonical frame URL on 304 too so a
    # client that boots without a cached URL (non-e-ink panels, factory
    # reset, etc.) can still re-fetch the image. RFC 7231 §3.1.4.2
    # explicitly permits Content-Location on 304.
    assert "/renders/abc123.bin" in resp.headers["Content-Location"]
    assert push_mgr.last_served_render_for("bedroom_pico")["digest"] == "abc123"
    assert push_mgr.last_served_render_for("bedroom_pico")["preview_digest"] == "preview123"
    assert push_mgr.has_pending_render("bedroom_pico") is False


def test_fetch_latest_action_bypasses_matching_etag_without_rendering(app: Flask) -> None:
    """``fetch_latest`` returns the existing artefact with 200 while
    leaving the render pipeline and latest-render content untouched."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    settings = app.config["SETTINGS_STORE"]
    devices = settings.get_section("devices") or {}
    devices["bedroom_pico"] = {
        **devices.get("bedroom_pico", {}),
        "button_map": {"custom": "fetch_latest"},
    }
    settings.update_section("devices", devices)

    push_mgr = app.config["PUSH_MANAGER"]
    latest = {
        "digest": "abc123",
        "ext": "bin",
        "filename": "abc123.bin",
        "renderer_id": "pico_bin",
        "timestamp": time.time(),
        "composition_digest": "comp123",
    }
    push_mgr._latest_renders["bedroom_pico"] = latest.copy()

    resp = client.get(
        "/api/v1/device/bedroom_pico/frame?button=custom&button_event_id=1",
        headers={
            "Authorization": f"Bearer {token}",
            "If-None-Match": '"abc123"',
        },
    )

    assert resp.status_code == 200
    assert resp.get_json()["render_id"] == "abc123"
    stored = push_mgr._latest_renders["bedroom_pico"]
    assert {key: stored[key] for key in latest} == latest
    assert stored["last_served_digest"] == "abc123"


def test_frame_accepts_legacy_event_alias_and_prefers_canonical_id(app: Flask) -> None:
    """Firmware through v1.5.0 sent ``event`` on /frame. Accept it as
    an alias, but let an explicitly supplied ``button_event_id`` win."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    settings = app.config["SETTINGS_STORE"]
    devices = settings.get_section("devices") or {}
    devices["bedroom_pico"] = {
        **devices.get("bedroom_pico", {}),
        "button_map": {"custom": "fetch_latest"},
    }
    settings.update_section("devices", devices)
    app.config["PUSH_MANAGER"]._latest_renders["bedroom_pico"] = {
        "digest": "abc123",
        "ext": "bin",
        "filename": "abc123.bin",
        "renderer_id": "pico_bin",
        "timestamp": time.time(),
        "composition_digest": "comp123",
    }
    headers = {"Authorization": f"Bearer {token}"}

    first = client.get(
        "/api/v1/device/bedroom_pico/frame?button=custom&button_event_id=7&event=6",
        headers=headers,
    )
    retry = client.get(
        "/api/v1/device/bedroom_pico/frame?button=custom&event=7",
        headers=headers,
    )

    assert first.status_code == 200
    assert retry.status_code == 200
    rows = [
        row
        for row in app.config["EVENT_LOG"].list(type="push")
        if row.source == "button" and row.target == "bedroom_pico"
    ]
    assert [row.status for row in rows[:2]] == ["deduped", "fetched"]
    assert [row.extra["button_event_id"] for row in rows[:2]] == [7, 7]


def test_resend_forces_one_200_then_reverts_to_304(app: Flask) -> None:
    """Resend from History (#119): a frame flagged ``force_refetch`` serves
    one 200 to a REST client even when its ETag matches (so an explicit
    resend re-paints identical content, matching MQTT's force_publish),
    then clears the flag so the next unchanged poll is 304 again."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders["bedroom_pico"] = {
        "digest": "abc123",
        "ext": "bin",
        "filename": "abc123.bin",
        "renderer_id": "pico_bin",
        "timestamp": time.time(),
        "composition_digest": "comp123",
        "force_refetch": True,
    }

    headers = {"Authorization": f"Bearer {token}", "If-None-Match": '"abc123"'}
    # First poll: matching ETag would normally 304, but the resend flag
    # forces a 200 so the panel re-paints.
    first = client.get("/api/v1/device/bedroom_pico/frame", headers=headers)
    assert first.status_code == 200
    assert first.headers["ETag"] == '"abc123"'
    # Flag consumed: the next unchanged poll is back to 304.
    second = client.get("/api/v1/device/bedroom_pico/frame", headers=headers)
    assert second.status_code == 304


def test_frame_returns_content_location_on_200(app: Flask) -> None:
    """The Content-Location header ships on 200 too so caching clients
    that don't parse the JSON body still have the canonical URL."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders["bedroom_pico"] = {
        "digest": "def456",
        "ext": "png",
        "filename": "def456.png",
        "renderer_id": "circuitpython_png",
        "timestamp": time.time(),
        "composition_digest": "comp456",
    }

    resp = client.get(
        "/api/v1/device/bedroom_pico/frame",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "/renders/def456.png" in resp.headers["Content-Location"]
    # And matches the body's ``url`` field.
    assert resp.get_json()["url"] == resp.headers["Content-Location"]


# -- status ------------------------------------------------------------


def test_status_response_includes_config_and_next_poll(app: Flask) -> None:
    """One round-trip per wake: status post returns the latest config
    + when to poll again. Firmware doesn't need a separate poll."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    resp = client.post(
        "/api/v1/device/bedroom_pico/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_mv": 3850, "battery_pct": 72, "rssi": -64}),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert "config" in body
    assert "next_poll_s" in body
    # Default for pico_bin_client is 60s per device.json schema (every
    # device kind now defaults to a tight cadence so newly-paired
    # devices stay responsive until the user picks a longer one).
    assert body["next_poll_s"] == 60
    # The merged status is now in the cache, in the same shape the MQTT
    # path uses ({"received_at": ts, "parsed": {...}}) so the Devices
    # UI's _status_view reads it correctly.
    cache = app.config["DEVICE_STATUS"]
    record = cache["bedroom_pico"]
    assert record["parsed"]["battery_pct"] == 72
    assert record["received_at"] > 0


def test_status_response_carries_local_time_fields(app: Flask) -> None:
    """The ``/status`` response carries resolved local-time fields so
    memory-constrained clients (CircuitPython, MicroPython) don't have
    to ship the IANA tz database. ``tz`` in the heartbeat body picks
    the resolution zone; absent / invalid → server's setting / system.

    Four fields are always present in the response: ``local_time`` (ISO
    8601 with offset), ``tz`` (echo of what was actually used),
    ``tz_offset_seconds`` (so RTC-equipped clients can derive without
    round-trips), and ``dst_active`` (informational)."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="tz_pico")
    token = resp.get_json()["device_token"]

    # Heartbeat-sent IANA tz wins. Berlin in June = AEDT (CEST), UTC+2,
    # DST active.
    resp = client.post(
        "/api/v1/device/tz_pico/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 50, "tz": "Europe/Berlin"}),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tz"] == "Europe/Berlin"
    assert body["tz_offset_seconds"] == 7200  # +02:00
    assert body["dst_active"] is True
    # ISO 8601 with offset suffix; +02:00 for Berlin in summer.
    assert "+02:00" in body["local_time"]


def test_status_response_falls_back_when_tz_is_invalid(app: Flask) -> None:
    """A garbled IANA name in the heartbeat doesn't break the response
    or surface as an error; it silently falls through to the server's
    settings / system tz, and the response echoes what was used."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="badtz_pico")
    token = resp.get_json()["device_token"]

    resp = client.post(
        "/api/v1/device/badtz_pico/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 50, "tz": "Not/A_Real_Zone"}),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    # tz field always present + always a real IANA name (or UTC).
    assert isinstance(body["tz"], str) and body["tz"]
    assert isinstance(body["tz_offset_seconds"], int)
    assert isinstance(body["dst_active"], bool)
    # local_time is a parseable ISO 8601 string.
    from datetime import datetime

    datetime.fromisoformat(body["local_time"])  # raises if malformed


def test_status_response_local_time_fields_optional_request(app: Flask) -> None:
    """A heartbeat that doesn't send ``tz`` still gets the four
    local-time fields back; server falls back to its own setting /
    system tz. Existing clients that pre-date the addition pay nothing."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="notz_pico")
    token = resp.get_json()["device_token"]

    resp = client.post(
        "/api/v1/device/notz_pico/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 50}),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert "local_time" in body
    assert "tz" in body and isinstance(body["tz"], str) and body["tz"]
    assert "tz_offset_seconds" in body
    assert "dst_active" in body


def test_rest_status_updates_received_at_so_last_seen_is_fresh(app: Flask) -> None:
    """v0.53 regression: a REST device heartbeat must write
    ``received_at`` to the status cache so the Devices admin page's
    "last seen" / freshness dot updates instead of reading 0 (epoch).

    Pre-v0.53.1 the REST handler wrote a flat dict with ``last_seen``;
    the UI reads ``received_at``, so REST devices appeared stuck at
    "20624 days ago" in the admin UI. This test pins the field
    contract so a future refactor can't reintroduce the drift."""
    import time as _t

    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="freshness_pico")
    token = resp.get_json()["device_token"]

    before = _t.time()
    resp = client.post(
        "/api/v1/device/freshness_pico/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 50, "rssi": -55}),
    )
    after = _t.time()
    assert resp.status_code == 200

    cache = app.config["DEVICE_STATUS"]["freshness_pico"]
    # The Devices UI's _status_view reads cache.get("received_at", 0)
    # then computes ``age = now - received_at``. If this returns 0 the
    # UI shows "20624 days ago".
    assert "received_at" in cache, "received_at must be set on REST heartbeats"
    assert before <= cache["received_at"] <= after + 1
    # Parsed dict lives nested under "parsed", same as the MQTT path,
    # so _status_view's cache.get("parsed", {}) finds the diagnostic
    # fields instead of returning empty.
    assert cache["parsed"]["battery_pct"] == 50
    assert cache["parsed"]["rssi"] == -55


def test_rest_status_records_battery_history(app: Flask) -> None:
    """The device_battery widget plots heartbeats from the
    BATTERY_HISTORY store. Pre-v0.53.1 the REST handler skipped this
    side effect, so REST devices never showed up on the battery
    page. After the fix, a heartbeat with battery_pct lands a sample."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="batt_pico")
    token = resp.get_json()["device_token"]

    history = app.config.get("BATTERY_HISTORY")
    assert history is not None
    # Drive a status post with a battery reading.
    resp = client.post(
        "/api/v1/device/batt_pico/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 80, "battery_mv": 3900, "rssi": -50}),
    )
    assert resp.status_code == 200
    samples = list(history.recent("batt_pico", limit=10))
    assert samples, "REST heartbeat must record into battery history"
    assert samples[0].pct == 80


# -- log ---------------------------------------------------------------


def test_discover_endpoint_adds_to_discovery_cache(app: Flask) -> None:
    """REST discovery announce: a firmware without a pairing code POSTs
    to /discover, the entry shows up in the DiscoveryCache, and the
    Settings -> Devices page picks it up alongside MQTT-discovered
    devices."""
    client = app.test_client()
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "fresh_pico",
                "kind": "pico_bin_client",
                "panel_w": 1600,
                "panel_h": 1200,
                "fw_version": "0.0.1",
                "mac": "aabbccddeeff",
            }
        ),
    )
    assert resp.status_code == 200
    cache = app.config["DISCOVERY_CACHE"]
    discovered = {d.id: d for d in cache.all()}
    assert "fresh_pico" in discovered
    assert discovered["fresh_pico"].parsed.get("kind") == "pico_bin_client"


def test_discover_endpoint_rejects_missing_device_id(app: Flask) -> None:
    client = app.test_client()
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"kind": "pico_bin_client"}),
    )
    assert resp.status_code == 400


def test_discover_endpoint_tags_cache_entry_as_rest(app: Flask) -> None:
    """The cache entry needs a ``transport: "rest"`` hint so the
    admin's Register click on Settings -> Devices creates a REST-
    mode instance (not an MQTT one)."""
    client = app.test_client()
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "fresh_pico_2",
                "kind": "pico_bin_client",
                "mac": "aabbccddeeff",
            }
        ),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["registered"] is False
    cache = app.config["DISCOVERY_CACHE"]
    entry = cache.get("fresh_pico_2")
    assert entry is not None
    assert entry.parsed.get("transport") == "rest"


def test_discover_returns_token_when_mac_matches_registered_instance(app: Flask) -> None:
    """Discover-then-claim flow: admin clicked Register on a previous
    Discovered entry, the resulting instance has the firmware's MAC
    on its manifest. On the firmware's next discover POST the MAC
    matches and the server returns the device's access token. No
    pairing code involved."""
    # Pre-register an instance with the MAC that the firmware will
    # present next. Mirrors what devices_register_discovered would
    # have done after the admin clicked Register.
    from app import device_service

    devices_registry = app.config["DEVICE_REGISTRY"]
    renderers = app.config["RENDERER_REGISTRY"]
    result = device_service.create_instance(
        devices=devices_registry,
        renderers=renderers,
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id="claimed_pico",
        kind_id="pico_bin_client",
        mac="aa:bb:cc:dd:ee:ff",
        transport="rest",
    )
    assert result.device is not None
    expected_token = result.device.manifest["access_token"]

    # Firmware does its discover POST with the matching MAC.
    client = app.test_client()
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "claimed_pico",
                "kind": "pico_bin_client",
                "mac": "AABBCCDDEEFF",  # different format, same MAC
            }
        ),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["registered"] is True
    assert body["device_token"] == expected_token
    assert body["device_id"] == "claimed_pico"
    assert "config" in body
    # Announced id matched the stored one, so nothing changed. The
    # headers are still present, so a client can tell this apart from a
    # server too old to report it.
    assert body["device_id_changed"] is False
    assert "announced_device_id" not in body
    assert resp.headers["X-Tesserae-Device-Id"] == "claimed_pico"
    assert resp.headers["X-Tesserae-Device-Id-Changed"] == "false"


def test_discover_flags_a_device_id_the_server_did_not_keep(app: Flask) -> None:
    """The MAC is the identity on this path, so announcing a different
    device_id still claims the matched instance and returns the stored
    id. That's deliberate (a re-flash with wiped settings re-acquires
    this way), but the response has to say so, or a client keeps using
    an id the server doesn't have and only notices by diffing the echo
    (issue #239)."""
    from app import device_service

    result = device_service.create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id="device_a",
        kind_id="pico_bin_client",
        mac="aa:bb:cc:dd:ee:ff",
        transport="rest",
    )
    assert result.device is not None
    expected_token = result.device.manifest["access_token"]

    resp = app.test_client().post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "device_b",
                "kind": "pico_bin_client",
                "mac": "aa:bb:cc:dd:ee:ff",
            }
        ),
    )

    # Still a success: the client is paired and the token works. An
    # error here would strand the re-flash case this path exists for.
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["registered"] is True
    assert body["device_token"] == expected_token
    assert body["device_id"] == "device_a"
    assert body["device_id_changed"] is True
    assert body["announced_device_id"] == "device_b"
    assert resp.headers["X-Tesserae-Device-Id"] == "device_a"
    assert resp.headers["X-Tesserae-Device-Id-Changed"] == "true"

    # The announced id is reported, not adopted: no second instance, and
    # the matched one keeps its id.
    assert app.config["DEVICE_REGISTRY"].get("device_b") is None
    assert app.config["DEVICE_REGISTRY"].get("device_a") is not None


def test_discover_rejects_missing_mac(app: Flask) -> None:
    """A discover POST without a MAC has nothing for the claim path to
    match on later: the announce would land in the Discovered strip,
    register cleanly, and then poll forever on ``registered: false``
    (issue #226). Reject it with a reason instead, and keep it out of
    the cache so no admin registers a pairing that can't complete."""
    client = app.test_client()
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"device_id": "no_mac_pico", "kind": "pico_bin_client"}),
    )
    assert resp.status_code == 400
    assert "mac" in resp.get_json()["error"].lower()
    assert app.config["DISCOVERY_CACHE"].get("no_mac_pico") is None


def test_discover_rejects_blank_or_placeholder_mac(app: Flask) -> None:
    """``mac: null`` and the placeholders that reach the wire as strings
    are all treated as "no MAC". A client formatting its own null into
    the body sends ``"None"``; a driver polled before the radio is up
    reports the all-zero or broadcast MAC. Taking any of them at face
    value is worse than rejecting: see the collision test below."""
    client = app.test_client()
    for value in (
        None,
        "",
        "   ",
        "None",
        "none",
        "null",
        "n/a",
        "undefined",
        "00:00:00:00:00:00",
        "FF-FF-FF-FF-FF-FF",
    ):
        resp = client.post(
            "/api/v1/device/discover",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"device_id": "blank_mac_pico", "mac": value}),
        )
        assert resp.status_code == 400, f"{value!r} should not pair"
        assert app.config["DISCOVERY_CACHE"].get("blank_mac_pico") is None


def test_discover_never_claims_a_token_via_a_placeholder_mac(app: Flask) -> None:
    """Two clients sending the same placeholder are not the same device.
    Before #226 the second one matched the first one's instance and was
    handed its access token, so a placeholder has to stay unclaimable
    even when an older install already persisted one (no migration: the
    stored value simply stops matching)."""
    from app import device_service

    result = device_service.create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id="legacy_panel",
        kind_id="pico_bin_client",
        mac="None",  # what a pre-fix install persisted
        transport="rest",
    )
    assert result.device is not None
    victim_token = result.device.manifest["access_token"]

    client = app.test_client()
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"device_id": "other_panel", "kind": "pico_bin_client", "mac": "None"}),
    )
    assert resp.status_code == 400
    assert victim_token not in resp.get_data(as_text=True)


def test_register_does_not_persist_a_placeholder_mac(app: Flask) -> None:
    """The pairing-code path stores whatever MAC it's given. A stored
    placeholder would be claimable by any client sending the same string
    on /discover, so it's dropped: pairing still succeeds, the device
    just has no MAC to auto-claim with (issue #226)."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": "coded_pico", "kind": "pico_bin_client", "mac": "None"}),
    )
    assert resp.status_code == 201
    stored = app.config["DEVICE_REGISTRY"].get("coded_pico").manifest.get("mac")
    assert not stored, f"placeholder MAC persisted as {stored!r}"


def test_discover_endpoint_rate_limited(app: Flask) -> None:
    """Discovery shares the register endpoint's rate limiter; an
    attacker spamming discoveries gets 429 after the cap."""
    from app.state.rate_limiter import RateLimiter

    app.config["REGISTER_RATE_LIMITER"] = RateLimiter(max_attempts=2, window_s=60)
    client = app.test_client()
    body = json.dumps(
        {"device_id": "spam_pico", "kind": "pico_bin_client", "mac": "aa:bb:cc:00:00:01"}
    )
    for _ in range(2):
        resp = client.post(
            "/api/v1/device/discover",
            headers={"Content-Type": "application/json"},
            data=body,
        )
        assert resp.status_code == 200
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=body,
    )
    assert resp.status_code == 429


def test_admin_pairing_issue_requires_session(app: Flask) -> None:
    """Admin endpoints are session-gated despite living under /api/v1/
    (which is otherwise auth-bypassed). Unauth'd callers get 401."""
    client = app.test_client()
    # No /setup call -> no session.
    resp = client.post("/api/v1/device/admin/pairing/issue")
    assert resp.status_code == 401


def test_admin_pairing_issue_returns_code_when_authed(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/api/v1/device/admin/pairing/issue")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"].isdigit() and len(body["code"]) == 6
    # Code is consumable by the register endpoint.
    code = body["code"]
    reg = _register_via_api(client, code=code, device_id="just_for_test")
    assert reg.status_code == 201


def test_register_marks_instance_as_rest_transport(app: Flask) -> None:
    """Phase 1b: a REST-registered instance carries ``transport: "rest"``
    on its manifest so the push pipeline knows to skip MQTT publish."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    _register_via_api(client, code=code, device_id="bedroom_pico")
    devices = app.config["DEVICE_REGISTRY"]
    instance = devices.get("bedroom_pico")
    assert instance is not None
    assert instance.transport == "rest"
    assert instance.manifest.get("transport") == "rest"


def test_existing_mqtt_instances_default_to_mqtt_transport(app: Flask) -> None:
    """Backward compat: a pre-0.52 instance manifest with no ``transport``
    field reads as MQTT, which keeps the push pipeline behaving as before."""
    devices = app.config["DEVICE_REGISTRY"]
    # Pi BIN client kinds ship without a transport field on the kind
    # manifest (transport is per-instance). The kind itself, being
    # treated as a Device, should default to mqtt.
    pi_kind = devices.get("pi_bin_client")
    assert pi_kind is not None
    assert pi_kind.transport == "mqtt"


def test_push_pipeline_skips_publish_for_rest_devices(app: Flask) -> None:
    """End-to-end: a REST-mode instance's renderer clone is detected as
    http-polled, so ``_renderer_is_http_polled`` short-circuits the MQTT
    publish in ``_publish_artifact``. The transport mock would otherwise
    record a publish call we don't want."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    _register_via_api(client, code=code, device_id="bedroom_pico")
    push_mgr = app.config["PUSH_MANAGER"]
    renderers = app.config["RENDERER_REGISTRY"]
    # The cloned renderer for our REST device.
    clone = renderers.get("pico_bin__bedroom_pico")
    assert clone is not None
    assert push_mgr._renderer_is_http_polled(clone) is True
    # And the base mqtt-bound renderer is NOT marked http-polled, so
    # existing MQTT clients still publish normally.
    base = renderers.get("pi_bin")
    assert base is not None
    assert push_mgr._renderer_is_http_polled(base) is False


def test_admin_pairing_pending_lists_unredeemed_codes(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    a = client.post("/api/v1/device/admin/pairing/issue").get_json()["code"]
    b = client.post("/api/v1/device/admin/pairing/issue").get_json()["code"]
    resp = client.get("/api/v1/device/admin/pairing/pending")
    assert resp.status_code == 200
    codes = [p["code"] for p in resp.get_json()["pending"]]
    assert a in codes and b in codes


def test_log_endpoint_appends_to_event_log(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    resp = client.post(
        "/api/v1/device/bedroom_pico/log",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"level": "warn", "msg": "panel busy timeout"}),
    )
    assert resp.status_code == 200
    events = app.config["EVENT_LOG"]
    rows = events.list(type="device", limit=10)
    matches = [r for r in rows if r.target == "client_log" and r.source == "bedroom_pico"]
    assert matches, "no client_log event recorded"
    assert matches[0].status == "warn"
    assert matches[0].extra.get("msg") == "panel busy timeout"


def test_log_endpoint_accepts_list_msg_and_joins_with_newlines(app: Flask) -> None:
    """Memory-constrained MicroPython / CircuitPython clients can
    POST ``msg`` as a list of strings (typically
    ``traceback.format_exception()`` output) instead of joining
    them on-device, which would force an extra heap allocation at
    exactly the moment they most want to log, mid-exception."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    lines = [
        "Traceback (most recent call last):",
        '  File "main.py", line 42, in <module>',
        "    paint(frame)",
        "RuntimeError: panel busy",
    ]
    resp = client.post(
        "/api/v1/device/bedroom_pico/log",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"level": "error", "msg": lines}),
    )
    assert resp.status_code == 200
    events = app.config["EVENT_LOG"]
    rows = events.list(type="device", limit=10)
    matches = [r for r in rows if r.target == "client_log" and r.source == "bedroom_pico"]
    assert matches, "no client_log event recorded"
    assert matches[0].extra.get("msg") == "\n".join(lines)
    # Error-level entries also flow into the row's ``error`` slot.
    assert matches[0].error == "\n".join(lines)


def test_log_endpoint_strips_per_line_trailing_newlines_before_joining(app: Flask) -> None:
    """``traceback.format_exception()`` returns lines already
    terminated with ``\\n``. A naive ``"\\n".join(...)`` would emit
    double newlines and surface as blank lines between every
    traceback row on the Events page. ``_coerce_log_msg`` strips
    one trailing newline per line before joining so both shapes
    (pre-newlined and hand-crafted) produce clean output."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    # Mirrors ``traceback.format_exception()``: each entry ends in
    # ``\n``. Expected joined output has SINGLE newlines between
    # rows and no trailing newline; not the doubled-newline gunk a
    # naive join would produce.
    pre_newlined = [
        "Traceback (most recent call last):\n",
        '  File "main.py", line 42, in <module>\n',
        "    paint(frame)\n",
        "RuntimeError: panel busy\n",
    ]
    resp = client.post(
        "/api/v1/device/bedroom_pico/log",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"level": "error", "msg": pre_newlined}),
    )
    assert resp.status_code == 200
    events = app.config["EVENT_LOG"]
    rows = events.list(type="device", limit=10)
    matches = [r for r in rows if r.target == "client_log" and r.source == "bedroom_pico"]
    expected = (
        "Traceback (most recent call last):\n"
        '  File "main.py", line 42, in <module>\n'
        "    paint(frame)\n"
        "RuntimeError: panel busy"
    )
    assert matches[0].extra.get("msg") == expected
    # Defensive: confirm no double-newlines snuck in.
    assert "\n\n" not in matches[0].extra.get("msg")


def test_log_endpoint_caps_oversized_msg_at_4kb(app: Flask) -> None:
    """A noisy client can't flood the EventLog one entry at a time:
    msg is capped at 4 KB (post-join for list inputs)."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = _register_via_api(client, code=code, device_id="bedroom_pico")
    token = resp.get_json()["device_token"]

    huge = "x" * 10_000
    resp = client.post(
        "/api/v1/device/bedroom_pico/log",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"level": "info", "msg": huge}),
    )
    assert resp.status_code == 200
    events = app.config["EVENT_LOG"]
    rows = events.list(type="device", limit=10)
    matches = [r for r in rows if r.target == "client_log" and r.source == "bedroom_pico"]
    assert matches
    assert len(matches[0].extra.get("msg")) == 4096


# -- CORS ---------------------------------------------------------------


def test_cors_headers_present_on_normal_response(app: Flask) -> None:
    """Browser-based callers (the device emulator at
    emulator.tesserae.ink, future in-browser test-push UI) need the
    Access-Control-Allow-* headers on every REST API response."""
    client = app.test_client()
    resp = client.get("/api/v1/device/nonexistent/frame")
    # Auth fails before frame logic, but the after_request hook still
    # paints the headers — that's what we're verifying.
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    assert "GET" in resp.headers.get("Access-Control-Allow-Methods", "")
    assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")
    assert "Authorization" in resp.headers.get("Access-Control-Allow-Headers", "")
    expose = resp.headers.get("Access-Control-Expose-Headers", "")
    assert "ETag" in expose
    assert "Content-Location" in expose


def test_cors_preflight_returns_204(app: Flask) -> None:
    """OPTIONS preflight short-circuits to 204 with the CORS headers
    attached, instead of falling through to the route handlers
    (which would 405 on an OPTIONS request)."""
    client = app.test_client()
    resp = client.options(
        "/api/v1/device/bedroom_pico/frame",
        headers={
            "Origin": "https://emulator.tesserae.ink",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert resp.status_code == 204
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    assert "Authorization" in resp.headers.get("Access-Control-Allow-Headers", "")


def test_cors_preflight_register_allows_pairing_code_header(app: Flask) -> None:
    """The /register endpoint reads the 6-digit pair code from an
    ``X-Pairing-Code`` header (see app/rest_api.py:post_register).
    The browser sends a preflight asking whether that header is
    allowed before firing the real POST; the response must list it
    or Chrome / Safari refuse to send the request."""
    client = app.test_client()
    resp = client.options(
        "/api/v1/device/register",
        headers={
            "Origin": "http://localhost:8080",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Pairing-Code, Content-Type",
        },
    )
    assert resp.status_code == 204
    allowed = resp.headers.get("Access-Control-Allow-Headers", "")
    assert "X-Pairing-Code" in allowed
    assert "Content-Type" in allowed


def test_cors_preflight_for_status_post(app: Flask) -> None:
    """The status endpoint is the POST path the emulator hits most
    often (heartbeats every poll). Confirm OPTIONS works there too."""
    client = app.test_client()
    resp = client.options("/api/v1/device/bedroom_pico/status")
    assert resp.status_code == 204
    assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")


def test_cors_headers_on_auth_failure(app: Flask) -> None:
    """A 401 from a missing/invalid token must still carry the CORS
    headers — otherwise the browser swallows the response and the
    emulator can't even surface the auth error to the user."""
    client = app.test_client()
    resp = client.get(
        "/api/v1/device/bedroom_pico/frame",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code in (401, 403)
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"


def test_repair_resets_event_dedup_counter(app: Flask) -> None:
    """A reflash wipes the firmware's NVS wake-event counter, and since
    the kind heal keeps the device row across reflashes, the server's
    persisted high-water mark would otherwise dedup EVERY subsequent
    button/touch (counter restarts at 1 <= old mark). Both re-pair
    paths (/register on existing id, /discover MAC claim) must forget
    the mark."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    first = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": "hall_e1003", "kind": "esp32_client", "mac": "aa:bb:cc:00:00:99"}
        ),
    )
    assert first.status_code == 201
    token = first.get_json()["device_token"]

    def post_button(event_id: int):
        return client.post(
            "/api/v1/device/hall_e1003/status",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            data=json.dumps({"button": "refresh", "button_event_id": event_id}),
        )

    assert post_button(50).status_code == 200
    store = app.config["DEVICE_ROTATION_STATE_STORE"]
    assert store.get("hall_e1003").last_button_event_id == 50
    # Pre-fix behaviour: a post-reflash event_id=1 would be swallowed.

    # Reflash path A: MAC-claim discover.
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": "hall_e1003", "kind": "esp32_client", "mac": "aa:bb:cc:00:00:99"}
        ),
    )
    assert resp.status_code == 200 and resp.get_json()["registered"] is True
    state = store.get("hall_e1003")
    assert state is None or state.last_button_event_id is None

    assert post_button(1).status_code == 200
    assert store.get("hall_e1003").last_button_event_id == 1

    # Reflash path B: re-register with a fresh pairing code.
    code2 = _issue_pairing(app)
    again = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code2, "Content-Type": "application/json"},
        data=json.dumps({"device_id": "hall_e1003", "kind": "esp32_client"}),
    )
    assert again.status_code == 200 and again.get_json()["reused_existing"] is True
    state = store.get("hall_e1003")
    assert state is None or state.last_button_event_id is None
    assert post_button(1).status_code == 200
    assert store.get("hall_e1003").last_button_event_id == 1


def test_counter_restart_without_repair_still_dispatches(app: Flask) -> None:
    """The firmware's wake-event counter is RTC-backed and restarts at 0
    on ANY power cycle, usually without a re-pair (the token survives in
    NVS). Dedup is therefore equality-only: a lower id is a restart (or
    an offline-queue replay), never a retry, and must dispatch."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": "desk_e1003", "kind": "esp32_client"}),
    )
    token = resp.get_json()["device_token"]
    store = app.config["DEVICE_ROTATION_STATE_STORE"]

    def post_button(event_id: int):
        return client.post(
            "/api/v1/device/desk_e1003/status",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            data=json.dumps({"button": "refresh", "button_event_id": event_id}),
        )

    assert post_button(50).status_code == 200
    assert store.get("desk_e1003").last_button_event_id == 50
    # Power cycle, no re-pair: counter restarts at 1 -> must dispatch.
    assert post_button(1).status_code == 200
    assert store.get("desk_e1003").last_button_event_id == 1
    # A true retry (same id) is still swallowed: the high-water mark
    # stays, and the dedup outcome leaves state untouched.
    assert post_button(1).status_code == 200
    assert store.get("desk_e1003").last_button_event_id == 1


def test_discover_says_the_body_is_not_json(app: Flask) -> None:
    """A client formatting a Python repr instead of serialising JSON
    (single quotes, bare ``None``) sends something Flask can't parse. The
    empty-dict fallback used to surface that as "device_id is required",
    which sends the client author looking for a field they did send
    (issue #226)."""
    client = app.test_client()
    resp = client.post(
        "/api/v1/device/discover",
        headers={"Content-Type": "application/json"},
        data="{'device_id': 'pico_repr', 'kind': 'pico_bin_client', 'mac': None}",
    )
    assert resp.status_code == 400
    assert "valid JSON" in resp.get_json()["error"]


def test_register_says_the_body_is_not_json(app: Flask) -> None:
    """Same message on the pairing-code path."""
    client = app.test_client()
    _sign_in(client)
    code = _issue_pairing(app)
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data="{'device_id': 'pico_repr'}",
    )
    assert resp.status_code == 400
    assert "valid JSON" in resp.get_json()["error"]


def test_empty_body_still_names_the_missing_field(app: Flask) -> None:
    """No body at all is a different mistake from a malformed one, and
    keeps the field-level message."""
    client = app.test_client()
    resp = client.post("/api/v1/device/discover", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert "device_id" in resp.get_json()["error"]
