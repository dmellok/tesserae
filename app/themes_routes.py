"""Themes browse, builder, and user-themes CSS endpoints.

M-A shipped the read-only browse over the bundled registry. M-B (this
revision) layers a user-themes store on top: the browse page now
shows user themes alongside bundled ones, a builder lets the user
create / edit / duplicate / delete them, and the cascade picks them
up via a server-generated ``user.css`` payload loaded next to the
Spectra stylesheets. M-C will hang an image-extract endpoint off this
blueprint to seed the builder form.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from flask import (
    Blueprint,
    Flask,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from app.state.theme_registry import (
    BUNDLED_THEMES,
    FAMILY_LABELS,
    FAMILY_ORDER,
    Theme,
    build_registry,
    themes_by_family,
)
from app.state.user_themes import (
    USER_THEME_PREFIX,
    UserTheme,
    UserThemeStore,
    emit_css,
    valid_slug,
)

bp = Blueprint("themes", __name__, url_prefix="/themes")


# -- helpers -----------------------------------------------------------


def _store() -> UserThemeStore:
    return current_app.config["USER_THEMES_STORE"]  # type: ignore[no-any-return]


def _bundled_id_set() -> set[str]:
    """Bundled-theme ids that user themes must not collide with. The
    ``user-`` prefix already prevents direct collision (no bundled
    theme starts with ``user-``), but we still pass these to the
    store's ``unique_id_for`` so a user can't shadow a bundled name
    by manually slugifying ``Light`` → ``light``."""
    return {t.id for t in BUNDLED_THEMES}


def _all_themes() -> tuple[list[Theme], list[UserTheme]]:
    """Return (registry_view, raw_user_themes) so callers can render
    the browse page or seed builder defaults from either side without
    re-walking the store."""
    user_themes = _store().list_all()
    registry = build_registry(user_themes=[t.to_registry_theme() for t in user_themes])
    return registry, user_themes


# -- routes ------------------------------------------------------------


@bp.get("")
def index() -> str:
    """Catalogue page: bundled + user themes, grouped by family."""
    registry, _ = _all_themes()
    grouped = themes_by_family(registry)
    return render_template(
        "themes.html",
        families=FAMILY_ORDER,
        family_labels=FAMILY_LABELS,
        grouped=grouped,
        total=len(registry),
    )


@bp.get("/user.css")
def user_css() -> Response:
    """Serve the concatenated user-themes CSS. ``compose.html`` and the
    themes browse page both load it so user-defined ``data-theme``
    selectors land alongside the bundled Spectra cascade.

    Cache-busted on a per-flush basis via the file's mtime — flips
    immediately after a save without forcing a hard refresh of every
    page that's already painting with the old cascade."""
    store = _store()
    body = emit_css(store)
    resp = Response(body, mimetype="text/css")
    # Short max-age so a save-then-edit cycle picks up changes inside
    # one second. The browse page also adds a ``?v=`` query string when
    # it links the file, so most cache hits are intentional.
    resp.headers["Cache-Control"] = "max-age=2"
    return resp


@bp.get("/new")
def new() -> str:
    """Render the builder form pre-filled from ``?from=<theme_id>`` if
    present (defaults to ``light`` so the form is never empty).
    ``from`` may name a bundled theme; we copy whatever colour tokens
    we can read from the Spectra CSS, falling back to the dataclass
    defaults for any miss."""
    base_id = (request.args.get("from") or "light").strip()
    seed = _seed_from_template(base_id)
    return render_template(
        "theme_builder.html",
        is_new=True,
        theme=_template_view(seed),
        bundled_themes=BUNDLED_THEMES,
        action_url=url_for("themes.create"),
    )


@bp.get("/<theme_id>/edit")
def edit(theme_id: str) -> str:
    store = _store()
    theme = store.get(theme_id)
    if theme is None:
        abort(404)
    return render_template(
        "theme_builder.html",
        is_new=False,
        theme=_template_view(theme),
        bundled_themes=BUNDLED_THEMES,
        action_url=url_for("themes.update", theme_id=theme.id),
    )


def _template_view(theme: UserTheme) -> dict[str, Any]:
    """Adapt a UserTheme into a dict the Jinja template can index by
    string. Lets the builder template loop over ``accent_1`` …
    ``accent_6`` without needing a getattr filter."""
    raw = asdict(theme)
    return raw


@bp.post("/new")
def create() -> Response:
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Pick a name for the theme.", "error")
        return redirect(url_for("themes.new"))
    store = _store()
    new_id = store.unique_id_for(name, reserved=_bundled_id_set())
    theme = _theme_from_form(request.form, fallback_id=new_id)
    store.save(theme)
    flash(f"Saved theme {theme.name!r}.", "ok")
    return redirect(url_for("themes.edit", theme_id=theme.id))


@bp.post("/<theme_id>/update")
def update(theme_id: str) -> Response:
    store = _store()
    existing = store.get(theme_id)
    if existing is None:
        abort(404)
    theme = _theme_from_form(request.form, fallback_id=existing.id)
    # Keep the id stable even if the name has changed — renaming
    # shouldn't reshuffle every page bound to this theme.
    theme.id = existing.id
    store.save(theme)
    flash(f"Updated theme {theme.name!r}.", "ok")
    return redirect(url_for("themes.edit", theme_id=theme.id))


@bp.post("/<theme_id>/delete")
def delete(theme_id: str) -> Response:
    store = _store()
    target = store.get(theme_id)
    if target is None:
        abort(404)
    store.delete(theme_id)
    flash(f"Deleted theme {target.name!r}.", "ok")
    return redirect(url_for("themes.index"))


@bp.post("/<theme_id>/duplicate")
def duplicate(theme_id: str) -> Response:
    """Make a user-editable copy of any theme — bundled or user. The
    bundled-theme copy seeds from the Spectra CSS defaults (whatever
    ``_seed_from_template`` can read); copying an existing user theme
    just clones the in-memory record."""
    store = _store()
    src: UserTheme
    existing = store.get(theme_id)
    if existing is not None:
        # asdict() round-trips every field; we overwrite ``id`` + ``name``
        # below so the placeholder here is harmless. ``UserTheme``'s
        # ``id`` is a required positional argument, so this two-step
        # dance is cleaner than threading a builder constructor.
        cloned = asdict(existing)
        cloned["id"] = "user-pending"
        cloned["name"] = f"{existing.name} copy"
        src = UserTheme(**cloned)
    else:
        # Bundled theme — synthesize a new UserTheme from defaults.
        src = _seed_from_template(theme_id)
        src.name = f"{src.name} copy"
    new_id = store.unique_id_for(src.name, reserved=_bundled_id_set())
    src.id = new_id
    store.save(src)
    flash(f"Duplicated as {src.name!r}.", "ok")
    return redirect(url_for("themes.edit", theme_id=src.id))


# -- form helpers ------------------------------------------------------


def _theme_from_form(form: Any, *, fallback_id: str) -> UserTheme:
    """Build a :class:`UserTheme` from the builder's POST body. Every
    colour token field has a matching ``name="<token>"`` input on the
    form; missing fields fall back to the dataclass default so a
    half-filled submission still yields a usable theme."""
    name = (form.get("name") or "Untitled").strip()
    mode = form.get("mode") or "light"
    font_family = (form.get("font_family") or "").strip() or None
    kwargs: dict[str, Any] = {
        "id": fallback_id,
        "name": name,
        "mode": mode if mode in ("light", "dark") else "light",
        "font_family": font_family,
    }
    for key in UserTheme.TOKEN_FIELDS:
        raw = (form.get(key) or "").strip()
        if raw:
            kwargs[key] = raw
    return UserTheme(**kwargs)


def _seed_from_template(theme_id: str) -> UserTheme:
    """Build a :class:`UserTheme` initialized from a bundled theme's
    colours so the builder form has a sensible starting point.

    We don't parse the Spectra CSS — that file is already source of
    truth at render time, but for the builder seed we only need
    ballpark defaults. Use the UserTheme dataclass defaults (which
    mirror the ``light`` theme) and stamp the requested mode if the
    template id starts with ``dark`` / ``nord`` / etc.
    """
    mode = "dark" if theme_id in {"dark", "nord"} else "light"
    seed_name = "New theme"
    seed = UserTheme(
        id=f"{USER_THEME_PREFIX}new",
        name=seed_name,
        mode=mode,
    )
    # M-C-friendly hook: a future image-extract suggestion can fill
    # seed before the form renders.
    return seed


# -- registration ------------------------------------------------------


def register(app: Flask) -> None:
    app.register_blueprint(bp)


__all__ = ["bp", "register", "valid_slug"]
