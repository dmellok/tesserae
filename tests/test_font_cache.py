"""Server-side webfont cache: fetch once at author time, serve locally, never
touch the network at render time (app/font_cache.py).

Every remote fetch is patched out: the tests hand back a css2 response or
font bytes themselves, so nothing here reaches Google.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask

from app import font_cache
from app.main import REPO_ROOT, create_app

# A trimmed css2 answer: two subsets of the same face, plus an italic. Google
# labels each block with the subset it covers.
_GOOGLE_CSS = """
/* japanese */
@font-face {
  font-family: 'Shippori Mincho';
  font-style: normal;
  font-weight: 400;
  font-display: block;
  src: url(https://fonts.gstatic.com/s/shipporimincho/v14/jp-400.woff2) format('woff2');
  unicode-range: U+3000-303F, U+3040-309F;
}
/* latin */
@font-face {
  font-family: 'Shippori Mincho';
  font-style: normal;
  font-weight: 400;
  font-display: block;
  src: url(https://fonts.gstatic.com/s/shipporimincho/v14/latin-400.woff2) format('woff2');
  unicode-range: U+0000-00FF, U+0131, U+0152-0153;
}
/* latin */
@font-face {
  font-family: 'Shippori Mincho';
  font-style: normal;
  font-weight: 700;
  font-display: block;
  src: url(https://fonts.gstatic.com/s/shipporimincho/v14/latin-700.woff2) format('woff2');
  unicode-range: U+0000-00FF;
}
"""
_WOFF2 = b"wOF2" + b"\x00" * 60
_TTF = b"\x00\x01\x00\x00" + b"\x00" * 60


def _fake_fetch(url: str, **_: Any) -> tuple[bytes, str]:
    if "fonts.googleapis.com" in url:
        return _GOOGLE_CSS.encode(), "text/css"
    if url.endswith(".woff2"):
        return _WOFF2 + url.encode(), "font/woff2"
    if url.endswith(".ttf"):
        return _TTF, "font/ttf"
    return b"<html>not a font</html>", "text/html"


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


# -- module ----------------------------------------------------------------


def test_google_css_url_sorts_tuples_ital_first() -> None:
    url = font_cache.google_css_url("Shippori Mincho", [700, 400], ["italic", "normal"])
    assert "family=Shippori%20Mincho:ital,wght@0,400;0,700;1,400;1,700" in url
    assert url.startswith("https://fonts.googleapis.com/css2?")


def test_parse_google_css_reads_every_block() -> None:
    blocks = font_cache.parse_google_css(_GOOGLE_CSS)
    assert [(b["subset"], b["weight"]) for b in blocks] == [
        ("japanese", "400"),
        ("latin", "400"),
        ("latin", "700"),
    ]
    assert blocks[1]["url"].endswith("latin-400.woff2")
    assert blocks[1]["unicode_range"].startswith("U+0000-00FF")


def test_cache_google_font_keeps_only_requested_subsets(tmp_path: Path) -> None:
    with patch.object(font_cache, "fetch_bytes", side_effect=_fake_fetch):
        font = font_cache.cache_google_font(tmp_path, "Shippori Mincho", weights=[400, 700])
    assert font.slug == "shippori_mincho"
    assert font.weights == [400, 700]
    assert {f.subset for f in font.faces} == {"latin"}
    files = sorted(p.name for p in font_cache.font_dir(tmp_path, font.slug).iterdir())
    assert files == ["400.woff2", "700.woff2", "manifest.json"]
    # The unicode-range rides along so a second subset can coexist later.
    assert all(f.unicode_range for f in font.faces)
    # Idempotent: caching again replaces the faces, no duplicates.
    with patch.object(font_cache, "fetch_bytes", side_effect=_fake_fetch):
        again = font_cache.cache_google_font(tmp_path, "Shippori Mincho", weights=[400, 700])
    assert len(again.faces) == 2


def test_cache_google_font_names_missing_subset(tmp_path: Path) -> None:
    with (
        patch.object(font_cache, "fetch_bytes", side_effect=_fake_fetch),
        pytest.raises(font_cache.FontCacheError, match="available: japanese, latin"),
    ):
        font_cache.cache_google_font(tmp_path, "Shippori Mincho", subsets=["cyrillic"])


def test_cache_font_url_sniffs_the_file(tmp_path: Path) -> None:
    with patch.object(font_cache, "fetch_bytes", side_effect=_fake_fetch):
        font = font_cache.cache_font_url(
            tmp_path, "House Sans", "https://example.com/house.ttf", weight=500
        )
        with pytest.raises(font_cache.FontCacheError, match="not a font file"):
            font_cache.cache_font_url(tmp_path, "House Sans", "https://example.com/page.html")
    assert font.faces[0].format == "truetype"
    assert font.faces[0].file == "500.ttf"
    assert font_cache.get(tmp_path, "House Sans") is not None
    assert font_cache.get(tmp_path, "house_sans") is not None


@pytest.mark.parametrize("bad", ["", "   ", "…"])
def test_bad_family_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(font_cache.FontCacheError):
        font_cache.slugify(bad)
    assert font_cache.get(tmp_path, bad) is None


def test_slug_cannot_traverse(tmp_path: Path) -> None:
    """A family name is reduced to [a-z0-9_] before it becomes a folder name,
    so path characters never reach the filesystem."""
    assert font_cache.slugify("../x") == "x"
    assert font_cache.slugify("Shippori Mincho/../../etc") == "shippori_mincho_etc"
    with pytest.raises(font_cache.FontCacheError):
        font_cache.font_dir(tmp_path, "../x")


def test_weights_and_styles_validated(tmp_path: Path) -> None:
    with pytest.raises(font_cache.FontCacheError, match="out of range"):
        font_cache.cache_google_font(tmp_path, "Inter", weights=[50])
    with pytest.raises(font_cache.FontCacheError, match="invalid style"):
        font_cache.cache_google_font(tmp_path, "Inter", styles=["oblique"])


def test_css_builders(tmp_path: Path) -> None:
    with patch.object(font_cache, "fetch_bytes", side_effect=_fake_fetch):
        font = font_cache.cache_google_font(tmp_path, "Shippori Mincho")
    css = font_cache.font_face_css(font)
    assert "font-family: 'Shippori Mincho'" in css
    assert "url('/page-fonts/shippori_mincho/400.woff2') format('woff2')" in css
    assert "unicode-range: U+0000-00FF" in css
    data = font_cache.font_face_css_datauri(tmp_path, font)
    assert data.count("data:font/woff2;base64,") == 2
    assert font_cache.all_font_face_css(tmp_path) == css
    assert font_cache.delete_font(tmp_path, "Shippori Mincho")
    assert font_cache.list_fonts(tmp_path) == []
    assert font_cache.all_font_face_css(tmp_path) == ""


# -- served through the app ------------------------------------------------


def test_mcp_add_list_serve_delete(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    with patch.object(font_cache, "fetch_bytes", side_effect=_fake_fetch):
        r = client.post("/api/mcp/fonts", json={"family": "Shippori Mincho", "weights": [400, 700]})
    assert r.status_code == 200, r.get_json()
    rec = r.get_json()
    assert rec["id"] == "shippori_mincho" and rec["weights"] == [400, 700]
    assert "font-family: 'Shippori Mincho'" in rec["usage"]

    listed = client.get("/api/mcp/fonts").get_json()["fonts"]
    assert [f["id"] for f in listed] == ["shippori_mincho"]

    # The appearance catalog lists it next to the bundled fonts, tagged.
    fonts = client.get("/api/mcp/appearance").get_json()["fonts"]
    cached = [f for f in fonts if f.get("source") == "cached"]
    assert cached == [
        {
            "id": "shippori_mincho",
            "name": "Shippori Mincho",
            "source": "cached",
            "weights": [400, 700],
        }
    ]
    assert any(f["id"] == "inter" for f in fonts)

    # The files serve from the local origin; the sandbox CSS inlines them.
    served = client.get("/page-fonts/shippori_mincho/400.woff2")
    assert served.status_code == 200 and served.data.startswith(b"wOF2")
    served.close()  # send_from_directory streams; release the file handle
    assert client.get("/page-fonts/shippori_mincho/manifest.json").status_code == 404
    assert client.get("/page-fonts/shippori_mincho/../x").status_code == 404
    face = client.get("/fonts/face/shippori_mincho.css")
    assert face.status_code == 200
    assert b"data:font/woff2;base64," in face.data and b"'Shippori Mincho'" in face.data

    assert client.delete("/api/mcp/fonts/Shippori Mincho").status_code == 200
    assert client.get("/api/mcp/fonts").get_json()["fonts"] == []
    assert client.get("/fonts/face/shippori_mincho.css").status_code == 404
    assert client.delete("/api/mcp/fonts/shippori_mincho").status_code == 404


def test_mcp_add_font_rejects_bad_requests(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    assert client.post("/api/mcp/fonts", json={}).status_code == 422
    with patch.object(font_cache, "fetch_bytes", side_effect=_fake_fetch):
        r = client.post(
            "/api/mcp/fonts", json={"family": "Ghost", "url": "https://example.com/page.html"}
        )
    assert r.status_code == 422 and "not a font file" in r.get_json()["error"]


def test_canvas_page_uses_cached_font_and_degrades_without_it(app: Flask) -> None:
    """The acceptance path: a canvas whose code element names the family gets
    the face inlined by autolibs (it is in the sandbox font list), the page
    ``font`` field accepts the cached id, and both degrade when the cache entry
    is gone."""
    client = app.test_client()
    _sign_in(client)
    with patch.object(font_cache, "fetch_bytes", side_effect=_fake_fetch):
        client.post("/api/mcp/fonts", json={"family": "Shippori Mincho"})
    page_id = client.post("/api/mcp/pages", json={"name": "Type", "w": 800, "h": 480}).get_json()[
        "id"
    ]
    r = client.put(
        f"/api/mcp/pages/{page_id}/canvas",
        json={
            "w": 800,
            "h": 480,
            "font": "shippori_mincho",
            "els": [
                {
                    "id": "type1",
                    "kind": "code",
                    "x": 0,
                    "y": 0,
                    "w": 800,
                    "h": 480,
                    "html": "<h1>春</h1>",
                    "css": "h1{font-family:'Shippori Mincho', serif}",
                    "js": "",
                }
            ],
        },
    )
    assert r.status_code == 200, r.get_json()

    html = client.get(f"/compose/{page_id}").get_data(as_text=True)
    # Page-level: the artboard's font-family is the cached family, and the
    # page CSS carries a same-origin @font-face for it.
    assert "'Shippori Mincho'" in html
    assert "/page-fonts/shippori_mincho/400.woff2" in html
    # Sandbox-level: the autolibs font list names it with its data-URI CSS URL.
    assert '"name": "Shippori Mincho", "url": "/fonts/face/shippori_mincho.css"' in html

    font_cache.delete_font(app.config["DATA_ROOT"], "shippori_mincho")
    html = client.get(f"/compose/{page_id}").get_data(as_text=True)
    assert "/page-fonts/shippori_mincho" not in html
    assert '"name": "Shippori Mincho"' not in html
    # The page font falls back to the default bundled family, not a broken ref.
    assert "font-family: 'Shippori Mincho'" not in html.split("<body", 1)[0]


def test_editor_preview_sees_cached_fonts(app: Flask) -> None:
    """The in-browser editor renders code elements through the same sandbox
    list; a cached family missing there painted the fallback in the preview
    while the device push had the real face."""
    client = app.test_client()
    _sign_in(client)
    with patch.object(font_cache, "fetch_bytes", side_effect=_fake_fetch):
        client.post("/api/mcp/fonts", json={"family": "Shippori Mincho"})
    page_id = client.post("/api/mcp/pages", json={"name": "Type", "w": 800, "h": 480}).get_json()[
        "id"
    ]
    html = client.get(f"/pages/canvas/c/{page_id}").get_data(as_text=True)
    assert '"name": "Shippori Mincho", "url": "/fonts/face/shippori_mincho.css"' in html
    assert "/page-fonts/shippori_mincho/400.woff2" in html
