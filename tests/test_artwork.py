"""app.artwork: the pictures a widget makes and keeps, once for every widget."""

from __future__ import annotations

import io
import json
import threading
import time
from typing import Any

import pytest
from PIL import Image

from app import artwork, fal


def _jpeg(colour=(200, 40, 40), size=(120, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def fake_fal(monkeypatch):
    """fal.ai answered here: every call records its body and returns a red picture."""
    calls: list[dict[str, Any]] = []

    def http_json(url, body, key, timeout):
        calls.append({"url": url, "body": body, "key": key})
        if key == "bad":
            import urllib.error

            raise urllib.error.HTTPError(url, 401, "nope", {}, None)
        return {"images": [{"url": "https://cdn.example/out.jpg"}]}

    monkeypatch.setattr(fal, "_http_json", http_json)
    monkeypatch.setattr(fal, "_download", lambda url, timeout=60.0: _jpeg())
    return calls


def _wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _collection(name="paintings", **kw):
    def make(job: artwork.Job):
        if job.payload.get("boom"):
            raise artwork.ArtworkError("no reference photo")
        raw = job.fal.generate(
            job.payload.get("model", "nano_banana"),
            "paint " + job.title,
            refs=["https://ref.example/a.jpg"],
        )
        return raw, {
            "credit": f"AI painting of {job.title}",
            "style": job.payload.get("style", "gould"),
        }

    return artwork.Collection(
        "test_widget", name, make=make, label="Test widget", title="Painting", facet="style", **kw
    )


def _ctx(tmp_path, **kw):
    return {
        "data_dir": str(tmp_path),
        "preview": False,
        "settled": True,
        "cell_id": "cell1",
        "page_name": "Study",
        **kw,
    }


SETTINGS = {"fal_api_key": "k", "gen_daily_usd": "1"}


def test_a_panel_render_starts_a_painting_and_finds_it_next_time(tmp_path, fake_fal):
    c = _collection()
    art = c.open(_ctx(tmp_path), SETTINGS, subject="s1")
    got = art.request("magpie--gould", {"style": "gould"}, title="Australian Magpie")
    assert got.state == "painting" and art.live() == {"live_refresh_s": 6, "live_pending": 1}
    assert _wait(lambda: c.path("magpie--gould", tmp_path) is not None), "the worker painted it"
    assert fake_fal[0]["body"]["prompt"] == "paint Australian Magpie" and fake_fal[0]["key"] == "k"
    art.finish(_ctx(tmp_path), facet="gould")
    art2 = c.open(_ctx(tmp_path), SETTINGS, subject="s1")
    got = art2.request("magpie--gould", {"style": "gould"}, title="Australian Magpie")
    assert (
        got.ready
        and got.credit == "AI painting of Australian Magpie"
        and got.meta["payload"] == {"style": "gould"}
    )
    assert art2.live() == {"live_refresh_s": 0, "live_pending": 0}
    src = art2.url("magpie--gould", px=60, levels="mono")
    assert (
        src.startswith("data:image/jpeg;base64,") and (tmp_path / "paintings" / "derived").is_dir()
    )
    assert c.budget_used(tmp_path) == 4, "a Nano Banana painting is 4 cents"
    assert [d["name"] for d in c.dashboards(tmp_path)] == ["Study"], (
        "the panel's dashboard asked for it"
    )
    assert c.dashboards(tmp_path)[0]["jobs"]["magpie--gould"]["payload"] == {"style": "gould"}


def test_a_preview_holds_until_settled_and_paint_now_starts_it(tmp_path, fake_fal):
    c = _collection()
    art = c.open(_ctx(tmp_path, preview=True, settled=False), SETTINGS, subject="s1")
    got = art.request("wren--gould", {"style": "gould"}, title="Wren")
    assert got.state == "held" and "wren--gould" in c.held(tmp_path) and not fake_fal
    art.finish(_ctx(tmp_path, preview=True, settled=False))
    # the cell moves to another look: what it held for the old one goes
    art = c.open(_ctx(tmp_path, preview=True, settled=False), SETTINGS, subject="s1")
    art.request("wren--audubon", {"style": "audubon"}, title="Wren")
    art.finish(_ctx(tmp_path, preview=True, settled=False))
    assert list(c.held(tmp_path)) == ["wren--audubon"]
    art = c.open(_ctx(tmp_path, preview=True, settled=False), SETTINGS, subject="s1")
    art.request("wren--gould", {"style": "gould"}, title="Wren")
    art.finish(_ctx(tmp_path, preview=True, settled=False))
    assert c.start_held(tmp_path, "k", SETTINGS, subject="s1") == 1
    assert _wait(lambda: c.path("wren--gould", tmp_path) is not None) and not c.held(tmp_path)
    # a settled preview starts up to per-preview jobs, the rest wait
    art = c.open(
        _ctx(tmp_path, preview=True, settled=True),
        {**SETTINGS, "gen_per_refresh": "1"},
        subject="s2",
    )
    assert art.request("a--gould", {"style": "gould"}, title="A").state == "painting"
    assert art.request("b--gould", {"style": "gould"}, title="B").state == "waiting"
    assert art.pending == 2 and art.status() == ["painting 1 of 2"]


def test_an_older_host_without_settled_uses_the_settle_timer(tmp_path, fake_fal, monkeypatch):
    c = _collection()
    ctx = {"data_dir": str(tmp_path), "preview": True, "cell_id": "cellX"}
    art = c.open(ctx, SETTINGS, subject="s")
    assert not art.settled, "the first preview of a cell is not settled"
    monkeypatch.setattr(artwork, "SETTLE_S", 0)
    assert c.open(ctx, SETTINGS, subject="s").settled, (
        "the same cell seen again after the settle time is"
    )


def test_no_key_budget_and_failures(tmp_path, fake_fal):
    c = _collection()
    art = c.open(_ctx(tmp_path), {"gen_daily_usd": "1"}, subject="s")
    assert art.request("x--gould", {}, title="X").state == "no_key"
    assert "fal.ai key" in art.notes()[0]
    c.spend(tmp_path, 100)
    art = c.open(_ctx(tmp_path), SETTINGS, subject="s")
    assert art.request("x--gould", {}, title="X").state == "capped" and "US$1.00" in art.notes()[0]
    assert (
        c.cap({"gen_daily_cap": "12"}) == 48
        and c.cap({}) == 400
        and c.cap({"gen_daily_usd": 0}) == artwork.UNLIMITED
    )
    c.budget_reset(tmp_path)
    art = c.open(_ctx(tmp_path), SETTINGS, subject="s")
    assert art.request("y--gould", {"boom": True}, title="Y").state == "painting"
    assert _wait(lambda: c.state("y--gould", tmp_path) == "failed")
    assert c.why_failed("y--gould", tmp_path) == "no reference photo"
    art = c.open(_ctx(tmp_path), SETTINGS, subject="s")
    assert (
        art.request("y--gould", {"boom": True}, title="Y").state == "failed"
        and "1 picture failed" in art.notes()[0]
    )
    c.clear_error("y--gould", tmp_path)
    assert c.state("y--gould", tmp_path) == "none"
    art = c.open(_ctx(tmp_path), {"fal_api_key": "bad", "gen_daily_usd": "1"}, subject="s")
    art.request("z--gould", {}, title="Z")
    assert _wait(
        lambda: c.state("z--gould", tmp_path) == "failed"
    ) and "refused the key" in c.why_failed("z--gould", tmp_path)


def test_render_now_repaint_versions_and_purge(tmp_path, fake_fal):
    c = _collection()
    art = c.open(
        _ctx(tmp_path), {"gen_daily_usd": "1"}, subject="s"
    )  # no key: nothing starts, but the dashboard is noted
    art.request("robin--gould", {"style": "gould"}, title="Robin")
    art.finish(_ctx(tmp_path), facet="gould")
    started, found = c.render_now(tmp_path, "k", SETTINGS)
    assert (started, found) == (1, 1)
    assert _wait(lambda: c.path("robin--gould", tmp_path) is not None)
    first = c.path("robin--gould", tmp_path).read_bytes()
    assert c.repaint("robin--gould", tmp_path, "k", SETTINGS)
    assert _wait(lambda: len(c.versions("robin--gould", tmp_path)) == 1)
    listing = c.listing(tmp_path)
    assert (
        listing[0]["key"] == "robin--gould"
        and listing[0]["facet"] == "gould"
        and len(listing[0]["versions"]) == 1
    )
    stem = listing[0]["versions"][0]["stem"]
    assert (
        c.use_version(stem, tmp_path) == "robin--gould"
        and c.path("robin--gould", tmp_path).read_bytes() == first
    )
    # deleted but kept: no current file, still on the page with both versions, title and all,
    # and one of them can be made current again
    c.archive("robin--gould", tmp_path)
    assert c.path("robin--gould", tmp_path) is None
    gone = c.listing(tmp_path)
    assert (
        len(gone) == 1
        and gone[0]["key"] == "robin--gould"
        and gone[0]["file"] == ""
        and gone[0]["title"] == "Robin"
        and len(gone[0]["versions"]) == 2
    )
    assert c.use_version(gone[0]["versions"][-1]["stem"], tmp_path) == "robin--gould"
    assert c.path("robin--gould", tmp_path) is not None
    prog = c.progress(tmp_path, SETTINGS)
    assert prog["used_text"] == "US$0.08" and prog["cap_text"] == "US$1.00"
    assert (
        c.purge(tmp_path)["pictures"] == 1
        and c.path("robin--gould", tmp_path) is None
        and c.budget_used(tmp_path) == 8
    )


def test_cull_drops_a_previews_old_wishes_and_the_queue_survives_a_restart(
    tmp_path, fake_fal, monkeypatch
):
    c = _collection(parallel=1)
    slow = threading.Event()
    orig = fal._download

    def download(url, timeout=60.0):
        slow.wait(15)
        return orig(url, timeout)

    monkeypatch.setattr(fal, "_download", download)
    art = c.open(
        _ctx(tmp_path, preview=True, settled=True),
        {**SETTINGS, "gen_per_refresh": "5"},
        subject="s",
    )
    for k in ("p1", "p2", "p3"):
        assert art.request(f"{k}--gould", {"style": "gould"}, title=k).state == "painting"
    rows = json.loads((tmp_path / "paintings" / "queue.json").read_text())
    assert {r["key"] for r in rows} >= {"p2--gould", "p3--gould"}, (
        "waiting jobs are on disk for a restart"
    )
    assert c.cull("s", {"p1--gould", "p3--gould"}) == ["p2--gould"], (
        "the preview no longer wants p2"
    )
    slow.set()
    assert _wait(lambda: c.path("p3--gould", tmp_path) is not None, 20)
    # a fresh collection (a restarted server) resumes what the queue file says is waiting
    (tmp_path / "paintings" / "queue.json").write_text(
        json.dumps([{"key": "p9--gould", "payload": {"style": "gould"}, "title": "p9", "cap": 100}])
    )
    c2 = _collection(name="paintings2")
    (tmp_path / "paintings2").mkdir()
    (tmp_path / "paintings2" / "queue.json").write_text(
        json.dumps([{"key": "p9--gould", "payload": {"style": "gould"}, "title": "p9", "cap": 100}])
    )
    assert c2.resume(tmp_path, "k") == 1 and _wait(
        lambda: c2.path("p9--gould", tmp_path) is not None, 20
    )


def test_keys_are_checked_and_cell_ids_are_stable():
    c = _collection(name="k")
    art = c.open({"data_dir": "/tmp/nowhere", "preview": False, "settled": True}, SETTINGS)
    assert art.request("Bad Key!", {}, title="x").state == "failed"
    assert artwork.cell_id_of("w", {"b": 1, "a": [1, 2]}) == artwork.cell_id_of(
        "w", {"a": [1, 2], "b": 1}
    )
    assert artwork.cell_id_of("w", {"a": 1}) != artwork.cell_id_of("v", {"a": 1})


def test_fal_request_bodies_and_key_resolution(monkeypatch):
    assert fal.request_body("nano_banana", "p", ["u"], "3:4")["image_urls"] == ["u"]
    assert fal.request_body("flux_kontext", "p", ["u", "v"])["image_url"] == "u"
    assert fal.request_body("gpt_image", "p", ["u"], "3:4")["image_size"] == "portrait_4_3"
    assert fal.request_body("nano_banana_t2i", "p", [])["prompt"] == "p"
    assert fal.request_body("birefnet", "", ["u"])["image_url"] == "u"
    assert (
        fal.pick_image_url({"images": [{"url": "x"}]}) == "x"
        and fal.pick_image_url({"image": {"url": "y"}}) == "y"
        and fal.pick_image_url({}) == ""
    )
    assert fal.cents_of("nano_banana_pro") == 15 and fal.model_of("nope") == "nano_banana"
    monkeypatch.delenv("FAL_KEY", raising=False)
    assert fal.resolve_key({"fal_api_key": " own "}) == "own"
    monkeypatch.setenv("FAL_KEY", "env")
    assert fal.resolve_key({}) == "env"
    client = fal.FalClient(
        "k", charge=lambda c, m: (_ for _ in ()).throw(artwork.ArtworkError("spent"))
    )
    with pytest.raises(artwork.ArtworkError):
        client.generate("nano_banana", "p", refs=["u"])
    with pytest.raises(fal.FalError):
        fal.FalClient("").generate("nano_banana", "p", refs=["u"])


def test_an_older_installs_folder_is_adopted_once(tmp_path):
    c = _collection(name="adopted")
    old = tmp_path / "gen"
    old.mkdir()
    (old / "magpie--gould.jpg").write_bytes(_jpeg())
    (old / "magpie--gould.json").write_text(json.dumps({"credit": "old credit", "style": "gould"}))
    (old / "magpie--gould_400.jpg").write_bytes(_jpeg())
    (old / "magpie--gould.paper").write_text("#fff")
    (old / "budget.json").write_text(json.dumps({"2026-10-18": 3}))
    (old / "archive").mkdir()
    (old / "archive" / "magpie--gould__20261001-120000.jpg").write_bytes(_jpeg())
    (tmp_path / "held_paints.json").write_text(
        json.dumps(
            {
                "wren--gould": {
                    "key": "wren--gould",
                    "name": "Wren",
                    "style": "gould",
                    "subject": "s1",
                    "origin": "preview",
                }
            }
        )
    )
    (tmp_path / "dashboards.json").write_text(
        json.dumps(
            {
                "Study|gould|s1": {
                    "name": "Study",
                    "style": "gould",
                    "keys": ["magpie--gould"],
                    "jobs": {
                        "magpie--gould": {
                            "key": "magpie--gould",
                            "name": "Magpie",
                            "style": "gould",
                        }
                    },
                    "at": time.time(),
                }
            }
        )
    )

    def convert(job):
        return {k: v for k, v in job.items() if k not in ("key", "subject", "origin")}, str(
            job.get("name") or job["key"]
        )

    assert c.adopt(
        tmp_path,
        "gen",
        drop=r".*(_\d+(_\w+)?\.jpg|\.paper)",
        budget_counts=True,
        job_to_payload=convert,
    )
    assert (
        c.path("magpie--gould", tmp_path) is not None
        and c.meta("magpie--gould", tmp_path)["credit"] == "old credit"
    )
    assert not (tmp_path / "gen").exists() and [e["key"] for e in c.listing(tmp_path)] == [
        "magpie--gould"
    ], "resized copies went"
    assert c.budget_used(tmp_path) == 12 if time.strftime("%Y-%m-%d") == "2026-10-18" else True, (
        "three paintings at the typical price"
    )
    assert len(c.versions("magpie--gould", tmp_path)) == 1
    assert (
        c.held(tmp_path)["wren--gould"]["payload"] == {"name": "Wren", "style": "gould"}
        and c.held(tmp_path)["wren--gould"]["title"] == "Wren"
    )
    d = c.dashboards(tmp_path)
    assert (
        d[0]["name"] == "Study"
        and d[0]["facet"] == "gould"
        and d[0]["jobs"]["magpie--gould"]["payload"]["name"] == "Magpie"
        and d[0]["subject"] == "s1"
    )
    assert not c.adopt(tmp_path, "gen"), "once"
