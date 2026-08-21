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
    assert _room(book_url="https://x.example/book").book_url


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
    page = rooms.build_page(_room(book_url="https://x.example/book"))
    assert page.cells[0].on_tap == "webhook_refresh:https://x.example/book"
    assert page.cells[0].options["show_book_action"] is True


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
    _app, client = app_client
    resp = client.get("/settings/rooms")
    assert resp.status_code == 200
    assert b"No rooms yet" in resp.data


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
