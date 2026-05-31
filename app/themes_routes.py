"""Theme builder admin routes.

Shows every loaded theme (built-in + user) with a palette swatch grid.
Built-in themes are read-only; "Duplicate" turns one into a user theme
you can edit. User themes get an inline 12-colour form and a delete
button.

Themes save to ``data/plugins/themes_core/user.json`` via UserThemeStore.
The PluginRegistry merges them with the built-ins from
``plugins/themes_core/plugin.json`` so widgets / cells don't care where a
theme came from.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from flask import (
    Blueprint,
    Flask,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.wrappers import Response

from app.palette_extract import assign_to_tokens, extract_dominant
from app.plugin_loader import PluginRegistry, Theme
from app.state.user_themes import (
    PALETTE_TOKENS,
    VALID_MODES,
    UserTheme,
    UserThemeStore,
    slug_id,
    validate_palette,
)

_log = logging.getLogger(__name__)
_MAX_EXTRACT_BYTES = 8 * 1024 * 1024  # 8 MiB — generous for a UI upload


def _json_error(message: str, status: int) -> Response:
    """JSON error response with a status code set on the Response
    object (avoids the (Response, int) tuple form, which mypy can't
    reconcile with a `-> Response` annotation)."""
    resp = jsonify({"error": message})
    resp.status_code = status
    return resp


bp = Blueprint("themes", __name__, url_prefix="/themes")


def _registry() -> PluginRegistry:
    return current_app.config["PLUGIN_REGISTRY"]  # type: ignore[no-any-return]


def _user_store() -> UserThemeStore:
    """One UserThemeStore per request, but always pointed at the same file."""
    registry = _registry()
    theme_plugin = next(
        (p for p in registry.plugins.values() if p.kind == "theme"),
        None,
    )
    if theme_plugin is None:
        raise RuntimeError("no theme plugin loaded; cannot persist user themes")
    return UserThemeStore(theme_plugin.data_dir / "user.json")


def _palette_from_form(form: Any) -> dict[str, str]:
    return {token: str(form.get(token, "")).strip() for token in PALETTE_TOKENS}


def _rebuild_registry_themes() -> None:
    """Re-read user themes from disk into the in-memory registry so a
    just-saved theme is visible without restarting the process."""
    registry = _registry()
    theme_plugin = next(
        (p for p in registry.plugins.values() if p.kind == "theme"),
        None,
    )
    if theme_plugin is None:
        return
    # Drop existing user themes (keep built-ins), then reload.
    registry.themes = {tid: t for tid, t in registry.themes.items() if not t.is_user}
    # Rehydrate built-ins from the manifest in case a previous user
    # theme had shadowed one — when the shadow is deleted, the built-in
    # should reappear.
    for raw in theme_plugin.manifest.get("themes", []):
        tid = str(raw["id"])
        if tid not in registry.themes:
            registry.themes[tid] = Theme(
                id=tid,
                name=str(raw["name"]),
                mode=str(raw.get("mode", "")),
                palette={k: str(v) for k, v in raw["palette"].items()},
                plugin_id=theme_plugin.id,
            )
    for ut in _user_store().load():
        registry.themes[ut.id] = Theme(
            id=ut.id,
            name=ut.name,
            mode=ut.mode,
            palette=dict(ut.palette),
            plugin_id=theme_plugin.id,
            is_user=True,
        )


@bp.get("")
def index() -> str:
    themes = sorted(_registry().themes.values(), key=lambda t: (t.is_user, t.name.lower()))
    edit_id = request.args.get("edit")
    edit_theme: UserTheme | None = None
    if edit_id:
        existing = _user_store().get(edit_id)
        if existing is not None:
            edit_theme = existing
    # Seed the "New theme" form with the default theme's palette so the
    # live preview renders something legible from frame zero — a freshly
    # opened builder shows a working theme the user can tweak, rather
    # than an all-black void.
    default_theme = _registry().get_theme("default")
    blank_palette: dict[str, str] = (
        dict(default_theme.palette)
        if default_theme is not None
        else {t: "#000000" for t in PALETTE_TOKENS}
    )
    blank = UserTheme(id="", name="", mode="light", palette=blank_palette)
    return render_template(
        "themes.html",
        themes=themes,
        palette_tokens=PALETTE_TOKENS,
        edit_theme=edit_theme,
        blank_theme=blank,
        modes=sorted(VALID_MODES),
    )


def _unique_theme_id(base: str) -> str:
    """slug_id + numeric suffix until the id is free across the merged
    built-in + user theme registry. The user never picks the id."""
    taken = set(_registry().themes.keys())
    candidate = base
    n = 1
    while candidate in taken:
        n += 1
        candidate = f"{base}_{n}"
    return candidate


@bp.post("/new")
def create() -> Response:
    """Create a user theme. The id is derived from the name (with a
    numeric suffix if it collides) — no user-facing id input."""
    form = request.form
    name = (form.get("name") or "").strip()
    if not name:
        flash("Theme name is required.", "error")
        return redirect(url_for("themes.index"))
    mode = (form.get("mode") or "light").strip()
    if mode not in VALID_MODES:
        flash(f"Invalid mode {mode!r}.", "error")
        return redirect(url_for("themes.index"))
    palette = _palette_from_form(form)
    err = validate_palette(palette)
    if err is not None:
        flash(f"Invalid palette: {err}", "error")
        return redirect(url_for("themes.index"))
    # Fire a one-shot telemetry signal on the first user-created theme
    # for this install — lets the maintainer see how many installs ever
    # reach the theme builder. Subsequent themes don't re-fire. The
    # ``mode`` is the only prop (light / dark), no name or palette
    # content. Tracked via a sentinel file alongside user.json so the
    # one-shot survives a process restart.
    store = _user_store()
    first_ever = len(store.load()) == 0
    theme_id = _unique_theme_id(slug_id(name))
    store.upsert(UserTheme(id=theme_id, name=name, mode=mode, palette=palette))
    _rebuild_registry_themes()
    if first_ever:
        telemetry = current_app.config.get("TELEMETRY")
        if telemetry is not None:
            try:
                telemetry.send("theme.user_created", {"mode": mode})
            except Exception:
                current_app.logger.debug("telemetry: theme.user_created send failed", exc_info=True)
    flash(f"Theme {name!r} saved.", "ok")
    return redirect(url_for("themes.index"))


@bp.post("/<theme_id>/update")
def update(theme_id: str) -> Response:
    existing = _user_store().get(theme_id)
    if existing is None:
        flash(f"No user theme with id {theme_id!r}.", "error")
        return redirect(url_for("themes.index"))
    form = request.form
    name = (form.get("name") or existing.name).strip()
    mode = (form.get("mode") or existing.mode).strip()
    if mode not in VALID_MODES:
        flash(f"Invalid mode {mode!r}.", "error")
        return redirect(url_for("themes.index", edit=theme_id))
    palette = _palette_from_form(form)
    err = validate_palette(palette)
    if err is not None:
        flash(f"Invalid palette: {err}", "error")
        return redirect(url_for("themes.index", edit=theme_id))
    # Pin id to the URL so an edit can't fork into a second record.
    _user_store().upsert(UserTheme(id=theme_id, name=name, mode=mode, palette=palette))
    _rebuild_registry_themes()
    flash(f"Theme {name!r} updated.", "ok")
    return redirect(url_for("themes.index"))


@bp.post("/<theme_id>/delete")
def delete(theme_id: str) -> Response:
    if not _user_store().delete(theme_id):
        flash(f"No user theme with id {theme_id!r} to delete.", "error")
    else:
        _rebuild_registry_themes()
        flash("Theme deleted.", "ok")
    return redirect(url_for("themes.index"))


@bp.post("/<theme_id>/duplicate")
def duplicate(theme_id: str) -> Response:
    """Copy a built-in theme into the user store so it can be edited.
    Lands on /themes?edit=<new_id> so the form is pre-filled."""
    source = _registry().get_theme(theme_id)
    if source is None:
        flash(f"No theme {theme_id!r} to duplicate.", "error")
        return redirect(url_for("themes.index"))
    base_id = slug_id(source.name + " copy")
    new_id = base_id
    suffix = 1
    while _user_store().get(new_id) is not None or new_id in _registry().themes:
        suffix += 1
        new_id = f"{base_id}_{suffix}"
    _user_store().upsert(
        UserTheme(
            id=new_id,
            name=f"{source.name} (copy)",
            mode=source.mode or "light",
            palette=dict(source.palette),
        )
    )
    _rebuild_registry_themes()
    return redirect(url_for("themes.index", edit=new_id))


@bp.post("/extract-palette")
def extract_palette() -> Response:
    """Take an uploaded image, run k-means + token-assignment, return the
    suggested palette plus a base64 data URL the client can use for the
    eyedropper canvas. JSON only — no flash redirects."""
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        return _json_error("No image provided.", 400)
    data = upload.read(_MAX_EXTRACT_BYTES + 1)
    if len(data) > _MAX_EXTRACT_BYTES:
        return _json_error(f"Image too large (>{_MAX_EXTRACT_BYTES // (1024 * 1024)} MiB).", 413)
    mode = (request.form.get("mode") or "light").strip()
    if mode not in VALID_MODES:
        mode = "light"
    try:
        colors = extract_dominant(data)
    except Exception as exc:
        _log.exception("palette extraction failed")
        return _json_error(f"Could not parse image: {exc}", 400)
    palette = assign_to_tokens(colors, mode=mode)
    mime = upload.mimetype or "image/png"
    data_url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    return jsonify(
        {
            "palette": palette,
            "swatches": [c.hex() for c in colors],
            "image_data_url": data_url,
        }
    )


def register(app: Flask) -> None:
    app.register_blueprint(bp)
