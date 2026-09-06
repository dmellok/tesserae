"""gtfs smoke: a synthetic feed renders at every size, and the hand-rolled
GTFS-RT protobuf reader pulls the right arrival out of a TripUpdates blob.

No network: the URL opener is patched to serve an in-memory GTFS zip built a
few minutes into the future, so the render always has something to paint.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import pytest
from flask.testing import FlaskClient

# The plugin's restricted opener (no file:// / ftp:// handlers) means
# patching ``urllib.request.urlopen`` no longer intercepts anything. Patch
# the OpenerDirector class instead: that catches every instance, including
# the separate module object the Flask app loads for the real render path.
_OPEN = "urllib.request.OpenerDirector.open"

_PLUGIN = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("gtfs_server", _PLUGIN / "server.py")
assert _spec and _spec.loader
gtfs_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gtfs_server)


def _feed(
    second_direction: str = "0", second_stop: bool = False, pad_header: bool = False
) -> bytes:
    """A one-stop, two-trip GTFS zip with arrivals 4 and 9 minutes out.

    ``second_direction`` puts the two trips on opposite ``direction_id``s.
    ``second_stop`` moves the second trip to a different station, which is
    what a two-stop board merges. ``pad_header`` writes the stops.txt header
    with a space around every name, the way some agencies export it.
    """
    now = datetime.now(UTC)
    first = now + timedelta(minutes=4)
    second = now + timedelta(minutes=9)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("agency.txt", "agency_id,agency_name,agency_timezone\nT,Test,UTC\n")
        stops_header = "stop_id,stop_name,parent_station"
        if pad_header:
            stops_header = " stop_id , stop_name , parent_station "
        zf.writestr(
            "stops.txt",
            stops_header + "\n"
            "S1,Canal St,\n"
            "S1N,Canal St North,S1\n"
            "S2,Franklin St,\n"
            "S2N,Franklin St North,S2\n",
        )
        zf.writestr(
            "routes.txt",
            "route_id,route_short_name,route_long_name,route_type,route_color,route_text_color\n"
            "R1,A,Eighth Avenue Express,1,0039A6,FFFFFF\n",
        )
        zf.writestr(
            "trips.txt",
            "route_id,service_id,trip_id,trip_headsign,direction_id\n"
            "R1,ALL,T1,Inwood-207 St,0\n"
            f"R1,ALL,T2,Far Rockaway,{second_direction}\n",
        )
        zf.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            f"T1,{first:%H:%M:%S},{first:%H:%M:%S},S1N,1\n"
            f"T2,{second:%H:%M:%S},{second:%H:%M:%S},{'S2N' if second_stop else 'S1N'},1\n"
            # A row at a different stop, must not leak into the board.
            f"T1,{first:%H:%M:%S},{first:%H:%M:%S},S9,2\n",
        )
        zf.writestr(
            "calendar.txt",
            "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
            "start_date,end_date\n"
            "ALL,1,1,1,1,1,1,1,20000101,20990101\n",
        )
    return buf.getvalue()


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *a: object) -> bool:
        return False


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_gtfs_renders(client: FlaskClient, size: str) -> None:
    opts = '{"gtfs_url":"https://example.test/gtfs.zip","stop_id":"S1"}'
    feed = _feed()
    with patch(_OPEN, return_value=_FakeResp(feed)):
        resp = client.get(f"/_test/render?plugin=gtfs&size={size}&opts={opts}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="gtfs"' in body
    # Stop name and the first arrival's headsign came out of the zip.
    assert "Canal St" in body
    assert "Inwood-207 St" in body
    # The row at the other stop never made it into the board.
    assert "S9" not in body


# ----------------------------------------------------------------------
# GTFS-RT decoding
# ----------------------------------------------------------------------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint(field << 3 | wire)


def _msg(field: int, payload: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(payload)) + payload


def _str(field: int, text: str) -> bytes:
    return _msg(field, text.encode())


def _int(field: int, value: int) -> bytes:
    return _tag(field, 0) + _varint(value)


def _trip_update(trip_id: str, stop_id: str, epoch: int, canceled: bool = False) -> bytes:
    trip = _str(1, trip_id) + (_int(4, 3) if canceled else b"")
    stu = _str(4, stop_id) + _msg(2, _int(2, epoch))
    return _msg(3, _msg(1, trip) + _msg(2, stu))


def test_decode_trip_updates_reads_arrival_times() -> None:
    feed = _msg(2, _trip_update("T1", "S1N", 1_800_000_000))
    assert gtfs_server._decode_trip_updates(feed, {"S1N"}) == {"T1|S1N": 1_800_000_000}


def test_decode_trip_updates_skips_other_stops_and_cancellations() -> None:
    feed = _msg(2, _trip_update("T1", "S2", 1_800_000_000)) + _msg(
        2, _trip_update("T2", "S1N", 1_800_000_100, canceled=True)
    )
    assert gtfs_server._decode_trip_updates(feed, {"S1N"}) == {}


def test_realtime_prediction_overrides_the_schedule() -> None:
    """An RT time for a trip wins over its scheduled time, and the MTA's
    'RT drops the block prefix' trip_id form still matches."""
    now = datetime.now(UTC)
    arrivals = [
        {
            "trip_id": "BLOCK-Weekday-00_077150_C..S04R",
            "stop_id": "S1N",
            "minutes": 9,
            "time": "",
            "live": False,
        }
    ]
    epoch = int((now + timedelta(minutes=3)).timestamp())
    live = gtfs_server._apply_realtime(arrivals, {"077150_C..S04R|S1N": epoch}, now)
    assert live is True
    assert arrivals[0]["minutes"] == 2  # 3 minutes out, floored to whole minutes
    assert arrivals[0]["live"] is True


def test_delay_is_signed_minutes_against_the_timetable() -> None:
    """Late is positive, early is negative, both measured off ``sched_epoch``."""
    now = datetime.now(UTC)
    scheduled = now + timedelta(minutes=10)

    def _arrival() -> list[dict[str, object]]:
        return [
            {
                "trip_id": "T1",
                "stop_id": "S1N",
                "minutes": 10,
                "sched_epoch": scheduled.timestamp(),
                "delay": 0,
                "live": False,
            }
        ]

    late = _arrival()
    gtfs_server._apply_realtime(
        late, {"T1|S1N": int((scheduled + timedelta(minutes=6)).timestamp())}, now
    )
    assert late[0]["delay"] == 6

    early = _arrival()
    gtfs_server._apply_realtime(
        early, {"T1|S1N": int((scheduled - timedelta(minutes=2)).timestamp())}, now
    )
    assert early[0]["delay"] == -2


@pytest.mark.parametrize(
    "scenario", ["mixed", "delayed", "on_time", "buses", "no_service", "cancellations"]
)
def test_demo_fixtures_render(client: FlaskClient, scenario: str) -> None:
    """``gtfs_url: demo:<name>`` paints a canned board with no network at all."""
    opts = f'{{"gtfs_url":"demo:{scenario}","stop_id":"ignored"}}'
    resp = client.get(f"/_test/render?plugin=gtfs&size=md&opts={opts}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="gtfs"' in body
    assert "error" not in body.lower() or "no demo fixture" not in body.lower()


def test_unknown_demo_fixture_lists_the_real_ones() -> None:
    out = gtfs_server._demo("nope", "")
    assert "No demo fixture 'nope'" in out["error"]
    assert "delayed" in out["error"]


def test_canceled_trips_are_flagged_not_dropped() -> None:
    """A cancelled trip keeps its row; silently leaving it at its scheduled
    time would read as a train you could still catch."""
    canceled: set[str] = set()
    feed = _msg(2, _trip_update("T9", "S1N", 1_800_000_000, canceled=True))
    assert gtfs_server._decode_trip_updates(feed, {"S1N"}, canceled) == {}
    assert canceled == {"T9"}

    now = datetime.now(UTC)
    arrivals = [{"trip_id": "T9", "stop_id": "S1N", "minutes": 6, "live": False}]
    gtfs_server._apply_realtime(arrivals, {}, now, canceled)
    assert arrivals[0]["canceled"] is True
    assert arrivals[0]["minutes"] == 6


def _alert(header: str, stop_id: str = "", route_id: str = "") -> bytes:
    translated = _msg(10, _msg(1, _str(1, header)))
    selector = b""
    if stop_id:
        selector += _msg(5, _str(5, stop_id))
    if route_id:
        selector += _msg(5, _str(2, route_id))
    return _msg(2, _msg(5, selector + translated))


def test_alerts_match_by_stop_route_or_agency_wide() -> None:
    feed = (
        _alert("Signal problems at Canal St", stop_id="S1N")
        + _alert("A running express", route_id="R1")
        + _alert("Elevator out at 14 St", stop_id="S99")
        + _alert("Reduced service system-wide")
    )
    found = gtfs_server._decode_alerts(feed, {"S1N"}, {"R1"})
    assert found == [
        "Signal problems at Canal St",
        "A running express",
        "Reduced service system-wide",
    ]


def test_nyct_extension_track_is_read() -> None:
    """The MTA ships track numbers as a proto extension (field 1001 on
    StopTimeUpdate). Unknown fields are just fields to the walker, so this
    needs no generated code — but it does need the right field numbers."""
    # NyctStopTimeUpdate{scheduled_track="A1", actual_track="A2"}
    nyct = _msg(1001, _str(1, "A1") + _str(2, "A2"))
    stu = _str(4, "S1N") + _msg(2, _int(2, 1_800_000_000)) + nyct
    feed = _msg(2, _msg(3, _msg(1, _str(1, "T1")) + _msg(2, stu)))

    tracks: dict[str, str] = {}
    times = gtfs_server._decode_trip_updates(feed, {"S1N"}, None, tracks)
    assert times == {"T1|S1N": 1_800_000_000}
    # Actual track wins over scheduled.
    assert tracks == {"T1|S1N": "A2"}


def test_track_lands_on_the_arrival() -> None:
    now = datetime.now(UTC)
    arrivals = [{"trip_id": "T1", "stop_id": "S1N", "minutes": 5, "live": False}]
    gtfs_server._apply_realtime(
        arrivals,
        {"T1|S1N": int((now + timedelta(minutes=4)).timestamp())},
        now,
        None,
        {"T1|S1N": "A2"},
    )
    assert arrivals[0]["track"] == "A2"


def test_two_stops_merge_onto_one_board(client: FlaskClient) -> None:
    """Both picks land on the same board, each row labelled with its stop."""
    digest = hashlib.sha1(b"https://example.test/gtfs.zip").hexdigest()[:16]
    opts = json.dumps(
        {
            "gtfs_url": "https://example.test/gtfs.zip",
            "stop": f"{digest}:S1",
            "stop_2": f"{digest}:S2",
        }
    )
    with patch(_OPEN, return_value=_FakeResp(_feed(second_stop=True))):
        body = client.get(f"/_test/render?plugin=gtfs&size=md&opts={quote(opts)}").get_data(
            as_text=True
        )
    assert "Inwood-207 St" in body  # from S1
    assert "Far Rockaway" in body  # from S2
    # The row data carries which station each train leaves from.
    assert "Canal St" in body
    assert "Franklin St" in body


def test_departure_basis_uses_the_later_time() -> None:
    """At a terminus the train sits before running back, so departure is the
    time you can actually board."""
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    arrive = int((now + timedelta(minutes=10) - midnight).total_seconds())
    table = {
        "tz": "UTC",
        "stop_names": {"S1N": "Canal St"},
        "routes": {"R1": {"short": "A", "type": 1}},
        "trips": {"T1": ["R1", "Inwood-207 St", "0", "ALL", "S1N"]},
        "services": {"ALL": {"days": [1] * 7, "start": "", "end": "", "add": [], "rem": []}},
        "times": [["T1", arrive, 1, arrive + 300]],
    }
    by_arrival = gtfs_server._scheduled(table, now, 90, 0, "arrival")
    by_departure = gtfs_server._scheduled(table, now, 90, 0, "departure")
    assert by_arrival[0]["minutes"] == 9  # ~10 min out, floored
    assert by_departure[0]["minutes"] == 14  # five minutes later


def test_prune_drops_stale_feed_zips_and_keeps_fresh_ones(tmp_path: Path) -> None:
    """Feed zips are 5 MB each and re-downloaded on demand; nothing should
    keep one around forever."""
    import os

    old = tmp_path / "feed_abc.zip"
    fresh = tmp_path / "feed_def.zip"
    other = tmp_path / "rt_abc.json"
    for path in (old, fresh, other):
        path.write_bytes(b"x")
    stale = time.time() - gtfs_server.TIMETABLE_TTL_S * 2 - 10
    os.utime(old, (stale, stale))

    gtfs_server._prune(tmp_path)
    assert not old.exists()
    assert fresh.exists()
    # Small, short-TTL caches aren't the target and are left alone.
    assert other.exists()


def test_feed_stamp_is_read_from_the_header() -> None:
    """A stalled feed still serves confident predictions; the header's own
    timestamp is the only thing that says how old they are."""
    feed = _msg(1, _int(3, 1_800_000_000)) + _msg(2, _trip_update("T1", "S1N", 1))
    assert gtfs_server._feed_stamp(feed) == 1_800_000_000
    # A feed without a header timestamp reports nothing rather than lying.
    assert gtfs_server._feed_stamp(_msg(2, _trip_update("T1", "S1N", 1))) is None


def test_vehicle_positions_give_stops_away() -> None:
    # FeedEntity.vehicle=4, VehiclePosition{trip=1, current_stop_sequence=3}
    feed = _msg(2, _msg(4, _msg(1, _str(1, "T1")) + _int(3, 7)))
    assert gtfs_server._decode_vehicles(feed) == {"T1": 7}

    now = datetime.now(UTC)
    arrivals = [{"trip_id": "T1", "stop_id": "S1N", "minutes": 9, "seq": 10, "live": False}]
    gtfs_server._apply_realtime(
        arrivals,
        {"T1|S1N": int((now + timedelta(minutes=9)).timestamp())},
        now,
        None,
        None,
        {"T1": 7},
    )
    assert arrivals[0]["stops_away"] == 3


def test_stops_away_ignores_a_vehicle_already_past_us() -> None:
    """A feed that has the train beyond our stop would otherwise produce a
    negative countdown."""
    now = datetime.now(UTC)
    arrivals = [{"trip_id": "T1", "stop_id": "S1N", "minutes": 4, "seq": 5, "live": False}]
    gtfs_server._apply_realtime(
        arrivals,
        {"T1|S1N": int((now + timedelta(minutes=4)).timestamp())},
        now,
        None,
        None,
        {"T1": 9},
    )
    assert "stops_away" not in arrivals[0]


def test_routes_option_filters_the_board(client: FlaskClient) -> None:
    opts = json.dumps({"gtfs_url": "https://example.test/gtfs.zip", "stop_id": "S1", "routes": "Q"})
    with patch(_OPEN, return_value=_FakeResp(_feed())):
        body = client.get(f"/_test/render?plugin=gtfs&size=md&opts={quote(opts)}").get_data(
            as_text=True
        )
    # The fixture's only route is "A", so filtering to "Q" empties the board.
    assert "Inwood-207 St" not in body

    opts = json.dumps({"gtfs_url": "https://example.test/gtfs.zip", "stop_id": "S1", "routes": "a"})
    with patch(_OPEN, return_value=_FakeResp(_feed())):
        body = client.get(f"/_test/render?plugin=gtfs&size=md&opts={quote(opts)}").get_data(
            as_text=True
        )
    # Case-insensitive, so "a" still matches route "A".
    assert "Inwood-207 St" in body


def test_alerts_rank_stop_specific_first() -> None:
    """Only one alert fits in the title bar, so the one naming this stop has
    to beat a route-wide notice that merely touches it."""
    feed = (
        _alert("Reduced service system-wide")
        + _alert("JFK AirTrain suspended", route_id="R1")
        + _alert("Signal problems at Canal St", stop_id="S1N")
    )
    assert gtfs_server._decode_alerts(feed, {"S1N"}, {"R1"})[0] == ("Signal problems at Canal St")


def test_alert_text_is_flattened_and_stripped_of_icon_markers() -> None:
    feed = _alert("[airplane icon] JFK AirTrain\nis  suspended", stop_id="S1N")
    assert gtfs_server._decode_alerts(feed, {"S1N"}, set()) == ["JFK AirTrain is suspended"]


def test_alerts_dedupe_identical_headers() -> None:
    """Agencies publish one alert per affected route, same text on each."""
    feed = _alert("Weekend service change", route_id="R1") + _alert(
        "Weekend service change", stop_id="S1N"
    )
    assert gtfs_server._decode_alerts(feed, {"S1N"}, {"R1"}) == ["Weekend service change"]


def test_failed_build_backs_off_instead_of_refetching(tmp_path: Path) -> None:
    """A broken feed must not re-download on every render tick."""
    err_path = tmp_path / "err_x.json"
    gtfs_server._write_error(err_path, "That URL didn't return a GTFS zip.")
    message, cooling = gtfs_server._read_error(err_path)
    assert message == "That URL didn't return a GTFS zip."
    assert cooling is True

    # Aged past the cooldown, the next render is allowed to retry.
    import os

    old = time.time() - gtfs_server.BUILD_RETRY_S - 1
    os.utime(err_path, (old, old))
    _message, cooling = gtfs_server._read_error(err_path)
    assert cooling is False


def test_failed_build_leaves_a_stale_timetable_intact(tmp_path: Path) -> None:
    """Errors go to their own file, so a feed that breaks doesn't wipe the
    last-known-good table out from under the board."""
    tt_path = tmp_path / "tt_x.json"
    err_path = tmp_path / "err_x.json"
    gtfs_server._write_json(tt_path, {"stop_name": "Canal St", "trips": {}})
    with patch.object(gtfs_server, "_feed_bytes", side_effect=zipfile.BadZipFile):
        gtfs_server._build("https://example.test/x.zip", "S1", tt_path, err_path)
    assert json.loads(tt_path.read_text())["stop_name"] == "Canal St"
    assert "GTFS zip" in gtfs_server._read_error(err_path)[0]


def test_direction_filter_keeps_one_direction(client: FlaskClient) -> None:
    opts = '{"gtfs_url":"https://example.test/gtfs.zip","stop_id":"S1","direction":"0"}'
    with patch(_OPEN, return_value=_FakeResp(_feed())):
        resp = client.get(f"/_test/render?plugin=gtfs&size=md&opts={opts}")
    assert resp.status_code == 200
    # Both fixture trips are direction 0, so both survive; direction 1 empties
    # the board, which is the half that proves the filter is wired up.
    assert "Inwood-207 St" in resp.get_data(as_text=True)

    opts = '{"gtfs_url":"https://example.test/gtfs.zip","stop_id":"S1","direction":"1"}'
    with patch(_OPEN, return_value=_FakeResp(_feed())):
        resp = client.get(f"/_test/render?plugin=gtfs&size=md&opts={opts}")
    assert "Inwood-207 St" not in resp.get_data(as_text=True)


def test_preset_supplies_all_three_urls() -> None:
    """A preset overrides the URL fields, so a half-filled cell can't end up
    pairing one agency's timetable with another's realtime feed."""
    gtfs, rt, alerts = gtfs_server._preset_urls("mta_ace", "https://stale.example/old.zip", "", "")
    assert gtfs.endswith("gtfs_subway.zip")
    assert rt.endswith("nyct%2Fgtfs-ace")
    assert "subway-alerts" in alerts

    # "custom" leaves the user's own URLs untouched.
    assert gtfs_server._preset_urls("custom", "https://x.test/a.zip", "b", "c") == (
        "https://x.test/a.zip",
        "b",
        "c",
    )


def test_every_preset_is_offered_in_the_manifest() -> None:
    """The dropdown and the resolver must not drift apart."""
    manifest = json.loads((_PLUGIN / "plugin.json").read_text())
    option = next(o for o in manifest["cell_options"] if o["name"] == "preset")
    offered = {c["value"] for c in option["choices"]}
    assert offered == {"custom", *gtfs_server.PRESETS}


def test_stop_index_labels_carry_the_calling_routes() -> None:
    """A dropdown of eighteen identical "Canal St" rows would be no better
    than the text box, so labels have to disambiguate."""
    url = "https://example.test/gtfs.zip"
    index = gtfs_server._stop_index(_feed(), url, "Test Transit")
    # The platform (S1N) folds into its parent station (S1), one row.
    assert len(index) == 1
    assert index[0]["label"] == "Test Transit · Canal St (A) · S1"
    assert index[0]["value"].endswith(":S1")


def test_stop_choice_is_ignored_when_the_feed_changed() -> None:
    """The value carries a feed digest so a stop picked against one feed
    isn't silently looked up in another."""
    url = "https://example.test/gtfs.zip"
    value = gtfs_server._stop_index(_feed(), url)[0]["value"]
    assert gtfs_server._stop_from_choice(value, url) == "S1"
    assert gtfs_server._stop_from_choice(value, "https://other.test/gtfs.zip") == ""
    # A stop value saved by the build that encoded direction still resolves.
    assert gtfs_server._stop_from_choice(f"{value}:1", url) == "S1"


def test_stop_dropdown_falls_back_to_the_text_field(client: FlaskClient) -> None:
    """A pick from another feed must not shadow the Stop ID field."""
    opts = (
        '{"gtfs_url":"https://example.test/gtfs.zip","stop_id":"S1","stop":"deadbeefdeadbeef:S99"}'
    )
    with patch(_OPEN, return_value=_FakeResp(_feed())):
        resp = client.get(f"/_test/render?plugin=gtfs&size=md&opts={opts}")
    assert resp.status_code == 200
    assert "Inwood-207 St" in resp.get_data(as_text=True)


def test_rendering_a_preset_warms_the_stop_index(tmp_path: Path) -> None:
    """The editor shouldn't be what discovers the index is cold, so a render
    on a preset feed kicks the build off the zip it downloads anyway."""
    preset_url = gtfs_server.PRESETS["bart"]["gtfs_url"]
    with patch.object(gtfs_server, "_start_build") as started:
        gtfs_server._warm_stop_index(preset_url, tmp_path)
    assert started.called

    # A custom feed isn't in the dropdown, so there's nothing to warm.
    with patch.object(gtfs_server, "_start_build") as started:
        gtfs_server._warm_stop_index("https://custom.test/gtfs.zip", tmp_path)
    assert not started.called


def test_choices_only_answers_stops(client: FlaskClient) -> None:
    with client.application.app_context():
        assert gtfs_server.choices("boards") == []
        # Cold cache: a sentinel, never an inline download.
        with patch.object(gtfs_server, "_start_build") as started:
            out = gtfs_server.choices("stops")
        assert started.called
        assert any("loading" in c["label"] for c in out)


def test_preset_renders_without_any_url_fields(client: FlaskClient) -> None:
    opts = '{"preset":"mta_ace","stop_id":"S1"}'
    with patch(_OPEN, return_value=_FakeResp(_feed())):
        resp = client.get(f"/_test/render?plugin=gtfs&size=md&opts={opts}")
    assert resp.status_code == 200
    assert "Inwood-207 St" in resp.get_data(as_text=True)


# ----------------------------------------------------------------------
# URL safety
# ----------------------------------------------------------------------


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.test/f.zip", "/etc/passwd"])
def test_download_refuses_non_http_schemes(url: str) -> None:
    """Every URL here is user-supplied; urllib's default opener would happily
    read file:// off the Tesserae host and parse it."""
    with pytest.raises(ValueError, match="unsupported URL scheme"):
        gtfs_server._download(url, 1)


def test_file_url_in_the_admin_page_is_an_error_not_a_read(client: FlaskClient) -> None:
    resp = client.get("/plugins/gtfs/?feed_url=file:///etc/passwd")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "root:" not in body
    assert "Couldn&#39;t read the feed" in body


def test_widget_preview_resolves_dynamic_choices(client: FlaskClient) -> None:
    """``/_test/preview`` read the raw manifest, so every ``choices_from``
    dropdown rendered empty there — for any widget, not just this one."""
    app = client.application
    plugin_dir = app.config["PLUGIN_REGISTRY"].get("gtfs").data_dir
    plugin_dir.mkdir(parents=True, exist_ok=True)
    index = gtfs_server._stop_index(_feed(), "https://example.test/gtfs.zip", "Test Transit")
    for url in {spec["gtfs_url"] for spec in gtfs_server.PRESETS.values()}:
        digest = hashlib.sha1(url.encode()).hexdigest()[:16]
        (plugin_dir / f"stops3_{digest}.json").write_text(json.dumps({"stops": index}))

    opts = json.dumps({"preset": "mta_ace", "stop": "", "stop_id": ""})
    html = client.get(f"/_test/preview?widget=gtfs&opts={quote(opts)}").get_data(as_text=True)
    select = re.search(r'<select id="opt-stop".*?</select>', html, re.S)
    assert select is not None
    assert "Test Transit · Canal St" in select.group(0)
    # The preset's URLs show up filled and locked here too.
    tag = re.search(r'<input id="opt-rt_url".*?>', html, re.S)
    assert tag is not None
    assert "readonly" in tag.group(0)
    assert "gtfs-ace" in tag.group(0)


def test_cell_editor_shows_stops_and_locks_preset_urls(client: FlaskClient) -> None:
    """End-to-end through the real editor template: the Stop dropdown carries
    the index, and a chosen preset fills + locks the three URL fields."""
    from app.panel import Panel
    from app.state.page_store import Cell, Page

    app = client.application
    # Seed each preset feed's stop index in the plugin's data dir. Patching
    # the module object this test imported wouldn't work — the app loads its
    # own copy — and without a seed the editor would build the index for
    # real, i.e. hit the network from a unit test.
    plugin_dir = app.config["PLUGIN_REGISTRY"].get("gtfs").data_dir
    plugin_dir.mkdir(parents=True, exist_ok=True)
    index = gtfs_server._stop_index(_feed(), "https://example.test/gtfs.zip", "Test Transit")
    for url in {spec["gtfs_url"] for spec in gtfs_server.PRESETS.values()}:
        digest = hashlib.sha1(url.encode()).hexdigest()[:16]
        (plugin_dir / f"stops3_{digest}.json").write_text(json.dumps({"stops": index}))

    app.config["PAGE_STORE"].save(
        Page(
            id="gtfs-editor-test",
            name="t",
            panel=Panel(w=800, h=480),
            cells=[
                Cell(
                    id="c1",
                    plugin="gtfs",
                    x=0,
                    y=0,
                    w=800,
                    h=480,
                    options={"preset": "mta_ace", "stop": index[0]["value"]},
                )
            ],
        )
    )
    html = client.get("/pages/gtfs-editor-test").get_data(as_text=True)

    select = re.search(r'<select[^>]*name="opt_stop".*?</select>', html, re.S)
    assert select is not None
    assert "Test Transit · Canal St" in select.group(0)

    for field, expected in (
        ("opt_gtfs_url", "rrgtfsfeeds"),
        ("opt_rt_url", "gtfs-ace"),
        ("opt_alerts_url", "subway-alerts"),
    ):
        tag = re.search(rf'<input[^>]*name="{field}"[^>]*>', html, re.S)
        assert tag is not None, field
        assert "readonly" in tag.group(0), field
        assert expected in tag.group(0), field


# ----------------------------------------------------------------------
# Admin page
# ----------------------------------------------------------------------


def test_stop_finder_searches_the_feed(client: FlaskClient) -> None:
    with patch(_OPEN, return_value=_FakeResp(_feed())):
        resp = client.get("/plugins/gtfs/?feed_url=https://example.test/gtfs.zip&q=canal")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "S1N" in body
    assert "Canal St North" in body


def test_stop_finder_inspect_names_each_direction(client: FlaskClient) -> None:
    with patch(_OPEN, return_value=_FakeResp(_feed())):
        resp = client.get("/plugins/gtfs/?feed_url=https://example.test/gtfs.zip&inspect=S1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The whole point of inspect: which headsigns sit behind direction_id 0.
    assert "Inwood-207 St" in body
    assert "Eighth Avenue Express" in body or "A" in body


def test_admin_page_is_not_an_open_url_fetcher(client: FlaskClient) -> None:
    """The finder turns a query arg into an outbound request, so an
    unauthenticated caller is held to the preset feeds."""
    # The suite runs with TESTING on, where the host installs no auth gate at
    # all; flip it off so this exercises a real deployment's path.
    client.application.config["TESTING"] = False
    try:
        with patch(_OPEN, return_value=_FakeResp(_feed())) as opened:
            resp = client.get("/plugins/gtfs/?feed_url=https://attacker.test/x.zip")
        assert resp.status_code == 200
        assert "Sign in to search a feed" in resp.get_data(as_text=True)
        assert not opened.called  # no request was made at all

        # A preset feed is a fixed, known host, so it stays usable.
        with patch(_OPEN, return_value=_FakeResp(_feed())) as opened:
            client.get(
                "/plugins/gtfs/?feed_url=" + quote(gtfs_server.PRESETS["bart"]["gtfs_url"], safe="")
            )
        assert opened.called
    finally:
        client.application.config["TESTING"] = True


def test_stop_finder_reports_a_bad_feed(client: FlaskClient) -> None:
    with patch(_OPEN, return_value=_FakeResp(b"not a zip")):
        resp = client.get("/plugins/gtfs/?feed_url=https://example.test/nope.zip")
    assert resp.status_code == 200
    assert "didn&#39;t return a GTFS zip" in resp.get_data(as_text=True)


def test_padded_header_row_still_finds_the_stop() -> None:
    """A stops.txt header padded with spaces still resolves the stop and
    its child platforms; the names are trimmed before rows are read."""
    table = gtfs_server._distil(_feed(pad_header=True), "S1")
    assert "S1N" in table["stop_ids"]
    assert table["stop_name"] == "Canal St"
