"""MCP API: a token-authenticated JSON surface for building canvas dashboards.

This is the *server side* of Tesserae's MCP integration. The stdio bridge an
agent connects to is a separate package, ``tesserae-mcp``
(https://github.com/dmellok/tesserae-mcp), which talks to this surface over HTTP.
It lets an AI agent do what the freeform canvas editor does, but by writing the
canvas document directly and rendering a preview PNG to see the result: list
widgets/devices, create + edit canvas dashboards, render a preview, push to a panel.

Everything here reuses the panels editor's own helpers (:mod:`app.panels_routes`)
so the agent path and the UI path stay identical; nothing is reimplemented.

Auth (checked in :func:`_gate`): the surface is reachable from loopback without a
token (a co-located agent needs zero config), OR with the stored MCP token
presented as ``Authorization: Bearer <token>`` (a remote agent). The whole
blueprint is gated behind the ``mcp`` experiment flag (opt-in), so it 404s until
switched on in Settings.
"""

from __future__ import annotations

import json
import secrets
import uuid
from typing import Any

from flask import Blueprint, Flask, abort, current_app, jsonify, request, url_for
from pydantic import ValidationError
from werkzeug.wrappers import Response

from app import experiments
from app import panels_routes as _pr
from app.auth import _is_loopback
from app.panels_schema import build_catalog
from app.state.panel_store import CanvasLayout, CanvasPage
from app.state.settings_store import SettingsStore
from app.webhook_routes import _presented_token, generate_token

bp = Blueprint("mcp_api", __name__, url_prefix="/api/mcp")

_EXPERIMENT = "mcp"
# Where the MCP token lives in settings. ``_secret`` suffix → SecretBox-encrypted
# at rest, same convention as the webhook token.
_TOKEN_KEY = "mcp_token_secret"


# -- token storage ------------------------------------------------------


def _settings() -> SettingsStore:
    store: SettingsStore = current_app.config["SETTINGS_STORE"]
    return store


def mcp_token(settings: SettingsStore) -> str | None:
    """The stored MCP token, or None when one hasn't been generated yet."""
    raw = settings.get_section("app").get(_TOKEN_KEY) or ""
    return raw.strip() or None


def rotate_token(settings: SettingsStore) -> str:
    """Generate a fresh MCP token, persist it, and return it."""
    token = generate_token()
    settings.patch_section("app", {_TOKEN_KEY: token})
    return token


# -- auth + experiment gate ---------------------------------------------


@bp.before_request
def _gate() -> Response | None:
    """404 when the experiment is off; otherwise allow loopback or a valid token."""
    if not experiments.is_enabled(_EXPERIMENT):
        abort(404)
    if _is_loopback():
        return None
    stored = mcp_token(_settings())
    presented = _presented_token(request)
    if stored and presented and secrets.compare_digest(presented, stored):
        return None
    return _err(
        401,
        "unauthorized: present the MCP token as 'Authorization: Bearer <token>' "
        "(generate one in Settings → System → MCP), or call from localhost.",
    )


def _err(status: int, message: str, **extra: Any) -> Response:
    resp = jsonify({"error": message, **extra})
    resp.status_code = status
    return resp


# -- catalog / widget options -------------------------------------------


@bp.get("/catalog")
def catalog() -> Response:
    """Every renderable widget (with its fragments) plus theme/style/font options,
    so the agent knows what it can place and how to style the canvas.

    The per-widget ``sample`` payload is omitted here to keep the response small;
    fetch a widget's live data shape with ``POST /widgets/<key>/data`` instead."""
    widgets = build_catalog(_pr._registry())
    lean = [{k: v for k, v in w.items() if k != "sample"} for w in widgets]
    return jsonify({"widgets": lean, "appearance": _pr._appearance()})


# Format hints injected per option ``type`` so an agent knows the expected shape
# without reverse-engineering it. Keyed on the ``cell_options`` type string.
_TYPE_HINTS: dict[str, str] = {
    "location_search": (
        "A place. Accepts a bare name ('South Morang'), a 'City, CC' form "
        "('Paris, FR'), or a literal 'lat,lon' pair ('-37.65,145.09'). Resolved "
        "server-side to coordinates; the resolved place name becomes the label."
    ),
    "entity": "A single Home Assistant entity_id (e.g. 'sensor.living_room_temp').",
    "multiselect": "A JSON array of the option's choice values.",
    "select": "One of the option's choice values.",
    "color": "A CSS colour ('#1B1A16' or a named token).",
    "number": "A number.",
    "bool": "true or false.",
}
# Options whose materialised ``choices`` list can be huge (HA entity pickers run
# to hundreds of rows). Stripped by default so the schema stays small; fetch the
# rows from ``/widgets/<key>/choices`` when you actually need them.
_CHOICE_STRIP_THRESHOLD = 20


@bp.get("/widgets/<key>/options")
def widget_options(key: str) -> Response:
    """The configurable options for one widget (its ``cell_options``), so the
    agent can set an element's ``options`` correctly (e.g. a weather location).

    Big ``choices`` lists (HA entity pickers) are omitted by default to keep the
    response small: an option with a long list shows ``choices_count`` and a
    ``choices_endpoint`` instead. Pass ``?include_choices=true`` to inline them
    anyway. Each option also carries a ``format`` hint for its type."""
    plugin = _pr._registry().get(key)
    if plugin is None:
        return _err(404, f"unknown widget {key!r}")
    include = request.args.get("include_choices") == "true"
    opts = []
    for opt in _pr._materialised_options(plugin):
        o = dict(opt)
        otype = str(o.get("type") or "")
        if otype in _TYPE_HINTS and not o.get("format"):
            o["format"] = _TYPE_HINTS[otype]
        choices = o.get("choices")
        if isinstance(choices, list) and len(choices) > _CHOICE_STRIP_THRESHOLD and not include:
            o["choices_count"] = len(choices)
            o["choices_endpoint"] = f"/widgets/{key}/choices?option={o.get('name')}"
            o.pop("choices", None)
        opts.append(o)
    return jsonify({"key": key, "options": opts})


@bp.get("/widgets/<key>/choices")
def widget_choices(key: str) -> Response:
    """The choice rows for one of a widget's options, paginated. Query:
    ``option=<name>`` (required), ``limit=`` (default 100), ``offset=`` (default 0),
    ``q=`` (case-insensitive substring filter on value/label). Separated from
    ``/options`` so a picker with hundreds of entries doesn't bloat the schema."""
    plugin = _pr._registry().get(key)
    if plugin is None:
        return _err(404, f"unknown widget {key!r}")
    name = request.args.get("option")
    if not name:
        return _err(400, "pass ?option=<name> to name which option's choices you want")
    opt = next((o for o in _pr._materialised_options(plugin) if o.get("name") == name), None)
    if opt is None:
        return _err(404, f"widget {key!r} has no option {name!r}")
    choices = opt.get("choices")
    rows = list(choices) if isinstance(choices, list) else []
    q = (request.args.get("q") or "").strip().lower()
    if q:
        rows = [
            r
            for r in rows
            if q in str(r.get("value", "")).lower() or q in str(r.get("label", "")).lower()
        ]
    total = len(rows)
    try:
        limit = max(1, min(1000, int(request.args.get("limit", 100))))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return _err(400, "limit and offset must be integers")
    page = rows[offset : offset + limit]
    return jsonify({"key": key, "option": name, "total": total, "offset": offset, "choices": page})


def _trunc(value: Any) -> Any:
    """Keep scalars, truncate long strings so a field sample stays compact."""
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    s = str(value)
    return s if len(s) <= 60 else s[:60] + "…"


def _flatten_fields(
    data: Any, prefix: str = "", out: list[dict[str, Any]] | None = None, depth: int = 0
) -> list[dict[str, Any]]:
    """Flatten a data payload into bindable dot-paths with a sample value + type,
    so an agent can pick a data-primitive ``field`` without reverse-engineering
    the shape. Arrays of objects emit a pluck path (``series[].total``) that the
    field resolver understands for charts. Capped so a huge payload stays small."""
    if out is None:
        out = []
    if len(out) >= 80 or depth > 5:
        return out
    if isinstance(data, dict):
        for k, v in data.items():
            _flatten_fields(v, f"{prefix}.{k}" if prefix else str(k), out, depth + 1)
    elif isinstance(data, list):
        if data and isinstance(data[0], dict):
            _flatten_fields(data[0], f"{prefix}[]", out, depth + 1)
            out.append({"path": prefix, "type": "object[]", "len": len(data)})
        else:
            out.append({"path": prefix, "type": "array", "len": len(data), "sample": data[:3]})
    elif prefix:
        out.append({"path": prefix, "type": type(data).__name__, "sample": _trunc(data)})
    return out


@bp.post("/widgets/<key>/data")
def widget_data(key: str) -> Response:
    """The data a widget's fetch() returns, for field discovery before binding a
    data primitive. Body: ``{options?}``. Returns ``{key, data, data_source, reason,
    fields}`` where ``data_source`` is ``"live"`` (real fetch), ``"sample"`` (demo
    fallback because nothing was configured), or ``"error"`` (fetch failed and no
    fallback) so the agent never mistakes a placeholder for a real result. ``fields``
    lists the bindable dot-paths with sample values; an empty payload has fields
    with null values, a wrong key simply isn't in the list."""
    plugin = _pr._registry().get(key)
    if plugin is None:
        return _err(404, f"unknown widget {key!r}")
    from app.composer import _fetch_plugin_data, _location_configured, _resolved_options
    from app.widget_samples import get_sample

    body = request.get_json(silent=True) or {}
    raw_options = body.get("options")
    options: dict[str, Any] = raw_options if isinstance(raw_options, dict) else {}
    opts = _resolved_options(key, options)
    live: Any = None
    try:
        live = _fetch_plugin_data(key, opts, 600, 400, preview=False, cell_w=600, cell_h=400)
    except Exception as err:
        live = {"error": f"{type(err).__name__}: {err}"}

    data = live
    data_source = "live"
    reason: str | None = None
    if not isinstance(live, dict) or live.get("error"):
        reason = (
            live.get("error") if isinstance(live, dict) else None
        ) or "widget returned no data"
        # Only paper over the error with the demo sample when nothing was
        # configured. A set-but-unresolvable location (or a missing backend)
        # surfaces its real error rather than a plausible-looking sample.
        sample = get_sample(key) if not _location_configured(options) else None
        if isinstance(sample, dict):
            data, data_source = sample, "sample"
        else:
            data_source = "error"
    return jsonify(
        {
            "key": key,
            "data": data,
            "data_source": data_source,
            "reason": reason,
            "fields": _flatten_fields(data) if isinstance(data, dict) else [],
        }
    )


# -- devices ------------------------------------------------------------


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _gamut_info(gamut: str) -> dict[str, Any]:
    """Map a panel gamut to a human colour_mode + the actual palette hex list, so
    an agent can pick a palette the hardware can render instead of designing in
    colours that quantise away (the "designed colourful for a grayscale panel"
    trap)."""
    from app import quantizer as q

    table: dict[str, tuple[str, tuple[tuple[int, int, int], ...]]] = {
        "waveshare_e6": ("6-colour (Spectra 6)", q.WAVESHARE_E6_PALETTE),
        "inky_7colour": ("7-colour (ACeP)", q.INKY_7COLOUR_PALETTE),
        "bwry_4": ("4-colour · black/white/red/yellow", q.BWRY_4_PALETTE),
        "bwr_3": ("3-colour · black/white/red", q.BWR_3_PALETTE),
        "gray_4": ("4-level grayscale", q.GRAY_4_PALETTE),
    }
    label, palette = table.get(gamut, (gamut or "unknown", ()))
    colors = [_hex(c) for c in palette]
    return {
        "gamut": gamut,
        "color_mode": label,
        "colors": colors,
        "mono": len(colors) <= 2,
    }


@bp.get("/devices")
def devices() -> Response:
    """Registered display instances a canvas can be pushed to, with panel dims and
    colour capability (gamut, a friendly ``color_mode``, the renderable palette as
    hex, an ``orientation``, and a ``mono`` flag) so the agent can size and colour
    a canvas to the hardware instead of guessing."""
    from app.panel import device_panel

    reg = current_app.config.get("DEVICE_REGISTRY")
    out: list[dict[str, Any]] = []
    if reg is not None:
        for d in reg.all():
            if d.kind_of is None:  # instances only, not built-in kinds
                continue
            entry: dict[str, Any] = {"id": d.id, "name": str(d.manifest.get("name") or d.id)}
            panel = device_panel(d)
            if panel is not None:
                entry["w"] = panel.w
                entry["h"] = panel.h
                entry.update(_gamut_info(panel.gamut))
                block = d.manifest.get("panel") or {}
                if isinstance(block, dict) and block.get("orientation"):
                    entry["orientation"] = str(block["orientation"])
            out.append(entry)
    out.sort(key=lambda x: str(x["name"]).lower())
    return jsonify({"devices": out})


# -- canvas pages -------------------------------------------------------


@bp.get("/pages")
def list_pages() -> Response:
    """All canvas (freeform) dashboards, with size, element count, and targets."""
    out = [
        {
            "id": p.id,
            "name": p.name,
            "w": (p.canvas.w if p.canvas else 0),
            "h": (p.canvas.h if p.canvas else 0),
            "elements": (len(p.canvas.els) if p.canvas else 0),
            "device_ids": list(p.device_ids),
            "created_by": p.created_by,
        }
        for p in _pr._canvas_pages()
    ]
    out.sort(key=lambda x: str(x["name"]).lower())
    return jsonify({"pages": out})


@bp.post("/pages")
def create_page() -> Response:
    """Create an empty canvas dashboard. Body: ``{name?, w?, h?}``. The page is
    marked ``created_by="mcp"`` so it's flagged as agent-made in the UI."""
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "Untitled Panel").strip() or "Untitled Panel"
    page = _pr._new_canvas_page(name)
    page.created_by = "mcp"
    if page.canvas is not None:
        w, h = body.get("w"), body.get("h")
        if isinstance(w, int) and w > 0:
            page.canvas.w = w
        if isinstance(h, int) and h > 0:
            page.canvas.h = h
    _save_mcp(page)
    return jsonify({"id": page.id, "name": page.name})


@bp.get("/pages/<page_id>/canvas")
def get_canvas(page_id: str) -> Response:
    """The full canvas document (artboard size, appearance, and every element),
    plus ``rev`` / ``updated_at`` / ``updated_by`` so the agent can pass
    ``?base_rev=<rev>`` on its next write and be warned if the page drifted."""
    page = _pr._get_canvas(page_id)
    if page is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    doc = _pr._as_doc(page).model_dump(mode="json")
    doc["rev"] = _pr._canvas_rev(page)
    doc["updated_at"] = page.updated_at
    doc["updated_by"] = page.updated_by
    return jsonify(doc)


def _saved(page: Any) -> Response:
    """Compact save acknowledgement (id, rev, element count, last-write meta).
    Pass ``?return=doc`` to get the full document back instead. Keeping this small
    avoids echoing an 80k-char document on every write."""
    layout = page.canvas
    if request.args.get("return") == "doc":
        return jsonify(_pr._as_doc(page).model_dump(mode="json"))
    return jsonify(
        {
            "ok": True,
            "id": page.id,
            "rev": _pr._canvas_rev(page),
            "elements": len(layout.els) if layout is not None else 0,
            "updated_at": page.updated_at,
            "updated_by": page.updated_by,
        }
    )


def _drift_conflict(page: Any) -> Response | None:
    """Optimistic-concurrency guard. When the caller passes ``?base_rev=<rev>``
    (the rev it last read) and the stored document has since changed, return a
    409 so the agent re-reads instead of clobbering an edit made in the UI (or by
    another agent) between calls. No ``base_rev`` → no check (opt-in)."""
    base = request.args.get("base_rev")
    if base and base != _pr._canvas_rev(page):
        return _err(
            409,
            "the dashboard changed since you last read it; re-read it and retry",
            current_rev=_pr._canvas_rev(page),
            drifted=True,
            updated_at=page.updated_at,
            updated_by=page.updated_by,
        )
    return None


def _save_mcp(page: Any) -> None:
    """Stamp the page as MCP-written and persist it."""
    _pr._stamp(page, "mcp")
    _pr._pages().save(page)


@bp.put("/pages/<page_id>/canvas")
def set_canvas(page_id: str) -> Response:
    """Replace a canvas dashboard's document. Body is the canvas layout
    (``{w,h,theme,style,font,bg,bg_image,bg_fit,els[],name?}``). ``id`` and bound
    devices are preserved from the server. Returns a compact ``{ok,id,rev,elements}``
    ack by default (``?return=doc`` for the full document); an invalid document
    returns 422 with field-level messages so the agent can correct it."""
    page = _pr._get_canvas(page_id)
    if page is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    conflict = _drift_conflict(page)
    if conflict is not None:
        return conflict
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err(400, "body must be a JSON object")
    # Force server-owned fields; the agent supplies the layout only.
    doc_in = {**body, "id": page_id, "name": str(body.get("name") or page.name)}
    doc_in.setdefault("device_ids", list(page.device_ids))
    try:
        doc = CanvasPage.model_validate(doc_in)
    except ValidationError as exc:
        return _err(422, "invalid canvas document", details=exc.errors(include_url=False))
    new_page = _pr._doc_to_page(doc)
    new_page.created_by = page.created_by  # preserve provenance
    _save_mcp(new_page)
    return _saved(new_page)


@bp.post("/pages/<page_id>/elements")
def append_element(page_id: str) -> Response:
    """Append one element to a canvas and save (each call is one save, so an open
    editor updates live as an agent builds). Body is a single element object, e.g.
    ``{"kind":"data","source":"weather_now","field":"temp","x":10,"y":10,"w":120,"h":60}``.
    Returns the compact ack plus the new element's ``element_id``."""
    from app.state.panel_store import CanvasLayout, Element

    page = _pr._get_canvas(page_id)
    if page is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err(400, "body must be a single element object")
    conflict = _drift_conflict(page)
    if conflict is not None:
        return conflict
    if not body.get("id"):
        body = {**body, "id": uuid.uuid4().hex[:8]}
    try:
        element = Element.model_validate(body)
    except ValidationError as exc:
        return _err(422, "invalid element", details=exc.errors(include_url=False))
    layout = page.canvas or CanvasLayout()
    layout.els.append(element)
    page.canvas = layout
    _save_mcp(page)
    resp = _saved(page)
    data = resp.get_json()
    data["element_id"] = element.id
    return jsonify(data)


@bp.patch("/pages/<page_id>/elements/<element_id>")
def patch_element(page_id: str, element_id: str) -> Response:
    """Update one element in place without re-sending the whole document. Body is
    a partial element ``{field: value, ...}`` merged over the existing element
    (shallow: a provided ``options`` / ``parts`` replaces wholesale). Re-validates
    the merged element. Returns the compact ack. Supports ``?base_rev=`` drift
    guard. This is the token-cheap edit path for a large canvas."""
    from app.state.panel_store import Element

    page = _pr._get_canvas(page_id)
    if page is None or page.canvas is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    conflict = _drift_conflict(page)
    if conflict is not None:
        return conflict
    patch = request.get_json(silent=True)
    if not isinstance(patch, dict):
        return _err(400, "body must be a JSON object of fields to change")
    idx = next((i for i, e in enumerate(page.canvas.els) if e.id == element_id), None)
    if idx is None:
        return _err(404, f"no element {element_id!r} on {page_id!r}")
    merged = {**page.canvas.els[idx].model_dump(mode="json"), **patch, "id": element_id}
    try:
        element = Element.model_validate(merged)
    except ValidationError as exc:
        return _err(422, "invalid element after patch", details=exc.errors(include_url=False))
    page.canvas.els[idx] = element
    _save_mcp(page)
    return _saved(page)


@bp.delete("/pages/<page_id>/elements/<element_id>")
def delete_element(page_id: str, element_id: str) -> Response:
    """Remove one element from a canvas. Supports ``?base_rev=`` drift guard.
    Returns the compact ack; 404 if the element isn't present."""
    page = _pr._get_canvas(page_id)
    if page is None or page.canvas is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    conflict = _drift_conflict(page)
    if conflict is not None:
        return conflict
    before = len(page.canvas.els)
    page.canvas.els = [e for e in page.canvas.els if e.id != element_id]
    if len(page.canvas.els) == before:
        return _err(404, f"no element {element_id!r} on {page_id!r}")
    _save_mcp(page)
    return _saved(page)


@bp.patch("/pages/<page_id>/canvas")
def patch_canvas(page_id: str) -> Response:
    """Update document-level fields (name, size, appearance) without touching the
    elements. Body accepts any of ``{name,w,h,theme,style,font,bg,bg_image,bg_fit}``.
    Use ``set_canvas`` / element endpoints for ``els``. Supports ``?base_rev=``."""
    page = _pr._get_canvas(page_id)
    if page is None or page.canvas is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    conflict = _drift_conflict(page)
    if conflict is not None:
        return conflict
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err(400, "body must be a JSON object")
    layout = page.canvas
    if "name" in body and isinstance(body["name"], str) and body["name"].strip():
        page.name = body["name"].strip()
    for dim in ("w", "h"):
        val = body.get(dim)
        if isinstance(val, int) and val > 0:
            setattr(layout, dim, val)
    for field in ("theme", "style", "font", "bg", "bg_image", "bg_fit"):
        if field in body and isinstance(body[field], str):
            setattr(layout, field, body[field])
    _save_mcp(page)
    return _saved(page)


# -- layout helper ------------------------------------------------------


def _arrange(
    box: dict[str, int], layout: str, count: int, gap: int, pad: int, cols: int
) -> list[dict[str, int]]:
    """Compute ``count`` aligned child boxes inside ``box`` for a grid / row /
    column layout, so an agent lays out cells by intent instead of hand-computing
    every x/y/w/h. Rounds to whole pixels."""
    import math

    x0 = box["x"] + pad
    y0 = box["y"] + pad
    iw = max(0, box["w"] - 2 * pad)
    ih = max(0, box["h"] - 2 * pad)
    out: list[dict[str, int]] = []
    if count <= 0:
        return out
    if layout == "row":
        cw = (iw - gap * (count - 1)) / count
        for i in range(count):
            out.append(
                {"x": round(x0 + i * (cw + gap)), "y": round(y0), "w": round(cw), "h": round(ih)}
            )
    elif layout in ("column", "stack"):
        chh = (ih - gap * (count - 1)) / count
        for i in range(count):
            out.append(
                {"x": round(x0), "y": round(y0 + i * (chh + gap)), "w": round(iw), "h": round(chh)}
            )
    else:  # grid
        c = cols if cols > 0 else max(1, math.ceil(math.sqrt(count)))
        r = max(1, math.ceil(count / c))
        cw = (iw - gap * (c - 1)) / c
        chh = (ih - gap * (r - 1)) / r
        for i in range(count):
            row, col = divmod(i, c)
            out.append(
                {
                    "x": round(x0 + col * (cw + gap)),
                    "y": round(y0 + row * (chh + gap)),
                    "w": round(cw),
                    "h": round(chh),
                }
            )
    return out


@bp.post("/layout")
def layout() -> Response:
    """Compute aligned child boxes for a container region, so an agent places
    cells by intent (grid / row / column) instead of hand-computing pixels. Body:
    ``{box:{x,y,w,h}, layout:"grid"|"row"|"column", count, gap?, pad?, cols?}``.
    Returns ``{boxes:[{x,y,w,h}, ...]}`` to spread across each element's geometry.
    Bake the boxes into normal elements (they stay individually editable in the
    UI); re-call to reflow after a size change."""
    body = request.get_json(silent=True) or {}
    box = body.get("box")
    if not (
        isinstance(box, dict) and all(isinstance(box.get(k), int) for k in ("x", "y", "w", "h"))
    ):
        return _err(400, "box must be {x:int, y:int, w:int, h:int}")
    try:
        count = int(body["count"])
    except (KeyError, TypeError, ValueError):
        return _err(400, "count must be an integer")
    if count < 1 or count > 1000:
        return _err(400, "count must be between 1 and 1000")
    layout_kind = str(body.get("layout") or "grid")
    gap = int(body.get("gap") or 0)
    pad = int(body.get("pad") or 0)
    cols = int(body.get("cols") or 0)
    return jsonify({"boxes": _arrange(box, layout_kind, count, gap, pad, cols)})


# -- preview + push -----------------------------------------------------


def _render_png(page_id: str, layout: CanvasLayout) -> bytes:
    """Screenshot the shared ``/compose/<id>`` target at the canvas dims, the same
    path a device push and the editor preview use."""
    from app.renderer import RenderRequest, render_to_png, to_loopback_url

    path = url_for("composer.compose", page_id=page_id)
    url = to_loopback_url(request.host_url.rstrip("/") + path)
    return render_to_png(
        RenderRequest(url=url, viewport_w=layout.w, viewport_h=layout.h),
        pool=current_app.config.get("BROWSER_POOL"),
    )


@bp.get("/pages/<page_id>/preview.png")
def preview(page_id: str) -> Response:
    """Render the canvas to a PNG at its authored dims (the agent's feedback loop)."""
    page = _pr._get_canvas(page_id)
    if page is None or page.canvas is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    png = _render_png(page_id, page.canvas)
    return current_app.response_class(png, mimetype="image/png")


# JS evaluated in the composed page to produce a machine-readable render report:
# per-element box, resolved text, overflow/clip flags, live-vs-sample, computed
# colours, plus the board's resolved background/theme. A companion to the PNG so
# the agent verifies the render without eyeballing pixels.
_REPORT_JS: str = r"""() => {
  const board = document.getElementById('panels-board') || document.body;
  const bcs = getComputedStyle(board);
  const els = [];
  document.querySelectorAll('[data-el-id]').forEach((n) => {
    const cs = getComputedStyle(n);
    let kind = 'widget';
    if (n.classList.contains('deco')) {
      try { kind = (JSON.parse(n.getAttribute('data-el') || '{}').kind) || 'deco'; }
      catch (e) { kind = 'deco'; }
    }
    const txt = (n.textContent || '').trim().replace(/\s+/g, ' ');
    els.push({
      id: n.getAttribute('data-el-id'),
      kind: kind,
      data_source: n.getAttribute('data-data-source') || '',
      box: { x: n.offsetLeft, y: n.offsetTop, w: n.offsetWidth, h: n.offsetHeight },
      text: txt.slice(0, 160),
      overflow_x: n.scrollWidth > n.clientWidth + 1,
      overflow_y: n.scrollHeight > n.clientHeight + 1,
      scroll_w: n.scrollWidth,
      client_w: n.clientWidth,
      background: cs.backgroundColor,
      color: cs.color,
    });
  });
  return {
    board: {
      w: board.offsetWidth,
      h: board.offsetHeight,
      background: bcs.backgroundColor,
      color: bcs.color,
      theme: document.body.getAttribute('data-theme') || '',
      style: document.body.getAttribute('data-style') || '',
    },
    elements: els,
  };
}"""


@bp.get("/pages/<page_id>/render_report")
def render_report(page_id: str) -> Response:
    """A machine-readable companion to preview.png. Renders the canvas headless and
    reports, per element: the resolved box, the text that actually rendered,
    overflow/clip flags (``overflow_x`` when content is wider than its box),
    ``data_source`` (live | sample | error | static), and computed colours; plus
    the board's resolved background / theme. Lets an agent verify a render (catch
    clipping, confirm live data, read the real colours) without parsing a PNG.

    Note: widget cells render into shadow DOM, so their ``text`` may be empty; data
    primitives and decorations report their text. Overflow is measured on the
    element box regardless."""
    page = _pr._get_canvas(page_id)
    if page is None or page.canvas is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    from app.renderer import InspectRequest, RenderRequest, inspect_composed, to_loopback_url

    path = url_for("composer.compose", page_id=page_id)
    url = to_loopback_url(request.host_url.rstrip("/") + path)
    ir = InspectRequest(
        render=RenderRequest(url=url, viewport_w=page.canvas.w, viewport_h=page.canvas.h),
        script=_REPORT_JS,
    )
    try:
        report = inspect_composed(ir, pool=current_app.config.get("BROWSER_POOL"))
    except Exception as err:
        return _err(502, f"render report failed: {type(err).__name__}: {err}")
    if not isinstance(report, dict):
        report = {"board": {}, "elements": []}
    return jsonify({"id": page_id, "rev": _pr._canvas_rev(page), **report})


@bp.post("/measure-text")
def measure_text() -> Response:
    """Measure how wide/tall text renders in a given font, so an agent can size a
    box to its content instead of guessing (and clipping). Body: a single
    ``{text, font?, size?, weight?, max_width?}`` or ``{items:[...]}`` batch.
    Returns ``{items:[{text,width,height,fits}]}`` where ``fits`` is whether the
    text is within ``max_width`` (null when none given). Fonts are the widget
    fonts by family name (see the catalog's appearance.fonts)."""
    body = request.get_json(silent=True) or {}
    maybe_items = body.get("items")
    raw: list[Any] = maybe_items if isinstance(maybe_items, list) else [body]
    items = [i for i in raw if isinstance(i, dict) and isinstance(i.get("text"), str)]
    if not items:
        return _err(400, "provide {text, ...} or {items:[{text, ...}]}")
    items = items[:200]
    from app.renderer import InspectRequest, RenderRequest, inspect_composed, to_loopback_url

    path = url_for("composer.compose_measure")
    url = to_loopback_url(request.host_url.rstrip("/") + path)
    script = "() => window.__measure(" + json.dumps(items) + ")"
    ir = InspectRequest(
        render=RenderRequest(url=url, viewport_w=400, viewport_h=200), script=script
    )
    try:
        measured = inspect_composed(ir, pool=current_app.config.get("BROWSER_POOL"))
    except Exception as err:
        return _err(502, f"measure failed: {type(err).__name__}: {err}")
    return jsonify({"items": measured if isinstance(measured, list) else []})


@bp.post("/pages/<page_id>/push")
def push(page_id: str) -> Response:
    """Render the canvas and push it to explicit devices. Body: ``{device_ids: []}``
    (required — push is never implicit). Returns which devices got it and errors."""
    page = _pr._get_canvas(page_id)
    if page is None or page.canvas is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    body = request.get_json(silent=True) or {}
    picked = body.get("device_ids")
    device_ids = (
        [str(x) for x in picked if isinstance(x, str) and x] if isinstance(picked, list) else []
    )
    if not device_ids:
        return _err(400, "device_ids is required (push is always explicit)")

    manager = current_app.config.get("PUSH_MANAGER")
    if manager is None:
        return _err(503, "push pipeline unavailable")
    png = _render_png(page_id, page.canvas)
    sent: list[str] = []
    errors: list[dict[str, str]] = []
    for did in device_ids:
        try:
            result = manager.push_image(png, source_label=f"mcp:{page_id}", device_id=did)
        except Exception as err:  # a renderer / broker fault shouldn't 500 the route
            errors.append({"device": did, "error": f"{type(err).__name__}: {err}"})
            continue
        if getattr(result, "status", "") in ("sent", "no_change"):
            sent.append(did)
        else:
            errors.append(
                {
                    "device": did,
                    "error": str(
                        getattr(result, "error", None) or getattr(result, "status", "failed")
                    ),
                }
            )
    return jsonify({"sent": sent, "errors": errors})


def register(app: Flask) -> None:
    app.register_blueprint(bp)
