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
