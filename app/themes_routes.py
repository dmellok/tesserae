"""Themes browse + (in later milestones) builder + extract endpoints.

This is the slim re-introduction of the pre-v0.17 theme system. M-A
ships the read-only browse page; M-B layers create / update / delete
over a user-themes store; M-C adds the image → palette extractor.

Lives under ``/themes`` (top-level, like ``/events``) but the Settings
tab nav surfaces it so users land here from the same place they manage
devices, renderers, and plugins.
"""

from __future__ import annotations

from flask import Blueprint, Flask, render_template

from app.state.theme_registry import (
    BUNDLED_THEMES,
    FAMILY_LABELS,
    FAMILY_ORDER,
    themes_by_family,
)

bp = Blueprint("themes", __name__, url_prefix="/themes")


@bp.get("")
def index() -> str:
    """Catalogue page: a card per theme grouped by family.

    Each card sets ``data-theme="<id>"`` on its root so the CSS cascade
    paints the inner swatches from the theme's own variables — no
    palette plumbing needed Python-side. M-B will append the user
    themes from the store; for now the registry is bundled-only.
    """
    themes = list(BUNDLED_THEMES)
    grouped = themes_by_family(themes)
    return render_template(
        "themes.html",
        active_area="themes",
        families=FAMILY_ORDER,
        family_labels=FAMILY_LABELS,
        grouped=grouped,
        total=len(themes),
    )


def register(app: Flask) -> None:
    app.register_blueprint(bp)
