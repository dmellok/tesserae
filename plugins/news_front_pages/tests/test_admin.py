"""The Newspapers admin page and the widget's own config.json: storage,
the carry-over from settings.json, the plain-form edits (save, add,
remove, move, switch off) and their checks, and Verify without scripts."""

from __future__ import annotations

import html
import json
import re
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient

from plugins.news_front_pages import server

PAPERS = "/plugins/news_front_pages/papers"
PAGE = "/plugins/news_front_pages/"


def _dir(app: Flask) -> Path:
    return Path(app.config["PLUGIN_REGISTRY"].get("news_front_pages").data_dir)


def _module(app: Flask) -> Any:
    return app.config["PLUGIN_REGISTRY"].get("news_front_pages").server_module


def _saved(app: Flask) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((_dir(app) / "config.json").read_text())
    return data


def _form(rows: list[dict[str, Any]], op: str = "save", **extra: Any) -> dict[str, Any]:
    """The page's form as a browser posts it: a switched-off row sends no
    ``enabled``."""
    data: dict[str, Any] = {"count": str(len(rows)), "op": op, "small_screens": "top"}
    for i, row in enumerate(rows):
        data[f"row-{i}-code"] = row.get("code", "")
        data[f"row-{i}-css"] = row.get("css", "")
        if row.get("enabled", True):
            data[f"row-{i}-enabled"] = "on"
    data.update(extra)
    return data


def _codes(app: Flask) -> list[str]:
    return [row["code"] for row in _saved(app)["papers"]]


def _flashes(client: FlaskClient) -> list[str]:
    with client.session_transaction() as sess:
        return [message for _, message in sess.get("_flashes", [])]


DEFAULTS = [dict(row) for row in server.DEFAULT_PAPERS]


# ----- storage ---------------------------------------------------------------


def test_load_config_has_defaults_and_writes_nothing_without_an_app(tmp_path: Path) -> None:
    config = server.load_config(tmp_path)
    assert config == {"papers": server.DEFAULT_PAPERS, "small_screens": "top"}
    assert not (tmp_path / "config.json").exists()


@pytest.mark.parametrize(
    ("body", "papers", "fit"),
    [
        ("{not json", server.DEFAULT_PAPERS, "top"),
        ('["a list"]', server.DEFAULT_PAPERS, "top"),
        ('{"small_screens": "whole"}', server.DEFAULT_PAPERS, "whole"),
        ('{"papers": [], "small_screens": "huge"}', [], "top"),
        (
            '{"papers": [{"code": " wsj ", "enabled": "off", "css": 7}, "junk"]}',
            [{"code": "wsj", "enabled": False, "css": ""}],
            "top",
        ),
    ],
    ids=["broken", "not-an-object", "fit-only", "empty-list", "hand-edited"],
)
def test_load_config_reads_what_it_can(
    tmp_path: Path, body: str, papers: list[dict[str, Any]], fit: str
) -> None:
    (tmp_path / "config.json").write_text(body)
    assert server.load_config(tmp_path) == {"papers": papers, "small_screens": fit}


def test_save_config_writes_atomically_and_reads_back(tmp_path: Path) -> None:
    rows = [{"enabled": True, "code": "WSJ", "css": "opacity: 0.9"}]
    saved = server.save_config(tmp_path, {"papers": rows, "small_screens": "whole"})
    assert saved == {"papers": rows, "small_screens": "whole"}
    assert server.load_config(tmp_path) == saved
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]  # no temp file left


def test_a_failed_save_leaves_the_old_config(tmp_path: Path) -> None:
    server.save_config(tmp_path, {"papers": [{"code": "WSJ"}]})
    with patch("os.replace", side_effect=OSError("disk full")), pytest.raises(OSError):
        server.save_config(tmp_path, {"papers": [{"code": "USAT"}]})
    assert [r["code"] for r in server.load_config(tmp_path)["papers"]] == ["WSJ"]
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_concurrent_saves_never_leave_a_torn_file(tmp_path: Path) -> None:
    def save(code: str) -> None:
        for _ in range(20):
            server.save_config(tmp_path, {"papers": [{"code": code, "css": "x" * 500}]})

    threads = [threading.Thread(target=save, args=(c,)) for c in ("AA", "BB", "CC")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    data = json.loads((tmp_path / "config.json").read_text())
    assert data["papers"][0]["code"] in {"AA", "BB", "CC"}


# ----- carried over from settings.json ----------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            # What the settings card's repeater stored.
            [
                {"enabled": True, "code": "WSJ", "css": "transform: translateY(-2%)"},
                {"enabled": False, "code": "TX_HC", "css": ""},
            ],
            [
                {"enabled": True, "code": "WSJ", "css": "transform: translateY(-2%)"},
                {"enabled": False, "code": "TX_HC", "css": ""},
            ],
        ),
        (
            '[{"enabled": true, "code": "USAT", "css": ""}]',
            [{"enabled": True, "code": "USAT", "css": ""}],
        ),
        (
            # The textarea before rows: CODE [trim%] per line, # switches off.
            "NY_NYT 3.5\nWSJ 2\n# USAT",
            [
                {"enabled": True, "code": "NY_NYT", "css": "transform: translateY(-3.5%)"},
                {"enabled": True, "code": "WSJ", "css": "transform: translateY(-2%)"},
                {"enabled": False, "code": "USAT", "css": ""},
            ],
        ),
    ],
    ids=["rows", "json-string", "old-text"],
)
def test_the_first_read_carries_the_old_settings_over(
    app: Flask, client: FlaskClient, value: Any, expected: list[dict[str, Any]]
) -> None:
    store = app.config["SETTINGS_STORE"]
    old = {"papers": value, "small_screens": "whole"}
    store.patch_section("plugins", {"news_front_pages": dict(old)})
    assert not (_dir(app) / "config.json").exists()
    body = client.get(PAGE).get_data(as_text=True)
    assert _saved(app) == {"papers": expected, "small_screens": "whole"}
    assert f'value="{expected[0]["code"]}"' in body
    # Left in place, so an older version still finds them.
    assert store.get_section("plugins")["news_front_pages"] == old


def test_the_carry_over_runs_once(app: Flask, client: FlaskClient) -> None:
    store = app.config["SETTINGS_STORE"]
    store.patch_section("plugins", {"news_front_pages": {"papers": "WSJ"}})
    client.get(PAGE)
    assert _codes(app) == ["WSJ"]
    client.post(PAPERS, data=_form([{"code": "USAT"}]))
    store.patch_section("plugins", {"news_front_pages": {"papers": "TX_HC"}})
    client.get(PAGE)
    assert _codes(app) == ["USAT"]  # config.json wins from now on


def test_with_nothing_to_carry_over_the_defaults_are_written(
    app: Flask, client: FlaskClient
) -> None:
    client.get(PAGE)
    assert _saved(app) == {"papers": server.DEFAULT_PAPERS, "small_screens": "top"}


def test_a_render_carries_the_settings_over_too(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = app.config["SETTINGS_STORE"]
    store.patch_section("plugins", {"news_front_pages": {"papers": [{"code": "TX_HC"}]}})
    queued: list[list[str]] = []
    monkeypatch.setattr(_module(app), "_enqueue", lambda _d, codes, *a, **k: queued.append(codes))
    with app.test_request_context():
        _module(app).fetch({"paper": ""}, {}, ctx={"data_dir": str(_dir(app))})
    assert queued == [["TX_HC"]]  # the carried-over list, not the defaults
    assert _codes(app) == ["TX_HC"]


def test_choices_list_the_saved_papers_first(app: Flask, client: FlaskClient) -> None:
    client.post(PAPERS, data=_form([{"code": "TX_HC"}, {"code": "WSJ", "enabled": False}]))
    with app.app_context(), patch.object(_module(app), "directory", lambda _d: {"WSJ": "WSJ"}):
        rows = _module(app).choices("papers")
    assert [r["value"] for r in rows[:3]] == ["", "TX_HC", "WSJ"]
    assert rows[2]["label"] == "WSJ (WSJ), not in the rotation"


# ----- the page and its forms -------------------------------------------------


def test_the_page_draws_every_paper_as_a_plain_form(client: FlaskClient) -> None:
    body = client.get(PAGE).get_data(as_text=True)
    assert f'action="{PAPERS}"' in body
    assert 'name="count" value="4"' in body
    for i, row in enumerate(server.DEFAULT_PAPERS):
        assert f'name="row-{i}-code" value="{row["code"]}"' in body
    assert body.count('<details class="nx-row-main"') == len(server.DEFAULT_PAPERS)
    # The first submit button (what Enter presses) saves.
    first = re.search(r"<button[^>]*type=\"submit\"[^>]*>", body[body.index('id="nx-papers"') :])
    assert first is not None and 'value="save"' in first.group(0)
    assert 'value="up:0"' in body and 'value="down:3"' in body
    first_up = re.search(r'<button[^>]*value="up:0"[^>]*>', body, re.S)
    assert first_up is not None and "disabled" in first_up.group(0)
    assert 'name="row-3-enabled"' in body and "AZ_ADS: In rotation" in body
    assert 'option value="top" selected' in body


def test_settings_widgets_has_no_card_for_it(client: FlaskClient) -> None:
    general = client.get("/settings/plugins").get_data(as_text=True)
    assert 'href="/plugins/news_front_pages/"' in general
    assert 'id="plugin-news_front_pages"' not in general


def test_save_stores_edits_and_switches(app: Flask, client: FlaskClient) -> None:
    rows = [
        {"code": " ny_nyt ", "css": "filter:  contrast(1.1)\r\nopacity: 0.9 ", "enabled": False},
        {"code": "WSJ"},
    ]
    resp = client.post(PAPERS, data=_form(rows, small_screens="whole"))
    assert resp.status_code == 302
    assert resp.location == "/plugins/news_front_pages/#papers"
    assert _flashes(client) == ["Saved."]
    assert _saved(app) == {
        "papers": [
            {"enabled": False, "code": "NY_NYT", "css": "filter:  contrast(1.1)\nopacity: 0.9"},
            {"enabled": True, "code": "WSJ", "css": ""},
        ],
        "small_screens": "whole",
    }


def test_add_a_paper(app: Flask, client: FlaskClient) -> None:
    form = _form(DEFAULTS, "add", **{"new-code": "tx_hc", "new-css": "opacity: 0.8"})
    resp = client.post(PAPERS, data=form)
    assert resp.location == "/plugins/news_front_pages/#paper-4"
    assert _flashes(client) == ["Added TX_HC."]
    assert _saved(app)["papers"][-1] == {"enabled": False, "code": "TX_HC", "css": "opacity: 0.8"}
    # The switch on the new paper's fields counts.
    form = _form(_saved(app)["papers"], "add", **{"new-code": "CA_LAT", "new-enabled": "on"})
    client.post(PAPERS, data=form)
    assert _saved(app)["papers"][-1]["enabled"] is True


def test_save_with_a_new_code_typed_adds_it(app: Flask, client: FlaskClient) -> None:
    client.post(PAPERS, data=_form(DEFAULTS, **{"new-code": "TX_HC", "new-enabled": "on"}))
    assert _codes(app)[-1] == "TX_HC"
    assert _flashes(client) == ["Saved, and added TX_HC."]


def test_remove_a_paper_says_what_its_css_was(app: Flask, client: FlaskClient) -> None:
    resp = client.post(PAPERS, data=_form(DEFAULTS, "remove:0"))
    assert resp.status_code == 302
    assert _codes(app) == ["WSJ", "USAT", "AZ_ADS"]
    assert _flashes(client) == ["Removed NY_NYT. Its CSS was: transform: translateY(-3.5%)"]


def test_removing_a_bad_row_needs_no_fix_first(app: Flask, client: FlaskClient) -> None:
    rows = [{"code": "WSJ"}, {"code": "not a code!"}]
    assert client.post(PAPERS, data=_form(rows, "remove:1")).status_code == 302
    assert _codes(app) == ["WSJ"]


def test_move_up_and_down(app: Flask, client: FlaskClient) -> None:
    resp = client.post(PAPERS, data=_form(DEFAULTS, "down:0"))
    assert resp.location == "/plugins/news_front_pages/#paper-1"
    assert _codes(app) == ["WSJ", "NY_NYT", "USAT", "AZ_ADS"]
    assert _flashes(client) == ["Moved NY_NYT down."]
    client.post(PAPERS, data=_form(_saved(app)["papers"], "up:3"))
    assert _codes(app) == ["WSJ", "NY_NYT", "AZ_ADS", "USAT"]
    # A move saves the other edits in the form with it.
    rows = _saved(app)["papers"]
    rows[0]["css"] = "opacity: 0.7"
    client.post(PAPERS, data=_form(rows, "up:1"))
    assert _saved(app)["papers"][1] == {"enabled": True, "code": "WSJ", "css": "opacity: 0.7"}


def test_an_open_row_stays_open_where_it_moves(app: Flask, client: FlaskClient) -> None:
    form = _form(DEFAULTS, "down:1", open=["1", "3"])
    resp = client.post(PAPERS, data=form)
    assert resp.location == "/plugins/news_front_pages/?open=2,3#paper-2"
    body = client.get(resp.location).get_data(as_text=True)
    opened = re.findall(r'id="paper-(\d)"[^>]*>\s*<details class="nx-row-main" open', body)
    assert opened == ["2", "3"]


def test_edit_rows_maps_open_rows() -> None:
    rows = [{"code": c} for c in "ABCD"]
    moved, opened, landed = server.edit_rows(rows, "up", 2, {2, 3})
    assert [r["code"] for r in moved] == list("ACBD") and opened == {1, 3} and landed == 1
    gone, opened, landed = server.edit_rows(rows, "remove", 1, {0, 1, 3})
    assert [r["code"] for r in gone] == list("ACD") and opened == {0, 2} and landed is None
    assert server.edit_rows(rows, "up", 0, {0})[1:] == ({0}, 0)
    assert server.edit_rows(rows, "save", -1, {1})[1:] == ({1}, None)
    with pytest.raises(ValueError):
        server.edit_rows(rows, "down", 4, set())


@pytest.mark.parametrize(
    ("rows", "new", "said"),
    [
        ([{"code": "WSJ"}, {"code": "wsj "}], "", "WSJ is in the list already (row 1)."),
        ([{"code": "NY NYT"}], "", "Letters, digits, _ and - only, like NY_NYT."),
        ([{"code": "../etc"}], "", "Letters, digits, _ and - only"),
        ([{"code": ""}], "", "Type the paper&#39;s code, like NY_NYT."),
        ([{"code": "X" * 41}], "", "At most 40 characters."),
        ([{"code": "WSJ", "css": "x" * 1001}], "", "At most 1000 characters; this is 1001."),
        ([{"code": "WSJ"}], "wsj", "WSJ is in the list already (row 1)."),
        ([{"code": "WSJ"}], "a<b", "Letters, digits, _ and - only"),
    ],
    ids=["duplicate", "space", "path", "blank", "long-code", "long-css", "dup-new", "bad-new"],
)
def test_a_bad_list_is_shown_again_unsaved_with_the_problem_marked(
    app: Flask, client: FlaskClient, rows: list[dict[str, Any]], new: str, said: str
) -> None:
    client.get(PAGE)  # config.json with the defaults
    before = _saved(app)
    form = _form(rows, "add" if new else "save", **{"new-code": new})
    resp = client.post(PAPERS, data=form)
    assert resp.status_code == 400
    body = resp.get_data(as_text=True)
    assert said in body
    assert "Not saved: fix the marked fields first." in body
    assert 'aria-invalid="true"' in body
    assert _saved(app) == before
    # What was typed comes back as typed.
    assert f'value="{html.escape(rows[-1]["code"].strip())}"' in body
    if not new:
        assert re.search(
            r'class="nx-row has-error"[^>]*>\s*<details class="nx-row-main" open', body
        )


def test_the_list_holds_at_most_max_papers(app: Flask, client: FlaskClient) -> None:
    rows = [{"code": f"P{i}"} for i in range(server.MAX_PAPERS)]
    client.post(PAPERS, data=_form(rows))
    assert len(_codes(app)) == server.MAX_PAPERS
    body = client.get(PAGE).get_data(as_text=True)
    assert 'name="new-code"' not in body and "holds at most 40 papers" in body
    resp = client.post(PAPERS, data=_form(rows, "add", **{"new-code": "ONE_MORE"}))
    assert resp.status_code == 400
    assert len(_codes(app)) == server.MAX_PAPERS


@pytest.mark.parametrize(
    "form",
    [
        {"op": "save"},  # no row count: never empties the list
        {"count": "x", "op": "save"},
        {"count": "1", "row-0-code": "WSJ", "op": "explode"},
        {"count": "1", "row-0-code": "WSJ", "op": "remove:5"},
        {"count": "1", "row-0-code": "WSJ", "op": "up:x"},
    ],
    ids=["no-count", "bad-count", "bad-op", "no-such-row", "bad-index"],
)
def test_a_stray_post_is_refused(app: Flask, client: FlaskClient, form: dict[str, str]) -> None:
    client.get(PAGE)
    assert client.post(PAPERS, data=form).status_code == 400
    assert _saved(app)["papers"] == server.DEFAULT_PAPERS


def test_an_unknown_small_screens_value_saves_as_top(app: Flask, client: FlaskClient) -> None:
    client.post(PAPERS, data=_form(DEFAULTS, small_screens="sideways"))
    assert _saved(app)["small_screens"] == "top"


def test_a_css_note_says_what_wont_apply(app: Flask, client: FlaskClient) -> None:
    rows = [{"code": "WSJ", "css": "position: fixed; opacity: 0.9"}, {"code": "USAT", "css": "@x"}]
    client.post(PAPERS, data=_form(rows))
    body = client.get(PAGE).get_data(as_text=True)
    assert "Only this part applies: opacity: 0.9" in body
    assert "None of this CSS applies" in body
    assert server._css_note("filter:contrast(1.1);") == ""  # spacing alone isn't a change


def test_the_widget_renders_what_was_saved(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_module(app), "rasteriser_available", lambda: False)
    client.post(PAPERS, data=_form([{"code": "TX_HC", "css": "opacity: 0.8"}]))
    pages = _dir(app) / "pages"
    pages.mkdir(parents=True)
    today = server._edition_today()
    from PIL import Image

    Image.new("L", (700, 1342), 255).save(pages / f"TX_HC_{today:%Y%m%d}.jpg", "JPEG")
    with app.test_request_context():
        data = _module(app).fetch({"paper": ""}, {}, ctx={"data_dir": str(_dir(app))})
    assert (data["code"], data["css"], data["fit"]) == ("TX_HC", "opacity: 0.8", "top")


# ----- Verify without scripts ----------------------------------------------------


def test_verify_without_scripts_shows_the_page_as_posted_with_the_result(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.get(PAGE)
    seen: list[str] = []

    def verify_code(data_dir: Path, code: str) -> dict[str, Any]:
        seen.append(code)
        return {"ok": True, "tone": "ok", "chip": "Works", "message": f"{code}: downloaded."}

    monkeypatch.setattr(_module(app), "verify_code", verify_code)
    rows = [*DEFAULTS[:3], {"code": "tx_hc", "css": "opacity: 0.5"}]
    resp = client.post("/plugins/news_front_pages/verify", data=_form(rows, verify="3"))
    assert resp.status_code == 200 and resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)
    assert seen == ["TX_HC"]
    assert "TX_HC: downloaded." in body
    assert 'value="tx_hc"' in body  # the unsaved edit is kept
    assert "Not saved yet: press Save to keep your changes." in body
    assert re.search(r'id="paper-3"[^>]*>\s*<details class="nx-row-main" open', body)
    assert _saved(app)["papers"] == server.DEFAULT_PAPERS  # nothing saved
    # The add-a-paper fields have their own Verify.
    resp = client.post(
        "/plugins/news_front_pages/verify",
        data=_form(DEFAULTS, verify="new", **{"new-code": "x y"}),
    )
    assert "&#39;x y&#39; isn&#39;t a paper code" in resp.get_data(as_text=True)
    assert seen == ["TX_HC"]
    bad = _form(DEFAULTS, verify="9")
    assert client.post("/plugins/news_front_pages/verify", data=bad).status_code == 400


# ----- access and mounting -----------------------------------------------------


def test_the_admin_page_is_not_a_render_safe_endpoint(app: Flask, client: FlaskClient) -> None:
    app.config["TESTING"] = False
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": False})
    from app.auth import install_gate, set_password

    set_password(store, "test-admin-password")
    install_gate(app, store)
    for resp in (client.get(PAGE), client.post(PAPERS, data=_form(DEFAULTS))):
        assert resp.status_code == 302
        assert "/login" in resp.location
    assert not (_dir(app) / "config.json").exists()


def test_forms_and_redirects_keep_a_mount_prefix(client: FlaskClient) -> None:
    env = {"SCRIPT_NAME": "/tesserae"}
    page = client.get(PAGE, environ_overrides=env).get_data(as_text=True)
    assert 'action="/tesserae/plugins/news_front_pages/papers"' in page
    assert 'formaction="/tesserae/plugins/news_front_pages/verify"' in page
    assert 'action="/tesserae/plugins/news_front_pages/fetch"' in page
    resp = client.post(PAPERS, data=_form(DEFAULTS), environ_overrides=env)
    assert resp.location == "/tesserae/plugins/news_front_pages/#papers"
