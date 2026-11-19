"""Export and import of displays (#541)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import auth, bundle, device_bundle, device_service, mac_claim
from app.main import REPO_ROOT, create_app

KIND = "pico_bin_client"
MAC = "aa:bb:cc:dd:ee:01"


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _stores(app: Flask) -> dict[str, Any]:
    return {
        "devices": app.config["DEVICE_REGISTRY"],
        "renderers": app.config["RENDERER_REGISTRY"],
        "data_root": Path(app.config["DEVICE_DATA_ROOT"]),
        "settings": app.config["SETTINGS_STORE"],
    }


def _add(app: Flask, device_id: str = "kitchen", **fields: Any) -> Any:
    result = device_service.create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id=device_id,
        kind_id=fields.pop("kind_id", KIND),
        name=fields.pop("name", "Kitchen"),
        transport=fields.pop("transport", "rest"),
        **fields,
    )
    assert result.device is not None, result.error
    return result.device


def _raw(app: Flask, device_id: str) -> dict[str, Any]:
    return json.loads(app.config["DEVICE_REGISTRY"].get(device_id).path.read_text())


def _export(app: Flask, ids: list[str], *, identity: bool) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    displays = device_bundle.export_devices(
        device_ids=ids,
        devices=app.config["DEVICE_REGISTRY"],
        settings=app.config["SETTINGS_STORE"],
        include_identity=identity,
        notes=notes,
    )
    doc, notes = bundle.build_bundle(
        page_ids=[],
        deck_ids=[],
        data_root=Path(app.config["DATA_ROOT"]),
        page_store=app.config["PAGE_STORE"],
        deck_store=app.config["DECK_STORE"],
        registry=app.config["PLUGIN_REGISTRY"],
        devices=displays,
        notes=notes,
    )
    return json.loads(json.dumps(doc)), notes


def _apply(app: Flask, doc: dict[str, Any], replace: set[str] | None = None) -> Any:
    return device_bundle.apply_devices(
        bundle.read_bundle(json.dumps(doc).encode()), replace=replace or set(), **_stores(app)
    )


@pytest.fixture
def other(tmp_path: Path) -> Flask:
    """A second server to import into."""
    return create_app(testing=True, data_root=tmp_path / "other", plugins_dir=REPO_ROOT / "plugins")


def _seed(app: Flask) -> Any:
    device = _add(app, mac=MAC)
    device_service.update_instance_quiet_hours(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id="kitchen",
        enabled=True,
        start="22:00",
        end="07:00",
    )
    app.config["SETTINGS_STORE"].update_item(
        "devices",
        "kitchen",
        {
            "sleep_interval_s": 900,
            "button_map": {"a": "refresh"},
            "wifi_password_secret": "hunter2",
            "cloud_access_token": "abc",
            "cloud_device_id": "77",
        },
    )
    return app.config["DEVICE_REGISTRY"].get(device.id)


def test_export_carries_settings_and_no_credentials_by_default(app: Flask) -> None:
    device = _seed(app)
    token = device.manifest["access_token"]
    doc, notes = _export(app, ["kitchen"], identity=False)
    text = json.dumps(doc)
    assert token not in text and MAC not in text and "hunter2" not in text
    assert "identity" not in text and "holds_access_tokens" not in doc
    assert doc["tesserae_bundle"] == 2
    (entry,) = doc["devices"]
    assert entry["id"] == "kitchen" and entry["kind"] == KIND and entry["transport"] == "rest"
    assert entry["device"]["quiet_hours"]["start"] == "22:00"
    assert entry["device"]["panel"]["w"] > 0
    assert entry["settings"] == {"sleep_interval_s": 900, "button_map": {"a": "refresh"}}
    left_out = " ".join(notes)
    for name in ("wifi_password_secret", "cloud_access_token", "cloud_device_id"):
        assert name in left_out


def test_a_file_without_displays_keeps_the_older_format_number(app: Flask) -> None:
    doc, _ = _export(app, [], identity=True)
    assert doc["tesserae_bundle"] == 1 and "devices" not in doc


def test_identity_travels_only_when_asked_for(app: Flask) -> None:
    device = _seed(app)
    doc, _ = _export(app, ["kitchen"], identity=True)
    assert doc["devices"][0]["identity"] == {
        "access_token": device.manifest["access_token"],
        "mac": MAC,
    }
    assert device_bundle.has_identity(doc)


def test_a_display_moves_to_another_server_with_its_token(app: Flask, other: Flask) -> None:
    device = _seed(app)
    token = device.manifest["access_token"]
    doc, _ = _export(app, ["kitchen"], identity=True)

    report = _apply(other, doc)

    assert report.added == ["Kitchen"] and not report.skipped and not report.needs_pairing
    moved = other.config["DEVICE_REGISTRY"].get("kitchen")
    assert moved.manifest["access_token"] == token and moved.manifest["mac"] == MAC
    assert moved.transport == "rest" and moved.kind_of == KIND
    assert moved.manifest["quiet_hours"]["start"] == "22:00"
    assert moved.panel == device.panel
    stored = other.config["SETTINGS_STORE"].get_section("devices")["kitchen"]
    assert stored["sleep_interval_s"] == 900 and stored["button_map"] == {"a": "refresh"}
    assert "cloud_device_id" not in stored
    # The display already holds its token: its MAC must not collect it again.
    assert _raw(other, "kitchen")[mac_claim.CLOSED_KEY] is True
    assert mac_claim.is_closed(moved)
    # And the token is what the server answers to.
    with other.app_context():
        client = other.test_client()
        ok = client.get(
            "/api/v1/device/kitchen/frame", headers={"Authorization": f"Bearer {token}"}
        )
        wrong = client.get("/api/v1/device/kitchen/frame", headers={"Authorization": "Bearer nope"})
    assert ok.status_code != 401 and wrong.status_code == 401


def test_without_identity_a_new_display_gets_its_own_token(app: Flask, other: Flask) -> None:
    device = _seed(app)
    doc, _ = _export(app, ["kitchen"], identity=False)
    plan = device_bundle.describe_devices(doc, other.config["DEVICE_REGISTRY"])
    assert plan[0]["action"] == "add" and plan[0]["needs_pairing"] and not plan[0]["has_identity"]

    report = _apply(other, doc)

    assert report.added == ["Kitchen"] and report.needs_pairing == ["Kitchen"]
    made = other.config["DEVICE_REGISTRY"].get("kitchen")
    assert (
        made.manifest["access_token"]
        and made.manifest["access_token"] != device.manifest["access_token"]
    )
    assert "mac" not in made.manifest and not mac_claim.is_closed(made)


def test_replacing_updates_settings_and_keeps_the_token_already_here(
    app: Flask, other: Flask
) -> None:
    _seed(app)
    here = _add(other, name="Old name")
    here_token = here.manifest["access_token"]
    doc, _ = _export(app, ["kitchen"], identity=True)
    registry = other.config["DEVICE_REGISTRY"]
    assert device_bundle.describe_devices(doc, registry)[0]["action"] == "replace"

    untouched = _apply(other, doc)
    assert not untouched.changed and "left as it is" in untouched.skipped[0]
    assert registry.get("kitchen").name == "Old name"

    report = _apply(other, doc, replace={"kitchen"})
    assert report.replaced == ["Kitchen"] and not report.skipped
    updated = registry.get("kitchen")
    assert updated.name == "Kitchen" and updated.manifest["quiet_hours"]["start"] == "22:00"
    assert updated.manifest["access_token"] == here_token
    assert "mac" not in _raw(other, "kitchen")
    assert (
        other.config["SETTINGS_STORE"].get_section("devices")["kitchen"]["sleep_interval_s"] == 900
    )


def test_displays_that_cannot_come_in_are_skipped_one_by_one(app: Flask, other: Flask) -> None:
    _seed(app)
    _add(app, "hall", name="Hall", mac="aa:bb:cc:dd:ee:02")
    doc, _ = _export(app, ["kitchen", "hall"], identity=True)
    # Another display here already answers to the kitchen's token.
    _add(other, "pantry", name="Pantry", access_token=doc["devices"][0]["identity"]["access_token"])
    doc["devices"].append({"id": "attic", "kind": "no_such_kind", "name": "Attic"})
    doc["devices"].append({"id": "shed", "kind": KIND, "name": "Shed", "transport": "relay"})

    report = _apply(other, doc)

    assert report.added == ["Hall"]
    reasons = " | ".join(report.skipped)
    assert "Kitchen: its access token already belongs to the display pantry" in reasons
    assert "Attic: this server has no display type called no_such_kind" in reasons
    assert "Shed: a relay panel has to be paired" in reasons
    assert set(other.config["DEVICE_REGISTRY"].devices) >= {"hall", "pantry"}
    assert other.config["DEVICE_REGISTRY"].get("kitchen") is None


def test_a_display_of_another_type_under_the_same_id_is_left_alone(
    app: Flask, other: Flask
) -> None:
    _seed(app)
    _add(other, kind_id="esp32_client", name="Different")
    doc, _ = _export(app, ["kitchen"], identity=False)
    report = _apply(other, doc, replace={"kitchen"})
    assert not report.changed and "already here as a esp32_client" in report.skipped[0]


def test_import_does_not_trust_the_file_about_credentials(app: Flask, other: Flask) -> None:
    _seed(app)
    doc, _ = _export(app, ["kitchen"], identity=False)
    doc["devices"][0]["settings"].update({"api_token": "x", "wifi_password_secret": "y"})
    doc["devices"][0]["device"]["relay_frame_key"] = "k"
    doc["devices"][0]["device"]["mac_claim_closed"] = True
    plan = device_bundle.describe_devices(doc, other.config["DEVICE_REGISTRY"])
    assert plan[0]["dropped"] == ["api_token", "wifi_password_secret"]

    _apply(other, doc)

    stored = other.config["SETTINGS_STORE"].get_section("devices")["kitchen"]
    assert "api_token" not in stored and "wifi_password_secret" not in stored
    raw = _raw(other, "kitchen")
    assert "relay_frame_key" not in raw and mac_claim.CLOSED_KEY not in raw


def test_a_bad_panel_in_the_file_adds_nothing(app: Flask, other: Flask) -> None:
    _seed(app)
    doc, _ = _export(app, ["kitchen"], identity=False)
    doc["devices"][0]["device"]["panel"] = {"w": "wide", "h": -1}
    report = _apply(other, doc)
    registry = other.config["DEVICE_REGISTRY"]
    if report.added:
        # The loader took the panel: the display must then be usable.
        assert registry.get("kitchen") is not None
    else:
        assert registry.get("kitchen") is None
        assert not (Path(other.config["DEVICE_DATA_ROOT"]) / "kitchen.json").exists()


@pytest.mark.parametrize(
    "devices",
    [
        "nope",
        [{"id": "Bad Id", "kind": KIND}],
        [{"id": "kitchen"}],
        [{"id": "kitchen", "kind": KIND}, {"id": "kitchen", "kind": KIND}],
        [{"id": "kitchen", "kind": KIND, "settings": []}],
    ],
)
def test_unreadable_display_lists_are_refused(devices: Any) -> None:
    raw = json.dumps({"tesserae_bundle": 2, "dashboards": [], "lineups": [], "devices": devices})
    with pytest.raises(bundle.BundleError):
        bundle.read_bundle(raw.encode())


def test_routes_export_and_import_displays(app: Flask, other: Flask) -> None:
    device = _seed(app)
    token = device.manifest["access_token"]
    client = app.test_client()
    _sign_in(client)

    page = client.get("/settings/devices").get_data(as_text=True)
    assert "Move displays to another server" in page and 'name="identity"' in page

    plain = client.get("/bundles/export?device=kitchen")
    assert plain.status_code == 200 and token not in plain.get_data(as_text=True)
    assert plain.headers["Content-Disposition"] == 'attachment; filename="tesserae-kitchen.json"'
    # Export all is about dashboards: it never sweeps displays in.
    assert client.get("/bundles/export?all=1").status_code == 302

    exported = client.get("/bundles/export?device=kitchen&identity=1").get_data()
    target = other.test_client()
    _sign_in(target)
    preview = target.post(
        "/bundles/import",
        data={"file": (io.BytesIO(exported), "kitchen.json")},
        content_type="multipart/form-data",
    )
    body = preview.get_data(as_text=True)
    assert preview.status_code == 200 and "Displays (1)" in body
    assert "This file holds access tokens" in body and "with its access token" in body
    assert token not in body
    pending = body.split('name="token" value="')[1].split('"')[0]
    done = target.post("/bundles/import/apply", data={"token": pending})
    assert done.status_code == 302
    assert other.config["DEVICE_REGISTRY"].get("kitchen").manifest["access_token"] == token


def test_export_with_tokens_needs_the_admin_session(app: Flask) -> None:
    """The test app runs without the sign-in gate; with it, as in a real
    server, a signed-out request for a token-bearing export gets nothing."""
    device = _seed(app)
    token = device.manifest["access_token"]
    auth.install_gate(app, app.config["SETTINGS_STORE"])
    owner = app.test_client()
    _sign_in(owner)

    stranger = app.test_client().get("/bundles/export?device=kitchen&identity=1")
    assert stranger.status_code in (302, 401, 403)
    assert token.encode() not in stranger.get_data()

    assert token.encode() in owner.get("/bundles/export?device=kitchen&identity=1").get_data()


def test_a_single_display_exports_from_its_own_menu(app: Flask) -> None:
    """Like the per-row Export on dashboards: the display's More menu
    downloads just that display, without its token."""
    _add(app)
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings/devices/kitchen").get_data(as_text=True)
    assert 'href="/bundles/export?device=kitchen" download' in body
    assert "Export this display" in body


def test_the_export_form_offers_select_and_clear_all(app: Flask) -> None:
    _add(app)
    client = app.test_client()
    _sign_in(client)
    one = client.get("/settings/devices").get_data(as_text=True)
    assert 'data-export-pick="none"' not in one  # nothing to pick between

    _add(app, "hallway", name="Hallway")
    both = client.get("/settings/devices").get_data(as_text=True)
    assert 'data-export-pick="all"' in both and 'data-export-pick="none"' in both


def test_backup_copies_in_the_devices_folder_are_not_loaded(app: Flask) -> None:
    """A copy such as ``kitchen.bak.json`` used to load as a second display
    holding the first one's token (#541). Device ids never contain a dot."""
    from app.device_loader import DeviceRegistry, discover

    device = _add(app)
    root = Path(app.config["DEVICE_DATA_ROOT"])
    copy = device.path.read_text()
    (root / "kitchen.bak.json").write_text(copy)
    (root / ".kitchen.json").write_text(copy)

    fresh = discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=root,
        hardware_dir=REPO_ROOT / "hardware",
        hardware_schema_path=REPO_ROOT / "schema" / "hardware.schema.json",
    )
    assert isinstance(fresh, DeviceRegistry)
    assert set(fresh.devices) >= {"kitchen"}
    assert not {"kitchen.bak", ".kitchen"} & set(fresh.devices)
    skipped = {e.device_id for e in fresh.errors if "backup copy" in e.message}
    assert skipped == {"kitchen.bak", ".kitchen"}
