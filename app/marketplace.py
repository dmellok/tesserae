"""Community widget marketplace (audit-only catalog, phase 1).

A static ``widgets.json`` index hosted in a separate catalog repo
(default: ``dmellok/tesserae-widgets``) lists community widgets, each
pinned to a tagged release tarball + sha256. The Settings → Plugins →
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
    interesting actions go through ``Marketplace`` methods."""

    id: str
    name: str
    description: str
    icon: str | None
    author_name: str
    author_github: str | None
    tags: list[str]
    kind: Literal["widget", "font"]
    tesserae_compat: str
    official: bool
    screenshot_sizes: list[str]
    release_version: str
    release_tarball_url: str
    release_sha256: str
    source: str | None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CatalogEntry:
        author = raw.get("author") or {}
        release = raw.get("release") or {}
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
            release_version=str(release["version"]),
            release_tarball_url=str(release["tarball_url"]),
            release_sha256=str(release["sha256"]).lower(),
            source=(str(raw["source"]) if raw.get("source") else None),
        )


@dataclass(frozen=True)
class InstalledRecord:
    """One row of ``data/core/marketplace.json``: proof that a plugin
    folder under ``plugins/`` came from the catalog. The Browse page
    uses these to decide "Install" vs "Update" vs "Uninstall", and
    uninstall refuses any id that isn't on this list."""

    catalog_id: str
    plugin_id: str
    version: str
    sha256: str
    source: str | None
    installed_at: str  # ISO 8601, UTC

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> InstalledRecord:
        return cls(
            catalog_id=str(raw["catalog_id"]),
            plugin_id=str(raw["plugin_id"]),
            version=str(raw["version"]),
            sha256=str(raw["sha256"]),
            source=(str(raw["source"]) if raw.get("source") else None),
            installed_at=str(raw["installed_at"]),
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
    ) -> None:
        self._plugins_dir = plugins_dir
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
        with self._lock:
            self._index_cache = IndexSnapshot(url=url, entries=list(entries))
        return entries

    def cached_index(self) -> list[CatalogEntry] | None:
        """Best-effort fallback for the Browse page when ``fetch_index``
        fails (network blip). Returns the last successful snapshot for
        the current URL, or None if we've never seen it."""
        url = self.index_url()
        cached = self._index_cache
        if cached is None or cached.url != url:
            return None
        return list(cached.entries)

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
          1. Refuse if a bundled plugin already owns this id.
          2. Refuse if compat doesn't match the host's major version.
          3. Download the tarball with a size cap.
          4. Verify sha256 matches the catalog entry.
          5. Extract to a temp dir using ``tarfile``'s ``data`` filter
             (rejects absolute paths + ``..`` traversal + suid bits).
          6. Locate the single top-level folder + validate its
             ``plugin.json`` against ``schema/plugin.schema.json``.
          7. Refuse if the embedded manifest's name/id don't match the
             catalog claim.
          8. Move the validated tree to ``plugins/<id>/``; on existing
             marketplace install, replace it.
          9. Persist a new ``InstalledRecord`` to ``marketplace.json``.

        Raises ``InstallRefused`` or ``TarballRejected`` on any of the
        above. The route layer catches both and flashes the message.
        """
        from app.plugin_loader import HOST_MAJOR_VERSION, _compat_ok

        installed = self.installed()
        target_dir = self._plugins_dir / entry.id

        if target_dir.exists() and entry.id not in installed:
            raise InstallRefused(
                f"A plugin folder named {entry.id!r} already exists on disk and "
                "isn't tracked by the marketplace, refusing to overwrite a bundled "
                "or hand-installed plugin."
            )

        if not _compat_ok(entry.tesserae_compat, HOST_MAJOR_VERSION):
            raise InstallRefused(
                f"Widget compat {entry.tesserae_compat!r} doesn't match host "
                f"major version {HOST_MAJOR_VERSION}."
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

            plugin_root = self._locate_plugin_root(extract_dir, entry.id)
            self._validate_embedded_manifest(plugin_root, entry)

            # Atomic-ish swap: rename old into a backup, move new in,
            # remove backup on success. If anything between the two
            # moves fails, roll back to the backup.
            backup_dir: Path | None = None
            if target_dir.exists():
                backup_dir = target_dir.with_name(target_dir.name + ".old")
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                target_dir.rename(backup_dir)
            try:
                shutil.move(str(plugin_root), str(target_dir))
            except OSError as err:
                if backup_dir is not None and backup_dir.exists():
                    backup_dir.rename(target_dir)
                raise InstallRefused(f"Could not move plugin into place: {err}") from err
            if backup_dir is not None and backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)

        record = InstalledRecord(
            catalog_id=entry.id,
            plugin_id=entry.id,
            version=entry.release_version,
            sha256=entry.release_sha256,
            source=entry.source,
            installed_at=_utcnow_iso(),
        )
        self._persist_record(record)
        logger.info(
            "marketplace: installed %s v%s (sha256=%s…)",
            entry.id,
            entry.release_version,
            entry.release_sha256[:12],
        )
        return InstallResult(plugin_id=entry.id, version=entry.release_version)

    # -- uninstall -------------------------------------------------------

    def uninstall(self, plugin_id: str, *, delete_data: bool = False) -> bool:
        """Remove a marketplace-installed plugin from disk + state.

        Refuses any id not in ``marketplace.json``, this is the only
        thing standing between the Browse page and a bundled-plugin
        rm. Returns True if a plugin was removed; False if the id
        wasn't tracked (caller can flash "not installed by the
        marketplace").

        ``delete_data=False`` (default) leaves ``data/plugins/<id>/``
        in place so a reinstall finds the user's settings + caches
        intact. ``True`` also rms the data dir, the Browse uninstall
        form has a tick for this.
        """
        installed = self.installed()
        if plugin_id not in installed:
            return False
        target_dir = self._plugins_dir / plugin_id
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
            # data_root for plugins is conventionally data/plugins/<id>.
            # The path here mirrors what plugin_loader.discover passes;
            # we reconstruct rather than thread it through the
            # constructor to keep the wiring small.
            data_root = self._plugins_dir.parent / "data" / "plugins" / plugin_id
            if data_root.exists():
                try:
                    shutil.rmtree(data_root)
                except OSError as err:
                    logger.warning("marketplace: could not remove data dir %s: %s", data_root, err)
        # Drop the record last so a partial failure above still leaves
        # the user with a "stuck" entry they can retry, instead of an
        # untracked plugin folder.
        del installed[plugin_id]
        self._write_state(installed)
        logger.info("marketplace: uninstalled %s (delete_data=%s)", plugin_id, delete_data)
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

    def _locate_plugin_root(self, extract_dir: Path, plugin_id: str) -> Path:
        """Pick the directory inside the extracted tarball that should
        become ``plugins/<id>/``.

        Two layouts are tolerated:
          * Single top-level folder containing ``plugin.json``,
            common for GitHub source tarballs (``<repo>-<sha>/...``).
            We don't care about the folder name on the way in, we
            rename to ``plugin_id`` on the way out.
          * ``plugin.json`` at the extract root, common for the
            ``release.tar.gz`` artifact CI builds for the catalog.

        Anything else raises ``InstallRefused``."""
        if (extract_dir / "plugin.json").exists():
            return extract_dir
        candidates = [p for p in extract_dir.iterdir() if p.is_dir()]
        if len(candidates) == 1 and (candidates[0] / "plugin.json").exists():
            return candidates[0]
        raise InstallRefused(
            f"Could not find plugin.json in the {plugin_id!r} tarball (looked at "
            "the extract root and a single top-level folder)."
        )

    def _validate_embedded_manifest(self, plugin_root: Path, entry: CatalogEntry) -> None:
        """Validate the manifest inside the tarball against
        ``schema/plugin.schema.json`` AND check it claims the same
        identity as the catalog entry. Belt + braces — the catalog
        PR review is the primary trust gate, this catches the case
        where a tarball drifts after merge (republished tag, etc.)."""
        manifest_path = plugin_root / "plugin.json"
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise InstallRefused(f"Embedded plugin.json invalid: {err}") from err
        if not isinstance(raw, dict):
            raise InstallRefused("Embedded plugin.json must be a JSON object")
        try:
            schema = self._load_json(self._schema_path)
            jsonschema.validate(raw, schema)
        except jsonschema.ValidationError as err:
            field_path = ".".join(str(p) for p in err.absolute_path) or "<root>"
            raise InstallRefused(
                f"Embedded manifest failed schema validation [{field_path}]: {err.message}"
            ) from err
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
        existing[record.plugin_id] = record
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
