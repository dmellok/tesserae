"""The daily edition: stories made per source, the keep-previous rules, when
a run is due, and the background runner."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from plugins.news_illustrated import feeds, painters, pipeline, store, summarise
from plugins.news_illustrated.tests.fakes import FakeNet, article_page, ok, ok_json, picture, rss

FEEDS = {
    "Tech": "https://hnrss.example.com/frontpage",
    "Business": "https://feeds.example.com/business.xml",
    "Global": "https://feeds.example.com/world.xml",
}
BODY = "The minister said the plan would go ahead next spring. " * 20


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feeds, "FEED_RETRY_PAUSE_S", 0.0)


def _cfg(**extra: Any) -> pipeline.Config:
    settings = {
        "sources": "\n".join(f"{label} | {url}" for label, url in FEEDS.items()),
        "groq_api_key": "gsk",
        "huggingface_api_key": "hf",
        **extra,
    }
    return pipeline.Config.from_settings(settings)


def _news(fake: FakeNet, tag: str = "", *, painted: bool = True) -> None:
    for label, url in FEEDS.items():
        link = f"https://news.example.com/{label.lower()}{tag}"
        fake.on(url, ok(rss(f"{label} headline{tag}‑now", link, "Feed text."), "text/xml"))
        fake.on(link, ok(article_page(BODY)))
    fake.on(
        summarise.GROQ_URL, ok_json({"choices": [{"message": {"content": f"Summary{tag} here."}}]})
    )
    if painted:
        fake.on(painters.HUGGINGFACE_URL, ok(picture(), "image/jpeg"))
    else:
        fake.on(painters.HUGGINGFACE_URL, ok("<html>error</html>"))


def _run(data_dir: Path, cfg: pipeline.Config | None = None, **kw: Any) -> pipeline.Outcome:
    log: list[str] = []
    out = pipeline.run_edition(cfg or _cfg(), data_dir, log=log.append, **kw)
    return out


def test_an_edition_makes_one_illustrated_story_per_source(
    fake_net: FakeNet, tmp_path: Path
) -> None:
    _news(fake_net)
    outcome = _run(tmp_path)
    assert outcome == pipeline.Outcome(made=3)
    stories = store.load_stories(tmp_path)
    assert [s["source"] for s in stories] == ["Tech", "Business", "Global"]
    tech = stories[0]
    assert tech["title"] == "Tech headline-now"
    assert tech["summary"] == "Summary here."
    assert tech["url"] == "https://news.example.com/tech"
    assert tech["provider"] == "huggingface"
    assert tech["date"] == datetime.now().date().isoformat()
    assert store.image_path(tmp_path, tech["image"]) is not None
    assert len(fake_net.to(summarise.GROQ_URL)) == 3
    assert (
        "Content:\nThe minister said"
        in fake_net.to(summarise.GROQ_URL)[0].json()["messages"][0]["content"]
    )


def test_no_picture_keeps_the_whole_previous_story(fake_net: FakeNet, tmp_path: Path) -> None:
    _news(fake_net)
    _run(tmp_path)
    before = store.load_stories(tmp_path)
    fake_net.routes.clear()
    _news(fake_net, "-2", painted=False)
    outcome = _run(tmp_path)
    assert outcome == pipeline.Outcome(kept=3)
    assert store.load_stories(tmp_path) == before
    assert all(store.image_path(tmp_path, s["image"]) for s in before)


def test_no_picture_and_nothing_earlier_saves_text_only(fake_net: FakeNet, tmp_path: Path) -> None:
    _news(fake_net, painted=False)
    _run(tmp_path)
    stories = store.load_stories(tmp_path)
    assert len(stories) == 3 and all(s["image"] == "" for s in stories)
    # A text-only story is not a picture worth protecting: the next one replaces it.
    fake_net.routes.clear()
    _news(fake_net, "-2", painted=False)
    _run(tmp_path)
    assert store.load_stories(tmp_path)[0]["title"] == "Tech headline-2-now"


def test_a_failed_feed_keeps_its_previous_story(fake_net: FakeNet, tmp_path: Path) -> None:
    _news(fake_net)
    _run(tmp_path)
    before = store.load_stories(tmp_path)
    fake_net.routes.clear()
    fake_net.calls.clear()
    _news(fake_net, "-2")
    fake_net.routes.insert(0, (FEEDS["Business"], [ok("<html>down</html>")]))
    outcome = _run(tmp_path)
    assert outcome == pipeline.Outcome(made=2, kept=1)
    after = store.load_stories(tmp_path)
    assert after[1] == before[1]
    assert after[0]["title"] == "Tech headline-2-now"
    assert len(fake_net.to(FEEDS["Business"])) == 2  # retried once


def test_an_unchanged_story_is_not_paid_for_again(fake_net: FakeNet, tmp_path: Path) -> None:
    _news(fake_net)
    _run(tmp_path)
    fake_net.calls.clear()
    assert _run(tmp_path) == pipeline.Outcome(unchanged=3)
    assert fake_net.to(summarise.GROQ_URL) == [] and fake_net.to(painters.HUGGINGFACE_URL) == []
    # By hand it is made again.
    assert _run(tmp_path, force=True) == pipeline.Outcome(made=3)
    assert len(fake_net.to(painters.HUGGINGFACE_URL)) == 3


def test_removed_sources_and_their_pictures_go(fake_net: FakeNet, tmp_path: Path) -> None:
    _news(fake_net)
    _run(tmp_path)
    old = {s["image"] for s in store.load_stories(tmp_path)}
    fake_net.routes.clear()
    _news(fake_net, "-2")
    _run(tmp_path, _cfg(sources=f"Global | {FEEDS['Global']}"))
    stories = store.load_stories(tmp_path)
    assert [s["source"] for s in stories] == ["Global"]
    # Tech's and Business's pictures stay for the grace period (a render may
    # still be loading them), then go; the one on screen stays.
    assert len({p.name for p in (tmp_path / "images").iterdir()}) == 3
    keep = {stories[0]["image"]}
    assert store.prune_images(tmp_path, keep, now=time.time() + 2 * 3600) == 2
    left = {p.name for p in (tmp_path / "images").iterdir()}
    assert left == keep
    assert len(old - left) == 2


def test_a_stop_ends_the_run_between_stories(fake_net: FakeNet, tmp_path: Path) -> None:
    _news(fake_net)
    calls = {"n": 0}

    def stopped() -> bool:
        calls["n"] += 1
        return calls["n"] > 4

    _run(tmp_path, stopped=stopped)
    assert len(store.load_stories(tmp_path)) == 1


def test_settings_parsing() -> None:
    cfg = pipeline.Config.from_settings(
        {
            "sources": "# comment\n\nTech | https://a.example/rss\nhttps://b.example/feed\nBad | ftp://x\n",
            "provider_order": "Gemini, nonsense > kie gemini",
            "run_at": "7:30",
            "summary_words": "9999",
            "kie_api_key": " k ",
        }
    )
    assert cfg.sources == (
        pipeline.Source("Tech", "https://a.example/rss"),
        pipeline.Source("News", "https://b.example/feed"),
    )
    assert cfg.paint.order == ("gemini", "kie")
    assert cfg.run_at == "07:30"
    assert cfg.summary_words == 300
    assert cfg.painters_ready == ["kie"]
    defaults = pipeline.Config.from_settings({})
    assert [s.label for s in defaults.sources] == ["Tech", "Business", "Global"]
    assert defaults.paint.order == painters.PROVIDERS and defaults.run_at == "06:05"
    assert defaults.groq_model == "openai/gpt-oss-120b"
    assert pipeline.parse_run_at("25:00") == "06:05"


def test_source_keys_survive_reordering_but_not_a_new_feed() -> None:
    a = pipeline.Source("Tech", "https://a.example/rss")
    assert a.key == pipeline.Source("Tech", "https://a.example/rss").key
    assert a.key.startswith("tech-")
    assert a.key != pipeline.Source("Tech", "https://b.example/rss").key


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def test_editions_turn_over_at_the_run_time() -> None:
    assert pipeline.edition(_at("2026-10-25 06:04"), "06:05") == "2026-10-24"
    assert pipeline.edition(_at("2026-10-25 06:05"), "06:05") == "2026-10-25"


def test_when_a_run_is_due(tmp_path: Path) -> None:
    morning = _at("2026-10-25 07:00")
    assert pipeline.due(tmp_path, "06:05", morning), "no stories yet"
    store.save_stories(tmp_path, [{"title": "x"}])
    store.save_state(tmp_path, {"edition": "2026-10-24"})
    assert not pipeline.due(tmp_path, "06:05", _at("2026-10-25 05:00")), "yesterday's is done"
    assert pipeline.due(tmp_path, "06:05", morning), "today's is not"
    store.save_state(tmp_path, {"edition": "2026-10-25"})
    assert not pipeline.due(tmp_path, "06:05", morning)


def test_a_failed_day_is_retried_a_little_later_then_left(tmp_path: Path) -> None:
    """The backoff (media_lifecycle): 30 minutes after the first
    try, then an hour, then two, and at most MAX_ATTEMPTS tries a day."""
    store.save_stories(tmp_path, [{"title": "x"}])
    tries = [_at("2026-10-25 06:10").timestamp()]

    def due_at(when: str) -> bool:
        store.save_state(tmp_path, {"edition": "2026-10-24", "attempts": {"2026-10-25": tries}})
        return bool(pipeline.due(tmp_path, "06:05", _at(f"2026-10-25 {when}")))

    assert not due_at("06:35")
    assert due_at("06:41")
    tries.append(_at("2026-10-25 06:41").timestamp())
    assert not due_at("07:35")
    assert due_at("07:42")
    tries.append(_at("2026-10-25 07:42").timestamp())
    assert not due_at("09:35")
    assert due_at("09:43")
    tries.append(_at("2026-10-25 09:43").timestamp())
    assert len(tries) == pipeline.MAX_ATTEMPTS
    assert not due_at("23:00")  # out of tries for the day


def _only_job(data_dir: Path) -> dict[str, Any]:
    jobs = store.load_jobs(data_dir)
    assert len(jobs) <= 1
    return next(iter(jobs.values()), {})


def _wait(runner: pipeline.Runner, data_dir: Path) -> None:
    deadline = time.monotonic() + 10
    while runner.running(data_dir):
        assert time.monotonic() < deadline, "run did not finish"
        time.sleep(0.02)


def test_runner_makes_the_edition_in_the_background(fake_net: FakeNet, tmp_path: Path) -> None:
    _news(fake_net)
    runner = pipeline.Runner()
    assert runner.start(_cfg(), tmp_path, tz=None, reason="schedule")
    _wait(runner, tmp_path)
    assert len(store.load_stories(tmp_path)) == 3
    state = store.load_state(tmp_path)
    today = pipeline.edition(datetime.now(), "06:05")
    assert state["edition"] == today
    assert len(state["attempts"][today]) == 1
    last = state["last_run"]
    assert last["reason"] == "schedule" and last["outcome"] == "3 new" and not last["failed"]
    assert any("Story 2 of 3: Business" in line for line in last["log"])
    assert not store.lock_held(tmp_path)
    assert not pipeline.due(tmp_path, "06:05", datetime.now())


def test_only_one_run_at_a_time(tmp_path: Path) -> None:
    runner = pipeline.Runner()
    other = store.run_lock(tmp_path)
    assert other.acquire(wait=False)  # another process is running
    assert not runner.start(_cfg(), tmp_path, tz=None, reason="manual")
    assert runner.running(tmp_path) and runner.pending(tmp_path) == 1
    other.release()
    assert not runner.running(tmp_path)


def test_a_run_that_crashes_releases_the_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(*a: Any, **k: Any) -> pipeline.Outcome:
        raise RuntimeError("bug")

    monkeypatch.setattr(pipeline, "run_edition", boom)
    runner = pipeline.Runner()
    assert runner.start(_cfg(), tmp_path, tz=None, reason="manual")
    _wait(runner, tmp_path)
    last = store.load_state(tmp_path)["last_run"]
    assert last["failed"] and last["outcome"] == "RuntimeError: bug"
    assert "edition" not in store.load_state(tmp_path)
    assert not store.lock_held(tmp_path)


def test_manual_runs_do_not_use_up_the_days_attempts(fake_net: FakeNet, tmp_path: Path) -> None:
    _news(fake_net, painted=False)
    runner = pipeline.Runner()
    assert runner.start(_cfg(), tmp_path, tz=None, reason="manual", force=True)
    _wait(runner, tmp_path)
    today = pipeline.edition(datetime.now(), "06:05")
    assert store.load_state(tmp_path)["attempts"] == {today: []}


def test_a_failed_setup_leaves_no_phantom_run(
    monkeypatch: pytest.MonkeyPatch, fake_net: FakeNet, tmp_path: Path
) -> None:
    def disk_full(*a: Any, **k: Any) -> None:
        raise OSError("No space left on device")

    real = pipeline._note_attempt
    monkeypatch.setattr(pipeline, "_note_attempt", disk_full)
    runner = pipeline.Runner()
    assert not runner.start(_cfg(), tmp_path, tz=None, reason="schedule")
    assert not runner.running(tmp_path) and not store.lock_held(tmp_path)
    monkeypatch.setattr(pipeline, "_note_attempt", real)
    _news(fake_net)
    assert runner.start(_cfg(), tmp_path, tz=None, reason="schedule")
    _wait(runner, tmp_path)


def test_the_edition_is_the_one_the_run_started_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    days = iter(["2026-10-24", "2026-10-25", "2026-10-25"])
    monkeypatch.setattr(pipeline, "edition", lambda now, run_at: next(days))

    def made_one(*a: Any, **k: Any) -> pipeline.Outcome:
        return pipeline.Outcome(made=1)  # the clock passes 06:05 meanwhile

    monkeypatch.setattr(pipeline, "run_edition", made_one)
    runner = pipeline.Runner()
    assert runner.start(_cfg(), tmp_path, tz=None, reason="schedule")
    _wait(runner, tmp_path)
    state = store.load_state(tmp_path)
    assert state["edition"] == "2026-10-24"
    assert list(state["attempts"]) == ["2026-10-24"]


def test_one_broken_source_does_not_cost_the_others(
    monkeypatch: pytest.MonkeyPatch, fake_net: FakeNet, tmp_path: Path
) -> None:
    _news(fake_net)
    real = feeds.newest_item

    def flaky(url: str, log: Any, sleep: Any = None) -> Any:
        if url == FEEDS["Business"]:
            raise LookupError("unknown encoding: x-made-up")
        return real(url, log)

    monkeypatch.setattr(feeds, "newest_item", flaky)
    assert _run(tmp_path) == pipeline.Outcome(made=2, missing=1)
    assert [s["source"] for s in store.load_stories(tmp_path)] == ["Tech", "Global"]


def test_a_paid_kie_task_survives_into_the_next_run(fake_net: FakeNet, tmp_path: Path) -> None:
    cfg = _cfg(huggingface_api_key="", kie_api_key="kie", sources=f"Tech | {FEEDS['Tech']}")
    _news(fake_net)
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t1"}}))
    fake_net.on(painters.KIE_RECORD_URL, ok_json({"data": {"state": "generating"}}))
    clock = {"now": 0.0}

    def budget() -> painters.Budget:
        def sleep(s: float) -> None:
            clock["now"] += s

        return painters.Budget(clock=lambda: clock["now"], sleep=sleep)

    # Nothing earlier to keep, so the story goes up as text only meanwhile.
    assert _run(tmp_path, cfg, budget=budget) == pipeline.Outcome(made=1)
    assert store.load_stories(tmp_path)[0]["image"] == ""
    job = _only_job(tmp_path)
    assert job["kie"]["task_id"] == "t1" and job["story"]["summary"] == "Summary here."
    # The next run (after a restart, say) collects the task with the same summary.
    fake_net.calls.clear()
    fake_net.routes = [r for r in fake_net.routes if not r[0].startswith(painters.KIE_RECORD_URL)]
    fake_net.on(
        painters.KIE_RECORD_URL,
        ok_json(
            {
                "data": {
                    "state": "success",
                    "resultJson": '{"resultUrls": ["https://cdn.example.com/p.png"]}',
                }
            }
        ),
    )
    fake_net.on("https://cdn.example.com/p.png", ok(picture(), "image/png"))
    assert _run(tmp_path, cfg, budget=budget) == pipeline.Outcome(made=1)
    assert fake_net.to(painters.KIE_CREATE_URL) == []
    assert fake_net.to(summarise.GROQ_URL) == []
    assert store.load_stories(tmp_path)[0]["provider"] == "kie"
    assert _only_job(tmp_path) == {}


def test_secrets_never_reach_the_log(fake_net: FakeNet, tmp_path: Path) -> None:
    _news(fake_net)
    fake_net.routes.insert(
        0,
        (
            summarise.GROQ_URL,
            [ok_json({"error": {"message": "Invalid API Key gsk_live_SECRET"}}, 401)],
        ),
    )
    cfg = _cfg(groq_api_key="gsk_live_SECRET", gemini_api_key="AIza\nInjected: 1")
    assert cfg.paint.keys["gemini"] == "" and cfg.problems
    assert "gsk_live_SECRET" not in repr(cfg)
    runner = pipeline.Runner()
    assert runner.start(cfg, tmp_path, tz=None, reason="manual")
    _wait(runner, tmp_path)
    saved = (tmp_path / store.STATE).read_text(encoding="utf-8")
    assert "gsk_live_SECRET" not in saved and "AIza" not in saved
    assert "Groq failed: Invalid API Key ***" in saved
    assert "gemini_api_key has spaces or control characters in it" in saved


KIE_RESULT = "https://cdn.example.com/p.png"


def _fake_budget() -> painters.Budget:
    clock = {"now": 0.0}

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    return painters.Budget(clock=lambda: clock["now"], sleep=sleep)


def _kie_only() -> pipeline.Config:
    return _cfg(huggingface_api_key="", kie_api_key="kie", sources=f"Tech | {FEEDS['Tech']}")


def _kie_finishes(fake: FakeNet) -> None:
    fake.routes = [r for r in fake.routes if not r[0].startswith(painters.KIE_RECORD_URL)]
    fake.on(
        painters.KIE_RECORD_URL,
        ok_json(
            {"data": {"state": "success", "resultJson": f'{{"resultUrls": ["{KIE_RESULT}"]}}'}}
        ),
    )
    fake.on(KIE_RESULT, ok(picture(), "image/png"))


def test_a_pending_task_is_collected_without_waiting_for_tomorrow(
    monkeypatch: pytest.MonkeyPatch, fake_net: FakeNet, tmp_path: Path
) -> None:
    monkeypatch.setattr(painters, "KIE_POLL_EVERY_S", 0.001)
    monkeypatch.setattr(painters, "KIE_POLL_MAX_S", 0.02)
    cfg = _kie_only()
    _news(fake_net)
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t1"}}))
    fake_net.on(painters.KIE_RECORD_URL, ok_json({"data": {"state": "generating"}}))
    runner = pipeline.Runner()
    assert runner.start(cfg, tmp_path, tz=None, reason="schedule")
    _wait(runner, tmp_path)
    now = datetime.now()
    # The text-only story completes the edition, but the paid task is still owed...
    assert store.load_state(tmp_path)["edition"] == pipeline.edition(now, "06:05")
    assert len(pipeline.owed(tmp_path, time.time())) == 1
    assert pipeline.due(tmp_path, "06:05", now) == "collect"
    # ...so a run comes round for it, and collects it rather than ordering another.
    _kie_finishes(fake_net)
    assert runner.start(cfg, tmp_path, tz=None, reason="collect")
    _wait(runner, tmp_path)
    assert len(fake_net.to(painters.KIE_CREATE_URL)) == 1
    assert store.load_stories(tmp_path)[0]["provider"] == "kie"
    assert pipeline.owed(tmp_path, time.time()) == []
    assert pipeline.due(tmp_path, "06:05", now) == ""
    attempts = store.load_state(tmp_path)["attempts"][pipeline.edition(now, "06:05")]
    assert len(attempts) == 1, "a collection run does not use up the day's attempts"


def test_collection_runs_are_spaced_out(tmp_path: Path) -> None:
    store.save_stories(tmp_path, [{"title": "x"}])
    now = datetime.now()
    store.save_job(tmp_path, "tech", {"kie": {"task_id": "t1", "at": now.timestamp()}})
    done = pipeline.edition(now, "06:05")
    store.save_state(tmp_path, {"edition": done, "collect_at": now.timestamp() - 60})
    assert pipeline.due(tmp_path, "06:05", now) == ""
    store.save_state(tmp_path, {"edition": done, "collect_at": now.timestamp() - 601})
    assert pipeline.due(tmp_path, "06:05", now) == "collect"
    old = now.timestamp() - painters.KIE_RESUME_S - 1
    store.save_job(tmp_path, "tech", {"kie": {"task_id": "t1", "at": old}})
    assert pipeline.due(tmp_path, "06:05", now) == "", "an expired task is not chased"


def test_an_image_write_failure_after_payment_is_recovered(
    monkeypatch: pytest.MonkeyPatch, fake_net: FakeNet, tmp_path: Path
) -> None:
    cfg = _kie_only()
    _news(fake_net)
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t1"}}))
    _kie_finishes(fake_net)
    real = store.save_image

    def disk_full(*a: Any, **k: Any) -> str:
        raise OSError("No space left on device")

    monkeypatch.setattr(store, "save_image", disk_full)
    assert _run(tmp_path, cfg, budget=_fake_budget) == pipeline.Outcome(missing=1)
    assert _only_job(tmp_path)["kie"]["task_id"] == "t1"
    monkeypatch.setattr(store, "save_image", real)
    fake_net.calls.clear()
    assert _run(tmp_path, cfg, budget=_fake_budget) == pipeline.Outcome(made=1)
    assert fake_net.to(painters.KIE_CREATE_URL) == [] and fake_net.to(summarise.GROQ_URL) == []
    assert store.load_stories(tmp_path)[0]["image"]
    assert _only_job(tmp_path) == {}


def test_a_story_commit_failure_after_payment_is_recovered(
    monkeypatch: pytest.MonkeyPatch, fake_net: FakeNet, tmp_path: Path
) -> None:
    cfg = _kie_only()
    _news(fake_net)
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t1"}}))
    _kie_finishes(fake_net)
    real = store.save_stories

    def disk_full(*a: Any, **k: Any) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(store, "save_stories", disk_full)
    with pytest.raises(OSError):
        _run(tmp_path, cfg, budget=_fake_budget)
    assert _only_job(tmp_path)["kie"]["task_id"] == "t1"
    monkeypatch.setattr(store, "save_stories", real)
    fake_net.calls.clear()
    assert _run(tmp_path, cfg, budget=_fake_budget) == pipeline.Outcome(made=1)
    assert fake_net.to(painters.KIE_CREATE_URL) == []
    assert _only_job(tmp_path) == {}


def test_a_kept_story_does_not_settle_a_pending_task(fake_net: FakeNet, tmp_path: Path) -> None:
    """The picture failed and the previous story (with its own picture) stays:
    the task for the new story is still owed."""
    cfg = _kie_only()
    _news(fake_net)
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t0"}}))
    _kie_finishes(fake_net)
    _run(tmp_path, cfg, budget=_fake_budget)
    fake_net.routes.clear()
    _news(fake_net, "-2")
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t1"}}))
    fake_net.on(painters.KIE_RECORD_URL, ok_json({"data": {"state": "generating"}}))
    assert _run(tmp_path, cfg, budget=_fake_budget) == pipeline.Outcome(kept=1)
    assert _only_job(tmp_path)["kie"]["task_id"] == "t1"


def _owe(data_dir: Path, cfg: pipeline.Config, link: str, title: str, ordered_at: float) -> str:
    """Leave a paid, uncollected kie.ai task for the story at ``link``."""
    source = cfg.sources[0]
    sid = pipeline.story_id(source, feeds.Item(title, link, ""))
    story = {
        "story_id": sid,
        "source_key": source.key,
        "source": source.label,
        "title": title,
        "summary": "Summary of the paid story.",
        "url": link,
        "date": "2026-10-25",
        "image": "",
        "provider": "",
    }
    store.save_job(data_dir, sid, {"story": story, "kie": {"task_id": "paid", "at": ordered_at}})
    return sid


def test_a_collection_run_only_collects(fake_net: FakeNet, tmp_path: Path) -> None:
    """The feed has moved on to a new headline: collecting must not fetch it,
    summarise it or order a picture for it."""
    cfg = _kie_only()
    sid = _owe(tmp_path, cfg, "https://news.example.com/old", "Old headline", time.time() - 60)
    _news(fake_net, "-new")
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "second"}}))
    _kie_finishes(fake_net)
    outcome = pipeline.collect_owed(cfg, tmp_path, budget=_fake_budget, log=[].append)
    assert outcome == pipeline.Outcome(made=1)
    assert fake_net.to(FEEDS["Tech"]) == [] and fake_net.to(summarise.GROQ_URL) == []
    assert fake_net.to(painters.KIE_CREATE_URL) == []
    (story,) = store.load_stories(tmp_path)
    assert story["story_id"] == sid and story["title"] == "Old headline" and story["image"]
    assert store.load_jobs(tmp_path) == {}


def test_a_collected_picture_never_replaces_a_newer_story(
    fake_net: FakeNet, tmp_path: Path
) -> None:
    cfg = _kie_only()
    _owe(tmp_path, cfg, "https://news.example.com/old", "Old headline", time.time() - 600)
    newer = {
        "story_id": "x",
        "source_key": cfg.sources[0].key,
        "title": "Newer",
        "image": "",
        "made": int(time.time()),
    }
    store.save_stories(tmp_path, [newer])
    _kie_finishes(fake_net)
    log: list[str] = []
    assert pipeline.collect_owed(cfg, tmp_path, budget=_fake_budget, log=log.append) == (
        pipeline.Outcome(kept=1)
    )
    assert store.load_stories(tmp_path) == [newer]
    assert "That story has been replaced since, so its picture is not shown" in log


def test_a_same_titled_old_story_does_not_settle_a_new_paid_task(tmp_path: Path) -> None:
    cfg = _kie_only()
    sid = _owe(tmp_path, cfg, "https://news.example.com/new", "Same title", time.time() - 60)
    fallback = {"story_id": "tech--other", "title": "Same title", "image": "old.jpg"}
    pipeline._settle(tmp_path, sid, fallback)
    assert store.load_job(tmp_path, sid)["kie"]["task_id"] == "paid"
    pipeline._settle(tmp_path, sid, {"story_id": sid, "title": "Same title", "image": ""})
    assert store.load_job(tmp_path, sid), "text only is not the picture it paid for"
    pipeline._settle(tmp_path, sid, {"story_id": sid, "title": "Same title", "image": "new.jpg"})
    assert store.load_job(tmp_path, sid) == {}


def test_one_timestamp_decides_owed_and_expiry(fake_net: FakeNet, tmp_path: Path) -> None:
    """A task ordered 4 hours ago (for a summary written earlier still) is
    owed, and the scheduled run collects it rather than ordering another."""
    cfg = _kie_only()
    link = "https://news.example.com/tech"
    _owe(tmp_path, cfg, link, "Tech headline-now", time.time() - 4 * 3600)
    assert len(pipeline.owed(tmp_path, time.time())) == 1
    _news(fake_net)
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "second"}}))
    _kie_finishes(fake_net)
    assert _run(tmp_path, cfg, budget=_fake_budget) == pipeline.Outcome(made=1)
    assert fake_net.to(painters.KIE_CREATE_URL) == [] and fake_net.to(summarise.GROQ_URL) == []
    assert store.load_stories(tmp_path)[0]["summary"] == "Summary of the paid story."
    # Past the window it is no longer owed, and only then may a new task be ordered.
    _owe(tmp_path, cfg, link, "Tech headline-now", time.time() - painters.KIE_RESUME_S - 1)
    assert pipeline.owed(tmp_path, time.time()) == []


def test_every_run_is_kept_as_a_line(tmp_path: Path) -> None:
    import threading
    from types import SimpleNamespace

    run = SimpleNamespace(data_dir=tmp_path, stop=threading.Event(), edition="2026-10-25")
    for n in range(pipeline.RUN_HISTORY + 5):
        pipeline._record_run(run, "schedule", float(n), None, "Feed failed" if n % 2 else "", [])
    runs = store.load_state(tmp_path)["runs"]
    assert len(runs) == pipeline.RUN_HISTORY
    assert runs[-1] == {"reason": "schedule", "started": 34, "outcome": "", "failed": False}
    assert runs[-2]["failed"] is True and runs[-2]["outcome"] == "Feed failed"


# ----------------------------------------------------------------------
# The redesigned settings, read the old way and the new


@pytest.mark.parametrize(
    ("saved", "order"),
    [
        (None, painters.PROVIDERS),  # never saved: all four, the default order
        ("", painters.PROVIDERS),
        ("kie, gemini, pollinations, huggingface", painters.PROVIDERS),
        ("huggingface kie", ("huggingface", "kie")),  # spaces, as typed before
        ("gemini > kie", ("gemini", "kie")),
        ("nonsense", painters.PROVIDERS),
        ("none", ()),  # every box unticked on the page
        (" NONE ", ()),
    ],
)
def test_provider_order_old_and_new(saved: str | None, order: tuple[str, ...]) -> None:
    settings = {} if saved is None else {"provider_order": saved}
    cfg = pipeline.Config.from_settings({**settings, "kie_api_key": "k", "gemini_api_key": "g"})
    assert cfg.paint.order == order
    assert cfg.painters_ready == [p for p in order if p in ("kie", "gemini")]


def test_new_settings_default_when_absent_or_broken() -> None:
    old = pipeline.Config.from_settings({"groq_api_key": "gsk"})  # saved before this version
    assert (old.summary_service, old.image_shape, old.summary_key) == ("groq", "auto", "gsk")
    odd = pipeline.Config.from_settings(
        {"groq_api_key": "gsk", "summary_service": "openai", "image_shape": "7:5"}
    )
    assert (odd.summary_service, odd.image_shape) == ("groq", "auto")
    off = pipeline.Config.from_settings({"groq_api_key": "gsk", "summary_service": "off"})
    assert off.summary_key == "" and off.groq_key == "gsk"
    assert pipeline.Config.from_settings({"image_shape": "16:9"}).image_shape == "16:9"


def test_unusable_source_lines_are_named() -> None:
    raw = "Tech | https://a.example/rss\nBad | ftp://x\nwww.bbc.co.uk/news\n# note\n\nFine | http://b.example"
    problems = pipeline.source_problems(raw)
    assert problems == [
        "Sources line 2 is skipped: 'ftp://x' is not an http(s) feed address",
        "Sources line 3 is skipped: 'www.bbc.co.uk/news' is not an http(s) feed address",
    ]
    cfg = pipeline.Config.from_settings({"sources": raw})
    assert [s.label for s in cfg.sources] == ["Tech", "Fine"]
    assert set(problems) <= set(cfg.problems)


def test_the_picture_shape_follows_the_biggest_frame() -> None:
    assert pipeline.pick_aspect("auto", []) == "9:16"
    assert pipeline.pick_aspect("auto", [(800, 480), (1440, 2560)]) == "9:16"
    assert pipeline.pick_aspect("auto", [(800, 480), (1600, 1200)]) == "4:3"
    assert pipeline.pick_aspect("auto", [(800, 480)]) == "16:9"
    assert pipeline.pick_aspect("1:1", [(1440, 2560)]) == "1:1"


def test_summaries_off_use_the_article_without_groq(fake_net: FakeNet, tmp_path: Path) -> None:
    _news(fake_net)
    log: list[str] = []
    pipeline.run_edition(_cfg(summary_service="off"), tmp_path, log=log.append)
    assert fake_net.to(summarise.GROQ_URL) == []
    assert store.load_stories(tmp_path)[0]["summary"].startswith("The minister said")
    assert any("Summaries are off" in line for line in log)


def _groq_with_usage(fake: FakeNet) -> None:
    fake.routes = [r for r in fake.routes if r[0] != summarise.GROQ_URL]
    fake.on(
        summarise.GROQ_URL,
        ok_json(
            {
                "choices": [{"message": {"content": "Summary here."}}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 100, "total_tokens": 1000},
            }
        ),
    )


def test_a_run_counts_what_it_used(fake_net: FakeNet, tmp_path: Path) -> None:
    _news(fake_net)
    _groq_with_usage(fake_net)
    usage = pipeline.Usage()
    pipeline.run_edition(_cfg(), tmp_path, log=lambda line: None, usage=usage)
    assert usage.record() == {"tokens": {"groq": 3000}, "pictures": {"huggingface": 3}}
    assert usage.sentence() == "Groq 3,000 tokens; 3 pictures from HuggingFace"
    story = store.load_stories(tmp_path)[0]
    assert story["usage"] == {
        "summary": {"provider": "groq", "tokens": 1000, "in": 900, "out": 100},
        "picture": {"provider": "huggingface"},
    }
    assert story["size"] == [768, 1376]


def test_the_runner_records_usage_and_kie_credits(
    monkeypatch: pytest.MonkeyPatch, fake_net: FakeNet, tmp_path: Path
) -> None:
    monkeypatch.setattr(painters, "KIE_POLL_EVERY_S", 0.001)
    _news(fake_net)
    _groq_with_usage(fake_net)
    fake_net.on(
        painters.KIE_CREDIT_URL,
        ok_json({"code": 200, "data": 100.0}),
        ok_json({"code": 200, "data": 96.0}),
    )
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t1"}}))
    _kie_finishes(fake_net)
    cfg = _cfg(huggingface_api_key="", kie_api_key="kie", sources=f"Tech | {FEEDS['Tech']}")
    runner = pipeline.Runner()
    assert runner.start(cfg, tmp_path, tz=None, reason="manual", force=True)
    _wait(runner, tmp_path)
    last = store.load_state(tmp_path)["last_run"]
    assert last["usage"] == {
        "tokens": {"groq": 1000},
        "pictures": {"kie": 1},
        "kie_balance": 96.0,
        "kie_used": 4.0,
    }
    assert last["log"][-1].endswith(
        "Used: Groq 1,000 tokens; 1 picture from kie.ai (4 credits, 96 left)"
    )
    assert store.load_state(tmp_path)["runs"][-1]["usage"] == last["usage"]
    assert last["log"][1].endswith("Pictures are drawn 9:16")


def test_the_runner_draws_the_frames_shape(
    monkeypatch: pytest.MonkeyPatch, fake_net: FakeNet, tmp_path: Path
) -> None:
    monkeypatch.setattr(painters, "KIE_POLL_EVERY_S", 0.001)
    _news(fake_net)
    fake_net.on(painters.KIE_CREATE_URL, ok_json({"data": {"taskId": "t1"}}))
    _kie_finishes(fake_net)
    store.note_frame(tmp_path, "living-room", 1600, 1200, now=time.time())
    cfg = _cfg(huggingface_api_key="", kie_api_key="kie", sources=f"Tech | {FEEDS['Tech']}")
    runner = pipeline.Runner()
    assert runner.start(cfg, tmp_path, tz=None, reason="manual", force=True)
    _wait(runner, tmp_path)
    (create,) = fake_net.to(painters.KIE_CREATE_URL)
    assert create.json()["input"]["image_size"] == "4:3"
    assert "Landscape (4:3) picture" in create.json()["input"]["prompt"]


def test_a_provider_that_reports_nothing_still_counts_its_pictures(
    fake_net: FakeNet, tmp_path: Path
) -> None:
    _news(fake_net)
    runner = pipeline.Runner()
    assert runner.start(_cfg(groq_api_key=""), tmp_path, tz=None, reason="manual")
    _wait(runner, tmp_path)
    last = store.load_state(tmp_path)["last_run"]
    assert last["usage"] == {"pictures": {"huggingface": 3}}  # Groq unused, HF says nothing


def test_a_run_with_nothing_to_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "run_edition", lambda *a, **k: pipeline.Outcome(kept=1))
    runner = pipeline.Runner()
    assert runner.start(_cfg(), tmp_path, tz=None, reason="manual")
    _wait(runner, tmp_path)
    state = store.load_state(tmp_path)
    assert state["last_run"]["usage"] == {}
    assert state["last_run"]["log"][-1].endswith("Used: nothing the services reported")
    assert "usage" not in state["runs"][-1]


def test_usage_sentence_ignores_junk() -> None:
    assert pipeline.usage_sentence({}) == ""
    assert pipeline.usage_sentence("x") == ""  # type: ignore[arg-type]
    assert pipeline.usage_sentence({"tokens": {"groq": "lots"}, "pictures": {"kie": 2}}) == (
        "2 pictures from kie.ai"
    )


@pytest.mark.parametrize(
    "body",
    [
        b"<html><body>Not a feed</body></html>",
        b"<?xml version='1.0'?><rss><channel><item><title>cut",
        b'<!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>',
    ],
)
def test_a_broken_feed_costs_only_its_own_story(
    fake_net: FakeNet, tmp_path: Path, body: bytes
) -> None:
    _news(fake_net)
    fake_net.routes.insert(0, (FEEDS["Business"], [ok(body, "application/xml")]))
    log: list[str] = []
    outcome = pipeline.run_edition(_cfg(), tmp_path, log=log.append)
    assert outcome == pipeline.Outcome(made=2, missing=1)
    assert [s["source"] for s in store.load_stories(tmp_path)] == ["Tech", "Global"]
    assert any(line.startswith("Feed attempt 2:") for line in log)
