"""Community widget marketplace (audit-only catalog, phase 1).

A static ``widgets.json`` index hosted in a separate catalog repo
(default: ``dmellok/tesserae-widgets``) lists community widgets, each
pinned to a tagged release tarball + sha256. The Settings → Widgets →
Browse page lets the user one-click install an entry; the install path
downloads the tarball, verifies the sha256, validates the embedded
``plugin.json`` against ``schema/plugin.schema.json``, and drops the
result into ``plugins/<id>/`` alongside the bundled widgets.

The trust model is **audit-only**: every catalog entry lands via a PR
reviewed by the catalog maintainer. There is no capability sandbox or
process isolation in this phase, see #2 / #3 for the follow-up work
those represent.

Persistence: ``data/core/marketplace.json`` records which on-disk
plugins came from the catalog (catalog id, version, sha256, source
URL, install timestamp). Uninstall refuses to touch any plugin whose
id isn't in this record, so bundled plugins are safe even if someone
fabricates a marketplace POST.

The new plugin folder is NOT loaded into the running registry: live
re-discovery would need blueprint deregistration + safe importlib
reload, which Flask doesn't support cleanly. Browse flashes a
restart-required banner instead, the existing updater re-exec path
handles the actual restart.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import jsonschema

from app.plugin_http import fetch_json

logger = logging.getLogger(__name__)

# Bounded read for catalog tarballs. Even a content-heavy widget
# (templates + bundled SVGs + fonts) shouldn't break 4 MiB. Anything
# larger is almost certainly a misconfigured release or hostile payload.
_MAX_TARBALL_BYTES: int = 4 * 1024 * 1024
_HTTP_TIMEOUT_S: float = 15.0
# How long the in-memory index cache lives. The catalog moves slowly,
# but reloading Browse mid-session shouldn't re-pull every time. Bypass
# with ``Marketplace.fetch_index(force=True)``.
_INDEX_CACHE_TTL_S: float = 5 * 60.0

# Supported index schema versions. Bumped when widgets.json grows a
# breaking shape change; older hosts refuse to load the new index
# rather than guess at semantics.
_SUPPORTED_INDEX_VERSIONS: frozenset[int] = frozenset({1})


class MarketplaceError(Exception):
    """Base class for install / uninstall / fetch failures.

    Caught + flashed by the route layer. Subclasses categorise the
    failure so the UI can show a concrete reason without leaking
    implementation details (URL, file paths, traceback)."""


class IndexUnavailable(MarketplaceError):
    """Couldn't fetch or parse the catalog index (network, JSON, schema)."""


class TarballRejected(MarketplaceError):
    """Tarball download failed validation (sha256, size cap, format)."""


class InstallRefused(MarketplaceError):
    """Install path won't proceed (id collides with a bundled plugin,
    manifest validation failed, etc.)."""


@dataclass(frozen=True)
class CatalogEntry:
    """One widget entry from the catalog index. Frozen so the route
    layer can pass it around without worrying about mutation, all the
    interesting actions go through ``Marketplace`` methods.

    ``folders`` is the informational list of plugin folders this entry
    installs. Single-widget entries can omit it; the install path
    auto-detects ``[id]``. Bundle entries (a ``_core`` admin plugin
    plus its display widgets, e.g. github_core + github_releases +
    github_actions) should list every folder so the Browse card can
    show what's about to land, and the install path verifies the
    tarball matches exactly."""

    id: str
    name: str
    description: str
    icon: str | None
    author_name: str
    author_github: str | None
    tags: list[str]
    kind: Literal["widget", "font", "theme"]
    tesserae_compat: str
    official: bool
    screenshot_sizes: list[str]
    # Optional carousel extras. When > 0, the catalog repo also ships
    # ``screenshots/<id>/extra-<n>.png`` for n in 1..count alongside
    # the primary ``lg.png``; the Browse card renders all N+1 shots as
    # an inline carousel. Defaults to 0 = single-screenshot card,
    # which is byte-identical to the pre-feature render.
    extra_screenshot_count: int
    folders: list[str] | None
    release_version: str
    release_tarball_url: str
    release_sha256: str
    source: str | None
    # Populated from a sibling ``stars.json`` published alongside the
    # catalog index (refreshed hourly by a GitHub Action in the catalog
    # repo). ``None`` means the sidecar wasn't reachable; ``0`` means
    # the widget repo has zero stars; positive int means real signal.
    # The Browse template hides the chip when this is None or 0.
    stars: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CatalogEntry:
        author = raw.get("author") or {}
        release = raw.get("release") or {}
        folders_raw = raw.get("folders")
        return cls(
            id=str(raw["id"]),
            name=str(raw["name"]),
            description=str(raw["description"]),
            icon=(str(raw["icon"]) if raw.get("icon") else None),
            author_name=str(author.get("name", "")),
            author_github=(str(author["github"]) if author.get("github") else None),
            tags=[str(t) for t in raw.get("tags", [])],
            kind=str(raw["kind"]),  # type: ignore[arg-type]
            tesserae_compat=str(raw["tesserae_compat"]),
            official=bool(raw.get("official", False)),
            screenshot_sizes=[str(s) for s in raw.get("screenshot_sizes", [])],
            extra_screenshot_count=int(raw.get("extra_screenshot_count", 0) or 0),
            folders=([str(f) for f in folders_raw] if folders_raw else None),
            release_version=str(release["version"]),
            release_tarball_url=str(release["tarball_url"]),
            release_sha256=str(release["sha256"]).lower(),
            source=(str(raw["source"]) if raw.get("source") else None),
        )


@dataclass(frozen=True)
class InstalledRecord:
    """One row of ``data/core/marketplace.json``: proof that a set of
    plugin folders under ``plugins/`` came from the catalog. The
    Browse page uses these to decide "Install" vs "Update" vs
    "Uninstall", and uninstall refuses any catalog id that isn't on
    this list.

    ``folders`` is the authoritative list of plugin folders the install
    created. For single-widget entries it's ``[catalog_id]``; for
    bundles it's every subfolder name (e.g.
    ``["github_core", "github_releases", "github_actions"]``).
    Uninstall removes every folder listed here."""

    catalog_id: str
    folders: list[str]
    version: str
    sha256: str
    source: str | None
    installed_at: str  # ISO 8601, UTC
    # ``widget`` (the historical default), ``font``, or ``theme``.
    # Stored on the record so uninstall knows whether to ``rm`` from
    # ``plugins/`` or ``themes/community/`` without re-fetching the
    # catalog. Defaults to ``widget`` for backwards compat with pre-
    # 0.47.8 records that never carried the field.
    kind: str = "widget"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> InstalledRecord:
        # Backward compat: pre-bundle records stored ``plugin_id``
        # (single folder name, always equal to catalog_id) instead of
        # a ``folders`` array. Treat the legacy shape as a one-element
        # folder list keyed off whichever id field is present.
        folders_raw = raw.get("folders")
        if not folders_raw:
            legacy = raw.get("plugin_id") or raw.get("catalog_id")
            folders_raw = [legacy] if legacy else []
        return cls(
            catalog_id=str(raw["catalog_id"]),
            folders=[str(f) for f in folders_raw],
            version=str(raw["version"]),
            sha256=str(raw["sha256"]),
            source=(str(raw["source"]) if raw.get("source") else None),
            installed_at=str(raw["installed_at"]),
            kind=str(raw.get("kind") or "widget"),
        )


@dataclass(frozen=True)
class InstallResult:
    """Returned by ``Marketplace.install`` so the caller can flash
    something specific. ``restart_required`` is always True today;
    kept on the dataclass so a future live-reload path doesn't have
    to change the contract."""

    plugin_id: str
    version: str
    restart_required: bool = True


@dataclass
class IndexSnapshot:
    """In-memory cache of the last successful index fetch. Set to the
    most recent fetch's URL + entries + epoch so cache lookups can
    invalidate when the URL setting changes mid-session."""

    url: str
    entries: list[CatalogEntry]
    fetched_at: float = field(default_factory=time.monotonic)


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _screenshots_base(index_url: str) -> str:
    """Derive the base URL where screenshots live, given the index URL.

    Convention: the catalog repo holds widgets.json at the root and
    screenshots under ``screenshots/<id>/<size>.png`` alongside it.
    Strip the last path segment from the index URL to get the root."""
    if "/" not in index_url:
        return ""
    return index_url.rsplit("/", 1)[0]


class Marketplace:
    """Audit-only catalog client. One instance per app, owned by the
    app factory and reachable via ``app.config['MARKETPLACE']``.

    Constructor wiring:
      * ``plugins_dir`` — where bundled + catalog plugins live on disk.
        Install drops new folders here; uninstall rms from here.
      * ``state_path`` — ``data/core/marketplace.json`` (created on
        first install). Records what came from the catalog.
      * ``schema_path`` — ``schema/plugin.schema.json``. Used to
        validate the embedded manifest in any downloaded tarball
        before it lands on disk.
      * ``index_schema_path`` — ``schema/marketplace.schema.json``.
        Used to validate the catalog index before we trust any entry.
      * ``settings_store`` — provides ``app.marketplace_index_url``.
        Read on every fetch so changing the setting doesn't require a
        restart.
    """

    def __init__(
        self,
        *,
        plugins_dir: Path,
        state_path: Path,
        schema_path: Path,
        index_schema_path: Path,
        index_url_provider: IndexUrlProvider,
        bundled_plugins_dir: Path | None = None,
        plugin_data_root: Path | None = None,
        themes_dir: Path | None = None,
    ) -> None:
        """``plugins_dir`` is where this Marketplace **writes** when a
        user installs from the catalog, and where it **rms from** when
        a user uninstalls. In 0.42.2+ this is the persistent user
        marketplace dir (``<data_root>/marketplace/`` in Docker / HA
        Add-on installs) so installs survive image upgrades.

        ``bundled_plugins_dir`` is the read-only directory shipped with
        the image (``/app/plugins/`` in Docker), only used for the
        install collision check, refusing to install a catalog id
        whose folder name clashes with a bundled widget. Defaults to
        ``plugins_dir`` for legacy single-dir setups + tests.

        ``plugin_data_root`` is where each plugin's data dir lives
        (e.g. ``<data_root>/plugins/picture_gallery/`` for uploaded
        photos), used by ``uninstall(delete_data=True)``. Defaults to
        the legacy ``plugins_dir.parent / "data" / "plugins"`` for
        backwards compat with single-dir test setups."""
        self._plugins_dir = plugins_dir
        self._bundled_plugins_dir = bundled_plugins_dir or plugins_dir
        self._plugin_data_root = plugin_data_root or plugins_dir.parent / "data" / "plugins"
        # Community themes land in ``<data_root>/themes/community/<id>/``
        # alongside ``themes/user.json``. Passed in so test setups can
        # point it at a tmp dir; defaults to a path next to the user
        # marketplace dir for production layouts.
        self._themes_dir = themes_dir or plugins_dir.parent / "themes" / "community"
        self._state_path = state_path
        self._schema_path = schema_path
        self._index_schema_path = index_schema_path
        self._index_url_provider = index_url_provider
        self._lock = threading.Lock()
        self._index_cache: IndexSnapshot | None = None

    # -- index -----------------------------------------------------------

    def index_url(self) -> str:
        return self._index_url_provider().strip()

    def screenshots_base(self) -> str:
        return _screenshots_base(self.index_url())

    def plugins_dir(self) -> Path:
        """The on-disk plugins directory. Exposed so the Browse route
        can detect pre-bundled widgets (folders present but no
        marketplace record) without reaching into ``_plugins_dir``."""
        return self._plugins_dir

    def fetch_index(self, *, force: bool = False) -> list[CatalogEntry]:
        """Return the parsed catalog index. Cached in memory for
        ``_INDEX_CACHE_TTL_S``; ``force=True`` skips the cache.

        Raises ``IndexUnavailable`` on network / parse / schema
        failure. The caller (route layer) catches and flashes a
        muted "Couldn't refresh, showing cached" line, then falls
        back to ``self._index_cache`` if available."""
        url = self.index_url()
        if not url:
            raise IndexUnavailable("Marketplace index URL is empty")
        cached = self._index_cache
        if (
            not force
            and cached is not None
            and cached.url == url
            and (time.monotonic() - cached.fetched_at) < _INDEX_CACHE_TTL_S
        ):
            return list(cached.entries)
        try:
            raw = fetch_json(url, timeout=_HTTP_TIMEOUT_S, retries=1)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as err:
            raise IndexUnavailable(f"Could not fetch catalog index: {err}") from err
        if not isinstance(raw, dict):
            raise IndexUnavailable("Catalog index must be a JSON object")
        version = raw.get("version")
        if not isinstance(version, int) or version not in _SUPPORTED_INDEX_VERSIONS:
            raise IndexUnavailable(
                f"Catalog index version {version!r} not supported by this host "
                f"(supports {sorted(_SUPPORTED_INDEX_VERSIONS)})"
            )
        try:
            schema = self._load_json(self._index_schema_path)
            jsonschema.validate(raw, schema)
        except jsonschema.ValidationError as err:
            raise IndexUnavailable(
                f"Catalog index failed schema validation: {err.message}"
            ) from err
        widgets_raw = raw.get("widgets") or []
        if not isinstance(widgets_raw, list):
            raise IndexUnavailable("Catalog index 'widgets' must be a list")
        entries: list[CatalogEntry] = []
        for raw_entry in widgets_raw:
            try:
                entries.append(CatalogEntry.from_dict(raw_entry))
            except (KeyError, TypeError, ValueError) as err:
                logger.warning("marketplace: dropping malformed entry: %s", err)
                continue
        stars = self._fetch_stars_sidecar(url)
        if stars:
            entries = [
                dataclasses.replace(e, stars=stars.get(e.id)) if e.id in stars else e
                for e in entries
            ]
        with self._lock:
            self._index_cache = IndexSnapshot(url=url, entries=list(entries))
        return entries

    def _fetch_stars_sidecar(self, index_url: str) -> dict[str, int]:
        """Best-effort fetch of ``stars.json`` next to the catalog
        index. Returns ``{widget_id: count}`` or empty dict on any
        failure. The sidecar is published hourly by a GitHub Action
        in the catalog repo; a 404 / network blip / parse error is
        not fatal — the catalog renders without star counts and the
        Browse template hides the chip. Never raises."""
        stars_url = _screenshots_base(index_url) + "/stars.json"
        try:
            raw = fetch_json(stars_url, timeout=_HTTP_TIMEOUT_S, retries=0)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            logger.debug("marketplace: stars.json was not a JSON object")
            return {}
        stars_block = raw.get("stars")
        if not isinstance(stars_block, dict):
            return {}
        out: dict[str, int] = {}
        for k, v in stars_block.items():
            if isinstance(k, str) and isinstance(v, int) and v >= 0:
                out[k] = v
        return out

    def cached_index(self) -> list[CatalogEntry] | None:
        """Best-effort fallback for the Browse page when ``fetch_index``
        fails (network blip). Returns the last successful snapshot for
        the current URL, or None if we've never seen it."""
        url = self.index_url()
        cached = self._index_cache
        if cached is None or cached.url != url:
            return None
        return list(cached.entries)

    def _find_catalog_entry(self, catalog_id: str) -> CatalogEntry | None:
        """Look up a catalog entry by id, refreshing from upstream if
        the in-memory cache hasn't been populated yet. Used by
        ``uninstall`` to recover the declared-folders list for the
        pre-bundled-adoption path."""
        cached = self.cached_index()
        if cached is None:
            try:
                cached = self.fetch_index()
            except IndexUnavailable:
                return None
        return next((e for e in cached if e.id == catalog_id), None)

    # -- installed state -------------------------------------------------

    def installed(self) -> dict[str, InstalledRecord]:
        """Read ``marketplace.json`` and return ``{plugin_id: record}``.
        Missing / corrupt file = empty dict (the next install rewrites
        it). Errors here never raise: the Browse page must render even
        when state is wonky so the user can recover."""
        if not self._state_path.exists():
            return {}
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("marketplace: failed to read %s, treating as empty", self._state_path)
            return {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, InstalledRecord] = {}
        for plugin_id, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            try:
                out[str(plugin_id)] = InstalledRecord.from_json(entry)
            except (KeyError, TypeError, ValueError):
                logger.warning("marketplace: dropping malformed record for %s", plugin_id)
                continue
        return out

    # -- install ---------------------------------------------------------

    def install(self, entry: CatalogEntry) -> InstallResult:
        """Download + validate + install one catalog entry.

        Steps (in this order, so a failure leaves no partial state on
        disk):
          1. Refuse if compat doesn't match the host's major version.
          2. Download the tarball with a size cap.
          3. Verify sha256 matches the catalog entry.
          4. Extract to a temp dir using ``tarfile``'s ``data`` filter
             (rejects absolute paths + ``..`` traversal + suid bits).
          5. Detect layout: single-widget (one folder with
             plugin.json) OR bundle (a containing folder whose
             children are each plugin folders).
          6. If the catalog entry declares ``folders``, the detected
             set must match exactly, this is the catalog's claim
             check.
          7. Validate each folder's ``plugin.json`` against
             ``schema/plugin.schema.json``. For single-widget entries
             also verify embedded kind/version match the catalog
             claim; bundles skip those checks because each subfolder
             has its own kind/version.
          8. Refuse if any of the destination folders already exist
             on disk and aren't owned by this catalog entry.
          9. Move each validated folder to ``plugins/<folder>/``; on
             reinstall over the same catalog entry, replace.
          10. Persist a new ``InstalledRecord`` to
             ``marketplace.json`` listing every folder that landed.

        Raises ``InstallRefused`` or ``TarballRejected`` on any of the
        above. The route layer catches both and flashes the message.
        """
        from app.plugin_loader import HOST_MAJOR_VERSION, _compat_ok

        if not _compat_ok(entry.tesserae_compat, HOST_MAJOR_VERSION):
            raise InstallRefused(
                f"Widget compat {entry.tesserae_compat!r} doesn't match host "
                f"major version {HOST_MAJOR_VERSION}."
            )

        # Themes are a different shape from widgets / fonts (two flat
        # files vs a plugin folder). Hand off to a dedicated installer
        # so the rest of this method stays focused on the plugin path.
        if entry.kind == "theme":
            return self._install_theme(entry)

        # Pre-flight collision check on whatever folders the catalog
        # declared (single-widget: just entry.id). Saves the user from
        # a download + extract round-trip when we already know the
        # install will fail. The post-extract pass below catches the
        # auto-detected bundle case + any folders the catalog didn't
        # declare.
        early_installed = self.installed()
        early_record = early_installed.get(entry.id)
        early_owned: set[str] = set(early_record.folders) if early_record is not None else set()
        candidates = list(entry.folders) if entry.folders else [entry.id]
        for folder_id in candidates:
            # Refuse if the folder name clashes with a bundled widget
            # in the image (read-only, can't be touched). This catches
            # the case where the catalog re-publishes a widget that
            # later became bundled.
            if (
                self._bundled_plugins_dir != self._plugins_dir
                and (self._bundled_plugins_dir / folder_id).exists()
            ):
                raise InstallRefused(
                    f"Plugin folder {folder_id!r} is already shipped with "
                    "Tesserae as a bundled widget; the marketplace entry "
                    "for it is now obsolete. Skip this entry."
                )
            target = self._plugins_dir / folder_id
            if not target.exists() or folder_id in early_owned:
                continue
            other = next(
                (rec.catalog_id for rec in early_installed.values() if folder_id in rec.folders),
                None,
            )
            if other is not None:
                raise InstallRefused(
                    f"Plugin folder {folder_id!r} is already installed by "
                    f"a different catalog entry ({other!r})."
                )
            raise InstallRefused(
                f"A plugin folder named {folder_id!r} already exists on disk and "
                "isn't tracked by the marketplace, refusing to overwrite a bundled "
                "or hand-installed plugin."
            )

        with tempfile.TemporaryDirectory(prefix="tesserae-mkt-") as tmp:
            tmp_dir = Path(tmp)
            tar_path = tmp_dir / "release.tar.gz"
            try:
                self._download(entry.release_tarball_url, tar_path)
            except (urllib.error.URLError, TimeoutError, OSError) as err:
                raise TarballRejected(f"Could not download release tarball: {err}") from err

            actual_sha = self._sha256(tar_path)
            if actual_sha != entry.release_sha256:
                raise TarballRejected(
                    f"Tarball sha256 mismatch (expected {entry.release_sha256[:12]}…, "
                    f"got {actual_sha[:12]}…)"
                )

            extract_dir = tmp_dir / "unpacked"
            extract_dir.mkdir()
            try:
                with tarfile.open(tar_path, "r:*") as tar:
                    # tarfile.data_filter (PEP 706, available 3.11+)
                    # strips suid/sgid, rejects absolute paths + ".."
                    # traversal, and refuses device files. Plugins are
                    # just data files anyway, this is the right filter.
                    tar.extractall(extract_dir, filter="data")
            except (tarfile.TarError, OSError) as err:
                raise TarballRejected(f"Could not extract release tarball: {err}") from err

            layout = self._detect_layout(extract_dir, entry)
            is_bundle = len(layout) > 1 or entry.id not in layout

            # Catalog-declared folders must match the tarball exactly.
            # When folders is omitted we trust the auto-detected set.
            if entry.folders is not None:
                declared = set(entry.folders)
                actual = set(layout.keys())
                if declared != actual:
                    raise InstallRefused(
                        f"Catalog declares folders {sorted(declared)!r} but tarball "
                        f"contains {sorted(actual)!r}."
                    )

            for folder_id, folder_path in layout.items():
                self._validate_embedded_manifest(
                    folder_path,
                    entry,
                    folder_id=folder_id,
                    is_bundle=is_bundle,
                )

            installed = self.installed()
            existing_record = installed.get(entry.id)
            owned_folders: set[str] = (
                set(existing_record.folders) if existing_record is not None else set()
            )
            # Collision guard: every destination folder must either be
            # absent on disk or already owned by THIS catalog entry
            # (the reinstall / upgrade path). A folder owned by a
            # different catalog entry, or one that's bundled / hand-
            # installed, blocks the install.
            for folder_id in layout:
                target = self._plugins_dir / folder_id
                if not target.exists():
                    continue
                if folder_id in owned_folders:
                    continue
                conflicting_owner = next(
                    (rec.catalog_id for rec in installed.values() if folder_id in rec.folders),
                    None,
                )
                if conflicting_owner is not None:
                    raise InstallRefused(
                        f"Plugin folder {folder_id!r} is already installed by "
                        f"a different catalog entry ({conflicting_owner!r})."
                    )
                raise InstallRefused(
                    f"A plugin folder named {folder_id!r} already exists on disk "
                    "and isn't tracked by the marketplace, refusing to overwrite "
                    "a bundled or hand-installed plugin."
                )

            # Move each folder into place. Backups + rollback are
            # per-folder so a partial failure (rare; only on disk
            # errors mid-move) doesn't leave a half-installed bundle.
            moved: list[tuple[Path, Path | None]] = []
            try:
                for folder_id, src_path in layout.items():
                    target = self._plugins_dir / folder_id
                    backup: Path | None = None
                    if target.exists():
                        backup = target.with_name(target.name + ".old")
                        if backup.exists():
                            shutil.rmtree(backup)
                        target.rename(backup)
                    shutil.move(str(src_path), str(target))
                    moved.append((target, backup))
            except OSError as err:
                # Roll back: restore each backed-up dir, drop any
                # newly-moved trees so the install is atomic-ish.
                for target, backup in moved:
                    if target.exists():
                        shutil.rmtree(target, ignore_errors=True)
                    if backup is not None and backup.exists():
                        backup.rename(target)
                raise InstallRefused(f"Could not move plugin into place: {err}") from err
            # Success: drop the backups.
            for _target, backup in moved:
                if backup is not None and backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)

        record = InstalledRecord(
            catalog_id=entry.id,
            folders=sorted(layout.keys()),
            version=entry.release_version,
            sha256=entry.release_sha256,
            source=entry.source,
            installed_at=_utcnow_iso(),
            kind=entry.kind,  # "widget" or "font"
        )
        self._persist_record(record)
        logger.info(
            "marketplace: installed %s v%s (folders=%s, sha256=%s…)",
            entry.id,
            entry.release_version,
            record.folders,
            entry.release_sha256[:12],
        )
        return InstallResult(plugin_id=entry.id, version=entry.release_version)

    # -- theme install ---------------------------------------------------

    def _install_theme(self, entry: CatalogEntry) -> InstallResult:
        """Install one ``kind: theme`` entry.

        Two tarball shapes are accepted, mirroring widget bundles:

        * **Single theme**: ``theme.json`` + ``theme.css`` at the root.
          ``theme.json.id`` must equal ``entry.id`` so the catalog
          identifier and the ``[data-theme="<id>"]`` selector line up.
          One theme installs to ``themes/community/<entry.id>/``.

        * **Theme pack**: per-theme subfolders each containing
          ``theme.json`` + ``theme.css``. Each subfolder's
          ``theme.json.id`` must equal the subfolder name. When the
          catalog entry declares ``folders``, the set must match the
          subfolders exactly. ``entry.id`` is the pack identifier (the
          marketplace record key); the installed theme ids are the
          subfolder names. Each lands in ``themes/community/<theme_id>/``.

        The GitHub-style ``<repo>-<sha>/`` envelope is unwrapped
        automatically before either layout check."""
        from app.state.theme_registry import BUNDLED_THEMES

        bundled_ids = {t.id for t in BUNDLED_THEMES}

        with tempfile.TemporaryDirectory(prefix="tesserae-mkt-theme-") as tmp:
            tmp_dir = Path(tmp)
            tar_path = tmp_dir / "release.tar.gz"
            try:
                self._download(entry.release_tarball_url, tar_path)
            except (urllib.error.URLError, TimeoutError, OSError) as err:
                raise TarballRejected(f"Could not download theme tarball: {err}") from err

            actual_sha = self._sha256(tar_path)
            if actual_sha != entry.release_sha256:
                raise TarballRejected(
                    f"Tarball sha256 mismatch (expected {entry.release_sha256[:12]}…, "
                    f"got {actual_sha[:12]}…)"
                )

            extract_dir = tmp_dir / "unpacked"
            extract_dir.mkdir()
            try:
                with tarfile.open(tar_path, "r:*") as tar:
                    tar.extractall(extract_dir, filter="data")
            except (tarfile.TarError, OSError) as err:
                raise TarballRejected(f"Could not extract theme tarball: {err}") from err

            root = self._unwrap_envelope(extract_dir)
            theme_layout = self._detect_theme_layout(root, entry)

            # Validate each theme. No mutation before this loop fully
            # passes — a single bad theme in a pack rejects the whole
            # install rather than leaving a half-installed pack on disk.
            for theme_id, manifest_path, css_path in theme_layout:
                if theme_id in bundled_ids:
                    raise InstallRefused(
                        f"Theme id {theme_id!r} clashes with a bundled "
                        "Tesserae theme; the catalog entry can't shadow it."
                    )
                self._validate_embedded_theme(manifest_path, css_path, theme_id=theme_id)

            # Collision guard. Any destination dir that already exists
            # must be owned by THIS catalog entry (the reinstall path),
            # not by another catalog entry or a hand-installed folder.
            installed = self.installed()
            existing_record = installed.get(entry.id)
            owned = set(existing_record.folders) if existing_record is not None else set()
            for theme_id, _, _ in theme_layout:
                target = self._themes_dir / theme_id
                if not target.exists():
                    continue
                if theme_id in owned:
                    continue
                other_owner = next(
                    (
                        rec.catalog_id
                        for rec in installed.values()
                        if rec.kind == "theme" and theme_id in rec.folders
                    ),
                    None,
                )
                if other_owner is not None:
                    raise InstallRefused(
                        f"Theme {theme_id!r} is already installed by a different "
                        f"catalog entry ({other_owner!r})."
                    )
                raise InstallRefused(
                    f"A theme dir at {target} already exists but isn't tracked "
                    "by the marketplace, refusing to overwrite it."
                )

            # Stage each theme into its canonical host-side dir layout
            # (themes/community/<id>/theme.json + theme.css). The tarball
            # is flat by convention; the host-side keeps a per-theme
            # subfolder so the store reads a uniform shape regardless
            # of how many themes the tarball shipped.
            self._themes_dir.mkdir(parents=True, exist_ok=True)
            moved: list[tuple[Path, Path | None]] = []
            try:
                for theme_id, manifest_path, css_path in theme_layout:
                    target = self._themes_dir / theme_id
                    backup: Path | None = None
                    if target.exists():
                        backup = target.with_name(target.name + ".old")
                        if backup.exists():
                            shutil.rmtree(backup)
                        target.rename(backup)
                    target.mkdir()
                    shutil.move(str(manifest_path), str(target / "theme.json"))
                    shutil.move(str(css_path), str(target / "theme.css"))
                    moved.append((target, backup))
            except OSError as err:
                for target, backup in moved:
                    if target.exists():
                        shutil.rmtree(target, ignore_errors=True)
                    if backup is not None and backup.exists():
                        backup.rename(target)
                raise InstallRefused(f"Could not move theme into place: {err}") from err
            for _target, backup in moved:
                if backup is not None and backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)

        installed_theme_ids = sorted(tid for tid, _, _ in theme_layout)
        record = InstalledRecord(
            catalog_id=entry.id,
            folders=installed_theme_ids,
            version=entry.release_version,
            sha256=entry.release_sha256,
            source=entry.source,
            installed_at=_utcnow_iso(),
            kind="theme",
        )
        self._persist_record(record)
        logger.info(
            "marketplace: installed theme%s %s v%s (themes=%s, sha256=%s…)",
            "" if len(installed_theme_ids) == 1 else " pack",
            entry.id,
            entry.release_version,
            installed_theme_ids,
            entry.release_sha256[:12],
        )
        return InstallResult(plugin_id=entry.id, version=entry.release_version)

    def _detect_theme_layout(self, root: Path, entry: CatalogEntry) -> list[tuple[str, Path, Path]]:
        """Return a sorted ``[(theme_id, manifest_path, css_path), …]``
        list for the tarball at ``root``.

        Convention (single + pack, same rule): each theme is two files
        at the envelope root, named by id — ``<id>.json`` + ``<id>.css``.
        A single-theme tarball just has one pair; a pack has N.

        ``entry.folders`` (when declared) must match the discovered id
        set exactly, mirroring the widget-bundle claim check. For a
        single-theme entry, the discovered id must equal ``entry.id``.

        Unpaired files (a ``.json`` without a matching ``.css`` or vice
        versa) reject the install rather than skip silently, since they
        almost certainly indicate a contributor mistake."""
        manifests = {p.stem: p for p in root.iterdir() if p.is_file() and p.suffix == ".json"}
        css_files = {p.stem: p for p in root.iterdir() if p.is_file() and p.suffix == ".css"}
        all_ids = set(manifests) | set(css_files)
        unpaired = sorted(tid for tid in all_ids if tid not in manifests or tid not in css_files)
        if unpaired:
            missing = [f"{tid}.{'css' if tid in manifests else 'json'}" for tid in unpaired]
            raise InstallRefused(
                f"Theme tarball has unpaired files (missing: {missing!r}). "
                "Each theme must be one <id>.json + one <id>.css at the root."
            )
        ids = sorted(manifests)
        if not ids:
            raise InstallRefused(
                "Theme tarball must contain one or more <id>.json + <id>.css pairs at the root."
            )
        if entry.folders is not None:
            declared = set(entry.folders)
            actual = set(ids)
            if declared != actual:
                raise InstallRefused(
                    f"Theme pack declares folders {sorted(declared)!r} but tarball "
                    f"contains {sorted(actual)!r}."
                )
        elif len(ids) == 1 and ids[0] != entry.id:
            # Single-theme entries must match the catalog id so the
            # browse card and install record stay aligned. Multi-theme
            # tarballs without an explicit ``folders`` declaration are
            # accepted as packs keyed off whatever ids the tarball
            # carries.
            raise InstallRefused(
                f"Single-theme tarball declares id {ids[0]!r}, but the catalog "
                f"entry id is {entry.id!r}. Add a 'folders' claim to ship a pack."
            )
        return [(tid, manifests[tid], css_files[tid]) for tid in ids]

    def _validate_embedded_theme(
        self, manifest_path: Path, css_path: Path, *, theme_id: str
    ) -> None:
        """Validate one theme's pair against the contract: manifest
        parses + declares the right id + carries a usable name; CSS
        targets the declared id. Raises ``InstallRefused`` with a
        contributor-friendly message on any failure."""
        try:
            manifest_raw = manifest_path.read_text(encoding="utf-8")
            manifest = json.loads(manifest_raw)
        except (OSError, json.JSONDecodeError) as err:
            raise InstallRefused(f"{manifest_path.name} is not readable JSON: {err}") from err
        if not isinstance(manifest, dict):
            raise InstallRefused(f"{manifest_path.name} must be a JSON object.")
        declared_id = manifest.get("id")
        if declared_id != theme_id:
            raise InstallRefused(
                f"{manifest_path.name} id {declared_id!r} doesn't match the file stem {theme_id!r}."
            )
        name = manifest.get("name")
        if not isinstance(name, str) or not name.strip():
            raise InstallRefused(f"{manifest_path.name} must declare a non-empty 'name'.")
        try:
            css_text = css_path.read_text(encoding="utf-8")
        except OSError as err:
            raise InstallRefused(f"{css_path.name} is not readable: {err}") from err
        if f'[data-theme="{theme_id}"]' not in css_text:
            raise InstallRefused(f"{css_path.name} must include a [data-theme={theme_id!r}] block.")

    def _unwrap_envelope(self, extract_dir: Path) -> Path:
        """Most archives have a single top-level folder (``<repo>-<sha>/``
        for GitHub tarballs). Return that folder if it exists,
        otherwise return ``extract_dir`` itself."""
        children = [p for p in extract_dir.iterdir() if not p.name.startswith(".")]
        if len(children) == 1 and children[0].is_dir():
            return children[0]
        return extract_dir

    # -- uninstall -------------------------------------------------------

    def uninstall(self, catalog_id: str, *, delete_data: bool = False) -> bool:
        """Remove a marketplace-installed catalog entry from disk + state.

        Returns True if folders were removed; False if the id wasn't
        tracked and no matching folders exist on disk either.

        Two paths:

        * **Marketplace record present**: remove every folder the
          install record lists. Standard uninstall.
        * **No record but the catalog entry's declared folders all
          exist on disk**: pre-bundled adoption path. A widget that
          shipped in the Tesserae bundle on a previous release but
          moved to the catalog later leaves its folders on disk after
          upgrade; the Browse page surfaces it as installed and the
          user can Uninstall it here. Only the folders the catalog
          *currently* declares are touched, never arbitrary plugin
          folders the catalog doesn't know about.

        For bundle entries (either path) this removes every declared
        folder (so a github bundle takes its `_core` + every display
        widget along with it). ``delete_data=False`` (default) leaves
        every folder's ``data/plugins/<folder>/`` in place so a
        reinstall finds the user's settings + caches intact;
        ``True`` also rms the data dirs.
        """
        installed = self.installed()
        record = installed.get(catalog_id)

        # Theme records uninstall from ``themes/community/`` instead of
        # ``plugins/``. Same record-tracked semantic, different on-disk
        # location. ``delete_data`` is a no-op for themes; they have no
        # per-theme data dir.
        if record is not None and record.kind == "theme":
            for theme_id in record.folders:
                target_dir = self._themes_dir / theme_id
                if target_dir.exists():
                    try:
                        shutil.rmtree(target_dir)
                    except OSError as err:
                        logger.warning(
                            "marketplace: could not remove theme %s: %s, dropping record anyway",
                            target_dir,
                            err,
                        )
            del installed[catalog_id]
            self._write_state(installed)
            logger.info("marketplace: uninstalled theme %s", catalog_id)
            return True

        if record is not None:
            folders = list(record.folders)
        else:
            # No marketplace record. Fall back to the catalog entry's
            # declared folders, only if ALL of them exist on disk
            # (partial / unrelated states are too ambiguous to act on).
            entry = self._find_catalog_entry(catalog_id)
            if entry is None:
                return False
            candidate_folders = list(entry.folders) if entry.folders else [entry.id]
            if not candidate_folders:
                return False
            if not all((self._plugins_dir / f).exists() for f in candidate_folders):
                return False
            folders = candidate_folders
            logger.info(
                "marketplace: adopting pre-bundled %s for uninstall (folders=%s)",
                catalog_id,
                folders,
            )

        for folder_id in folders:
            target_dir = self._plugins_dir / folder_id
            if target_dir.exists():
                try:
                    shutil.rmtree(target_dir)
                except OSError as err:
                    logger.warning(
                        "marketplace: could not remove %s: %s, dropping record anyway",
                        target_dir,
                        err,
                    )
            if delete_data:
                # Per-plugin data dir; wired in from app_factory in
                # 0.42.2+ to decouple from the plugins-dir location
                # (the marketplace now writes code to
                # ``<data_root>/marketplace/`` while data still lives
                # at ``<data_root>/plugins/<id>/``).
                data_root = self._plugin_data_root / folder_id
                if data_root.exists():
                    try:
                        shutil.rmtree(data_root)
                    except OSError as err:
                        logger.warning(
                            "marketplace: could not remove data dir %s: %s", data_root, err
                        )
        # Drop the record last so a partial failure above still leaves
        # the user with a "stuck" entry they can retry, instead of an
        # untracked plugin folder. No-op when adopting a pre-bundled
        # widget (the catalog id was never in marketplace.json to begin
        # with).
        if record is not None:
            del installed[catalog_id]
            self._write_state(installed)
        logger.info(
            "marketplace: uninstalled %s (folders=%s, delete_data=%s)",
            catalog_id,
            folders,
            delete_data,
        )
        return True

    # -- internals -------------------------------------------------------

    def _download(self, url: str, dest: Path) -> None:
        """Stream a remote URL into ``dest`` with a hard size cap.

        Raises ``TarballRejected`` if the response would exceed the
        cap; the caller catches that. Other errors propagate to the
        ``urllib.error.URLError`` / ``OSError`` branch of the install
        loop so they get categorised consistently."""
        if not (url.startswith("http://") or url.startswith("https://")):
            raise TarballRejected("Tarball URL must be http(s)://")
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "tesserae/marketplace"},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            written = 0
            with dest.open("wb") as fh:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > _MAX_TARBALL_BYTES:
                        raise TarballRejected(
                            f"Tarball exceeds {_MAX_TARBALL_BYTES // (1024 * 1024)} MiB cap"
                        )
                    fh.write(chunk)

    def _sha256(self, path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(64 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _detect_layout(self, extract_dir: Path, entry: CatalogEntry) -> dict[str, Path]:
        """Return ``{folder_id: Path}`` for either a single-widget or
        a bundle tarball.

        Layouts auto-detected:
          * **Single widget**: top-level folder contains ``plugin.json``
            directly. Returns ``{entry.id: <that folder>}`` regardless
            of the on-disk folder name (we rename on move).
          * **Bundle**: top-level folder's *children* are themselves
            plugin folders, each with ``plugin.json``. Returns
            ``{child_name: <child path>, ...}``. Useful for widget
            families that pair a ``_core`` admin plugin with display
            widgets (github_core + github_releases + …) so a single
            install ships the whole family.

        First unwraps the GitHub-style ``<repo>-<sha>/`` envelope
        (single top-level folder containing the real layout).
        Anything that fits neither shape raises ``InstallRefused``."""
        root = extract_dir
        # Unwrap the GitHub source-tarball envelope when present:
        # one top-level folder + nothing else at the root.
        if not (root / "plugin.json").exists():
            top_entries = list(root.iterdir())
            top_dirs = [p for p in top_entries if p.is_dir()]
            if len(top_entries) == 1 and len(top_dirs) == 1:
                root = top_dirs[0]

        if (root / "plugin.json").exists():
            return {entry.id: root}

        # Bundle: every subfolder must contain a plugin.json. Anything
        # else (a stray file, a subfolder without plugin.json) is a
        # malformed bundle; better to reject than partially install.
        subfolders = sorted(p for p in root.iterdir() if p.is_dir())
        if not subfolders:
            raise InstallRefused(
                f"Could not find plugin.json in the {entry.id!r} tarball (looked at "
                "the extract root, a single wrapping folder, and any subfolders)."
            )
        layout: dict[str, Path] = {}
        for sub in subfolders:
            if not (sub / "plugin.json").exists():
                raise InstallRefused(
                    f"Bundle subfolder {sub.name!r} is missing plugin.json. "
                    "Every direct child of the tarball must be a valid plugin folder."
                )
            layout[sub.name] = sub
        return layout

    def _validate_embedded_manifest(
        self,
        plugin_root: Path,
        entry: CatalogEntry,
        *,
        folder_id: str,
        is_bundle: bool,
    ) -> None:
        """Validate one folder's manifest against
        ``schema/plugin.schema.json``.

        For single-widget entries (``is_bundle=False``) additionally
        verify embedded kind + version match the catalog claim — belt
        + braces against the case where a tarball drifts after merge
        (republished tag, etc.). For bundle entries each subfolder
        has its own kind + version independently, so we drop those
        cross-checks (the sha256 verify catches tarball drift
        regardless)."""
        manifest_path = plugin_root / "plugin.json"
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise InstallRefused(
                f"Embedded plugin.json in {folder_id!r} is invalid: {err}"
            ) from err
        if not isinstance(raw, dict):
            raise InstallRefused(f"Embedded plugin.json in {folder_id!r} must be a JSON object")
        try:
            schema = self._load_json(self._schema_path)
            jsonschema.validate(raw, schema)
        except jsonschema.ValidationError as err:
            field_path = ".".join(str(p) for p in err.absolute_path) or "<root>"
            raise InstallRefused(
                f"Embedded manifest in {folder_id!r} failed schema validation "
                f"[{field_path}]: {err.message}"
            ) from err
        if is_bundle:
            return
        embedded_kind = str(raw.get("kind", ""))
        if embedded_kind != entry.kind:
            raise InstallRefused(
                f"Catalog says kind={entry.kind!r} but tarball declares kind={embedded_kind!r}."
            )
        embedded_version = str(raw.get("version", ""))
        if embedded_version != entry.release_version:
            raise InstallRefused(
                f"Catalog says version={entry.release_version!r} but tarball "
                f"declares version={embedded_version!r}."
            )

    def _persist_record(self, record: InstalledRecord) -> None:
        existing = self.installed()
        existing[record.catalog_id] = record
        self._write_state(existing)

    def _write_state(self, state: dict[str, InstalledRecord]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {pid: rec.to_json() for pid, rec in state.items()}
        tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._state_path)

    def _load_json(self, path: Path) -> dict[str, Any]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(raw, dict)
        return raw


# Type alias the wiring layer uses; defined here so app_factory doesn't
# have to import a callable signature from elsewhere.
class IndexUrlProvider:
    """Callable that returns the current marketplace index URL.

    Wrapped as a class rather than ``Callable[[], str]`` so mypy
    --strict accepts it as a stored attribute on Marketplace without
    a ``Callable`` import dance. The app factory passes a tiny
    lambda-equivalent that reads the settings store."""

    def __call__(self) -> str:
        raise NotImplementedError
