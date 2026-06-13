"""Theme catalogue.

Single source of truth for which themes Tesserae knows about. Each
entry mirrors a ``[data-theme="..."]`` block in
``static/style/spectra-tokens.css`` / the user-themes CSS endpoint, so
we don't end up with stale dropdowns referencing themes that have been
removed from the stylesheet or vice versa. A guard test in
``tests/test_theme_registry.py`` enforces both directions of the
invariant.

User themes don't live in this module; they come from
``app.state.user_themes`` via :func:`build_registry` at app-factory
time so the picker shows them alongside the bundled set. The visible
preview on the themes page works by setting ``data-theme="<id>"`` on
the card and letting the CSS cascade paint each swatch from the
relevant ``--bg`` / ``--surface`` / ``--accent-*`` variables, so there
are no hex codes to keep in lockstep here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ThemeFamily = Literal["light", "dark", "movement", "vivid", "gradient", "user"]


@dataclass(frozen=True)
class Theme:
    """A theme entry as seen by the picker and the browse page.

    ``id`` is the literal value of ``data-theme="..."``; the CSS cascade
    is keyed off it. ``family`` drives filtering on the browse page and
    is the only piece of taxonomy we keep, anything beyond that is
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


# Bundled themes, in the order they appear in the Spectra stylesheet -
# kept stable so the picker isn't reshuffled by alphabetic sorts. Add a
# new theme here AND in the matching CSS block; the guard test will
# fail loudly if you forget either half.
BUNDLED_THEMES: tuple[Theme, ...] = (
    # Light family, warm-undertone Spectra defaults.
    Theme(id="light", name="Light", family="light", tagline="warm paper"),
    Theme(id="sepia", name="Sepia", family="light", tagline="book paper"),
    Theme(id="cool-gray", name="Cool gray", family="light", tagline="neutral slate"),
    Theme(id="high-contrast", name="High contrast", family="light"),
    # B&W e-ink-ready themes: pure white canvas, no colour. Paper is
    # strict 1-bit (no greys); Newsprint admits a small greyscale
    # hierarchy for muted text + hairline edges so panels with grey
    # support get tonal depth and pure-B&W panels render the greys as
    # stipple texture.
    Theme(id="paper", name="Paper", family="light", tagline="1-bit ink"),
    Theme(id="newsprint", name="Newsprint", family="light", tagline="grey hierarchy"),
    # Vivid light variants, chunky bg → surface contrast + saturated accents.
    Theme(id="vivid-light", name="Vivid", family="light", tagline="bold paper"),
    Theme(id="citrus-light", name="Citrus", family="light", tagline="cream pop"),
    Theme(id="arctic-light", name="Arctic", family="light", tagline="cool jewel"),
    # Dark family.
    Theme(id="dark", name="Dark", family="dark", tagline="warm charcoal"),
    Theme(id="nord", name="Nord", family="dark", tagline="cool blue night"),
    # Movement themes, palettes for matching movement styles. Tagline
    # signals the colour-only nature so users pair with the same-named
    # Style for the full design-movement look.
    Theme(id="bauhaus", name="Bauhaus", family="movement", tagline="palette only"),
    Theme(id="destijl", name="De Stijl", family="movement", tagline="palette only"),
    Theme(id="brutalist", name="Brutalist", family="movement", tagline="palette only"),
    # Vivid family, saturated flat-colour surfaces. Distinct from the
    # gradient family (no gradient on .w, just bold canvas + accents).
    # Each theme's accents are picked to harmonise with its canvas
    # hue rather than reusing a shared palette.
    Theme(id="tangerine", name="Tangerine", family="vivid", tagline="bright orange"),
    Theme(id="lime", name="Lime", family="vivid", tagline="electric green"),
    Theme(id="cobalt", name="Cobalt", family="vivid", tagline="deep blue"),
    Theme(id="magenta", name="Magenta", family="vivid", tagline="vivid fuchsia"),
    Theme(id="emerald", name="Emerald", family="vivid", tagline="rich green"),
    Theme(id="crimson", name="Crimson", family="vivid", tagline="deep red"),
    Theme(id="cyan", name="Cyan", family="vivid", tagline="electric cyan"),
    Theme(id="aubergine", name="Aubergine", family="vivid", tagline="deep purple"),
    Theme(id="mustard", name="Mustard", family="vivid", tagline="bright yellow"),
    Theme(id="teal-pop", name="Teal Pop", family="vivid", tagline="saturated teal"),
    Theme(id="hot-pink", name="Hot Pink", family="vivid", tagline="bright pink"),
    Theme(id="lavender-pop", name="Lavender Pop", family="vivid", tagline="bright lavender"),
    Theme(id="olive-pop", name="Olive Pop", family="vivid", tagline="rich olive"),
    Theme(id="burgundy", name="Burgundy", family="vivid", tagline="deep wine"),
    Theme(id="forest", name="Forest", family="vivid", tagline="deep green"),
    # Gradient family, vivid linear-gradient card surfaces via the
    # --surface-gradient opt-in token (the rest of the Spectra system
    # behaves the same; only .w's background changes). The renderer's
    # Floyd-Steinberg dither approximates the gradient on the panel
    # palette; pairs especially well with 7-colour Spectra panels.
    Theme(id="sunset", name="Sunset", family="gradient", tagline="orange → amber"),
    Theme(id="aurora", name="Aurora", family="gradient", tagline="teal → magenta"),
    Theme(id="twilight", name="Twilight", family="gradient", tagline="violet night"),
    Theme(id="spectrum", name="Spectrum", family="gradient", tagline="full pop"),
    # Subtle gradient set, narrow hue range + lower saturation so the
    # gradient reads as a tonal shift rather than a colour shift. All
    # light-leaning; dark text throughout.
    Theme(id="coral", name="Coral", family="gradient", tagline="peach → blush"),
    Theme(id="mist", name="Mist", family="gradient", tagline="blue-gray → lavender"),
    Theme(id="sand", name="Sand", family="gradient", tagline="cream → taupe"),
    Theme(id="sage", name="Sage", family="gradient", tagline="moss → teal"),
    Theme(id="linen", name="Linen", family="gradient", tagline="cream → gold"),
    Theme(id="mauve", name="Mauve", family="gradient", tagline="rose → lavender"),
    Theme(id="marble", name="Marble", family="gradient", tagline="ivory → pale stone"),
    Theme(id="glacier", name="Glacier", family="gradient", tagline="teal → mint"),
    Theme(id="honey", name="Honey", family="gradient", tagline="butter → amber"),
    Theme(id="pearl", name="Pearl", family="gradient", tagline="blush → cream"),
)


FAMILY_LABELS: dict[ThemeFamily, str] = {
    "light": "Light",
    "dark": "Dark",
    "movement": "Movement",
    "vivid": "Vivid",
    "gradient": "Gradient",
    "user": "Your themes",
}


# Family display order on the browse page; mirrors how a hobbyist tends
# to scan a theme catalogue (familiar defaults first, then experimental,
# then user-curated).
FAMILY_ORDER: tuple[ThemeFamily, ...] = (
    "light",
    "dark",
    "movement",
    "vivid",
    "gradient",
    "user",
)


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
    "vivid": "Vivid themes",
    "gradient": "Gradient themes",
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


# -- bundled theme colour lookup --------------------------------------
#
# The Spectra CSS files are the source of truth for what each bundled
# theme actually paints. The builder needs those colours too, when a
# user clicks Duplicate on Nord we want the new user theme to carry
# Nord's actual ``bg`` / ``surface`` / ``accent-*`` values, not the
# UserTheme dataclass defaults (which mirror Light). Parsing the CSS
# at import time keeps that single source of truth and avoids having
# to hand-maintain a Python copy of every theme's palette.

_BLOCK_RE = re.compile(
    r'\[data-theme="([^"]+)"\]\s*(?:,\s*\[data-theme="[^"]+"\]\s*)*\{([^}]+)\}',
    re.DOTALL,
)
_VAR_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);")
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _parse_theme_blocks(css_text: str) -> dict[str, dict[str, str]]:
    """Pull every ``[data-theme="x"] { ... }`` block out of a Spectra
    stylesheet and return a ``{theme_id: {css_var_name: value}}`` map.
    Comments are stripped first so example syntax in the file header
    doesn't get scraped as a real rule. Values are returned verbatim
    (whitespace trimmed) so a colour like ``var(--text-primary)`` can
    be detected and resolved by the caller."""
    out: dict[str, dict[str, str]] = {}
    cleaned = _COMMENT_RE.sub("", css_text)
    for match in _BLOCK_RE.finditer(cleaned):
        theme_id, body = match.group(1), match.group(2)
        # A rule like ``:root, [data-theme="light"]{...}`` matches twice;
        # the regex still attributes the body to "light" because that's
        # the captured selector. Other comma-grouped selectors aren't
        # used by Spectra today but the loop handles them via update.
        variables: dict[str, str] = {}
        for var_match in _VAR_RE.finditer(body):
            variables[var_match.group(1)] = var_match.group(2).strip()
        if variables:
            out.setdefault(theme_id, {}).update(variables)
    return out


def _load_bundled_theme_colours() -> dict[str, dict[str, str]]:
    """Parse the Spectra stylesheets once at import time, return a
    ``{theme_id: {field_name: hex}}`` map keyed by the UserTheme
    dataclass field names (``--text-primary`` → ``text_primary``).

    Variables whose value is ``var(--other)`` (e.g. ``--icon:
    var(--text-primary)``) are resolved within the same theme block so
    the seed colours are concrete hex codes the builder form can
    populate inputs with. Unknown bundled themes simply yield an empty
    inner dict, and the caller falls back to dataclass defaults.
    """
    here = Path(__file__).resolve()
    css_dir = here.parent.parent.parent / "static" / "style"
    files = ("spectra-tokens.css",)
    raw: dict[str, dict[str, str]] = {}
    for fname in files:
        path = css_dir / fname
        if not path.is_file():
            continue
        raw.update(_parse_theme_blocks(path.read_text(encoding="utf-8")))

    out: dict[str, dict[str, str]] = {}
    for theme_id, variables in raw.items():
        # Resolve trivial ``var(--other)`` indirections within the same
        # block. One pass suffices because Spectra never chains more
        # than one level of indirection (``--icon: var(--text-primary)``
        # being the only case).
        resolved: dict[str, str] = {}
        for css_name, value in variables.items():
            if value.startswith("var(") and value.endswith(")"):
                ref = value[len("var(") : -1].split(",")[0].strip()
                value = variables.get(ref, value)
            field = css_name.removeprefix("--").replace("-", "_")
            resolved[field] = value
        out[theme_id] = resolved
    return out


_BUNDLED_THEME_COLOURS: dict[str, dict[str, str]] = _load_bundled_theme_colours()


def bundled_theme_colours(theme_id: str) -> dict[str, str]:
    """Return the bundled theme's parsed colour map keyed by UserTheme
    field names (``bg``, ``accent_1_soft``, etc.) or an empty dict for
    an unknown id. Caller blends the result with the dataclass
    defaults so missing tokens don't show up as empty inputs."""
    return dict(_BUNDLED_THEME_COLOURS.get(theme_id, {}))
