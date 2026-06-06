"""Marketplace contract: index fetch + tarball install + uninstall.

The HTTP layer is mocked at ``urllib.request.urlopen`` so tests don't
hit the network. Tarballs are built in-memory + their sha256 captured
the same way the catalog CI would do for a release artifact.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.main import REPO_ROOT
from app.marketplace import (
    CatalogEntry,
    IndexUnavailable,
    IndexUrlProvider,
    InstalledRecord,
    InstallRefused,
    Marketplace,
    TarballRejected,
)

# -- helpers -----------------------------------------------------------


def _make_index(widgets: list[dict[str, Any]]) -> bytes:
    return json.dumps({"version": 1, "widgets": widgets}).encode("utf-8")


def _make_tarball(
    *,
    folder: str,
    manifest: dict[str, Any],
    client_js: str = "export default function () {}\n",
) -> bytes:
    """Build a tar.gz like a GitHub source tarball: single top-level
    folder + manifest + client.js inside."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = json.dumps(manifest).encode("utf-8")
        info = tarfile.TarInfo(f"{folder}/plugin.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        client_bytes = client_js.encode("utf-8")
        info = tarfile.TarInfo(f"{folder}/client.js")
        info.size = len(client_bytes)
        tar.addfile(info, io.BytesIO(client_bytes))
    return buf.getvalue()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _minimal_manifest() -> dict[str, Any]:
    return {
        "tesserae_compat": "1.x",
        "name": "Sample",
        "version": "0.0.1",
        "kind": "widget",
        "supports": {"sizes": ["md"]},
    }


def _make_catalog_entry(
    *,
    widget_id: str = "sample",
    version: str = "0.0.1",
    sha256: str = "deadbeef" * 8,
    tarball_url: str = "https://example.invalid/sample-0.0.1.tar.gz",
) -> CatalogEntry:
    return CatalogEntry(
        id=widget_id,
        name="Sample",
        description="A sample widget for tests.",
        icon=None,
        author_name="Test Author",
        author_github="testauthor",
        tags=["utility"],
        kind="widget",
        tesserae_compat="1.x",
        official=False,
        screenshot_sizes=["lg"],
        release_version=version,
        release_tarball_url=tarball_url,
        release_sha256=sha256,
        source=None,
    )


class _FakeResponse:
    """Minimal context-manager response for monkey-patched urlopen."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._cursor = 0

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._payload[self._cursor :]
            self._cursor = len(self._payload)
            return chunk
        chunk = self._payload[self._cursor : self._cursor + size]
        self._cursor += len(chunk)
        return chunk


@pytest.fixture
def url_fixture(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    """Routing table for the patched ``urlopen``. Tests append URL →
    response bytes; any request to an unmapped URL raises."""
    table: dict[str, bytes] = {}

    def fake_urlopen(req: Any, timeout: float = 10.0) -> _FakeResponse:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url not in table:
            raise OSError(f"unexpected url in test: {url}")
        return _FakeResponse(table[url])

    # Patch the names the marketplace module imports at call time.
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return table


@pytest.fixture
def marketplace(tmp_path: Path, url_fixture: dict[str, bytes]) -> Iterator[Marketplace]:
    """A Marketplace pointed at temp dirs + a fake index URL. Test
    populates ``url_fixture`` with whatever the URL should return."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    state_path = tmp_path / "data" / "core" / "marketplace.json"

    index_url = "https://example.invalid/widgets.json"

    class _Provider(IndexUrlProvider):
        def __call__(self) -> str:
            return index_url

    mkt = Marketplace(
        plugins_dir=plugins_dir,
        state_path=state_path,
        schema_path=REPO_ROOT / "schema" / "plugin.schema.json",
        index_schema_path=REPO_ROOT / "schema" / "marketplace.schema.json",
        index_url_provider=_Provider(),
    )
    yield mkt


# -- index -------------------------------------------------------------


def test_empty_index_url_raises_unavailable(
    tmp_path: Path,
) -> None:
    """An unset index URL is treated as IndexUnavailable so the route
    layer can show "Marketplace disabled" without partially-rendered
    state."""

    class _Empty(IndexUrlProvider):
        def __call__(self) -> str:
            return ""

    mkt = Marketplace(
        plugins_dir=tmp_path / "plugins",
        state_path=tmp_path / "state.json",
        schema_path=REPO_ROOT / "schema" / "plugin.schema.json",
        index_schema_path=REPO_ROOT / "schema" / "marketplace.schema.json",
        index_url_provider=_Empty(),
    )
    with pytest.raises(IndexUnavailable):
        mkt.fetch_index()


def test_unsupported_index_version_rejected(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    url_fixture["https://example.invalid/widgets.json"] = json.dumps(
        {"version": 99, "widgets": []}
    ).encode("utf-8")
    with pytest.raises(IndexUnavailable, match="not supported"):
        marketplace.fetch_index()


def test_invalid_index_schema_rejected(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    # version: 1 + widgets-as-string (instead of array) fails the
    # schema even before per-entry validation runs.
    url_fixture["https://example.invalid/widgets.json"] = json.dumps(
        {"version": 1, "widgets": "not-an-array"}
    ).encode("utf-8")
    with pytest.raises(IndexUnavailable):
        marketplace.fetch_index()


def test_valid_index_returns_entries(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    url_fixture["https://example.invalid/widgets.json"] = _make_index(
        [
            {
                "id": "sample",
                "name": "Sample",
                "description": "A sample widget for tests.",
                "author": {"name": "Test Author", "github": "testauthor"},
                "tags": ["utility"],
                "kind": "widget",
                "tesserae_compat": "1.x",
                "screenshot_sizes": ["lg"],
                "release": {
                    "version": "0.0.1",
                    "tarball_url": "https://example.invalid/sample.tar.gz",
                    "sha256": "a" * 64,
                },
            }
        ]
    )
    entries = marketplace.fetch_index()
    assert len(entries) == 1
    assert entries[0].id == "sample"
    assert entries[0].tags == ["utility"]


def test_index_cache_reused_within_ttl(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """Second call to fetch_index returns the cached snapshot without
    re-hitting the URL; clearing the URL table proves the cache is the
    only source on the second call."""
    url_fixture["https://example.invalid/widgets.json"] = _make_index([])
    marketplace.fetch_index()
    url_fixture.clear()  # would raise on any further urlopen
    again = marketplace.fetch_index()
    assert again == []


# -- install -----------------------------------------------------------


def test_install_rejects_bundled_collision(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """A folder at plugins/<id>/ that isn't in marketplace.json is
    assumed to be bundled. Install refuses rather than overwrite."""
    bundled = marketplace._plugins_dir / "weather_now"
    bundled.mkdir()
    entry = _make_catalog_entry(widget_id="weather_now")
    with pytest.raises(InstallRefused, match="already exists"):
        marketplace.install(entry)


def test_install_rejects_compat_mismatch(marketplace: Marketplace) -> None:
    entry = _make_catalog_entry()
    bad_compat = CatalogEntry(**{**entry.__dict__, "tesserae_compat": "9.x"})
    with pytest.raises(InstallRefused, match="compat"):
        marketplace.install(bad_compat)


def test_install_rejects_sha256_mismatch(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    tarball = _make_tarball(folder="sample-main", manifest=_minimal_manifest())
    url = "https://example.invalid/sample-bad-hash.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        sha256="0" * 64,  # deliberately wrong
        tarball_url=url,
    )
    with pytest.raises(TarballRejected, match="sha256"):
        marketplace.install(entry)


def test_install_rejects_oversize_tarball(
    marketplace: Marketplace,
    url_fixture: dict[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cap the tarball size to a tiny value, then ship a payload that
    exceeds it. The download streams 64 KiB at a time so the cap fires
    on the second chunk."""
    monkeypatch.setattr("app.marketplace._MAX_TARBALL_BYTES", 64)
    big_payload = b"\x00" * 200
    url = "https://example.invalid/big.tar.gz"
    url_fixture[url] = big_payload
    entry = _make_catalog_entry(
        sha256=_sha256(big_payload),
        tarball_url=url,
    )
    with pytest.raises(TarballRejected, match="cap"):
        marketplace.install(entry)


def test_install_rejects_missing_manifest(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """Tarball is a valid gzip but contains no plugin.json anywhere."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"not a manifest"
        info = tarfile.TarInfo("readme.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    payload = buf.getvalue()
    url = "https://example.invalid/no-manifest.tar.gz"
    url_fixture[url] = payload
    entry = _make_catalog_entry(
        sha256=_sha256(payload),
        tarball_url=url,
    )
    with pytest.raises(InstallRefused, match=r"plugin\.json"):
        marketplace.install(entry)


def test_install_rejects_manifest_kind_mismatch(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """Catalog claims kind=widget; tarball ships kind=font. The
    embedded check rejects so the catalog can't lie about what it
    points at."""
    manifest = _minimal_manifest()
    manifest["kind"] = "font"
    # Make it pass the font schema by providing a fonts array.
    manifest["fonts"] = [
        {
            "id": "f",
            "name": "F",
            "weights": [400],
            "files": {"400": "f.woff2"},
        }
    ]
    tarball = _make_tarball(folder="sample-main", manifest=manifest)
    url = "https://example.invalid/wrong-kind.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        sha256=_sha256(tarball),
        tarball_url=url,
    )
    with pytest.raises(InstallRefused, match="kind"):
        marketplace.install(entry)


def test_install_happy_path(marketplace: Marketplace, url_fixture: dict[str, bytes]) -> None:
    """End-to-end: download → sha256 verify → extract → manifest
    validate → move into plugins_dir → persist record."""
    tarball = _make_tarball(folder="sample-main", manifest=_minimal_manifest())
    url = "https://example.invalid/sample-0.0.1.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        sha256=_sha256(tarball),
        tarball_url=url,
    )
    result = marketplace.install(entry)
    assert result.plugin_id == "sample"
    assert result.version == "0.0.1"
    assert result.restart_required is True

    target = marketplace._plugins_dir / "sample"
    assert (target / "plugin.json").exists()
    assert (target / "client.js").exists()

    installed = marketplace.installed()
    assert "sample" in installed
    record = installed["sample"]
    assert isinstance(record, InstalledRecord)
    assert record.version == "0.0.1"
    assert record.sha256 == _sha256(tarball)


def test_install_replaces_existing_marketplace_entry(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """Installing over a marketplace-installed plugin (upgrade flow)
    succeeds; the second tarball wins."""
    t1 = _make_tarball(folder="sample-main", manifest=_minimal_manifest())
    url1 = "https://example.invalid/sample-0.0.1.tar.gz"
    url_fixture[url1] = t1
    entry1 = _make_catalog_entry(sha256=_sha256(t1), tarball_url=url1)
    marketplace.install(entry1)

    upgraded_manifest = _minimal_manifest()
    upgraded_manifest["version"] = "0.0.2"
    upgraded_manifest["name"] = "Sample (upgraded)"
    t2 = _make_tarball(folder="sample-main", manifest=upgraded_manifest)
    url2 = "https://example.invalid/sample-0.0.2.tar.gz"
    url_fixture[url2] = t2
    entry2 = _make_catalog_entry(
        version="0.0.2",
        sha256=_sha256(t2),
        tarball_url=url2,
    )
    marketplace.install(entry2)

    installed = marketplace.installed()
    assert installed["sample"].version == "0.0.2"
    fresh_manifest = json.loads((marketplace._plugins_dir / "sample" / "plugin.json").read_text())
    assert fresh_manifest["version"] == "0.0.2"
    assert fresh_manifest["name"] == "Sample (upgraded)"


# -- uninstall ---------------------------------------------------------


def test_uninstall_untracked_returns_false(marketplace: Marketplace) -> None:
    """Refuses to touch any folder we didn't install. This is the
    safety net for bundled plugins."""
    bundled = marketplace._plugins_dir / "weather_now"
    bundled.mkdir()
    (bundled / "plugin.json").write_text("{}")
    assert marketplace.uninstall("weather_now") is False
    assert bundled.exists()


def test_uninstall_removes_marketplace_plugin(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    tarball = _make_tarball(folder="sample-main", manifest=_minimal_manifest())
    url = "https://example.invalid/sample-0.0.1.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(sha256=_sha256(tarball), tarball_url=url)
    marketplace.install(entry)

    assert marketplace.uninstall("sample") is True
    assert not (marketplace._plugins_dir / "sample").exists()
    assert "sample" not in marketplace.installed()


def test_uninstall_keeps_data_dir_by_default(
    marketplace: Marketplace, url_fixture: dict[str, bytes], tmp_path: Path
) -> None:
    """delete_data=False (default) leaves data/plugins/<id>/ alone so
    a reinstall finds the user's state."""
    tarball = _make_tarball(folder="sample-main", manifest=_minimal_manifest())
    url = "https://example.invalid/sample-0.0.1.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(sha256=_sha256(tarball), tarball_url=url)
    marketplace.install(entry)

    data_dir = tmp_path / "data" / "plugins" / "sample"
    data_dir.mkdir(parents=True)
    (data_dir / "state.json").write_text('{"foo":"bar"}')

    marketplace.uninstall("sample", delete_data=False)
    assert data_dir.exists()
    assert (data_dir / "state.json").read_text() == '{"foo":"bar"}'


def test_uninstall_deletes_data_dir_when_asked(
    marketplace: Marketplace, url_fixture: dict[str, bytes], tmp_path: Path
) -> None:
    tarball = _make_tarball(folder="sample-main", manifest=_minimal_manifest())
    url = "https://example.invalid/sample-0.0.1.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(sha256=_sha256(tarball), tarball_url=url)
    marketplace.install(entry)

    # data_root convention from Marketplace.uninstall: plugins_dir.parent / data / plugins
    data_dir = marketplace._plugins_dir.parent / "data" / "plugins" / "sample"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "state.json").write_text("{}")

    marketplace.uninstall("sample", delete_data=True)
    assert not data_dir.exists()


# -- persistence -------------------------------------------------------


def test_corrupt_state_file_treated_as_empty(
    marketplace: Marketplace,
) -> None:
    marketplace._state_path.parent.mkdir(parents=True, exist_ok=True)
    marketplace._state_path.write_text("not-json")
    assert marketplace.installed() == {}
