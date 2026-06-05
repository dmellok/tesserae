"""Theme catalogue.

Single source of truth for which themes Tesserae knows about. Each
entry mirrors a ``[data-theme="..."]`` block in
``static/style/spectra-tokens.css`` / ``spectra-base16.css`` / (later)
the user-themes CSS endpoint, so we don't end up with stale dropdowns
referencing themes that have been removed from the stylesheet or vice
versa. A guard test in ``tests/test_theme_registry.py`` enforces both
directions of the invariant.

User themes don't live in this module; they come from
``app.state.user_themes`` via :func:`build_registry` at app-factory
time so the picker shows them alongside the bundled set. The visible
preview on the themes page works by setting ``data-theme="<id>"`` on
the card and letting the CSS cascade paint each swatch from the
relevant ``--bg`` / ``--surface`` / ``--accent-*`` variables, so there
are no hex codes to keep in lockstep here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ThemeFamily = Literal["light", "dark", "movement", "base16", "user"]


@dataclass(frozen=True)
class Theme:
    """A theme entry as seen by the picker and the browse page.

    ``id`` is the literal value of ``data-theme="..."``; the CSS cascade
    is keyed off it. ``family`` drives filtering on the browse page and
    is the only piece of taxonomy we keep — anything beyond that is
    expressible by reading the CSS variables off a probe element.

    ``tagline`` is the short flavour line shown beneath the name in
    pickers (e.g. ``"warm paper"`` for Light); the browse page hides it
    on the card head because the swatches already do that work. None
    when there's nothing useful to add.

    ``user`` is True when the theme came from the user-themes store
    (filled in by :func:`build_registry` rather than declared statically);
    it gates the "delete" / "edit" buttons.
    """

    id: str
    name: str
    family: ThemeFamily
    tagline: str | None = None
    user: bool = False

    def picker_label(self) -> str:
        """Format for ``<option>`` text in the page editor's theme picker.

        Falls back to just the name when there's no tagline so a
        registry-side change to a theme's tagline doesn't move existing
        dropdown values around."""
        if self.tagline:
            return f"{self.name} · {self.tagline}"
        return self.name


# Bundled themes, in the order they appear in the Spectra stylesheet —
# kept stable so the picker isn't reshuffled by alphabetic sorts. Add a
# new theme here AND in the matching CSS block; the guard test will
# fail loudly if you forget either half.
BUNDLED_THEMES: tuple[Theme, ...] = (
    # Light family — warm-undertone Spectra defaults.
    Theme(id="light", name="Light", family="light", tagline="warm paper"),
    Theme(id="sepia", name="Sepia", family="light", tagline="book paper"),
    Theme(id="cool-gray", name="Cool gray", family="light", tagline="neutral slate"),
    Theme(id="high-contrast", name="High contrast", family="light"),
    # Dark family.
    Theme(id="dark", name="Dark", family="dark", tagline="warm charcoal"),
    Theme(id="nord", name="Nord", family="dark", tagline="cool blue night"),
    # Movement themes — palettes for matching movement styles. Tagline
    # signals the colour-only nature so users pair with the same-named
    # Style for the full design-movement look.
    Theme(id="bauhaus", name="Bauhaus", family="movement", tagline="palette only"),
    Theme(id="destijl", name="De Stijl", family="movement", tagline="palette only"),
    Theme(id="brutalist", name="Brutalist", family="movement", tagline="palette only"),
    # base16 themes — popular code-editor palettes adapted for dashboards.
    Theme(id="base16-gruvbox-dark", name="Gruvbox", family="base16", tagline="dark"),
    Theme(id="base16-gruvbox-light", name="Gruvbox", family="base16", tagline="light"),
    Theme(id="base16-solarized-dark", name="Solarized", family="base16", tagline="dark"),
    Theme(id="base16-solarized-light", name="Solarized", family="base16", tagline="light"),
    Theme(id="base16-dracula", name="Dracula", family="base16"),
    Theme(id="base16-catppuccin-mocha", name="Catppuccin Mocha", family="base16"),
    Theme(id="base16-monokai", name="Monokai", family="base16"),
    Theme(id="base16-tomorrow-night", name="Tomorrow Night", family="base16"),
    Theme(id="base16-tomorrow", name="Tomorrow", family="base16"),
    Theme(id="base16-one-dark", name="One Dark", family="base16"),
)


FAMILY_LABELS: dict[ThemeFamily, str] = {
    "light": "Light",
    "dark": "Dark",
    "movement": "Movement",
    "base16": "base16",
    "user": "Your themes",
}


# Family display order on the browse page; mirrors how a hobbyist tends
# to scan a theme catalogue (familiar defaults first, then experimental,
# then user-curated).
FAMILY_ORDER: tuple[ThemeFamily, ...] = ("light", "dark", "movement", "base16", "user")


def build_registry(user_themes: list[Theme] | None = None) -> list[Theme]:
    """Bundled themes + any user-saved ones, in family-then-declared order.

    ``user_themes`` is supplied at request time by the routes layer
    (which holds the ``UserThemeStore``) so this module stays free of
    file-I/O and easy to import from the page-editor template helpers.
    """
    out: list[Theme] = list(BUNDLED_THEMES)
    if user_themes:
        out.extend(user_themes)
    return out


def themes_by_family(themes: list[Theme]) -> dict[ThemeFamily, list[Theme]]:
    """Group a theme list by family, preserving each family's input order."""
    out: dict[ThemeFamily, list[Theme]] = {f: [] for f in FAMILY_ORDER}
    for t in themes:
        out.setdefault(t.family, []).append(t)
    return out


# Family → optgroup label as it appears in the picker. Slightly tighter
# than FAMILY_LABELS (which is for the browse page) so the dropdown
# stays compact: "Light themes" reads cleaner mid-picker than the
# bare "Light".
_PICKER_GROUP_LABELS: dict[ThemeFamily, str] = {
    "light": "Light themes",
    "dark": "Dark themes",
    "movement": "Movement themes",
    "base16": "base16",
    "user": "Your themes",
}


def picker_options(themes: list[Theme]) -> list[dict[str, str]]:
    """Shape themes for the page editor's ``select_field`` macro.

    Each option dict has ``value`` (theme id), ``label``
    (picker_label), and ``group`` (family bucket). Order is preserved
    so the picker is byte-identical to the previous hardcoded list
    while the data flows from a single source. Replaces the duplicated
    cell + page theme option blocks in ``templates/page_editor.html``.
    """
    return [
        {
            "value": t.id,
            "label": t.picker_label(),
            "group": _PICKER_GROUP_LABELS.get(t.family, t.family),
        }
        for t in themes
    ]
