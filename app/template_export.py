"""Build a shareable template from a canvas dashboard (issue: template marketplace).

The exporter's contract: nothing install-specific and nothing secret leaves the
machine. It starts from the page's :class:`CanvasLayout`, then

* strips ``device_ids`` (by construction: only the layout is exported) and
  every ``CodeSource.headers`` entry, converting each into a suggested
  ``secret`` input targeting that slot;
* redacts option values whose ``cell_options`` schema marks them
  ``secret: true`` (e.g. ``rest_service`` url/headers), likewise suggesting
  inputs;
* suggests non-secret inputs for install-specific values (Home Assistant
  entity ids, locations) so a template asks its installer instead of leaking
  the author's home;
* inlines a page-asset background image as a ``data:`` URI (small ones) or
  blocks the export;
* collects ``requires[]`` (marketplace catalog ids for non-bundled widgets and
  themes) so install can offer the missing pieces;
* runs the credential lint (:mod:`app.template_lint`, duplicated on the API)
  and blocks on errors.

``apply_inputs`` is the other half of the contract: the installer (and the
server-side rebuild) patch input values into the doc through the same
``targets`` vocabulary, so a round-trip is testable end to end.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from app import template_lint
from app.state.page_store import Page

# bg_image inline cap. Larger backgrounds must be re-hosted by the author.
MAX_INLINE_ASSET_BYTES = 262_144

# Option names that are install-specific rather than secret: the exporter
# suggests a (non-secret) input so the installer supplies their own.
_INSTALL_SPECIFIC_OPTION_NAMES = {"entity", "entity_id", "entities", "location", "calendar_url"}

TEMPLATE_SCHEMA_VERSION = 1

# Option names that hold a human-facing label for an element. Used to tell one
# element's questions apart from another's on the install form: a dashboard
# with three sensor tiles otherwise asks "Entities" three times with no way to
# know which tile each answer lands on.
_CONTEXT_OPTION_NAMES = ("title", "label", "name", "heading", "caption")


def _element_context(element: Any, canvas_w: int, canvas_h: int) -> tuple[str, str]:
    """(name, position) describing an element to a human.

    ``name`` is the element's own title/label if it has one, else "". Position
    is a coarse ninth of the canvas ("top left"), which is enough to find the
    tile being asked about when it has no title of its own."""
    options = getattr(element, "options", None) or {}
    name = ""
    for key in _CONTEXT_OPTION_NAMES:
        value = options.get(key)
        if isinstance(value, str) and value.strip():
            name = value.strip()[:40]
            break
    if not name and getattr(element, "kind", "") == "text":
        text = (getattr(element, "text", "") or "").strip()
        name = text[:40]
    cx = (getattr(element, "x", 0) or 0) + (getattr(element, "w", 0) or 0) / 2
    cy = (getattr(element, "y", 0) or 0) + (getattr(element, "h", 0) or 0) / 2
    col = ("left", "centre", "right")[min(2, max(0, int(cx / max(1, canvas_w) * 3)))]
    row = ("top", "middle", "bottom")[min(2, max(0, int(cy / max(1, canvas_h) * 3)))]
    position = f"{row} {col}" if row != "middle" or col != "centre" else "centre"
    return name, position


def _walk_option_schema(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """name -> cell_options entry for a plugin manifest."""
    return {
        str(opt.get("name")): opt
        for opt in manifest.get("cell_options") or []
        if isinstance(opt, dict) and opt.get("name")
    }


class ExportBlocked(Exception):
    """The dashboard cannot be shared as-is; ``problems`` says why."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _suggest_input(
    inputs: dict[str, dict[str, Any]],
    *,
    name: str,
    label: str,
    type_: str,
    secret: bool,
    target: dict[str, Any],
    note: str,
    group: str,
) -> None:
    """Add a suggested input, or attach this target to an existing one.

    ``group`` decides sharing: two slots that held the *same original value*
    (one API key used by two sources) become one question answered once, while
    slots that held different values (three tiles watching three sensors) stay
    separate questions. Grouping on the value rather than the option name is
    what keeps those two cases apart; the values themselves never leave the
    machine, they only decide the grouping here."""
    for entry in inputs.values():
        if entry.get("_group") == group:
            if target not in entry["targets"]:
                entry["targets"].append(target)
            return
    inputs[name] = {
        "name": name,
        "label": label,
        "type": type_,
        "secret": secret,
        "required": secret,  # secret slots don't work when left empty
        "default": "",
        "targets": [target],
        "note": note,
        "_group": group,
    }


def _contextual_label(option_label: str, context: tuple[str, str]) -> str:
    """ "Kitchen: Entities" rather than three fields all called "Entities"."""
    name = context[0]
    if not name or name.lower() == option_label.lower():
        return option_label
    return f"{name}: {option_label}"


def _where(context: tuple[str, str], note: str) -> str:
    """Append the element's position so an untitled tile is still findable."""
    position = context[1]
    return f"{note} ({position} of the dashboard)" if position else note


def _group_key(plugin_id: str, key: str, value: Any) -> str:
    """Identity for input sharing: same widget option holding the same original
    value means one question. The value is hashed and never stored."""
    import hashlib as _hashlib
    import json as _json

    blob = _json.dumps(value, sort_keys=True, default=str)
    return f"{plugin_id}:{key}:{_hashlib.sha256(blob.encode()).hexdigest()[:12]}"


def _input_name(base: str, taken: dict[str, dict[str, Any]]) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in base.lower()).strip("_")[:28] or "value"
    name = slug
    n = 2
    while name in taken:
        name = f"{slug}_{n}"
        n += 1
    return name


def build_template(
    page: Page,
    *,
    registry: Any,
    installed_records: dict[str, Any],
    data_root: Path,
) -> dict[str, Any]:
    """Export ``page`` as a template. Returns
    ``{template, inputs_suggested, redactions, lint, blocking}``; a non-empty
    ``blocking`` list means the dashboard can't be shared until fixed.

    ``registry`` is the plugin registry (``app.config["PLUGIN_REGISTRY"]``);
    ``installed_records`` the marketplace's ``installed()`` map (catalog id ->
    InstalledRecord), used to reverse-map plugin folders / theme ids / font
    plugin ids to catalog ids for ``requires[]``."""
    if page.layout_kind != "canvas" or page.canvas is None:
        raise ExportBlocked(["only canvas dashboards can be shared as templates"])

    layout = page.canvas.model_copy(deep=True)
    blocking: list[str] = []
    redactions: list[str] = []
    requires: set[str] = set()
    inputs: dict[str, dict[str, Any]] = {}

    def folder_to_catalog_id(folder: str) -> str | None:
        for catalog_id, record in installed_records.items():
            if folder in getattr(record, "folders", ()):  # widgets, fonts, bundles
                return catalog_id
        return None

    def note_widget(plugin_id: str, where: str) -> None:
        if not plugin_id:
            return
        plugin = registry.get(plugin_id)
        if plugin is None:
            blocking.append(f"{where}: widget {plugin_id!r} is not installed here")
            return
        catalog_id = folder_to_catalog_id(plugin_id)
        if catalog_id is not None:
            requires.add(catalog_id)
        # No catalog record = bundled plugin; every install has it.

    def redact_options(
        el_id: str,
        plugin_id: str,
        options: dict[str, Any],
        slot: str,
        index: int | None,
        context: tuple[str, str] = ("", ""),
    ) -> None:
        """``context`` is (element name, position) used to tell one element's
        questions from another's when a dashboard has several of the same
        widget."""
        plugin = registry.get(plugin_id) if plugin_id else None
        schema = _walk_option_schema(getattr(plugin, "manifest", None) or {}) if plugin else {}
        for key in list(options.keys()):
            value = options.get(key)
            if value in (None, "", [], {}):
                continue
            opt = schema.get(key) or {}
            target: dict[str, Any] = {"el": el_id, "slot": slot, "key": key}
            if index is not None:
                target["index"] = index
            if opt.get("secret"):
                options[key] = ""
                redactions.append(f"{el_id}: removed secret option {plugin_id}.{key}")
                _suggest_input(
                    inputs,
                    name=_input_name(f"{context[0] or plugin_id}_{key}", inputs),
                    label=_contextual_label(str(opt.get("label") or key), context),
                    type_="string",
                    secret=True,
                    target=target,
                    note=_where(context, f"was the author's {plugin_id} {key}"),
                    group=_group_key(plugin_id, key, value),
                )
            elif key in _INSTALL_SPECIFIC_OPTION_NAMES or (
                isinstance(value, str) and value.startswith(("sensor.", "light.", "switch."))
            ):
                if isinstance(value, list):
                    options[key] = []
                elif isinstance(value, dict):
                    options[key] = {}
                else:
                    options[key] = ""
                redactions.append(f"{el_id}: cleared install-specific option {key}")
                _suggest_input(
                    inputs,
                    name=_input_name(f"{context[0]}_{key}" if context[0] else key, inputs),
                    label=_contextual_label(
                        str(opt.get("label") or key.replace("_", " ").title()), context
                    ),
                    type_="location_search" if opt.get("type") == "location_search" else "string",
                    secret=False,
                    target=target,
                    note=_where(context, "the installer picks their own"),
                    group=_group_key(plugin_id, key, value),
                )

    for el in layout.els:
        el_id = el.id
        # Captured before redaction: the element's own title is the clearest
        # way to tell one tile's questions from another's, and some titles are
        # themselves cleared below.
        context = _element_context(el, layout.w, layout.h)
        if el.kind == "widget" or el.widget:
            note_widget(el.widget, f"element {el_id}")
            redact_options(el_id, el.widget, el.options, "options", None, context)
        if getattr(el, "source", ""):
            note_widget(el.source, f"element {el_id}")
            redact_options(el_id, el.source, el.options, "options", None, context)
        for i, source in enumerate(el.sources or []):
            if source.key:
                note_widget(source.key, f"element {el_id} source {i}")
                redact_options(el_id, source.key, source.options, "source_options", i, context)
            if source.headers:
                original_headers = dict(source.headers)
                source.headers = {}
                redactions.append(f"{el_id}: removed request headers on source {i}")
                _suggest_input(
                    inputs,
                    name=_input_name(f"{source.name or source.key or 'source'}_headers", inputs),
                    label=f"Request headers for {source.name or source.url or 'source'} (JSON)",
                    type_="textarea",
                    secret=True,
                    target={"el": el_id, "slot": "source_header", "index": i},
                    note=_where(context, "request headers never leave the author's machine"),
                    group=_group_key("source", "headers", original_headers),
                )
        for i, bind in enumerate(el.bind or []):
            if bind.source:
                note_widget(bind.source, f"element {el_id} bind {i}")
                redact_options(el_id, bind.source, bind.options, "bind_options", i, context)
        value_key = getattr(el, "value_key", "") or ""
        if value_key.startswith("ha:"):
            el.value_key = ""
            redactions.append(f"{el_id}: cleared HA entity binding {value_key!r}")

    # Theme: bundled themes travel by id; user themes are local-only; community
    # themes become requirements (their catalog id doubles as the theme id).
    theme = layout.theme or ""
    if theme.startswith("user-"):
        blocking.append(
            f"theme {theme!r} is a local user theme; switch to a bundled or "
            "marketplace theme before sharing"
        )
    elif theme:
        for catalog_id, record in installed_records.items():
            if getattr(record, "kind", "") == "theme" and theme in getattr(record, "folders", ()):
                requires.add(catalog_id)
                break

    # Font: bundled fonts pass; marketplace fonts become requirements.
    if layout.font:
        font = (getattr(registry, "fonts", None) or {}).get(layout.font)
        if font is None:
            blocking.append(f"font {layout.font!r} is not available here")
        else:
            font_catalog_id = folder_to_catalog_id(font.plugin_id)
            if font_catalog_id is not None:
                requires.add(font_catalog_id)

    # Background image: local page assets inline (small) or block; other local
    # paths can't travel at all.
    bg = layout.bg_image or ""
    if bg.startswith("/page-assets/"):
        name = bg.rsplit("/", 1)[-1]
        from app.page_assets import assets_dir

        path = assets_dir(data_root, page.id) / name
        try:
            raw = path.read_bytes()
        except OSError:
            raw = b""
        if not raw:
            blocking.append(f"background image {name!r} could not be read")
        elif len(raw) > MAX_INLINE_ASSET_BYTES:
            blocking.append(
                f"background image {name!r} is too large to share "
                f"({len(raw) // 1024}KB > {MAX_INLINE_ASSET_BYTES // 1024}KB); "
                "use a smaller image or a public URL"
            )
        else:
            mime = mimetypes.guess_type(name)[0] or "image/png"
            layout.bg_image = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
            redactions.append(f"background image {name!r} inlined ({len(raw) // 1024}KB)")
    elif bg and not bg.startswith(("https://", "data:image/")):
        blocking.append(f"background image {bg!r} is not shareable (https or inline only)")

    doc = layout.model_dump(mode="json", exclude_none=True)
    template = {
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "title": page.name or "Untitled",
        "description": "",
        "tags": [],
        "requires": sorted(requires),
        "inputs": [],  # the dialog finalises which suggestions become inputs
        "canvas": doc,
    }

    lint_items = _collect_lint_items(template)
    lint = template_lint.lint_strings(lint_items)
    for err in lint["errors"]:
        blocking.append(
            f"credential material at {err['where']} ({err['rule']}); remove it "
            "or turn it into a secret input"
        )

    suggestions = list(inputs.values())
    for suggestion in suggestions:
        suggestion.pop("_base", None)
    return {
        "template": template,
        "inputs_suggested": suggestions,
        "redactions": redactions,
        "lint": lint,
        "blocking": blocking,
    }


def _collect_lint_items(template: dict[str, Any]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []

    def walk(node: Any, path: str) -> None:
        if len(items) >= 2000:
            return
        if isinstance(node, str):
            items.append((path, node))
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(template, "")
    return items


def apply_inputs(template: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Patch input ``values`` into a template's canvas through the declared
    ``targets``. Returns a deep-copied canvas dict ready for
    ``CanvasLayout.model_validate``. Unknown input names are ignored; declared
    inputs without a supplied value keep their (redacted/empty) slot."""
    import copy

    canvas = copy.deepcopy(template.get("canvas") or {})
    els_by_id = {el.get("id"): el for el in canvas.get("els") or [] if isinstance(el, dict)}
    for input_spec in template.get("inputs") or []:
        name = input_spec.get("name")
        if name not in values:
            continue
        value = values[name]
        for target in input_spec.get("targets") or []:
            el = els_by_id.get(target.get("el"))
            if el is None:
                continue
            slot = target.get("slot")
            key = target.get("key")
            index = target.get("index")
            if slot == "options":
                el.setdefault("options", {})[key] = value
            elif slot == "source_options":
                sources = el.get("sources") or []
                if isinstance(index, int) and 0 <= index < len(sources):
                    sources[index].setdefault("options", {})[key] = value
            elif slot == "source_header":
                sources = el.get("sources") or []
                if isinstance(index, int) and 0 <= index < len(sources):
                    headers = sources[index].setdefault("headers", {})
                    if key:
                        headers[key] = value
                    elif isinstance(value, dict):
                        headers.update(value)
                    elif isinstance(value, str) and value.strip():
                        import json as _json

                        try:
                            parsed = _json.loads(value)
                        except ValueError:
                            parsed = None
                        if isinstance(parsed, dict):
                            headers.update(parsed)
            elif slot == "source_url":
                sources = el.get("sources") or []
                if isinstance(index, int) and 0 <= index < len(sources):
                    sources[index]["url"] = value
            elif slot == "bind_options":
                binds = el.get("bind") or []
                if isinstance(index, int) and 0 <= index < len(binds):
                    binds[index].setdefault("options", {})[key] = value
    return canvas


def read_inputs(canvas: dict[str, Any], inputs: list[dict[str, Any]]) -> dict[str, Any]:
    """The inverse of :func:`apply_inputs`: read each input's current value out
    of ``canvas`` through its declared targets.

    An input can point at several places (one answer filling three tiles' entity
    ids), so the first target that resolves to a set value wins and the rest are
    assumed to agree, which is what applying the input made true. Missing
    targets read as the input's ``default``, so a Configure form pre-fills with
    what the dashboard is actually rendering rather than blanks.
    """
    els_by_id = {el.get("id"): el for el in canvas.get("els") or [] if isinstance(el, dict)}
    values: dict[str, Any] = {}
    for input_spec in inputs:
        name = input_spec.get("name")
        if not name:
            continue
        found: Any = None
        for target in input_spec.get("targets") or []:
            el = els_by_id.get(target.get("el"))
            if el is None:
                continue
            slot = target.get("slot")
            key = target.get("key")
            index = target.get("index")
            if slot == "options":
                found = (el.get("options") or {}).get(key)
            elif slot in ("source_options", "source_header", "source_url"):
                sources = el.get("sources") or []
                if not (isinstance(index, int) and 0 <= index < len(sources)):
                    continue
                source = sources[index]
                if slot == "source_options":
                    found = (source.get("options") or {}).get(key)
                elif slot == "source_url":
                    found = source.get("url")
                else:
                    headers = source.get("headers") or {}
                    # A header input either names one header or owns the whole
                    # map; the map form is round-tripped as JSON text because
                    # that is what the textarea control edits.
                    if key:
                        found = headers.get(key)
                    elif headers:
                        import json as _json

                        found = _json.dumps(headers)
            elif slot == "bind_options":
                binds = el.get("bind") or []
                if isinstance(index, int) and 0 <= index < len(binds):
                    found = (binds[index].get("options") or {}).get(key)
            if found not in (None, "", [], {}):
                break
        values[name] = found if found not in (None, "", [], {}) else input_spec.get("default", "")
    return values


# -- preview image -------------------------------------------------------

# A dashboard renders at its authored size (up to 1600x1200 on a 13.3"
# panel), which routinely blows the submission size cap, so downsample before
# submitting. The target is NOT the browse card (~260px): the same image is
# what a reviewer judges the submission by in the Discord embed, so it stays
# big enough to read the dashboard's text and spot anything off. 1200px on the
# long edge is comfortably readable when opened full-size in Discord, and
# supersampling down from the full render looks better than rendering small.
PREVIEW_MAX_EDGE = 1200
# Under the server's 1MB cap with headroom, so a different Pillow version on
# the submitting machine can't push an accepted encoding over the line.
PREVIEW_TARGET_BYTES = 900_000
# Tried in order; the first encoding that fits wins. With the palette fallback
# below, a dashboard essentially always fits at the first edge, so the smaller
# steps exist only for pathological photographic renders.
_PREVIEW_EDGES = (PREVIEW_MAX_EDGE, 1000, 800, 640)


def _encode_png(image: Any, *, quantize: bool) -> bytes:
    import io

    from PIL import Image

    out = image
    if quantize:
        # e-ink dashboards are mostly flat fills and text, so an adaptive
        # 256-colour palette is visually near-identical and much smaller.
        out = image.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    buffer = io.BytesIO()
    out.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def shrink_preview(png: bytes, *, target_bytes: int = PREVIEW_TARGET_BYTES) -> bytes:
    """Downscale a rendered dashboard PNG to catalog size.

    Returns the first encoding at or under ``target_bytes``, trying full
    colour before a palette at each step down; if even the smallest attempt
    is over (a pathological render), the smallest one is returned and the
    server's cap decides. Unreadable input is passed through untouched so a
    preview problem can never be worse than the original bytes."""
    import io

    from PIL import Image

    # Already small enough: send the full-resolution render untouched. Most
    # dashboards are flat colour blocks and text, which compress far better at
    # native size than after resampling (interpolation adds noise PNG can't
    # pack), so shrinking one of those makes the file BIGGER and the reviewer's
    # view worse. Only pay the cost when the budget actually demands it.
    if len(png) <= target_bytes:
        return png

    try:
        opened = Image.open(io.BytesIO(png))
        opened.load()
    except Exception:
        return png
    source = opened.convert("RGB")

    smallest = png
    for edge in _PREVIEW_EDGES:
        candidate = source.copy()
        candidate.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        for quantize in (False, True):
            try:
                encoded = _encode_png(candidate, quantize=quantize)
            except Exception:
                continue
            if len(encoded) <= target_bytes:
                return encoded
            if len(encoded) < len(smallest):
                smallest = encoded
    return smallest
