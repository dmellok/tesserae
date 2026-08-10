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
    IndexSnapshot,
    IndexUnavailable,
    IndexUrlProvider,
    InstalledRecord,
    InstallRefused,
    Marketplace,
    TarballRejected,
)


def _seed_index(marketplace: Marketplace, entries: list[CatalogEntry]) -> None:
    """Skip the fetch + parse round-trip when a test only cares about
    a downstream code path (uninstall, browse payload, etc.) and not
    the index-loading itself. Seeds the in-memory cache directly."""
    marketplace._index_cache = IndexSnapshot(url=marketplace.index_url(), entries=entries)


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


def _make_bundle_tarball(
    *,
    wrapper: str,
    folders: dict[str, dict[str, Any]],
    extra_files: dict[str, bytes] | None = None,
) -> bytes:
    """Build a bundle tarball: a single wrapping folder whose children
    are themselves plugin folders. Mirrors the GitHub source-tarball
    shape but with multiple subplugins inside, e.g.::

        github-1.2/
          github_core/plugin.json
          github_core/client.js
          github_releases/plugin.json
          github_releases/client.js
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for sub, manifest in folders.items():
            for name, payload in [
                ("plugin.json", json.dumps(manifest).encode()),
                ("client.js", b"export default function () {}\n"),
            ]:
                info = tarfile.TarInfo(f"{wrapper}/{sub}/{name}")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        for path, payload in (extra_files or {}).items():
            info = tarfile.TarInfo(f"{wrapper}/{path}")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
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
    folders: list[str] | None = None,
    extra_screenshot_count: int = 0,
    kind: str = "widget",
) -> CatalogEntry:
    return CatalogEntry(
        id=widget_id,
        name="Sample",
        description="A sample widget for tests.",
        icon=None,
        author_name="Test Author",
        author_github="testauthor",
        tags=["utility"],
        kind=kind,  # type: ignore[arg-type]
        tesserae_compat="1.x",
        official=False,
        screenshot_sizes=["lg"],
        extra_screenshot_count=extra_screenshot_count,
        folders=folders,
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
    # Default when the field is omitted: zero extras, single-image card.
    assert entries[0].extra_screenshot_count == 0


def test_extra_screenshot_count_parses_through(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """A widget that declares ``extra_screenshot_count: 2`` exposes
    the value on CatalogEntry so the route layer can build the
    carousel URL list."""
    url_fixture["https://example.invalid/widgets.json"] = _make_index(
        [
            {
                "id": "sample",
                "name": "Sample",
                "description": "A sample widget for tests.",
                "author": {"name": "Test Author"},
                "tags": ["utility"],
                "kind": "widget",
                "tesserae_compat": "1.x",
                "screenshot_sizes": ["lg"],
                "extra_screenshot_count": 2,
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
    assert entries[0].extra_screenshot_count == 2


def test_extra_screenshot_count_rejects_above_schema_cap(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """The schema caps extras at 9 to keep the dot-indicator row
    from overflowing the 320px-min card width. A widget that
    declares 10 must be rejected at the schema layer, not silently
    truncated at render time."""
    url_fixture["https://example.invalid/widgets.json"] = _make_index(
        [
            {
                "id": "toomany",
                "name": "Too many",
                "description": "Tries to ship more carousel shots than the schema allows.",
                "author": {"name": "Test Author"},
                "tags": ["utility"],
                "kind": "widget",
                "tesserae_compat": "1.x",
                "screenshot_sizes": ["lg"],
                "extra_screenshot_count": 10,
                "release": {
                    "version": "0.0.1",
                    "tarball_url": "https://example.invalid/sample.tar.gz",
                    "sha256": "a" * 64,
                },
            }
        ]
    )
    with pytest.raises(IndexUnavailable):
        marketplace.fetch_index()


def test_entries_payload_builds_screenshot_urls_list() -> None:
    """``_entries_payload`` materialises ``screenshot_urls`` as a list
    so the Browse template can branch on ``len(urls) > 1`` to render
    the carousel. Single-screenshot entries get a one-element list
    (back-compat with the single-image render); entries that declare
    ``extra_screenshot_count: N`` get N+1 URLs in catalog order."""
    from app.marketplace_routes import _entries_payload

    single = _make_catalog_entry(widget_id="single")
    multi = _make_catalog_entry(widget_id="multi", extra_screenshot_count=2)
    payload = _entries_payload(
        [single, multi],
        installed={},
        screenshots_base="https://cdn.example.invalid",
        plugins_dir=None,
    )
    assert payload[0]["screenshot_urls"] == [
        "https://cdn.example.invalid/screenshots/single/lg.png",
    ]
    assert payload[1]["screenshot_urls"] == [
        "https://cdn.example.invalid/screenshots/multi/lg.png",
        "https://cdn.example.invalid/screenshots/multi/extra-1.png",
        "https://cdn.example.invalid/screenshots/multi/extra-2.png",
    ]


def test_stars_sidecar_merges_into_entries(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """When ``stars.json`` is published next to ``widgets.json``,
    ``fetch_index`` merges the counts into ``CatalogEntry.stars`` for
    every entry whose id matches. Missing ids stay ``None``."""
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
            },
            {
                "id": "uncounted",
                "name": "Uncounted",
                "description": "No star data published yet.",
                "author": {"name": "Test Author", "github": "testauthor"},
                "tags": ["utility"],
                "kind": "widget",
                "tesserae_compat": "1.x",
                "screenshot_sizes": ["lg"],
                "release": {
                    "version": "0.0.1",
                    "tarball_url": "https://example.invalid/uncounted.tar.gz",
                    "sha256": "a" * 64,
                },
            },
        ]
    )
    url_fixture["https://example.invalid/stars.json"] = json.dumps(
        {
            "fetched_at": "2026-06-13T11:00:00Z",
            "stars": {"sample": 42, "stale_entry_no_longer_in_catalog": 7},
        }
    ).encode("utf-8")
    entries = {e.id: e for e in marketplace.fetch_index()}
    assert entries["sample"].stars == 42
    assert entries["uncounted"].stars is None


def test_stars_sidecar_missing_does_not_break_catalog(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """A 404 / network blip on ``stars.json`` must not poison the
    catalog. Entries should still come through with ``stars=None``."""
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
    # stars.json deliberately not added to url_fixture → OSError on fetch.
    entries = marketplace.fetch_index()
    assert len(entries) == 1
    assert entries[0].stars is None


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


def test_legacy_record_without_folders_loads_as_single_folder(
    marketplace: Marketplace,
) -> None:
    """Pre-bundle records stored ``plugin_id`` instead of a folders
    list. Make sure they read back as a single-folder install so an
    upgrade doesn't break old marketplace.json files."""
    marketplace._state_path.parent.mkdir(parents=True, exist_ok=True)
    marketplace._state_path.write_text(
        json.dumps(
            {
                "sample": {
                    "catalog_id": "sample",
                    "plugin_id": "sample",
                    "version": "0.0.1",
                    "sha256": "deadbeef",
                    "source": None,
                    "installed_at": "2026-06-01T00:00:00Z",
                }
            }
        )
    )
    installed = marketplace.installed()
    assert "sample" in installed
    assert installed["sample"].folders == ["sample"]


# -- bundles -----------------------------------------------------------


def _github_manifest(name: str) -> dict[str, Any]:
    """Variant of _minimal_manifest with a distinct name so each
    subfolder's plugin.json reads as a different widget. Versions
    deliberately differ between subfolders because real bundles
    (calendar_core + calendar_day + calendar_week, etc.) version each
    folder independently, the marketplace's per-folder version check
    should be skipped for bundles."""
    return {
        "tesserae_compat": "1.x",
        "name": name,
        "version": "0.0.1",
        "kind": "widget",
        "supports": {"sizes": ["md"]},
    }


def test_bundle_install_happy_path(marketplace: Marketplace, url_fixture: dict[str, bytes]) -> None:
    """End-to-end bundle install: tarball contains a wrapper folder
    with two plugin subfolders, both land as siblings under plugins/,
    and the install record lists both."""
    tarball = _make_bundle_tarball(
        wrapper="github-bundle-1.0",
        folders={
            "github_core": _github_manifest("GitHub Core"),
            "github_releases": _github_manifest("GitHub Releases"),
        },
    )
    url = "https://example.invalid/github-bundle.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        widget_id="github",
        sha256=_sha256(tarball),
        tarball_url=url,
        folders=["github_core", "github_releases"],
    )
    result = marketplace.install(entry)
    assert result.plugin_id == "github"

    plugins_dir = marketplace._plugins_dir
    assert (plugins_dir / "github_core" / "plugin.json").exists()
    assert (plugins_dir / "github_releases" / "plugin.json").exists()

    installed = marketplace.installed()
    assert "github" in installed
    assert installed["github"].folders == ["github_core", "github_releases"]


def test_bundle_install_auto_detects_layout_without_declared_folders(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """When the catalog entry omits `folders`, the install path
    auto-detects the bundle layout by inspecting the tarball."""
    tarball = _make_bundle_tarball(
        wrapper="calendar-1.0",
        folders={
            "calendar_core": _github_manifest("Calendar Core"),
            "calendar_day": _github_manifest("Calendar Day"),
            "calendar_week": _github_manifest("Calendar Week"),
        },
    )
    url = "https://example.invalid/calendar-bundle.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        widget_id="calendar",
        sha256=_sha256(tarball),
        tarball_url=url,
        folders=None,  # auto-detect
    )
    marketplace.install(entry)

    record = marketplace.installed()["calendar"]
    assert sorted(record.folders) == ["calendar_core", "calendar_day", "calendar_week"]


def test_bundle_install_rejects_declared_folders_mismatch(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """Catalog claims folders A+B but tarball has A+C — must reject
    rather than silently installing whatever was inside."""
    tarball = _make_bundle_tarball(
        wrapper="bundle-1.0",
        folders={
            "a_core": _github_manifest("A Core"),
            "c_widget": _github_manifest("C Widget"),  # not declared
        },
    )
    url = "https://example.invalid/mismatch.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        widget_id="mismatch",
        sha256=_sha256(tarball),
        tarball_url=url,
        folders=["a_core", "b_widget"],  # b_widget missing from tarball
    )
    with pytest.raises(InstallRefused, match="declares folders"):
        marketplace.install(entry)


def test_bundle_install_rejects_subfolder_without_manifest(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """Every direct child of the bundle root must have plugin.json;
    a stray subfolder (LICENSE, docs/, etc.) trips this. Real bundles
    put aux files inside the plugin subfolders, not at the bundle root."""
    tarball = _make_bundle_tarball(
        wrapper="bundle-1.0",
        folders={"core": _github_manifest("Core")},
        extra_files={"docs/README.md": b"# docs"},
    )
    url = "https://example.invalid/stray-docs.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        widget_id="bundle",
        sha256=_sha256(tarball),
        tarball_url=url,
        folders=None,
    )
    with pytest.raises(InstallRefused, match="docs"):
        marketplace.install(entry)


def test_bundle_install_collision_on_one_subfolder_aborts(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """One subfolder already exists (bundled or hand-installed) →
    the whole bundle is refused, leaving the existing folder + the
    other subfolders untouched."""
    bundled = marketplace._plugins_dir / "foo_core"
    bundled.mkdir()
    (bundled / "plugin.json").write_text("{}")

    tarball = _make_bundle_tarball(
        wrapper="bundle-1.0",
        folders={
            "foo_core": _github_manifest("Foo Core"),  # collides
            "foo_widget": _github_manifest("Foo Widget"),
        },
    )
    url = "https://example.invalid/foo-bundle.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        widget_id="foo",
        sha256=_sha256(tarball),
        tarball_url=url,
        folders=["foo_core", "foo_widget"],
    )
    with pytest.raises(InstallRefused, match="already exists"):
        marketplace.install(entry)

    # The collision sits at foo_core; foo_widget must NOT have been
    # partial-installed. The early collision check fires before any
    # download / extract, so the post-extract install never runs.
    assert not (marketplace._plugins_dir / "foo_widget").exists()


def test_bundle_uninstall_removes_every_folder(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """uninstall(catalog_id) drops the record + rms every folder
    listed on the install record, not just the catalog id."""
    tarball = _make_bundle_tarball(
        wrapper="bundle-1.0",
        folders={
            "github_core": _github_manifest("GitHub Core"),
            "github_releases": _github_manifest("GitHub Releases"),
            "github_repo": _github_manifest("GitHub Repo"),
        },
    )
    url = "https://example.invalid/github.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        widget_id="github",
        sha256=_sha256(tarball),
        tarball_url=url,
        folders=["github_core", "github_releases", "github_repo"],
    )
    marketplace.install(entry)
    assert (marketplace._plugins_dir / "github_core").exists()
    assert (marketplace._plugins_dir / "github_releases").exists()
    assert (marketplace._plugins_dir / "github_repo").exists()

    assert marketplace.uninstall("github") is True
    assert not (marketplace._plugins_dir / "github_core").exists()
    assert not (marketplace._plugins_dir / "github_releases").exists()
    assert not (marketplace._plugins_dir / "github_repo").exists()
    assert "github" not in marketplace.installed()


def test_bundle_uninstall_with_delete_data_clears_each_data_dir(
    marketplace: Marketplace, url_fixture: dict[str, bytes], tmp_path: Path
) -> None:
    """delete_data=True should rm data/plugins/<folder>/ for every
    folder in the bundle, not just the catalog id."""
    tarball = _make_bundle_tarball(
        wrapper="bundle-1.0",
        folders={
            "foo_core": _github_manifest("Foo Core"),
            "foo_widget": _github_manifest("Foo Widget"),
        },
    )
    url = "https://example.invalid/foo.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        widget_id="foo",
        sha256=_sha256(tarball),
        tarball_url=url,
        folders=["foo_core", "foo_widget"],
    )
    marketplace.install(entry)

    # data_root convention from Marketplace.uninstall:
    # plugins_dir.parent / data / plugins / <folder>
    data_root = marketplace._plugins_dir.parent / "data" / "plugins"
    for f in ("foo_core", "foo_widget"):
        (data_root / f).mkdir(parents=True, exist_ok=True)
        (data_root / f / "state.json").write_text("{}")

    marketplace.uninstall("foo", delete_data=True)
    assert not (data_root / "foo_core").exists()
    assert not (data_root / "foo_widget").exists()


def test_bundle_reinstall_replaces_in_place(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    """A second install over the same catalog_id (upgrade flow) should
    replace folders the bundle owns; folders introduced by the new
    version land fresh, and the record now lists the new set."""
    t1 = _make_bundle_tarball(
        wrapper="bundle-1.0",
        folders={"core": _github_manifest("Core v0")},
    )
    url1 = "https://example.invalid/v1.tar.gz"
    url_fixture[url1] = t1
    entry1 = _make_catalog_entry(
        widget_id="bundle",
        sha256=_sha256(t1),
        tarball_url=url1,
        folders=["core"],
    )
    marketplace.install(entry1)

    # Second release adds a second folder + bumps the first.
    t2 = _make_bundle_tarball(
        wrapper="bundle-1.1",
        folders={
            "core": _github_manifest("Core v1"),
            "widget_a": _github_manifest("Widget A"),
        },
    )
    url2 = "https://example.invalid/v2.tar.gz"
    url_fixture[url2] = t2
    entry2 = _make_catalog_entry(
        widget_id="bundle",
        version="0.0.2",
        sha256=_sha256(t2),
        tarball_url=url2,
        folders=["core", "widget_a"],
    )
    marketplace.install(entry2)

    record = marketplace.installed()["bundle"]
    assert sorted(record.folders) == ["core", "widget_a"]
    fresh_core = json.loads((marketplace._plugins_dir / "core" / "plugin.json").read_text())
    assert fresh_core["name"] == "Core v1"


def test_uninstall_adopts_prebundled_folders_without_record(
    marketplace: Marketplace, url_fixture: dict[str, bytes], tmp_path: Path
) -> None:
    """Pre-bundled adoption: when a widget shipped in the Tesserae
    bundle on an earlier release and moved to the catalog later, the
    user has the folders on disk but no marketplace.json record.
    Uninstalling via the catalog id should still remove those folders
    (only the catalog's declared set, never anything else), so the
    Browse page's Uninstall button actually works on legacy state.

    Regression for the 0.41.0 slim-down upgrade path."""
    # Pre-seed the plugins dir with a "pre-bundled" widget.
    (marketplace._plugins_dir / "legacy_widget").mkdir()
    (marketplace._plugins_dir / "legacy_widget" / "plugin.json").write_text("{}")
    # A neighbouring folder that's not in the catalog entry, must NOT
    # be touched.
    (marketplace._plugins_dir / "unrelated").mkdir()
    (marketplace._plugins_dir / "unrelated" / "plugin.json").write_text("{}")

    # The catalog has an entry for it now.
    _seed_index(marketplace, [_make_catalog_entry(widget_id="legacy_widget")])

    assert marketplace.uninstall("legacy_widget") is True
    assert not (marketplace._plugins_dir / "legacy_widget").exists()
    # Unrelated folder untouched.
    assert (marketplace._plugins_dir / "unrelated").exists()
    # No record was ever written, no record to remove.
    assert "legacy_widget" not in marketplace.installed()


def test_uninstall_prebundled_bundle_with_all_folders_present(
    marketplace: Marketplace, url_fixture: dict[str, bytes], tmp_path: Path
) -> None:
    """Same adoption path for bundles: every catalog-declared folder
    must exist on disk before adoption fires (some-but-not-all is
    ambiguous and stays as a no-op)."""
    for f in ("github_core", "github_releases", "github_repo"):
        (marketplace._plugins_dir / f).mkdir()
        (marketplace._plugins_dir / f / "plugin.json").write_text("{}")

    _seed_index(
        marketplace,
        [
            _make_catalog_entry(
                widget_id="github",
                folders=["github_core", "github_releases", "github_repo"],
            )
        ],
    )

    assert marketplace.uninstall("github") is True
    for f in ("github_core", "github_releases", "github_repo"):
        assert not (marketplace._plugins_dir / f).exists()


def test_uninstall_prebundled_no_op_when_some_folders_missing(
    marketplace: Marketplace, url_fixture: dict[str, bytes], tmp_path: Path
) -> None:
    """If only some of the catalog's declared folders exist on disk,
    don't adopt; the on-disk state isn't clearly the pre-bundled
    install and the user should reconcile it manually."""
    # Only one of three folders present.
    (marketplace._plugins_dir / "github_core").mkdir()
    (marketplace._plugins_dir / "github_core" / "plugin.json").write_text("{}")

    _seed_index(
        marketplace,
        [
            _make_catalog_entry(
                widget_id="github",
                folders=["github_core", "github_releases", "github_repo"],
            )
        ],
    )

    assert marketplace.uninstall("github") is False
    # github_core stays put, nothing was removed.
    assert (marketplace._plugins_dir / "github_core").exists()


# ----- Persistent user-dir install (0.42.2, issue: HA upgrades wipe plugins) ---


def test_install_writes_to_user_dir_not_bundled(tmp_path: Path) -> None:
    """0.42.2: marketplace installs go to a separate user dir
    (``data/marketplace/`` in Docker / HA setups) so they survive
    image upgrades that wipe ``/app/plugins/``. The bundled dir is
    only consulted for the collision check."""
    user_dir = tmp_path / "marketplace"
    user_dir.mkdir()
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    plugin_data_root = tmp_path / "plugin-data"
    state_path = tmp_path / "core" / "marketplace.json"

    url = "https://example.invalid/widget.tar.gz"
    tarball = _make_tarball(
        folder="indie",
        manifest=_minimal_manifest(),
    )
    url_table: dict[str, bytes] = {url: tarball}

    def fake_urlopen(req, timeout=10):
        return _FakeResponse(url_table[req.full_url if hasattr(req, "full_url") else str(req)])

    import urllib.request

    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:

        class _Provider(IndexUrlProvider):
            def __call__(self) -> str:
                return "https://example.invalid/widgets.json"

        mkt = Marketplace(
            plugins_dir=user_dir,
            bundled_plugins_dir=bundled_dir,
            plugin_data_root=plugin_data_root,
            state_path=state_path,
            schema_path=REPO_ROOT / "schema" / "plugin.schema.json",
            index_schema_path=REPO_ROOT / "schema" / "marketplace.schema.json",
            index_url_provider=_Provider(),
        )

        entry = _make_catalog_entry(
            widget_id="indie",
            sha256=_sha256(tarball),
            tarball_url=url,
        )
        mkt.install(entry)
        # Lands in the user dir...
        assert (user_dir / "indie").exists()
        # ...not the bundled dir (which the image owns).
        assert not (bundled_dir / "indie").exists()
    finally:
        urllib.request.urlopen = orig


def test_install_refused_when_folder_clashes_with_bundled(tmp_path: Path) -> None:
    """A catalog entry whose folder name matches a bundled widget
    name (rare; happens if the marketplace re-publishes something
    later adopted into the bundle) is rejected with a clear message,
    no download / extract is even attempted."""
    user_dir = tmp_path / "marketplace"
    user_dir.mkdir()
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    # Pre-seed a bundled "indie" folder.
    (bundled_dir / "indie").mkdir()
    (bundled_dir / "indie" / "plugin.json").write_text("{}")

    class _Provider(IndexUrlProvider):
        def __call__(self) -> str:
            return "https://example.invalid/widgets.json"

    mkt = Marketplace(
        plugins_dir=user_dir,
        bundled_plugins_dir=bundled_dir,
        state_path=tmp_path / "state.json",
        schema_path=REPO_ROOT / "schema" / "plugin.schema.json",
        index_schema_path=REPO_ROOT / "schema" / "marketplace.schema.json",
        index_url_provider=_Provider(),
    )
    entry = _make_catalog_entry(widget_id="indie")
    with pytest.raises(InstallRefused, match="bundled widget"):
        mkt.install(entry)


# -- theme catalog kind (Phase 2) -----------------------------------


def _make_theme_tarball(
    *,
    envelope: str = "themes-pack-v0.1.0",
    themes: dict[str, dict[str, Any]],
) -> bytes:
    """Build a theme tarball in the new flat shape: per-theme
    ``<id>.json`` + ``<id>.css`` files at the envelope root.

    ``themes`` maps theme id → manifest dict; the CSS file is generated
    automatically with a valid ``[data-theme="<id>"]`` block."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for theme_id, manifest in themes.items():
            manifest_bytes = json.dumps(manifest).encode("utf-8")
            info = tarfile.TarInfo(f"{envelope}/{theme_id}.json")
            info.size = len(manifest_bytes)
            tar.addfile(info, io.BytesIO(manifest_bytes))
            css_bytes = (
                f'[data-theme="{theme_id}"]{{ --bg: #fff; --text-primary: #000; }}\n'
            ).encode()
            info = tarfile.TarInfo(f"{envelope}/{theme_id}.css")
            info.size = len(css_bytes)
            tar.addfile(info, io.BytesIO(css_bytes))
    return buf.getvalue()


def _theme_manifest(
    theme_id: str = "aqua",
    name: str = "Aqua",
    family: str = "light",
    tagline: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"id": theme_id, "name": name, "family": family}
    if tagline is not None:
        out["tagline"] = tagline
    return out


def test_install_single_theme_happy_path(
    marketplace: Marketplace, url_fixture: dict[str, bytes], tmp_path: Path
) -> None:
    """End-to-end install of a single-theme entry: download, validate,
    move into themes_dir, persist a record with kind='theme'."""
    tarball = _make_theme_tarball(themes={"aqua": _theme_manifest("aqua")})
    url = "https://example.invalid/aqua-0.1.0.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        widget_id="aqua", kind="theme", sha256=_sha256(tarball), tarball_url=url
    )

    marketplace._themes_dir = tmp_path / "themes-community"
    marketplace.install(entry)
    target = marketplace._themes_dir / "aqua"
    assert (target / "theme.json").exists()
    assert (target / "theme.css").exists()
    record = marketplace.installed()["aqua"]
    assert record.kind == "theme"
    assert record.folders == ["aqua"]


def test_install_theme_pack_lays_down_every_subfolder(
    marketplace: Marketplace, url_fixture: dict[str, bytes], tmp_path: Path
) -> None:
    """A pack with N pairs at the root installs N per-theme dirs on
    the host and records every id under ``folders``."""
    tarball = _make_theme_tarball(
        envelope="pastels-v0.1.0",
        themes={
            "pastel-rose": _theme_manifest("pastel-rose", name="Pastel Rose"),
            "pastel-mint": _theme_manifest("pastel-mint", name="Pastel Mint"),
            "pastel-lavender": _theme_manifest("pastel-lavender", name="Pastel Lavender"),
        },
    )
    url = "https://example.invalid/pastels-0.1.0.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        widget_id="pastels",
        kind="theme",
        sha256=_sha256(tarball),
        tarball_url=url,
        folders=["pastel-rose", "pastel-mint", "pastel-lavender"],
    )

    marketplace._themes_dir = tmp_path / "themes-community"
    marketplace.install(entry)
    for theme_id in ("pastel-rose", "pastel-mint", "pastel-lavender"):
        target = marketplace._themes_dir / theme_id
        assert (target / "theme.json").exists()
        assert (target / "theme.css").exists()
    record = marketplace.installed()["pastels"]
    assert record.kind == "theme"
    assert sorted(record.folders) == ["pastel-lavender", "pastel-mint", "pastel-rose"]


def test_install_theme_rejects_unpaired_files(
    marketplace: Marketplace, url_fixture: dict[str, bytes], tmp_path: Path
) -> None:
    """A lone <id>.json without a matching <id>.css is almost certainly
    a contributor typo; reject the install rather than silently
    skipping."""
    # Build a tarball with one good pair + one .json with no .css partner.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for theme_id in ("aqua",):  # complete pair
            for ext, content in (
                (".json", json.dumps(_theme_manifest(theme_id)).encode()),
                (".css", f'[data-theme="{theme_id}"]{{}}'.encode()),
            ):
                info = tarfile.TarInfo(f"env/{theme_id}{ext}")
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
        # Orphan JSON (no matching css)
        orphan = json.dumps(_theme_manifest("orphan")).encode()
        info = tarfile.TarInfo("env/orphan.json")
        info.size = len(orphan)
        tar.addfile(info, io.BytesIO(orphan))
    tarball = buf.getvalue()
    url = "https://example.invalid/orphan-0.1.0.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        widget_id="aqua", kind="theme", sha256=_sha256(tarball), tarball_url=url
    )

    marketplace._themes_dir = tmp_path / "themes-community"
    with pytest.raises(InstallRefused, match="unpaired"):
        marketplace.install(entry)


def test_install_theme_rejects_id_mismatch(
    marketplace: Marketplace, url_fixture: dict[str, bytes], tmp_path: Path
) -> None:
    """File stem MUST equal manifest id; otherwise the CSS selector
    and the catalog id would resolve to different things."""
    tarball = _make_theme_tarball(
        themes={"aqua": _theme_manifest("typoed-id", name="Aqua")}  # id ≠ stem
    )
    url = "https://example.invalid/aqua-0.1.0.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        widget_id="aqua", kind="theme", sha256=_sha256(tarball), tarball_url=url
    )

    marketplace._themes_dir = tmp_path / "themes-community"
    with pytest.raises(InstallRefused, match="doesn't match"):
        marketplace.install(entry)


def test_install_theme_rejects_pack_with_folder_mismatch(
    marketplace: Marketplace, url_fixture: dict[str, bytes], tmp_path: Path
) -> None:
    """When ``folders`` is declared on the catalog entry, the tarball's
    discovered id set must match exactly — same claim check widget
    bundles use."""
    tarball = _make_theme_tarball(
        envelope="pack",
        themes={
            "pastel-rose": _theme_manifest("pastel-rose"),
            "pastel-mint": _theme_manifest("pastel-mint"),
        },
    )
    url = "https://example.invalid/pack-0.1.0.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        widget_id="pastels",
        kind="theme",
        sha256=_sha256(tarball),
        tarball_url=url,
        folders=["pastel-rose", "pastel-mint", "pastel-lavender"],  # claims one extra
    )

    marketplace._themes_dir = tmp_path / "themes-community"
    with pytest.raises(InstallRefused, match="declares folders"):
        marketplace.install(entry)


def test_install_theme_refuses_bundled_id_clash(
    marketplace: Marketplace, url_fixture: dict[str, bytes], tmp_path: Path
) -> None:
    """A catalog theme can't shadow a bundled Spectra theme; the install
    refuses BEFORE moving anything to disk so the bundled cascade stays
    authoritative."""
    tarball = _make_theme_tarball(themes={"light": _theme_manifest("light")})
    url = "https://example.invalid/light-0.1.0.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        widget_id="light", kind="theme", sha256=_sha256(tarball), tarball_url=url
    )

    marketplace._themes_dir = tmp_path / "themes-community"
    with pytest.raises(InstallRefused, match="bundled"):
        marketplace.install(entry)


def test_uninstall_theme_pack_removes_every_subfolder(
    marketplace: Marketplace, url_fixture: dict[str, bytes], tmp_path: Path
) -> None:
    """Uninstalling a pack drops every theme dir it installed and the
    marketplace record — no half-uninstalled state."""
    tarball = _make_theme_tarball(
        envelope="pack",
        themes={
            "pastel-rose": _theme_manifest("pastel-rose"),
            "pastel-mint": _theme_manifest("pastel-mint"),
        },
    )
    url = "https://example.invalid/pack-0.1.0.tar.gz"
    url_fixture[url] = tarball
    entry = _make_catalog_entry(
        widget_id="pastels",
        kind="theme",
        sha256=_sha256(tarball),
        tarball_url=url,
        folders=["pastel-rose", "pastel-mint"],
    )

    marketplace._themes_dir = tmp_path / "themes-community"
    marketplace.install(entry)
    assert (marketplace._themes_dir / "pastel-rose").exists()
    assert (marketplace._themes_dir / "pastel-mint").exists()

    ok = marketplace.uninstall("pastels")
    assert ok is True
    assert not (marketplace._themes_dir / "pastel-rose").exists()
    assert not (marketplace._themes_dir / "pastel-mint").exists()
    assert "pastels" not in marketplace.installed()


# -- image icons -------------------------------------------------------


def _entry_with(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "sample",
        "name": "Sample",
        "description": "A sample widget for tests.",
        "author": {"name": "Test Author"},
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
    base.update(extra)
    return base


def test_icon_asset_parses_through(marketplace: Marketplace, url_fixture: dict[str, bytes]) -> None:
    """An entry wrapping a recognisable service can ship its own mark
    instead of a Phosphor glyph."""
    url_fixture["https://example.invalid/widgets.json"] = _make_index(
        [_entry_with(icon="ph-images-square", icon_asset="immich.svg")]
    )
    entry = marketplace.fetch_index()[0]
    assert entry.icon_asset == "immich.svg"
    # The glyph stays as the fallback for an unreachable catalog.
    assert entry.icon == "ph-images-square"


def test_icon_asset_defaults_to_none(
    marketplace: Marketplace, url_fixture: dict[str, bytes]
) -> None:
    url_fixture["https://example.invalid/widgets.json"] = _make_index([_entry_with()])
    assert marketplace.fetch_index()[0].icon_asset is None


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.invalid/beacon.png",
        "//evil.invalid/beacon.png",
        "../../../etc/passwd",
        "icons/../secret.png",
        "sub/dir/logo.png",
        "logo.jpg",
        "logo",
        "logo.png.exe",
        "",
        123,
        None,
    ],
)
def test_a_url_or_path_shaped_icon_asset_is_dropped(
    marketplace: Marketplace, url_fixture: dict[str, bytes], value: object
) -> None:
    """The value arrives from a remote index. Anything URL-shaped would
    let a catalog point every Browse page's <img> at a host of its
    choosing; a path segment would escape the icons directory. Both fall
    back to the glyph rather than failing the parse."""
    url_fixture["https://example.invalid/widgets.json"] = _make_index(
        [_entry_with(icon_asset=value)]
    )
    entries = marketplace.fetch_index()
    assert len(entries) == 1, "a bad icon must not drop the entry"
    assert entries[0].icon_asset is None
