"""Persistence for Panels canvas documents (issue #60).

A canvas ``Panel`` (distinct from a device's physical panel) is a fixed-size
artboard holding absolutely-positioned visual ``Element`` s, each optionally
bound to a widget data field. This is the storage layer for the experimental
canvas editor; it mirrors :class:`app.state.page_store.PageStore` (whole-file
JSON, atomic rename, thread-locked) since these documents are small and
human-inspectable.

Kept deliberately separate from the grid ``Page`` model: canvas mode is
additive and opt-in, and its document shape (elements, not cells) is
different enough that folding it into ``Page`` would muddy both.

Field names are snake_case and shared verbatim with the editor's client-side
element objects, so there's no alias/mapping layer between the JSON on disk
and the JS in the browser.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.state.widget_update_schedule import WidgetUpdateSchedule


class PartScale(BaseModel):
    """A CSS-selector-scoped transform inside a widget fragment: scale the
    sub-element(s) matching ``sel`` (a class or id selector) to ``scale``
    percent. Lets the canvas nudge one piece of a rendered fragment, e.g. grow
    the hero number, without forking the widget. Applied as a ``transform:
    scale()`` injected into the widget's shadow root, both in the editor and the
    headless compose."""

    sel: str = ""
    scale: int = Field(default=100, ge=10, le=400)


class Binding(BaseModel):
    """A live data binding on an element: read ``field`` from ``source``'s data
    each render and map it through ``transform`` to patch element props (x / y /
    w / h / color / icon / text). This is what makes a *shape* reflect data,
    decorations are otherwise static geometry the renderer redraws but never
    recomputes. Evaluated server-side in the composer, in the same pass that
    resolves ``data`` elements, so a bound shape updates in lockstep with data
    primitives on every frame (no agent tick, survives push / rotation).

    ``transform`` is one of: ``position`` (scalar to a coordinate along a
    segment), ``length`` (scalar to a size), ``pick`` (integer index selects from
    arrays), ``color`` (scalar to a colour by thresholds), ``gradient`` (scalar
    interpolated smoothly along colour stops), ``icon`` (code/string to a Phosphor
    glyph). ``params`` is transform-specific; see :mod:`app.bindings`."""

    source: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
    field: str = ""
    transform: str = "position"
    params: dict[str, Any] = Field(default_factory=dict)


class CodeSource(BaseModel):
    """One named data source for a ``code`` element, exposed to the element's JS
    as ``ctx.data[name]``. A code element can list several, so its script can
    combine data from any number of sources (weather + calendar + transit, …).

    A source is one of two shapes:

    * **Widget / service** (``key`` set): a widget or ``service`` plugin id plus
      its ``options``, resolved through the normal plugin fetch. ``name``
      defaults to ``key`` when empty.
    * **URL** (``url`` set, ``key`` empty): a raw JSON HTTP(S) endpoint fetched
      server-side through the SSRF guard (:mod:`app.net_guard`) and delivered
      parsed. ``headers`` carries optional request headers (e.g. an
      ``Authorization`` token). Lets the agent wire an arbitrary API straight
      into a dashboard without a bespoke service plugin. ``name`` defaults to
      ``"data"`` when empty.

    Note: ``headers`` is stored in the page document, so treat any token here as
    visible to anyone the dashboard is exported/shared with."""

    key: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
    name: str = ""
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)


class CropInsets(BaseModel):
    """How much of a widget's rendered output to crop away from each edge, in
    percent of the box (widget-only). The kept rectangle is scaled to fill the
    box, so cropping the top drops a fixed title and the body reclaims the
    space, and cropping the bottom trims content to free room for another
    element. All zero = no crop. Capped at 90 an edge so at least a sliver of
    content always survives."""

    top: float = Field(default=0, ge=0, le=90)
    right: float = Field(default=0, ge=0, le=90)
    bottom: float = Field(default=0, ge=0, le=90)
    left: float = Field(default=0, ge=0, le=90)


class Element(BaseModel):
    """One placed element on the canvas: a widget instance rendered as one of
    its declared fragments, at an absolute box. ``z`` is implicit in list order
    (paint order, first painted first).

    Each element is its own widget instance with its own ``options``, so two
    elements can be the same widget configured differently (weather for two
    cities), and ``fragment`` selects which part of the widget paints.
    """

    id: str
    # What this element is: a placed widget, a decoration shape, a data-bound
    # primitive, a custom-HTML block, or an invisible touch hotspot.
    kind: str = "widget"  # widget | rect | ellipse | line | icon | text | data | html | svg | code | hotspot | button | switch | slider | stepper
    # Plugin id whose render() paints this element. Empty = an unassigned box
    # (placed but not yet pointed at a widget).
    widget: str = ""
    # Which declared fragment to paint; "full" is the whole widget. Passed to
    # the widget's render() as ``ctx.fragment``.
    fragment: str = "full"
    # Per-instance widget config, matching the widget's ``cell_options`` shape;
    # resolved via ``_resolved_options`` and handed to ``fetch()``.
    options: dict[str, Any] = Field(default_factory=dict)
    # Per-placement opt-in for manifest-declared ``updates.on_change`` events.
    # Kept on the element (not plugin settings) so two dashboards using the
    # same widget can choose independently. False is the compatibility default.
    update_on_change: bool = False
    # Optional host-owned refresh trigger for this widget placement. Static
    # elements cannot retain it; route-level capability guards enforce that.
    update_schedule: WidgetUpdateSchedule | None = None
    # Decoration props (kind != "widget"; ignored for widgets). ``color`` is a
    # CSS colour or a Spectra token (e.g. "var(--accent-1)") so decorations can
    # follow the theme. ``fill`` false = outlined, ``stroke`` = outline/line
    # thickness, ``radius`` = rect corner radius, ``icon`` = phosphor name.
    color: str = ""
    fill: bool = True
    stroke: int = Field(default=2, ge=0)
    radius: int = Field(default=0, ge=0)
    icon: str = ""
    # Phosphor icon weight for kind == "icon": thin|light|regular|bold|fill|duotone.
    weight: str = "bold"
    # Text-element (kind == "text") content, horizontal alignment, and font
    # size in px (0 = auto-size from the box height). ``text``/``align``/``size``
    # are shared by the ``data`` kind below.
    text: str = ""
    align: str = "left"
    size: int = Field(default=0, ge=0)
    # Data primitive (kind == "data"): binds a widget's data field to a scalable
    # text / number / graph. ``source`` is the widget id whose fetch() supplies
    # the data (its config lives in ``options``); ``field`` is a dotted path into
    # that data (e.g. "current.temp" or "hourly.temps"); ``display`` is one of
    # text | number | line | bar | sparkline; ``unit`` is a suffix, ``precision``
    # the decimals for numbers, ``label`` an optional caption.
    source: str = ""
    field: str = ""
    display: str = "text"
    unit: str = ""
    precision: int = Field(default=0, ge=0)
    label: str = ""
    # Optional value formatter for text/number data primitives: "relative", a date
    # pattern (e.g. "HH:mm", "MMM d", "ddd HH:mm"), or a number pattern ("0.0").
    format: str = ""
    # Custom HTML element (kind == "html") or SVG element (kind == "svg"): agent-
    # or user-authored markup that renders in a sandboxed iframe (no scripts, no
    # network). ``html`` holds the markup (the raw <svg> for kind "svg"); ``css``
    # is injected into the iframe's <style>.
    #
    # kind == "code" reuses ``html`` + ``css`` and adds ``js`` for author
    # JavaScript, fed by any number of widgets' live data via ``sources`` below:
    # each named source's resolved data is injected as ``ctx.data[name]`` into a
    # scripts-enabled but origin-less, network-blocked sandbox (see ``renderCode``
    # in decorate.js), so the JS builds the DOM from real widget data without the
    # data ever passing through the network from inside the frame. Runs once at
    # render (e-ink is static), same lifecycle as a widget.
    html: str = ""
    css: str = ""
    # Author JavaScript for kind == "code". Runs inside the sandboxed iframe with
    # the widget data available as ``ctx.data[name]`` per source (and
    # ``ctx.options`` / ``ctx.w`` / ``ctx.h``). Empty for every other kind.
    js: str = ""
    # Vendored-library auto-injection for kind == "code". The sandbox infers
    # which bundles (Chart.js, Phosphor, a named font, …) to inline from the
    # element's own html/css/js. Set false to inject NOTHING: an element that
    # hand-authors its markup and styling gets no ambient stylesheets, and no
    # heuristic can restyle what it draws. render_report's ``injected_libs``
    # says what the inference chose and which token triggered it.
    autolibs: bool = True
    # Named data sources for kind == "code": each ``{key, options, name}`` is a
    # widget whose fetched data lands at ``ctx.data[name]`` (name falls back to
    # key). Lets one code element combine data from several widgets. A legacy
    # bare ``source`` / ``options`` (single-source form) is honoured as one more
    # source keyed by the widget id.
    sources: list[CodeSource] = Field(default_factory=list)
    # Element opacity 0-100 (applies to widgets and decorations alike).
    opacity: int = Field(default=100, ge=0, le=100)
    # Rotation in degrees, applied to the whole element around its centre.
    rotate: int = 0
    # Position may be negative / past the panel so an element can sit partly
    # off-canvas; the artboard clips at the panel edge. Size stays positive.
    x: int = 0
    y: int = 0
    w: int = Field(default=1, gt=0)
    h: int = Field(default=1, gt=0)
    # Per-sub-element scale overrides for a widget fragment (widget-only): each
    # scales the CSS selector it names inside the rendered shadow root.
    parts: list[PartScale] = Field(default_factory=list)
    # Crop insets (widget-only): trim the rendered output at each edge and let
    # the kept region fill the box. Composes with the ``.w`` content scale.
    crop: CropInsets = Field(default_factory=CropInsets)
    # Per-element dither opt-out (issue #86): flat packs nearest-colour.
    dither: bool = True
    visible: bool = True
    locked: bool = False
    group: str | None = None
    # Live data bindings (issue: shapes don't auto-update). Each entry reads a
    # widget field and patches this element's props every render; several may
    # combine (e.g. bind x by position AND colour by threshold). Empty for a
    # static element. See :class:`Binding` and :mod:`app.bindings`.
    bind: list[Binding] = Field(default_factory=list)
    # Touch actions (issue #49). ``on_tap`` is an action spec in the
    # ``button_actions`` string grammar (``refresh`` / ``page:<id>`` /
    # ``webhook:<url>`` / …; the dict form is reserved for structured actions).
    # ``on_swipe`` maps directions (up/down/left/right) to specs. The composer
    # stamps these as ``data-on-tap`` / ``data-on-swipe`` on the element's
    # container, so the render-time extractor picks the element's box up as a
    # touch region. The dedicated ``hotspot`` kind is an invisible element that
    # exists only to carry these. Empty = not tappable.
    on_tap: str | dict[str, Any] | None = None
    # Direction (up/down/left/right) → action spec. Each value is a grammar
    # string ("rotate_next") or a structured HA object ({"action":"ha",…}),
    # the same forms on_tap accepts, so a swipe can fire a service call.
    on_swipe: dict[str, str | dict[str, Any]] | None = None
    # Slider gesture (phase 3): ``{"axis": "x"|"y", "action": <spec>}``.
    # A slider region absorbs every stroke; the end point maps to a 0-100
    # value along the axis and substitutes ``{value}`` placeholders in
    # the action (a structured HA call or a string spec). Vertical
    # sliders fill upward (top = 100).
    on_slide: dict[str, Any] | None = None
    # Named actions for kind == "code" (mirrors ``sources``: sources are data
    # in, actions are touches out). The element's markup references them as
    # ``data-on-tap="@name"`` (whole-attribute references), so structured
    # actions stay in validated config and never inline in markup. Values use
    # the same spec forms as ``on_tap`` (string grammar, or a direction map for
    # ``data-on-swipe="@name"``).
    actions: dict[str, Any] = Field(default_factory=dict)
    # --- Touch v3 typed primitives (device-owned touch; kind == button | switch
    # | slider | stepper). The firmware draws these from the served spec and owns
    # interaction; see notes/design-handoffs/touch-v3/. ``value_key`` binds a
    # switch/slider/stepper to an entity ("ha:light.desk"); ``state`` seeds a
    # switch ("on"/"off"); ``axis`` is the slider fill direction ("x"|"y");
    # ``value_min``/``value_max``/``value_step``/``value_now`` are the
    # slider/stepper numeric range and seeded value. A button's action is its
    # ``on_tap`` spec (reused); switch/slider/stepper derive their action from
    # ``value_key`` when the wire spec is built. Left lenient here (an in-progress
    # primitive may lack ``value_key``); required fields are enforced at
    # spec-build time. Ignored for every other kind.
    value_key: str = ""
    state: str = ""
    axis: str = ""
    value_min: float = 0.0
    value_max: float = 100.0
    value_step: float = 1.0
    value_now: float = 0.0

    @model_validator(mode="after")
    def _canonicalize_touch(self) -> Element:
        """Store touch actions in their canonical, dispatchable form so the
        canvas editor's Interaction panel can decode them (it recognises
        ``{"action":"ha",…}``, not the flat ``{"service":…}`` an agent may
        write) and dispatch sees the same shape. Runs on every load too, so
        legacy dashboards self-heal (issue #49)."""
        from app.touch_regions import canonical_action, canonical_slide, canonical_swipe

        object.__setattr__(self, "on_tap", canonical_action(self.on_tap))
        object.__setattr__(self, "on_swipe", canonical_swipe(self.on_swipe))
        object.__setattr__(self, "on_slide", canonical_slide(self.on_slide))
        if self.actions:
            object.__setattr__(
                self, "actions", {k: canonical_action(v) for k, v in self.actions.items()}
            )
        return self


class ConfigInputTarget(BaseModel):
    """Where one config input's value lands inside the canvas.

    Same shape the template format uses (``schema/template.schema.json``), so a
    dashboard's local config surface and the questions its catalog listing asks
    are the same declaration rather than two parallel ones.
    """

    el: str
    # options | source_options | source_header | source_url | bind_options.
    # Validated against the applier's known slots rather than an enum here, so
    # a doc written by a newer build round-trips instead of failing to load.
    slot: str = "options"
    index: int | None = Field(default=None, ge=0)
    key: str = ""


class ConfigInput(BaseModel):
    """One question a dashboard asks whoever is setting it up.

    An agent building a canvas declares these as it goes, so an MCP-authored
    dashboard arrives with a config surface instead of forcing whoever inherits
    it to hunt through per-element drawers for the one URL they need to change
    (#237). Declared inputs are also what the Share flow offers, so what the
    author configured locally is what an installer is asked.
    """

    name: str = Field(pattern=r"^[a-z0-9_]{1,32}$")
    label: str = Field(min_length=1, max_length=80)
    type: str = "string"
    secret: bool = False
    # Whether the field is masked in the config form. Defaults to ``secret``
    # via :meth:`masked`, matching the cell-option contract, so a URL can be
    # sensitive without being unreadable.
    mask: bool | None = None
    required: bool = False
    default: Any = ""
    help: str = ""
    choices: list[dict[str, Any]] = Field(default_factory=list)
    targets: list[ConfigInputTarget] = Field(default_factory=list, max_length=20)

    def masked(self) -> bool:
        return self.secret if self.mask is None else self.mask


class CanvasLayout(BaseModel):
    """The freeform-canvas layout carried by a ``Page`` whose ``layout_kind`` is
    ``"canvas"`` (issue #60). Same shape as :class:`CanvasPage` minus the
    dashboard-level fields (id / name / device_ids), which live on the Page so a
    canvas behaves 1:1 with a grid page across scheduling, rotation, binding, and
    history."""

    # Authored artboard size. The render scales this to the target panel.
    w: int = Field(default=600, gt=0)
    h: int = Field(default=400, gt=0)
    # Canvas-wide appearance. theme = Spectra colour palette (data-theme),
    # style = typographic axis (data-style), font = font plugin id (blank =
    # default), bg = background colour override (blank = the theme's --bg).
    theme: str = "light"
    style: str = "standard"
    font: str = ""
    bg: str = ""
    # Optional background image (URL) behind all elements, and how it fits.
    bg_image: str = ""
    bg_fit: str = "cover"  # cover | contain | stretch
    els: list[Element] = Field(default_factory=list)
    # The dashboard's own config surface. Not part of the render payload; the
    # renderer ignores it and reads the resolved element options as before.
    inputs: list[ConfigInput] = Field(default_factory=list, max_length=20)


class CanvasPage(BaseModel):
    """A canvas document: a fixed artboard plus its freely-placed elements.

    Legacy standalone store (``PANEL_STORE``); canvases are migrating to
    ``Page(layout_kind="canvas")``. Kept for reading old data + the experimental
    editor until the migration lands.
    """

    id: str
    name: str = "Untitled Panel"
    w: int = Field(default=600, gt=0)
    h: int = Field(default=400, gt=0)
    theme: str = "light"
    style: str = "standard"
    font: str = ""
    bg: str = ""
    bg_image: str = ""
    bg_fit: str = "cover"  # cover | contain | stretch
    # Device instances this canvas is sent to.
    device_ids: list[str] = Field(default_factory=list)
    els: list[Element] = Field(default_factory=list)
    inputs: list[ConfigInput] = Field(default_factory=list, max_length=20)

    def to_layout(self) -> CanvasLayout:
        """The render payload for this canvas (drops id/name/device_ids)."""
        return CanvasLayout(
            w=self.w,
            h=self.h,
            theme=self.theme,
            style=self.style,
            font=self.font,
            bg=self.bg,
            bg_image=self.bg_image,
            bg_fit=self.bg_fit,
            els=list(self.els),
            inputs=list(self.inputs),
        )


class CanvasStore:
    """Thread-safe, file-backed dict of canvas documents. Whole-file JSON
    with an atomic rename, mirroring :class:`PageStore`."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._docs: dict[str, CanvasPage] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{self._path} must contain a JSON object keyed by canvas id")
        self._docs = {cid: CanvasPage.model_validate(doc) for cid, doc in raw.items()}

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload: dict[str, Any] = {
            cid: doc.model_dump(mode="json") for cid, doc in self._docs.items()
        }
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, canvas_id: str) -> CanvasPage | None:
        with self._lock:
            return self._docs.get(canvas_id)

    def list(self) -> list[CanvasPage]:
        with self._lock:
            return list(self._docs.values())

    def save(self, doc: CanvasPage) -> None:
        with self._lock:
            self._docs[doc.id] = doc
            self._flush()

    def delete(self, canvas_id: str) -> bool:
        with self._lock:
            existed = self._docs.pop(canvas_id, None) is not None
            if existed:
                self._flush()
        return existed

    def __len__(self) -> int:
        with self._lock:
            return len(self._docs)
