"""Static files: versioned URLs are cached for good, unversioned ones for
a day, and a dev server keeps unversioned files revalidated."""

from __future__ import annotations

from pathlib import Path

from flask import Flask

from app.main import REPO_ROOT, create_app


def _make(tmp_path: Path, *, dev: bool) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
        dev=dev,
    )
    a.config["TESTING"] = True
    return a


def _cache_control(app: Flask, path: str) -> tuple[int, str | None]:
    resp = app.test_client().get(path)
    try:
        return resp.status_code, resp.headers.get("Cache-Control")
    finally:
        resp.close()


def test_versioned_static_is_immutable(tmp_path: Path) -> None:
    app = _make(tmp_path, dev=False)
    v = app.config["STATIC_VERSION"]
    status, cc = _cache_control(app, f"/static/style/base.css?v={v}")
    assert status == 200
    assert cc == "public, max-age=31536000, immutable"


def test_stale_or_missing_version_is_cached_a_day(tmp_path: Path) -> None:
    app = _make(tmp_path, dev=False)
    # The icon font is reached from its stylesheet by relative path, no version.
    status, cc = _cache_control(app, "/static/icons/phosphor/regular/Phosphor.woff2")
    assert status == 200
    assert cc == "public, max-age=86400"
    assert _cache_control(app, "/static/style/base.css?v=0.1.0")[1] == "public, max-age=86400"


def test_dev_keeps_unversioned_static_revalidated(tmp_path: Path) -> None:
    app = _make(tmp_path, dev=True)
    v = app.config["STATIC_VERSION"]
    assert _cache_control(app, f"/static/style/base.css?v={v}")[1] == (
        "public, max-age=31536000, immutable"
    )
    assert _cache_control(app, "/static/style/base.css")[1] == "no-cache"


def test_pages_are_not_affected(tmp_path: Path) -> None:
    app = _make(tmp_path, dev=False)
    status, cc = _cache_control(app, "/login")
    assert status in (200, 302)
    assert "immutable" not in (cc or "")


def test_bundled_fonts_are_cached_between_page_navigations(tmp_path: Path) -> None:
    app = _make(tmp_path, dev=False)
    path = "/plugins/fonts_core/static/archivo/400.woff2"
    assert _cache_control(app, path) == (200, "public, max-age=86400")
    client = app.test_client()
    response = client.get(path)
    etag = response.headers["ETag"]
    response.close()
    response = client.get(path, headers={"If-None-Match": etag})
    try:
        assert response.status_code == 304
        assert response.headers["Cache-Control"] == "public, max-age=86400"
    finally:
        response.close()
    # Editable plugin code and missing assets must not inherit the font policy.
    assert _cache_control(app, "/plugins/weather_now/client.js")[1] == "no-cache"
    assert _cache_control(app, "/plugins/fonts_core/static/missing.woff2")[0] == 404


def test_dev_keeps_bundled_fonts_revalidated(tmp_path: Path) -> None:
    app = _make(tmp_path, dev=True)
    assert _cache_control(app, "/plugins/fonts_core/static/inter/400.woff2") == (
        200,
        "no-cache",
    )


def test_the_version_changes_with_the_static_files(tmp_path: Path) -> None:
    """A branch build keeps the package version, so the files themselves
    must move the version, or browsers hold the old stylesheets for a year."""
    from app.app_factory import _static_digest

    root = tmp_path / "static"
    (root / "style").mkdir(parents=True)
    css = root / "style" / "a.css"
    css.write_text("a{}")
    before = _static_digest(root)
    assert before and before == _static_digest(root)
    css.write_text("a{color:red}")
    assert _static_digest(root) != before
    assert _make(tmp_path / "app", dev=False).config["STATIC_VERSION"].count("-") >= 1
