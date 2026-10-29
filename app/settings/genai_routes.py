"""Settings › Generative AI: the shared fal.ai key, the ceilings over every
painting widget, what was spent and where it went."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from flask import current_app, flash, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from app import artwork, commons, fal, genai, openai_images
from app.state.settings_store import SECRET_MASK

from ._shared import bp, plugins, settings_store


def _ledger() -> genai.Ledger:
    return genai.Ledger(Path(current_app.config["DATA_ROOT"]) / "artwork")


def _rows(summary: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The "who is painting" rows, one per collection the host knows, and the
    "where it went" rows, one per collection and model this month."""
    registry = plugins()
    seen: set[tuple[str, str]] = set()
    who: list[dict[str, Any]] = []
    for c in artwork.collections():
        if c.parent is not None:
            continue
        plugin = registry.get(c.plugin_id) if registry is not None else None
        if plugin is None:
            continue
        data_dir = Path(plugin.data_dir)
        fields = plugin.manifest.get("settings", []) if isinstance(plugin.manifest, dict) else []
        settings = settings_store().get_for_runtime("plugins", c.plugin_id, fields)
        own = [artwork.dollars(c.cap(settings))]
        for child in c.children:
            own.append(f"{artwork.dollars(child.cap(settings))} {child.name.replace('_', ' ')}")
        kept = len(c.listing(data_dir))
        for child in c.children:
            kept += len(child.listing(data_dir))
        spent = {"today": 0, "period": 0}
        for name in [c.name] + [ch.name for ch in c.children]:
            got = summary["by_widget"].get((c.plugin_id, name))
            seen.add((c.plugin_id, name))
            if got:
                spent["today"] += got["today"]
                spent["period"] += got["period"]
        prog = c.progress(data_dir, settings)
        title = str(plugin.manifest.get("name") or c.plugin_id)
        who.append(
            {
                "plugin_id": c.plugin_id,
                "name": c.name,
                "title": title,
                "label": "" if c.label.lower() in title.lower() else c.label,
                "own": ", ".join(own),
                "own_key": bool(str(settings.get("fal_api_key") or "").strip()),
                "today": spent["today"],
                "period": spent["period"],
                "kept": kept,
                "painting": len(prog.get("running") or []),
                "queued": len(prog.get("queued") or []),
                "href": url_for("artwork.index", plugin_id=c.plugin_id, name=c.name),
            }
        )
    for (pid, name), got in summary["by_widget"].items():
        if (pid, name) in seen:
            continue
        # In the ledger but not declared yet: a widget declares its collection
        # the first time it runs, so an installed one that has not run since
        # the server started is listed by its plugin page; one that is gone
        # is said so.
        plugin = registry.get(pid) if registry is not None else None
        installed = plugin is not None
        manifest: dict[str, Any] = {}
        if plugin is not None and isinstance(plugin.manifest, dict):
            manifest = plugin.manifest
        who.append(
            {
                "plugin_id": pid,
                "name": name,
                "title": str(manifest.get("name") or pid),
                "label": name.replace("_", " "),
                "own": "",
                "own_key": False,
                "today": got["today"],
                "period": got["period"],
                "kept": 0,
                "painting": 0,
                "queued": 0,
                "href": f"{request.script_root}/plugins/{pid}/" if installed else "",
                "gone": not installed,
            }
        )
    titles = {w["plugin_id"]: w["title"] for w in who}
    went: list[dict[str, Any]] = []
    for (pid, name, model), got in summary["by_model"].items():
        went.append(
            {
                "widget": titles.get(pid, pid),
                "collection": name,
                "model": fal.label_of(model) if model else "unknown model",
                "each": fal.cents_of(model) if model else 0,
                "pictures": got["pictures"],
                "cents": got["cents"],
            }
        )
    went.sort(key=lambda r: -r["cents"])
    return who, went


@bp.get("/settings/genai")
def genai_index() -> str:
    store = settings_store()
    form = genai.for_admin(store)
    daily, monthly = genai.ceilings(store)
    summary = _ledger().summary()
    who, went = _rows(summary)
    env_key = fal.resolve_key({}) if not form["has_key"] else ""
    env_openai = (
        bool(os.environ.get("OPENAI_API_KEY", "").strip()) if not form["has_openai_key"] else False
    )
    return render_template(
        "settings_genai.html",
        active="genai",
        form=form,
        env_key=bool(env_key),
        env_openai=env_openai,
        checked_openai=request.args.get("checked_openai", ""),
        check_openai_ok=request.args.get("ok_openai") == "1",
        daily=daily,
        monthly=monthly,
        summary=summary,
        who=who,
        went=went,
        kept=sum(w["kept"] for w in who),
        commons_cache=commons.cache_size(),
        checked=request.args.get("checked", ""),
        check_ok=request.args.get("ok") == "1",
        dollars=genai.dollars,
    )


@bp.post("/settings/genai")
def genai_save() -> Response:
    genai.save(settings_store(), request.form)
    flash("Saved.", "ok")
    return redirect(url_for("auth.genai_index"))


@bp.post("/settings/genai/check")
def genai_check() -> Response:
    """Check the key as fal.ai sees it; the posted one when it is not the
    mask, else the stored one. Nothing is painted. A new key that fal.ai
    accepts is kept: nobody types a key to check it and then throw it away."""
    posted = str(request.form.get("api_key") or "").strip()
    fresh = bool(posted) and posted != SECRET_MASK
    key = posted if fresh else genai.settings(settings_store())["api_key"]
    if not key:
        key = fal.resolve_key({})
    ok, note = genai.check_key(key)
    if ok and fresh:
        genai.save(settings_store(), {"api_key": posted})
        note += " Saved."
    return redirect(url_for("auth.genai_index", checked=note, ok="1" if ok else "0"))


@bp.post("/settings/genai/check-openai")
def genai_check_openai() -> Response:
    """Check the OpenAI key by listing the account's models, which costs
    nothing; a new key OpenAI accepts is saved, as with the fal.ai key."""
    posted = str(request.form.get("openai_api_key") or "").strip()
    fresh = bool(posted) and posted != SECRET_MASK
    key = posted if fresh else openai_images.resolve_key()
    ok, note = openai_images.check_key(key)
    if ok and fresh:
        genai.save(settings_store(), {"openai_api_key": posted})
        note += " Saved."
    return redirect(url_for("auth.genai_index", checked_openai=note, ok_openai="1" if ok else "0"))


@bp.post("/settings/genai/reset")
def genai_reset() -> Response:
    _ledger().reset_today()
    flash("To-day's spend against the ceiling counts from now.", "ok")
    return redirect(url_for("auth.genai_index"))
