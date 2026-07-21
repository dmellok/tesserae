"""Per-dashboard image asset catalog: store module, serving route, MCP
endpoints, and the delete cascade that frees a page's folder with the page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask

from app import page_assets
from app.main import REPO_ROOT, create_app

_PNG = b"\x89PNG\r\n\x1a\n" + b"pretend-image-bytes"


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    a.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})
    return a


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _make_page(client: Any) -> str:
    return str(
        client.post("/api/mcp/pages", json={"name": "Art", "w": 800, "h": 480}).get_json()["id"]
    )


# -- store module --------------------------------------------------------


def test_cache_url_stores_and_is_content_addressed(tmp_path: Path) -> None:
    with patch("app.page_assets.fetch_bytes", return_value=(_PNG, "image/png")):
        rec1 = page_assets.cache_url(tmp_path, "pageone", "https://cdn.example.com/a.png")
        rec2 = page_assets.cache_url(tmp_path, "pageone", "https://cdn.example.com/a.png")
    assert rec1["name"] == rec2["name"]  # same bytes -> same name
    assert rec1["url"] == f"/page-assets/pageone/{rec1['name']}"
    assert (page_assets.assets_dir(tmp_path, "pageone") / str(rec1["name"])).read_bytes() == _PNG


def test_cache_url_rejects_non_image(tmp_path: Path) -> None:
    with (
        patch("app.page_assets.fetch_bytes", return_value=(b"<html>", "text/html")),
        pytest.raises(page_assets.AssetError, match="not a supported image"),
    ):
        page_assets.cache_url(tmp_path, "pageone", "https://x.example/p")


def test_save_bytes_and_list_and_delete(tmp_path: Path) -> None:
    rec = page_assets.save_bytes(tmp_path, "pg", _PNG, "image/png")
    assert page_assets.list_assets(tmp_path, "pg") == [rec]
    assert page_assets.delete_asset(tmp_path, "pg", str(rec["name"])) is True
    assert page_assets.list_assets(tmp_path, "pg") == []


def test_delete_all_removes_folder(tmp_path: Path) -> None:
    page_assets.save_bytes(tmp_path, "pg", _PNG, "image/png")
    assert page_assets.assets_dir(tmp_path, "pg").exists()
    page_assets.delete_all(tmp_path, "pg")
    assert not page_assets.assets_dir(tmp_path, "pg").exists()
    page_assets.delete_all(tmp_path, "pg")  # idempotent


@pytest.mark.parametrize("bad", ["../evil", "a/b", "", "a b"])
def test_bad_page_id_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(page_assets.AssetError):
        page_assets.assets_dir(tmp_path, bad)


def test_delete_asset_rejects_traversal_name(tmp_path: Path) -> None:
    with pytest.raises(page_assets.AssetError):
        page_assets.delete_asset(tmp_path, "pg", "../../secrets.json")


# -- MCP endpoints -------------------------------------------------------


def test_mcp_cache_list_serve_delete(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _make_page(client)
    with patch("app.page_assets.fetch_bytes", return_value=(_PNG, "image/png")):
        rec = client.post(
            f"/api/mcp/pages/{pid}/assets", json={"url": "https://cdn.example.com/logo.png"}
        ).get_json()
    assert rec["url"].startswith(f"/page-assets/{pid}/")

    listing = client.get(f"/api/mcp/pages/{pid}/assets").get_json()
    assert listing["assets"][0]["name"] == rec["name"]

    served = client.get(rec["url"])  # loopback bypass serves it
    assert served.status_code == 200 and served.data == _PNG and served.mimetype == "image/png"
    served.close()  # release the send_from_directory file handle (py3.14 ResourceWarning)

    assert client.delete(f"/api/mcp/pages/{pid}/assets/{rec['name']}").status_code == 200
    assert client.get(f"/api/mcp/pages/{pid}/assets").get_json()["assets"] == []


def test_mcp_cache_blocks_private_host(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _make_page(client)
    resp = client.post(f"/api/mcp/pages/{pid}/assets", json={"url": "http://10.0.0.1/x"})
    assert resp.status_code == 422
    assert "error" in resp.get_json()


def test_mcp_assets_404_for_non_canvas(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    assert client.get("/api/mcp/pages/nope/assets").status_code == 404
    assert client.post("/api/mcp/pages/nope/assets", json={"url": "https://x/y"}).status_code == 404


def test_deleting_page_cascades_asset_folder(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _make_page(client)
    with patch("app.page_assets.fetch_bytes", return_value=(_PNG, "image/png")):
        client.post(f"/api/mcp/pages/{pid}/assets", json={"url": "https://cdn.example.com/a.png"})
    data_root = Path(app.config["DATA_ROOT"])
    assert page_assets.assets_dir(data_root, pid).exists()
    client.delete(f"/api/mcp/pages/{pid}")
    assert not page_assets.assets_dir(data_root, pid).exists()


def test_page_asset_route_rejects_traversal(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    assert client.get("/page-assets/pg/..%2f..%2fsecrets").status_code == 404


# -- editor (panels blueprint) endpoints ---------------------------------


def test_editor_assets_cache_list_delete(app: Flask) -> None:
    """The editor drawer's session-authed endpoints edit the same folder as the
    MCP ones, so an image cached in the editor is visible to the agent too."""
    client = app.test_client()
    _sign_in(client)
    pid = _make_page(client)
    with patch("app.page_assets.fetch_bytes", return_value=(_PNG, "image/png")):
        rec = client.post(
            f"/pages/canvas/c/{pid}/assets", json={"url": "https://cdn.example.com/a.png"}
        ).get_json()
    assert rec["url"].startswith(f"/page-assets/{pid}/")
    # Same folder as the MCP surface.
    listing = client.get(f"/api/mcp/pages/{pid}/assets").get_json()
    assert listing["assets"][0]["name"] == rec["name"]

    assert (
        client.get(f"/pages/canvas/c/{pid}/assets.json").get_json()["assets"][0]["name"]
        == rec["name"]
    )
    dele = client.post(f"/pages/canvas/c/{pid}/assets/{rec['name']}/delete")
    assert dele.status_code == 200 and dele.get_json()["deleted"] is True


def test_editor_assets_upload(app: Flask) -> None:
    import io

    client = app.test_client()
    _sign_in(client)
    pid = _make_page(client)
    resp = client.post(
        f"/pages/canvas/c/{pid}/assets",
        data={"file": (io.BytesIO(_PNG), "logo.png", "image/png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert resp.get_json()["url"].startswith(f"/page-assets/{pid}/")


def test_editor_assets_blocks_private_host(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _make_page(client)
    resp = client.post(f"/pages/canvas/c/{pid}/assets", json={"url": "http://127.0.0.1/x"})
    assert resp.status_code == 422
