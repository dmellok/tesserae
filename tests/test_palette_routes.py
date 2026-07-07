"""Calibration-tab palette-profile routes.

Covers the six endpoints wired to the Calibration-tab's palette card:
apply / save / reset / export / import / delete. The storage layer
itself is tested in :mod:`test_palette_profiles`; this suite is the
HTTP-plumbing round-trip.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

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


def _register_device(client, device_id: str = "esp32_demo", kind: str = "esp32_client") -> str:
    resp = client.post(
        "/settings/devices/add",
        data={"id": device_id, "kind": kind},
        follow_redirects=False,
    )
    assert resp.status_code == 302, f"device add failed: {resp.data!r}"
    return device_id


def test_apply_bundled_profile_writes_slug_to_device(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    resp = client.post(
        f"/settings/devices/{dev}/palette/apply",
        data={"slug": "paperlesspaper-spectra6"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "tab=calibration" in resp.location
    # Store round-trip: the picker should read back the slug.
    body = client.get("/settings/devices").get_data(as_text=True)
    assert "paperlesspaper-spectra6" in body


def test_apply_unknown_slug_flashes_error(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    resp = client.post(
        f"/settings/devices/{dev}/palette/apply",
        data={"slug": "not-a-real-profile"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "tab=calibration" in resp.location


def test_reset_clears_the_slug(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    client.post(
        f"/settings/devices/{dev}/palette/apply",
        data={"slug": "boeber-spectra6"},
    )
    resp = client.post(
        f"/settings/devices/{dev}/palette/reset",
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_save_as_new_writes_user_profile_and_applies(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    resp = client.post(
        f"/settings/devices/{dev}/palette/save",
        data={"name": "My warm study", "base_slug": "paperlesspaper-spectra6"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # New profile lives on disk.
    files = list((tmp_path / "palette_profiles").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["name"] == "My warm study"
    assert payload["family"] == "spectra6"
    assert payload["based_on"] == "paperlesspaper/epdoptimize · spectra6"
    assert not payload.get("bundled")


def test_save_requires_a_name(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    resp = client.post(
        f"/settings/devices/{dev}/palette/save",
        data={"name": "", "base_slug": "paperlesspaper-spectra6"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # No file written.
    prof_dir = Path(app.config["DATA_ROOT"]) / "palette_profiles"
    assert not prof_dir.exists() or not list(prof_dir.glob("*.json"))


def test_export_bundled_profile_returns_json(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/settings/palette-profiles/paperlesspaper-spectra6/export.json")
    assert resp.status_code == 200
    assert resp.mimetype == "application/json"
    payload = json.loads(resp.data.decode("utf-8"))
    assert payload["slug"] == "paperlesspaper-spectra6"
    assert payload["family"] == "spectra6"
    assert "palette" in payload


def test_export_unknown_profile_404s(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/settings/palette-profiles/nope/export.json")
    assert resp.status_code == 404


def test_import_roundtrips_a_profile(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    payload = {
        "slug": "imported-eve",
        "name": "Imported eve",
        "family": "spectra6",
        "palette": {
            "black": "#1A1A1A",
            "white": "#EEEEEE",
            "yellow": "#CCCC00",
            "red": "#AA0000",
            "blue": "#0000AA",
            "green": "#00AA00",
        },
    }
    resp = client.post(
        "/settings/palette-profiles/import",
        data={
            "instance_id": dev,
            "profile": (io.BytesIO(json.dumps(payload).encode("utf-8")), "eve.json"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    imported = list((tmp_path / "palette_profiles").glob("*.json"))
    assert len(imported) == 1
    on_disk = json.loads(imported[0].read_text(encoding="utf-8"))
    assert on_disk["name"] == "Imported eve"


def test_import_rejects_non_json(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/palette-profiles/import",
        data={
            "profile": (io.BytesIO(b"not json"), "junk.txt"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_delete_user_profile(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    client.post(
        f"/settings/devices/{dev}/palette/save",
        data={"name": "ToDelete", "base_slug": "paperlesspaper-spectra6"},
    )
    files = list((tmp_path / "palette_profiles").glob("*.json"))
    assert len(files) == 1
    slug = files[0].stem
    resp = client.post(
        f"/settings/palette-profiles/{slug}/delete",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert not list((tmp_path / "palette_profiles").glob("*.json"))


def test_update_tone_on_bundled_profile_forks_to_user_copy(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    client.post(
        f"/settings/devices/{dev}/palette/apply",
        data={"slug": "paperlesspaper-spectra6"},
    )
    resp = client.post(
        f"/settings/devices/{dev}/palette/update-tone",
        data={"exposure": "20", "s_curve": "-10", "diffusion_strength": "80"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    forks = list((tmp_path / "palette_profiles").glob("*.json"))
    assert len(forks) == 1
    fork_body = json.loads(forks[0].read_text(encoding="utf-8"))
    assert fork_body["name"].endswith("(edited)")
    assert fork_body["tone"]["exposure"] == 20
    assert fork_body["tone"]["s_curve"] == -10
    assert fork_body["dither"]["diffusion_strength"] == 80
    # Bundled preset itself is untouched.
    body = client.get("/settings/palette-profiles/paperlesspaper-spectra6/export.json")
    original = json.loads(body.data.decode("utf-8"))
    assert original["tone"]["exposure"] == 0


def test_update_tone_on_user_profile_edits_in_place(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    # Save-as-new → applies the user profile → edit-tone stays on it.
    client.post(
        f"/settings/devices/{dev}/palette/save",
        data={"name": "MyDeck", "base_slug": "paperlesspaper-spectra6"},
    )
    (files_before,) = list((tmp_path / "palette_profiles").glob("*.json"))
    client.post(
        f"/settings/devices/{dev}/palette/update-tone",
        data={"exposure": "15", "s_curve": "0", "diffusion_strength": "100"},
    )
    files_after = list((tmp_path / "palette_profiles").glob("*.json"))
    # Same file (no fork).
    assert [f.name for f in files_after] == [files_before.name]
    body = json.loads(files_after[0].read_text(encoding="utf-8"))
    assert body["tone"]["exposure"] == 15


def test_update_palette_on_bundled_forks_with_new_colours(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    client.post(
        f"/settings/devices/{dev}/palette/apply",
        data={"slug": "paperlesspaper-spectra6"},
    )
    resp = client.post(
        f"/settings/devices/{dev}/palette/update-palette",
        data={
            "black": "#101010",
            "white": "#f0f0f0",
            "yellow": "#c0c000",
            "red": "#a01010",
            "blue": "#1010a0",
            "green": "#108010",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    forks = list((tmp_path / "palette_profiles").glob("*.json"))
    assert len(forks) == 1
    fork = json.loads(forks[0].read_text(encoding="utf-8"))
    assert fork["palette"]["black"] == "#101010"
    assert fork["palette"]["red"] == "#a01010"
    # Bundled preset stays clean.
    original = json.loads(
        client.get("/settings/palette-profiles/paperlesspaper-spectra6/export.json").data.decode(
            "utf-8"
        )
    )
    assert original["palette"]["black"] == "#1F2226"


def test_update_palette_on_user_profile_edits_in_place(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    client.post(
        f"/settings/devices/{dev}/palette/save",
        data={"name": "MyRoom", "base_slug": "paperlesspaper-spectra6"},
    )
    (before,) = list((tmp_path / "palette_profiles").glob("*.json"))
    client.post(
        f"/settings/devices/{dev}/palette/update-palette",
        data={
            "black": "#222222",
            "white": "#eeeeee",
            "yellow": "#dddd00",
            "red": "#bb2222",
            "blue": "#2222bb",
            "green": "#22bb22",
        },
    )
    after = list((tmp_path / "palette_profiles").glob("*.json"))
    assert [f.name for f in after] == [before.name]
    body = json.loads(after[0].read_text(encoding="utf-8"))
    assert body["palette"]["black"] == "#222222"
    assert body["palette"]["red"] == "#bb2222"


def test_update_palette_ignores_bad_hex(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    client.post(
        f"/settings/devices/{dev}/palette/apply",
        data={"slug": "paperlesspaper-spectra6"},
    )
    resp = client.post(
        f"/settings/devices/{dev}/palette/update-palette",
        data={
            "black": "not-a-hex",
            "white": "#f0f0f0",
            "yellow": "#c0c000",
            "red": "#a01010",
            "blue": "#1010a0",
            "green": "#108010",
        },
    )
    assert resp.status_code == 302
    forks = list((tmp_path / "palette_profiles").glob("*.json"))
    fork = json.loads(forks[0].read_text(encoding="utf-8"))
    # Bad ``black`` fell back to the base preset value; other colours
    # took the submitted values.
    assert fork["palette"]["black"] == "#1F2226"
    assert fork["palette"]["white"] == "#f0f0f0"


def test_update_tone_refused_when_no_profile_applied(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    resp = client.post(
        f"/settings/devices/{dev}/palette/update-tone",
        data={"exposure": "10"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # No profile file written.
    prof_dir = Path(app.config["DATA_ROOT"]) / "palette_profiles"
    assert not prof_dir.exists() or not list(prof_dir.glob("*.json"))


def test_slug_self_heal_on_stale_slug_for_supported_family(app: Flask, tmp_path: Path) -> None:
    """Issue #52 follow-up: after a save the Calibration tab's tone
    section vanishes when the device's ``palette_profile_slug`` points
    at a profile that no longer resolves (deleted user profile, disk
    cleanup, etc.). The self-heal in ``_palette_profile_slug_for``
    backfills the family's default bundled slug so the tone editor
    stays rendered."""
    from app.settings.index_routes import _palette_profile_slug_for, _palette_profile_tone_for

    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    # Poke a stale slug straight into settings, mimicking a user profile
    # that got deleted while it was still the device's active slug.
    store = app.config["SETTINGS_STORE"]
    store.update_for_namespace(
        "devices",
        dev,
        {"palette_profile_slug": "ghost-profile-that-doesnt-exist"},
        [{"name": "palette_profile_slug", "type": "string", "default": ""}],
    )
    with app.test_request_context():
        device = app.config["DEVICE_REGISTRY"].get(dev)
        healed_slug = _palette_profile_slug_for(device)
        assert healed_slug == "paperlesspaper-spectra6"
        tone = _palette_profile_tone_for(device)
        assert tone.get("editable") is True

    # And that healed slug is persisted, so a second read is a plain hit.
    raw = store.get_for_runtime(
        "devices",
        dev,
        [{"name": "palette_profile_slug", "type": "string", "default": ""}],
    )
    assert raw["palette_profile_slug"] == "paperlesspaper-spectra6"


def test_slug_self_heal_on_empty_slug_for_supported_family(app: Flask, tmp_path: Path) -> None:
    """A fresh device with a Spectra 6 gamut and no slug set gets the
    family default backfilled so the tone editor is visible out of
    the box."""
    from app.settings.index_routes import _palette_profile_slug_for, _palette_profile_tone_for

    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    with app.test_request_context():
        device = app.config["DEVICE_REGISTRY"].get(dev)
        healed_slug = _palette_profile_slug_for(device)
        assert healed_slug == "paperlesspaper-spectra6"
        tone = _palette_profile_tone_for(device)
        assert tone.get("editable") is True


def test_slug_self_heal_leaves_unsupported_gamut_alone(app: Flask, tmp_path: Path) -> None:
    """Panels whose gamut has no matching profile family (mono / bwry_4
    / rgb24 / rgb16) should still hide the palette section, not get
    forced onto a Spectra 6 profile."""
    from app.settings.index_routes import _palette_profile_slug_for, _palette_profile_tone_for

    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client, kind="pi_bin_client")
    device = app.config["DEVICE_REGISTRY"].get(dev)
    # Force gamut to mono to model an unsupported family.
    from app.device_service import update_instance_panel

    devices = app.config["DEVICE_REGISTRY"]
    from app.renderer_loader import RendererRegistry  # noqa: F401
    from pathlib import Path as _Path

    update_instance_panel(
        devices=devices,
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=_Path(app.config["DATA_ROOT"]) / "devices",
        instance_id=dev,
        w=device.panel["w"],
        h=device.panel["h"],
        orientation=device.panel.get("orientation", "landscape"),
        gamut="bwry_4",
    )
    with app.test_request_context():
        device = app.config["DEVICE_REGISTRY"].get(dev)
        assert _palette_profile_slug_for(device) == ""
        tone = _palette_profile_tone_for(device)
        assert tone.get("editable") is False


def test_delete_bundled_profile_refused(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/palette-profiles/paperlesspaper-spectra6/delete",
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_calibration_tab_renders_palette_card(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _register_device(client)
    body = client.get("/settings/devices").get_data(as_text=True)
    # Palette picker + attribution link show up.
    assert "Palette recalibration" in body
    assert "paperlesspaper-spectra6" in body
    assert "paperlesspaper/epdoptimize" in body
    # Save-as-new fold-out is present.
    assert "Save as new profile" in body
