"""Themes index + builder + user-themes CSS endpoints + image extract.

M-A shipped the read-only browse over the bundled registry. M-B added
the user-themes store. M-C (this revision) collapses the browse + edit
pages into a single ``/themes`` view: a left strip lists every theme,
a right pane holds the builder form for whichever theme is selected,
and an image-upload section at the top of the builder seeds the form
from a k-means-extracted palette. Bundled themes show a read-only
form with a prominent Duplicate-to-edit CTA so they're never mutated
directly; user themes are editable + deletable.
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
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from app.palette_extract import (
    PaletteExtractError,
    assign_to_tokens,
    extract_dominant,
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
def index() -> Response | str:
    """Unified themes page: vertical strip + builder pane.

    Default selection prefers the user's first saved theme so a user
    iterating on their own work lands back where they were. Falls
    back to the bundled ``light`` theme on a fresh install.
    """
    registry, user_themes = _all_themes()
    default_id = user_themes[0].id if user_themes else "light"
    return _render_index(registry, default_id)


@bp.get("/<theme_id>")
def show(theme_id: str) -> Response | str:
    """Same unified view, with ``<theme_id>`` selected. Routes named
    ``new`` / ``user.css`` are declared before this so the catch-all
    doesn't shadow them."""
    if theme_id in {"new", "user.css", "extract-palette"}:
        abort(404)
    registry, _ = _all_themes()
    return _render_index(registry, theme_id)


# Legacy alias used by the M-B builder route and any external links
# that hit ``/themes/<id>/edit`` from the pre-unification UI. Redirect
# to the unified page so bookmarks keep working.
@bp.get("/<theme_id>/edit")
def edit_alias(theme_id: str) -> Response:
    return redirect(url_for("themes.show", theme_id=theme_id))


@bp.get("/user.css")
def user_css() -> Response:
    """Serve the concatenated user-themes CSS. ``compose.html`` and
    the themes page both load it so user-defined ``data-theme``
    selectors land alongside the bundled Spectra cascade.

    Short ``Cache-Control`` so a save-then-edit cycle picks up changes
    inside a second on subsequent loads."""
    store = _store()
    body = emit_css(store)
    resp = Response(body, mimetype="text/css")
    resp.headers["Cache-Control"] = "max-age=2"
    return resp


@bp.get("/new")
def new() -> Response | str:
    """Synonym for "show me the builder seeded for a fresh save."
    Renders the unified view with a placeholder ``new`` theme on the
    right; the strip on the left highlights nothing."""
    registry, _ = _all_themes()
    return _render_index(registry, selected_id=None, is_new=True)


def _render_index(
    registry: list[Theme],
    selected_id: str | None,
    *,
    is_new: bool = False,
) -> Response | str:
    """Shared renderer for the unified themes view. ``selected_id``
    drives the builder on the right; ``None`` (paired with ``is_new=True``)
    means "blank seed, save as new." A bad id 404s rather than silently
    falling back, so a typo'd link is visible."""
    grouped = themes_by_family(registry)
    bundled_ids = {t.id for t in BUNDLED_THEMES}

    is_bundled = False
    theme: UserTheme
    action_url: str

    if is_new or selected_id is None:
        theme = _seed_from_template("light")
        action_url = url_for("themes.create")
        is_new = True
    elif selected_id in bundled_ids:
        bundled = next(t for t in BUNDLED_THEMES if t.id == selected_id)
        theme = _seed_from_template(selected_id)
        # Reflect the chosen bundled id + name back so the preview
        # pane's data-theme attribute targets the right CSS block and
        # its header reads the bundled theme's actual name (instead of
        # the seed's placeholder "New theme").
        theme.id = selected_id
        theme.name = bundled.name
        is_bundled = True
        # Bundled themes can't be updated; the form's submit is
        # disabled, so action_url is harmless either way.
        action_url = url_for("themes.create")
    else:
        existing = _store().get(selected_id)
        if existing is None:
            abort(404)
        theme = existing
        action_url = url_for("themes.update", theme_id=existing.id)

    return render_template(
        "themes.html",
        families=FAMILY_ORDER,
        family_labels=FAMILY_LABELS,
        grouped=grouped,
        total=len(registry),
        selected_id=theme.id if not is_new else None,
        is_new=is_new,
        is_bundled=is_bundled,
        theme=_template_view(theme),
        bundled_themes=BUNDLED_THEMES,
        action_url=action_url,
    )


def _template_view(theme: UserTheme) -> dict[str, Any]:
    """Adapt a UserTheme into a dict the Jinja template can index by
    string. Lets the builder loop over ``accent_1`` … ``accent_6``
    without needing a getattr filter."""
    return asdict(theme)


@bp.post("/extract-palette")
def extract_palette() -> Response:
    """Take an uploaded image, return JSON with a Spectra palette
    suggestion. The builder posts the upload with ``fetch`` and
    paints the response into the form inputs client-side; no full
    reload, so the user can keep iterating without losing
    half-typed values.

    Returns 400 on a decode / size error with a friendly ``error``
    string the JS can surface as an inline message."""
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        return jsonify({"error": "Pick an image to extract from."}), 400
    raw = upload.read()
    try:
        colors = extract_dominant(raw)
    except PaletteExtractError as err:
        return jsonify({"error": str(err)}), 400
    mode_hint = request.form.get("mode") or None
    tokens = assign_to_tokens(colors, mode=mode_hint)
    # Include the detected mode so the JS can flip the mode dropdown
    # too — gives users the same "light vs dark" heuristic the
    # extractor itself used to assign tokens.
    detected_mode = "dark" if colors and colors[0].luminance < 0.4 else "light"
    return jsonify({"tokens": tokens, "mode": detected_mode})


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
    return redirect(url_for("themes.show", theme_id=theme.id))


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
    return redirect(url_for("themes.show", theme_id=theme.id))


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
    return redirect(url_for("themes.show", theme_id=src.id))


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
        # Checkbox-style boolean — present in form data only when the
        # box is checked. Persist the preference so toggling survives
        # reloads.
        "auto_soft_tints": form.get("auto_soft_tints") == "on",
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
