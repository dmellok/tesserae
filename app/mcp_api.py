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
import logging
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Blueprint, Flask, abort, current_app, g, jsonify, request, url_for
from pydantic import ValidationError
from werkzeug.wrappers import Response

from app import agent_activity, experiments, mcp_bridge
from app import panels_routes as _pr
from app.auth import _is_loopback
from app.panels_schema import build_catalog
from app.state.panel_store import CanvasLayout, CanvasPage, ConfigInput
from app.state.settings_store import SettingsStore
from app.touch_spec import PRIMITIVE_KINDS
from app.webhook_routes import _presented_token, generate_token

bp = Blueprint("mcp_api", __name__, url_prefix="/api/mcp")

logger = logging.getLogger(__name__)

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
    """404 when the experiment is off; otherwise allow loopback or a valid token.

    An authorised call also records which client made it (:mod:`app.mcp_bridge`),
    so Settings can show the connected bridge and flag an out-of-date one. Only
    authorised calls are recorded, so an unauthenticated prod can't write there.
    """
    if not experiments.is_enabled(_EXPERIMENT):
        abort(404)
    if not _authorised():
        return _err(
            401,
            "unauthorized: present the MCP token as 'Authorization: Bearer <token>' "
            "(generate one in Settings → System → MCP), or call from localhost.",
        )
    mcp_bridge.record_request(_settings(), request.headers.get("User-Agent", ""))
    g.mcp_started = time.monotonic()
    return None


@bp.after_request
def _narrate(response: Response) -> Response:
    """Record the call on the agent-activity bus, so the canvas editor's rail
    and the admin shell's follow toast can show the build as it happens.

    Only calls that got past :func:`_gate` carry a start time, so a 401/404 is
    never narrated. Best-effort throughout: a step is decoration, and failing to
    describe a call must not fail the call.
    """
    started = g.pop("mcp_started", None)
    if started is None:
        return response
    endpoint = (request.endpoint or "").rsplit(".", 1)[-1]
    try:
        payload = (
            response.get_json(silent=True)
            if endpoint in agent_activity._READS_RESPONSE and response.is_json
            else None
        )
        target, detail, page_id = agent_activity.summarise(
            endpoint,
            request.view_args,
            request.get_json(silent=True) if request.is_json else None,
            payload if isinstance(payload, dict) else None,
        )
        agent_activity.bus().record(
            endpoint=endpoint,
            status="ok" if response.status_code < 400 else "error",
            code=response.status_code,
            duration_ms=int((time.monotonic() - started) * 1000),
            target=target,
            detail=detail,
            page_id=page_id,
            # Declared lengths only: reading a body to size it would defeat the
            # point on the endpoints that stream. None (a streamed response)
            # counts as zero rather than guessing.
            bytes_in=request.content_length or 0,
            bytes_out=response.content_length or 0,
        )
    except Exception:
        logger.debug("narrating MCP call %r failed", endpoint, exc_info=True)
    return response


def _authorised() -> bool:
    """Loopback is trusted (a co-located agent needs zero config); anything else
    presents the stored MCP token as a bearer token."""
    if _is_loopback():
        return True
    stored = mcp_token(_settings())
    presented = _presented_token(request)
    return bool(stored and presented and secrets.compare_digest(presented, stored))


def _err(status: int, message: str, **extra: Any) -> Response:
    resp = jsonify({"error": message, **extra})
    resp.status_code = status
    return resp


def _data_root() -> Any:
    return current_app.config["DATA_ROOT"]


# -- vendored code-element toolkit + icon set ---------------------------

_PHOSPHOR_MANIFEST = (
    Path(__file__).resolve().parents[1] / "static" / "icons" / "phosphor" / "manifest.json"
)
_PHOSPHOR_WEIGHTS = ("thin", "light", "regular", "bold", "fill", "duotone")
_ICON_CACHE: list[str] | None = None

# The JS libraries preloaded in the code-element sandbox, so an agent can
# discover the toolkit structurally instead of only from the prose docs. Each
# auto-loads when its global is referenced; unused ones cost nothing.
_LIBRARIES: list[dict[str, str]] = [
    {
        "name": "Chart.js",
        "global": "Chart",
        "purpose": (
            "Charts to a <canvas> (animations already off). Plugins auto-registered: "
            "ChartDataLabels (window.ChartDataLabels) to bake values onto bars/points, "
            "and a Sankey chart type (chartjs-chart-sankey) for flow diagrams."
        ),
    },
    {
        "name": "canvas-gauges",
        "global": "RadialGauge / LinearGauge",
        "purpose": "Dials and meters (temperature, fuel-style).",
    },
    {
        "name": "Day.js",
        "global": "dayjs",
        "purpose": "Date/time parse + format; utc + timezone plugins pre-extended.",
    },
    {
        "name": "qrcode",
        "global": "qrcode",
        "purpose": "QR codes via .createSvgTag() / .createImgTag().",
    },
    {"name": "marked", "global": "marked", "purpose": "Markdown -> HTML string."},
    {
        "name": "chroma.js",
        "global": "chroma",
        "purpose": "Colour parsing/scales for rich fills + gradients.",
    },
    {
        "name": "SVG.js",
        "global": "SVG",
        "purpose": "Programmatic vector graphics (rings, arcs, badges).",
    },
    {
        "name": "Phosphor icons",
        "global": "(CSS classes, not a JS global)",
        "purpose": (
            "Icon font, six weights (ph, ph-bold, ph-thin, ph-light, ph-fill, ph-duotone). "
            "Search valid names at GET /api/mcp/icons?q=<term>."
        ),
    },
]


def _phosphor_icons() -> list[str]:
    """The vendored Phosphor icon slugs (from the on-disk manifest), cached."""
    global _ICON_CACHE
    if _ICON_CACHE is None:
        try:
            raw = json.loads(_PHOSPHOR_MANIFEST.read_text(encoding="utf-8"))
            _ICON_CACHE = (
                sorted(s for s in raw if isinstance(s, str)) if isinstance(raw, list) else []
            )
        except Exception:
            _ICON_CACHE = []
    return _ICON_CACHE


def _norm_icon_slug(name: Any) -> str:
    """Normalise an icon reference to the manifest's bare slug form: lowercase,
    the ``ph-`` prefix stripped, underscores as dashes. The surfaces disagree
    (icon elements accept both ``heart`` and ``ph-heart``; bind tables and code
    markup use ``ph-heart``), so validation and search meet them all halfway."""
    return str(name).strip().lower().removeprefix("ph-").replace("_", "-")


# ``ph-<slug>`` tokens in authored markup, excluding tokens preceded by a word
# char or "-" (so ``--ph-color`` custom properties and ``graph-line`` class
# names don't false-match).
_PH_TOKEN_RE = re.compile(r"(?<![\w-])ph-([a-z0-9]+(?:-[a-z0-9]+)*)\b")


def _invalid_icons(layout: CanvasLayout) -> list[dict[str, Any]]:
    """Icon references on the canvas that resolve to NO glyph, so a blank box
    is named instead of silently rendered. Checked per element: an ``icon``
    kind's slug and weight, every ``icon``-transform bind table value, and a
    heuristic scan of code/html/svg markup for ``ph-<slug>`` classes that
    aren't real Phosphor names (weight classes excluded). Purely server-side
    against the vendored manifest; an empty manifest disables the check
    rather than flagging everything."""
    icons = set(_phosphor_icons())
    if not icons:
        return []
    weights = set(_PHOSPHOR_WEIGHTS)
    out: list[dict[str, Any]] = []
    for e in layout.els:
        if e.kind == "icon":
            slug = _norm_icon_slug(e.icon or "")
            if slug and slug not in icons:
                out.append(
                    {
                        "el": e.id,
                        "icon": str(e.icon),
                        "reason": "no such Phosphor icon; search list_icons(q) for a real slug",
                    }
                )
            w = (e.weight or "bold").strip().lower()
            if w not in weights:
                out.append(
                    {
                        "el": e.id,
                        "weight": str(e.weight),
                        "reason": (
                            "weight must be one of thin|light|regular|bold|fill|duotone "
                            "(the renderer falls back to bold)"
                        ),
                    }
                )
        for b in e.bind or []:
            if b.transform != "icon":
                continue
            table = b.params.get("table")
            names = list(table.values()) if isinstance(table, dict) else []
            if b.params.get("default"):
                names.append(b.params["default"])
            for n in names:
                slug = _norm_icon_slug(n)
                if slug and slug not in icons:
                    out.append(
                        {
                            "el": e.id,
                            "icon": str(n),
                            "reason": (
                                "bind icon transform maps to no Phosphor icon; "
                                "search list_icons(q) for a real slug"
                            ),
                        }
                    )
        if e.kind in ("code", "html", "svg"):
            blob = "\n".join(filter(None, (e.html or "", e.css or "", e.js or "")))
            for tok in sorted(set(_PH_TOKEN_RE.findall(blob))):
                if tok in weights or tok in icons:
                    continue
                out.append(
                    {
                        "el": e.id,
                        "icon": f"ph-{tok}",
                        "reason": (
                            "no such Phosphor icon (heuristic markup scan); "
                            "search list_icons(q) for a real slug"
                        ),
                    }
                )
    return out[:50]


# -- catalog / widget options -------------------------------------------


@bp.get("/catalog")
def catalog() -> Response:
    """Every renderable widget (with its fragments) plus theme/style/font options,
    the vendored code-element libraries, and the icon set, so the agent knows what
    it can place, how to style the canvas, and what's in the code-element toolkit.

    The per-widget ``sample`` payload is omitted here to keep the response small;
    fetch a widget's live data shape with ``POST /widgets/<key>/data`` instead.
    The full icon name list is searched via ``GET /icons?q=`` rather than inlined."""
    widgets = build_catalog(_pr._registry())
    lean = [{k: v for k, v in w.items() if k != "sample"} for w in widgets]
    return jsonify(
        {
            "widgets": lean,
            "appearance": _pr._appearance(),
            "libraries": _LIBRARIES,
            "icons": {
                "total": len(_phosphor_icons()),
                "weights": list(_PHOSPHOR_WEIGHTS),
                "search_endpoint": "/api/mcp/icons?q=<term>",
                "usage": (
                    'icon element {"kind":"icon","icon":"<slug>"}; '
                    'in code markup <i class="ph-bold ph-<slug>"></i>'
                ),
            },
        }
    )


@bp.get("/icons")
def icons() -> Response:
    """Search the vendored Phosphor icon set (all six weights). Use a returned
    slug as an ``icon`` element's ``"icon"`` value, or in code markup as
    ``ph-<slug>`` (weight via ph / ph-bold / ph-thin / ph-light / ph-fill /
    ph-duotone). ``?q=`` substring-filters the names; the query is normalised
    to slug form first (``ph-`` prefix stripped, underscores as dashes), so
    ``ph-heart`` and ``calendar_heart`` match. Without a query a capped sample
    is returned alongside the total. ``?limit=`` caps results (default 100, max 500)."""
    all_icons = _phosphor_icons()
    q = _norm_icon_slug(request.args.get("q") or "")
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 500))
    except (TypeError, ValueError):
        limit = 100
    matches = [s for s in all_icons if q in s] if q else all_icons
    return jsonify(
        {
            "weights": list(_PHOSPHOR_WEIGHTS),
            "total": len(all_icons),
            "matched": len(matches),
            "icons": matches[:limit],
            "usage": (
                'icon element {"kind":"icon","icon":"<slug>"}; '
                'in code markup <i class="ph-bold ph-<slug>"></i>'
            ),
        }
    )


@bp.get("/services")
def services() -> Response:
    """Service plugins (kind ``service``): non-placeable data sources that expose a
    whole external service's API for a code element to pull from. They don't show
    up in ``/catalog`` (they can't be placed on the canvas), but you use them
    exactly like a widget source: their ``key`` is a valid ``source`` for a
    ``code`` / ``data`` element, and ``POST /widgets/<key>/data`` (probe) and
    ``GET /widgets/<key>/options`` work on them unchanged.

    Returns ``{services: [{key, name, description, options}]}``. A service's
    ``options`` pick which slice of the API to fetch (a scope / endpoint /
    entity). Probe with empty options first: by convention a service returns a
    self-describing map of the scopes it offers, so the agent can explore the API
    before choosing what to source."""
    reg = _pr._registry()
    out = [
        {
            "key": p.id,
            "name": p.manifest.get("name", p.id),
            "description": p.manifest.get("description", ""),
            "options": _pr._materialised_options(p),
        }
        for p in reg.plugins.values()
        if getattr(p, "kind", "") == "service"
    ]
    out.sort(key=lambda e: str(e["name"]).lower())
    return jsonify({"services": out})


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
    # ``?fresh=true`` bypasses caches (ctx["fresh"]) so a just-edited server.py is
    # reflected immediately instead of a stale cached payload.
    fresh = request.args.get("fresh") in ("1", "true", "True")
    live: Any = None
    try:
        live = _fetch_plugin_data(
            key, opts, 600, 400, preview=False, cell_w=600, cell_h=400, fresh=fresh
        )
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


@bp.post("/probe-url")
def probe_url() -> Response:
    """Fetch a raw JSON API URL for field discovery before wiring it into a code
    element as a URL data source. Body: ``{url, headers?}``. Returns ``{url,
    data, data_source, fields}``. This is the no-plugin path: hand over any
    public https JSON endpoint and get back its parsed shape, then set it on a
    code element via ``sources: [{url, headers?, name}]`` (delivered at
    ``ctx.data[name]``). Fetched through the SSRF guard, so loopback / private
    hosts are refused."""
    from app.net_guard import BlockedURLError, fetch_json

    body = request.get_json(silent=True) or {}
    url = str(body.get("url") or "").strip()
    if not url:
        return _err(422, "url is required")
    raw_headers = body.get("headers")
    headers = (
        {str(k): str(v) for k, v in raw_headers.items()} if isinstance(raw_headers, dict) else None
    )
    try:
        data = fetch_json(url, headers=headers)
    except BlockedURLError as err:
        return jsonify(
            {"url": url, "data": {"error": str(err)}, "data_source": "error", "fields": []}
        )
    except Exception as err:
        return jsonify(
            {
                "url": url,
                "data": {"error": f"{type(err).__name__}: {err}"},
                "data_source": "error",
                "fields": [],
            }
        )
    return jsonify(
        {
            "url": url,
            "data": data,
            "data_source": "live",
            "fields": _flatten_fields(data) if isinstance(data, dict) else [],
        }
    )


# -- widget push / install (Tesserae Studio) ----------------------------


_RELOAD_MODES = ("auto", "in_process", "restart", "none")


def _reload_registry(mode: str, *, restart_if_blueprint: bool) -> dict[str, Any]:
    """Bring pushed-widget changes live. ``in_process`` rebuilds the registry and
    swaps it (fast, no dropped connections); ``restart`` re-execs the process (needed
    to register a new admin ``blueprint()``); ``auto`` picks in-process unless a
    blueprint is involved or the rebuild fails; ``none`` does nothing. Raises on a
    hard failure so the caller can surface it."""
    app = current_app._get_current_object()  # type: ignore[attr-defined]

    def _restart() -> dict[str, Any]:
        updater = app.config.get("UPDATER")
        if updater is None:
            raise RuntimeError("restart unavailable (no updater configured)")
        updater.restart(delay_s=1.0)
        return {"reload": "restart", "restarting": True, "reloaded": False}

    if mode == "none":
        return {"reload": "none", "restarting": False, "reloaded": False}
    if mode == "restart" or (mode == "auto" and restart_if_blueprint):
        return _restart()

    rediscover = app.config.get("REDISCOVER_PLUGINS")
    if rediscover is None:
        if mode == "in_process":
            raise RuntimeError("in-process reload unavailable")
        return _restart()
    try:
        new_registry = rediscover()
    except Exception:
        logger.exception("in-process plugin reload failed")
        if mode == "in_process":
            raise
        return _restart()
    # Atomic swap (single config assignment). Derived views (catalog, options)
    # read the registry fresh per request, so nothing else needs invalidating.
    app.config["PLUGIN_REGISTRY"] = new_registry
    for perr in new_registry.errors:
        logger.warning("plugin reload: %s, %s", perr.plugin_id, perr.message)
    # ``reloaded`` + the re-imported server-module count let the caller tell a real
    # in-process reload (server.py re-imported) from a no-op it would otherwise
    # guess needed a restart.
    modules = sum(1 for p in new_registry.plugins.values() if p.server_module is not None)
    return {"reload": "in_process", "restarting": False, "reloaded": True, "modules": modules}


@bp.post("/widgets/install")
def install_widget() -> Response:
    """Install or upsert a single authored widget from an uploaded tarball into
    ``<data_root>/authored/<id>``, then reload. Body: a gzipped tar (``application/
    gzip``) whose root is the widget or a single folder containing it, or
    ``multipart/form-data`` with a ``tarball`` part. Query: ``id`` (override the id),
    ``reload`` (``auto`` | ``in_process`` | ``restart`` | ``none``, default ``auto``)."""
    from app import authored_widgets as aw

    if request.content_length and request.content_length > aw.MAX_COMPRESSED_BYTES:
        return _err(413, "tarball too large")
    if (request.content_type or "").startswith("multipart/form-data"):
        part = request.files.get("tarball")
        if part is None:
            return _err(400, "multipart body must include a 'tarball' file part")
        tar_bytes = part.read()
    else:
        tar_bytes = request.get_data(cache=False)
    if not tar_bytes:
        return _err(400, "empty request body (send the widget tarball)")

    mode = request.args.get("reload", "auto")
    if mode not in _RELOAD_MODES:
        return _err(400, f"reload must be one of {'|'.join(_RELOAD_MODES)}")

    try:
        result = aw.install_tarball(
            tar_bytes,
            data_root=current_app.config["DATA_ROOT"],
            plugins_dir=current_app.config["PLUGINS_DIR"],
            schema_path=current_app.config["PLUGIN_SCHEMA"],
            id_override=request.args.get("id") or None,
        )
    except aw.InstallError as err:
        return _err(err.status, err.message)

    try:
        rel = _reload_registry(mode, restart_if_blueprint=bool(result["blueprint"]))
    except Exception as err:
        return _err(500, f"installed but reload failed: {err}")

    active = not rel["restarting"] and _pr._registry().get(result["id"]) is not None
    return jsonify(
        {
            "ok": True,
            "id": result["id"],
            "version": result["version"],
            "installed": True,
            "reload": rel["reload"],
            "reloaded": rel.get("reloaded", False),
            "active": active,
            "restarting": rel["restarting"],
        }
    )


@bp.delete("/widgets/<key>")
def uninstall_widget(key: str) -> Response:
    """Uninstall a pushed widget (only ever removes entries under
    ``<data_root>/authored/``). Query ``reload`` as for install."""
    from app import authored_widgets as aw

    data_root = current_app.config["DATA_ROOT"]
    if not aw.uninstall(key, data_root=data_root):
        return _err(404, f"no pushed widget {key!r} under authored/")
    mode = request.args.get("reload", "auto")
    if mode not in _RELOAD_MODES:
        return _err(400, f"reload must be one of {'|'.join(_RELOAD_MODES)}")
    try:
        rel = _reload_registry(mode, restart_if_blueprint=aw.any_blueprint(data_root))
    except Exception as err:
        return _err(500, f"uninstalled but reload failed: {err}")
    return jsonify(
        {
            "ok": True,
            "id": key,
            "reload": rel["reload"],
            "reloaded": rel.get("reloaded", False),
            "active": False,
            "restarting": rel["restarting"],
        }
    )


@bp.post("/reload")
def reload_plugins() -> Response:
    """Reload the plugin registry without installing anything. Body/query ``mode``
    (``auto`` | ``in_process`` | ``restart``, default ``auto``)."""
    from app import authored_widgets as aw

    body = request.get_json(silent=True) or {}
    mode = str(body.get("mode") or request.args.get("mode") or "auto")
    if mode not in _RELOAD_MODES:
        return _err(400, f"mode must be one of {'|'.join(_RELOAD_MODES)}")
    try:
        rel = _reload_registry(
            mode, restart_if_blueprint=aw.any_blueprint(current_app.config["DATA_ROOT"])
        )
    except Exception as err:
        return _err(500, f"reload failed: {err}")
    return jsonify(
        {
            "ok": True,
            "mode": rel["reload"],
            "reloaded": rel.get("reloaded", False),
            "modules": rel.get("modules", 0),
            "restarting": rel["restarting"],
        }
    )


@bp.get("/widgets")
def list_authored_widgets() -> Response:
    """List pushed widgets so a client can reconcile. Requires ``?origin=authored``."""
    if request.args.get("origin") != "authored":
        return _err(400, "pass ?origin=authored to list pushed widgets")
    from app import authored_widgets as aw

    widgets = aw.list_authored(current_app.config["DATA_ROOT"], _pr._registry())
    return jsonify({"widgets": widgets})


@bp.get("/widgets/<key>/render.png")
def widget_render(key: str) -> Response:
    """Faithful e-ink render of a single widget as a PNG, over the authed MCP
    surface (so a client can preview against a remote / HA instance where
    ``/_test/render`` is not reachable). Query: ``size`` (xs|sm|md|lg, default
    lg; ``lg`` is 1200x800) OR explicit ``w`` & ``h`` (clamped); ``options`` (or
    ``opts``, JSON cell options); optional ``theme`` / ``style`` / ``sample`` /
    ``zoom``; ``fragment`` (render one declared fragment, default the whole
    widget); ``fresh=true`` (bypass caches so a just-edited server.py is
    reflected now).

    Screenshot Contract error semantics: unknown widget -> 404, unknown fragment
    or bad ``options`` JSON -> 400, render unavailable -> 503. Never 200 + a
    blank / HTML body."""
    from urllib.parse import urlencode

    from app.composer import SIZE_DIMENSIONS, clamp_screenshot_dim
    from app.panels_schema import fragments_of
    from app.renderer import RenderRequest, render_to_png, to_loopback_url

    plugin = _pr._registry().get(key)
    if plugin is None:
        return _err(404, f"unknown widget {key!r}")
    # Explicit ``w,h`` override the size preset (Screenshot Contract); clamped
    # so a typo can't ask the browser for a runaway viewport. Absent, the
    # xs|sm|md|lg mapping stands (``lg`` == 1200x800).
    w_arg = request.args.get("w")
    h_arg = request.args.get("h")
    size = request.args.get("size", "lg")
    if w_arg is not None or h_arg is not None:
        try:
            w = clamp_screenshot_dim(int(w_arg))  # type: ignore[arg-type]
            h = clamp_screenshot_dim(int(h_arg))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return _err(400, "w and h must both be integers")
    else:
        if size not in SIZE_DIMENSIONS:
            return _err(400, f"size must be one of {'|'.join(sorted(SIZE_DIMENSIONS))}")
        w, h = SIZE_DIMENSIONS[size]
    fragment = request.args.get("fragment")
    if fragment and fragment != "full" and fragment not in {f["id"] for f in fragments_of(plugin)}:
        return _err(400, f"widget {key!r} has no fragment {fragment!r}")
    # ``options`` is the Screenshot Contract spelling; ``opts`` is the existing
    # one. Validate up front: bad JSON is a 400 here (the dev preview silently
    # falls through to defaults, which would read as a passing screenshot).
    opts_raw = request.args.get("opts") or request.args.get("options")
    if opts_raw:
        try:
            parsed = json.loads(opts_raw)
        except (json.JSONDecodeError, ValueError):
            return _err(400, "options must be valid JSON")
        if not isinstance(parsed, dict):
            return _err(400, "options must be a JSON object")
    params: dict[str, str] = {"plugin": key, "size": size}
    if w_arg is not None or h_arg is not None:
        params["w"] = str(w)
        params["h"] = str(h)
    if opts_raw:
        params["opts"] = opts_raw
    for arg in ("theme", "style", "sample", "zoom", "fragment", "fresh"):
        val = request.args.get(arg)
        if val:
            params[arg] = val
    path = url_for("composer.test_render") + "?" + urlencode(params)
    url = to_loopback_url(request.host_url.rstrip("/") + path)
    try:
        png = render_to_png(
            RenderRequest(url=url, viewport_w=w, viewport_h=h),
            pool=current_app.config.get("BROWSER_POOL"),
        )
    except Exception as err:
        return _err(503, f"render unavailable: {type(err).__name__}: {err}")
    return current_app.response_class(png, mimetype="image/png")


# -- devices ------------------------------------------------------------


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _gamut_info(gamut: str) -> dict[str, Any]:
    """Map a panel gamut to a human colour_mode + the actual palette hex list. The
    panel dithers the full-colour render down to these inks, so the agent should
    design in full colour and use this palette to keep fine detail (thin text /
    icons) crisp and to honour the ``mono`` flag, not to flatten the whole layout
    to the raw inks. The one real trap is the inverse: painting a colourful design
    for a genuinely grayscale (``mono``) panel."""
    from app import quantizer as q

    # Panels declare chemistry aliases (``spectra_6``, ``acep_7colour``) that the
    # rest of the stack collapses onto the canonical packer targets
    # (``waveshare_e6``, ``inky_7colour``). Resolve the alias here too, otherwise
    # a Spectra 6 / ACeP panel misses the table below, falls through to the empty
    # palette, and reports as mono. Only accepted values are canonicalised so an
    # unrecognised string still degrades to the conservative "unknown" default
    # rather than the ``waveshare_e6`` fallback ``canonicalise_gamut`` applies.
    resolved = q.canonicalise_gamut(gamut) if gamut in q.ACCEPTED_GAMUTS else gamut

    # Full-colour transports carry no fixed ink palette but must not read as mono.
    if resolved in ("rgb24", "rgb16"):
        bits = "24" if resolved == "rgb24" else "16"
        return {
            "gamut": gamut,
            "color_mode": f"full colour ({bits}-bit)",
            "colors": [],
            "mono": False,
        }

    table: dict[str, tuple[str, tuple[tuple[int, int, int], ...]]] = {
        "waveshare_e6": ("6-colour (Spectra 6)", q.WAVESHARE_E6_PALETTE),
        "inky_7colour": ("7-colour (ACeP)", q.INKY_7COLOUR_PALETTE),
        "bwry_4": ("4-colour · black/white/red/yellow", q.BWRY_4_PALETTE),
        "bwr_3": ("3-colour · black/white/red", q.BWR_3_PALETTE),
        "gray_4": ("4-level grayscale", q.GRAY_4_PALETTE),
        "gray_16": ("16-level grayscale", q.GRAY_16_PALETTE),
        "mono": ("black & white", ()),
    }
    label, palette = table.get(resolved, (resolved or "unknown", ()))
    colors = [_hex(c) for c in palette]
    # ``gray_4`` / ``gray_16`` carry multiple ink levels but are chromatically
    # grayscale, so they should still force a grayscale layout; ``mono`` is
    # grayscale by definition.
    grayscale = resolved in ("mono", "gray_4", "gray_16")
    return {
        "gamut": gamut,
        "color_mode": label,
        "colors": colors,
        "mono": grayscale or len(colors) <= 2,
    }


@bp.get("/devices")
def devices() -> Response:
    """Registered display instances a canvas can be pushed to, with panel dims and
    colour capability (gamut, a friendly ``color_mode``, the renderable palette as
    hex, an ``orientation``, and a ``mono`` flag) so the agent can size and colour
    a canvas to the hardware. The panel dithers the full-colour render to these
    inks, so the palette guides fine detail, it doesn't cap the whole design; only
    the ``mono`` flag should force a grayscale layout.

    ``touch: true`` appears on panels with a touch digitizer (issue #49), meaning
    on_tap / on_swipe / on_slide actions on the dashboard's elements will actually
    fire on that hardware. Its absence means the panel is display-only (or
    button-driven), so touch actions won't do anything there.

    Firmware capabilities (from the device's live heartbeats) ride along when
    present: ``overlay: {schema, max_targets}`` means the panel does local
    partial-refresh overlays, so up to ``max_targets`` tap zones echo
    instantly and ``data-overlay-key`` value slots repaint with live values
    between full renders. ``schema: 2`` additionally means frame patches:
    after a tap fires an HA action, and on periodic re-renders that only
    move small chrome (a header clock), the panel partial-paints just the
    changed regions instead of full-flashing, so a control dashboard's
    state stays truthful and a clock in the header doesn't cost a full
    e-ink repaint per tick (``schema: 1`` panels converge via a debounced
    full re-push a few seconds after a tap burst instead).
    ``proto: {v: 2}`` means the panel speaks protocol v2 (interaction
    manifests + state bundles + a push stream): it hit-tests touch locally
    against stable region ids and gives instant local feedback — inverts,
    pre-shipped state tiles, live slider thumbs — with the server
    confirming by patch. On these panels, pin ids on interactive
    code-element markup with ``data-touch-id`` so optimistic feedback
    survives markup edits (unpinned regions fall back to plain invert),
    and treat ``max_targets`` as a HARD interactive-region cap: zones
    beyond it are trimmed from the manifest and do not fire at all (v1
    panels merely lose the instant echo). The trim keeps navigation,
    then sliders, then taps, then swipes, document order within each
    class; keep dashboards inside the device's advertised budget or
    split them across deck pages.
    ``deck_cache: {capacity_bytes}`` means the device caches deck frames on
    local storage and navigates decks without a network round trip. All are
    read-only facts about the hardware; their absence just means those
    features silently don't apply there."""
    from app.panel import device_panel

    reg = current_app.config.get("DEVICE_REGISTRY")
    status_cache = current_app.config.get("DEVICE_STATUS") or {}
    out: list[dict[str, Any]] = []
    if reg is not None:
        for d in reg.all():
            if d.kind_of is None:  # instances only, not built-in kinds
                continue
            entry: dict[str, Any] = {
                "id": d.id,
                "name": str(d.manifest.get("name") or d.id),
                "kind": d.kind_of,
            }
            panel = device_panel(d)
            if panel is not None:
                entry["w"] = panel.w
                entry["h"] = panel.h
                entry.update(_gamut_info(panel.gamut))
                block = d.manifest.get("panel") or {}
                if isinstance(block, dict) and block.get("orientation"):
                    entry["orientation"] = str(block["orientation"])
            if d.manifest.get("touch") is True:
                entry["touch"] = True
            status = status_cache.get(d.id) if isinstance(status_cache, dict) else None
            if isinstance(status, dict):
                overlay_cap = status.get("overlay")
                if isinstance(overlay_cap, dict):
                    # max_targets = how many tap zones get instant echo on
                    # this panel (v1.8 firmware baseline is 8); schema 2 =
                    # the panel applies post-action frame patches.
                    entry["overlay"] = {
                        "schema": int(overlay_cap.get("schema") or 1),
                        "max_targets": overlay_cap.get("max_targets", 8),
                    }
                proto_cap = status.get("proto")
                if isinstance(proto_cap, dict) and proto_cap.get("v"):
                    entry["proto"] = {"v": int(proto_cap["v"])}
                deck_cache = status.get("deck_cache")
                if isinstance(deck_cache, dict):
                    entry["deck_cache"] = {
                        "capacity_bytes": int(deck_cache.get("capacity_bytes") or 0)
                    }
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


@bp.delete("/pages/<page_id>")
def delete_page(page_id: str) -> Response:
    """Delete a canvas dashboard (e.g. a throwaway QA page). Only removes canvas
    pages, same guardrail as the other page writes; 404 if it isn't one."""
    if _pr._get_canvas(page_id) is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    _pr._pages().delete(page_id)
    return jsonify({"ok": True, "id": page_id})


@bp.get("/pages/<page_id>/assets")
def list_page_assets(page_id: str) -> Response:
    """List a dashboard's cached image assets. Returns ``{assets: [{name, url,
    bytes}]}``. Each ``url`` is a stable local path a code element can reference
    (``<img src="/page-assets/…">``) instead of hotlinking a remote URL."""
    from app import page_assets as _pa

    if _pr._get_canvas(page_id) is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    return jsonify({"assets": _pa.list_assets(_data_root(), page_id)})


@bp.post("/pages/<page_id>/assets")
def add_page_asset(page_id: str) -> Response:
    """Cache an image into a dashboard's asset folder so a code element can
    reference a stable local copy instead of hotlinking. Body: ``{url,
    headers?}`` to fetch a remote image (through the SSRF guard), or a multipart
    ``file`` upload. Returns ``{name, url, bytes}``. The folder is deleted with
    the dashboard, so assets never outlive their page."""
    from app import page_assets as _pa

    if _pr._get_canvas(page_id) is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    try:
        upload = request.files.get("file")
        if upload is not None:
            rec = _pa.save_bytes(
                _data_root(), page_id, upload.read(), upload.mimetype or "application/octet-stream"
            )
        else:
            body = request.get_json(silent=True) or {}
            url = str(body.get("url") or "").strip()
            if not url:
                return _err(422, "provide a 'url' to cache or a multipart 'file'")
            raw_headers = body.get("headers")
            headers = (
                {str(k): str(v) for k, v in raw_headers.items()}
                if isinstance(raw_headers, dict)
                else None
            )
            rec = _pa.cache_url(_data_root(), page_id, url, headers=headers)
    except _pa.AssetError as err:
        return _err(422, str(err))
    return jsonify(rec)


@bp.delete("/pages/<page_id>/assets/<name>")
def delete_page_asset(page_id: str, name: str) -> Response:
    """Remove one cached image from a dashboard's asset folder."""
    from app import page_assets as _pa

    if _pr._get_canvas(page_id) is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    try:
        removed = _pa.delete_asset(_data_root(), page_id, name)
    except _pa.AssetError as err:
        return _err(422, str(err))
    if not removed:
        return _err(404, f"no asset {name!r}")
    return jsonify({"ok": True, "name": name})


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
    _pr._coerce_canvas_update_policy(page)
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
    els = doc_in.get("els")
    if isinstance(els, list):
        for i, el in enumerate(els):
            if isinstance(el, dict):
                bad = _unknown_element_keys(el)
                if bad:
                    return _element_key_error(bad, index=i)
    try:
        doc = CanvasPage.model_validate(doc_in)
    except ValidationError as exc:
        return _err(422, "invalid canvas document", details=exc.errors(include_url=False))
    new_page = _pr._doc_to_page(doc)
    new_page.created_by = page.created_by  # preserve provenance
    _save_mcp(new_page)
    return _saved(new_page)


@bp.post("/pages/<page_id>/background")
def generate_background(page_id: str) -> Response:
    """Generate an AI background for a canvas (fal.ai) and set it as ``bg_image``.

    Body: ``{prompt (required), model?, style?, fit?, eink_friendly?, seed?}``.
    The image is generated from the prompt, stored as a local render asset, and
    the canvas' ``bg_image`` / ``bg_fit`` are updated. This is Approach A: the
    background is decorative and the data widgets composite on top, so the data
    never passes through the image model. Uses the canvas' own w/h for the
    aspect. 400 if no prompt or no fal key is configured; 502 if fal fails. Same
    drift guard + compact ack as the other canvas writes, plus ``bg_image``."""
    from app import fal_backgrounds as fb
    from app.state.panel_store import CanvasLayout

    page = _pr._get_canvas(page_id)
    if page is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    conflict = _drift_conflict(page)
    if conflict is not None:
        return conflict
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err(400, "body must be a JSON object")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return _err(400, "prompt is required")
    api_key = fb.resolve_fal_key(_pr._registry(), _settings())
    if not api_key:
        return _err(
            400,
            "no fal.ai API key configured: set one on an installed fal-image widget, "
            "under app.fal.api_key, or in the FAL_KEY environment variable",
        )
    layout = page.canvas or CanvasLayout()
    model = str(body.get("model") or fb.DEFAULT_MODEL).strip()
    style = str(body.get("style") or "none").strip()
    fit = str(body.get("fit") or layout.bg_fit or "cover").strip().lower()
    if fit not in ("cover", "contain", "stretch"):
        fit = "cover"
    seed_in = body.get("seed")
    seed = int(seed_in) if isinstance(seed_in, int) else None
    try:
        png = fb.generate(
            prompt,
            api_key=api_key,
            model=model,
            style=style,
            width=int(layout.w),
            height=int(layout.h),
            eink_friendly=bool(body.get("eink_friendly", True)),
            seed=seed,
        )
    except fb.FalError as err:
        return _err(502, f"background generation failed: {err}")
    url = fb.store_background(current_app.config["RENDERS_DIR"], png)
    layout.bg_image = url
    layout.bg_fit = fit
    page.canvas = layout
    _save_mcp(page)
    data = _saved(page).get_json()
    data.update({"bg_image": url, "model": model, "prompt": prompt})
    return jsonify(data)


def _unknown_element_keys(body: dict[str, Any]) -> list[str]:
    """Keys that aren't Element fields. Pydantic silently ignores unknown
    keys, so an agent that writes ``tap`` instead of ``on_tap`` (or nests
    an interaction under a made-up key) would get a 200 while the action
    evaporates, and then spend a session wondering why nothing fires.
    The MCP write paths 422 instead so the mistake surfaces on the spot."""
    from app.state.panel_store import Element

    return sorted(k for k in body if k not in Element.model_fields)


def _element_key_error(bad: list[str], *, index: int | None = None) -> Response:
    where = f"els[{index}]: " if index is not None else ""
    return _err(
        422,
        f"{where}unknown element field(s): {', '.join(bad)}. Touch actions "
        "use on_tap / on_swipe / on_slide (a code element's named map is "
        "'actions'); see the canvas doc shape for the full field list.",
        unknown_fields=bad,
    )


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
    bad = _unknown_element_keys(body)
    if bad:
        return _element_key_error(bad)
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


@bp.post("/pages/<page_id>/elements/bulk")
def append_elements_bulk(page_id: str) -> Response:
    """Append many elements to a canvas in one save. Body is
    ``{"elements": [ {element}, … ]}`` (max 500 per call). Built for large
    primitive boards that don't fit in a single ``set_canvas`` body: chunk
    the elements across a few bulk calls (each is one save, so an open
    editor updates as you go) instead of inlining the whole 20k+ document.

    All-or-nothing: if any element is invalid (unknown field or schema
    error) nothing is appended and the offending index is named, so a bad
    chunk never half-lands. Returns the ack plus ``element_ids`` in order.
    Supports ``?base_rev=`` drift guard."""
    from app.state.panel_store import CanvasLayout, Element

    page = _pr._get_canvas(page_id)
    if page is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    conflict = _drift_conflict(page)
    if conflict is not None:
        return conflict
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("elements"), list):
        return _err(400, 'body must be {"elements": [ {element}, … ]}')
    raw_els = body["elements"]
    if not raw_els:
        return _err(400, "elements must be a non-empty list")
    if len(raw_els) > 500:
        return _err(400, f"too many elements ({len(raw_els)}); send at most 500 per call")
    validated: list[Any] = []
    for i, raw in enumerate(raw_els):
        if not isinstance(raw, dict):
            return _err(422, f"els[{i}] must be an object")
        bad = _unknown_element_keys(raw)
        if bad:
            return _element_key_error(bad, index=i)
        el_in = raw if raw.get("id") else {**raw, "id": uuid.uuid4().hex[:8]}
        try:
            validated.append(Element.model_validate(el_in))
        except ValidationError as exc:
            return _err(422, f"els[{i}] is invalid", details=exc.errors(include_url=False))
    layout = page.canvas or CanvasLayout()
    layout.els.extend(validated)
    page.canvas = layout
    _save_mcp(page)
    data = _saved(page).get_json()
    data["element_ids"] = [e.id for e in validated]
    data["appended"] = len(validated)
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
    bad = _unknown_element_keys(patch)
    if bad:
        return _element_key_error(bad)
    merged = {**page.canvas.els[idx].model_dump(mode="json"), **patch, "id": element_id}
    try:
        element = Element.model_validate(merged)
    except ValidationError as exc:
        return _err(422, "invalid element after patch", details=exc.errors(include_url=False))
    page.canvas.els[idx] = element
    _save_mcp(page)
    return _saved(page)


@bp.post("/pages/<page_id>/elements/<element_id>/append")
def append_element_code(page_id: str, element_id: str) -> Response:
    """Append text to a ``code`` element's ``html`` / ``css`` / ``js`` and save,
    so an editor open on the page re-renders after each chunk (stream a code
    element in line by line rather than posting the whole blob at the end).

    Body: ``{field, text}`` where ``field`` is one of ``html`` | ``css`` | ``js``.
    Each call is one save, which is what pushes the live update to the editor.
    Returns the compact ack plus the field's new ``length``. Don't pass
    ``base_rev`` while streaming (the rev changes on every append); the returned
    ``rev`` lets you chain if you want. 422 if the append makes the element
    invalid, so a half-written element still round-trips."""
    from app.state.panel_store import Element

    page = _pr._get_canvas(page_id)
    if page is None or page.canvas is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    conflict = _drift_conflict(page)
    if conflict is not None:
        return conflict
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err(400, "body must be a JSON object")
    field = str(body.get("field") or "")
    if field not in ("html", "css", "js"):
        return _err(400, "field must be one of html|css|js")
    text = body.get("text")
    if not isinstance(text, str):
        return _err(400, "text must be a string")
    idx = next((i for i, e in enumerate(page.canvas.els) if e.id == element_id), None)
    if idx is None:
        return _err(404, f"no element {element_id!r} on {page_id!r}")
    current = page.canvas.els[idx]
    merged = {**current.model_dump(mode="json"), field: (getattr(current, field) or "") + text}
    try:
        element = Element.model_validate(merged)
    except ValidationError as exc:
        return _err(422, "invalid element after append", details=exc.errors(include_url=False))
    page.canvas.els[idx] = element
    _save_mcp(page)
    data = _saved(page).get_json()
    data["length"] = len(getattr(element, field))
    return jsonify(data)


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
    """Update document-level fields (name, size, appearance, config inputs) without
    touching the elements. Body accepts any of
    ``{name,w,h,theme,style,font,bg,bg_image,bg_fit,inputs}``. Use ``set_canvas`` /
    element endpoints for ``els``. Supports ``?base_rev=``.

    ``inputs`` replaces the dashboard's declared config surface wholesale, so an
    agent that has just placed the elements can declare what the finished
    dashboard asks its operator in one call without resending the layout."""
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
    if "inputs" in body:
        if not isinstance(body["inputs"], list):
            return _err(400, "inputs must be a list")
        try:
            layout.inputs = [ConfigInput.model_validate(item) for item in body["inputs"]]
        except ValidationError as exc:
            return _err(422, "invalid config inputs", details=exc.errors(include_url=False))
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


def _render_png(page_id: str, layout: CanvasLayout, *, fresh: bool = False) -> bytes:
    """Screenshot the shared ``/compose/<id>`` target at the canvas dims, the same
    path a device push and the editor preview use. ``fresh`` re-fetches widget
    data (skips the last-good fallback + widget caches via ``ctx["fresh"]``)."""
    from app.renderer import RenderRequest, render_to_png, to_loopback_url

    path = url_for("composer.compose", page_id=page_id)
    if fresh:
        path += "?fresh=1"
    url = to_loopback_url(request.host_url.rstrip("/") + path)
    return render_to_png(
        RenderRequest(url=url, viewport_w=layout.w, viewport_h=layout.h),
        pool=current_app.config.get("BROWSER_POOL"),
    )


@bp.get("/pages/<page_id>/preview.png")
def preview(page_id: str) -> Response:
    """Render the canvas to a PNG at its authored dims (the agent's feedback loop).

    ``?fresh=1`` bypasses widget-data caches (last-good fallback +
    ``ctx["fresh"]``), so a mid-debug preview reflects the current data, not a
    stale cached result."""
    page = _pr._get_canvas(page_id)
    if page is None or page.canvas is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    fresh = request.args.get("fresh") in ("1", "true", "True")
    png = _render_png(page_id, page.canvas, fresh=fresh)
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
    // Vendored bundles the code-element sandboxes inlined. Reported without
    // ?debug=1 because the choice is INFERRED from the element's own code: an
    // element can end up with a stylesheet nobody asked for, and that has to be
    // visible without knowing to go looking for it.
    injected_libs: (window.__tesseraeLibReport || []).filter(
      (r) => (r.libs || []).length || r.autolibs === false,
    ),
  };
}"""


# In-page half of the ``?debug=1`` diagnostics (the Playwright-event half
# lives in app.renderer._attach_diag_listeners). Collects what only the page
# itself can see: per-font-face load status (with the @font-face src when
# reachable), a best-effort report of authored element CSS the browser
# silently dropped (parser-rejected rules, invalid declarations, @import in
# the no-network sandbox), and which vendored bundles each code element
# actually inlined. Runs AFTER the settle waits, so "pending-at-capture"
# means exactly that: the screenshot would have shipped without this font.
_DIAG_JS: str = r"""() => {
  const unq = (s) => String(s || '').trim().replace(/^['"]|['"]$/g, '');
  const diag = {
    document: {
      ready_state: document.readyState,
      fonts_status: document.fonts ? document.fonts.status : 'unavailable',
    },
    fonts: [],
    css: [],
    libraries: {
      page: { chart_global: typeof window.Chart !== 'undefined' },
      elements: window.__tesseraeLibReport || [],
      note: 'element libs inline before the ctx + user script in sandbox document order; '
        + 'inferred:true means a token in the element code triggered it (see matched), '
        + 'not that the element asked for it. Set the element autolibs:false for none.',
    },
  };
  // family -> src from reachable @font-face rules, so a failed face names its
  // URL. Cross-origin sheets throw on cssRules access; skip those.
  const srcByFamily = {};
  for (const sheet of document.styleSheets) {
    let rules = null;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    for (const r of rules || []) {
      if (r instanceof CSSFontFaceRule) {
        const fam = unq(r.style.getPropertyValue('font-family'));
        if (fam && !(fam in srcByFamily)) {
          srcByFamily[fam] = String(r.style.getPropertyValue('src') || '').slice(0, 300);
        }
      }
    }
  }
  if (document.fonts) {
    document.fonts.forEach((f) => {
      const status = f.status === 'loaded' ? 'loaded'
        : f.status === 'error' ? 'failed'
        : f.status === 'loading' ? 'pending-at-capture' : 'never-requested';
      const fam = unq(f.family);
      const entry = { family: fam, weight: f.weight, style: f.style, status };
      if (srcByFamily[fam]) entry.src = srcByFamily[fam];
      diag.fonts.push(entry);
    });
  }
  // -- authored-CSS report ------------------------------------------------
  // The browser drops invalid rules/declarations without a trace; re-parse
  // each element's authored CSS with the browser's own parser and diff.
  // Best-effort: native nesting and exotic at-rules are skipped, not flagged.
  // The parser rewrites what it KEEPS, so the diff has to compare on a shape
  // both spellings collapse to or it reports a rewrite as a drop. Legacy
  // ``a:before`` comes back as ``a::before``, so without the ``::`` collapse
  // every author using the single-colon form was told their rule had been
  // "dropped by the CSS parser" while it was applying perfectly well. A
  // diagnostic that contradicts the render is worse than no diagnostic.
  // Same normalisation as decorate.js's in-sandbox cssSelfCheck, which had it
  // and this one didn't, so the two analysers disagreed with each other.
  const normSel = (s) =>
    String(s).replace(/\s+/g, '').replace(/['"]/g, '').replace(/::/g, ':').toLowerCase();
  const walkBlocks = (text) => {
    const blocks = [];
    let depth = 0, buf = '', body = '', sel = '';
    for (const ch of text) {
      if (ch === '{') {
        if (depth === 0) { sel = buf.trim(); buf = ''; body = ''; } else body += ch;
        depth++;
      } else if (ch === '}') {
        depth--;
        if (depth === 0) { blocks.push({ sel, body }); sel = ''; } else body += ch;
      } else if (depth === 0) buf += ch;
      else body += ch;
    }
    return blocks;
  };
  const analyze = (elId, kind, src) => {
    const out = [];
    const clean = String(src || '').replace(/\/\*[\s\S]*?\*\//g, ' ');
    if (!clean.trim()) return out;
    let parsedSelectors = null;
    try {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(clean);
      parsedSelectors = new Set(
        Array.from(sheet.cssRules).filter((r) => r.selectorText)
          .map((r) => normSel(r.selectorText)),
      );
    } catch (e) { /* no constructable sheets: skip the parse diff */ }
    const checkDecls = (sel, body) => {
      for (const part of body.split(';')) {
        const idx = part.indexOf(':');
        if (idx < 1) continue;
        const prop = part.slice(0, idx).trim();
        const value = part.slice(idx + 1).trim();
        if (!prop || !value || prop.startsWith('--')) continue;
        if (!/^-?[a-zA-Z-]+$/.test(prop)) continue; // nested block fragments etc.
        let ok = true;
        try { ok = CSS.supports(prop, value); } catch (e) { /* keep ok */ }
        if (!ok) {
          out.push({
            el: elId, selector: sel.slice(0, 120),
            declaration: (prop + ': ' + value).slice(0, 200),
            reason: 'invalid or unsupported declaration; the browser drops it silently',
          });
        }
      }
    };
    for (const b of walkBlocks(clean)) {
      if (!b.sel) continue;
      if (b.sel.startsWith('@')) {
        const at = b.sel.split(/[\s(]/)[0];
        if (at === '@media' || at === '@supports') {
          for (const inner of walkBlocks(b.body)) {
            if (inner.sel && !inner.sel.startsWith('@')) checkDecls(inner.sel, inner.body);
          }
        }
        continue;
      }
      if (parsedSelectors && !parsedSelectors.has(normSel(b.sel))) {
        out.push({
          el: elId, selector: b.sel.replace(/\s+/g, ' ').slice(0, 120),
          reason: 'rule dropped by the CSS parser (invalid selector or syntax)',
        });
        continue;
      }
      checkDecls(b.sel.replace(/\s+/g, ' '), b.body);
    }
    for (const im of clean.match(/@import[^;]+;/g) || []) {
      out.push({
        el: elId, selector: '@import', declaration: im.slice(0, 200),
        reason: kind === 'code'
          ? 'blocked: the code-element sandbox has no network'
          : '@import does not load in a sandboxed element',
      });
    }
    return out;
  };
  // Rules that went missing from the stylesheet a code element's sandbox
  // actually composed, self-reported from inside the frame (an opaque origin
  // nothing out here can read). This is the only view of the FINAL sheet:
  // analyze() below re-parses authored CSS standing alone, so a rule that
  // parses fine on its own but is consumed once composed shows up only here.
  // ``sheet: 'library'`` means the auto-injected CSS is malformed: an injector
  // bug, not an authoring one. Pushed first so the 50-cap can't bury them.
  for (const rep of window.__tesseraeCssReport || []) {
    for (const d of rep.dropped || []) {
      diag.css.push({
        el: rep.el,
        selector: String(d.selector || '').slice(0, 120),
        reason: d.sheet === 'library'
          ? 'auto-injected library CSS lost this rule when the sandbox parsed it'
          : 'authored rule missing from the composed sandbox stylesheet',
        composed_counts: rep.counts || {},
      });
    }
  }
  for (const n of document.querySelectorAll('.deco[data-el]')) {
    let el = null;
    try { el = JSON.parse(n.getAttribute('data-el') || '{}'); } catch (e) { continue; }
    if (!el || !el.css) continue;
    diag.css.push(...analyze(String(el.id || ''), String(el.kind || ''), el.css));
    if (diag.css.length >= 50) { diag.css.length = 50; break; }
  }
  return diag;
}"""


def _touch_primitives(page: Any) -> list[dict[str, Any]]:
    """The touch-v3 primitives placed on ``page``, each with the bound devices
    that will draw it themselves.

    A primitive with a non-empty ``device_drawn`` renders as a BLANK rect in the
    frame those panels receive: their firmware owns those pixels and draws the
    control locally. An empty list means the server paints the control into the
    composition, which is what a display-only panel, a preview, and this report's
    own render all show. Without this an agent reading a render of a panel that
    draws its own controls sees an empty box and can't tell "reserved" from
    "broken" (#228)."""
    from app.composer import device_draws_touch_primitives

    canvas = getattr(page, "canvas", None)
    if canvas is None:
        return []
    drawn_by_device = sorted(
        d for d in (getattr(page, "device_ids", None) or []) if device_draws_touch_primitives(d)
    )
    return [
        {
            "id": el.id,
            "kind": el.kind,
            "x": el.x,
            "y": el.y,
            "w": el.w,
            "h": el.h,
            "device_drawn": drawn_by_device,
        }
        for el in canvas.els
        if el.kind in PRIMITIVE_KINDS
    ]


def build_render_report(
    page_id: str, page: Any, *, host_url: str, debug: bool = False, fresh: bool = False
) -> dict[str, Any]:
    """Render ``page`` headless and build the full report dict (elements,
    touch wiring, icon_invalid, optional diagnostics). Extracted from the
    route (move-only) so the template Share flow can run the same quality
    gate without going through the token-gated MCP blueprint. Raises on
    renderer failure; the callers translate that to their own error shape."""
    from app.renderer import InspectRequest, RenderRequest, inspect_composed, to_loopback_url
    from app.touch_regions import (
        EXTRACT_INTERACTIVE_JS,
        normalize_regions,
        normalize_slots,
        split_capture_result,
    )

    path = url_for("composer.compose", page_id=page_id)
    if fresh:
        path += "?fresh=1"
    url = to_loopback_url(host_url.rstrip("/") + path)
    # One navigation, two scripts: the element report plus the combined
    # touch-region + overlay-slot map (issue #49 / hybrid render mode),
    # so an agent can verify which boxes its data-on-tap / @name /
    # data-overlay-key annotations actually produced. ``debug`` rides a
    # third script along (the in-page diagnostics) plus the renderer's own
    # event capture, still one navigation.
    combined = (
        "async () => ({ ...("
        + _REPORT_JS
        + ")(), interactive: await ("
        + EXTRACT_INTERACTIVE_JS
        + ")()"
        + (", diag: (" + _DIAG_JS + ")()" if debug else "")
        + " })"
    )
    ir = InspectRequest(
        render=RenderRequest(url=url, viewport_w=page.canvas.w, viewport_h=page.canvas.h),
        script=combined,
        diagnostics=debug,
    )
    report = inspect_composed(ir, pool=current_app.config.get("BROWSER_POOL"))
    renderer_diag: dict[str, Any] = {}
    if debug and isinstance(report, dict) and "diagnostics" in report:
        renderer_diag = report.get("diagnostics") or {}
        report = report.get("result")
    if not isinstance(report, dict):
        report = {"board": {}, "elements": [], "injected_libs": []}
    report.setdefault("injected_libs", [])
    if debug:
        # Merge the renderer-side capture (console / errors / network /
        # settle) with the in-page report (fonts / css / libraries).
        page_diag = report.pop("diag", None)
        report["diagnostics"] = {
            **renderer_diag,
            **(page_diag if isinstance(page_diag, dict) else {}),
        }
    raw_regions, raw_slots = split_capture_result(report.pop("interactive", None))
    regions = normalize_regions(raw_regions)
    report["tap_regions"] = regions
    # Overlay value slots (hybrid render mode): the data-overlay-key
    # elements that actually extracted, with their resolved box + font
    # bucket, so an agent can confirm a slot annotation survived the
    # render (an empty list against authored annotations means the
    # element collapsed, or sits inside a code-element iframe, which
    # the extractor deliberately skips).
    report["overlay_slots"] = normalize_slots(raw_slots)
    report["tap_dangling"] = sorted({n for r in regions for n in r.get("dangling", [])})
    # Icon references that resolve to no glyph (bad slug / weight / bind table
    # / ph-<name> markup class). Server-side against the vendored manifest,
    # always on, mirroring tap_invalid: a blank icon box is named here instead
    # of being pixel-hunted.
    report["icon_invalid"] = _invalid_icons(page.canvas)
    # Touch-v3 primitives + which bound panels draw them on-device. This render
    # (like any preview) paints every control, so without the list an agent
    # can't tell that the SAME element arrives blank on a panel that draws its
    # own.
    report["touch_primitives"] = _touch_primitives(page)
    # Regions whose declared action wouldn't dispatch (issue #49). Empty
    # tap_invalid means every region will actually fire; a non-empty list
    # is the honest signal that a dashboard looks wired but is dead, e.g.
    # an HA call missing its domain/service or a bad structured shape.
    report["tap_invalid"] = [
        {"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"], **bad}
        for r in regions
        for bad in r.get("invalid", [])
    ]
    return report


@bp.get("/pages/<page_id>/render_report")
def render_report(page_id: str) -> Response:
    """A machine-readable companion to preview.png. Renders the canvas headless and
    reports, per element: the resolved box, the text that actually rendered,
    overflow/clip flags (``overflow_x`` when content is wider than its box),
    ``data_source`` (live | sample | error | static), and computed colours; plus
    the board's resolved background / theme. Lets an agent verify a render (catch
    clipping, confirm live data, read the real colours) without parsing a PNG.

    Also reports touch wiring (issue #49): ``tap_regions`` (every region that
    rendered, with its resolved on_tap/on_swipe/on_slide and, when pinned via
    ``data-touch-id`` or a canvas element id, the stable ``touch_id`` that
    protocol-v2 panels key their local feedback on), ``tap_dangling``
    (code-element ``@name`` refs with no matching entry), and ``tap_invalid``
    (regions whose declared action would NOT dispatch, e.g. an HA call missing
    its domain/service, with the box + gesture + reason). ``tap_invalid == []``
    is the real "this dashboard is wired" signal, a region appearing in
    ``tap_regions`` only means it was stored, not that it will fire.

    ``icon_invalid`` (always on, same spirit as ``tap_invalid``): icon
    references that resolve to NO glyph and would render a blank box, each
    with the element id and reason. Covers ``icon`` elements (unknown slug or
    weight), ``icon``-transform bind tables, and a heuristic scan of
    code/html/svg markup for ``ph-<name>`` classes that aren't real Phosphor
    names. Fix with a slug from ``GET /icons?q=``.

    ``touch_primitives`` lists the button / switch / slider / stepper elements
    with the bound devices whose firmware draws them on-device
    (``device_drawn``). This report's render, like every preview, paints all of
    them; a primitive with a non-empty ``device_drawn`` is the one case where
    the frame that panel receives carries a blank rect instead.

    ``overlay_slots`` (hybrid render mode) lists the ``data-overlay-key``
    annotations that actually extracted (box + key + font bucket): the
    live-value slots overlay-capable panels repaint locally between full
    renders. Keys are ``ha:<entity_id>`` or an attribute path
    (``ha:light.desk:attributes.brightness``); a slot may declare
    ``data-overlay-map='{"on":"1","off":"0"}'`` to render non-numeric
    states with the numeric glyph atlas. Code-element markup is covered:
    slots inside a sandbox are mirrored out the same way its touch regions
    are, so an annotation missing here collapsed at render time (zero box,
    hidden, or malformed key), not because it sat in an iframe.

    Note: widget cells render into shadow DOM, so their ``text`` may be empty; data
    primitives and decorations report their text. Overflow is measured on the
    element box regardless.

    ``injected_libs`` lists the vendored bundles each code element's sandbox
    inlined (Chart.js, a Phosphor weight, a bundled font). The choice is
    INFERRED from the element's own html/css/js, so each entry carries
    ``inferred`` plus the ``matched`` token that triggered it: an element
    carrying a stylesheet it never asked for is named here rather than left to
    be deduced from the pixels. Set the element's ``autolibs: false`` to inject
    nothing at all (it then reports ``autolibs: false`` with an empty list).

    Large boards: pass ``?fields=tap_invalid,tap_dangling`` (any of ``board`` /
    ``elements`` / ``tap_regions`` / ``tap_invalid`` / ``tap_dangling`` /
    ``overlay_slots`` / ``injected_libs``) to trim the response, or
    ``?view=touch`` for the touch-and-overlay wiring subset, so verifying
    interactions doesn't pull the whole ``elements`` array. ``id`` + ``rev``
    always ride along.

    ``?debug=1`` adds a ``diagnostics`` section that surfaces the failures a
    PNG hides: ``console`` (error/warn output from every frame, INCLUDING
    code-element sandboxes; a throwing element script lands here tagged
    ``[code-el <id>]``), ``page_errors`` (uncaught exceptions), ``network``
    (failed and 4xx/5xx requests, so a 404 font names its URL), ``settle``
    (what gated the capture: goto / compose-signal / image-wait / font-wait
    outcome + elapsed ms per phase), ``fonts`` (every font face with
    loaded | pending-at-capture | failed | never-requested and its src),
    ``css`` (CSS the browser silently dropped: invalid authored declarations,
    plus any rule missing from the stylesheet a code element's sandbox actually
    composed, which is where an authored rule eaten by injected library CSS
    shows up), and ``libraries`` (the same inlining record as
    ``injected_libs``, with per-element detail). Diagnose from this instead of
    pixel-diffing.

    ``?fresh=1`` re-fetches widget data (bypasses the last-good fallback and
    widget-side caches via ``ctx["fresh"]``), so a mid-debug report can't be
    poisoned by a stale cached result."""
    page = _pr._get_canvas(page_id)
    if page is None or page.canvas is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    debug = request.args.get("debug") in ("1", "true", "True")
    fresh = request.args.get("fresh") in ("1", "true", "True")
    try:
        report = build_render_report(
            page_id, page, host_url=request.host_url, debug=debug, fresh=fresh
        )
    except Exception as err:
        return _err(502, f"render report failed: {type(err).__name__}: {err}")
    # ``?fields=a,b`` trims the response to the named top-level sections so a
    # feedback loop can ask for just what it needs (e.g. ``tap_invalid,
    # tap_dangling``) instead of pulling the whole ``elements`` array, which
    # on a large board blows the output cap and dumps to a file. ``id`` +
    # ``rev`` always ride along. ``?view=touch`` is the touch-wiring preset.
    selectable = {
        "board",
        "elements",
        "tap_regions",
        "tap_invalid",
        "tap_dangling",
        "overlay_slots",
        "icon_invalid",
        "touch_primitives",
        "injected_libs",
    }
    if debug:
        selectable.add("diagnostics")
    view = (request.args.get("view") or "").strip().lower()
    raw_fields = (request.args.get("fields") or "").strip()
    wanted: set[str] | None = None
    if view == "touch":
        wanted = {"tap_regions", "tap_invalid", "tap_dangling", "overlay_slots", "touch_primitives"}
    elif raw_fields:
        wanted = {f.strip() for f in raw_fields.split(",") if f.strip() in selectable}
    if wanted is not None and debug:
        # debug=1 asked for the diagnostics explicitly; a fields/view trim
        # shouldn't silently drop them.
        wanted.add("diagnostics")
    out: dict[str, Any] = {"id": page_id, "rev": _pr._canvas_rev(page)}
    for key in selectable:
        if wanted is None or key in wanted:
            out[key] = report.get(key)
    return jsonify(out)


_ACTION_STRING_DOCS: dict[str, str] = {
    "refresh": "re-render and re-push the current page",
    "rotate_next": "advance to the next step of the device's rotation",
    "rotate_prev": "go back to the previous rotation step",
    "step": "jump to a rotation step by index, e.g. 'step:2' (0-based)",
    "page": "switch the device to a saved dashboard, e.g. 'page:kitchen'",
    "webhook": "fire an HTTP request, e.g. 'webhook:https://host/hook' (config origin only)",
    "room_book": (
        "book the named room in its own calendar, e.g. 'room_book:kestrel'. Requires the "
        "room to have CalDAV booking enabled (config origin only)"
    ),
    "webhook_refresh": (
        "fire an HTTP request, then re-render and repaint a few seconds later, e.g. "
        "'webhook_refresh:https://host/hook'. Use when the request changes what the "
        "dashboard displays (config origin only)"
    ),
}


@bp.get("/actions/describe")
def describe_actions() -> Response:
    """The authoritative touch-action vocabulary for canvas elements
    (issue #49), so an agent doesn't have to reverse-engineer it.

    Covers the element fields (``on_tap`` / ``on_swipe`` / ``on_slide`` /
    ``actions``), the flat string grammar, the Home Assistant structured
    form and the input variations that normalise to it, the slider
    ``{value}`` placeholder, the provenance rule for side-effecting
    actions, and how to verify wiring. Element-level actions aren't part
    of ``get_widget_options`` (that's cell data options), which is why
    this lives on its own."""
    from app.button_actions import parse_action_spec, registered_actions

    string_actions = []
    for name in registered_actions():
        takes_arg = False
        try:
            parse_action_spec(f"{name}:probe")
            # A verb that ignores the arg (refresh/rotate_*) still parses;
            # distinguish by whether the bare form is meaningful.
            takes_arg = name in ("step", "page", "webhook")
        except Exception:
            takes_arg = False
        string_actions.append(
            {
                "spec": f"{name}:<arg>" if takes_arg else name,
                "takes_arg": takes_arg,
                "desc": _ACTION_STRING_DOCS.get(name, ""),
            }
        )

    return jsonify(
        {
            "element_fields": {
                "on_tap": "one action: a grammar string or a structured HA object",
                "on_swipe": (
                    "a map of direction -> action: "
                    '{"left":"rotate_next","right":{"action":"ha",...}}. '
                    "Keys are up/down/left/right; a bare object with no direction "
                    "key will NOT fire and is reported in render_report.tap_invalid"
                ),
                "on_slide": (
                    '{"axis":"x"|"y","action":<spec>}: the whole box becomes a '
                    "slider; the stroke's 0-100 position substitutes {value} (or "
                    "$value) in the action, e.g. brightness_pct"
                ),
                "actions": (
                    "code elements only: a named map {name: <spec>}; the markup "
                    'references them as data-on-tap="@name" so structured actions '
                    "stay in validated config, never inline in markup"
                ),
            },
            "string_actions": string_actions,
            "home_assistant": {
                "canonical": {
                    "action": "ha",
                    "domain": "light",
                    "service": "turn_on",
                    "data": {"entity_id": ["light.hall"], "brightness_pct": 50},
                },
                "accepted_variations": [
                    "omit 'action' when 'service' is present (inferred as ha)",
                    "dotted service: {'service':'light.turn_on'} splits to domain+service",
                    "flat: service data (brightness_pct, …) at the top level is folded into data",
                    "entity_id at the top level or under target.entity_id is folded into data",
                    "a comma-joined entity_id string is split into the list HA expects",
                ],
                "note": (
                    "All the above normalise to the canonical form and dispatch "
                    "identically. Runs server-side via the ha_core connection "
                    "(POST /api/services/<domain>/<service>). The panel catches "
                    "up afterwards on its own: a debounced background reconcile "
                    "re-renders the page the device is showing and delivers the "
                    "change as partial-refresh patches (overlay schema 2) or a "
                    "full re-push a few seconds after the tap burst, so the "
                    "dashboard does not need to encode state in tap echoes."
                ),
            },
            "provenance": (
                "Side-effecting actions (webhook / ha) fire only from config origin "
                "(editor / MCP element fields, or a code element's named actions map), "
                "never from raw widget markup, so a third-party widget can't aim a "
                "webhook by annotating its own HTML."
            ),
            "region_ids": (
                "Protocol v2 panels (list_devices entry carries proto: {v: 2}) "
                "hit-test locally against stable region ids. Canvas elements are "
                "pinned automatically by their element id; interactive nodes inside "
                'code-element markup should carry data-touch-id="<stable-name>" so '
                "their id survives markup edits — unpinned markup regions get a "
                "content-hash id that can churn, silently downgrading their instant "
                "feedback to a plain invert. Not needed on v1 panels."
            ),
            "verify": (
                "GET pages/<id>/render_report?view=touch. tap_regions lists what "
                "rendered; tap_invalid == [] is the real 'this will fire' signal "
                "(a region in tap_regions was only stored, not proven dispatchable); "
                "tap_dangling lists code-element @refs with no matching action."
            ),
        }
    )


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


@bp.post("/pages/<page_id>/devices")
def bind_devices(page_id: str) -> Response:
    """Persistently bind a canvas dashboard to a set of devices. Body:
    ``{device_ids: []}`` replaces the bound set (``[]`` unbinds). Unlike ``/push``
    (a one-off explicit fan-out), this SAVES the set on the page so a later
    schedule / rotation / the editor's Send targets the same panels. Ids that
    don't match a registered device are dropped and reported rather than stored.
    Returns ``{bound: [...], unknown: [...]}``."""
    page = _pr._get_canvas(page_id)
    if page is None or page.canvas is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    body = request.get_json(silent=True) or {}
    picked = body.get("device_ids")
    if not isinstance(picked, list):
        return _err(400, "device_ids must be a list")
    reg = current_app.config.get("DEVICE_REGISTRY")
    known = {d.id for d in reg.all() if d.kind_of is not None} if reg is not None else set()
    bound: list[str] = []
    unknown: list[str] = []
    for x in picked:
        did = str(x).strip()
        if not did:
            continue
        (bound if did in known else unknown).append(did)
    page.device_ids = bound
    _save_mcp(page)
    return jsonify({"bound": bound, "unknown": unknown})


# ---- rotations / schedules / decks -----------------------------------
#
# Read + create + delete for the three scheduling / navigation primitives, so
# an agent that has built pages can wire up how they cycle (rotations), when
# they push (schedules), and how they group for instant navigation (decks).
# Create validates the JSON body against the model and returns 422 with
# field-level details on a bad shape, so the agent can correct and retry.


def _model_errors(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {"loc": ".".join(str(x) for x in e.get("loc", ())), "msg": e.get("msg", "invalid")}
        for e in exc.errors()
    ]


@bp.get("/rotations")
def mcp_list_rotations() -> Response:
    """All rotations (ordered page cycles) with their steps + device bindings."""
    store = current_app.config["ROTATION_STORE"]
    return jsonify(
        {"rotations": [r.model_dump(mode="json", exclude_none=True) for r in store.all()]}
    )


@bp.post("/rotations")
def mcp_create_rotation() -> Response:
    """Create / replace a rotation. Body is a full Rotation object
    (``{id, name, device_ids, steps:[{page_id, dwell_minutes}], anchor?, ...}``)."""
    from app.state.rotation_model import Rotation

    try:
        rotation = Rotation.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _err(422, "invalid rotation", details=_model_errors(exc))
    current_app.config["ROTATION_STORE"].upsert(rotation)
    return jsonify({"ok": True, "id": rotation.id})


@bp.delete("/rotations/<rotation_id>")
def mcp_delete_rotation(rotation_id: str) -> Response:
    if not current_app.config["ROTATION_STORE"].delete(rotation_id):
        return _err(404, f"no rotation {rotation_id!r}")
    return jsonify({"ok": True, "id": rotation_id})


@bp.get("/schedules")
def mcp_list_schedules() -> Response:
    """All schedules (time-driven page pushes)."""
    store = current_app.config["SCHEDULE_STORE"]
    return jsonify(
        {"schedules": [s.model_dump(mode="json", exclude_none=True) for s in store.all()]}
    )


@bp.post("/schedules")
def mcp_create_schedule() -> Response:
    """Create / replace a schedule. Body is a full Schedule object
    (``{id, page_id, type, ...}``, e.g. an interval or daily push)."""
    from app.state.schedule_model import Schedule

    try:
        schedule = Schedule.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _err(422, "invalid schedule", details=_model_errors(exc))
    current_app.config["SCHEDULE_STORE"].upsert(schedule)
    return jsonify({"ok": True, "id": schedule.id})


@bp.delete("/schedules/<schedule_id>")
def mcp_delete_schedule(schedule_id: str) -> Response:
    if not current_app.config["SCHEDULE_STORE"].delete(schedule_id):
        return _err(404, f"no schedule {schedule_id!r}")
    return jsonify({"ok": True, "id": schedule_id})


@bp.get("/decks")
def mcp_list_decks() -> Response:
    """All decks. This is the full truth post-#167: pure timer decks (the
    decommissioned rotations and schedules) appear here AND through the
    deprecated list_rotations / list_schedules views."""
    store = current_app.config["DECK_STORE"]
    return jsonify({"decks": [d.model_dump(mode="json", exclude_none=True) for d in store.all()]})


@bp.post("/decks")
def mcp_create_deck() -> Response:
    """Create / replace a deck. Body is a full Deck object
    (``{id, name, device_ids, pages:[{page_id, links:[{target_page_id, button|zone}]}],
    entry_page_id?, refresh_interval_minutes?}``).

    When the submitted pages carry NO links at all (the common agent
    flow: name + page set, no hand-built graph), the graph is derived
    automatically from the pages' authored ``page:<id>`` tap / swipe
    links (same derivation as the Decks page "Sync from links"). Pages
    still link-less after that fall back to the manifest's default
    prev/next navigation on-device, so the deck navigates either way.
    A submitted graph is used verbatim."""
    from app.state.deck_model import Deck

    try:
        deck = Deck.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return _err(422, "invalid deck", details=_model_errors(exc))
    if not any(p.links for p in deck.pages):
        from app.deck_suggest import graph_for_pages

        derived = graph_for_pages(
            current_app.config["PAGE_STORE"].list(), [p.page_id for p in deck.pages]
        )
        if any(p.links for p in derived):
            refresh_by_id = {p.page_id: p.refresh_interval_minutes for p in deck.pages}
            deck = deck.model_copy(
                update={
                    "pages": [
                        p.model_copy(
                            update={"refresh_interval_minutes": refresh_by_id.get(p.page_id)}
                        )
                        for p in derived
                    ]
                }
            )
    current_app.config["DECK_STORE"].upsert(deck)
    return jsonify({"ok": True, "id": deck.id, "links_derived": any(p.links for p in deck.pages)})


@bp.delete("/decks/<deck_id>")
def mcp_delete_deck(deck_id: str) -> Response:
    if not current_app.config["DECK_STORE"].delete(deck_id):
        return _err(404, f"no deck {deck_id!r}")
    return jsonify({"ok": True, "id": deck_id})


@bp.get("/decks/suggest")
def mcp_suggest_decks() -> Response:
    """Decks suggested from the ``page:<id>`` tap / swipe links across pages
    (the navigation you authored in the canvas), each a ready-to-create Deck."""
    from app.deck_suggest import suggest_decks

    pages = current_app.config["PAGE_STORE"].list()
    decks = current_app.config["DECK_STORE"].all()
    suggestions = [
        d.model_dump(mode="json", exclude_none=True) for d in suggest_decks(pages, decks)
    ]
    return jsonify({"suggestions": suggestions})


@bp.get("/instructions")
def instructions() -> Response:
    """The agent-facing MCP docs (handshake instructions + canvas doc-shape).

    The tesserae-mcp bridge fetches this at startup so a capability / copy change
    goes live on the next agent connection with no bridge republish; the bridge
    keeps an embedded copy as a fallback. ``schema`` lets an older bridge tell
    whether it understands the payload shape.

    ``tool_docs`` carries each tool's own description, so a corrected contract
    reaches an installed bridge too, and ``bridge`` names the release this server
    ships with so a bridge can tell the agent it is behind. Both are additive
    keys: a bridge that predates them reads ``instructions`` / ``doc_shape`` and
    ignores the rest, so no ``schema`` bump is needed (bumping would be actively
    wrong -- the bridge treats a schema it doesn't know as unreadable and falls
    back to its embedded copy wholesale)."""
    from app import mcp_docs

    return jsonify(
        {
            "schema": mcp_docs.DOCS_SCHEMA,
            "instructions": mcp_docs.INSTRUCTIONS,
            "doc_shape": mcp_docs.DOC_SHAPE,
            "tool_docs": mcp_docs.TOOL_DOCS,
            "bridge": {
                "latest": mcp_bridge.EXPECTED_VERSION,
                "upgrade": mcp_bridge.UPGRADE_COMMAND,
            },
        }
    )


def register(app: Flask) -> None:
    app.register_blueprint(bp)
