"""Companion Gallery surface: browse, create, upload, fetch (#225).

Drives the real app so the responses are validated against the same
component schemas the iOS client decodes with, and covers the parts the
contract makes the server responsible for: opaque identity that can't be
walked back into a host path, metadata stripped on the way in with
orientation baked into the pixels, external folders refusing writes, and
an interrupted upload retried under the same key resolving to the same
image rather than a duplicate.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from flask import Flask
from PIL import Image

from app import companion_gallery
from app.main import REPO_ROOT, create_app

from ._schema import schema_for

_FMT = jsonschema.FormatChecker()


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


def _validate(instance: Any, component: str) -> None:
    jsonschema.validate(instance, schema_for(component), format_checker=_FMT)


_CLIENT = {
    "name": "Test iPhone",
    "platform": "ios",
    "app_version": "0.1.0",
    "installation_id": "A1B2C3D4-E5F6-47A8-9012-3456789ABCDE",
}


@pytest.fixture
def auth(app: Flask) -> dict[str, str]:
    code = app.config["COMPANION_PAIRING_STORE"].issue(note="test").code
    resp = app.test_client().post(
        "/api/app/v1/pair",
        data=json.dumps({"code": code, "client": _CLIENT}),
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return {"Authorization": f"Bearer {resp.get_json()['token']}"}


def _gallery_dir(app: Flask) -> Path:
    with app.app_context():
        return Path(app.config["PLUGIN_REGISTRY"].get("picture_gallery").data_dir)


def _photo(
    size: tuple[int, int] = (64, 48),
    *,
    fmt: str = "JPEG",
    exif: bytes | None = None,
    colour: tuple[int, int, int] = (200, 40, 40),
) -> bytes:
    buf = io.BytesIO()
    image = Image.new("RGB", size, colour)
    if exif is not None:
        image.save(buf, format=fmt, exif=exif)
    else:
        image.save(buf, format=fmt)
    return buf.getvalue()


def _seed_image(app: Flask, folder: str, name: str, **kwargs: Any) -> Path:
    root = _gallery_dir(app)
    target = root if folder == "_root" else root / folder
    target.mkdir(parents=True, exist_ok=True)
    path = target / name
    path.write_bytes(_photo(**kwargs))
    return path


def _upload(
    app: Flask, auth: dict[str, str], folder_id: str, blob: bytes, *, key: str, name: str = "b.jpg"
) -> Any:
    return app.test_client().post(
        f"/api/app/v1/gallery/folders/{folder_id}/images",
        headers={**auth, "Idempotency-Key": key},
        data={"image": (io.BytesIO(blob), name, "image/jpeg")},
        content_type="multipart/form-data",
    )


# -- browse --------------------------------------------------------------


def test_folder_list_is_scoped_and_matches_the_contract(app: Flask, auth: dict[str, str]) -> None:
    _seed_image(app, "family", "one.jpg")
    client = app.test_client()

    assert client.get("/api/app/v1/gallery/folders").status_code == 401

    resp = client.get("/api/app/v1/gallery/folders", headers=auth)
    assert resp.status_code == 200
    body = resp.get_json()
    _validate(body, "GalleryFoldersResponse")
    family = next(f for f in body["folders"] if f["name"] == "family")
    assert family["kind"] == "internal"
    assert family["writable"] is True
    assert family["image_count"] == 1
    assert family["cover_thumbnail_url"].startswith("/api/app/v1/gallery/images/")


def test_folder_detail_lists_images_without_exposing_a_path(
    app: Flask, auth: dict[str, str]
) -> None:
    path = _seed_image(app, "family", "one.jpg", size=(80, 40))
    folder_id = companion_gallery.folder_id("family")
    resp = app.test_client().get(f"/api/app/v1/gallery/folders/{folder_id}", headers=auth)
    assert resp.status_code == 200
    body = resp.get_json()
    _validate(body, "GalleryFolderResponse")
    (image,) = body["images"]
    assert image["width"] == 80 and image["height"] == 40
    assert image["content_type"] == "image/jpeg"
    assert image["bytes"] == path.stat().st_size
    # Neither identifier is a path, and neither leaks where the plugin
    # keeps its data.
    serialized = json.dumps(body)
    assert str(_gallery_dir(app)) not in serialized
    assert "/" not in image["id"].removeprefix("img_")


def test_an_id_cannot_be_pointed_outside_the_gallery(app: Flask, auth: dict[str, str]) -> None:
    """A decoded id is client input, re-validated against the plugin's own
    path rules, so a crafted one reads as a missing image rather than a
    file read."""
    _seed_image(app, "family", "one.jpg")
    client = app.test_client()
    for crafted in (
        companion_gallery.image_id("family", "../../../etc/passwd"),
        companion_gallery.image_id("..", "secrets.jpg"),
        companion_gallery.folder_id("../.."),
        "img_not-base64!!",
        "fld_",
    ):
        for route in (
            f"/api/app/v1/gallery/folders/{crafted}",
            f"/api/app/v1/gallery/images/{crafted}/content",
        ):
            resp = client.get(route, headers=auth)
            assert resp.status_code == 404, (crafted, route, resp.status_code)


def test_content_and_thumbnail_revalidate_with_an_etag(app: Flask, auth: dict[str, str]) -> None:
    _seed_image(app, "family", "one.jpg")
    image_id = companion_gallery.image_id("family", "one.jpg")
    client = app.test_client()

    content = client.get(f"/api/app/v1/gallery/images/{image_id}/content", headers=auth)
    assert content.status_code == 200
    assert content.mimetype == "image/jpeg"
    assert "one.jpg" in content.headers["Content-Disposition"]
    again = client.get(
        f"/api/app/v1/gallery/images/{image_id}/content",
        headers={**auth, "If-None-Match": content.headers["ETag"]},
    )
    assert again.status_code == 304

    thumb = client.get(f"/api/app/v1/gallery/images/{image_id}/thumbnail", headers=auth)
    assert thumb.status_code == 200
    assert thumb.mimetype == "image/jpeg"
    assert len(thumb.get_data()) > 0


def test_unrepresentable_formats_are_left_out_rather_than_mislabelled(
    app: Flask, auth: dict[str, str]
) -> None:
    """The plugin has always accepted GIF and BMP through the Web UI. The
    contract's Gallery media types don't cover them, so they're skipped
    here instead of being served under a type the client can't decode."""
    _seed_image(app, "family", "one.jpg")
    _seed_image(app, "family", "old.gif", fmt="GIF")
    folder_id = companion_gallery.folder_id("family")
    body = (
        app.test_client().get(f"/api/app/v1/gallery/folders/{folder_id}", headers=auth).get_json()
    )
    _validate(body, "GalleryFolderResponse")
    assert [i["name"] for i in body["images"]] == ["one.jpg"]
    # The count agrees with the list rather than with the directory.
    assert body["folder"]["image_count"] == 1


# -- create --------------------------------------------------------------


def test_create_folder_normalizes_the_name(app: Flask, auth: dict[str, str]) -> None:
    resp = app.test_client().post(
        "/api/app/v1/gallery/folders",
        headers=auth,
        json={"name": "Summer 2026!"},
    )
    assert resp.status_code == 201
    body = resp.get_json()
    _validate(body, "GalleryFolderResponse")
    assert body["folder"]["name"] == "summer-2026"
    assert body["folder"]["kind"] == "internal"
    assert body["images"] == []
    assert (_gallery_dir(app) / "summer-2026").is_dir()


def test_create_folder_rejects_a_duplicate_and_an_unusable_name(
    app: Flask, auth: dict[str, str]
) -> None:
    client = app.test_client()
    assert (
        client.post("/api/app/v1/gallery/folders", headers=auth, json={"name": "trips"}).status_code
        == 201
    )
    dup = client.post("/api/app/v1/gallery/folders", headers=auth, json={"name": "Trips"})
    assert dup.status_code == 409
    _validate(dup.get_json(), "ErrorResponse")
    assert dup.get_json()["error"]["code"] == "resource_conflict"

    for name in ("", "***", "x" * 81):
        bad = client.post("/api/app/v1/gallery/folders", headers=auth, json={"name": name})
        assert bad.status_code == 400, name
        _validate(bad.get_json(), "ErrorResponse")


def test_create_folder_cannot_link_a_host_path(app: Flask, auth: dict[str, str]) -> None:
    """Linking an external folder stays an admin action. Whatever a client
    sends as a name normalizes into a folder inside the plugin's own data
    directory, never a pointer at the host filesystem."""
    resp = app.test_client().post(
        "/api/app/v1/gallery/folders", headers=auth, json={"name": "/etc/shadow"}
    )
    assert resp.status_code == 201
    assert resp.get_json()["folder"]["kind"] == "internal"
    assert (_gallery_dir(app) / "etc-shadow").is_dir()


# -- upload --------------------------------------------------------------


def test_upload_stores_a_normalized_image(app: Flask, auth: dict[str, str]) -> None:
    (_gallery_dir(app) / "family").mkdir(parents=True)
    folder_id = companion_gallery.folder_id("family")
    resp = _upload(app, auth, folder_id, _photo((120, 90)), key="k" * 20, name="Beach Day.jpg")
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    _validate(body, "GalleryImageResponse")
    image = body["image"]
    assert image["width"] == 120 and image["height"] == 90
    assert image["folder_id"] == folder_id
    assert image["name"].startswith("beach-day-")
    assert resp.headers["Location"] == image["content_url"]
    assert resp.headers["ETag"] == image["etag"]
    # It is on disk and readable back through the same surface.
    fetched = app.test_client().get(image["content_url"], headers=auth)
    assert fetched.status_code == 200


def test_upload_strips_location_metadata_and_bakes_orientation(
    app: Flask, auth: dict[str, str]
) -> None:
    """The two promises the contract makes about what lands on disk: no
    EXIF at all (GPS included), and a rotation applied to the pixels
    rather than left as a tag for each renderer to interpret."""
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 CW
    exif[0x8825] = {1: "N", 2: (51.5, 30.0, 0.0), 3: "W", 4: (0.12, 0.0, 0.0)}  # GPSInfo
    blob = _photo((120, 90), exif=exif.tobytes())
    with Image.open(io.BytesIO(blob)) as source:
        assert source.getexif().get_ifd(0x8825), "the source photo must really carry GPS"

    (_gallery_dir(app) / "family").mkdir(parents=True)
    folder_id = companion_gallery.folder_id("family")
    resp = _upload(app, auth, folder_id, blob, key="k" * 20)
    assert resp.status_code == 201
    image = resp.get_json()["image"]

    # Orientation 6 means the stored pixels are the portrait rotation.
    assert (image["width"], image["height"]) == (90, 120)

    stored = _gallery_dir(app) / "family" / image["name"]
    with Image.open(stored) as reopened:
        assert reopened.size == (90, 120)
        assert not dict(reopened.getexif())
        assert "exif" not in reopened.info


def test_upload_preserves_the_colour_profile(app: Flask, auth: dict[str, str]) -> None:
    buf = io.BytesIO()
    profile = b"fake-icc-profile-bytes"
    Image.new("RGB", (40, 30), (10, 200, 90)).save(buf, format="JPEG", icc_profile=profile)

    (_gallery_dir(app) / "family").mkdir(parents=True)
    folder_id = companion_gallery.folder_id("family")
    resp = _upload(app, auth, folder_id, buf.getvalue(), key="k" * 20)
    assert resp.status_code == 201
    stored = _gallery_dir(app) / "family" / resp.get_json()["image"]["name"]
    with Image.open(stored) as reopened:
        assert reopened.info.get("icc_profile") == profile


def test_retrying_an_upload_resolves_to_the_same_image(app: Flask, auth: dict[str, str]) -> None:
    """A dropped connection retried on reconnect must not leave two copies
    of the same photo in the folder."""
    (_gallery_dir(app) / "family").mkdir(parents=True)
    folder_id = companion_gallery.folder_id("family")
    blob = _photo((60, 60))
    first = _upload(app, auth, folder_id, blob, key="retry-key-0123456789")
    second = _upload(app, auth, folder_id, blob, key="retry-key-0123456789")
    assert first.status_code == second.status_code == 201
    assert first.get_json() == second.get_json()
    assert len(list((_gallery_dir(app) / "family").glob("*.jpg"))) == 1


def test_reusing_a_key_for_a_different_photo_conflicts(app: Flask, auth: dict[str, str]) -> None:
    (_gallery_dir(app) / "family").mkdir(parents=True)
    folder_id = companion_gallery.folder_id("family")
    assert (
        _upload(app, auth, folder_id, _photo((60, 60)), key="same-key-0123456789").status_code
        == 201
    )
    clash = _upload(
        app, auth, folder_id, _photo((60, 60), colour=(5, 5, 5)), key="same-key-0123456789"
    )
    assert clash.status_code == 409
    _validate(clash.get_json(), "ErrorResponse")
    assert clash.get_json()["error"]["code"] == "idempotency_conflict"


def test_upload_requires_an_idempotency_key(app: Flask, auth: dict[str, str]) -> None:
    (_gallery_dir(app) / "family").mkdir(parents=True)
    folder_id = companion_gallery.folder_id("family")
    resp = app.test_client().post(
        f"/api/app/v1/gallery/folders/{folder_id}/images",
        headers=auth,
        data={"image": (io.BytesIO(_photo()), "a.jpg", "image/jpeg")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    _validate(resp.get_json(), "ErrorResponse")


def test_upload_rejects_an_undecodable_or_unsupported_payload(
    app: Flask, auth: dict[str, str]
) -> None:
    (_gallery_dir(app) / "family").mkdir(parents=True)
    folder_id = companion_gallery.folder_id("family")
    client = app.test_client()

    declared_wrong = client.post(
        f"/api/app/v1/gallery/folders/{folder_id}/images",
        headers={**auth, "Idempotency-Key": "k" * 20},
        data={"image": (io.BytesIO(b"not an image"), "a.txt", "text/plain")},
        content_type="multipart/form-data",
    )
    assert declared_wrong.status_code == 415

    # Declaring a supported type doesn't make the bytes decodable.
    lying = _upload(app, auth, folder_id, b"not an image at all", key="k" * 20)
    assert lying.status_code == 415
    _validate(lying.get_json(), "ErrorResponse")
    assert lying.get_json()["error"]["code"] == "unsupported_image"


def test_upload_to_an_external_folder_is_refused(
    app: Flask, auth: dict[str, str], tmp_path: Path
) -> None:
    """External folders are somebody's real photo library, pointed at
    rather than owned. The plugin never writes there and neither does
    this surface."""
    host_dir = tmp_path / "nas-archive"
    host_dir.mkdir()
    (host_dir / "holiday.jpg").write_bytes(_photo())
    meta = _gallery_dir(app) / ".folders.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(
        json.dumps({"archive": {"label": "archive", "external_path": str(host_dir)}}),
        encoding="utf-8",
    )

    folder_id = companion_gallery.folder_id("archive")
    listing = app.test_client().get(f"/api/app/v1/gallery/folders/{folder_id}", headers=auth)
    assert listing.status_code == 200
    assert listing.get_json()["folder"]["writable"] is False
    assert listing.get_json()["folder"]["kind"] == "external"

    refused = _upload(app, auth, folder_id, _photo(), key="k" * 20)
    assert refused.status_code == 409
    _validate(refused.get_json(), "ErrorResponse")
    assert refused.get_json()["error"]["code"] == "resource_conflict"
    assert sorted(p.name for p in host_dir.iterdir()) == ["holiday.jpg"]


def test_gallery_write_routes_need_the_write_scope(app: Flask) -> None:
    """A credential that can browse can't necessarily add. The check is
    wired per-scope rather than per-role, so a narrower credential reads
    but doesn't write, and it answers 403 rather than 401: the credential
    is fine and re-pairing wouldn't grant anything."""
    plaintext, _record = app.config["COMPANION_TOKENS"].issue(
        client=_CLIENT, scopes=["gallery:read"]
    )
    read_only = {"Authorization": f"Bearer {plaintext}"}

    client = app.test_client()
    assert client.get("/api/app/v1/gallery/folders", headers=read_only).status_code == 200
    denied = client.post("/api/app/v1/gallery/folders", headers=read_only, json={"name": "trips"})
    assert denied.status_code == 403
    _validate(denied.get_json(), "ErrorResponse")
    assert denied.get_json()["error"]["code"] == "forbidden"
