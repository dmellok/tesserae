"""Routes for the community widget marketplace (Settings → Plugins → Browse).

Three POST endpoints plus the Browse page render, all mounted under
``/plugins/`` to live alongside the existing plugin admin routes. The
underlying ``Marketplace`` (``app.config['MARKETPLACE']``) owns the
trust-sensitive work, fetch / validate / sha256 / extract / persist;
this module just translates between form data and that API and flashes
the outcome.

Restart-required UX: install + uninstall flash a banner and offer a
``Restart now`` button that hits :func:`restart` here, which calls
``app.config['UPDATER'].restart()`` for the actual re-exec. Live
re-discovery of the registry would need blueprint deregistration +
safe ``importlib.reload``, which Flask doesn't support cleanly, so v1
treats every marketplace mutation as "restart to pick it up".
"""

from __future__ import annotations

import logging
from typing import Any

from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.wrappers import Response

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


def _filter_entries(entries: list[CatalogEntry], *, tag: str | None) -> list[CatalogEntry]:
    """Apply the tag-chip filter from the Browse page query string.
    No tag (or unknown tag) returns everything."""
    if not tag:
        return list(entries)
    return [e for e in entries if tag in e.tags]


def _entries_payload(
    entries: list[CatalogEntry],
    installed: dict[str, Any],
    screenshots_base: str,
) -> list[dict[str, Any]]:
    """Shape catalog entries for the template: include install state,
    screenshot URL, and the "Update available" decision so the
    template stays presentation-only."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        record = installed.get(entry.id)
        installed_version = record.version if record is not None else None
        update_available = bool(
            installed_version is not None and installed_version != entry.release_version
        )
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
        out.append(
            {
                "id": entry.id,
                "name": entry.name,
                "description": entry.description,
                "icon": entry.icon or "ph-puzzle-piece",
                "author_name": entry.author_name,
                "author_github": entry.author_github,
                "tags": entry.tags,
                "kind": entry.kind,
                "official": entry.official,
                "source": entry.source,
                "version": entry.release_version,
                "installed": record is not None,
                "installed_version": installed_version,
                "update_available": update_available,
                "screenshot_url": primary_url,
                "screenshot_sizes": entry.screenshot_sizes,
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


@bp.get("/browse")
def browse() -> str:
    """Render the Browse page. Failures to fetch the index don't
    blank the page, fall back to the cached snapshot when possible
    and surface a muted "Couldn't refresh" line."""
    mkt = _marketplace()
    if not mkt.index_url():
        return render_template(
            "plugins_browse.html",
            entries=[],
            tags=[],
            active_tag=None,
            installed_count=0,
            index_url="",
            stale=False,
            error=None,
            restart_pending=_restart_pending(),
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
    active_tag = request.args.get("tag") or None
    entries = _filter_entries(entries, tag=active_tag)
    installed = mkt.installed()
    return render_template(
        "plugins_browse.html",
        entries=_entries_payload(entries, installed, mkt.screenshots_base()),
        tags=_collect_tags(mkt.cached_index() or entries),
        active_tag=active_tag,
        installed_count=len(installed),
        index_url=mkt.index_url(),
        stale=stale,
        error=error,
        restart_pending=_restart_pending(),
    )


@bp.post("/browse/install")
def install() -> Response:
    """Install one catalog entry by id. The form sends only the id;
    we re-fetch the index here so a stale browser tab can't smuggle
    in a different tarball URL or sha256 than the catalog actually
    serves right now."""
    mkt = _marketplace()
    catalog_id = (request.form.get("catalog_id") or "").strip()
    if not catalog_id:
        flash("Missing catalog id.", "error")
        return redirect(url_for("marketplace.browse"))
    try:
        entries = mkt.fetch_index(force=True)
    except IndexUnavailable as err:
        flash(f"Couldn't refresh the catalog: {err}", "error")
        return redirect(url_for("marketplace.browse"))
    entry = next((e for e in entries if e.id == catalog_id), None)
    if entry is None:
        flash(f"Catalog entry {catalog_id!r} not found (try refreshing).", "error")
        return redirect(url_for("marketplace.browse"))
    try:
        result = mkt.install(entry)
    except (InstallRefused, TarballRejected) as err:
        flash(f"Install failed: {err}", "error")
        return redirect(url_for("marketplace.browse"))
    except Exception:
        logger.exception("marketplace: unexpected install failure for %s", catalog_id)
        flash("Install failed with an unexpected error (see server log).", "error")
        return redirect(url_for("marketplace.browse"))
    _mark_restart_pending()
    flash(
        f"Installed {entry.name} v{result.version}. Restart Tesserae to load it.",
        "ok",
    )
    return redirect(url_for("marketplace.browse"))


@bp.post("/browse/uninstall")
def uninstall() -> Response:
    """Uninstall a marketplace-installed plugin. Refuses bundled
    plugins by checking the marketplace record first; the ``Marketplace``
    layer also enforces this, so a hand-crafted POST with a bundled id
    still hits the safety net."""
    mkt = _marketplace()
    plugin_id = (request.form.get("plugin_id") or "").strip()
    if not plugin_id:
        flash("Missing plugin id.", "error")
        return redirect(url_for("marketplace.browse"))
    delete_data = request.form.get("delete_data") == "1"
    try:
        removed = mkt.uninstall(plugin_id, delete_data=delete_data)
    except Exception:
        logger.exception("marketplace: unexpected uninstall failure for %s", plugin_id)
        flash("Uninstall failed with an unexpected error (see server log).", "error")
        return redirect(url_for("marketplace.browse"))
    if not removed:
        flash(
            f"{plugin_id!r} isn't tracked by the marketplace, refusing to "
            "remove a bundled or hand-installed plugin.",
            "error",
        )
        return redirect(url_for("marketplace.browse"))
    _mark_restart_pending()
    msg = f"Uninstalled {plugin_id}."
    if delete_data:
        msg += " Plugin data dir also removed."
    msg += " Restart Tesserae to drop it from the running registry."
    flash(msg, "ok")
    return redirect(url_for("marketplace.browse"))


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
