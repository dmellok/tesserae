"""Admin page at ``/stats``.

The aggregate view of what this install actually does: frames painted,
what asked for them, which displays do the work, how often panels check
in. Everything on the page is read from ``data/core/stats.db``, which is
written locally by :mod:`app.stats_recorder` and read by nothing else.

The page states that plainly and backs it with controls rather than a
promise: export the whole file, delete every counter, or pause
collection. A stats feature in a project that removed its phone-home has
to be visibly the opposite of one.

Renders on a fresh install with no data: every panel has an empty state
keyed off "collecting since", so the answer to "why is this blank" is on
the page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    Flask,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from app import sponsor_prompt
from app.state.stats_store import (
    ACTIVITY_BY_TYPE,
    DEVICE_WAKES,
    FRAMES_BY_DEVICE,
    METRICS,
    PUSH_MS_SUM,
    PUSHES_BY_SOURCE,
    PUSHES_BY_STATUS,
    StatsStore,
    days_back,
)
from app.stats_recorder import BUCKET_ORDER, bucket_for

bp = Blueprint("stats", __name__, url_prefix="/stats")

WINDOW_DAYS_DEFAULT = 30
WINDOW_OPTIONS: tuple[int, ...] = (7, 30, 90, 365)
TOP_DEVICES = 8

# Outcomes worth naming on the page. Anything else the push manager
# records lands in "other" rather than being dropped, so the outcome
# table always adds up to the pushes counted.
OUTCOME_LABELS: dict[str, str] = {
    "sent": "Sent",
    "failed": "Failed",
    "quiet": "Held for quiet hours",
    "busy": "Skipped, already rendering",
    "superseded": "Superseded",
    "not_found": "Nothing to send",
    "unbound": "No display bound",
}


def _store() -> StatsStore | None:
    store = current_app.config.get("STATS_STORE")
    return store if isinstance(store, StatsStore) else None


def _window() -> int:
    raw = request.args.get("window")
    try:
        window = int(raw) if raw else WINDOW_DAYS_DEFAULT
    except (TypeError, ValueError):
        return WINDOW_DAYS_DEFAULT
    return window if window in WINDOW_OPTIONS else WINDOW_DAYS_DEFAULT


def _device_names() -> dict[str, str]:
    registry = current_app.config.get("DEVICE_REGISTRY")
    if registry is None:
        return {}
    out: dict[str, str] = {}
    for device in getattr(registry, "devices", {}).values():
        out[device.id] = getattr(device, "display_name", None) or device.name or device.id
    return out


def _stacked_by_bucket(store: StatsStore, window: int) -> dict[str, Any]:
    """Daily pushes grouped into the four source buckets.

    The store keeps the raw source, so the grouping is applied here and
    regrouping later doesn't rewrite history."""
    series = store.series(PUSHES_BY_SOURCE, days=window)
    days = days_back(window)
    buckets: dict[str, list[int]] = {name: [0] * len(days) for name in BUCKET_ORDER}
    for index, day in enumerate(days):
        for source, value in series.get(day, {}).items():
            buckets[bucket_for(source)][index] += value
    return {
        "days": days,
        "series": [
            {"label": name, "values": values}
            for name, values in buckets.items()
            # Drop a band nobody uses rather than drawing an empty legend
            # entry; "other" survives only when something landed in it.
            if any(values)
        ],
        "total": sum(sum(values) for values in buckets.values()),
    }


def _top_devices(store: StatsStore, window: int) -> list[dict[str, Any]]:
    counts = store.by_dim(FRAMES_BY_DEVICE, days=window)
    wakes = store.by_dim(DEVICE_WAKES, days=window)
    names = _device_names()
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    rows = [
        {
            "id": device_id,
            "name": names.get(device_id, device_id),
            "frames": value,
            "wakes": wakes.get(device_id, 0),
            "known": device_id in names,
        }
        for device_id, value in ranked[:TOP_DEVICES]
    ]
    rest = ranked[TOP_DEVICES:]
    if rest:
        rows.append(
            {
                "id": "",
                "name": f"{len(rest)} more",
                "frames": sum(value for _, value in rest),
                "wakes": 0,
                "known": True,
            }
        )
    return rows


def _outcomes(store: StatsStore, window: int) -> list[dict[str, Any]]:
    counts = store.by_dim(PUSHES_BY_STATUS, days=window)
    total = sum(counts.values())
    rows = [
        {
            "key": key,
            "label": OUTCOME_LABELS.get(key, key.replace("_", " ").capitalize()),
            "value": value,
            "share": round(value * 100 / total) if total else 0,
        }
        for key, value in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return rows


def _fleet() -> list[dict[str, Any]]:
    """Current shape of the install, counted live. Not stored: it's a
    snapshot of what exists now, and keeping a daily history of it would
    be recording the operator's setup over time for no benefit."""
    registry = current_app.config.get("DEVICE_REGISTRY")
    devices = list(getattr(registry, "devices", {}).values()) if registry else []
    pages = current_app.config.get("PAGE_STORE")
    decks = current_app.config.get("DECK_STORE")
    albums = current_app.config.get("ALBUM_STORE")
    plugins = current_app.config.get("PLUGIN_REGISTRY")
    renderers = current_app.config.get("RENDERER_REGISTRY")

    def _count(store: Any, method: str) -> int:
        try:
            return len(getattr(store, method)())
        except Exception:
            return 0

    return [
        {"label": "Displays", "value": len(devices)},
        {"label": "Dashboards", "value": _count(pages, "list")},
        {"label": "Lineups", "value": _count(decks, "all")},
        {"label": "Albums", "value": _count(albums, "all")},
        {"label": "Widgets", "value": len(getattr(plugins, "plugins", {}) or {})},
        {"label": "Renderers", "value": len(getattr(renderers, "renderers", {}) or {})},
    ]


def _sponsor_state(store: StatsStore) -> dict[str, Any] | None:
    """The sponsor card, when a milestone has actually been reached.

    Lives on this page on purpose: the operator opened it to look at what
    the install has done for them, which is the only context where the
    ask isn't an interruption."""
    from app import install_id as install_id_module

    settings = current_app.config.get("SETTINGS_STORE")
    if settings is None:
        return None
    data_root = current_app.config.get("DATA_ROOT")
    meta = install_id_module.read_metadata(Path(data_root)) if data_root else None
    return sponsor_prompt.state(
        settings=settings,
        stats=store,
        install_created_at=(meta or {}).get("created_at", ""),
    )


@bp.get("/")
def index() -> str:
    store = _store()
    window = _window()
    if store is None:
        return render_template("stats.html", available=False, metrics=METRICS)

    sent_window = store.total(PUSHES_BY_STATUS, dim="sent", days=window)
    ms_window = store.total(PUSH_MS_SUM, days=window)
    tiles = [
        {
            "label": "Frames painted",
            "value": store.total(FRAMES_BY_DEVICE),
            "note": "all time",
        },
        {
            "label": "Pushes",
            "value": store.total(PUSHES_BY_STATUS, days=window),
            "note": f"last {window} days",
        },
        {
            "label": "Sent today",
            "value": store.total(PUSHES_BY_STATUS, dim="sent", days=1),
            "note": "so far",
        },
        {
            "label": "Check-ins",
            "value": store.total(DEVICE_WAKES, days=window),
            "note": f"last {window} days",
        },
        {
            "label": "Average render",
            "value": round(ms_window / sent_window) if sent_window else 0,
            "unit": "ms",
            "note": f"last {window} days",
        },
    ]
    return render_template(
        "stats.html",
        available=True,
        window=window,
        window_options=WINDOW_OPTIONS,
        since=store.since(),
        paused=store.paused(),
        tiles=tiles,
        pushes=_stacked_by_bucket(store, window),
        devices=_top_devices(store, window),
        outcomes=_outcomes(store, window),
        activity=sorted(
            store.by_dim(ACTIVITY_BY_TYPE, days=window).items(),
            key=lambda kv: (-kv[1], kv[0]),
        ),
        fleet=_fleet(),
        metrics=METRICS,
        db_path="data/core/stats.db",
        sponsor=_sponsor_state(store),
    )


@bp.get("/export.json")
def export() -> Response:
    """The whole store as a download. Deliberately the same data the
    page draws from, so "what does it know about me" is answerable by
    reading a file rather than trusting a sentence."""
    store = _store()
    if store is None:
        return jsonify({"error": "stats store not configured"}), 503  # type: ignore[return-value]
    response = jsonify(store.export())
    response.headers["Content-Disposition"] = 'attachment; filename="tesserae-stats.json"'
    return response


@bp.post("/pause")
def pause() -> Any:
    store = _store()
    if store is not None:
        paused = not store.paused()
        store.set_paused(paused)
        flash(
            "Stats collection paused. Existing counters are kept."
            if paused
            else "Stats collection resumed.",
            "ok",
        )
    return redirect(url_for("stats.index"))


@bp.post("/sponsor/dismiss")
def dismiss_sponsor() -> Any:
    """Permanent and silent. No "remind me later": the footer keeps a
    plain sponsor link, so dismissing hides the interruption rather than
    the door."""
    settings = current_app.config.get("SETTINGS_STORE")
    if settings is not None:
        sponsor_prompt.dismiss(settings)
    return redirect(url_for("stats.index"))


@bp.post("/delete")
def delete() -> Any:
    store = _store()
    if store is not None:
        removed = store.delete_all()
        flash(f"Deleted {removed} counter row{'' if removed == 1 else 's'}.", "ok")
    return redirect(url_for("stats.index"))


def register(app: Flask) -> None:
    app.register_blueprint(bp)
