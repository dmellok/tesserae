"""Approaching-vehicle board for one GTFS stop, plus optional GTFS-RT.

Two very different upstreams behind one ``fetch()``:

* **Static GTFS** is a zip of CSVs, and for a big agency ``stop_times.txt``
  runs to hundreds of MB uncompressed. Downloading and scanning that is a
  minute-scale job, far past the composer's per-widget hydration budget, so
  the first render kicks a background build and paints a "loading" state;
  every render after that reads a distilled per-stop timetable (a few KB)
  out of ``data_dir``. The distillate is cached for 12 hours and refreshed
  stale-while-revalidate, matching how often agencies actually republish.

* **GTFS-RT** is a small protobuf fetched inline on every render with a
  20-second cache. Decoded by hand (see ``_pb_fields``); pulling in
  ``gtfs-realtime-bindings`` + ``protobuf`` for four field numbers isn't
  worth the dependency.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import re
import threading
import time
import urllib.request
import zipfile
from collections.abc import Iterator
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, current_app, render_template, request

TIMETABLE_TTL_S = 12 * 3600
RT_TTL_S = 20
ALERT_TTL_S = 60
# Cooldown after a failed build. Without it every render retries, and a
# retry means re-downloading the whole feed.
BUILD_RETRY_S = 600
# Static feeds are tens of MB; the read happens on a background thread, so a
# generous timeout here costs the dashboard nothing.
GTFS_TIMEOUT_S = 120
RT_TIMEOUT_S = 8
# How long a render waits on a fresh build before giving up and painting the
# loading state. Small feeds (and the test suite) finish inside this and
# return data on the very first render; MTA-sized ones don't.
BUILD_WAIT_S = 4.0
# How long the cell editor may block building a cold stop index (all preset
# feeds combined) before falling back to "it's still building".
CHOICES_BUILD_BUDGET_S = 20.0
USER_AGENT = "tesserae/0.1 (+gtfs)"
# "[airplane icon]", "[accessibility icon]" — MTA-style placeholders for
# glyphs a plain-text board can't render.
_ICON_MARKER = re.compile(r"\[[^\]]*\bicon\b[^\]]*\]", re.IGNORECASE)
MAX_ARRIVALS = 12

# route_type -> semantic mode name the client maps to a Phosphor glyph.
_MODES = {
    0: "tram",
    1: "subway",
    2: "rail",
    3: "bus",
    4: "ferry",
    5: "tram",
    6: "gondola",
    7: "funicular",
    11: "bus",
    12: "monorail",
}

_BUILDS: dict[str, threading.Thread] = {}
_BUILD_LOCK = threading.Lock()

# Known feeds, so the common case isn't "go find three URLs". The MTA splits
# its realtime feeds by line group (one protobuf per group), all sharing one
# static zip and one alerts feed — hence the repetition below. No API key
# needed since 2023. Source: https://api.mta.info/#/subwayRealTimeFeeds
_MTA_STATIC = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip"
_MTA_RT = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2F"
_MTA_ALERTS = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts"

PRESETS: dict[str, dict[str, str]] = {
    # BART publishes one realtime feed for the whole system, so it needs no
    # line-group split. The static URL 307s to a dated file; urllib follows it.
    "bart": {
        "label": "BART (San Francisco Bay Area)",
        "gtfs_url": "https://www.bart.gov/dev/schedules/google_transit.zip",
        # http:// on BART's published URLs redirects to https anyway; naming
        # https here skips the hop.
        "rt_url": "https://api.bart.gov/gtfsrt/tripupdate.aspx",
        "alerts_url": "https://api.bart.gov/gtfsrt/alerts.aspx",
    },
}

PRESETS.update(
    {
        key: {
            "label": f"NYC Subway ({label})",
            "gtfs_url": _MTA_STATIC,
            "rt_url": _MTA_RT + path,
            "alerts_url": _MTA_ALERTS,
        }
        for key, label, path in (
            ("mta_ace", "A/C/E, Rockaway Shuttle", "gtfs-ace"),
            ("mta_bdfm", "B/D/F/M, Franklin Shuttle", "gtfs-bdfm"),
            ("mta_g", "G", "gtfs-g"),
            ("mta_jz", "J/Z", "gtfs-jz"),
            ("mta_nqrw", "N/Q/R/W", "gtfs-nqrw"),
            ("mta_l", "L", "gtfs-l"),
            ("mta_123456", "1/2/3/4/5/6/7, 42 St Shuttle", "gtfs"),
            ("mta_sir", "Staten Island Railway", "gtfs-si"),
        )
    }
)


def _preset_urls(preset: str, gtfs_url: str, rt_url: str, alerts_url: str) -> tuple[str, str, str]:
    """A chosen preset supplies all three URLs; "custom" leaves them alone."""
    spec = PRESETS.get(preset)
    if spec is None:
        return gtfs_url, rt_url, alerts_url
    return spec["gtfs_url"], spec["rt_url"], spec["alerts_url"]


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    gtfs_url = str(options.get("gtfs_url") or "").strip()
    stop_opt = str(options.get("stop_id") or "").strip()
    rt_url = str(options.get("rt_url") or "").strip()
    alerts_url = str(options.get("alerts_url") or "").strip()
    direction = str(options.get("direction") or "any").strip()
    # NB: not "label" — the host overwrites that option with the app-level
    # Settings location name, which would title a transit board with a city.
    title = str(options.get("title") or "").strip()
    gtfs_url, rt_url, alerts_url = _preset_urls(
        str(options.get("preset") or "custom").strip(), gtfs_url, rt_url, alerts_url
    )
    # A second preset contributes only its realtime feed: the MTA splits
    # realtime by line group, so a board covering both an A/C/E platform and
    # a 1 platform needs two feeds behind one static timetable.
    extra = PRESETS.get(str(options.get("preset_2") or "").strip())
    # Commas let a custom setup name several feeds in the one field.
    rt_urls = [u.strip() for u in rt_url.split(",") if u.strip()]
    if extra and extra["rt_url"] not in rt_urls:
        rt_urls.append(extra["rt_url"])
    if not gtfs_url:
        return {"error": "Pick a feed preset, or set a GTFS feed URL, in the cell editor."}
    if gtfs_url.startswith("demo:"):
        return _demo(gtfs_url.split(":", 1)[1], title)
    # The Stop dropdown only covers the preset feeds; the text field is the
    # fallback for a custom one. A dropdown pick wins when it belongs to the
    # feed this cell is actually reading.
    # Up to two stops on one board: an office between a subway entrance and a
    # bus stop wants both, merged by time. The distiller already accepts a
    # comma-separated set, so a second pick just joins the list.
    picks = [
        _stop_from_choice(str(options.get(name) or "").strip(), gtfs_url)
        for name in ("stop", "stop_2")
    ]
    chosen = [p for p in picks if p]
    if chosen:
        stop_opt = ",".join(chosen)
    if not stop_opt:
        return {"error": "Pick a stop in the cell editor."}

    basis = "departure" if str(options.get("time_basis") or "") == "departure" else "arrival"
    horizon = _positive_int(options.get("horizon"), 90)
    walk = max(0, _positive_int(options.get("walk_minutes"), 0))
    # Comma-separated route short names ("A,C"), case-insensitive. Blank
    # means every route calling at the stop, which is the lobby-board default.
    wanted_routes = {
        r.strip().casefold() for r in str(options.get("routes") or "").split(",") if r.strip()
    }

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)
    # Warm the Stop dropdown's index off the feed this render is downloading
    # anyway, so the editor isn't the thing that discovers it's cold. Cheap:
    # the zip is shared with the timetable build, and the index is a 0.6s
    # parse cached for 12 hours.
    _warm_stop_index(gtfs_url, data_dir)
    key = hashlib.sha1(f"{gtfs_url}|{stop_opt}".encode()).hexdigest()[:16]
    tt_path = data_dir / f"tt2_{key}.json"
    err_path = data_dir / f"err_{key}.json"

    table, fresh = _read_cache(tt_path, TIMETABLE_TTL_S)
    # A failed build must not re-download a 100 MB feed on every refresh tick,
    # so failures park in their own file for BUILD_RETRY_S before another
    # attempt. Separate from tt_path deliberately: a build that fails while a
    # stale-but-usable timetable is on disk should leave that table alone.
    err, err_fresh = _read_error(err_path)
    if (table is None or not fresh) and not err_fresh:
        thread = _start_build(key, lambda: _build(gtfs_url, stop_opt, tt_path, err_path))
        if table is None:
            # Nothing to paint yet. Give a small feed a chance to land inside
            # this render before falling back to the loading state.
            thread.join(BUILD_WAIT_S)
            table, _ = _read_cache(tt_path, TIMETABLE_TTL_S)
            err, _ = _read_error(err_path)
    if table is None:
        return {"error": err or "Loading the timetable from the GTFS feed…"}

    try:
        tz = ZoneInfo(table.get("tz") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)

    arrivals = _scheduled(table, now, horizon, walk, basis)
    if direction in ("0", "1"):
        arrivals = [a for a in arrivals if a["direction"] == direction]
    if wanted_routes:
        arrivals = [
            a
            for a in arrivals
            if a["route"].casefold() in wanted_routes or a["route_id"].casefold() in wanted_routes
        ]

    live = False
    note = ""
    stop_ids = set(table.get("stop_ids") or [])
    feed_age_s = None
    if rt_urls:
        bundle, note = _realtime_all(rt_urls, data_dir, key, stop_ids)
        rt = bundle.get("times") or {}
        canceled = set(bundle.get("canceled") or [])
        if rt or canceled:
            live = _apply_realtime(
                arrivals,
                rt,
                now,
                canceled,
                bundle.get("tracks") or {},
                bundle.get("vehicles") or {},
            )
        stamp = bundle.get("stamp")
        if stamp:
            feed_age_s = max(0, int(now.timestamp() - int(stamp)))
    if alerts_url:
        routes = {a["route_id"] for a in arrivals}
        found = _alerts(alerts_url, data_dir, key, stop_ids, routes)
        # A real service alert outranks the "live feed is down" note.
        if found:
            note = found[0]
    # A live prediction can pull a trip earlier than its schedule, past the
    # window the scheduled pass filtered on, so re-apply the bounds after the
    # merge. Otherwise a vehicle that already left shows as "-1 min".
    arrivals = [a for a in arrivals if walk <= a["minutes"] <= horizon]
    arrivals.sort(key=lambda a: a["minutes"])
    for a in arrivals:
        a.pop("sched_epoch", None)

    return {
        "stop": table.get("stop_name") or stop_opt,
        "label": title or table.get("stop_name") or stop_opt,
        "now": now.strftime("%H:%M"),
        "live": live,
        "feed_age_s": feed_age_s,
        "note": note,
        "arrivals": arrivals[:MAX_ARRIVALS],
    }


# ----------------------------------------------------------------------
# Stop dropdown for the preset feeds
# ----------------------------------------------------------------------


def choices(name: str) -> list[dict[str, str]]:
    """Cell-option choices. Only ``stops`` is served.

    The host calls this once per plugin when the editor loads, with no cell
    context — so it can't know which feed a *particular* cell uses. That's why
    the dropdown covers the preset feeds only; a custom feed still uses the
    Stop ID text field. Building an index means reading the whole feed, so
    this never downloads inline: a cold index kicks a background build and
    says so, and the next editor open has the list.
    """
    if name != "stops":
        return []
    data_dir = _data_dir()
    out: list[dict[str, str]] = [{"value": "", "label": "— use the Stop ID field below —"}]
    building = False
    # Budget for building a cold index inline. Downloading + indexing BART is
    # ~0.5s and the MTA ~4s, so the editor waits once and the dropdown is
    # populated the first time it's opened. Anything slower finishes in the
    # background and lands on the next open. The earlier background-only
    # version left a first-time user staring at an apparently empty dropdown
    # with no way to tell what was wrong.
    deadline = time.monotonic() + CHOICES_BUILD_BUDGET_S
    # One index per distinct static feed, labelled with the agency so the
    # merged list groups by operator instead of interleaving Bay Area and
    # New York stations alphabetically.
    #
    # Only feeds this install actually uses get built. Indexing every preset
    # meant a BART-only user downloading the MTA's 5.6 MB zip to populate a
    # dropdown they'd never scroll — and that cost grows with each preset
    # added. A fresh install with no gtfs cells yet has nothing to go on, so
    # it falls back to all of them, once.
    in_use = _presets_in_use()
    feeds: dict[str, str] = {}
    for key, spec in PRESETS.items():
        if in_use and key not in in_use:
            continue
        feeds.setdefault(spec["gtfs_url"], spec["label"].split(" (")[0])
    for url, agency in feeds.items():
        digest = hashlib.sha1(url.encode()).hexdigest()[:16]
        path = data_dir / f"stops3_{digest}.json"
        cached, fresh = _read_cache(path, TIMETABLE_TTL_S)
        if cached is not None:
            out.extend(cached.get("stops") or [])
        if cached is None or not fresh:
            thread = _start_build(
                f"stops-{digest}",
                lambda u=url, p=path, a=agency: _build_stop_index(u, p, a),
            )
            if cached is None:
                thread.join(max(0.0, deadline - time.monotonic()))
                landed, _ = _read_cache(path, TIMETABLE_TTL_S)
                if landed is not None:
                    out.extend(landed.get("stops") or [])
                else:
                    building = True
    if building:
        out.insert(1, {"value": "", "label": "(Stop list is loading, reopen this editor shortly)"})
    return out


def _warm_stop_index(gtfs_url: str, data_dir: Path) -> None:
    """Build this feed's stop index in the background, if it's a preset feed
    and the index is missing or stale. No-op for custom feeds, which the
    dropdown doesn't cover anyway."""
    agency = next(
        (spec["label"].split(" (")[0] for spec in PRESETS.values() if spec["gtfs_url"] == gtfs_url),
        "",
    )
    if not agency:
        return
    digest = hashlib.sha1(gtfs_url.encode()).hexdigest()[:16]
    path = data_dir / f"stops3_{digest}.json"
    _cached, fresh = _read_cache(path, TIMETABLE_TTL_S)
    if fresh:
        return
    _start_build(f"stops-{digest}", lambda: _build_stop_index(gtfs_url, path, agency))


def _presets_in_use() -> set[str]:
    """Preset ids referenced by saved gtfs cells, empty if none / unknown."""
    used: set[str] = set()
    with contextlib.suppress(Exception):
        store = current_app.config.get("PAGE_STORE")
        for page in store.all() if store is not None else []:
            for cell in page.cells:
                if cell.plugin == "gtfs":
                    preset = str((cell.options or {}).get("preset") or "")
                    if preset in PRESETS:
                        used.add(preset)
    return used


def _build_stop_index(url: str, path: Path, agency: str = "") -> None:
    try:
        index = _stop_index(_feed_bytes(url, path.parent), url, agency)
    except Exception:
        return
    with contextlib.suppress(OSError):
        _write_json(path, {"stops": index})


def _stop_index(raw: bytes, url: str, agency: str = "") -> list[dict[str, str]]:
    """``stops.txt`` reduced to a labelled dropdown list.

    Labels carry the calling routes because a big agency repeats stop names
    relentlessly — the MTA has 18 stops called "Canal St", and a dropdown of
    18 identical labels is no better than the text box it replaces. Getting
    routes-per-stop means joining ``stop_times`` against ``trips``, i.e.
    reading the feed's largest member, which is why this runs in the
    background and gets cached.

    Platforms fold into their parent station: one board per station is what
    people want, and it halves the list. Direction isn't part of the entry —
    that's the Direction option's job.
    """
    digest = hashlib.sha1(url.encode()).hexdigest()[:16]
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = {n.rsplit("/", 1)[-1]: n for n in zf.namelist()}
        stops: dict[str, tuple[str, str]] = {}
        for row in _rows(zf, names, "stops.txt"):
            stops[row.get("stop_id", "")] = (
                row.get("stop_name", ""),
                row.get("parent_station", ""),
            )
        route_name: dict[str, str] = {}
        for row in _rows(zf, names, "routes.txt"):
            rid = row.get("route_id", "")
            route_name[rid] = (
                row.get("route_short_name", "") or row.get("route_long_name", "") or rid
            )
        trip_route: dict[str, str] = {}
        for row in _rows(zf, names, "trips.txt"):
            trip_route[row.get("trip_id", "")] = row.get("route_id", "")
        serving: dict[str, set[str]] = {}
        for row in _rows(zf, names, "stop_times.txt"):
            sid = row.get("stop_id", "")
            entry = stops.get(sid)
            if entry is None:
                continue
            # Fold a platform into its parent station.
            key = entry[1] or sid
            route = route_name.get(trip_route.get(row.get("trip_id", ""), ""), "")
            if route:
                serving.setdefault(key, set()).add(route)

    out: list[dict[str, str]] = []
    for sid, routes in serving.items():
        entry = stops.get(sid)
        if entry is None:
            continue
        listed = ", ".join(sorted(routes)[:6])
        label = f"{entry[0]} ({listed}) · {sid}" if listed else f"{entry[0]} · {sid}"
        out.append(
            {
                "value": f"{digest}:{sid}",
                "label": f"{agency} · {label}" if agency else label,
            }
        )
    out.sort(key=lambda item: item["label"])
    return out


def _stop_from_choice(value: str, gtfs_url: str) -> str:
    """``<feed-digest>:<stop_id>`` -> ``stop_id``, if the digest still matches.

    The digest guards against a stale pick: change the cell's feed and a stop
    ID from the old one would otherwise be silently looked up in the new feed.
    A trailing ``:<direction>`` from an earlier build of this widget is
    parsed off and ignored; direction lives in its own option.
    """
    digest, _, rest = value.partition(":")
    stop_id = rest.partition(":")[0]
    if not stop_id or digest != hashlib.sha1(gtfs_url.encode()).hexdigest()[:16]:
        return ""
    return stop_id


# ----------------------------------------------------------------------
# Admin page, stop finder
# ----------------------------------------------------------------------


def blueprint() -> Blueprint:
    """``/plugins/gtfs/``, search a feed for the stop ID a cell needs.

    Typing a raw ``stop_id`` is the worst part of setting this widget up: the
    IDs live inside a zip nobody wants to download and grep. This page does
    that lookup, and inspecting a stop lists which routes call there and what
    each ``direction_id`` actually means in headsign terms — the two things
    the cell editor can't tell you.
    """
    bp = Blueprint("gtfs_admin", __name__, template_folder="templates")

    @bp.get("/")
    def index() -> str:
        feed_url = (request.args.get("feed_url") or "").strip()
        query = (request.args.get("q") or "").strip()
        inspect = (request.args.get("inspect") or "").strip()
        stops: list[dict[str, str]] = []
        detail: dict[str, Any] | None = None
        error = ""
        truncated = 0

        if feed_url and not _may_fetch(feed_url):
            # Defence in depth: this page turns a query arg into an outbound
            # request, so an unauthenticated caller only gets the preset
            # feeds — fixed, known hosts. Arbitrary URLs need a session.
            error = "Sign in to search a feed that isn't one of the presets."
            feed_url = ""
        if feed_url:
            try:
                raw = _feed_bytes(feed_url, _data_dir())
                if inspect:
                    detail = _inspect_stop(raw, inspect)
                else:
                    stops, truncated = _search_stops(raw, query)
            except zipfile.BadZipFile:
                error = "That URL didn't return a GTFS zip."
            except _StopNotFound as err:
                error = str(err)
            except Exception as err:
                error = f"Couldn't read the feed: {type(err).__name__}"

        return render_template(
            "gtfs/index.html",
            feed_url=feed_url,
            query=query,
            stops=stops,
            truncated=truncated,
            detail=detail,
            inspect=inspect,
            error=error,
            fixtures=sorted(p.stem for p in FIXTURES.glob("*.json")),
            # One entry per distinct static feed: the MTA's eight presets
            # differ only in which realtime feed they pair with, and the
            # finder only ever reads the static one.
            presets=list(
                {
                    spec["gtfs_url"]: spec["label"].split(" (")[0] for spec in PRESETS.values()
                }.items()
            ),
        )

    return bp


def _may_fetch(feed_url: str) -> bool:
    """Preset feeds are always allowed; anything else needs a trusted caller.

    "Trusted" means an authed session, or an install that has deliberately
    turned the password off (Settings -> System -> Auth), where the whole
    admin UI is already open to the local network and gating this one field
    would only break the finder for its actual users.
    """
    if any(spec["gtfs_url"] == feed_url for spec in PRESETS.values()):
        return True
    from app.auth import is_authed, password_required

    if is_authed():
        return True
    # Mirror the host's own policy rather than inventing a second one: the
    # gate isn't installed at all under testing (app_factory), and an admin
    # can switch the password off, in which case the whole admin UI is
    # already open to the local network.
    if current_app.testing:
        return True
    settings = current_app.config.get("SETTINGS_STORE")
    return settings is not None and not password_required(settings)


def _data_dir() -> Path:
    registry = current_app.config["PLUGIN_REGISTRY"]
    plugin = registry.get("gtfs")
    if plugin is None:
        raise RuntimeError("gtfs plugin not registered")
    path: Path = plugin.data_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


SEARCH_LIMIT = 60


def _search_stops(raw: bytes, query: str) -> tuple[list[dict[str, str]], int]:
    """Substring search over ``stops.txt``. Returns ``(rows, dropped_count)``.

    Only ``stops.txt`` is read — it's the small member, so this stays fast
    even on a feed whose ``stop_times.txt`` is hundreds of MB.
    """
    needle = query.casefold()
    rows: list[dict[str, str]] = []
    dropped = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = {n.rsplit("/", 1)[-1]: n for n in zf.namelist()}
        for row in _rows(zf, names, "stops.txt"):
            name = row.get("stop_name", "")
            sid = row.get("stop_id", "")
            if needle and needle not in name.casefold() and needle not in sid.casefold():
                continue
            if len(rows) >= SEARCH_LIMIT:
                dropped += 1
                continue
            rows.append(
                {
                    "stop_id": sid,
                    "stop_name": name,
                    "parent_station": row.get("parent_station", ""),
                    "location_type": row.get("location_type", ""),
                }
            )
    rows.sort(key=lambda r: (r["stop_name"], r["stop_id"]))
    return rows, dropped


def _inspect_stop(raw: bytes, stop_opt: str) -> dict[str, Any]:
    """Distil one stop and summarise what actually calls there.

    Reuses the render path's ``_distil`` so the page can't drift from what a
    cell would show, then groups by ``direction_id`` — the answer to "which
    direction is 0?", which is agency-defined and undocumented in the feed.
    """
    table = _distil(raw, stop_opt)
    directions: dict[str, dict[str, Any]] = {}
    for route_id, headsign, direction, _service, platform in table["trips"].values():
        bucket = directions.setdefault(
            str(direction or ""), {"headsigns": {}, "routes": set(), "platforms": set()}
        )
        if headsign:
            bucket["headsigns"][headsign] = bucket["headsigns"].get(headsign, 0) + 1
        bucket["routes"].add(route_id)
        bucket["platforms"].add(platform)
    summary = []
    for direction, bucket in sorted(directions.items()):
        top = sorted(bucket["headsigns"].items(), key=lambda kv: -kv[1])[:4]
        summary.append(
            {
                "direction": direction or "(unset)",
                "headsigns": [h for h, _n in top],
                "routes": sorted(bucket["routes"]),
                "platforms": sorted(bucket["platforms"]),
            }
        )
    routes = table.get("routes") or {}
    return {
        "stop_id": stop_opt,
        "stop_name": table.get("stop_name", ""),
        "stop_ids": table.get("stop_ids") or [],
        "tz": table.get("tz", ""),
        "trips": len(table.get("trips") or {}),
        "routes": [
            {
                "id": rid,
                "name": r.get("short") or r.get("long") or rid,
                "color": r.get("color", ""),
                # The admin page's .pill paints dark-on-light by default,
                # which disappears against a dark route colour.
                "text_color": r.get("text_color", "") or "#FFFFFF",
            }
            for rid, r in sorted(routes.items())
        ],
        "directions": summary,
    }


# ----------------------------------------------------------------------
# Demo fixtures
# ----------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"


def _demo(name: str, title: str) -> dict[str, Any]:
    """Paint a canned board from ``fixtures/<name>.json``.

    Set the feed URL to ``demo:delayed`` (or any other fixture name) to see a
    scenario that's awkward to catch on a live feed — a badly delayed train,
    a stop with nothing running — without waiting for one to happen. Arrival
    times are rebuilt against the current clock on every render, so the board
    always looks live rather than frozen at whatever time it was written.
    """
    path = FIXTURES / f"{Path(name).name}.json"
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        available = ", ".join(sorted(p.stem for p in FIXTURES.glob("*.json")))
        return {"error": f"No demo fixture '{name}'. Try: {available}."}

    now = datetime.now().astimezone()
    arrivals = []
    for i, entry in enumerate(fixture.get("arrivals") or []):
        minutes = _int(entry.get("in"), 0)
        when = now + timedelta(minutes=minutes)
        delay = _int(entry.get("delay"), 0)
        arrivals.append(
            {
                "trip_id": f"demo-{name}-{i}",
                "stop_id": "DEMO",
                "minutes": minutes,
                "time": when.strftime("%H:%M"),
                "sched": (when - timedelta(minutes=delay)).strftime("%H:%M"),
                "delay": delay,
                "route": entry.get("route", ""),
                "headsign": entry.get("headsign", ""),
                "mode": entry.get("mode", "subway"),
                "color": entry.get("color", ""),
                "text_color": entry.get("text_color", ""),
                "live": bool(entry.get("live", True)),
                "canceled": bool(entry.get("canceled", False)),
                "route_id": entry.get("route", ""),
                "direction": str(entry.get("direction", "0")),
            }
        )
    return {
        "stop": fixture.get("stop", "Demo stop"),
        "label": title or fixture.get("stop", "Demo stop"),
        "now": now.strftime("%H:%M"),
        "live": any(a["live"] for a in arrivals),
        "note": fixture.get("note", ""),
        "arrivals": arrivals[:MAX_ARRIVALS],
    }


# ----------------------------------------------------------------------
# Scheduled arrivals
# ----------------------------------------------------------------------


def _scheduled(
    table: dict[str, Any],
    now: datetime,
    horizon: int,
    walk: int,
    basis: str = "arrival",
) -> list[dict[str, Any]]:
    """Expand the distilled timetable into concrete arrivals around ``now``.

    Walks yesterday / today / tomorrow because GTFS arrival times run past
    24:00:00 for after-midnight trips, so "tomorrow 00:20" can belong to
    today's service day and yesterday's service day can still be running.
    """
    routes = table.get("routes") or {}
    trips = table.get("trips") or {}
    services = table.get("services") or {}
    lo = now + timedelta(minutes=walk)
    hi = now + timedelta(minutes=horizon)

    out: list[dict[str, Any]] = []
    today = now.date()
    for offset in (-1, 0, 1):
        day = today + timedelta(days=offset)
        active = {sid for sid, spec in services.items() if _runs_on(spec, day)}
        if not active:
            continue
        # GTFS service day = noon minus 12h, which lands on the right wall
        # clock across a DST boundary (a plain midnight can be ambiguous).
        base = datetime(day.year, day.month, day.day, 12, tzinfo=now.tzinfo) - timedelta(hours=12)
        stop_names = table.get("stop_names") or {}
        for trip_id, sec, *rest in table.get("times") or []:
            trip = trips.get(trip_id)
            if trip is None or trip[3] not in active:
                continue
            if basis == "departure" and len(rest) > 1:
                sec = rest[1]
            when = base + timedelta(seconds=sec)
            if when < lo or when > hi:
                continue
            route = routes.get(trip[0]) or {}
            out.append(
                {
                    "trip_id": trip_id,
                    "stop_id": trip[4],
                    "minutes": int((when - now).total_seconds() // 60),
                    "time": when.strftime("%H:%M"),
                    # Kept so an RT prediction can be expressed as a delay
                    # against the timetable; stripped before the payload ships.
                    "sched": when.strftime("%H:%M"),
                    "sched_epoch": when.timestamp(),
                    "seq": rest[0] if rest else 0,
                    "stop_name": stop_names.get(trip[4], ""),
                    "delay": 0,
                    "route": route.get("short") or route.get("long") or trip[0],
                    "route_id": trip[0],
                    "direction": str(trip[2] or ""),
                    "headsign": trip[1] or route.get("long") or "",
                    "mode": _MODES.get(_int(route.get("type"), 3), "bus"),
                    "color": route.get("color") or "",
                    "text_color": route.get("text_color") or "",
                    "live": False,
                }
            )
    out.sort(key=lambda a: a["minutes"])
    return out


def _runs_on(spec: dict[str, Any], day: _date) -> bool:
    ymd = day.strftime("%Y%m%d")
    if ymd in (spec.get("rem") or []):
        return False
    if ymd in (spec.get("add") or []):
        return True
    days = spec.get("days") or []
    if len(days) != 7 or not days[day.weekday()]:
        return False
    start, end = spec.get("start") or "", spec.get("end") or ""
    if start and ymd < start:
        return False
    return not (end and ymd > end)


# ----------------------------------------------------------------------
# GTFS-RT
# ----------------------------------------------------------------------


def _realtime_all(
    urls: list[str], data_dir: Path, key: str, stop_ids: set[str]
) -> tuple[dict[str, Any], str]:
    """Merge several realtime feeds into one bundle.

    Needed because agencies split realtime by line group — the MTA publishes
    one feed per group — so a board covering two stations often spans two
    feeds. Later feeds don't overwrite earlier ones; the keys are per trip,
    and a trip appears in exactly one group's feed.
    """
    merged: dict[str, Any] = {"times": {}, "canceled": [], "tracks": {}, "vehicles": {}}
    notes: list[str] = []
    stamps: list[int] = []
    for url in urls:
        bundle, note = _realtime(url, data_dir, f"{key}_{_url_key(url)}", stop_ids)
        merged["times"].update(bundle.get("times") or {})
        merged["tracks"].update(bundle.get("tracks") or {})
        merged["vehicles"].update(bundle.get("vehicles") or {})
        merged["canceled"].extend(bundle.get("canceled") or [])
        if bundle.get("stamp"):
            stamps.append(int(bundle["stamp"]))
        if note:
            notes.append(note)
    # Report the oldest feed's age: the board is only as current as its
    # stalest source.
    merged["stamp"] = min(stamps) if stamps else None
    return merged, notes[0] if notes else ""


def _url_key(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:8]


def _realtime(url: str, data_dir: Path, key: str, stop_ids: set[str]) -> tuple[dict[str, Any], str]:
    """Return ``(realtime_bundle, note)`` for this stop.

    The bundle carries ``times`` / ``canceled`` / ``tracks`` / ``vehicles``
    (trip -> the stop_sequence it's currently at) / ``stamp`` (the feed's own
    header timestamp). Errors are non-fatal: the scheduled board is still
    worth painting, so a dead RT feed comes back as a note instead of an
    error.
    """
    cache = data_dir / f"rt_{key}.json"
    cached, fresh = _read_cache(cache, RT_TTL_S)
    if cached is not None and fresh:
        return cached, ""
    try:
        body = _download(url, RT_TIMEOUT_S)
        canceled: set[str] = set()
        tracks: dict[str, str] = {}
        times = _decode_trip_updates(body, stop_ids, canceled, tracks)
        bundle: dict[str, Any] = {
            "times": times,
            "canceled": sorted(canceled),
            "tracks": tracks,
            "vehicles": _decode_vehicles(body),
            # The feed's own timestamp, so the widget can say how old these
            # predictions are instead of presenting a stalled feed as live.
            "stamp": _feed_stamp(body),
        }
    except Exception:
        # Serve the last good predictions rather than dropping to schedule-
        # only on a single blip; they're at most a couple of minutes old.
        if cached is not None:
            return cached, "Live feed unavailable"
        return {}, "Live feed unavailable"
    with contextlib.suppress(OSError):
        _write_json(cache, bundle)
    return bundle, ""


def _apply_realtime(
    arrivals: list[dict[str, Any]],
    rt: dict[str, int],
    now: datetime,
    canceled: set[str] | None = None,
    tracks: dict[str, str] | None = None,
    vehicles: dict[str, int] | None = None,
) -> bool:
    """Overlay RT predictions onto scheduled arrivals in place."""
    live = False
    for a in arrivals:
        if canceled and any(c in canceled for c in _trip_keys(a["trip_id"])):
            # Keep the row at its scheduled time but flag it: "the 14:05 is
            # cancelled" is the useful statement, not a silently missing row.
            a["canceled"] = True
            a["live"] = True
            live = True
            continue
        for candidate in _trip_keys(a["trip_id"]):
            epoch = rt.get(f"{candidate}|{a['stop_id']}")
            if epoch is None:
                continue
            when = datetime.fromtimestamp(epoch, tz=now.tzinfo)
            a["minutes"] = int((when - now).total_seconds() // 60)
            a["time"] = when.strftime("%H:%M")
            a["live"] = True
            if tracks:
                a["track"] = tracks.get(f"{candidate}|{a['stop_id']}", "")
            if vehicles:
                at_seq = vehicles.get(candidate)
                seq = a.get("seq") or 0
                # Both sequences come from the same trip, so the difference is
                # the number of stops still to go. Negative means the feed has
                # the train already past us; treat that as "no idea" rather
                # than showing a nonsense countdown.
                if at_seq is not None and seq:
                    away = seq - at_seq
                    if 0 <= away <= 25:
                        a["stops_away"] = away
            # Signed whole minutes against the timetable: positive is late.
            # Rounded, not floored, so a 90-second delay reads as 2 rather
            # than 1 and the board doesn't under-report lateness.
            sched_epoch = a.get("sched_epoch")
            if sched_epoch:
                a["delay"] = round((epoch - sched_epoch) / 60)
            live = True
            break
    return live


def _trip_keys(trip_id: str) -> tuple[str, ...]:
    """Trip IDs an RT feed might use for this static trip.

    Most feeds reuse the static trip_id verbatim. The MTA's don't: static is
    ``AFA24GEN-A-Weekday-00_064750_A..N03R`` where RT sends
    ``064750_A..N03R``, i.e. everything after the first underscore.
    ponytail: two candidates covers every feed tried; a full block/start-time
    match is the upgrade if some agency needs it.
    """
    if "_" in trip_id:
        return (trip_id, trip_id.split("_", 1)[1])
    return (trip_id,)


# gtfs-realtime.proto field numbers. Named once here so the decoders below
# read as structure rather than arithmetic; the proto is the source of truth
# for every value. 1001 is the NYCT extension slot (gtfs-realtime-NYCT.proto),
# which needs no special handling — an extension is just a field number the
# base spec doesn't claim.
_FEED_HEADER, _FEED_ENTITY = 1, 2
_HEADER_TIMESTAMP = 3
_ENTITY_TRIP_UPDATE, _ENTITY_VEHICLE, _ENTITY_ALERT = 3, 4, 5
_TU_TRIP, _TU_STOP_TIME_UPDATE = 1, 2
_TRIP_ID, _TRIP_SCHEDULE_REL = 1, 4
_SCHEDULE_REL_CANCELED = 3
_STU_ARRIVAL, _STU_DEPARTURE, _STU_STOP_ID, _STU_NYCT = 2, 3, 4, 1001
_NYCT_SCHEDULED_TRACK, _NYCT_ACTUAL_TRACK = 1, 2
_EVENT_TIME = 2
_VP_TRIP, _VP_CURRENT_STOP_SEQUENCE = 1, 3
_ALERT_INFORMED, _ALERT_HEADER = 5, 10
_SELECTOR_ROUTE_ID, _SELECTOR_STOP_ID = 2, 5
_TRANSLATION, _TRANSLATION_TEXT = 1, 1


def _subs(buf: bytes, field: int) -> Iterator[bytes]:
    """Every length-delimited value of ``field`` — i.e. nested messages."""
    for got, _wire, value in _pb_fields(buf):
        if got == field and isinstance(value, bytes):
            yield value


def _sub(buf: bytes | None, field: int) -> bytes | None:
    """The first nested message at ``field``, or None."""
    if buf is None:
        return None
    return next(_subs(buf, field), None)


def _text(buf: bytes | None, field: int) -> str:
    if buf is None:
        return ""
    value = next(_subs(buf, field), None)
    return value.decode("utf-8", "ignore") if value is not None else ""


def _num(buf: bytes | None, field: int) -> int | None:
    """The first numeric (varint / fixed-width) value of ``field``."""
    if buf is None:
        return None
    for got, _wire, value in _pb_fields(buf):
        if got == field and isinstance(value, int):
            return value
    return None


def _feed_stamp(body: bytes) -> int | None:
    """FeedHeader.timestamp — when the agency last built this feed.

    Without it a stalled feed still paints confident countdowns, because the
    predictions themselves carry no hint that nothing has moved for an hour.
    """
    return _num(_sub(body, _FEED_HEADER), _HEADER_TIMESTAMP)


def _decode_trip_updates(
    body: bytes,
    stop_ids: set[str],
    canceled_out: set[str] | None = None,
    tracks_out: dict[str, str] | None = None,
) -> dict[str, int]:
    """Pull ``(trip_id, stop_id) -> arrival epoch`` out of a FeedMessage.

    Canceled trips carry no useful times, but dropping them silently would
    leave the trip painting at its *scheduled* time as though it were still
    running — worse than not knowing. Their IDs collect in ``canceled_out``,
    and NYCT track labels in ``tracks_out``.
    """
    out: dict[str, int] = {}
    for entity in _subs(body, _FEED_ENTITY):
        for update in _subs(entity, _ENTITY_TRIP_UPDATE):
            trip = _sub(update, _TU_TRIP)
            trip_id = _text(trip, _TRIP_ID)
            if not trip_id:
                continue
            if _num(trip, _TRIP_SCHEDULE_REL) == _SCHEDULE_REL_CANCELED:
                if canceled_out is not None:
                    canceled_out.add(trip_id)
                continue
            for stu in _subs(update, _TU_STOP_TIME_UPDATE):
                stop_id = _text(stu, _STU_STOP_ID)
                if not stop_id or (stop_ids and stop_id not in stop_ids):
                    continue
                when = _num(_sub(stu, _STU_ARRIVAL), _EVENT_TIME) or _num(
                    _sub(stu, _STU_DEPARTURE), _EVENT_TIME
                )
                if not when:
                    continue
                key = f"{trip_id}|{stop_id}"
                out[key] = int(when)
                if tracks_out is not None:
                    nyct = _sub(stu, _STU_NYCT)
                    track = _text(nyct, _NYCT_ACTUAL_TRACK) or _text(nyct, _NYCT_SCHEDULED_TRACK)
                    if track:
                        tracks_out[key] = track
    return out


def _decode_vehicles(body: bytes) -> dict[str, int]:
    """``trip_id -> the stop_sequence the vehicle is currently at``.

    Same response as the trip updates, different entity type. Comparing this
    against the trip's sequence at our stop is what turns a countdown into
    "three stops away".
    """
    out: dict[str, int] = {}
    for entity in _subs(body, _FEED_ENTITY):
        for vehicle in _subs(entity, _ENTITY_VEHICLE):
            trip_id = _text(_sub(vehicle, _VP_TRIP), _TRIP_ID)
            seq = _num(vehicle, _VP_CURRENT_STOP_SEQUENCE)
            if trip_id and seq is not None:
                out[trip_id] = int(seq)
    return out


def _alerts(
    url: str, data_dir: Path, key: str, stop_ids: set[str], route_ids: set[str]
) -> list[str]:
    """Header text of any service alert naming this stop or one of its routes."""
    cache = data_dir / f"al_{key}.json"
    cached, fresh = _read_cache(cache, ALERT_TTL_S)
    if cached is not None and fresh:
        texts: list[str] = list(cached.get("alerts") or [])
        return texts
    try:
        found = _decode_alerts(_download(url, RT_TIMEOUT_S), stop_ids, route_ids)
    except Exception:
        return list(cached.get("alerts") or []) if cached else []
    with contextlib.suppress(OSError):
        _write_json(cache, {"alerts": found})
    return found


def _decode_alerts(body: bytes, stop_ids: set[str], route_ids: set[str]) -> list[str]:
    """Matching alert headers, most specific first.

    Only one alert fits in the title bar, so ranking is the feature: an alert
    about this stop beats one about a route that merely calls here, which
    beats an agency-wide notice. An alert naming no entity at all is
    agency-wide by definition.
    """
    ranked: list[tuple[int, str]] = []
    for entity in _subs(body, _FEED_ENTITY):
        for alert in _subs(entity, _ENTITY_ALERT):
            selectors = 0
            rank = 3
            for selector in _subs(alert, _ALERT_INFORMED):
                selectors += 1
                if _text(selector, _SELECTOR_STOP_ID) in stop_ids:
                    rank = min(rank, 0)
                elif _text(selector, _SELECTOR_ROUTE_ID) in route_ids:
                    rank = min(rank, 1)
            if selectors == 0:
                rank = min(rank, 2)
            header = _translated(_sub(alert, _ALERT_HEADER))
            if header and rank < 3:
                ranked.append((rank, header))
    ranked.sort(key=lambda item: item[0])
    # Dedupe, keeping order: agencies routinely publish one alert per
    # affected route, all with identical header text.
    return list(dict.fromkeys(text for _rank, text in ranked))


def _translated(buf: bytes | None) -> str:
    """First translation's text out of a TranslatedString.

    Agencies embed hard newlines and runs of spaces in alert text; the title
    bar is one line, so flatten here rather than fighting it in CSS.
    "[airplane icon]"-style markers are placeholders for glyphs we don't
    have — drop those, but keep route bullets like "[A]".
    """
    text = _text(_sub(buf, _TRANSLATION), _TRANSLATION_TEXT)
    return " ".join(_ICON_MARKER.sub("", text).split())


def _pb_fields(buf: bytes) -> Iterator[tuple[int, int, Any]]:
    """Walk one protobuf message, yielding ``(field_number, wire_type, value)``.

    Length-delimited fields come back as ``bytes`` (a nested message, or a
    string); varints and fixed-width fields as ``int``. Unknown fields fall
    out naturally, which is the whole reason this is enough to read an RT
    feed — including vendor extensions — without the generated classes or a
    protobuf dependency. Malformed input raises, and the callers treat that
    as "no live data".
    """
    i, n = 0, len(buf)
    value: Any
    while i < n:
        key, i = _varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 1:
            value, i = int.from_bytes(buf[i : i + 8], "little"), i + 8
        elif wire == 2:
            length, i = _varint(buf, i)
            if i + length > n:
                raise ValueError("truncated protobuf field")
            value, i = buf[i : i + length], i + length
        elif wire == 5:
            value, i = int.from_bytes(buf[i : i + 4], "little"), i + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield field, wire, value


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


# ----------------------------------------------------------------------
# Static feed -> distilled per-stop timetable
# ----------------------------------------------------------------------


def _start_build(key: str, work: Any) -> threading.Thread:
    """Run ``work`` on a background thread, one per (feed, stop) at a time."""
    with _BUILD_LOCK:
        existing = _BUILDS.get(key)
        if existing is not None and existing.is_alive():
            return existing
        thread = threading.Thread(target=work, name=f"gtfs-build-{key}", daemon=True)
        _BUILDS[key] = thread
        thread.start()
        return thread


def _prune(data_dir: Path) -> None:
    """Drop cache files nothing will read again.

    Feed zips are the big ones (5 MB+ each) and are re-downloaded on demand,
    so anything past twice the timetable TTL is dead weight; distilled
    tables and indexes are small but accumulate one per stop ever previewed.
    Runs on the background build path, so no render waits for it.
    """
    now = time.time()
    ages = {"feed_": TIMETABLE_TTL_S * 2, "tt2_": 86400 * 7, "stops3_": 86400 * 7}
    for path in data_dir.glob("*"):
        limit = next((age for prefix, age in ages.items() if path.name.startswith(prefix)), None)
        if limit is None:
            continue
        with contextlib.suppress(OSError):
            if now - path.stat().st_mtime > limit:
                path.unlink()


def _build(url: str, stop_opt: str, path: Path, err_path: Path) -> None:
    """Download the feed, distil this stop's timetable, write it to disk."""
    _prune(path.parent)
    try:
        table = _distil(_feed_bytes(url, path.parent), stop_opt)
    except zipfile.BadZipFile:
        _write_error(err_path, "That URL didn't return a GTFS zip.")
        return
    except _StopNotFound as err:
        _write_error(err_path, str(err))
        return
    except Exception:
        _write_error(err_path, "Couldn't load the GTFS feed right now.")
        return
    with contextlib.suppress(OSError):
        _write_json(path, table)
    # A good build clears the cooldown so a feed that recovers isn't held
    # back by the last failure.
    with contextlib.suppress(OSError):
        err_path.unlink(missing_ok=True)


def _feed_bytes(url: str, data_dir: Path) -> bytes:
    """The feed zip, cached on disk for the timetable TTL.

    Shared by the builder and the admin stop finder: without it, searching
    stops and then rendering re-downloads the same tens of MB twice.
    """
    cache = data_dir / f"feed_{hashlib.sha1(url.encode()).hexdigest()[:16]}.zip"
    try:
        if cache.exists() and (time.time() - cache.stat().st_mtime) < TIMETABLE_TTL_S:
            return cache.read_bytes()
    except OSError:
        pass
    raw = _download(url, GTFS_TIMEOUT_S)
    with contextlib.suppress(OSError):
        tmp = cache.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(cache)
    return raw


class _StopNotFound(Exception):
    pass


def _distil(raw: bytes, stop_opt: str) -> dict[str, Any]:
    wanted = {s.strip() for s in stop_opt.split(",") if s.strip()}
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = {n.rsplit("/", 1)[-1]: n for n in zf.namelist()}

        stop_ids: set[str] = set()
        stop_name = ""
        station_of: dict[str, str] = {}
        names_by_id: dict[str, str] = {}
        for row in _rows(zf, names, "stops.txt"):
            sid = row.get("stop_id", "")
            parent = row.get("parent_station", "")
            if sid in wanted or parent in wanted:
                stop_ids.add(sid)
                station_of[sid] = parent or sid
                names_by_id[sid] = row.get("stop_name", "")
                if not stop_name or sid in wanted:
                    stop_name = row.get("stop_name", "") or stop_name
        if not stop_ids:
            raise _StopNotFound(f"Stop '{stop_opt}' isn't in this feed.")

        routes: dict[str, Any] = {}
        for row in _rows(zf, names, "routes.txt"):
            routes[row.get("route_id", "")] = {
                "short": row.get("route_short_name", ""),
                "long": row.get("route_long_name", ""),
                "color": _hex(row.get("route_color", "")),
                "text_color": _hex(row.get("route_text_color", "")),
                "type": _int(row.get("route_type"), 3),
            }

        # stop_times.txt is the big one (hundreds of MB for a subway feed),
        # so it streams and keeps only rows at this stop.
        times: list[tuple[str, int, int, int]] = []
        at_stop: dict[str, str] = {}
        for row in _rows(zf, names, "stop_times.txt"):
            sid = row.get("stop_id", "")
            if sid not in stop_ids:
                continue
            arr = _hms(row.get("arrival_time") or row.get("departure_time") or "")
            dep = _hms(row.get("departure_time") or row.get("arrival_time") or "")
            if arr is None:
                continue
            trip_id = row.get("trip_id", "")
            # stop_sequence is what a VehiclePosition's own sequence gets
            # compared against to answer "how many stops away is it". Both
            # times are kept: at a terminus the departure is the useful one.
            times.append(
                (trip_id, arr, _int(row.get("stop_sequence"), 0), dep if dep is not None else arr)
            )
            at_stop[trip_id] = sid
        times.sort(key=lambda t: t[1])

        trips: dict[str, list[Any]] = {}
        for row in _rows(zf, names, "trips.txt"):
            trip_id = row.get("trip_id", "")
            if trip_id not in at_stop:
                continue
            trips[trip_id] = [
                row.get("route_id", ""),
                row.get("trip_headsign", ""),
                row.get("direction_id", ""),
                row.get("service_id", ""),
                at_stop[trip_id],
            ]

        services: dict[str, Any] = {}
        for row in _rows(zf, names, "calendar.txt"):
            services[row.get("service_id", "")] = {
                "days": [
                    _int(row.get(d), 0)
                    for d in (
                        "monday",
                        "tuesday",
                        "wednesday",
                        "thursday",
                        "friday",
                        "saturday",
                        "sunday",
                    )
                ],
                "start": row.get("start_date", ""),
                "end": row.get("end_date", ""),
                "add": [],
                "rem": [],
            }
        for row in _rows(zf, names, "calendar_dates.txt"):
            spec = services.setdefault(
                row.get("service_id", ""),
                {"days": [0] * 7, "start": "", "end": "", "add": [], "rem": []},
            )
            bucket = "add" if _int(row.get("exception_type"), 1) == 1 else "rem"
            spec[bucket].append(row.get("date", ""))

        tz = ""
        for row in _rows(zf, names, "agency.txt"):
            tz = row.get("agency_timezone", "") or tz
            break

    # Only the routes/services these trips actually reference get kept, so
    # the distillate stays a few KB even for a 60-route agency.
    used_routes = {t[0] for t in trips.values()}
    used_services = {t[3] for t in trips.values()}
    # A platform inherits its station's name, so a two-station board can
    # label each row with where it leaves from.
    platform_names = {
        sid: names_by_id.get(station_of.get(sid, sid)) or names_by_id.get(sid, "")
        for sid in stop_ids
    }
    return {
        "built": time.time(),
        "stop_name": stop_name,
        "stop_names": platform_names,
        "stop_ids": sorted(stop_ids),
        "tz": tz or "UTC",
        "routes": {k: v for k, v in routes.items() if k in used_routes},
        "services": {k: v for k, v in services.items() if k in used_services},
        "trips": trips,
        "times": [list(t) for t in times if t[0] in trips],
    }


def _rows(zf: zipfile.ZipFile, names: dict[str, str], member: str) -> Iterator[dict[str, str]]:
    """Stream one GTFS CSV member. Missing members yield nothing, several are
    optional (calendar.txt, calendar_dates.txt) and feeds ship either or both."""
    path = names.get(member)
    if path is None:
        return
    with zf.open(path) as handle:
        yield from csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig", newline=""))


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------


# Only http(s) reaches the network. Every URL here — feed, TripUpdates,
# alerts, and the admin page's ``feed_url`` query arg — is user-supplied, and
# urllib's default opener also speaks file:// and ftp://, so a pasted
# "file:///etc/passwd" would otherwise be read and parsed server-side.
# Registering only the HTTP handlers refuses those at the transport layer
# rather than relying on the scheme check alone.
_OPENER = urllib.request.build_opener(urllib.request.HTTPHandler, urllib.request.HTTPSHandler)


def _download(url: str, timeout: float) -> bytes:
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"unsupported URL scheme: {scheme or '(none)'}")
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    )
    with _OPENER.open(req, timeout=timeout) as resp:
        body: bytes = resp.read()
    return body


def _read_cache(path: Path, ttl: float) -> tuple[dict[str, Any] | None, bool]:
    """Return ``(payload, is_fresh)``. Stale payloads still come back so the
    caller can serve them while a rebuild runs."""
    if not path.exists():
        return None, False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, False
    if not isinstance(payload, dict) or "error" in payload:
        return None, False
    return payload, (time.time() - path.stat().st_mtime) < ttl


def _read_error(path: Path) -> tuple[str, bool]:
    """Return ``(message, still_in_cooldown)`` for a failed build.

    The cooldown flag is what stops a broken feed from being re-fetched on
    every single render.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - path.stat().st_mtime
    except (json.JSONDecodeError, OSError):
        return "", False
    if not isinstance(payload, dict):
        return "", False
    return str(payload.get("error", "")), age < BUILD_RETRY_S


def _write_error(path: Path, message: str) -> None:
    with contextlib.suppress(OSError):
        _write_json(path, {"error": message})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write via a temp file so a render never reads a half-written table."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def _hms(value: str) -> int | None:
    """``"25:10:00"`` -> seconds since the service day started."""
    parts = value.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) > 2 and parts[2] else 0
    except ValueError:
        return None
    return h * 3600 + m * 60 + s


def _hex(value: str) -> str:
    v = value.strip().lstrip("#")
    return f"#{v.upper()}" if len(v) == 6 and all(c in "0123456789abcdefABCDEF" for c in v) else ""


def _int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _positive_int(value: Any, default: int) -> int:
    parsed = _int(value, default)
    return parsed if parsed > 0 else default
