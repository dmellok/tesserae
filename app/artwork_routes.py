"""The admin page every artwork collection gets: ``/plugins/<plugin_id>/artwork/<name>/``.

Rendered by the host for any widget that declares an ``app.artwork.Collection``:
the day's spend and budget, Render now, Paint the held ones, Purge, a live
ticker of what is painting, and the pictures grouped by the dashboard that
asked for them with a tab per facet (the style), each with Repaint, Delete,
Clear error, its earlier versions and whatever actions the widget added.
"""

from __future__ import annotations

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
    send_from_directory,
    url_for,
)
from werkzeug.wrappers import Response as WerkzeugResponse

from app import artwork, fal

bp = Blueprint("artwork", __name__, url_prefix="/plugins")


def _lookup(plugin_id: str, name: str) -> tuple[artwork.Collection, Any, Path, dict[str, Any]]:
    """The collection, its plugin, the plugin's data dir and its settings; 404
    when the plugin is not loaded or declares no such collection."""
    registry = current_app.config.get("PLUGIN_REGISTRY")
    plugin = registry.get(plugin_id) if registry is not None else None
    if plugin is None:
        abort(404)
    c = artwork.collection(plugin_id, name)
    if c is None:
        abort(404)
    store = current_app.config.get("SETTINGS_STORE")
    settings: dict[str, Any] = {}
    if store is not None:
        settings = store.get_for_runtime("plugins", plugin_id, plugin.manifest.get("settings", []))
    return c, plugin, Path(plugin.data_dir), settings


def _back(plugin_id: str, name: str) -> WerkzeugResponse:
    return redirect(url_for("artwork.index", plugin_id=plugin_id, name=name))


def gallery(c: artwork.Collection, data_dir: Path) -> dict[str, Any]:
    """The pictures grouped by dashboard with a tab per facet, then the rest."""
    pictures = {e["key"]: e for e in c.listing(data_dir)}
    errors = {e["key"]: e for e in c.errors(data_dir)}
    held = c.held(data_dir)
    prog = c.progress(data_dir, None)
    running = {r["key"] for r in prog["running"]} | {r["key"] for r in prog["queued"]}
    groups: list[dict[str, Any]] = []
    on_dashboard: set[str] = set()
    by_name: dict[str, dict[str, Any]] = {}
    for d in c.dashboards(data_dir):
        name = str(d.get("name") or "")
        grp = by_name.get(name)
        if grp is None:
            grp = by_name[name] = {"name": name, "icon": str(d.get("icon") or ""), "tabs": []}
            groups.append(grp)
        keys = [str(k) for k in d.get("keys") or []]
        cards = []
        missing = 0
        for k in keys:
            on_dashboard.add(k)
            if k in pictures:
                cards.append(pictures[k])
            else:
                missing += 1
                title = str(((d.get("jobs") or {}).get(k) or {}).get("title") or k)
                cards.append(
                    {
                        "key": k,
                        "title": title,
                        "file": "",
                        "meta": {},
                        "versions": c.versions(k, data_dir),
                        "facet_label": "",
                        "error": errors.get(k, {}).get("why", ""),
                        "painting": k in running,
                        "held": k in held,
                    }
                )
        grp["tabs"].append(
            {
                "id": d["id"],
                "label": str(d.get("facet_label") or d.get("facet") or "All"),
                "cards": cards,
                "missing": missing,
            }
        )
    rest = [e for k, e in pictures.items() if k not in on_dashboard]
    for e in list(pictures.values()) + rest:
        e.setdefault("painting", e["key"] in running)
        e.setdefault("held", e["key"] in held)
    stray = [e for k, e in errors.items() if k not in pictures and k not in on_dashboard]
    return {"groups": groups, "rest": rest, "errors": stray, "count": len(pictures)}


@bp.get("/<plugin_id>/artwork/<name>/")
def index(plugin_id: str, name: str) -> str:
    c, plugin, data_dir, settings = _lookup(plugin_id, name)
    key = fal.painting_key(settings)
    extra: dict[str, Any] = {}
    if c.extra_context is not None:
        try:
            extra = dict(c.extra_context(data_dir))
        except Exception:
            current_app.logger.exception("artwork %s/%s: extra context failed", plugin_id, name)
    return render_template(
        "artwork.html",
        c=c,
        plugin=plugin,
        g=gallery(c, data_dir),
        progress=c.progress(data_dir, settings),
        has_key=bool(key),
        held=len(c.held(data_dir)),
        wanted=len(c.dashboards(data_dir)),
        **extra,
    )


@bp.get("/<plugin_id>/artwork/<name>/progress")
def progress(plugin_id: str, name: str) -> WerkzeugResponse:
    c, _plugin, data_dir, settings = _lookup(plugin_id, name)
    return jsonify(c.progress(data_dir, settings))


@bp.get("/<plugin_id>/artwork/<name>/file/<path:filename>")
def file(plugin_id: str, name: str, filename: str) -> WerkzeugResponse:
    c, _plugin, data_dir, _settings = _lookup(plugin_id, name)
    if ".." in filename or filename.startswith("/"):
        abort(404)
    return send_from_directory(c.folder(data_dir), filename, max_age=3600)


@bp.post("/<plugin_id>/artwork/<name>/stop")
def stop(plugin_id: str, name: str) -> WerkzeugResponse:
    c, _plugin, data_dir, _settings = _lookup(plugin_id, name)
    ok = c.stop(str(request.form.get("key") or ""), data_dir)
    return jsonify({"stopped": ok})


@bp.post("/<plugin_id>/artwork/<name>/stop-all")
def stop_all(plugin_id: str, name: str) -> WerkzeugResponse:
    c, _plugin, data_dir, _settings = _lookup(plugin_id, name)
    return jsonify({"stopped": c.stop_all(data_dir)})


@bp.post("/<plugin_id>/artwork/<name>/render-now")
def render_now(plugin_id: str, name: str) -> WerkzeugResponse:
    c, _plugin, data_dir, settings = _lookup(plugin_id, name)
    key = fal.painting_key(settings)
    if not key:
        flash(
            "No AI key: add a fal.ai or OpenAI key under Settings › Generative AI, or a fal.ai key to this widget's settings.",
            "warn",
        )
        return _back(plugin_id, name)
    started, found = c.render_now(data_dir, key, settings, str(request.form.get("who") or ""))
    if started:
        flash(f"Painting {started} of the {found} the panels are missing.", "ok")
    elif found:
        flash(
            f"To-day's budget of {artwork.dollars(c.cap(settings))} is spent, or those are already being painted.",
            "warn",
        )
    else:
        flash("The panels have every picture they asked for.", "ok")
    return _back(plugin_id, name)


@bp.post("/<plugin_id>/artwork/<name>/paint-held")
def paint_held(plugin_id: str, name: str) -> WerkzeugResponse:
    c, _plugin, data_dir, settings = _lookup(plugin_id, name)
    key = fal.painting_key(settings)
    if not key:
        flash("No AI key yet: add a fal.ai or OpenAI key under Settings › Generative AI.", "warn")
        return _back(plugin_id, name)
    n = c.start_held(data_dir, key, settings, str(request.form.get("subject") or ""))
    flash(
        f"Started {n} held painting{'' if n == 1 else 's'}."
        if n
        else "Nothing was waiting, or the day's budget is spent.",
        "ok" if n else "warn",
    )
    return _back(plugin_id, name)


@bp.post("/<plugin_id>/artwork/<name>/purge")
def purge(plugin_id: str, name: str) -> WerkzeugResponse:
    c, _plugin, data_dir, _settings = _lookup(plugin_id, name)
    out = c.purge(data_dir)
    flash(
        f"Removed {out['pictures']} picture{'' if out['pictures'] == 1 else 's'} and {out['versions']} earlier version{'' if out['versions'] == 1 else 's'}.",
        "ok",
    )
    return _back(plugin_id, name)


@bp.post("/<plugin_id>/artwork/<name>/budget-reset")
def budget_reset(plugin_id: str, name: str) -> WerkzeugResponse:
    c, _plugin, data_dir, _settings = _lookup(plugin_id, name)
    c.budget_reset(data_dir)
    flash("To-day's spend reset to US$0.", "ok")
    return _back(plugin_id, name)


@bp.post("/<plugin_id>/artwork/<name>/<key>/repaint")
def repaint(plugin_id: str, name: str, key: str) -> WerkzeugResponse:
    c, _plugin, data_dir, settings = _lookup(plugin_id, name)
    fal_key = fal.painting_key(settings)
    if not fal_key:
        flash("No AI key yet: add a fal.ai or OpenAI key under Settings › Generative AI.", "warn")
    elif c.repaint(key, data_dir, fal_key, settings):
        flash("Painting it again; the current one is kept as an earlier version.", "ok")
    else:
        flash(
            "It cannot be painted again from here: nothing is known about how it was made, or it is already painting.",
            "warn",
        )
    return _back(plugin_id, name)


@bp.post("/<plugin_id>/artwork/<name>/<key>/delete")
def delete(plugin_id: str, name: str, key: str) -> WerkzeugResponse:
    c, _plugin, data_dir, _settings = _lookup(plugin_id, name)
    if request.form.get("versions") == "1":
        c.delete(key, data_dir, versions=True)
        flash("Deleted, with its earlier versions.", "ok")
    else:
        if c.path(key, data_dir) is not None and c.keep_versions:
            c.archive(key, data_dir)
            flash("Deleted; it is kept as an earlier version.", "ok")
        else:
            c.delete(key, data_dir, versions=False)
            flash("Deleted.", "ok")
    return _back(plugin_id, name)


@bp.post("/<plugin_id>/artwork/<name>/<key>/clear-error")
def clear_error(plugin_id: str, name: str, key: str) -> WerkzeugResponse:
    c, _plugin, data_dir, _settings = _lookup(plugin_id, name)
    c.clear_error(key, data_dir)
    return _back(plugin_id, name)


@bp.post("/<plugin_id>/artwork/<name>/<key>/action/<slug>")
def action(plugin_id: str, name: str, key: str, slug: str) -> WerkzeugResponse:
    c, _plugin, data_dir, _settings = _lookup(plugin_id, name)
    for s, _label, fn in c.actions:
        if s == slug:
            try:
                flash(fn(key, data_dir) or "Done.", "ok")
            except Exception as err:
                flash(f"{type(err).__name__}: {err}", "warn")
            return _back(plugin_id, name)
    abort(404)


@bp.post("/<plugin_id>/artwork/<name>/archive/<stem>/use")
def use_version(plugin_id: str, name: str, stem: str) -> WerkzeugResponse:
    c, _plugin, data_dir, _settings = _lookup(plugin_id, name)
    flash(
        "That version is current again." if c.use_version(stem, data_dir) else "No such version.",
        "ok",
    )
    return _back(plugin_id, name)


@bp.post("/<plugin_id>/artwork/<name>/archive/<stem>/delete")
def delete_version(plugin_id: str, name: str, stem: str) -> WerkzeugResponse:
    c, _plugin, data_dir, _settings = _lookup(plugin_id, name)
    c.delete_version(stem, data_dir)
    flash("Earlier version deleted.", "ok")
    return _back(plugin_id, name)


def register(app: Flask) -> None:
    app.register_blueprint(bp)
