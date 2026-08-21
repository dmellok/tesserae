"""Rooms as a generator (#90).

The property under test throughout is that a room produces an *ordinary*
page. If these ever start asserting that something renders through a
room, the design has drifted and the feature has stopped being cheap to
delete.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import rooms
from app.state.page_store import Page, PageStore
from app.state.room_model import Room, page_id_for
from app.state.room_store import RoomStore


@pytest.fixture
def store(tmp_path: Path) -> RoomStore:
    return RoomStore(tmp_path / "rooms.json")


@pytest.fixture
def pages(tmp_path: Path) -> PageStore:
    return PageStore(tmp_path / "pages.json")


def _room(**kw: object) -> Room:
    base: dict[str, object] = {"id": "kestrel", "name": "Kestrel", "feed_id": "cal_kestrel"}
    base.update(kw)
    return Room(**base)  # type: ignore[arg-type]


# -- model ---------------------------------------------------------------


def test_room_id_rejects_shapes_that_would_break_a_page_id() -> None:
    for bad in ("Kestrel", "kes trel", "", "kestrel/../etc", "-lead"):
        with pytest.raises(ValueError):
            _room(id=bad)


def test_room_name_is_required() -> None:
    with pytest.raises(ValueError):
        _room(name="   ")


def test_book_url_must_be_http() -> None:
    with pytest.raises(ValueError):
        _room(book_url="ftp://example.com/book")
    assert _room(booking_mode="endpoint", book_url="https://x.example/book").book_url


def test_page_id_is_namespaced() -> None:
    """A room must not be able to claim a hand-made page's id."""
    assert page_id_for("kestrel") == "room_kestrel"
    assert _room().resolved_page_id() == "room_kestrel"


# -- store ---------------------------------------------------------------


def test_upsert_and_get_round_trip(store: RoomStore) -> None:
    store.upsert(_room())
    got = store.get("kestrel")
    assert got is not None and got.name == "Kestrel"


def test_upsert_replaces_rather_than_duplicates(store: RoomStore) -> None:
    store.upsert(_room())
    store.upsert(_room(name="Kestrel Room"))
    assert [r.name for r in store.all()] == ["Kestrel Room"]


def test_delete_reports_whether_it_removed_anything(store: RoomStore) -> None:
    store.upsert(_room())
    assert store.delete("kestrel") is True
    assert store.delete("kestrel") is False


def test_a_malformed_row_does_not_take_the_list_down(store: RoomStore, tmp_path: Path) -> None:
    """One bad row must not blank the Rooms page."""
    (tmp_path / "rooms.json").write_text(
        '[{"id": "ok", "name": "Fine"}, {"id": "!!bad", "name": "Broken"}]', encoding="utf-8"
    )
    assert [r.id for r in store.all()] == ["ok"]


def test_unreadable_store_reads_as_empty(store: RoomStore, tmp_path: Path) -> None:
    (tmp_path / "rooms.json").write_text("not json", encoding="utf-8")
    assert store.all() == []


# -- generation ----------------------------------------------------------


def test_generated_page_is_an_ordinary_page(pages: PageStore) -> None:
    """The whole point: no room-specific page kind, no runtime hook."""
    page = rooms.sync(_room(device_ids=["door"]), page_store=pages)
    assert page.layout_kind == "grid"
    assert page.device_ids == ["door"]
    assert len(page.cells) == 1
    assert page.cells[0].plugin == "room_status"
    assert pages.get("room_kestrel") is not None


def test_generation_is_deterministic(pages: PageStore) -> None:
    a = rooms.build_page(_room())
    b = rooms.build_page(_room())
    assert a.model_dump() == b.model_dump()


def test_widget_options_carry_the_rooms_identity(pages: PageStore) -> None:
    page = rooms.sync(_room(location_filter="Kestrel"), page_store=pages)
    opts = page.cells[0].options
    assert opts["feed_id"] == "cal_kestrel"
    assert opts["room_name"] == "Kestrel"
    assert opts["location_filter"] == "Kestrel"


def test_layout_stays_on_auto(pages: PageStore) -> None:
    """One room definition has to serve a door panel and a wall tile, so
    the widget picks by cell shape rather than the operator choosing."""
    assert rooms.build_page(_room()).cells[0].options["layout"] == "auto"


def test_titles_are_off_in_generated_pages(pages: PageStore) -> None:
    """A corridor panel publishes whatever it renders."""
    assert rooms.build_page(_room()).cells[0].options["show_titles"] is False


def test_book_url_becomes_a_cell_tap_action(pages: PageStore) -> None:
    """Booking is dispatched by the cell, because the provenance gate
    rejects a side-effecting action originating in widget markup."""
    page = rooms.build_page(_room(booking_mode="endpoint", book_url="https://x.example/book"))
    assert page.cells[0].on_tap == "webhook_refresh:https://x.example/book?room=kestrel"
    assert page.cells[0].options["show_book_action"] is True


def test_book_action_names_the_room(pages: PageStore) -> None:
    """The webhook payload reports device_id, and a page id only when the
    page is rotation-bound. A room panel is bound directly, so without
    this a receiver serving several rooms cannot tell which was tapped."""
    assert rooms.book_action(_room()) is None
    assert rooms.book_action(_room(booking_mode="endpoint", book_url="https://x.example/b")) == (
        "webhook_refresh:https://x.example/b?room=kestrel"
    )


def test_book_action_preserves_an_existing_query_string(pages: PageStore) -> None:
    action = rooms.book_action(
        _room(booking_mode="endpoint", book_url="https://x.example/b?src=panel")
    )
    assert action == "webhook_refresh:https://x.example/b?src=panel&room=kestrel"


def test_book_action_survives_the_action_parser(pages: PageStore) -> None:
    """parse_action_spec splits on the first colon, so a URL with a scheme
    and a query string has to come back intact."""
    from app.button_actions import parse_action_spec

    action = rooms.book_action(
        _room(booking_mode="endpoint", book_url="https://x.example/b?src=panel")
    )
    assert action is not None
    name, arg = parse_action_spec(action)
    assert name == "webhook_refresh"
    assert arg == "https://x.example/b?src=panel&room=kestrel"


def test_no_book_url_means_no_tap_and_no_button(pages: PageStore) -> None:
    page = rooms.build_page(_room())
    assert page.cells[0].on_tap is None
    assert page.cells[0].options["show_book_action"] is False


def test_rename_updates_the_page_in_place(pages: PageStore) -> None:
    rooms.sync(_room(), page_store=pages)
    rooms.sync(_room(name="Kestrel Boardroom"), page_store=pages)
    assert len(pages.list()) == 1
    page = pages.get("room_kestrel")
    assert page is not None
    assert page.name == "Kestrel Boardroom"
    assert page.cells[0].options["room_name"] == "Kestrel Boardroom"


def test_sync_preserves_styling_rooms_does_not_own(pages: PageStore) -> None:
    """Someone who restyles a room page to match their office must not
    have it reverted by an unrelated rename."""
    rooms.sync(_room(), page_store=pages)
    page = pages.get("room_kestrel")
    assert page is not None
    page.theme = "dark"
    page.cells[0].font = "inter"
    pages.save(page)

    rooms.sync(_room(name="Renamed"), page_store=pages)
    after = pages.get("room_kestrel")
    assert after is not None
    assert after.theme == "dark"
    assert after.cells[0].font == "inter"
    assert after.name == "Renamed"


def test_sync_all_skips_disabled_rooms(pages: PageStore) -> None:
    written = rooms.sync_all(
        [_room(), _room(id="osprey", name="Osprey", enabled=False)], page_store=pages
    )
    assert written == 1
    assert pages.get("room_osprey") is None


def test_sync_all_keeps_going_past_a_bad_room(pages: PageStore) -> None:
    """One broken room must not stop the rest from being generated."""

    class _Exploding(Room):
        def resolved_page_id(self) -> str:
            raise RuntimeError("boom")

    bad = _Exploding(id="bad", name="Bad")
    written = rooms.sync_all([bad, _room()], page_store=pages)
    assert written == 1
    assert pages.get("room_kestrel") is not None


# -- deletion ------------------------------------------------------------


def test_delete_page_removes_the_generated_page(pages: PageStore) -> None:
    rooms.sync(_room(), page_store=pages)
    assert rooms.delete_page(_room(), page_store=pages) is True
    assert pages.get("room_kestrel") is None


def test_delete_page_refuses_a_page_it_did_not_generate(pages: PageStore) -> None:
    """A room pointed at a hand-made page must not delete it."""
    pages.save(Page(id="handmade", name="Mine"))
    room = _room(page_id="handmade")
    assert rooms.delete_page(room, page_store=pages) is False
    assert pages.get("handmade") is not None


# -- routes --------------------------------------------------------------


@pytest.fixture
def app_client(tmp_path: Path):
    from app.main import REPO_ROOT, create_app

    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    client = a.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    return a, client


def test_rooms_page_renders_with_no_rooms(app_client) -> None:
    """With no feeds configured the page teaches the dependency instead of
    offering a form that could not work."""
    _app, client = app_client
    resp = client.get("/settings/rooms")
    assert resp.status_code == 200
    assert b"Rooms need a calendar to read" in resp.data


def test_creating_a_room_generates_its_dashboard(app_client) -> None:
    app, client = app_client
    resp = client.post(
        "/settings/rooms",
        data={"name": "Kestrel", "feed_id": "cal_a", "enabled": "on"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    store = app.config["ROOM_STORE"]
    created = store.all()
    assert [r.name for r in created] == ["Kestrel"]
    assert created[0].id == "kestrel"
    page = app.config["PAGE_STORE"].get("room_kestrel")
    assert page is not None
    assert page.cells[0].plugin == "room_status"


def test_room_ids_are_slugified_and_deduped(app_client) -> None:
    app, client = app_client
    for _ in range(2):
        client.post("/settings/rooms", data={"name": "Big Room!", "enabled": "on"})
    ids = sorted(r.id for r in app.config["ROOM_STORE"].all())
    assert ids == ["big-room", "big-room-2"]


def test_updating_a_room_regenerates_the_page(app_client) -> None:
    app, client = app_client
    client.post("/settings/rooms", data={"name": "Kestrel", "enabled": "on"})
    client.post("/settings/rooms/kestrel", data={"name": "Kestrel Boardroom", "enabled": "on"})
    page = app.config["PAGE_STORE"].get("room_kestrel")
    assert page is not None and page.name == "Kestrel Boardroom"


def test_a_bad_book_url_is_rejected_without_500(app_client) -> None:
    app, client = app_client
    resp = client.post(
        "/settings/rooms",
        data={"name": "Kestrel", "book_url": "ftp://nope", "enabled": "on"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert app.config["ROOM_STORE"].all() == []


def test_deleting_a_room_can_take_its_page(app_client) -> None:
    app, client = app_client
    client.post("/settings/rooms", data={"name": "Kestrel", "enabled": "on"})
    client.post("/settings/rooms/kestrel/delete", data={"delete_page": "on"})
    assert app.config["ROOM_STORE"].all() == []
    assert app.config["PAGE_STORE"].get("room_kestrel") is None


def test_deleting_a_room_can_keep_its_page(app_client) -> None:
    """Someone who built on the generated dashboard shouldn't lose it."""
    app, client = app_client
    client.post("/settings/rooms", data={"name": "Kestrel", "enabled": "on"})
    client.post("/settings/rooms/kestrel/delete", data={})
    assert app.config["ROOM_STORE"].all() == []
    assert app.config["PAGE_STORE"].get("room_kestrel") is not None


def test_resync_rebuilds_a_hand_edited_dashboard(app_client) -> None:
    app, client = app_client
    client.post("/settings/rooms", data={"name": "Kestrel", "enabled": "on"})
    pages_store = app.config["PAGE_STORE"]
    page = pages_store.get("room_kestrel")
    assert page is not None
    page.cells = []
    pages_store.save(page)

    client.post("/settings/rooms/kestrel/resync", data={})
    rebuilt = pages_store.get("room_kestrel")
    assert rebuilt is not None and len(rebuilt.cells) == 1


def test_unknown_room_does_not_500(app_client) -> None:
    _app, client = app_client
    for path in (
        "/settings/rooms/nope",
        "/settings/rooms/nope/delete",
        "/settings/rooms/nope/resync",
    ):
        assert client.post(path, data={}, follow_redirects=True).status_code == 200


# -- board (L2) ----------------------------------------------------------


def test_board_has_one_row_per_enabled_room(pages: PageStore) -> None:
    page = rooms.build_board_page(
        [
            _room(),
            _room(id="osprey", name="Osprey"),
            _room(id="falcon", name="Falcon", enabled=False),
        ]
    )
    assert [c.options["room_name"] for c in page.cells] == ["Kestrel", "Osprey"]


def test_board_rows_tile_the_panel_without_a_seam(pages: PageStore) -> None:
    """Integer row height leaves a remainder; the last row absorbs it so
    the board fills the panel exactly."""
    roomset = [_room(id=f"r{i}", name=f"Room {i}") for i in range(7)]
    page = rooms.build_board_page(roomset)
    height = page.cells[0].h * 0 + sum(c.h for c in page.cells)
    assert page.cells[0].y == 0
    assert height == 480
    for prev, nxt in zip(page.cells, page.cells[1:], strict=False):
        assert prev.y + prev.h == nxt.y


def test_board_rows_use_the_horizontal_layout(pages: PageStore) -> None:
    """A board row is a strip. Every stacked layout renders the room name
    a few pixels tall and leaves most of the row empty."""
    page = rooms.build_board_page([_room(), _room(id="osprey", name="Osprey")])
    assert {c.options["layout"] for c in page.cells} == {"row"}


def test_board_never_offers_booking(pages: PageStore) -> None:
    """A tap would book whichever room the finger landed on."""
    page = rooms.build_board_page(
        [_room(booking_mode="endpoint", book_url="https://x.example/book")]
    )
    assert page.cells[0].options["show_book_action"] is False
    assert page.cells[0].on_tap is None


def test_board_with_no_enabled_rooms_is_empty_not_broken(pages: PageStore) -> None:
    page = rooms.build_board_page([_room(enabled=False)])
    assert page.cells == []


def test_sync_board_keeps_its_binding_when_not_given_one(pages: PageStore) -> None:
    rooms.sync_board([_room()], page_store=pages, device_ids=["lobby"])
    rooms.sync_board([_room(), _room(id="osprey", name="Osprey")], page_store=pages)
    board = pages.get("room_board")
    assert board is not None
    assert board.device_ids == ["lobby"]
    assert len(board.cells) == 2


def test_board_route_builds_and_binds(app_client) -> None:
    app, client = app_client
    client.post("/settings/rooms", data={"name": "Kestrel", "enabled": "on"})
    client.post("/settings/rooms", data={"name": "Osprey", "enabled": "on"})
    resp = client.post("/settings/rooms/board", data={}, follow_redirects=True)
    assert resp.status_code == 200
    board = app.config["PAGE_STORE"].get("room_board")
    assert board is not None and len(board.cells) == 2


# -- CalDAV booking (L4) -------------------------------------------------


class _FakeCore:
    """Stands in for the calendar_core plugin, exposing the three private
    helpers rooms.book_now leans on."""

    def __init__(self, feeds: list[dict[str, object]], opener: object) -> None:
        self._feeds = feeds
        self._opener = opener
        self.built_for: list[str] = []

        class _Module:
            @staticmethod
            def _load_feeds(_dd: object) -> dict[str, object]:
                return {"feeds": feeds}

            @staticmethod
            def _feed_auth(feed: dict[str, object]) -> dict[str, object]:
                return {"mode": "basic", "username": "u", "password": "p"}

            @staticmethod
            def _build_opener(url: str, _auth: object) -> object:
                return opener

        self.server_module = _Module()
        self.data_dir = "/tmp/cal"


class _CapturingOpener:
    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.requests: list[object] = []

    def open(self, request: object, timeout: int = 0) -> object:
        self.requests.append(request)

        class _R:
            status = self.status

            def getcode(self_inner) -> int:
                return 201

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return None

        return _R()


def _feed(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "cal_kestrel",
        "name": "Kestrel",
        "url": "https://dav.example/cal/kestrel/",
        "enabled": True,
    }
    base.update(kw)
    return base


def test_caldav_booking_writes_into_the_rooms_collection() -> None:
    opener = _CapturingOpener()
    core = _FakeCore([_feed()], opener)
    url = rooms.book_now(_room(booking_mode="caldav"), core=core)
    assert url.startswith("https://dav.example/cal/kestrel/")
    assert url.endswith(".ics")
    assert opener.requests and opener.requests[0].get_method() == "PUT"


def test_caldav_booking_uses_the_rooms_length_and_name() -> None:
    from datetime import UTC, datetime

    opener = _CapturingOpener()
    core = _FakeCore([_feed()], opener)
    now = datetime(2026, 8, 21, 14, 0, tzinfo=UTC)
    rooms.book_now(_room(booking_mode="caldav", book_minutes=45), core=core, now=now)
    body = opener.requests[0].data.decode("utf-8")
    assert "DTSTART:20260821T140000Z" in body
    assert "DTEND:20260821T144500Z" in body
    assert "LOCATION:Kestrel" in body


def test_caldav_booking_without_a_feed_raises() -> None:
    from app.caldav_write import CalDavWriteError

    core = _FakeCore([], _CapturingOpener())
    with pytest.raises(CalDavWriteError, match="no usable calendar feed"):
        rooms.book_now(_room(booking_mode="caldav"), core=core)


def test_caldav_booking_without_a_url_raises() -> None:
    from app.caldav_write import CalDavWriteError

    core = _FakeCore([_feed(url="")], _CapturingOpener())
    with pytest.raises(CalDavWriteError):
        rooms.book_now(_room(booking_mode="caldav"), core=core)


def test_feed_lookup_falls_back_to_the_first_enabled_feed() -> None:
    core = _FakeCore([_feed(id="other"), _feed(id="second")], _CapturingOpener())
    found = rooms.feed_for(_room(feed_id=""), core=core)
    assert found is not None and found["id"] == "other"


def test_feed_lookup_skips_disabled_feeds() -> None:
    core = _FakeCore([_feed(id="off", enabled=False), _feed(id="on")], _CapturingOpener())
    found = rooms.feed_for(_room(feed_id=""), core=core)
    assert found is not None and found["id"] == "on"


# -- the action a CalDAV room generates ----------------------------------


def test_caldav_room_taps_book_internally(pages: PageStore) -> None:
    """There is nothing outbound to point at, so the action names the
    room rather than a URL."""
    assert rooms.book_action(_room(booking_mode="caldav")) == "room_book:kestrel"


def test_caldav_booking_wins_over_a_book_url(pages: PageStore) -> None:
    room = _room(booking_mode="caldav", book_url="https://x.example/b")
    assert rooms.book_action(room) == "room_book:kestrel"


def test_caldav_room_shows_the_book_button(pages: PageStore) -> None:
    page = rooms.build_page(_room(booking_mode="caldav"))
    assert page.cells[0].options["show_book_action"] is True
    assert page.cells[0].on_tap == "room_book:kestrel"


def test_booking_length_is_bounded() -> None:
    """A mistyped length must not book a room for a year."""
    with pytest.raises(ValueError):
        _room(book_minutes=0)
    with pytest.raises(ValueError):
        _room(book_minutes=10_000)


def test_room_book_is_side_effecting() -> None:
    """It writes to a calendar, so widget markup must not be able to aim
    it any more than it can aim a webhook."""
    from app.touch_regions import SIDE_EFFECTING_ACTIONS, is_side_effecting

    assert "room_book" in SIDE_EFFECTING_ACTIONS
    assert is_side_effecting("room_book:kestrel") is True


def test_room_book_requires_a_room_id() -> None:
    from app.button_actions import ButtonActionError, dispatch
    from tests.test_button_actions import _ctx  # type: ignore[import-not-found]

    with pytest.raises(ButtonActionError):
        dispatch("room_book", _ctx())


def test_caldav_booking_can_be_set_from_the_ui(app_client) -> None:
    app, client = app_client
    client.post("/settings/rooms", data={"name": "Kestrel", "enabled": "on"})
    client.post(
        "/settings/rooms/kestrel",
        data={
            "name": "Kestrel",
            "enabled_present": "1",
            "enabled": "on",
            "booking_mode": "caldav",
            "book_minutes": "45",
        },
    )
    room = app.config["ROOM_STORE"].get("kestrel")
    assert room is not None and room.booking_mode == "caldav" and room.book_minutes == 45
    page = app.config["PAGE_STORE"].get("room_kestrel")
    assert page is not None and page.cells[0].on_tap == "room_book:kestrel"


def test_an_out_of_range_booking_length_is_rejected_without_500(app_client) -> None:
    app, client = app_client
    resp = client.post(
        "/settings/rooms",
        data={"name": "Kestrel", "enabled": "on", "book_minutes": "9999"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert app.config["ROOM_STORE"].all() == []


# -- booking mode migration ----------------------------------------------


def test_legacy_caldav_toggle_becomes_caldav_mode() -> None:
    """Rooms written before booking_mode stored a bool alongside a URL,
    which is exactly the ambiguity the mode replaced. Resolve it the way
    the old dispatch did: the toggle won."""
    room = Room(id="a", name="A", book_caldav=True, book_url="https://x/b")
    assert room.booking_mode == "caldav"


def test_legacy_url_alone_becomes_endpoint_mode() -> None:
    assert Room(id="a", name="A", book_url="https://x/b").booking_mode == "endpoint"


def test_legacy_room_with_neither_is_not_bookable() -> None:
    assert Room(id="a", name="A").booking_mode == "none"


def test_an_explicit_mode_is_never_overridden_by_migration() -> None:
    room = Room(id="a", name="A", booking_mode="none", book_url="https://x/b")
    assert room.booking_mode == "none"
    assert room.is_bookable is False


def test_endpoint_mode_without_a_url_is_not_bookable() -> None:
    """The mode says how, the URL says where. Without one there is
    nothing to POST to, so no button is drawn."""
    room = Room(id="a", name="A", booking_mode="endpoint")
    assert room.is_bookable is False
    assert rooms.book_action(room) is None


# -- row view -------------------------------------------------------------


def _feed_row(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "cal_a",
        "name": "Kestrel calendar",
        "url": "https://dav.example/cal/kestrel/",
        "auth_mode": "basic",
        "username": "rooms",
        "enabled": True,
    }
    base.update(kw)
    return base


def test_row_view_resolves_the_named_feed(pages: PageStore) -> None:
    view = rooms.row_view(_room(feed_id="cal_a"), feeds=[_feed_row()], page_store=pages)
    assert view["feed_name"] == "Kestrel calendar"
    assert view["feed_missing"] is False


def test_row_view_flags_a_feed_that_no_longer_exists(pages: PageStore) -> None:
    """Deleting a feed shouldn't make a room silently read the wrong one."""
    view = rooms.row_view(_room(feed_id="gone"), feeds=[_feed_row()], page_store=pages)
    assert view["feed_missing"] is True


def test_row_view_marks_an_implicit_feed(pages: PageStore) -> None:
    view = rooms.row_view(_room(feed_id=""), feeds=[_feed_row()], page_store=pages)
    assert view["feed_implicit"] is True
    assert view["feed_name"] == "Kestrel calendar"


def test_row_view_reports_the_dashboard_build_state(pages: PageStore) -> None:
    room = _room()
    assert rooms.row_view(room, feeds=[], page_store=pages)["dashboard_exists"] is False
    rooms.sync(room, page_store=pages)
    assert rooms.row_view(room, feeds=[], page_store=pages)["dashboard_exists"] is True


def test_row_view_names_panels_and_flags_missing_ones(pages: PageStore) -> None:
    class _Reg:
        def __init__(self) -> None:
            self.devices = {"door": type("D", (), {"display_name": "Kestrel door"})()}

    view = rooms.row_view(
        _room(device_ids=["door", "ghost"]), feeds=[], devices=_Reg(), page_store=pages
    )
    assert [p["name"] for p in view["panels"]] == ["Kestrel door", "ghost"]
    assert [p["missing"] for p in view["panels"]] == [False, True]


# -- caldav capability ----------------------------------------------------


def test_a_credentialled_caldav_feed_can_write() -> None:
    assert rooms.feed_can_write(_feed_row()) is True


def test_a_feed_without_credentials_cannot_write() -> None:
    """An ICS export URL is read-only however good the credentials are,
    and a collection with no credentials cannot authenticate."""
    assert rooms.feed_can_write(_feed_row(auth_mode="none", username="")) is False


def test_a_feed_with_no_url_cannot_write() -> None:
    assert rooms.feed_can_write(_feed_row(url="")) is False


def test_no_feed_cannot_write() -> None:
    assert rooms.feed_can_write(None) is False


# -- partial form updates -------------------------------------------------


def test_saving_one_section_does_not_clear_the_others(app_client) -> None:
    """The add form carries two fields and the row editor carries the
    rest. A post that doesn't include a section must leave it alone,
    or editing the name would silently unbind every panel."""
    app, client = app_client
    client.post("/settings/rooms", data={"name": "Kestrel", "enabled": "on"})
    client.post(
        "/settings/rooms/kestrel",
        data={
            "name": "Kestrel",
            "enabled_present": "1",
            "enabled": "on",
            "booking_mode": "endpoint",
            "book_url": "https://x.example/book",
            "book_minutes": "45",
        },
    )
    # A later post that omits booking entirely.
    client.post("/settings/rooms/kestrel", data={"name": "Kestrel Boardroom"})
    room = app.config["ROOM_STORE"].get("kestrel")
    assert room is not None
    assert room.name == "Kestrel Boardroom"
    assert room.booking_mode == "endpoint"
    assert room.book_url == "https://x.example/book"
    assert room.book_minutes == 45


def test_an_unticked_enabled_box_still_disables(app_client) -> None:
    """A checkbox posts nothing when unticked, so the form declares its
    own presence; without that, unticking could never be saved."""
    app, client = app_client
    client.post("/settings/rooms", data={"name": "Kestrel", "enabled": "on"})
    client.post("/settings/rooms/kestrel", data={"name": "Kestrel", "enabled_present": "1"})
    room = app.config["ROOM_STORE"].get("kestrel")
    assert room is not None and room.enabled is False


def test_an_unknown_booking_mode_falls_back_rather_than_500(app_client) -> None:
    app, client = app_client
    client.post("/settings/rooms", data={"name": "Kestrel", "enabled": "on"})
    resp = client.post(
        "/settings/rooms/kestrel",
        data={"name": "Kestrel", "booking_mode": "nonsense"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    room = app.config["ROOM_STORE"].get("kestrel")
    assert room is not None and room.booking_mode == "none"
