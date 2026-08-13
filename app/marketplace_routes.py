"""Routes for the community catalog (Settings → Widgets → Browse catalog).

Three POST endpoints plus the Browse page render, all mounted under
``/plugins/`` to live alongside the existing plugin admin routes. The
underlying ``Marketplace`` (``app.config['MARKETPLACE']``) owns the
trust-sensitive work, fetch / validate / sha256 / extract / persist;
this module just translates between form data and that API and flashes
the outcome.

Restart-required UX: install + uninstall set
``MARKETPLACE_RESTART_PENDING`` on the app config and flash a
notice, but DON'T auto-restart the process. Reason: users often
install several widgets in a row, and forcing a restart after each
one is friction. Instead a persistent "Restart required" button
lights up in the topbar (rendered from ``_base.html`` via the
``marketplace_restart_pending`` context-processor flag) and the
user clicks it once when they're done batching, which hits
:func:`restart` here to call ``app.config['UPDATER'].restart()``
for the re-exec. Live in-process re-discovery of the registry would
need blueprint deregistration + safe ``importlib.reload``, which
Flask doesn't support cleanly, so we treat every marketplace
mutation as "restart to pick it up".
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.wrappers import Response

from app import authored_widgets, plugin_loader
from app.marketplace import (
    CatalogEntry,
    IndexUnavailable,
    InstallRefused,
    Marketplace,
    TarballRejected,
)

logger = logging.getLogger(__name__)

bp = Blueprint("marketplace", __name__)


def _marketplace() -> Marketplace:
    mkt = current_app.config.get("MARKETPLACE")
    if mkt is None:
        abort(404)
    assert isinstance(mkt, Marketplace)
    return mkt


def _restart_pending() -> bool:
    """True when an install/uninstall has happened but the user hasn't
    restarted yet. Backed by a flag in app.config so it survives across
    requests (one process), but resets naturally on restart (the flag
    is in-memory). Surfaced as the "Restart required" banner."""
    return bool(current_app.config.get("MARKETPLACE_RESTART_PENDING", False))


def _mark_restart_pending() -> None:
    current_app.config["MARKETPLACE_RESTART_PENDING"] = True


def _install_counts() -> dict[str, int]:
    """Per-widget install counts for the Browse cards. Best-effort and gated by
    the master online-features switch; ``{}`` when off or the endpoint is down
    (the cards then simply omit the count)."""
    from app import online

    try:
        if not online.online_enabled(current_app.config.get("SETTINGS_STORE")):
            return {}
        return online.widget_install_counts()
    except Exception:
        return {}


def _report_install(entry: CatalogEntry) -> None:
    """Best-effort: report the install to api.tesserae.ink for the anonymous
    per-widget count, and log a ``telemetry`` event so it shows on /events.

    The event's ``target`` is the widget id and its ``extra`` names the widget
    (id + human name) so the /events row and its expanded detail both say which
    widget was installed. Gated by the master online-features switch. Never
    raises; a failure here must not affect the install the user just completed.
    """
    from app import online

    catalog_id = entry.id
    try:
        settings = current_app.config.get("SETTINGS_STORE")
        if not online.online_enabled(settings):
            return
        install_id = current_app.config.get("INSTALL_ID")
        version = current_app.config.get("APP_VERSION")
        sent = online.report_widget_install(catalog_id, install_id, version)
        event_log = current_app.config.get("EVENT_LOG")
        if event_log is not None:
            event_log.record(
                type="telemetry",
                source="install",
                target=catalog_id,
                status="sent" if sent else "failed",
                extra={
                    "widget": catalog_id,
                    "name": entry.name,
                    "version": version or "",
                    "endpoint": "widgets/install",
                },
            )
    except Exception:
        logger.debug("marketplace: install report failed for %s", catalog_id, exc_info=True)


def _filter_entries(
    entries: list[CatalogEntry],
    *,
    tag: str | None,
    kind: str | None,
    query: str | None,
) -> list[CatalogEntry]:
    """Apply the chip filters + free-text query from the Browse page
    query string. No tag/kind/query (or unknown value) returns
    everything. Query matches case-insensitively on name, description,
    id, author, and tags."""
    out = list(entries)
    if tag:
        out = [e for e in out if tag in e.tags]
    if kind:
        out = [e for e in out if e.kind == kind]
    if query:
        q = query.casefold()

        def _haystack(e: CatalogEntry) -> str:
            return " ".join(
                str(part).casefold()
                for part in (
                    e.id,
                    e.name,
                    e.description,
                    e.author_name or "",
                    " ".join(e.tags),
                )
            )

        out = [e for e in out if q in _haystack(e)]
    return out


def _entries_payload(
    entries: list[CatalogEntry],
    installed: dict[str, Any],
    screenshots_base: str,
    plugins_dir: Any,
    install_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Shape catalog entries for the template: include install state,
    screenshot URL, and the "Update available" decision so the
    template stays presentation-only.

    ``folders`` on the payload is the list the user should see
    landing in ``plugins/``: catalog's declared folders when present,
    falling back to the catalog id for single-widget entries. For
    installed entries we prefer the marketplace record's actual
    folders so reinstalling over an old set surfaces the truth.

    Pre-bundled detection: when an entry's declared folders all exist
    on disk but no marketplace record tracks them, the entry is
    surfaced as ``installed`` with ``installed_from_disk: True``.
    Covers the upgrade case where a widget shipped in the bundle on
    an older Tesserae release moved to the catalog later, the user
    still has the folder, the Browse page should let them Uninstall
    it rather than show a confusing "Install" button that refuses on
    folder collision."""
    counts = install_counts or {}
    out: list[dict[str, Any]] = []
    for entry in entries:
        record = installed.get(entry.id)
        installed_version = record.version if record is not None else None
        # lg is required by the schema, so it's always in screenshot_sizes
        # for a valid entry; default to it explicitly to keep the
        # template's image src derivation simple.
        primary_size = (
            "lg"
            if "lg" in entry.screenshot_sizes
            else (entry.screenshot_sizes[0] if entry.screenshot_sizes else None)
        )
        primary_url = (
            f"{screenshots_base}/screenshots/{entry.id}/{primary_size}.png"
            if (primary_size and screenshots_base)
            else None
        )
        # Carousel extras (optional). When the entry declares
        # ``extra_screenshot_count``, the catalog also ships
        # ``screenshots/<id>/extra-<n>.png`` for n in 1..count, same
        # 3:2 lg-size shape. The template uses len(screenshot_urls) > 1
        # to switch from <img> to the carousel widget; a one-element
        # list (the default) renders byte-identically to the pre-feature
        # behaviour.
        screenshot_urls: list[str] = [primary_url] if primary_url else []
        if primary_url and screenshots_base and entry.extra_screenshot_count > 0:
            screenshot_urls.extend(
                f"{screenshots_base}/screenshots/{entry.id}/extra-{n}.png"
                for n in range(1, entry.extra_screenshot_count + 1)
            )
        icon_url = (
            f"{screenshots_base}/icons/{entry.icon_asset}"
            if (entry.icon_asset and screenshots_base)
            else None
        )
        # A mark stands in for the preview entirely, not just the small
        # icon slot: for a widget that renders whatever the user's own
        # library holds, one screenshot is an arbitrary photo and says
        # nothing about what the widget is. Dropped here rather than in
        # the view so the client model never has to re-decide it.
        if icon_url:
            primary_url = None
            screenshot_urls = []
        if record is not None:
            folders = list(record.folders)
        elif entry.folders:
            folders = list(entry.folders)
        else:
            folders = [entry.id]
        is_bundle = len(folders) > 1 or folders != [entry.id]

        # All declared folders present on disk + no marketplace record =
        # pre-bundled (or hand-installed) instance. Some-but-not-all is
        # ambiguous; leave it for the user to clean up by hand.
        installed_from_disk = (
            record is None
            and bool(folders)
            and plugins_dir is not None
            and all((plugins_dir / f).exists() for f in folders)
        )
        is_installed = record is not None or installed_from_disk

        update_available = bool(
            installed_version is not None and installed_version != entry.release_version
        )
        out.append(
            {
                "id": entry.id,
                "name": entry.name,
                "description": entry.description,
                "icon": entry.icon or "ph-puzzle-piece",
                "icon_url": icon_url,
                "author_name": entry.author_name,
                "author_github": entry.author_github,
                "tags": entry.tags,
                "kind": entry.kind,
                "official": entry.official,
                "source": entry.source,
                "stars": entry.stars,
                "installs": counts.get(entry.id),
                "version": entry.release_version,
                "installed": is_installed,
                "installed_from_disk": installed_from_disk,
                "installed_version": installed_version,
                "update_available": update_available,
                "screenshot_url": primary_url,
                "screenshot_urls": screenshot_urls,
                "screenshot_sizes": entry.screenshot_sizes,
                "folders": folders,
                "is_bundle": is_bundle,
            }
        )
    return out


def _collect_tags(entries: list[CatalogEntry]) -> list[str]:
    """Tags that actually appear in the index, sorted for stable
    chip ordering. Hidden tags (declared in the schema but never used
    by an entry) don't render a filter chip."""
    seen: set[str] = set()
    for entry in entries:
        seen.update(entry.tags)
    return sorted(seen)


# Kind chips render in this fixed order. Widget first (the bulk of the
# catalog), then themes (visual), then fonts (typographic). Unknown
# kinds fall to the end alphabetically so a future kind shows up
# without a code change.
_KIND_ORDER = ["widget", "theme", "font"]
_KIND_LABELS = {"widget": "Widgets", "theme": "Themes", "font": "Fonts"}


def _collect_kinds(entries: list[CatalogEntry]) -> list[dict[str, Any]]:
    """Per-kind counts for the chip row, in display order. Kinds with
    zero entries are omitted so the row stays useful (a catalog with
    no themes shouldn't show a "Themes (0)" chip)."""
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.kind] = counts.get(entry.kind, 0) + 1
    ordered: list[dict[str, Any]] = []
    for k in _KIND_ORDER:
        if counts.get(k):
            ordered.append({"key": k, "label": _KIND_LABELS[k], "count": counts[k]})
    for k in sorted(counts):
        if k in _KIND_ORDER:
            continue
        ordered.append({"key": k, "label": k.title(), "count": counts[k]})
    return ordered


def _panel_fit() -> dict[str, Any]:
    """What the rail's "Fits my panels only" filter needs: the resolutions
    the user actually has registered, plus the known device names at each
    resolution so a template group can say what it fits. Shared with the
    template catalog (same helpers the standalone templates page used)."""
    from app import template_market

    devices = current_app.config.get("DEVICE_REGISTRY")
    return {
        "my_resolutions": template_market.registered_device_resolutions(devices),
        "resolution_devices": template_market.resolution_device_labels(),
    }


@bp.get("/browse")
def browse() -> str:
    """Render the Browse catalog page.

    The page is one catalog over three item types: widgets and themes come
    from the static index on GitHub (rendered here), templates from
    api.tesserae.ink (fetched by the client from
    ``/plugins/templates/index.json``, which needs the online switch).

    Filtering, sorting, grouping and the detail sheet all run client-side
    off ``catalog_payload``, so typing never navigates. ``?q=`` / ``?tag=``
    / ``?kind=`` / ``?type=`` still work: they seed the client's initial
    filter state, and the ``<noscript>`` list below is filtered server-side
    by the same values so a JS-less browser can still install.

    Failures to fetch the index don't blank the page: fall back to the
    cached snapshot when possible and surface a muted "Couldn't refresh"
    line."""
    mkt = _marketplace()
    initial = {
        "type": request.args.get("type") or "All",
        "tag": request.args.get("tag") or None,
        "kind": request.args.get("kind") or None,
        "q": (request.args.get("q") or "").strip(),
        "status": request.args.get("status") or "All",
    }
    if not mkt.index_url():
        return render_template(
            "plugins_browse.html",
            entries=[],
            catalog_payload={"items": [], "initial": initial},
            active_tag=None,
            active_kind=None,
            active_query=None,
            index_url="",
            stale=False,
            error=None,
            restart_pending=_restart_pending(),
            templates_enabled=_templates_enabled(),
            templates_online=_templates_online(),
        )
    error: str | None = None
    stale = False
    entries: list[CatalogEntry] = []
    refresh = request.args.get("refresh") == "1"
    try:
        entries = mkt.fetch_index(force=refresh)
    except IndexUnavailable as err:
        error = str(err)
        cached = mkt.cached_index()
        if cached is not None:
            entries = cached
            stale = True
    active_tag = initial["tag"]
    active_kind = initial["kind"]
    active_query = initial["q"] or None
    installed = mkt.installed()
    install_counts = _install_counts()
    payload_items = _entries_payload(
        entries, installed, mkt.screenshots_base(), mkt.plugins_dir(), install_counts
    )
    # The client gets the whole index and narrows it live; the noscript
    # list gets the server-filtered slice so deep links still land
    # somewhere sensible without JS.
    fallback = _entries_payload(
        _filter_entries(entries, tag=active_tag, kind=active_kind, query=active_query),
        installed,
        mkt.screenshots_base(),
        mkt.plugins_dir(),
        install_counts,
    )
    catalog_payload: dict[str, Any] = {
        "items": payload_items,
        "categories": _collect_tags(entries),
        "kinds": _collect_kinds(entries),
        "initial": initial,
        "templates_enabled": _templates_enabled(),
        "templates_online": _templates_online(),
        "stale": stale,
        "index_url": mkt.index_url(),
        **_panel_fit(),
    }
    return render_template(
        "plugins_browse.html",
        entries=fallback,
        catalog_payload=catalog_payload,
        active_tag=active_tag,
        active_kind=active_kind,
        active_query=active_query,
        index_url=mkt.index_url(),
        stale=stale,
        error=error,
        restart_pending=_restart_pending(),
        templates_enabled=_templates_enabled(),
        templates_online=_templates_online(),
    )


def _templates_enabled() -> bool:
    """Whether the community-templates section shows on Browse at all: the
    experiment flag alone. An install that never opted into the experiment
    sees nothing, same as before."""
    from app import experiments

    return experiments.is_enabled("templates")


def _templates_online() -> bool:
    """Whether that section can actually reach its catalog. The templates
    live on api.tesserae.ink, so they need the master online switch too.
    Kept separate from :func:`_templates_enabled` so the section can explain
    itself when the switch is off instead of silently vanishing (#224):
    widgets come from a static index on GitHub and stay browsable either way,
    which made the disappearance look arbitrary."""
    from app import online

    return online.online_enabled(current_app.config.get("SETTINGS_STORE"))


def _wants_json() -> bool:
    """True when the caller is the Browse catalog's fetch(), not a form post.

    The catalog page installs in place (the row keeps its filters, sort
    position and scroll), so it needs the outcome back as data rather than
    a redirect + flash. Anything else, including a ``<noscript>`` form
    post, keeps the redirect path untouched."""
    return request.headers.get("X-Requested-With") == "tesserae-fetch"


def _outcome(ok: bool, message: str, *, status: int = 400, **extra: Any) -> Response:
    """Flash + redirect for form posts, JSON for the catalog page."""
    if _wants_json():
        resp = jsonify(
            {"ok": ok, "message": message, "restart_pending": _restart_pending(), **extra}
        )
        resp.status_code = 200 if ok else status
        return resp
    flash(message, "ok" if ok else "error")
    return redirect(url_for("marketplace.browse"))


@bp.post("/browse/install")
def install() -> Response | tuple[Response, int]:
    """Install one catalog entry by id. The form sends only the id;
    we re-fetch the index here so a stale browser tab can't smuggle
    in a different tarball URL or sha256 than the catalog actually
    serves right now."""
    mkt = _marketplace()
    catalog_id = (request.form.get("catalog_id") or "").strip()
    if not catalog_id:
        return _outcome(False, "Missing catalog id.", status=400)
    try:
        entries = mkt.fetch_index(force=True)
    except IndexUnavailable as err:
        return _outcome(False, f"Couldn't refresh the catalog: {err}", status=502)
    entry = next((e for e in entries if e.id == catalog_id), None)
    if entry is None:
        return _outcome(
            False, f"Catalog entry {catalog_id!r} not found (try refreshing).", status=404
        )
    try:
        result = mkt.install(entry)
    except (InstallRefused, TarballRejected) as err:
        return _outcome(False, f"Install failed: {err}", status=400)
    except Exception:
        logger.exception("marketplace: unexpected install failure for %s", catalog_id)
        return _outcome(
            False, "Install failed with an unexpected error (see server log).", status=500
        )
    _mark_restart_pending()
    _report_install(entry)
    return _outcome(
        True,
        f"Installed {entry.name} v{result.version}. Click "
        '"Restart required" in the top bar when you\'re done installing '
        "to load it (you can queue more installs first).",
        installed=True,
        version=result.version,
    )


@bp.post("/browse/uninstall")
def uninstall() -> Response:
    """Uninstall a marketplace-installed catalog entry. For bundle
    entries this removes every folder listed on the install record
    (so a github bundle takes its `_core` + every display widget
    with it). Refuses bundled plugins by checking the marketplace
    record first; the ``Marketplace`` layer also enforces this so a
    hand-crafted POST with a bundled id still hits the safety net."""
    mkt = _marketplace()
    catalog_id = (request.form.get("catalog_id") or "").strip()
    if not catalog_id:
        return _outcome(False, "Missing catalog id.")
    delete_data = request.form.get("delete_data") == "1"
    try:
        removed = mkt.uninstall(catalog_id, delete_data=delete_data)
    except Exception:
        logger.exception("marketplace: unexpected uninstall failure for %s", catalog_id)
        return _outcome(
            False, "Uninstall failed with an unexpected error (see server log).", status=500
        )
    if not removed:
        return _outcome(
            False,
            f"{catalog_id!r} isn't tracked by the marketplace, refusing to "
            "remove a bundled or hand-installed plugin.",
            status=409,
        )
    _mark_restart_pending()
    msg = f"Uninstalled {catalog_id}."
    if delete_data:
        msg += " Plugin data dir(s) also removed."
    msg += (
        ' Click "Restart required" in the top bar when you\'re done'
        " to drop it from the running registry."
    )
    return _outcome(True, msg, installed=False)


@bp.post("/errors/remove")
def remove_shadowed() -> Response:
    """Delete a plugin folder the loader skipped as a duplicate id.

    Widgets pushed from Tesserae Studio land in ``data/authored/`` and
    marketplace installs in ``data/marketplace/``, and the first scan root
    to claim an id wins (bundled, then authored, then marketplace). The
    losing folder sits on disk forever, doing nothing but filling the
    Widgets page with errors, and until now the only way out was a shell.

    Deleting is safe here precisely because the folder never loaded: it
    contributed no widget, no blueprint, no data. The gate is that the
    live registry must still be reporting this exact (id, path) pair as a
    duplicate, so a hand-crafted POST can't point this at anything else.
    """
    plugin_id = (request.form.get("plugin_id") or "").strip()
    raw_path = (request.form.get("path") or "").strip()
    registry = current_app.config.get("PLUGIN_REGISTRY")
    matched = next(
        (
            err
            for err in (getattr(registry, "errors", None) or [])
            if err.plugin_id == plugin_id
            and str(err.path) == raw_path
            and err.message == plugin_loader.DUPLICATE_ID_MESSAGE
        ),
        None,
    )
    if matched is None:
        flash("That folder isn't a duplicate the loader reported; nothing removed.", "error")
        return redirect(url_for("plugins.plugins_index"))

    data_root = Path(current_app.config["DATA_ROOT"])
    origin = plugin_loader.shadowed_origin(matched.path, data_root)
    if origin is None:
        flash("Only widgets under the data directory can be removed here.", "error")
        return redirect(url_for("plugins.plugins_index"))

    removed: bool
    if origin == "authored":
        removed = authored_widgets.uninstall(plugin_id, data_root=data_root)
    else:
        outcome = _remove_marketplace_folder(plugin_id, matched.path)
        if outcome is None:
            return redirect(url_for("plugins.plugins_index"))
        removed = outcome

    if not removed:
        flash(f"Could not remove {matched.path}.", "error")
        return redirect(url_for("plugins.plugins_index"))

    # A shadowed folder never loaded, so dropping it can't leave a stale
    # blueprint behind and an in-process re-scan is enough to clear the
    # error. No restart banner for this one.
    rediscover = current_app.config.get("REDISCOVER_PLUGINS")
    if callable(rediscover):
        current_app.config["PLUGIN_REGISTRY"] = rediscover()
    flash(f"Removed the shadowed copy of {plugin_id!r} from {origin}.", "ok")
    return redirect(url_for("plugins.plugins_index"))


def _remove_marketplace_folder(plugin_id: str, path: Path) -> bool | None:
    """Remove a shadowed folder under ``data/marketplace/``.

    Routed through ``Marketplace.uninstall`` when the folder is the whole
    of a catalog install, so the record in ``marketplace.json`` goes with
    it and Browse stops claiming the entry is installed. A folder that is
    one of several in a bundle is left alone: removing a bundle piecemeal
    would break the siblings that are loading fine. Returns ``None`` when
    the caller should stop (already flashed)."""
    mkt = current_app.config.get("MARKETPLACE")
    if isinstance(mkt, Marketplace):
        for catalog_id, record in mkt.installed().items():
            if record.kind == "theme" or plugin_id not in record.folders:
                continue
            if len(record.folders) > 1:
                flash(
                    f"{plugin_id!r} is part of the {catalog_id!r} bundle. Uninstall the "
                    "whole entry from Browse instead, so its other widgets go with it.",
                    "error",
                )
                return None
            return bool(mkt.uninstall(catalog_id))
    # Untracked folder (hand-dropped, or a record that has since been
    # lost). Nothing to unwind in marketplace state, so remove it directly.
    try:
        shutil.rmtree(path)
    except OSError:
        logger.exception("marketplace: could not remove shadowed folder %s", path)
        return False
    return True


@bp.post("/browse/restart")
def restart() -> Response:
    """Hit the existing Updater re-exec path so the marketplace
    install/uninstall takes effect. Same machinery the self-updater
    uses; no-op under ``--dev`` where the reloader handles it."""
    updater = current_app.config.get("UPDATER")
    if updater is None:
        flash("Restart endpoint unavailable.", "error")
        return redirect(url_for("marketplace.browse"))
    try:
        updater.restart()
    except Exception:
        logger.exception("marketplace: restart() raised")
        flash("Could not schedule a restart (see server log).", "error")
        return redirect(url_for("marketplace.browse"))
    flash("Restarting Tesserae, this tab will reload in a few seconds.", "ok")
    return redirect(url_for("marketplace.browse"))


def register(app: Flask) -> None:
    """Mount the marketplace blueprint at ``/plugins`` so its routes
    live alongside the existing plugin admin URLs. Called from the
    app factory after plugin_loader.register_routes so the static
    ``/plugins/browse`` path wins over the parametric
    ``/plugins/<plugin_id>/<asset>`` route by Flask's specificity
    rules."""
    app.register_blueprint(bp, url_prefix="/plugins")
